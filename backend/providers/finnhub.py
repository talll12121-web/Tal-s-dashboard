"""
Finnhub fundamentals fallback
=============================

yfinance's .info endpoint is the richest free fundamentals source, but Yahoo
blocks it from datacenter IPs (Render). Finnhub's free tier serves the same
metrics as clean JSON and is far more datacenter-friendly, so we use it as a
fundamentals fallback when yfinance is blocked.

Endpoints (free tier, ~60 calls/min):
  * /stock/metric?metric=all  -> valuation, margins, growth, balance-sheet ratios
  * /stock/profile2           -> company name, sector (finnhubIndustry), market cap

Returns the SAME standard fundamentals dict shape that market_data.get_fundamentals
produces, so the scorecard code doesn't care where the numbers came from. All
values are normalised to yfinance's scale (fractions for margins/growth/yield,
~percent-magnitude number for debtToEquity) so the scoring bands keep working.

No key configured -> returns {} (caller falls back to the next source).
"""

from __future__ import annotations
import logging
import requests

from .. import config

logger = logging.getLogger(__name__)

_BASE = "https://finnhub.io/api/v1"


def _f(x):
    try:
        v = float(x)
        return v if v == v else None  # drop NaN
    except (TypeError, ValueError):
        return None


def _first(metric: dict, *keys):
    """First non-None value among several possible Finnhub metric keys."""
    for k in keys:
        v = _f(metric.get(k))
        if v is not None:
            return v
    return None


def _frac(pct):
    """Finnhub reports margins/growth/ROE as percent numbers (e.g. 25.4).
    yfinance uses fractions (0.254); normalise so the scoring bands match."""
    return round(pct / 100.0, 6) if pct is not None else None


def get_fundamentals(symbol: str) -> dict:
    """Standard fundamentals dict from Finnhub, or {} if unavailable/no key."""
    key = config.FINNHUB_API_KEY
    if not key:
        return {}
    sym = symbol.upper().strip()
    try:
        m = requests.get(f"{_BASE}/stock/metric",
                         params={"symbol": sym, "metric": "all", "token": key},
                         timeout=10)
        metric = (m.json() or {}).get("metric", {}) if m.ok else {}
        p = requests.get(f"{_BASE}/stock/profile2",
                         params={"symbol": sym, "token": key}, timeout=10)
        profile = p.json() or {} if p.ok else {}
    except Exception as e:
        logger.debug("finnhub fundamentals %s: %s", sym, e)
        return {}

    if not metric and not profile:
        return {}

    # totalDebt/totalEquity comes as a ratio (~1.2); yfinance scales it ~120.
    dte = _first(metric, "totalDebt/totalEquityAnnual",
                 "totalDebt/totalEquityQuarterly",
                 "longTermDebt/equityAnnual")
    market_cap = _f(profile.get("marketCapitalization"))
    out = {
        "symbol": sym,
        "name": profile.get("name"),
        "sector": profile.get("finnhubIndustry"),
        "industry": profile.get("finnhubIndustry"),
        "marketCap": round(market_cap * 1e6) if market_cap is not None else None,
        "trailingPE": _first(metric, "peTTM", "peBasicExclExtraTTM", "peAnnual"),
        "forwardPE": _first(metric, "peForward", "forwardPE"),
        "pegRatio": _first(metric, "pegTTM", "pegRatio"),
        "priceToBook": _first(metric, "pbAnnual", "pbQuarterly"),
        "profitMargins": _frac(_first(metric, "netProfitMarginTTM", "netProfitMarginAnnual")),
        "returnOnEquity": _frac(_first(metric, "roeTTM", "roeRfy", "roeAnnual")),
        "revenueGrowth": _frac(_first(metric, "revenueGrowthTTMYoy", "revenueGrowthQuarterlyYoy")),
        "earningsGrowth": _frac(_first(metric, "epsGrowthTTMYoy", "epsGrowthQuarterlyYoy")),
        "debtToEquity": round(dte * 100, 2) if dte is not None else None,
        "currentRatio": _first(metric, "currentRatioAnnual", "currentRatioQuarterly"),
        "dividendYield": _frac(_first(metric, "dividendYieldIndicatedAnnual", "currentDividendYieldTTM")),
        "fiftyTwoWeekHigh": _first(metric, "52WeekHigh"),
        "fiftyTwoWeekLow": _first(metric, "52WeekLow"),
        "beta": _first(metric, "beta"),
        "source": "finnhub",
    }
    return out
