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


def get_fundamentals(symbol: str) -> dict:
    """Standard fundamentals dict from FMP, or {} if unavailable/no key."""
    key = config.FMP_API_KEY
    if not key:
        return {}
    sym = symbol.upper().strip()
    profile = _get(f"profile/{sym}", key)
    ratios = _get(f"ratios-ttm/{sym}", key)
    growth = _get(f"financial-growth/{sym}", key, period="annual", limit=1)
    if not profile and not ratios:
        return {}

    dte = _first(ratios, "debtEquityRatioTTM", "debtToEquityTTM")
    out = {
        "symbol": sym,
        "name": profile.get("companyName"),
        "sector": profile.get("sector"),
        "industry": profile.get("industry"),
        "marketCap": _f(profile.get("mktCap")) or _f(profile.get("marketCap")),
        "beta": _f(profile.get("beta")),
        "trailingPE": _first(ratios, "peRatioTTM", "priceEarningsRatioTTM"),
        "pegRatio": _first(ratios, "priceEarningsToGrowthRatioTTM", "pegRatioTTM"),
        "priceToBook": _first(ratios, "priceToBookRatioTTM", "pbRatioTTM"),
        "profitMargins": _first(ratios, "netProfitMarginTTM"),
        "returnOnEquity": _first(ratios, "returnOnEquityTTM"),
        "revenueGrowth": _f(growth.get("revenueGrowth")),
        "earningsGrowth": _first(growth, "epsgrowth", "epsGrowth", "netIncomeGrowth"),
        "debtToEquity": round(dte * 100, 2) if dte is not None else None,
        "currentRatio": _first(ratios, "currentRatioTTM"),
        "dividendYield": _first(ratios, "dividendYielTTM", "dividendYieldTTM"),
        "source": "fmp",
    }
    return out
