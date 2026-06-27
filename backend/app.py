"""
Trading Dashboard - Flask app
=============================

One backend, four views: Intraday / Swing / Long-term / Journal.
Runs in two shapes from the SAME code:

  * Local all-in-one  - runs on your desktop, connects to IBKR directly.
  * Cloud + bridge    - runs on a server (Render etc.); a small desktop agent
                        (see bridge/) pushes live IBKR data up. Phone-friendly.

Storage is SQLite locally / Postgres in the cloud (core/db.py). Live IBKR data
flows through a shared live-state store (core/store.py) so every worker - and
your phone - sees the same thing.

Run locally:  python -m backend.app
Run in cloud: gunicorn backend.wsgi:app   (see Procfile)
"""

from __future__ import annotations
import logging

from flask import Flask, jsonify, request, send_from_directory, session, redirect

from . import config
from .core import db, store, auth
from .providers import market_data, news
from .providers.ibkr import IBKRProvider
from .scanners import intraday, swing, sector, fundamental, ideas
from .journal import journal

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("dashboard")

app = Flask(__name__, static_folder=None)
app.secret_key = auth.SECRET_KEY

# -- init persistence ---------------------------------------------------
db.init_db()

# -- IBKR: only connect directly when explicitly enabled (local mode).
#    In the cloud you leave IB_ENABLED=false and the desktop bridge pushes data.
IBKR = IBKRProvider()
if config.IB_ENABLED:
    IBKR.set_watchlist(store.all_symbols())
    IBKR.start()


