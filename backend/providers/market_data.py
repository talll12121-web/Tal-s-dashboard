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
        logger.warning("yfinance quote failed for %s: %s", symbol, e)
        return {"symbol": symbol, "price": None, "source": "unavailable", "error": str(e)}


# -- History / bars -----------------------------------------------------
def get_history(symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
    """OHLCV history as a DataFrame indexed by datetime. yfinance-backed."""
    key = f"hist:{symbol}:{period}:{interval}"
    cached = _cache_get(key, config.HISTORY_TTL)
    if cached is not None:
        return cached
    try:
        yf = _yf()
        df = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=False)
        if df is None:
            df = pd.DataFrame()
        _cache_put(key, df)
        return df
    except Exception as e:
        logger.warning("history failed for %s: %s", symbol, e)
        return pd.DataFrame()


# -- Fundamentals -------------------------------------------------------
def get_fundamentals(symbol: str) -> dict:
    """Key fundamental metrics for the long-term view. yfinance-backed."""
    key = f"fund:{symbol}"
    cached = _cache_get(key, config.FUNDAMENTAL_TTL)
    if cached:
        return cached
    out = {"symbol": symbol}
    try:
        yf = _yf()
        info = yf.Ticker(symbol).info or {}
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
    except Exception as e:
        logger.warning("fundamentals failed for %s: %s", symbol, e)
        out["error"] = str(e)
    _cache_put(key, out)
    return out


# -- helpers ------------------------------------------------------------
def _f(x, digits: int = 2) -> Optional[float]:
    try:
        v = float(x)
        if v != v:  # NaN
            return None
        return round(v, digits) if digits > 0 else int(v)
    except (TypeError, ValueError):
        return None
