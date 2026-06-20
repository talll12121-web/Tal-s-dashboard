"""
Sector heat / rotation
=======================

For the long-term / macro view. Computes momentum across the 11 SPDR sector
ETFs to show which parts of the market are leading and lagging - the classic
"sector heat map" that frames where to hunt for longs.

For each sector ETF we compute 1-week, 1-month and 3-month returns, RSI, and
relative strength vs SPY, then rank them. The result drives a heat-map style
panel in the UI.
"""

from __future__ import annotations
import logging

from ..providers import market_data as md
from . import indicators as ta
from .. import config

logger = logging.getLogger(__name__)


def scan() -> dict:
    spy = md.get_history(config.BENCHMARK, period="6mo", interval="1d")
    spy_1m = ta.pct_change(spy["Close"], 21) if not spy.empty else None

    rows = []
    for etf, name in config.SECTOR_ETFS.items():
        df = md.get_history(etf, period="6mo", interval="1d")
        if df.empty:
            continue
        close = df["Close"]
        ret_1w = ta.pct_change(close, 5)
        ret_1m = ta.pct_change(close, 21)
        ret_3m = ta.pct_change(close, 63)
        rsi14 = ta.last(ta.rsi(close, 14))
        rel = round(ret_1m - spy_1m, 2) if (ret_1m is not None and spy_1m is not None) else None
        rows.append({
            "etf": etf,
            "sector": name,
            "ret1w": ret_1w,
            "ret1m": ret_1m,
            "ret3m": ret_3m,
            "rsi": round(float(rsi14), 1) if rsi14 is not None else None,
            "relStrength": rel,
            "sparkline": [round(x, 2) for x in close.tail(30).tolist()],
        })

    rows.sort(key=lambda r: (r.get("ret1m") if r.get("ret1m") is not None else -999), reverse=True)
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return {
        "benchmark": config.BENCHMARK,
        "benchmark1m": spy_1m,
        "sectors": rows,
    }
