"""
Swing scanner  (NEW)
====================

For the multi-day / swing-trader view. Uses daily bars (yfinance) to score
each watchlist symbol on classic swing setups and produce a 0-100 swing score
plus a human-readable setup tag.

Signals evaluated per symbol:
  * Trend        : price above rising 20 & 50 SMA
  * Pullback     : price near (within ~3%) the rising 20-SMA in an uptrend
  * Breakout     : price within 2% of, or above, the 20-day high
  * RSI regime   : 40-65 healthy (room to run), >70 extended, <30 oversold
  * Rel strength : 1-month return vs SPY
  * Volatility   : ATR% for position sizing context

The score weights trend + setup quality so the best, cleanest setups rise
to the top of the table.
"""

from __future__ import annotations
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

from ..providers import market_data as md
from . import indicators as ta
from .. import config

logger = logging.getLogger(__name__)

# Whole-scan wall-clock budget (seconds), comfortably under the frontend abort.
SCAN_DEADLINE = 22.0

# Last successful per-symbol row, so a single failed poll (yfinance blocked AND
# Stooq throttled at the same moment) reuses the prior result instead of making
# the symbol flicker out of the table. Stale entries expire after LAST_GOOD_TTL.
LAST_GOOD_TTL = 60 * 30
_last_good: dict[str, tuple[float, dict]] = {}


def _bench_return(lookback: int = 21) -> float | None:
    df = md.get_history(config.BENCHMARK, period="3mo", interval="1d")
    if df.empty:
        return None
    return ta.pct_change(df["Close"], lookback)


def analyze(symbol: str, bench_ret: float | None) -> dict | None:
    df = md.get_history(symbol, period="8mo", interval="1d")
    if df.empty or len(df) < 60:
        return None

    close = df["Close"]
    price = float(close.iloc[-1])
    sma20 = ta.sma(close, 20)
    sma50 = ta.sma(close, 50)
    rsi14 = ta.last(ta.rsi(close, 14))
    atr14 = ta.last(ta.atr(df, 14))

    s20, s50 = float(sma20.iloc[-1]), float(sma50.iloc[-1])
    s20_prev = float(sma20.iloc[-6])
    s50_prev = float(sma50.iloc[-6])
    rising20 = s20 > s20_prev
    rising50 = s50 > s50_prev

    hi20 = float(close.tail(20).max())
    ret_1m = ta.pct_change(close, 21)
    ret_3m = ta.pct_change(close, 63)
    atr_pct = round(atr14 / price * 100, 2) if atr14 and price else None
    rel_strength = round(ret_1m - bench_ret, 2) if (ret_1m is not None and bench_ret is not None) else None

    # -- scoring --------------------------------------------------------
    score = 0
    tags = []

    uptrend = price > s20 > s50 and rising20 and rising50
    if uptrend:
        score += 35
        tags.append("Uptrend")
    elif price > s50 and rising50:
        score += 15

    near_20 = abs(price - s20) / s20 <= 0.03
    if uptrend and near_20 and price >= s20:
        score += 25
        tags.append("Pullback to 20SMA")

    near_high = price >= hi20 * 0.98
    if near_high:
        score += 20
        tags.append("Breakout watch")

    if rsi14 is not None:
        if 40 <= rsi14 <= 65:
            score += 12
        elif rsi14 < 30:
            score += 8
            tags.append("Oversold")
        elif rsi14 > 70:
            score -= 8
            tags.append("Extended")

    if rel_strength is not None and rel_strength > 0:
        score += 8
        tags.append("Leads SPY")

    score = max(0, min(100, score))

    return {
        "symbol": symbol.upper(),
        "price": round(price, 2),
        "sma20": round(s20, 2),
        "sma50": round(s50, 2),
        "rsi": round(float(rsi14), 1) if rsi14 is not None else None,
        "atrPct": atr_pct,
        "ret1m": ret_1m,
        "ret3m": ret_3m,
        "relStrength": rel_strength,
        "high20": round(hi20, 2),
        "score": score,
        "setup": ", ".join(tags) if tags else "No setup",
        "sparkline": [round(x, 2) for x in close.tail(30).tolist()],
    }


def _analyze_or_last_good(sym: str, bench: float | None) -> dict | None:
    """Analyze a symbol; on failure or empty data, reuse the last good row."""
    try:
        r = analyze(sym, bench)
    except Exception as e:
        logger.warning("swing analyze %s: %s", sym, e)
        r = None
    if r:
        _last_good[sym] = (time.time(), r)
        return r
    cached = _last_good.get(sym)
    if cached and (time.time() - cached[0]) < LAST_GOOD_TTL:
        row = dict(cached[1])
        row["stale"] = True  # let the UI mark it if it wants
        return row
    return None


def scan(symbols: list[str]) -> list[dict]:
    rows = []
    if not symbols:
        return rows
    bench = _bench_return()
    t0 = time.time()
    ex = ThreadPoolExecutor(max_workers=min(8, len(symbols)))
    try:
        futures = {ex.submit(_analyze_or_last_good, s, bench): s for s in symbols}
        for fut, sym in futures.items():
            remaining = SCAN_DEADLINE - (time.time() - t0)
            try:
                r = fut.result(timeout=max(0.1, remaining))
            except FuturesTimeout:
                logger.warning("swing %s exceeded scan deadline - skipped", sym)
                continue
            if r:
                rows.append(r)
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows
