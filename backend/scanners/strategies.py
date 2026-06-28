"""
Strategy scanners — real, named setups
======================================
Scans a universe of liquid names for specific, documented strategies rather than
generic momentum:

  qm_breakout  — Qullamaggie continuation breakout: a market leader (strong
                 1/3/6-month RS) above a rising 10>20EMA / 50SMA, with ADR% >= ~4%
                 and enough liquidity, consolidating tightly near its highs.
  qm_ep        — Qullamaggie Episodic Pivot: a big gap/surge (>=10%) on heavy
                 volume off a base, still holding above the 20EMA.
  exhaustion_short — short side: a parabolic, overextended runner (far above its
                 20MA, RSI very high) that's starting to fail. Built from general
                 short-selling principles; tune the thresholds to taste.

Cloud-friendly: price data only (yfinance -> Stooq), bounded + cached. Universe
is the Ideas sector map for now (~150 liquid names) — easy to widen later.
Analytical screens, not buy/sell recommendations.
"""

from __future__ import annotations
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

import numpy as np
import pandas as pd

from ..providers import market_data as md
from . import ideas as _ideas

logger = logging.getLogger(__name__)

SCAN_DEADLINE = 26.0
UNIVERSE = sorted({tk for s in _ideas.SECTOR_MAP.values()
                   for lst in s["roles"].values() for tk in lst})

STRATEGY_META = {
    "qm_breakout": {
        "name": "Qullamaggie Breakout",
        "side": "long",
        "desc": "Leaders above a rising 10>20EMA>50SMA, ADR% >= 4%, consolidating tight near highs.",
        "cols": [["adrPct", "ADR%"], ["roc3m", "3M %"], ["distHigh", "From high"], ["tight10", "Tightness"], ["dollarVolM", "$Vol(M)"]],
    },
    "qm_ep": {
        "name": "Episodic Pivot",
        "side": "long",
        "desc": "A gap/surge >= 10% on >= 3x volume off a base, still holding above the 20EMA.",
        "cols": [["gapPct", "Gap %"], ["gapVol", "Gap vol x"], ["daysSinceGap", "Days ago"], ["roc6m", "6M %"], ["dollarVolM", "$Vol(M)"]],
    },
    "exhaustion_short": {
        "name": "Exhaustion Short",
        "side": "short",
        "desc": "Parabolic, overextended runner (far above 20MA, RSI 78+) starting to fail.",
        "cols": [["distEma20", "Above 20MA"], ["rsi", "RSI"], ["roc1m", "1M %"], ["lastChg", "Last day"], ["dollarVolM", "$Vol(M)"]],
    },
}


def _rsi(close, p=14):
    d = close.diff()
    g = d.clip(lower=0).rolling(p).mean()
    l = (-d.clip(upper=0)).rolling(p).mean()
    return float((100 - 100 / (1 + g / l.replace(0, np.nan))).iloc[-1])


def _metrics(ticker: str) -> dict | None:
    df = md.get_history(ticker, period="1y", interval="1d")
    if df.empty or len(df) < 130:
        return None
    close, high, low = df["Close"], df["High"], df["Low"]
    vol = df.get("Volume", pd.Series(0.0, index=close.index)).fillna(0)
    price = float(close.iloc[-1])
    ema10 = float(close.ewm(span=10, adjust=False).mean().iloc[-1])
    ema20s = close.ewm(span=20, adjust=False).mean()
    ema20 = float(ema20s.iloc[-1])
    sma50 = float(close.rolling(50).mean().iloc[-1])

    def roc(p):
        return (price / float(close.iloc[-p - 1]) - 1) * 100 if len(close) > p else None

    chg = close.pct_change() * 100
    g_window = chg.tail(10)
    gap_pct = float(g_window.max())
    g_pos = int(np.argmax(g_window.values))
    days_since_gap = len(g_window) - 1 - g_pos
    g_idx = g_window.index[g_pos]
    base_vol = float(vol.tail(60).mean()) or 1.0
    gap_vol = float(vol.loc[g_idx]) / base_vol
    high50 = float(close.tail(50).max())
    lo10, hi10 = float(close.tail(10).min()), float(close.tail(10).max())
    return {
        "ticker": ticker, "price": round(price, 2),
        "ema10": ema10, "ema20": ema20, "sma50": sma50,
        "adrPct": round(float(((high - low) / close).tail(20).mean() * 100), 2),
        "roc1m": round(roc(21), 2) if roc(21) is not None else None,
        "roc3m": round(roc(63), 2) if roc(63) is not None else None,
        "roc6m": round(roc(126), 2) if roc(126) is not None else None,
        "dollarVolM": round(price * float(vol.tail(20).mean()) / 1e6, 1),
        "distHigh": round((price / high50 - 1) * 100, 1),
        "distEma20": round((price / ema20 - 1) * 100, 1),
        "rsi": round(_rsi(close), 0),
        "gapPct": round(gap_pct, 1),
        "gapVol": round(gap_vol, 1),
        "daysSinceGap": days_since_gap,
        "tight10": round((hi10 / lo10 - 1) * 100, 1) if lo10 else None,
        "ema20Rising": bool(ema20 > float(ema20s.iloc[-11])),
        "lastChg": round(float(chg.iloc[-1]), 2),
        "stacked": price > ema10 > ema20 > sma50,
    }


