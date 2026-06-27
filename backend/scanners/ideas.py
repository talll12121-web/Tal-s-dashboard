"""
Master Ranking — actionable ideas by role within hot sectors
============================================================
Ported and extended from the Sector-scanner repo (master_scanner.py +
momentum_scanner.py + sector_to_stocks.py). Cloud-friendly: uses only price
history (market_data -> Stooq fallback), so it runs on Render where Yahoo is
blocked.

Pipeline:
  1. sector.scan() ranks 35 ETFs by composite heat (already cloud-ported).
  2. Take the top hot sectors that have a curated stock map.
  3. For every stock in those sectors, compute a momentum read (ROC, acceleration,
     volume surge, relative strength, run-stage).
  4. Combine sector heat + stock momentum into a final score, tag each idea with
     its ROLE (Leader / Pure Play / Picks & Shovels / Toll Booth / Arms Dealer /
     Second Derivative) and a computed "Catch-up" (laggard) flag.
  5. Group ranked ideas by hot sector.

Roles are how a stock plays a theme — same idea as "picks & shovels", broadened:
  Leader            - the dominant, obvious franchise
  Pure Play         - most direct, highest-beta bet on the theme
  Picks & Shovels   - sells the tools/infrastructure everyone in the theme needs
  Toll Booth        - takes a recurring cut of usage (royalties, exchanges, rent)
  Arms Dealer       - supplies all competitors, wins regardless of who wins
  Second Derivative - one step removed beneficiary (often mispriced)
  Catch-up (flag)   - quality name in the group that hasn't run yet, RS turning
"""

from __future__ import annotations
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

import pandas as pd

from ..providers import market_data as md
from . import indicators as ta
from . import sector as sector_mod

logger = logging.getLogger(__name__)

SCAN_DEADLINE = 26.0
TOP_SECTORS = 5
W_HEAT, W_MOMENTUM = 0.40, 0.60

ROLE_ORDER = ["Leader", "Pure Play", "Picks & Shovels", "Toll Booth",
              "Arms Dealer", "Second Derivative"]

