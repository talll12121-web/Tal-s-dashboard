"""
5-Floor Analyzer  (deterministic port of the 5-floor repo)
==========================================================
Per-ticker institutional-style scorecard: 5 Floors x 5 signals = 25 signals,
each graded green / yellow / red, rolled into per-floor scores and a composite
verdict.

The original 5-floor app scored signals with Claude prompts; this is a
deterministic re-implementation computed from price/volume + indicators — so it's
reproducible and runs on Render with no broker or API key. (An AI "explain why"
layer can slot on top later via ANTHROPIC_API_KEY.)

  F1  Order Flow      - where price sits, volume behaviour, accumulation
  F2  Mean Reversion  - RSI, Stochastic, stretch from MAs, MACD
  F3  Gamma / Options - NEEDS an options feed -> shown as N/A on the cloud
  F4  Dark Pool       - VWAP, volume divergence, OBV, proximity to highs
  F5  Market Regime   - EMA cross, 200MA, relative strength, trend, market health
"""

from __future__ import annotations
import logging
import pandas as pd

from ..providers import market_data as md
from .. import config

logger = logging.getLogger(__name__)

GREEN, YELLOW, RED, NA = "green", "yellow", "red", "na"
_PTS = {GREEN: 1.0, YELLOW: 0.5, RED: 0.0}


def _grade(val, green, yellow, invert=False):
    """Grade a value: green if past `green`, yellow if past `yellow`, else red.
    invert=True means lower is better."""
    if val is None:
        return NA
    if invert:
        if val <= green:
            return GREEN
        if val <= yellow:
            return YELLOW
        return RED
    if val >= green:
        return GREEN
    if val >= yellow:
        return YELLOW
    return RED


def _band(val, lo, hi, edge=None):
    """green inside [lo,hi]; red outside an optional wider [edge_lo,edge_hi]."""
    if val is None:
        return NA
    if lo <= val <= hi:
        return GREEN
    if edge and edge[0] <= val <= edge[1]:
        return YELLOW
    return RED


def _sig(name, color, detail):
    return {"name": name, "color": color, "detail": detail}


def _rsi(close, p=14):
    d = close.diff()
    g = d.clip(lower=0).rolling(p).mean()
    l = (-d.clip(upper=0)).rolling(p).mean()
    rs = g / l.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


def _floor1(close, high, low, vol):
    price = float(close.iloc[-1])
    hi52, lo52 = float(close.tail(252).max()), float(close.tail(252).min())
    pos = (price - lo52) / (hi52 - lo52) if hi52 > lo52 else 0.5
    vr = float(vol.tail(10).mean()) / float(vol.tail(60).mean()) if len(vol) >= 60 and vol.tail(60).mean() else 1.0
    lb_h, lb_l, lb_c = float(high.iloc[-1]), float(low.iloc[-1]), price
    cir = (lb_c - lb_l) / (lb_h - lb_l) if lb_h > lb_l else 0.5
    up = vol[close.diff() > 0].tail(20).sum()
    dn = vol[close.diff() < 0].tail(20).sum()
    udv = (up / dn) if dn else 2.0
    hl = float(low.tail(10).min()) > float(low.iloc[-20:-10].min()) if len(low) >= 20 else False
    return [
        _sig("Position in 52w range", _grade(pos, 0.5, 0.25), f"{pos*100:.0f}% of range"),
        _sig("Volume expansion", _grade(vr, 1.25, 0.85), f"{vr:.2f}x 60d avg"),
        _sig("Close strength", _grade(cir, 0.6, 0.35), f"closed {cir*100:.0f}% up the bar"),
        _sig("Up/down volume", _grade(udv, 1.15, 0.85), f"{udv:.2f} up:down (20d)"),
        _sig("Higher lows", GREEN if hl else RED, "making higher lows" if hl else "not yet"),
    ]


