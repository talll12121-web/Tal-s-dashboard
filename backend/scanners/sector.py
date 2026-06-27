"""
Sector heat / rotation  (ported from the Sector-scanner repo)
=============================================================

Ranks sector + thematic ETFs by a composite momentum "heat" score (0-100) to
show what's rotating in. Ported from sector_heat.py — the rich version with the
full ETF universe (11 SPDR sectors + 24 thematic ETFs), ROC blend across
1/3/6-month lookbacks, relative strength vs SPY, and MA-position / volume
adjustments.

Cloud-friendly: uses the unified market_data layer (yfinance -> Stooq fallback),
so it works on Render where Yahoo is blocked. scan_historical() reconstructs
weekly heat snapshots straight from price history — rotation over time without
needing to accumulate daily snapshots.
"""

from __future__ import annotations
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

import pandas as pd

from ..providers import market_data as md
from . import indicators as ta
from .. import config

logger = logging.getLogger(__name__)

SCAN_DEADLINE = 24.0

STANDARD_SECTORS = {
    "XLK": "Technology", "XLF": "Financials", "XLE": "Energy", "XLV": "Healthcare",
    "XLI": "Industrials", "XLY": "Consumer Discretionary", "XLP": "Consumer Staples",
    "XLU": "Utilities", "XLB": "Materials", "XLRE": "Real Estate", "XLC": "Communication Services",
}
THEMATIC_ETFS = {
    "SMH": "Semiconductors", "SOXX": "Semiconductors (alt)", "IGV": "Software",
    "ITA": "Aerospace & Defense", "URA": "Uranium", "LIT": "Lithium & Battery",
    "KWEB": "China Internet", "ARKK": "Disruptive Innovation", "ICLN": "Clean Energy",
    "TAN": "Solar", "JETS": "Airlines", "KRE": "Regional Banks", "GDX": "Gold Miners",
    "XBI": "Biotech", "IBB": "Biotech (alt)", "HACK": "Cybersecurity", "BOTZ": "Robotics & AI",
    "CIBR": "Cybersecurity (alt)", "PAVE": "Infrastructure", "COPX": "Copper Miners",
    "REMX": "Rare Earth & Strategic Metals", "XOP": "Oil & Gas Exploration",
    "OIH": "Oil Services", "TLT": "Long Treasury Bonds",
}
ALL_ETFS = {**STANDARD_SECTORS, **THEMATIC_ETFS}

# Heat-score weights / lookbacks
_SHORT, _MEDIUM, _LONG = 21, 63, 126
_W_SHORT, _W_MEDIUM, _W_LONG, _W_RS = 0.30, 0.40, 0.20, 0.10


def _roc(prices: pd.Series, lb: int):
    if len(prices) < lb + 1:
        return None
    return (prices.iloc[-1] / prices.iloc[-lb - 1] - 1) * 100


def _ytd(prices: pd.Series, as_of=None):
    ref = as_of if as_of is not None else pd.Timestamp.now()
    y = prices[prices.index >= pd.Timestamp(ref.year, 1, 1)]
    if len(y) < 2:
        return None
    return (y.iloc[-1] / y.iloc[0] - 1) * 100


def _ma_dist(prices: pd.Series, period: int):
    if len(prices) < period:
        return None
    ma = prices.rolling(period).mean().iloc[-1]
    return (prices.iloc[-1] / ma - 1) * 100 if ma else None


def _vol_ratio(volume: pd.Series):
    if len(volume) < 60:
        return None
    base = volume.tail(60).mean()
    return (volume.tail(20).mean() / base) if base else None


def _norm(v, lo, hi):
    if v is None:
        return 50.0
    return max(0.0, min(100.0, (v - lo) / (hi - lo) * 100))


def _heat(prices: pd.Series, volume: pd.Series, spy_ret: dict, as_of=None) -> dict | None:
    if prices is None or len(prices) < _LONG + 1:
        return None
    roc1, roc3, roc6 = _roc(prices, _SHORT), _roc(prices, _MEDIUM), _roc(prices, _LONG)
    if roc1 is None or roc3 is None or roc6 is None:
        return None
    roc_ytd = _ytd(prices, as_of) or 0.0
    rs3 = roc3 - (spy_ret.get("3m") or 0)
    rs_ytd = roc_ytd - (spy_ret.get("ytd") or 0)
    pct50 = _ma_dist(prices, 50) or 0.0
    pct200 = _ma_dist(prices, 200) or 0.0
    vr = _vol_ratio(volume) or 1.0

    heat = (_norm(roc1, -10, 15) * _W_SHORT + _norm(roc3, -15, 25) * _W_MEDIUM +
            _norm(roc6, -20, 40) * _W_LONG + _norm(rs3, -15, 15) * _W_RS)
    if pct50 > 0 and pct200 > 0:
        heat *= 1.05
    elif pct50 < 0 and pct200 < 0:
        heat *= 0.95
    if vr > 1.5:
        heat *= 1.05
    elif vr < 0.7:
        heat *= 0.95
    heat = max(0.0, min(100.0, heat))
    return {
        "price": round(float(prices.iloc[-1]), 2),
        "ret1m": round(roc1, 2), "ret3m": round(roc3, 2), "ret6m": round(roc6, 2),
        "retYtd": round(roc_ytd, 2), "rs3m": round(rs3, 2), "rsYtd": round(rs_ytd, 2),
        "pct50ma": round(pct50, 2), "pct200ma": round(pct200, 2),
        "above50ma": pct50 > 0, "above200ma": pct200 > 0,
        "volRatio": round(vr, 2), "heat": round(heat, 1),
    }