# Per-ETF role map. Roles are illustrative archetypes, not buy recommendations.
SECTOR_MAP: dict[str, dict] = {
    "XLK": {"name": "Technology", "roles": {
        "Leader": ["MSFT", "AAPL", "NVDA"],
        "Pure Play": ["NVDA", "PLTR", "SMCI"],
        "Picks & Shovels": ["ASML", "AMAT", "LRCX", "KLAC", "ANET"],
        "Arms Dealer": ["TSM", "ASML"],
        "Second Derivative": ["VRT", "ETN", "POWL"]}},
    "XLF": {"name": "Financials", "roles": {
        "Leader": ["JPM", "BRK.B", "V"],
        "Pure Play": ["JPM", "BAC", "GS"],
        "Picks & Shovels": ["ICE", "CME", "SPGI", "MCO", "MSCI", "FDS"],
        "Toll Booth": ["V", "MA", "ICE", "CME", "SPGI"],
        "Arms Dealer": ["SPGI", "MSCI"]}},
    "XLE": {"name": "Energy", "roles": {
        "Leader": ["XOM", "CVX", "COP"],
        "Pure Play": ["FANG", "DVN", "EOG"],
        "Picks & Shovels": ["SLB", "HAL", "BKR"],
        "Toll Booth": ["EPD", "KMI", "WMB", "ET"],
        "Arms Dealer": ["SLB", "HAL"]}},
    "XLV": {"name": "Healthcare", "roles": {
        "Leader": ["LLY", "UNH", "JNJ"],
        "Pure Play": ["LLY", "NVO"],
        "Picks & Shovels": ["TMO", "DHR", "IQV", "ICLR"],
        "Arms Dealer": ["TMO", "DHR"]}},
    "XLI": {"name": "Industrials", "roles": {
        "Leader": ["GE", "CAT", "RTX"],
        "Picks & Shovels": ["PH", "EMR", "ROK", "ETN"],
        "Second Derivative": ["ETN", "POWL", "GEV"]}},
    "XLY": {"name": "Consumer Discretionary", "roles": {
        "Leader": ["AMZN", "TSLA", "HD"],
        "Toll Booth": ["AMZN", "BKNG"],
        "Picks & Shovels": ["ORLY", "AZO", "POOL"]}},
    "XLP": {"name": "Consumer Staples", "roles": {
        "Leader": ["WMT", "PG", "COST"],
        "Picks & Shovels": ["SYY", "ADM", "BG"]}},
    "XLU": {"name": "Utilities", "roles": {
        "Leader": ["NEE", "SO", "DUK"],
        "Pure Play": ["CEG", "VST", "TLN"],
        "Second Derivative": ["GEV", "ETN", "POWL"],
        "Picks & Shovels": ["GEV", "PWR"]}},
    "XLB": {"name": "Materials", "roles": {
        "Leader": ["LIN", "SHW", "APD"],
        "Picks & Shovels": ["ECL", "PPG"],
        "Second Derivative": ["NUE", "STLD", "FCX"]}},
    "XLRE": {"name": "Real Estate", "roles": {
        "Leader": ["PLD", "AMT", "EQIX"],
        "Toll Booth": ["AMT", "CCI", "EQIX", "PSA"],
        "Second Derivative": ["DLR", "EQIX", "IRM"]}},
    "XLC": {"name": "Communication Services", "roles": {
        "Leader": ["META", "GOOGL", "NFLX"],
        "Toll Booth": ["GOOGL", "META"],
        "Arms Dealer": ["TTD", "APP"]}},
    "SMH": {"name": "Semiconductors", "roles": {
        "Leader": ["NVDA", "TSM", "AVGO"],
        "Pure Play": ["NVDA", "AMD", "MU"],
        "Picks & Shovels": ["ASML", "AMAT", "LRCX", "KLAC", "ENTG"],
        "Arms Dealer": ["TSM", "ASML"],
        "Second Derivative": ["VRT", "ANET"]}},
    "SOXX": {"name": "Semiconductors", "roles": {
        "Leader": ["NVDA", "AVGO", "AMD"],
        "Picks & Shovels": ["ASML", "AMAT", "LRCX", "KLAC", "TER", "ONTO"],
        "Arms Dealer": ["TSM", "ASML"]}},
    "IGV": {"name": "Software", "roles": {
        "Leader": ["MSFT", "CRM", "ORCL"],
        "Pure Play": ["PLTR", "CRWD", "NOW"],
        "Toll Booth": ["MSFT", "ORCL", "SNOW", "DDOG"],
        "Picks & Shovels": ["SNOW", "DDOG", "NET", "MDB"]}},
    "ITA": {"name": "Aerospace & Defense", "roles": {
        "Leader": ["RTX", "LMT", "BA"],
        "Pure Play": ["LMT", "GD", "NOC"],
        "Picks & Shovels": ["HEI", "TDG", "CW", "HXL"],
        "Arms Dealer": ["LMT", "RTX", "GD"]}},
    "URA": {"name": "Uranium", "roles": {
        "Leader": ["CCJ"],
        "Pure Play": ["NXE", "UEC", "UUUU", "DNN"],
        "Picks & Shovels": ["BWXT", "LEU"],
        "Second Derivative": ["CEG", "VST"]}},
    "LIT": {"name": "Lithium & Battery", "roles": {
        "Leader": ["ALB", "SQM"],
        "Pure Play": ["LAC", "PLL"],
        "Picks & Shovels": ["ENS"]}},
    "KWEB": {"name": "China Internet", "roles": {
        "Leader": ["BABA", "PDD", "JD"],
        "Pure Play": ["BABA", "BIDU"],
        "Picks & Shovels": ["NTES", "BILI"]}},
    "ARKK": {"name": "Disruptive Innovation", "roles": {
        "Leader": ["TSLA", "COIN", "HOOD"],
        "Pure Play": ["PLTR", "RBLX", "PATH"],
        "Picks & Shovels": ["TWLO", "TDOC"]}},
    "ICLN": {"name": "Clean Energy", "roles": {
        "Leader": ["FSLR", "ENPH", "NEE"],
        "Pure Play": ["FSLR", "RUN"],
        "Picks & Shovels": ["NXT", "ARRY", "SHLS"],
        "Second Derivative": ["PWR", "GEV"]}},
    "TAN": {"name": "Solar", "roles": {
        "Leader": ["FSLR", "ENPH", "SEDG"],
        "Pure Play": ["FSLR", "RUN", "CSIQ"],
        "Picks & Shovels": ["NXT", "ARRY", "SHLS"]}},
    "JETS": {"name": "Airlines", "roles": {
        "Leader": ["DAL", "UAL", "LUV"],
        "Picks & Shovels": ["BA", "GE", "HEI", "TDG"]}},
    "KRE": {"name": "Regional Banks", "roles": {
        "Leader": ["USB", "PNC", "TFC"],
        "Picks & Shovels": ["FI", "FIS", "JKHY"]}},
    "GDX": {"name": "Gold Miners", "roles": {
        "Leader": ["NEM", "GOLD", "AEM"],
        "Pure Play": ["NEM", "AEM", "KGC"],
        "Toll Booth": ["FNV", "WPM", "RGLD", "SAND"]}},
    "XBI": {"name": "Biotech", "roles": {
        "Leader": ["VRTX", "REGN", "GILD"],
        "Picks & Shovels": ["TMO", "DHR", "IQV", "ICLR"],
        "Arms Dealer": ["TMO", "DHR"]}},
    "IBB": {"name": "Biotech", "roles": {
        "Leader": ["VRTX", "AMGN", "GILD"],
        "Picks & Shovels": ["TMO", "DHR", "IQV"]}},
    "HACK": {"name": "Cybersecurity", "roles": {
        "Leader": ["CRWD", "PANW", "FTNT"],
        "Pure Play": ["CRWD", "ZS", "S"],
        "Picks & Shovels": ["OKTA", "CYBR", "TENB"]}},
    "CIBR": {"name": "Cybersecurity", "roles": {
        "Leader": ["CRWD", "PANW", "CSCO"],
        "Pure Play": ["ZS", "S", "CYBR"]}},
    "BOTZ": {"name": "Robotics & AI", "roles": {
        "Leader": ["NVDA", "ISRG", "ABB"],
        "Picks & Shovels": ["ROK", "EMR", "SYM"]}},
    "PAVE": {"name": "Infrastructure", "roles": {
        "Leader": ["CAT", "DE", "URI"],
        "Picks & Shovels": ["PWR", "MYRG", "EME", "PRIM"],
        "Second Derivative": ["VMC", "MLM", "ETN", "POWL"]}},
    "COPX": {"name": "Copper Miners", "roles": {
        "Leader": ["FCX", "SCCO", "BHP"],
        "Pure Play": ["FCX", "IVN", "ERO"],
        "Second Derivative": ["TECK", "HBM"]}},
    "REMX": {"name": "Rare Earth & Strategic Metals", "roles": {
        "Leader": ["MP", "LYC"],
        "Pure Play": ["MP", "USAR"]}},
    "XOP": {"name": "Oil & Gas Exploration", "roles": {
        "Leader": ["XOM", "CVX", "COP"],
        "Pure Play": ["FANG", "DVN", "EOG"],
        "Picks & Shovels": ["SLB", "HAL", "NOV"]}},
    "OIH": {"name": "Oil Services", "roles": {
        "Leader": ["SLB", "HAL", "BKR"],
        "Picks & Shovels": ["NOV", "CHX", "WHD"]}},
}

