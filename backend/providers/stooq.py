"""
Stooq data source (datacenter-friendly fallback)
================================================

yfinance (Yahoo) is great locally but Yahoo frequently blocks requests coming
from cloud/datacenter IPs (Render, AWS, etc.), which leaves the cloud dashboard
empty. Stooq serves free daily OHLCV as plain CSV with no API key and works fine
from datacenter IPs, so we use it as an automatic fallback for history + quotes.

  history:  https://stooq.com/q/d/l/?s=aapl.us&i=d   -> Date,Open,High,Low,Close,Volume
  quote:    https://stooq.com/q/l/?s=aapl.us&f=sd2t2ohlcv&h&e=csv

Daily only (no intraday) — intraday VWAP still comes from IBKR via the bridge.
"""

from __future__ import annotations
import io
import logging
import requests
import pandas as pd

logger = logging.getLogger(__name__)

_HISTORY_URL = "https://stooq.com/q/d/l/?s={sym}&i=d"
_QUOTE_URL = "https://stooq.com/q/l/?s={sym}&f=sd2t2ohlcv&h&e=csv"

_PERIOD_DAYS = {
    "5d": 7, "1mo": 31, "2mo": 62, "3mo": 93, "6mo": 186,
    "8mo": 248, "1y": 366, "2y": 732,
}


def _to_stooq(symbol: str) -> str:
    s = symbol.lower().strip()
    if "." not in s:
        s += ".us"
    return s


def get_history(symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
    """Daily OHLCV as a DataFrame matching yfinance's column names. Empty on failure."""
    if interval != "1d":
        return pd.DataFrame()  # Stooq free tier: daily only
    try:
        url = _HISTORY_URL.format(sym=_to_stooq(symbol))
        r = requests.get(url, timeout=12, headers={"User-Agent": "Mozilla/5.0"})
        if not r.ok or not r.text or r.text.startswith("<"):
            return pd.DataFrame()
        df = pd.read_csv(io.StringIO(r.text))
        if df.empty or "Close" not in df.columns:
            return pd.DataFrame()
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date").sort_index()
        days = _PERIOD_DAYS.get(period, 186)
        cutoff = df.index.max() - pd.Timedelta(days=days)
        df = df[df.index >= cutoff]
        # ensure expected numeric columns exist
        for col in ("Open", "High", "Low", "Close", "Volume"):
            if col not in df.columns:
                df[col] = 0.0
        return df[["Open", "High", "Low", "Close", "Volume"]]
    except Exception as e:
        logger.debug("stooq history %s: %s", symbol, e)
        return pd.DataFrame()


def get_quote(symbol: str) -> dict | None:
    """Latest close as a quote. Uses the last two daily bars for prevClose/change."""
    df = get_history(symbol, period="1mo")
    if df.empty or len(df) < 1:
        return None
    last = float(df["Close"].iloc[-1])
    prev = float(df["Close"].iloc[-2]) if len(df) >= 2 else None
    return {
        "symbol": symbol.upper(),
        "price": round(last, 2),
        "prevClose": round(prev, 2) if prev else None,
        "change": round(last - prev, 2) if prev else None,
        "changePct": round((last - prev) / prev * 100, 2) if prev else None,
        "dayHigh": round(float(df["High"].iloc[-1]), 2),
        "dayLow": round(float(df["Low"].iloc[-1]), 2),
        "volume": int(df["Volume"].iloc[-1]) if df["Volume"].iloc[-1] else None,
        "source": "Stooq",
    }
