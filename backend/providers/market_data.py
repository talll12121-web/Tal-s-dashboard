"""
Unified market-data layer  ("the mix")
=======================================

Design goal: one set of functions the rest of the app calls, regardless of
where the data actually comes from. Each function picks the best *free*
source for that data type and falls back gracefully.

  quotes        -> IBKR live snapshot (if gateway connected) else yfinance
  history/bars  -> yfinance (free, deep history, no key)
  fundamentals  -> yfinance .info / .get_info (free, broad coverage)
  intraday VWAP -> IBKR 1-min bars (if connected) else yfinance 1-min

Why this mix:
  * yfinance  - free, no API key, deepest coverage for history + fundamentals.
                Unofficial but rock-solid for personal use. This is the backbone.
  * IBKR      - your real-time, exchange-accurate prices and the only source
                that knows your positions/executions. Used to *override*
                yfinance quotes whenever the gateway is up.
  * Finnhub   - optional (free key) for a real-time quote fallback if you ever
                run without IBKR; wired but off by default.

Everything is wrapped in a tiny TTL cache so the UI can poll aggressively
without hammering the sources.
"""

from __future__ import annotations
import time
import threading
import logging
from typing import Optional

import pandas as pd

from .. import config
from ..core import store

logger = logging.getLogger(__name__)

# -- tiny thread-safe TTL cache -----------------------------------------
_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()


def _cache_get(key: str, ttl: float):
    with _lock:
        hit = _cache.get(key)
    if hit and (time.time() - hit[0]) < ttl:
        return hit[1]
    return None


def _cache_put(key: str, value):
    with _lock:
        _cache[key] = (time.time(), value)


# -- lazy yfinance import (keeps startup fast / optional) ---------------
def _yf():
    import yfinance as yf
    return yf


# -- Quotes -------------------------------------------------------------
def get_quote(symbol: str) -> dict:
    """Best-available current quote for one symbol.

    Returns: {symbol, price, prevClose, change, changePct, dayHigh, dayLow,
              volume, source}

    Live IBKR data (pushed by the desktop bridge or a local IBKR connection)
    is stored in the shared live-state store and overrides yfinance when fresh.
    """
    symbol = symbol.upper().strip()

    # 1) IBKR live override (from the shared live-state store)
    q = store.live_quote(symbol)
    if q and q.get("price") is not None:
        q = dict(q)
        q["source"] = "IBKR"
        return q

    # 2) yfinance (cached)
    cached = _cache_get(f"quote:{symbol}", config.QUOTE_TTL)
    if cached:
        return cached

    q = _yfinance_quote(symbol)
    _cache_put(f"quote:{symbol}", q)
    return q


def get_quotes(symbols: list[str]) -> dict[str, dict]:
    return {s: get_quote(s) for s in symbols}


def _yfinance_quote(symbol: str) -> dict:
    try:
        yf = _yf()
        t = yf.Ticker(symbol)
        # fast_info is the cheap, reliable path for live-ish numbers
        fi = t.fast_info
        price = _f(fi.get("last_price"))
        prev = _f(fi.get("previous_close"))
        change = round(price - prev, 2) if price and prev else None
        change_pct = round((price - prev) / prev * 100, 2) if price and prev else None
        if price is None:
            raise ValueError("no price from yfinance")
        return {
            "symbol": symbol,
            "price": price,
            "prevClose": prev,
            "change": change,
            "changePct": change_pct,
            "dayHigh": _f(fi.get("day_high")),
            "dayLow": _f(fi.get("day_low")),
            "volume": _f(fi.get("last_volume"), digits=0),
            "source": "yfinance",
        }
    except Exception as e:
        logger.debug("yfinance quote failed for %s: %s — trying Stooq", symbol, e)
        from . import stooq
        q = stooq.get_quote(symbol)
        if q:
            return q
        return {"symbol": symbol, "price": None, "source": "unavailable"}


# -- History / bars -----------------------------------------------------
def get_history(symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
    """OHLCV history as a DataFrame indexed by datetime. yfinance-backed."""
    key = f"hist:{symbol}:{period}:{interval}"
    cached = _cache_get(key, config.HISTORY_TTL)
    if cached is not None:
        return cached
    df = None
    try:
        yf = _yf()
        df = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=False)
    except Exception as e:
        logger.debug("yfinance history failed for %s: %s", symbol, e)
        df = None
    # Fallback to Stooq when yfinance is blocked/empty (e.g. datacenter IPs)
    if df is None or df.empty:
        from . import stooq
        df = stooq.get_history(symbol, period=period, interval=interval)
    if df is None:
        df = pd.DataFrame()
    _cache_put(key, df)
    return df