# -- momentum scoring (ported from momentum_scanner.py) -----------------
_LB_4W, _LB_13W, _LB_26W = 20, 63, 126


def _roc(prices: pd.Series, lb: int):
    if len(prices) < lb + 1:
        return None
    return (prices.iloc[-1] / prices.iloc[-lb - 1] - 1) * 100


def _ytd(prices: pd.Series):
    y = prices[prices.index >= pd.Timestamp(pd.Timestamp.now().year, 1, 1)]
    if len(y) < 2:
        return None
    return (y.iloc[-1] / y.iloc[0] - 1) * 100


def _momentum(close: pd.Series, volume: pd.Series, spy_4w: float, spy_4w_ago: float) -> dict | None:
    if close is None or len(close) < _LB_26W + 1:
        return None
    roc_4w, roc_13w = _roc(close, _LB_4W), _roc(close, _LB_13W)
    if roc_4w is None or roc_13w is None:
        return None
    roc_ytd = _ytd(close) or 0.0
    accel = roc_4w - roc_13w

    vol_surge = 1.0
    if len(volume) >= 60:
        base = float(volume.tail(60).mean())
        vol_surge = float(volume.tail(10).mean()) / base if base > 0 else 1.0

    rs_4w = roc_4w - spy_4w
    rs_4w_ago = None
    if len(close) >= _LB_4W * 2 + 1:
        past = _roc(close.iloc[:-_LB_4W], _LB_4W)
        if past is not None:
            rs_4w_ago = past - spy_4w_ago
    rs_turning = (rs_4w_ago is not None) and (rs_4w_ago < 0) and (rs_4w > 0)

    high_52 = float(close.tail(252).max())
    near_high = float(close.iloc[-1]) >= high_52 * 0.97
    ma200 = close.rolling(200).mean()
    above_200 = bool(close.iloc[-1] > ma200.iloc[-1]) if not pd.isna(ma200.iloc[-1]) else False
    crossed = False
    if len(ma200.dropna()) >= 20:
        crossed = bool(close.iloc[-1] > ma200.iloc[-1] and close.iloc[-20] <= ma200.iloc[-20])

    score = 0.0
    score += min(30.0, max(0.0, accel * 2.0))
    score += min(25.0, max(0.0, (vol_surge - 1) * 25))
    score += min(20.0, max(0.0, rs_4w * 2.0))
    if near_high:
        score += 15.0
    if crossed:
        score += 10.0
    score = min(100.0, score)

    if roc_4w > 12 and roc_13w > 20:
        stage = "Extended"
    elif accel < -3 or vol_surge < 0.7:
        stage = "Cooling"
    elif accel > 3 and vol_surge > 1.3 and (rs_turning or crossed):
        stage = "Early"
    elif accel > 0 and above_200 and vol_surge >= 1.0:
        stage = "Mid-Run"
    else:
        stage = "Neutral"

    return {
        "roc4w": round(roc_4w, 2), "roc13w": round(roc_13w, 2), "rocYtd": round(roc_ytd, 2),
        "accel": round(accel, 2), "volSurge": round(vol_surge, 2), "rs4w": round(rs_4w, 2),
        "rsTurning": rs_turning, "near52wHigh": near_high, "above200ma": above_200,
        "crossed200ma": crossed, "momentumScore": round(score, 1), "runStage": stage,
    }


