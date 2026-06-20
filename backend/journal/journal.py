"""
Trading journal
===============

Stores your executions and turns them into round-trip trades with realised P&L
and performance stats. Storage goes through core/db.py, so it works identically
on local SQLite and cloud Postgres.

Two ways to feed it:
  1. Live IBKR executions  - pushed from the desktop bridge (or a local IBKR run).
  2. CSV upload            - an IBKR Flex Query / Activity Statement export, or
                             any CSV with symbol / datetime / qty / price / side.

Fills are stored raw, then matched FIFO per symbol into closed trades so we can
compute win rate, profit factor, expectancy, R-multiples, etc.
"""

from __future__ import annotations
import logging
import io
import csv
from collections import defaultdict, deque

from ..core import db

logger = logging.getLogger(__name__)


def init_db():
    db.init_db()


def _norm_side(raw: str, shares: float) -> tuple[str, float]:
    r = (raw or "").upper()
    if r in ("BOT", "BUY", "B"):
        return "BUY", abs(shares)
    if r in ("SLD", "SELL", "S"):
        return "SELL", abs(shares)
    return ("BUY", shares) if shares >= 0 else ("SELL", abs(shares))


def add_fills(fills: list[dict]) -> int:
    added = 0
    for f in fills:
        side, shares = _norm_side(f.get("side"), float(f.get("shares") or 0))
        exec_id = f.get("execId") or f"{f.get('symbol')}-{f.get('time')}-{side}-{shares}-{f.get('price')}"
        # detect whether it already exists (so we can report a true "added" count)
        existing = db.q("SELECT 1 FROM fills WHERE exec_id = :e", e=exec_id)
        if existing:
            continue
        try:
            db.execute(
                "INSERT INTO fills (exec_id, symbol, side, shares, price, commission, ts, source) "
                "VALUES (:e,:sym,:side,:sh,:pr,:co,:ts,:src) ON CONFLICT (exec_id) DO NOTHING",
                e=exec_id, sym=(f.get("symbol") or "").upper(), side=side, sh=shares,
                pr=float(f.get("price") or 0), co=float(f.get("commission") or 0),
                ts=str(f.get("time") or ""), src=f.get("source", "ibkr"))
            added += 1
        except Exception as e:
            logger.debug("skip fill: %s", e)
    return added


# -- CSV ingest ---------------------------------------------------------
_COL_ALIASES = {
    "symbol": ["symbol", "ticker", "underlyingsymbol"],
    "side": ["side", "buy/sell", "action"],
    "shares": ["quantity", "qty", "shares"],
    "price": ["t. price", "tprice", "price", "trade price"],
    "commission": ["comm/fee", "commission", "ibcommission", "fees", "comm"],
    "time": ["date/time", "datetime", "date", "trade date/time", "tradedate"],
}


def _match_col(headers, aliases):
    low = {h.lower().strip(): h for h in headers}
    for a in aliases:
        if a in low:
            return low[a]
    for h in headers:
        if any(a in h.lower().strip() for a in aliases):
            return h
    return None


def ingest_csv(text_data: str) -> dict:
    reader = csv.reader(io.StringIO(text_data))
    rows = [r for r in reader if r]
    if not rows:
        return {"added": 0, "error": "empty file"}

    header_idx = 0
    for i, r in enumerate(rows):
        if any(str(cell).lower().strip() in ("symbol", "ticker") for cell in r):
            header_idx = i
            break
    headers = [h.strip() for h in rows[header_idx]]
    cols = {k: _match_col(headers, v) for k, v in _COL_ALIASES.items()}
    if not cols["symbol"] or not cols["shares"] or not cols["price"]:
        return {"added": 0, "error": "could not find symbol/quantity/price columns", "headers": headers}

    idx = {h: i for i, h in enumerate(headers)}
    fills = []
    for r in rows[header_idx + 1:]:
        if len(r) < len(headers):
            continue
        try:
            sym = r[idx[cols["symbol"]]].strip()
            if not sym or sym.lower() in ("symbol", "total"):
                continue
            shares = float(str(r[idx[cols["shares"]]]).replace(",", ""))
            price = float(str(r[idx[cols["price"]]]).replace(",", ""))
            side = r[idx[cols["side"]]].strip() if cols["side"] else ""
            comm = 0.0
            if cols["commission"]:
                try:
                    comm = abs(float(str(r[idx[cols["commission"]]]).replace(",", "")))
                except ValueError:
                    comm = 0.0
            ts = r[idx[cols["time"]]].strip() if cols["time"] else ""
            fills.append({"symbol": sym, "side": side, "shares": shares, "price": price,
                          "commission": comm, "time": ts, "source": "csv"})
        except Exception as e:
            logger.debug("row skip: %s", e)
    added = add_fills(fills)
    return {"added": added, "parsed": len(fills)}


# -- round-trip matching (FIFO) -----------------------------------------
def _all_fills():
    return db.q("SELECT * FROM fills ORDER BY ts ASC")


