"""
Desktop IBKR Bridge
===================

Runs on your trading PC (where IB Gateway / TWS lives) and pushes live data up
to your cloud dashboard. This is the *only* piece that must run locally, because
IBKR only accepts API connections from the same machine.

What it does on a loop:
  * connects to IB Gateway / TWS via ib_async
  * tracks your combined watchlist (fetched from the cloud app)
  * pushes live quotes + VWAP/SMA indicators + positions  -> /api/bridge/live
  * pushes recent executions (fills)                       -> /api/bridge/executions

When this is NOT running (e.g. you're on your phone), the cloud dashboard simply
shows delayed yfinance data + your saved journal. Start it when you sit down to
trade; stop it when you're done.

Setup:
  1. pip install -r requirements.txt   (same deps; needs ib_async + requests)
  2. copy bridge/.env.example -> bridge/.env  and fill in:
        DASHBOARD_URL=https://your-app.onrender.com
        BRIDGE_TOKEN=<same value you set on the server>
        IB_PORT=4001        (live) or 4002 (paper)
  3. python -m bridge.ibkr_bridge       (or double-click bridge/run_bridge.bat)
"""

from __future__ import annotations
import os
import sys
import time
import logging
import requests
from pathlib import Path
from dotenv import load_dotenv

# load bridge/.env then project .env
load_dotenv(Path(__file__).resolve().parent / ".env")
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# reuse the exact same IBKR provider the app uses
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.providers.ibkr import IBKRProvider  # noqa: E402
from backend import config  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [bridge] %(message)s")
log = logging.getLogger("bridge")

DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://localhost:5000").rstrip("/")
BRIDGE_TOKEN = os.environ.get("BRIDGE_TOKEN", "")
PUSH_EVERY = int(os.environ.get("BRIDGE_PUSH_SECS", 5))
EXEC_EVERY = int(os.environ.get("BRIDGE_EXEC_SECS", 60))

HEADERS = {"X-Bridge-Token": BRIDGE_TOKEN, "Content-Type": "application/json"}


def _get_watchlist() -> list[str]:
    try:
        r = requests.get(f"{DASHBOARD_URL}/api/status", timeout=10, headers=HEADERS)
        if r.ok:
            wl = r.json().get("watchlists", {})
            seen = []
            for syms in wl.values():
                for s in syms:
                    if s not in seen:
                        seen.append(s)
            return seen
    except Exception as e:
        log.warning("could not fetch watchlist: %s", e)
    return []


def _post(path: str, payload: dict):
    try:
        r = requests.post(f"{DASHBOARD_URL}{path}", json=payload, headers=HEADERS, timeout=15)
        if not r.ok:
            log.warning("POST %s -> %s %s", path, r.status_code, r.text[:120])
        return r.ok
    except Exception as e:
        log.warning("POST %s failed: %s", path, e)
        return False


def main():
    if not BRIDGE_TOKEN:
        log.error("BRIDGE_TOKEN not set - copy bridge/.env.example to bridge/.env and set it.")
        sys.exit(1)

    log.info("Dashboard: %s | IBKR port: %s", DASHBOARD_URL, config.IB_PORT)
    config.IB_ENABLED = True
    ib = IBKRProvider()
    ib.start()

    last_exec = 0
    while True:
        time.sleep(PUSH_EVERY)
        # keep IBKR tracking the cloud watchlist
        wl = _get_watchlist()
        if wl:
            ib.set_watchlist(wl)

        if not ib.is_connected():
            log.info("waiting for IB Gateway/TWS connection...")
            continue

        # push live snapshot
        try:
            positions = ib.positions()
        except Exception:
            positions = []
        ok = _post("/api/bridge/live", {
            "quotes": ib.quotes,
            "indicators": ib.indicators,
            "positions": positions,
        })
        if ok:
            log.info("pushed %d quotes, %d positions", len(ib.quotes), len(positions))

        # push executions less often
        if time.time() - last_exec > EXEC_EVERY:
            fills = ib.executions(days=7)
            if fills:
                r = _post("/api/bridge/executions", {"fills": fills})
                if r:
                    log.info("pushed %d executions", len(fills))
            last_exec = time.time()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nbridge stopped.")