def _spy_ref() -> tuple[float, float]:
    spy = md.get_history("SPY", period="1y", interval="1d")
    if spy.empty:
        return 0.0, 0.0
    c = spy["Close"]
    s4 = _roc(c, _LB_4W) or 0.0
    s4_ago = (_roc(c.iloc[:-_LB_4W], _LB_4W) or 0.0) if len(c) >= _LB_4W * 2 + 1 else 0.0
    return s4, s4_ago


def _score(ticker: str, spy_4w: float, spy_4w_ago: float) -> dict | None:
    df = md.get_history(ticker, period="1y", interval="1d")
    if df.empty:
        return None
    m = _momentum(df["Close"], df.get("Volume", pd.Series(dtype=float)), spy_4w, spy_4w_ago)
    if not m:
        return None
    m["ticker"] = ticker
    m["price"] = round(float(df["Close"].iloc[-1]), 2)
    m["sparkline"] = [round(x, 2) for x in df["Close"].tail(30).tolist()]
    return m


def scan(top_sectors: int = TOP_SECTORS) -> dict:
    heat = sector_mod.scan()
    hot = [s for s in heat.get("sectors", []) if s["etf"] in SECTOR_MAP][:top_sectors]
    if not hot:
        return {"sectors": [], "benchmark1m": heat.get("benchmark1m")}

    # unique tickers across the hot sectors' role lists
    uniq = set()
    for s in hot:
        for tks in SECTOR_MAP[s["etf"]]["roles"].values():
            uniq.update(tks)

    spy_4w, spy_4w_ago = _spy_ref()
    scores: dict[str, dict] = {}
    t0 = time.time()
    ex = ThreadPoolExecutor(max_workers=min(12, max(1, len(uniq))))
    try:
        futures = {ex.submit(_score, tk, spy_4w, spy_4w_ago): tk for tk in uniq}
        for fut, tk in futures.items():
            remaining = SCAN_DEADLINE - (time.time() - t0)
            try:
                r = fut.result(timeout=max(0.1, remaining))
            except FuturesTimeout:
                continue
            except Exception as e:
                logger.debug("ideas score %s: %s", tk, e)
                continue
            if r:
                scores[tk] = r
    finally:
        ex.shutdown(wait=False, cancel_futures=True)

    out_sectors = []
    for s in hot:
        etf = s["etf"]
        heat_score = s.get("heat", 0) or 0
        roles_map = SECTOR_MAP[etf]["roles"]
        ideas = []
        seen = set()
        for role in ROLE_ORDER:
            for tk in roles_map.get(role, []):
                if tk in seen:
                    continue
                sc = scores.get(tk)
                if not sc:
                    continue
                seen.add(tk)
                final = min(100.0, heat_score * W_HEAT + sc["momentumScore"] * W_MOMENTUM)
                # "Catch-up": quality laggard not yet extended, RS just turning up
                catchup = bool(sc["rsTurning"] and sc["roc4w"] < 6 and sc["runStage"] in ("Early", "Neutral"))
                ideas.append({**sc, "role": role, "finalScore": round(final, 1), "catchup": catchup})
        ideas.sort(key=lambda x: x["finalScore"], reverse=True)
        out_sectors.append({
            "etf": etf, "name": SECTOR_MAP[etf]["name"], "heat": round(heat_score, 1),
            "rank": s.get("rank"), "ideas": ideas,
        })
    return {"sectors": out_sectors, "benchmark1m": heat.get("benchmark1m"), "roles": ROLE_ORDER}
