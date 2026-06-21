"""
Fundamental ranking - 5-framework scorecard
============================================

For the long-term investor view. Five frameworks scored 0-100 -> composite:
  1. Valuation     - P/E, PEG, P/B (cheaper = higher)
  2. Profitability - net margin, ROE
  3. Growth        - revenue & earnings growth
  4. Health        - debt/equity, current ratio
  5. Momentum      - 3M & 6M price return
Symbols scored in parallel so the watchlist returns quickly.
"""

from __future__ import annotations
import logging
from concurrent.futures import ThreadPoolExecutor

from ..providers import market_data as md
from . import indicators as ta

logger = logging.getLogger(__name__)


def _band(value, good, great, invert=False):
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if invert:
        if v <= good:
            return 100.0
        if v >= great:
            return 0.0
        return round(100 * (great - v) / (great - good), 1)
    if v >= great:
        return 100.0
    if v <= good:
        return 0.0
    return round(100 * (v - good) / (great - good), 1)


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def analyze(symbol: str) -> dict:
    f = md.get_fundamentals(symbol)
    valuation = _avg([
        _band(f.get("trailingPE"), 10, 50, invert=True),
        _band(f.get("pegRatio"), 1.0, 3.0, invert=True),
        _band(f.get("priceToBook"), 1.0, 10.0, invert=True),
    ])
    profitability = _avg([
        _band(f.get("profitMargins"), 0.0, 0.30),
        _band(f.get("returnOnEquity"), 0.0, 0.30),
    ])
    growth = _avg([
        _band(f.get("revenueGrowth"), 0.0, 0.30),
        _band(f.get("earningsGrowth"), 0.0, 0.40),
    ])
    health = _avg([
        _band(f.get("debtToEquity"), 50, 200, invert=True),
        _band(f.get("currentRatio"), 1.0, 3.0),
    ])
    momentum = None
    df = md.get_history(symbol, period="8mo", interval="1d")
    if not df.empty and len(df) > 60:
        ret3m = ta.pct_change(df["Close"], 63)
        ret6m = ta.pct_change(df["Close"], 126)
        momentum = _avg([_band(ret3m, -10, 30), _band(ret6m, -15, 50)])
    composite = _avg([valuation, profitability, growth, health, momentum])
    return {
        "symbol": symbol.upper(),
        "name": f.get("name") or symbol.upper(),
        "sector": f.get("sector"),
        "marketCap": f.get("marketCap"),
        "price": md.get_quote(symbol).get("price"),
        "trailingPE": _round(f.get("trailingPE")),
        "pegRatio": _round(f.get("pegRatio")),
        "profitMargins": _pct(f.get("profitMargins")),
        "revenueGrowth": _pct(f.get("revenueGrowth")),
        "debtToEquity": _round(f.get("debtToEquity")),
        "dividendYield": _pct(f.get("dividendYield")),
        "recommendation": f.get("recommendationKey"),
        "valuation": valuation,
        "profitability": profitability,
        "growth": growth,
        "health": health,
        "momentum": momentum,
        "compositeScore": composite,
    }


def _safe_analyze(sym):
    try:
        return analyze(sym)
    except Exception as e:
        logger.warning("fundamental analyze %s: %s", sym, e)
        return None


def scan(symbols: list[str]) -> list[dict]:
    rows = []
    if not symbols:
        return rows
    with ThreadPoolExecutor(max_workers=8) as ex:
        for r in ex.map(_safe_analyze, symbols):
            if r:
                rows.append(r)
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
