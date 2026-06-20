"""
High-level data store built on db.py.

  * watchlists  - read/add/remove, persisted in the DB (multi-device safe)
  * live state  - quotes / indicators / positions pushed by the desktop bridge
                  (or written directly by a local IBKR connection), stored in the
                  kv table so every web worker sees the same thing.
"""

from __future__ import annotations
import json
import time
from datetime import datetime, timezone

from . import db

# How recent a live-state push must be to count as "connected" (seconds)
LIVE_FRESH_SECS = 45


# -- watchlists ---------------------------------------------------------
def get_watchlists() -> dict:
    rows = db.q("SELECT mode, symbol, ord FROM watchlist_items ORDER BY mode, ord, symbol")
    out: dict[str, list] = {}
    for r in rows:
        out.setdefault(r["mode"], []).append(r["symbol"])
    return out


def get_watchlist(mode: str) -> list:
    rows = db.q("SELECT symbol FROM watchlist_items WHERE mode = :m ORDER BY ord, symbol", m=mode)
    return [r["symbol"] for r in rows]


def add_symbol(mode: str, symbol: str) -> list:
    symbol = symbol.upper().strip()
    if symbol:
        # next ordinal
        rows = db.q("SELECT COALESCE(MAX(ord), -1) AS mx FROM watchlist_items WHERE mode = :m", m=mode)
        nxt = (rows[0]["mx"] if rows else -1) + 1
        db.execute(
            "INSERT INTO watchlist_items (mode, symbol, ord) VALUES (:m, :s, :o) "
            "ON CONFLICT (mode, symbol) DO NOTHING", m=mode, s=symbol, o=nxt)
    return get_watchlist(mode)


def remove_symbol(mode: str, symbol: str) -> list:
    db.execute("DELETE FROM watchlist_items WHERE mode = :m AND symbol = :s",
               m=mode, s=symbol.upper().strip())
    return get_watchlist(mode)


def all_symbols() -> list:
    seen = []
    for syms in get_watchlists().values():
        for s in syms:
            if s not in seen:
                seen.append(s)
    return seen


# -- key/value (live state) ---------------------------------------------
def kv_set(key: str, value):
    db.execute(
        "INSERT INTO kv (k, v, updated_at) VALUES (:k, :v, :t) "
        "ON CONFLICT (k) DO UPDATE SET v = :v, updated_at = :t",
        k=key, v=json.dumps(value), t=datetime.now(timezone.utc).isoformat())


def kv_get(key: str):
    rows = db.q("SELECT v, updated_at FROM kv WHERE k = :k", k=key)
    if not rows:
        return None, None
    try:
        return json.loads(rows[0]["v"]), rows[0]["updated_at"]
    except Exception:
        return None, rows[0]["updated_at"]


def _age_secs(iso_ts: str | None) -> float:
    if not iso_ts:
        return 1e9
    try:
        dt = datetime.fromisoformat(iso_ts)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except Exception:
        return 1e9


# -- live data accessors (read by market_data / intraday / status) ------
def set_live(quotes: dict | None = None, indicators: dict | None = None,
             positions: list | None = None):
    """Write a live snapshot. Called by the local IBKR provider or the bridge."""
    if quotes is not None:
        kv_set("live_quotes", quotes)
    if indicators is not None:
        kv_set("live_indicators", indicators)
    if positions is not None:
        kv_set("live_positions", positions)
    kv_set("live_heartbeat", time.time())


def live_connected() -> bool:
    _, ts = kv_get("live_heartbeat")
    return _age_secs(ts) < LIVE_FRESH_SECS


def live_quote(symbol: str) -> dict | None:
    quotes, ts = kv_get("live_quotes")
    if quotes and _age_secs(ts) < LIVE_FRESH_SECS:
        return quotes.get(symbol.upper())
    return None


def live_indicators(symbol: str) -> dict:
    inds, ts = kv_get("live_indicators")
    if inds and _age_secs(ts) < LIVE_FRESH_SECS:
        return inds.get(symbol.upper(), {})
    return {}


def live_positions() -> list:
    pos, ts = kv_get("live_positions")
    if pos and _age_secs(ts) < LIVE_FRESH_SECS:
        return pos
    return []