def _floor2(close):
    price = float(close.iloc[-1])
    rsi = float(_rsi(close).iloc[-1])
    ll, hh = float(close.tail(14).min()), float(close.tail(14).max())
    stoch = (price - ll) / (hh - ll) * 100 if hh > ll else 50.0
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std().iloc[-1]
    z = (price - float(sma20.iloc[-1])) / float(std20) if std20 else 0.0
    sma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None
    dist200 = (price / sma200 - 1) * 100 if sma200 else None
    ef, es = close.ewm(span=12, adjust=False).mean(), close.ewm(span=26, adjust=False).mean()
    macd = ef - es
    sigl = macd.ewm(span=9, adjust=False).mean()
    hist = macd - sigl
    h_now, h_prev = float(hist.iloc[-1]), float(hist.iloc[-3])
    macd_c = GREEN if (h_now > 0 and h_now >= h_prev) else (RED if (h_now < 0 and h_now <= h_prev) else YELLOW)
    return [
        _sig("RSI (14)", _band(rsi, 40, 65, edge=(30, 72)), f"RSI {rsi:.0f}"),
        _sig("Stochastic", _band(stoch, 20, 80, edge=(10, 90)), f"%K {stoch:.0f}"),
        _sig("Stretch from 20MA", _grade(abs(z), 1.5, 2.5, invert=True), f"{z:+.1f}σ from 20MA"),
        _sig("Distance from 200MA", _grade(abs(dist200) if dist200 is not None else None, 12, 28, invert=True),
             f"{dist200:+.0f}% vs 200MA" if dist200 is not None else "n/a"),
        _sig("MACD histogram", macd_c, f"hist {h_now:+.2f}"),
    ]


def _floor3():
    n = "Needs an options feed"
    return [
        _sig("Price vs max pain", NA, n), _sig("Put/call ratio", NA, n),
        _sig("IV rank", NA, n), _sig("Unusual OI", NA, n), _sig("Gamma wall", NA, n),
    ]


def _floor4(close, high, low, vol):
    price = float(close.iloc[-1])
    tp = (high + low + close) / 3
    vwap20 = float((tp.tail(20) * vol.tail(20)).sum() / vol.tail(20).sum()) if vol.tail(20).sum() else price
    vwap_pos = (price / vwap20 - 1) * 100
    prev_below = float(close.iloc[-6]) < float((tp.iloc[-26:-6] * vol.iloc[-26:-6]).sum() / vol.iloc[-26:-6].sum()) if len(close) >= 26 and vol.iloc[-26:-6].sum() else False
    reclaim = prev_below and price > vwap20
    pc = (price / float(close.iloc[-6]) - 1) * 100 if len(close) >= 6 else 0
    vc = (float(vol.tail(5).mean()) / float(vol.iloc[-10:-5].mean()) - 1) * 100 if len(vol) >= 10 and vol.iloc[-10:-5].mean() else 0
    div = GREEN if (pc > 0 and vc > 0) else (RED if (pc > 0 and vc < -10) else YELLOW)
    obv = (vol * close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))).cumsum()
    obv_up = float(obv.iloc[-1]) > float(obv.iloc[-20]) if len(obv) >= 20 else False
    hi52 = float(close.tail(252).max())
    pfh = (price / hi52 - 1) * 100
    return [
        _sig("Price vs VWAP (20d)", _grade(vwap_pos, 0, -1), f"{vwap_pos:+.1f}% vs VWAP"),
        _sig("VWAP reclaim", GREEN if reclaim else YELLOW, "reclaimed VWAP" if reclaim else "no recent reclaim"),
        _sig("Volume divergence", div, f"px {pc:+.0f}% / vol {vc:+.0f}%"),
        _sig("Accumulation (OBV)", GREEN if obv_up else RED, "OBV rising" if obv_up else "OBV falling"),
        _sig("Proximity to 52w high", _grade(pfh, -5, -15), f"{pfh:+.0f}% from high"),
    ]


