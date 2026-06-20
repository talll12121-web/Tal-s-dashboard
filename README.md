# Trading Dashboard - one-stop shop for stocks & options

A single backend and dashboard that unifies your earlier projects into four
trader views - with live IBKR data, free market data, a watchlist news feed, and
a trading journal built from your real IBKR fills.

It runs in **two shapes from the same code**:

- **Local all-in-one** - runs on your trading PC and talks to IB Gateway directly. Full live intraday.
- **Cloud + desktop bridge** - the dashboard lives on a server (so you can open it from your **phone or any device**); a tiny agent on your PC pushes live IBKR data up while you trade. This is the setup for "access it from anywhere."

It merges:
- **intraday-scanner** (the newer of your two identical repos - `ibkr-momentum-screener` is the old copy) -> the **Intraday** view
- **Sector-scanner** (`fundemental scanner`) -> the **Long-term** view (sector heat + fundamentals)
- **New** swing engine -> the **Swing** view
- **New** journal -> the **Journal** view

---

## The four views

| View | What it does | Engine |
|------|--------------|--------|
| **Intraday** | Live momentum board: price vs VWAP & SMA20, the signal from your original screener, plus a watchlist RSS news panel | IBKR live -> yfinance fallback |
| **Swing** | Multi-day setups scored 0-100 on trend / pullback-to-20SMA / breakout / RSI / relative strength vs SPY | yfinance daily bars |
| **Long-term** | Sector-rotation heat map across the 11 SPDR sector ETFs + a quality/growth/value fundamental ranking | yfinance |
| **Journal** | Your IBKR trades matched FIFO into closed round-trips: win rate, profit factor, expectancy, R:R, equity curve, by-symbol breakdown | IBKR executions + CSV import |

On your phone you get everything **except** IBKR-live intraday (delayed yfinance
data fills in) - which is exactly the tradeoff you chose, since day-trading
happens at the desk.

---

## How your data is kept ("memory")

Two things are durable and identical on every device; everything else is
recomputed live.

- **Watchlists** and **journal** live in a **database**: SQLite locally
  (`data/dashboard.db`), Postgres in the cloud. Same code, chosen by the
  `DATABASE_URL` env var. In the cloud this means your trades and watchlists are
  the same on your laptop and phone and survive any disk failure.
- **Live market data** (quotes, VWAP, news, scores) is never "saved" - it's
  fetched fresh each time, because a stale price is useless.

---

## Option A - run it locally (simplest, full live data)

```
cd trading-dashboard
# Windows:        run.bat
# macOS / Linux:  ./run.sh
```

Opens at **http://localhost:5000** on free data with no keys and no login. To add
live IBKR: start IB Gateway/TWS, copy `.env.example`->`.env`, set `IB_ENABLED=true`
and the right `IB_PORT` (Gateway live 4001 / paper 4002), restart.

---

## Option B - deploy to the cloud + phone access (Render)

You get a private URL (e.g. `https://trading-dashboard.onrender.com`) you can
open from anywhere, behind a password.

### 1. Push this folder to GitHub
It can be a private repo. (`.gitignore` already keeps secrets and your DB out.)

### 2. Create the app on Render
- Render -> **New -> Blueprint** -> pick your repo. The included `render.yaml`
  provisions the web service **and a free Postgres database** automatically.