# -- Fundamentals -------------------------------------------------------
def get_fundamentals(symbol: str) -> dict:
    """Key fundamental metrics for the long-term view. yfinance-backed.

    Yahoo frequently blocks the .info endpoint from datacenter IPs, so this:
      * retries briefly on transient failures,
      * NEVER caches a failure for the full (6h) fundamental TTL - a blocked
        request is cached only briefly so the next poll can recover instead of
        the Long-term tab staying empty for hours.
    """
    key = f"fund:{symbol}"
    cached = _cache_get(key, config.FUNDAMENTAL_TTL)
    # Only trust the long-lived cache when it actually holds data.
    if cached and not cached.get("error"):
        return cached
    # Short cache for recent failures avoids hammering a rate-limited source.
    failed = _cache_get(f"fundfail:{symbol}", 90)
    if failed:
        return failed

    out = {"symbol": symbol}
    last_err = None
    for attempt in range(2):
        try:
            yf = _yf()
            info = yf.Ticker(symbol).info or {}
            if not info.get("marketCap") and not info.get("shortName"):
                raise ValueError("empty .info payload (likely rate-limited)")
            out.update({
                "name": info.get("shortName") or info.get("longName"),
                "sector": info.get("sector"),
                "industry": info.get("industry"),
                "marketCap": info.get("marketCap"),
                "trailingPE": info.get("trailingPE"),
                "forwardPE": info.get("forwardPE"),
                "pegRatio": info.get("pegRatio"),
                "priceToBook": info.get("priceToBook"),
                "profitMargins": info.get("profitMargins"),
                "returnOnEquity": info.get("returnOnEquity"),
                "revenueGrowth": info.get("revenueGrowth"),
                "earningsGrowth": info.get("earningsGrowth"),
                "debtToEquity": info.get("debtToEquity"),
                "dividendYield": info.get("dividendYield"),
                "freeCashflow": info.get("freeCashflow"),
                "beta": info.get("beta"),
                "fiftyTwoWeekHigh": info.get("fiftyTwoWeekHigh"),
                "fiftyTwoWeekLow": info.get("fiftyTwoWeekLow"),
                "recommendationKey": info.get("recommendationKey"),
                "targetMeanPrice": info.get("targetMeanPrice"),
            })
            _cache_put(key, out)
            return out
        except Exception as e:
            last_err = e
            logger.debug("fundamentals attempt %d failed for %s: %s", attempt, symbol, e)

    # yfinance blocked (typical on Render). Fall back to sources that don't get
    # blocked from datacenter IPs: SEC EDGAR (no key, never blocks, official
    # filings) as the primary, with Finnhub filling the price-derived gaps
    # (P/E, P/B, market cap, sector) that EDGAR can't provide.
    merged = _fallback_fundamentals(symbol)
    if merged:
        _cache_put(key, merged)
        return merged

    logger.warning("fundamentals unavailable for %s: %s", symbol, last_err)
    out["error"] = str(last_err)
    _cache_put(f"fundfail:{symbol}", out)  # short TTL, not the 6h fundamental cache
    return out


def _fallback_fundamentals(symbol: str) -> Optional[dict]:
    """EDGAR (primary, never blocks) + Finnhub (gap-fill). Returns a merged
    fundamentals dict, or None if neither source produced anything usable."""
    from . import edgar, finnhub
    try:
        edgar_data = edgar.get_fundamentals(symbol)
    except Exception as e:
        logger.debug("edgar fallback %s: %s", symbol, e)
        edgar_data = {}
    try:
        finnhub_data = finnhub.get_fundamentals(symbol)
    except Exception as e:
        logger.debug("finnhub fallback %s: %s", symbol, e)
        finnhub_data = {}
    if not edgar_data and not finnhub_data:
        return None
    # Start from Finnhub (has the price-derived fields), then let EDGAR's
    # filing-based values take precedence where present (user's "EDGAR primary").
    merged = {"symbol": symbol.upper()}
    merged.update({k: v for k, v in finnhub_data.items() if v is not None})
    merged.update({k: v for k, v in edgar_data.items() if v is not None})
    srcs = [s for s in (edgar_data.get("source"), finnhub_data.get("source")) if s]
    merged["source"] = "+".join(srcs) if srcs else "fallback"
    return merged


# -- helpers ------------------------------------------------------------
def _f(x, digits: int = 2) -> Optional[float]:
    try:
        v = float(x)
        if v != v:  # NaN
            return None
        return round(v, digits) if digits > 0 else int(v)
    except (TypeError, ValueError):
        return None
