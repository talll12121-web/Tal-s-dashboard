"""
IBKR live provider
===================

Runs ib_async inside its own asyncio event loop on a background thread, so the
synchronous Flask app can read live data from in-memory caches without blocking.

Responsibilities:
  * connect / auto-reconnect to IB Gateway or TWS
  * live snapshot quotes for the active watchlist
  * 1-min + daily bars  -> VWAP, SMA20, sparkline  (intraday signal)
  * account positions    (live P&L)
  * executions / fills    (feeds the trading journal)

If the gateway isn't running, every method degrades to "not connected" and the
rest of the app falls back to yfinance. Nothing crashes.

Adapted from the user's existing ibkr_screener_server.py + ibkr_live.py.
"""

from __future__ import annotations
import asyncio
import threading
import logging
import math
import time
from datetime import datetime

from .. import config
from ..core import store

logger = logging.getLogger(__name__)


def _safe(v, digits=2):
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, digits) if digits > 0 else int(f)
    except Exception:
        return None


def _vwap(bars):
    if not bars:
        return None
    vol = sum(b.volume for b in bars)
    if not vol:
        return None
    return sum((b.high + b.low + b.close) / 3 * b.volume for b in bars) / vol


def _sma(bars, period=20):
    if not bars:
        return None
    use = bars[-period:]
    return sum(b.close for b in use) / len(use)