- When prompted (or under the service's **Environment** tab), set:
  - `APP_PASSWORD` - the password you'll type to log in.
  - `BRIDGE_TOKEN` - any long random string (you'll reuse it on your PC).
  - (`SECRET_KEY` and `DATABASE_URL` are filled in automatically.)
- Deploy. First boot creates the tables and seeds your default watchlists.

Visit the URL -> log in with `APP_PASSWORD`. Swing, Long-term, news and journal
work immediately (free data). Intraday shows delayed data until the bridge runs.

### 3. Run the desktop bridge when you trade
On your trading PC, with IB Gateway/TWS open:
```
copy bridge\.env.example  bridge\.env     (then edit it)
#   DASHBOARD_URL=https://your-app.onrender.com
#   BRIDGE_TOKEN=<same value you set on Render>
#   IB_PORT=4001   (live) or 4002 (paper)

# Windows:  double-click  bridge\run_bridge.bat
# or:       python -m bridge.ibkr_bridge
```
The bridge pushes live quotes/VWAP/positions every few seconds and your
executions every minute. The dashboard's status pill turns green. Close the
bridge window when you're done - the cloud app keeps working on delayed data.

> Render's free web service sleeps after inactivity and the free Postgres has a
> size cap - both fine for personal use. Upgrade to the ~$7/mo tier for
> always-on.

---

## Using the journal

- **Sync IBKR trades** (local mode) or the **bridge** (cloud) pull executions automatically.
- **Import CSV** -> an IBKR *Flex Query* / *Activity Statement* export, or any CSV
  with symbol / date-time / quantity / price / side columns (auto-detected).

Fills are deduped by execution id (safe to re-import) and matched FIFO per symbol
into closed trades.

---

## The data mix (free sources, you asked me to pick)

- **yfinance (Yahoo)** - the backbone. Free, no key, deep history + fundamentals. Powers Swing, Long-term, and the Intraday fallback.
- **IBKR** - your live edge: exchange-accurate quotes, 1-min VWAP, and the only source for your positions/executions. Overrides yfinance when the bridge (or local connection) is live.
- **RSS news** - Yahoo Finance RSS (primary) + Google News RSS (fallback), auto-tagged deal / bullish / bearish / neutral.
- **Finnhub / sec-api** - optional, off by default; add a free key in `.env` to enable.

---

## Architecture

```
trading-dashboard/
- backend/
|  - app.py            Flask app: routes, auth gate, bridge endpoints
|  - wsgi.py           gunicorn entry (cloud)
|  - config.py         env + defaults
|  - core/
|  |  - db.py          SQLAlchemy engine (SQLite local / Postgres cloud)
|  |  - store.py       watchlists + live-state (kv) accessors
|  |  - auth.py        password login + bridge-token guard
|  - providers/        market_data (mix) . ibkr . news (RSS)
|  - scanners/         intraday . swing . sector . fundamental . indicators
|  - journal/journal.py  FIFO matching + stats + CSV import
- bridge/
|  - ibkr_bridge.py    desktop agent -> pushes live IBKR data to the cloud
- frontend/            responsive light-SaaS UI + login page
- render.yaml . Procfile . runtime.txt   (cloud deploy)
- requirements.txt . .env.example . run.bat . run.sh
```

Data flow: the desktop bridge (or a local IBKR connection) writes live quotes
into a shared **live-state store** in the DB; every web worker - and your phone -
reads from it. So nothing depends on which process or device you're on.

---

## Security notes
- The whole app is behind `APP_PASSWORD` once deployed. Render gives you HTTPS automatically.
- The bridge authenticates with `BRIDGE_TOKEN`; without it, push endpoints reject everything.
- Your IBKR credentials never leave your PC - only derived quotes/positions/fills are pushed up.
- Keep the repo private if you like; `.env`, `bridge/.env` and the DB are gitignored.

---

## Verified
- All modules compile; both modes boot.
- **Cloud mode** end-to-end test passed: login gate (401/302), DB-backed watchlist add/remove, bridge-token enforcement (403/200), live push surfacing on the Intraday board with `IBKR` source + signal, positions endpoint, and bridge executions landing in the journal (AMD +998).
- Journal FIFO/P&L, CSV ingest, indicators, and fundamental scoring tested with sample data.
- Graceful degradation: if a data source is unreachable the app returns empty states instead of crashing.

---

## Ideas for next iterations
1. **Options layer** - option chains, IV rank, expected move, covered-call / spread screener (your IBKR link already supports it).
2. **Alerts** - push a phone/Telegram alert when an intraday signal fires or a swing score crosses a threshold.
3. **Backtest the swing score** - log daily scores, measure forward returns, tune the weights to your edge.
4. **Per-symbol charts** - embed TradingView lightweight-charts (port your Multi-Day Rolling VWAP Pine script).
5. **Position-aware sizing** - combine ATR% + account size to suggest share counts and stops.
6. **Journal tagging analytics** - win-rate-by-setup and time-of-day analysis (the notes schema already supports tags).
7. **Earnings calendar** overlay on the watchlist.
8. **Mobile PWA** - "Add to Home Screen" so it feels like a real phone app.

> Not financial advice - a personal research tool. Signals and scores are
> heuristics, not recommendations.
