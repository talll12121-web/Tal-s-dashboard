"""
Database layer  (SQLite locally, Postgres in the cloud - same code)
==================================================================

The whole app's durable "memory" lives here:
  * watchlist_items  - your per-mode watchlists (so they're identical on every device)
  * fills            - raw IBKR executions (feeds the journal)
  * trade_notes      - your notes / strategy tags per trade
  * kv               - small key/value store for live state pushed by the desktop bridge

Which database is used is decided by the DATABASE_URL env var:
  * unset                     -> SQLite file at data/dashboard.db   (local default)
  * postgres://... / postgresql://...  -> that Postgres (cloud)

All SQL here is written to run on BOTH engines (ANSI + ON CONFLICT, which
modern SQLite and Postgres both support), so nothing changes between local and
cloud except the connection string.
"""

from __future__ import annotations
import os
from sqlalchemy import create_engine, text

from .. import config


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        # local SQLite
        return f"sqlite:///{config.DATA_DIR / 'dashboard.db'}"
    # Render/Heroku hand out "postgres://"; SQLAlchemy needs "postgresql://"
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


DATABASE_URL = _database_url()
IS_SQLITE = DATABASE_URL.startswith("sqlite")

_engine_kwargs = {"pool_pre_ping": True}
if IS_SQLITE:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **_engine_kwargs)


SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS watchlist_items (
        mode    TEXT NOT NULL,
        symbol  TEXT NOT NULL,
        ord     INTEGER DEFAULT 0,
        PRIMARY KEY (mode, symbol)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fills (
        exec_id     TEXT PRIMARY KEY,
        symbol      TEXT,
        side        TEXT,
        shares      REAL,
        price       REAL,
        commission  REAL,
        ts          TEXT,
        source      TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trade_notes (
        trade_key   TEXT PRIMARY KEY,
        strategy    TEXT,
        setup       TEXT,
        note        TEXT,
        rating      INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS kv (
        k           TEXT PRIMARY KEY,
        v           TEXT,
        updated_at  TEXT
    )
    """,
]


def init_db():
    with engine.begin() as cx:
        for ddl in SCHEMA:
            cx.execute(text(ddl))
        # seed default watchlists on first run
        existing = cx.execute(text("SELECT COUNT(*) FROM watchlist_items")).scalar()
        if not existing:
            for mode, syms in config.DEFAULT_WATCHLISTS.items():
                for i, s in enumerate(syms):
                    cx.execute(
                        text("INSERT INTO watchlist_items (mode, symbol, ord) "
                             "VALUES (:m, :s, :o) ON CONFLICT (mode, symbol) DO NOTHING"),
                        {"m": mode, "s": s, "o": i},
                    )


def q(sql: str, **params):
    """Run a read query, return list of dict rows."""
    with engine.connect() as cx:
        rows = cx.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


def execute(sql: str, **params):
    """Run a write statement in its own transaction."""
    with engine.begin() as cx:
        cx.execute(text(sql), params)