def closed_trades() -> list[dict]:
    fills = _all_fills()
    open_lots: dict[str, deque] = defaultdict(deque)
    trades = []
    for f in fills:
        sym, side, shares, price, comm = f["symbol"], f["side"], f["shares"], f["price"], f["commission"]
        lots = open_lots[sym]
        net = sum(l["dir"] * l["shares"] for l in lots)
        adding = (net >= 0 and side == "BUY") or (net <= 0 and side == "SELL")
        if adding or not lots:
            open_lots[sym].append({"dir": 1 if side == "BUY" else -1, "shares": shares,
                                   "price": price, "comm": comm, "ts": f["ts"]})
            continue
        remaining = shares
        close_dir = 1 if side == "BUY" else -1
        while remaining > 1e-9 and lots:
            lot = lots[0]
            matched = min(remaining, lot["shares"])
            entry_dir, entry_price = lot["dir"], lot["price"]
            pnl = (price - entry_price) * matched * entry_dir
            entry_comm = lot["comm"] * (matched / lot["shares"]) if lot["shares"] else 0
            exit_comm = comm * (matched / shares) if shares else 0
            trades.append({
                "symbol": sym,
                "direction": "LONG" if entry_dir == 1 else "SHORT",
                "shares": round(matched, 4),
                "entryPrice": round(entry_price, 4),
                "exitPrice": round(price, 4),
                "entryTime": lot["ts"], "exitTime": f["ts"],
                "pnl": round(pnl - entry_comm - exit_comm, 2),
                "pnlPct": round((price / entry_price - 1) * 100 * entry_dir, 2) if entry_price else None,
                "commission": round(entry_comm + exit_comm, 2),
            })
            lot["shares"] -= matched
            remaining -= matched
            if lot["shares"] <= 1e-9:
                lots.popleft()
        if remaining > 1e-9:
            open_lots[sym].append({"dir": close_dir, "shares": remaining, "price": price,
                                   "comm": comm * (remaining / shares) if shares else 0, "ts": f["ts"]})
    for i, t in enumerate(trades):
        t["key"] = f"{t['symbol']}-{t['entryTime']}-{t['exitTime']}-{i}"
    return trades


def open_positions_from_fills() -> list[dict]:
    fills = _all_fills()
    open_lots: dict[str, deque] = defaultdict(deque)
    for f in fills:
        sym, side, shares = f["symbol"], f["side"], f["shares"]
        lots = open_lots[sym]
        net = sum(l["dir"] * l["shares"] for l in lots)
        adding = (net >= 0 and side == "BUY") or (net <= 0 and side == "SELL")
        if adding or not lots:
            open_lots[sym].append({"dir": 1 if side == "BUY" else -1, "shares": shares, "price": f["price"]})
        else:
            remaining = shares
            while remaining > 1e-9 and lots:
                lot = lots[0]
                m = min(remaining, lot["shares"])
                lot["shares"] -= m
                remaining -= m
                if lot["shares"] <= 1e-9:
                    lots.popleft()
    out = []
    for sym, lots in open_lots.items():
        net = sum(l["dir"] * l["shares"] for l in lots)
        if abs(net) > 1e-9:
            cost = sum(l["price"] * l["shares"] for l in lots) / sum(l["shares"] for l in lots)
            out.append({"symbol": sym, "shares": round(net, 4), "avgPrice": round(cost, 4)})
    return out


# -- stats --------------------------------------------------------------
def stats() -> dict:
    trades = closed_trades()
    notes = _notes_map()
    for t in trades:
        n = notes.get(t["key"], {})
        t.update({"strategy": n.get("strategy"), "setup": n.get("setup"),
                  "note": n.get("note"), "rating": n.get("rating")})
    if not trades:
        return {"summary": _empty_summary(), "trades": [], "bySymbol": [],
                "equityCurve": [], "openPositions": open_positions_from_fills()}

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    total = sum(t["pnl"] for t in trades)
    avg_win = gross_win / len(wins) if wins else 0
    avg_loss = gross_loss / len(losses) if losses else 0
    summary = {
        "totalTrades": len(trades), "wins": len(wins), "losses": len(losses),
        "winRate": round(len(wins) / len(trades) * 100, 1),
        "netPnL": round(total, 2), "grossProfit": round(gross_win, 2), "grossLoss": round(gross_loss, 2),
        "profitFactor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "avgWin": round(avg_win, 2), "avgLoss": round(avg_loss, 2),
        "avgRR": round(avg_win / avg_loss, 2) if avg_loss else None,
        "expectancy": round(total / len(trades), 2),
        "bestTrade": round(max(t["pnl"] for t in trades), 2),
        "worstTrade": round(min(t["pnl"] for t in trades), 2),
    }
    by_symbol = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})
    for t in trades:
        s = by_symbol[t["symbol"]]
        s["trades"] += 1
        s["pnl"] += t["pnl"]
        s["wins"] += 1 if t["pnl"] > 0 else 0
    by_symbol_list = [{"symbol": k, "trades": v["trades"], "pnl": round(v["pnl"], 2),
                       "winRate": round(v["wins"] / v["trades"] * 100, 1)} for k, v in by_symbol.items()]
    by_symbol_list.sort(key=lambda x: x["pnl"], reverse=True)
    equity, running = [], 0.0
    for t in sorted(trades, key=lambda x: x["exitTime"]):
        running += t["pnl"]
        equity.append(round(running, 2))
    return {"summary": summary, "trades": list(reversed(trades)), "bySymbol": by_symbol_list,
            "equityCurve": equity, "openPositions": open_positions_from_fills()}


def set_note(trade_key, strategy="", setup="", note="", rating=None):
    db.execute(
        "INSERT INTO trade_notes (trade_key, strategy, setup, note, rating) "
        "VALUES (:k,:st,:se,:n,:r) ON CONFLICT (trade_key) DO UPDATE SET "
        "strategy=:st, setup=:se, note=:n, rating=:r",
        k=trade_key, st=strategy, se=setup, n=note, r=rating)


def _notes_map():
    return {r["trade_key"]: r for r in db.q("SELECT * FROM trade_notes")}


def _empty_summary():
    return {"totalTrades": 0, "wins": 0, "losses": 0, "winRate": 0, "netPnL": 0,
            "grossProfit": 0, "grossLoss": 0, "profitFactor": None, "avgWin": 0,
            "avgLoss": 0, "avgRR": None, "expectancy": 0, "bestTrade": 0, "worstTrade": 0}