def _floor5(close, spy):
    price = float(close.iloc[-1])
    ema9 = float(close.ewm(span=9, adjust=False).mean().iloc[-1])
    ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
    sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else price
    sma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else price
    r1 = (price / float(close.iloc[-22]) - 1) * 100 if len(close) >= 22 else 0
    rs = None
    if spy is not None and not spy.empty and len(spy) >= 22:
        spy_r = (float(spy.iloc[-1]) / float(spy.iloc[-22]) - 1) * 100
        rs = r1 - spy_r
    stack = price > sma50 > sma200
    spy_healthy = None
    if spy is not None and not spy.empty and len(spy) >= 200:
        spy_healthy = float(spy.iloc[-1]) > float(spy.rolling(200).mean().iloc[-1])
    return [
        _sig("EMA 9 vs 20", GREEN if ema9 > ema20 else RED, "9 above 20" if ema9 > ema20 else "9 below 20"),
        _sig("Above 200MA", GREEN if price > sma200 else RED, "above" if price > sma200 else "below"),
        _sig("Relative strength vs SPY", _grade(rs, 0, -5) if rs is not None else NA,
             f"{rs:+.0f}% vs SPY (1m)" if rs is not None else "n/a"),
        _sig("Trend stack (50>200)", GREEN if stack else (YELLOW if price > sma200 else RED),
             "price>50>200" if stack else "partial/none"),
        _sig("Market regime (SPY)", (GREEN if spy_healthy else RED) if spy_healthy is not None else NA,
             "SPY above 200MA" if spy_healthy else "SPY below 200MA" if spy_healthy is not None else "n/a"),
    ]


def _floor_score(signals):
    graded = [s for s in signals if s["color"] != NA]
    if not graded:
        return None, NA
    score = sum(_PTS[s["color"]] for s in graded) / len(graded) * 5  # 0-5
    color = GREEN if score >= 3.4 else (YELLOW if score >= 2.0 else RED)
    return round(score, 1), color


def analyze(ticker: str) -> dict:
    sym = ticker.upper().strip()
    df = md.get_history(sym, period="2y", interval="1d")
    if df.empty or len(df) < 210:
        return {"symbol": sym, "error": "not enough price history", "floors": []}
    close, high, low = df["Close"], df["High"], df["Low"]
    vol = df.get("Volume", pd.Series(dtype=float)).fillna(0)
    spy = md.get_history(config.BENCHMARK, period="1y", interval="1d")
    spy_close = spy["Close"] if not spy.empty else None

    defs = [
        ("F1", "Order Flow", _floor1(close, high, low, vol)),
        ("F2", "Mean Reversion", _floor2(close)),
        ("F3", "Gamma / Options", _floor3()),
        ("F4", "Dark Pool", _floor4(close, high, low, vol)),
        ("F5", "Market Regime", _floor5(close, spy_close)),
    ]
    floors = []
    for key, name, sigs in defs:
        score, color = _floor_score(sigs)
        floors.append({"key": key, "name": name, "score": score, "color": color, "signals": sigs})

    avail = [f for f in floors if f["score"] is not None]
    green_floors = sum(1 for f in avail if f["color"] == GREEN)
    composite = round(sum(f["score"] for f in avail) / len(avail) / 5 * 100, 0) if avail else None
    if green_floors >= 3:
        verdict, vcolor = "Strong setup", GREEN
    elif green_floors == 2:
        verdict, vcolor = "Constructive", GREEN
    elif green_floors == 1:
        verdict, vcolor = "Mixed", YELLOW
    else:
        verdict, vcolor = "Avoid", RED

    return {
        "symbol": sym,
        "price": round(float(close.iloc[-1]), 2),
        "composite": composite,
        "greenFloors": green_floors,
        "availFloors": len(avail),
        "verdict": verdict,
        "verdictColor": vcolor,
        "floors": floors,
    }