class IBKRProvider:
    def __init__(self):
        self._ib = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self.connected = False
        # True = we want to be connected (auto-reconnect). Toggled by the
        # Settings "Connect"/"Disconnect" buttons or IB_ENABLED at boot.
        self._want_connect = config.IB_ENABLED
        self.symbols: list[str] = []

        # caches (read by Flask thread)
        self.quotes: dict[str, dict] = {}
        self.indicators: dict[str, dict] = {}
        self._tickers: dict[str, object] = {}
        self._contracts: dict[str, object] = {}

    # -- public sync API (called from Flask thread) --------------------
    def is_connected(self) -> bool:
        return self.connected

    def get_quote(self, symbol: str) -> dict | None:
        return self.quotes.get(symbol.upper())

    def get_indicators(self, symbol: str) -> dict:
        return self.indicators.get(symbol.upper(), {})

    def set_watchlist(self, symbols: list[str]):
        """Track this set of symbols for live snapshots + indicators."""
        self.symbols = [s.upper() for s in symbols]

    def start(self):
        if not (config.IB_ENABLED or self._want_connect):
            logger.info("IBKR disabled via config (IB_ENABLED=false).")
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def connect(self, timeout: float = 7.0) -> bool:
        """On-demand connect - used by the Settings 'Connect' button when the app
        runs on a PC with IB Gateway/TWS. On the cloud instance this just fails
        gracefully (nothing to reach), so the UI tells the user to run the bridge."""
        self._want_connect = True
        config.IB_ENABLED = True
        if not (self._thread and self._thread.is_alive()):
            self.start()
        elif self._loop is not None and self._ib is not None and not self.connected:
            try:
                asyncio.run_coroutine_threadsafe(self._connect(), self._loop).result(timeout=timeout)
            except Exception as e:
                logger.info("on-demand connect failed: %s", e)
        deadline = time.time() + timeout
        while time.time() < deadline and not self.connected:
            time.sleep(0.2)
        return self.connected

    def disconnect(self):
        """Stop auto-reconnect and drop the IBKR socket."""
        self._want_connect = False
        config.IB_ENABLED = False
        self.connected = False
        if self._loop is not None and self._ib is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._do_disconnect(), self._loop)
            except Exception:
                pass

    async def _do_disconnect(self):
        try:
            if self._ib and self._ib.isConnected():
                self._ib.disconnect()
        except Exception:
            pass
        self.connected = False

    def positions(self) -> list[dict]:
        """Live account positions with unrealised P&L (sync wrapper)."""
        if not self.connected:
            return []
        try:
            fut = asyncio.run_coroutine_threadsafe(self._positions(), self._loop)
            return fut.result(timeout=10)
        except Exception as e:
            logger.warning("positions() failed: %s", e)
            return []

    def executions(self, days: int = 7) -> list[dict]:
        """Recent fills/executions for the trading journal (sync wrapper)."""
        if not self.connected:
            return []
        try:
            fut = asyncio.run_coroutine_threadsafe(self._executions(days), self._loop)
            return fut.result(timeout=15)
        except Exception as e:
            logger.warning("executions() failed: %s", e)
            return []

    # -- background event loop -----------------------------------------
    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except Exception as e:
            logger.error("IBKR loop crashed: %s", e)

    async def _main(self):
        try:
            from ib_async import IB
        except ImportError:
            logger.error("ib_async not installed - IBKR features disabled. pip install ib_async")
            return

        self._ib = IB()
        await self._connect()

        # background tasks
        asyncio.create_task(self._keep_alive())
        asyncio.create_task(self._refresh_quotes_loop())
        asyncio.create_task(self._refresh_indicators_loop())
        asyncio.create_task(self._push_loop())

        while True:
            await asyncio.sleep(1)

    async def _push_loop(self):
        """Mirror live caches into the shared store so the web layer (and any
        other worker) reads consistent data, exactly like the cloud bridge does."""
        while True:
            await asyncio.sleep(5)
            if not self.connected:
                continue
            try:
                positions = await self._positions()
                store.set_live(quotes=self.quotes, indicators=self.indicators, positions=positions)
            except Exception as e:
                logger.debug("push_loop: %s", e)

    async def _connect(self) -> bool:
        try:
            if not self._ib.isConnected():
                await self._ib.connectAsync(
                    config.IB_HOST, config.IB_PORT, clientId=config.IB_CLIENT_ID
                )
                self.connected = True
                logger.info("Connected to IBKR %s:%s", config.IB_HOST, config.IB_PORT)
        except Exception as e:
            self.connected = False
            logger.info("IBKR not connected (%s). Falling back to yfinance.", e)
        return self.connected

    async def _keep_alive(self):
        while True:
            await asyncio.sleep(30)
            try:
                if self._want_connect and not self._ib.isConnected():
                    self.connected = False
                    await self._connect()
                elif not self._want_connect and self._ib.isConnected():
                    self._ib.disconnect()
                    self.connected = False
            except Exception:
                self.connected = False

    async def _contract(self, symbol):
        from ib_async import Stock
        if symbol in self._contracts:
            return self._contracts[symbol]
        c = Stock(symbol, "SMART", "USD")
        await self._ib.qualifyContractsAsync(c)
        self._contracts[symbol] = c
        return c

    def _market_open(self) -> bool:
        import pytz
        et = pytz.timezone("America/New_York")
        now = datetime.now(et)
        if now.weekday() >= 5:
            return False
        mins = now.hour * 60 + now.minute
        return 570 <= mins < 960

    async def _refresh_quotes_loop(self):
        while True:
            if not self.connected or not self.symbols:
                await asyncio.sleep(5)
                continue
            try:
                self._ib.reqMarketDataType(1 if self._market_open() else 2)
                for sym in list(self.symbols):
                    try:
                        c = await self._contract(sym)
                        tickers = await self._ib.reqTickersAsync(c)
                        if tickers:
                            self._tickers[sym] = tickers[0]
                            self._build_quote(sym)
                        await asyncio.sleep(0.2)
                    except Exception as e:
                        logger.debug("quote %s: %s", sym, e)
            except Exception as e:
                logger.debug("quote loop: %s", e)
            await asyncio.sleep(5 if self._market_open() else 60)

    def _build_quote(self, sym):
        t = self._tickers.get(sym)
        if not t:
            return
        price = _safe(t.last)
        if price is None:
            bid, ask = _safe(t.bid), _safe(t.ask)
            if bid and ask:
                price = round((bid + ask) / 2, 2)
        prev = _safe(t.close)
        ind = self.indicators.get(sym, {})
        self.quotes[sym] = {
            "symbol": sym,
            "price": price,
            "prevClose": prev,
            "change": round(price - prev, 2) if price and prev else None,
            "changePct": round((price - prev) / prev * 100, 2) if price and prev else None,
            "dayHigh": _safe(t.high) or ind.get("dayHigh"),
            "dayLow": _safe(t.low) or ind.get("dayLow"),
            "volume": _safe(t.volume, 0),
        }

    async def _refresh_indicators_loop(self):
        await asyncio.sleep(8)
        while True:
            if not self.connected or not self.symbols:
                await asyncio.sleep(10)
                continue
            for sym in list(self.symbols):
                try:
                    await self._refresh_indicators(sym)
                    await asyncio.sleep(2)
                except Exception as e:
                    logger.debug("ind %s: %s", sym, e)
            await asyncio.sleep(300 if self._market_open() else 3600)

    async def _refresh_indicators(self, sym):
        c = await self._contract(sym)
        bars_1m, bars_d = [], []
        if self._market_open():
            bars_1m = await self._ib.reqHistoricalDataAsync(
                c, endDateTime="", durationStr="1 D", barSizeSetting="1 min",
                whatToShow="TRADES", useRTH=True, formatDate=1, timeout=15) or []
            await asyncio.sleep(0.3)
        bars_d = await self._ib.reqHistoricalDataAsync(
            c, endDateTime="", durationStr="1 M", barSizeSetting="1 day",
            whatToShow="TRADES", useRTH=True, formatDate=1, timeout=15) or []

        vwap = _vwap(bars_1m) if bars_1m else None
        sma20 = _sma(bars_d, 20) if bars_d else None
        spark = [round(b.close, 2) for b in bars_d[-20:]] if bars_d else []
        self.indicators[sym] = {
            "vwap": round(vwap, 2) if vwap else None,
            "sma20": round(sma20, 2) if sma20 else None,
            "sparkline": spark,
            "dayHigh": round(max((b.high for b in bars_1m), default=0), 2) or None,
            "dayLow": round(min((b.low for b in bars_1m), default=0), 2) or None,
        }

    async def _positions(self):
        out = []
        for p in self._ib.positions():
            sym = p.contract.symbol
            q = self.quotes.get(sym, {})
            price = q.get("price")
            unreal = round((price - p.avgCost) * p.position, 2) if price and p.position else None
            pct = round((price - p.avgCost) / p.avgCost * 100, 2) if price and p.avgCost else None
            out.append({
                "symbol": sym,
                "position": p.position,
                "avgCost": round(p.avgCost, 2),
                "currentPrice": price,
                "unrealizedPnL": unreal,
                "unrealizedPct": pct,
                "account": p.account,
            })
        return out

    async def _executions(self, days):
        from ib_async import ExecutionFilter
        fills = await self._ib.reqExecutionsAsync(ExecutionFilter())
        out = []
        for f in fills:
            ex, c = f.execution, f.contract
            comm = getattr(getattr(f, "commissionReport", None), "commission", None)
            out.append({
                "execId": ex.execId,
                "symbol": c.symbol,
                "secType": c.secType,
                "side": ex.side,                 # BOT / SLD
                "shares": float(ex.shares),
                "price": float(ex.price),
                "time": str(ex.time),
                "commission": _safe(comm),
                "account": ex.acctNumber,
            })
        return out
