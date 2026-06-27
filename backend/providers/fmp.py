"""
Financial Modeling Prep (FMP) fundamentals fallback
===================================================

FMP's free tier serves complete fundamentals as clean JSON and is datacenter-
friendly, so it's the preferred fallback when yfinance is blocked on Render.
Unlike SEC EDGAR (filings only, no price), FMP also provides the price-derived
fields - P/E, P/B, market cap, sector - so it can fill the whole scorecard.

Endpoints (free tier, requires apikey):
  * /v3/profile/{sym}          -> name, sector, market cap, price, beta
  * /v3/ratios-ttm/{sym}       -> P/E, P/B, margins, ROE, debt/equity, current ratio
  * /v3/financial-growth/{sym} -> revenue & EPS growth (annual)

Returns the SAME standard fundamentals dict shape as market_data.get_fundamentals.
FMP ratios are already fractions (0.25 = 25%) like yfinance, except debt/equity
which is a raw ratio (~1.2) and is scaled x100 to match yfinance's ~120.

No key configured -> returns {} (caller falls back to the next source).
"""

from __future__ import annotations
import logging
import requests

from .. import config

logger = logging.getLogger(__name__)

_BASE = "https://financialmodelingprep.com/api/v3"


def _f(x):
    try:
        v = float(x)
        return v if v == v else None  # drop NaN
    except (TypeError, ValueError):
        return None


def _first(d: dict, *keys):
    for k in keys:
        v = _f(d.get(k))
        if v is not None:
            return v
    return None


def _get(path: str, key: str, **params):
    """GET an FMP endpoint, returning the first row of its JSON list, or {}."""
    try:
        params["apikey"] = key
        r = requests.get(f"{_BASE}/{path}", params=params, timeout=10)
        if not r.ok:
            return {}
        data = r.json()
        if isinstance(data, list):
            return data[0] if data else {}
        return data or {}
    except Exception as e:
        logger.debug("fmp %s: %s", path, e)
        return {}


def get_quote(symbol: str) -> dict | None:
    """Near-real-time quote from FMP for the Analyzer headline price + charts.
    Tries the /stable then legacy /v3 endpoint; returns None if no key/unavailable
    so callers fall back to the end-of-day close."""
    key = config.FMP_API_KEY
    if not key:
        return None
    sym = symbol.upper().strip()
    endpoints = [
        ("https://financialmodelingprep.com/stable/quote", {"symbol": sym}),
        (f"{_BASE}/quote/{sym}", {}),
    ]
    for url, params in endpoints:
        try:
            p = dict(params); p["apikey"] = key
            r = requests.get(url, params=p, timeout=8)
            if not r.ok:
                continue
            data = r.json()
            row = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else None)
            price = _f(row.get("price")) if row else None
            if price is None:
                continue
            pct = row.get("changePercentage")
            if pct is None:
                pct = row.get("changesPercentage")
            return {
                "price": price,
                "change": _f(row.get("change")),
                "changePct": _f(pct),
                "prevClose": _f(row.get("previousClose")),
                "source": "fmp",
            }
        except Exception as e:
            logger.debug("fmp quote %s: %s", sym, e)
    return None


def get_fundamentals(symbol: str) -> dict:
    """Standard fundamentals dict from FMP, or {} if unavailable/no key."""
    key = con