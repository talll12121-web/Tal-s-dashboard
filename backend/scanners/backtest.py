"""
Backtest engine
===============
Reproducible historical backtest of the dashboard's bullish signal: walk a
ticker's daily history, fire the signal at each past bar, and measure the
forward 5/10/20-day outcome. Reconstructed straight from price history (Stooq-
friendly), so it runs on the cloud with no waiting for live outcomes.

Signal (deterministic, mirrors the dashboard's trend+momentum bias):
    close > 200MA  AND  EMA9 > EMA20  AND  40 <= RSI(14) <= 72  AND  MACD hist > 0
A "signal" fires on the bar the condition flips from False to True.

For each horizon we report win rate, average / win / loss return, expectancy,
and the *edge* vs a buy-and-hold baseline (average forward return of every bar),
so you can see whether the signal actually beats just being long.
"""

from __future__ import annotations
import logging

import numpy as np
import pandas as pd

from ..providers import market_data as md

logger = logging.getLogger(__name__)

HORIZONS = [5, 10, 20]
SIGNAL_DESC = "close > 200MA · EMA9 > EMA20 · RSI 40-72 · MACD histogram > 0"


def _indicators(close: pd.Series):
    sma200 = close.rolling(200).mean()
    ema9 = close.ewm(span=9, adjust=False).mean()
    ema20 = close.ewm(span=20, adjust=False).mean()
    d = close.diff()
    g = d.clip(lower=0).rolling(14).mean()
    l = (-d.clip(upper=0)).rolling(14).mean()
    rsi = 100 - 100 / (1 + g / l.replace(0, np.nan))
    ef = close.ewm(span=12, adjust=False).mean()
    es = close.ewm(span=26, adjust=False).mean()
    macd = ef - es
    hist = macd - macd.ewm(span=9, adjust=False).mean()
    return sma200, ema9, ema20, rsi, hist


def backtest(ticker: str, horizons=None) -> dict:
    horizons = horizons or HORIZONS
    sym = ticker.upper().strip()
    df = md.get_history(sym, period="5y", interval="1d")
    if df.empty or len(df) < 260:
        return {"symbol": sym, "error": "not enough price history"}
    close = df["Close"]
    sma200, ema9, ema20, rsi, hist = _indicators(close)
    bull = (close > sma200) & (ema9 > ema20) & (rsi >= 40) & (rsi <= 72) & (hist > 0)
    entries = bull & ~bull.shift(1, fill_value=False)
    entry_locs = np.where(entries.values)[0]
    n = len(close)
    idx = df.index

    out = {
        "symbol": sym,
        "price": round(float(close.iloc[-1]), 2),
        "signalDesc": SIGNAL_DESC,
        "totalSignals": int(entries.sum()),
        "years": round(n / 252, 1),
        "horizons": [],
        "signals": [],
        "equity": [],
    }

    for h in horizons:
        fwd = (close.shift(-h) / close - 1) * 100
        rets = np.array([float(fwd.iloc[i]) for i in entry_locs if i + h < n])
        baseline = float(fwd.dropna().mean()) if fwd.notna().any() else 0.0
        if len(rets):
            wins = rets[rets > 0]
            losses = rets[rets <= 0]
            out["horizons"].append({
                "days": h,
                "signals": int(len(rets)),
                "winRate": round(len(wins) / len(rets) * 100, 1),
                "avgReturn": round(float(rets.mean()), 2),
                "avgWin": round(float(wins.mean()), 2) if len(wins) else 0.0,
                "avgLoss": round(float(losses.mean()), 2) if len(losses) else 0.0,
                "expectancy": round(float(rets.mean()), 2),
                "baseline": round(baseline, 2),
                "edge": round(float(rets.mean()) - baseline, 2),
                "best": round(float(rets.max()), 2),
                "worst": round(float(rets.min()), 2),
            })
        else:
            out["horizons"].append({"days": h, "signals": 0})

    # recent signals + their forward outcome at the middle horizon
    h = horizons[1] if len(horizons) > 1 else horizons[0]
    recent = []
    for i in entry_locs[-14:]:
        fwd_ret = round(float(close.iloc[i + h] / close.iloc[i] - 1) * 100, 2) if i + h < n else None
        recent.append({"date": idx[i].strftime("%Y-%m-%d"),
                       "price": round(float(close.iloc[i]), 2),
                       "fwd": fwd_ret, "fwdDays": h})
    out["signals"] = list(reversed(recent))

    # equity curve: take every signal sequentially at horizon h, compound
    eq = [100.0]
    for i in entry_locs:
        if i + h < n:
            eq.append(round(eq[-1] * (1 + float(close.iloc[i + h] / close.iloc[i] - 1)), 1))
    out["equity"] = eq
    out["equityHorizon"] = h
    return 