# -- auth routes ---------------------------------------------------------
LOGIN_HTML = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Sign in</title><link rel=stylesheet href=/style.css></head>
<body><div class=login-wrap><form class=login-card method=post action=/login>
<div class=login-mark>&#9650;</div><h1>Trading Terminal</h1>
<p class=muted>Enter your password to continue</p>
<input type=password name=password placeholder=Password autofocus>
<button type=submit>Sign in</button>{err}</form></div></body></html>"""


@app.route("/login", methods=["GET", "POST"])
def login():
    if not auth.LOGIN_REQUIRED:
        return redirect("/")
    if request.method == "POST":
        if auth.check_password(request.form.get("password", "")):
            session["auth"] = True
            return redirect("/")
        return LOGIN_HTML.format(err='<div class="login-err">Wrong password</div>'), 401
    return LOGIN_HTML.format(err="")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# -- frontend -------------------------------------------------------------
@app.route("/")
@auth.require_login
def index():
    return send_from_directory(config.FRONTEND_DIR, "index.html")


@app.route("/<path:path>")
def static_files(path):
    # static assets (css/js) are public so the login page can style itself
    return send_from_directory(config.FRONTEND_DIR, path)


# -- status -------------------------------------------------------------
@app.route("/api/status")
@auth.require_login
def status():
    return jsonify({
        "ibkrConnected": store.live_connected(),
        "ibkrPort": config.IB_PORT,
        "watchlists": store.get_watchlists(),
        "loginRequired": auth.LOGIN_REQUIRED,
    })


# -- watchlists ---------------------------------------------------------
@app.route("/api/watchlist/<mode>")
@auth.require_login
def get_watchlist(mode):
    return jsonify(store.get_watchlist(mode))


@app.route("/api/watchlist/<mode>/add", methods=["POST"])
@auth.require_login
def add_symbol(mode):
    sym = (request.json or {}).get("symbol", "").upper().strip()
    if not sym:
        return jsonify({"error": "no symbol"}), 400
    out = store.add_symbol(mode, sym)
    if config.IB_ENABLED:
        IBKR.set_watchlist(store.all_symbols())
    return jsonify(out)


@app.route("/api/watchlist/<mode>/remove", methods=["POST"])
@auth.require_login
def remove_symbol(mode):
    sym = (request.json or {}).get("symbol", "").upper().strip()
    out = store.remove_symbol(mode, sym)
    if config.IB_ENABLED:
        IBKR.set_watchlist(store.all_symbols())
    return jsonify(out)


# -- scanners -----------------------------------------------------------
@app.route("/api/intraday")
@auth.require_login
def api_intraday():
    return jsonify(intraday.scan(store.get_watchlist("intraday")))


@app.route("/api/swing")
@auth.require_login
def api_swing():
    return jsonify(swing.scan(store.get_watchlist("swing")))


@app.route("/api/sector")
@auth.require_login
def api_sector():
    return jsonify(sector.scan())


@app.route("/api/sector/history")
@auth.require_login
def api_sector_history():
    weeks = request.args.get("weeks", default=12, type=int)
    return jsonify(sector.scan_historical(weeks_back=max(4, min(26, weeks))))


@app.route("/api/ideas")
@auth.require_login
def api_ideas():
    n = request.args.get("sectors", default=5, type=int)
    return jsonify(ideas.scan(top_sectors=max(2, min(8, n))))


@app.route("/api/fundamental")
@auth.require_login
def api_fundamental():
    return jsonify(fundamental.scan(store.get_watchlist("longterm")))


@app.route("/api/candles/<symbol>")
@auth.require_login
def api_candles(symbol):
    tf = request.args.get("tf", "D")
    return jsonify(market_data.get_candles(symbol, tf))


# -- news ---------------------------------------------------------------
@app.route("/api/news")
@auth.require_login
def api_news():
    mode = request.args.get("mode", "intraday")
    return jsonify(news.fetch_many(store.get_watchlist(mode)))


@app.route("/api/news/<symbol>")
@auth.require_login
def api_news_symbol(symbol):
    return jsonify(news.fetch_symbol(symbol))


# -- positions (live, from the shared store) -----------------------------
@app.route("/api/positions")
@auth.require_login
def api_positions():
    return jsonify(store.live_positions())


# -- journal ------------------------------------------------------------
@app.route("/api/journal")
@auth.require_login
def api_journal():
    return jsonify(journal.stats())


@app.route("/api/journal/sync-ibkr", methods=["POST"])
@auth.require_login
def api_journal_sync():
    """Local mode only: pull executions directly. (In cloud, the bridge pushes.)"""
    if not config.IB_ENABLED or not IBKR.is_connected():
        return jsonify({"error": "IBKR not connected here - use the desktop bridge"}), 400
    fills = IBKR.executions(days=30)
    return jsonify({"added": journal.add_fills(fills), "fetched": len(fills)})


@app.route("/api/journal/import-csv", methods=["POST"])
@auth.require_login
def api_journal_import():
    text = ""
    if request.files:
        f = next(iter(request.files.values()))
        text = f.read().decode("utf-8", errors="ignore")
    elif request.json and "csv" in request.json:
        text = request.json["csv"]
    else:
        text = request.get_data(as_text=True)
    if not text.strip():
        return jsonify({"error": "no CSV content"}), 400
    return jsonify(journal.ingest_csv(text))


@app.route("/api/journal/note", methods=["POST"])
@auth.require_login
def api_journal_note():
    d = request.json or {}
    journal.set_note(d.get("key", ""), d.get("strategy", ""), d.get("setup", ""),
                     d.get("note", ""), d.get("rating"))
    return jsonify({"ok": True})


# -- bridge endpoints (desktop agent -> cloud) ----------------------------
@app.route("/api/bridge/live", methods=["POST"])
@auth.require_bridge_token
def bridge_live():
    d = request.json or {}
    store.set_live(quotes=d.get("quotes"), indicators=d.get("indicators"),
                   positions=d.get("positions"))
    return jsonify({"ok": True})


@app.route("/api/bridge/executions", methods=["POST"])
@auth.require_bridge_token
def bridge_executions():
    d = request.json or {}
    added = journal.add_fills(d.get("fills", []))
    return jsonify({"added": added})


if __name__ == "__main__":
    print("=" * 60)
    print("  Trading Dashboard  ->  http://localhost:%d" % config.WEB_PORT)
    print("  IBKR direct: %s (port %d) | login required: %s"
          % (config.IB_ENABLED, config.IB_PORT, auth.LOGIN_REQUIRED))
    print("=" * 60)
    app.run(host=config.WEB_HOST, port=config.WEB_PORT, debug=False, threaded=True)