def _spy_returns(as_of=None) -> tuple[dict, pd.DataFrame]:
    spy = md.get_history(config.BENCHMARK, period="1y", interval="1d")
    if spy.empty:
        return {"1m": None, "3m": None, "6m": None, "ytd": None}, spy
    c = spy["Close"]
    if as_of is not None:
        c = c[c.index <= as_of]
    return {"1m": _roc(c, _SHORT), "3m": _roc(c, _MEDIUM),
            "6m": _roc(c, _LONG), "ytd": _ytd(c, as_of)}, spy


def _analyze_etf(item, spy_ret):
    etf, name = item
    df = md.get_history(etf, period="1y", interval="1d")
    if df.empty:
        return None
    h = _heat(df["Close"], df.get("Volume", pd.Series(dtype=float)), spy_ret)
    if not h:
        return None
    h.update({
        "etf": etf, "sector": name,
        "ret1w": ta.pct_change(df["Close"], 5),
        "sparkline": [round(x, 2) for x in df["Close"].tail(30).tolist()],
    })
    return h


_CACHE: dict = {"t": 0.0, "data": None}
_CACHE_TTL = 300  # 5 min - Sector tab, Ideas and Overview all hit this


def scan() -> dict:
    now = time.time()
    if _CACHE["data"] is not None and (now - _CACHE["t"]) < _CACHE_TTL:
        return _CACHE["data"]
    data = _scan()
    _CACHE.update({"t": now, "data": data})
    return data


def _scan() -> dict:
    spy_ret, _ = _spy_returns()
    rows = []
    t0 = time.time()
    ex = ThreadPoolExecutor(max_workers=min(10, len(ALL_ETFS)))
    try:
        futures = {ex.submit(_analyze_etf, item, spy_ret): item[0] for item in ALL_ETFS.items()}
        for fut, etf in futures.items():
            remaining = SCAN_DEADLINE - (time.time() - t0)
            try:
                r = fut.result(timeout=max(0.1, remaining))
            except FuturesTimeout:
                logger.warning("sector %s exceeded scan deadline - skipped", etf)
                continue
            if r:
                rows.append(r)
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    rows.sort(key=lambda r: r.get("heat", -1), reverse=True)
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return {"benchmark": config.BENCHMARK, "benchmark1m": spy_ret.get("1m"),
            "benchmarkYtd": spy_ret.get("ytd"), "sectors": rows}


def scan_historical(weeks_back: int = 12) -> dict:
    """Reconstruct weekly heat snapshots for the standard sectors over the past
    weeks_back weeks, straight from 2y of price history. Returns
    {history: {etf: [heat, ...]}, weeks: [date,...], names: {etf: name}}."""
    etfs = STANDARD_SECTORS
    series_cache: dict[str, pd.DataFrame] = {}

    def hist(sym):
        if sym not in series_cache:
            series_cache[sym] = md.get_history(sym, period="2y", interval="1d")
        return series_cache[sym]

    spy_df = hist(config.BENCHMARK)
    if spy_df.empty:
        return {"history": {}, "weeks": [], "names": etfs}
    spy_close = spy_df["Close"]

    history = {e: [] for e in etfs}
    weeks = []
    for w in range(weeks_back, 0, -1):
        as_of = (pd.Timestamp.now() - pd.Timedelta(weeks=w)).normalize()
        sc = spy_close[spy_close.index <= as_of]
        if len(sc) < _LONG + 1:
            continue
        spy_ret = {"3m": _roc(sc, _MEDIUM), "ytd": _ytd(sc, as_of)}
        weeks.append(as_of.strftime("%Y-%m-%d"))
        for etf in etfs:
            df = hist(etf)
            if df.empty:
                history[etf].append(None); continue
            c = df["Close"][df["Close"].index <= as_of]
            v = df.get("Volume", pd.Series(dtype=float))
            v = v[v.index <= as_of] if len(v) else v
            h = _heat(c, v, spy_ret, as_of=as_of)
            history[etf].append(h["heat"] if h else None)
    return {"history": history, "weeks": weeks, "names": etfs}