def _qm_breakout(m):
    if not (m["adrPct"] and m["adrPct"] >= 3.5 and m["dollarVolM"] >= 3 and m["stacked"]
            and m["ema20Rising"] and m["roc3m"] is not None and m["roc3m"] >= 18
            and m["distHigh"] >= -10):
        return None
    tight = m["tight10"] or 99
    score = (min(40, m["roc3m"] / 2) + min(25, m["adrPct"] * 3)
             + min(20, (10 + m["distHigh"]) * 2) + max(0, 15 - tight))
    return round(min(100, score), 1)


def _qm_ep(m):
    if not (m["gapPct"] >= 9 and m["gapVol"] >= 3 and m["daysSinceGap"] <= 7
            and m["price"] > m["ema20"]):
        return None
    score = min(50, m["gapPct"] * 2) + min(30, m["gapVol"] * 5) + max(0, 20 - m["daysSinceGap"] * 2)
    return round(min(100, score), 1)


def _exhaustion_short(m):
    if not (m["distEma20"] >= 25 and m["rsi"] >= 78 and m["roc1m"] is not None
            and m["roc1m"] >= 25 and m["lastChg"] < 0):
        return None
    score = min(40, m["distEma20"]) + min(30, (m["rsi"] - 70)) + min(20, m["roc1m"] / 3) + min(10, -m["lastChg"] * 3)
    return round(min(100, score), 1)


_FNS = {"qm_breakout": _qm_breakout, "qm_ep": _qm_ep, "exhaustion_short": _exhaustion_short}
_CACHE: dict = {}


def scan(strategy: str) -> dict:
    if strategy not in _FNS:
        return {"strategy": strategy, "error": "unknown strategy", "matches": []}
    now = time.time()
    hit = _CACHE.get(strategy)
    if hit and (now - hit[0]) < 300:
        return hit[1]

    fn = _FNS[strategy]
    matches = []
    t0 = time.time()
    ex = ThreadPoolExecutor(max_workers=12)
    try:
        futs = {ex.submit(_metrics, tk): tk for tk in UNIVERSE}
        for fut, tk in futs.items():
            remaining = SCAN_DEADLINE - (time.time() - t0)
            try:
                m = fut.result(timeout=max(0.1, remaining))
            except FuturesTimeout:
                continue
            except Exception as e:
                logger.debug("scan %s: %s", tk, e)
                continue
            if not m:
                continue
            score = fn(m)
            if score is not None:
                m["score"] = score
                matches.append(m)
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    matches.sort(key=lambda x: x["score"], reverse=True)
    meta = STRATEGY_META[strategy]
    out = {"strategy": strategy, "name": meta["name"], "side": meta["side"],
           "desc": meta["desc"], "cols": meta["cols"], "scanned": len(UNIVERSE),
           "matches": matches}
    _CACHE[strategy] = (now, out)
    return out


def list_strategies() -> list:
    return [{"key": k, "name": v["name"], "side": v["side"], "desc": v["desc"]}
            for k, v in STRATEGY_META.items()]
