"""
Fundamental ranking
====================

For the long-term investor view. Pulls fundamentals (yfinance) for the
long-term watchlist and builds a composite quality/value/growth score so the
strongest businesses surface at the top.

Composite = quality (margins, ROE, low debt)
          + growth  (revenue & earnings growth)
          + value   (reasonable PE / PEG)
Each pillar is 0-100; the composite is their weighted average.
"""

from __future__ import annotations
import logging

from ..providers import market_data as md

logger = logging.getLogger(__name__)


def _band(value, good, great, invert=False):
    """Map a metric onto 0-100. invert=True means lower is better."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if invert:
        if v <= good:
            return 100
        if v >= great:
            return 0
        return round(100 * (great - v) / (great - good), 1)
    else:
        if v >= great:
            return 100
        if v <= good:
            return 0
        return round(100 * (v - good) / (great - good), 1)


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def analyze(symbol: str) -> dict:
    f = md.get_fundamentals(symbol)

    quality = _avg([
        _band(f.get("profitMargins"), 0.0, 0.30),
        _band(f.get("returnOnEquity"), 0.0, 0.30),
        _band(f.get("debtToEquity"), 50, 200, invert=True),
    ])
    growth = _avg([
        _band(f.get("revenueGrowth"), 0.0, 0.30),
        _band(f.get("earningsGrowth"), 0.0, 0.40),
    ])
    value = _avg([
        _band(f.get("trailingPE"), 10, 50, invert=True),
        _band(f.get("pegRatio"), 1.0, 3.0, invert=True),
    ])
    composite = _avg([
        (quality * 0.4) if quality is not None else None,
        (growth * 0.35) if growth is not None else None,
        (value * 0.25) if value is not None else None,
    ])
    # rescale composite back to 0-100 from the weighted pieces
    parts = [p for p in [quality, growth, value] if p is not None]
    composite = _avg(parts)

    return {
        "symbol": symbol.upper(),
        "name": f.get("name"),
        "sector": f.get("sector"),
        "marketCap": f.get("marketCap"),
        "trailingPE": _round(f.get("trailingPE")),
        "forwardPE": _round(f.get("forwardPE")),
        "pegRatio": _round(f.get("pegRatio")),
        "profitMargins": _pct(f.get("profitMargins")),
        "returnOnEquity": _pct(f.get("returnOnEquity")),
        "revenueGrowth": _pct(f.get("revenueGrowth")),
        "earningsGrowth": _pct(f.get("earningsGrowth")),
        "debtToEquity": _round(f.get("debtToEquity")),
        "dividendYield": _pct(f.get("dividendYield")),
        "recommendation": f.get("recommendationKey"),
        "targetMeanPrice": _round(f.get("targetMeanPrice")),
        "qualityScore": quality,
        "growthScore": growth,
        "valueScore": value,
        "compositeScore": composite,
    }


def scan(symbols: list[str]) -> list[dict]:
    rows = []
    for sym in symbols:
        try:
            rows.append(analyze(sym))
        except Exception as e:
            logger.warning("fundamental analyze %s: %s", sym, e)
    rows.sort(key=lambda r: (r.get("compositeScore") if r.get("compositeScore") is not None else -1), reverse=True)
    return rows


def _round(x, d=2):
    try:
        return round(float(x), d)
    except (TypeError, ValueError):
        return None


def _pct(x):
    try:
        return round(float(x) * 100, 2)
    except (TypeError, ValueError):
        return None
