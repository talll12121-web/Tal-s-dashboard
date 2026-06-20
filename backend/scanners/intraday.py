"""
Intraday scanner
================

For the day-trader view. Per watchlist symbol it reports the live quote plus
the momentum signal from your original screener:

    signal = price > VWAP  AND  price > SMA20

When IBKR is connected those indicators come straight from the gateway
(1-min VWAP, exchange-accurate). When it's off we approximate from yfinance
intraday bars so the view still works pre-market or without the gateway.
"""

from __future__ import annotations
import logging

from ..providers import market_data as md
from ..core import store
from . import indicators as ta

logger = logging.getLogger(__name__)


def _yf_intraday_indicators(symbol: str) -> dict:
    """Fallback VWAP/SMA20 from yfinance when IBKR is unavailable."""
    df = md.get_history(symbol, period="5d", interval="1m")
    if df.empty:
        # daily fallback so the row still renders
        d = md.get_history(symbol, period="2mo", interval="1d")
        if d.empty:
            return {}
        sma20 = ta.last(ta.sma(d["Close"], 20))
        return {
            "vwap": None,
            "sma20": round(float(sma20), 2) if sma20 else None,
            "sparkline": [round(x, 2) for x in d["Close"].tail(20).tolist()],
        }
    # restrict to the latest session
    last_day = df.index[-1].date()
    day = df[df.index.map(lambda x: x.date() == last_day)]
    tp = (day["High"] + day["Low"] + day["Close"]) / 3
    vwap = (tp * day["Volume"]).sum() / day["Volume"].sum() if day["Volume"].sum() else None
    sma20 = ta.last(ta.sma(df["Close"], 20))
    return {
        "vwap": round(float(vwap), 2) if vwap else None,
        "sma20": round(float(sma20), 2) if sma20 else None,
        "sparkline": [round(x, 2) for x in day["Close"].tail(30).tolist()],
    }


def scan(symbols: list[str]) -> list[dict]:
    rows = []
    for sym in symbols:
        sym = sym.upper()
        quote = md.get_quote(sym)
        price = quote.get("price")

        live_ind = store.live_indicators(sym)
        ind = live_ind if live_ind else _yf_intraday_indicators(sym)

        vwap = ind.get("vwap")
        sma20 = ind.get("sma20")
        signal = bool(price and vwap and sma20 and price > vwap and price > sma20)

        rows.append({
            "symbol": sym,
            "price": price,
            "change": quote.get("change"),
            "changePct": quote.get("changePct"),
            "dayHigh": quote.get("dayHigh"),
            "dayLow": quote.get("dayLow"),
            "volume": quote.get("volume"),
            "vwap": vwap,
            "sma20": sma20,
            "sparkline": ind.get("sparkline", []),
            "signal": signal,
            "aboveVwap": bool(price and vwap and price > vwap),
            "aboveSma20": bool(price and sma20 and price > sma20),
            "source": quote.get("source"),
        })
    # strongest momentum first
    rows.sort(key=lambda r: (r["signal"], r.get("changePct") or -999), reverse=True)
    return rows
