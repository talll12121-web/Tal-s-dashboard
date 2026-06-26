"""
Central configuration for the Trading Dashboard.

All tunables live here. Secrets (API keys) are read from a .env file
(see .env.example) so they never get committed to git.
"""

import os
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# -- Paths --------------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BACKEND_DIR.parent
FRONTEND_DIR = PROJECT_DIR / "frontend"
DATA_DIR = PROJECT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

WATCHLIST_FILE = DATA_DIR / "watchlists.json"
JOURNAL_DB = DATA_DIR / "journal.db"

# -- Web server ---------------------------------------------------------
WEB_HOST = "0.0.0.0"
WEB_PORT = int(os.environ.get("WEB_PORT", 5000))

# -- Interactive Brokers ------------------------------------------------
# IB Gateway:  live = 4001, paper = 4002
# TWS:         live = 7496, paper = 7497
IB_HOST = os.environ.get("IB_HOST", "127.0.0.1")
IB_PORT = int(os.environ.get("IB_PORT", 4001))
IB_CLIENT_ID = int(os.environ.get("IB_CLIENT_ID", 11))
IB_ENABLED = os.environ.get("IB_ENABLED", "true").lower() == "true"

# -- Data sources -------------------------------------------------------
# Primary free source is yfinance (no key). Optional keys enrich/augment.
# Fundamentals fallback chain (used when Yahoo blocks .info on datacenter IPs):
#   yfinance  ->  FMP (preferred, full scorecard)  >  SEC EDGAR (no key, never
#   blocks)  >  Finnhub (extra gap-fill). Each is optional; set the keys you have.
FMP_API_KEY = os.environ.get("FMP_API_KEY", "")
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")
SEC_API_KEY = os.environ.get("SEC_API_KEY", "")
# SEC requires a descriptive User-Agent with contact info on every EDGAR request.
# Set this to "Your Name your@email" via the env var in production.
SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT", "Tal Dashboard fundamentals (contact: talbramli@gmail.com)")

# How long quote/fundamental responses are cached (seconds)
QUOTE_TTL = 15
FUNDAMENTAL_TTL = 60 * 60 * 6      # 6 hours - fundamentals barely change intraday
HISTORY_TTL = 60 * 10             # 10 minutes
NEWS_TTL = 60 * 5                 # 5 minutes

# -- Universe for sector / long-term views ------------------------------
# Sector ETFs used to compute "sector heat" (relative strength rotation).
SECTOR_ETFS = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLV": "Health Care",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLI": "Industrials",
    "XLB": "Materials",
    "XLRE": "Real Estate",
    "XLU": "Utilities",
    "XLC": "Communication Svcs",
}
BENCHMARK = "SPY"

# -- Default watchlists per mode (used on first run) --------------------
DEFAULT_WATCHLISTS = {
    "intraday": ["AMD", "META", "HOOD", "NVDA", "TSLA"],
    "swing": ["AAPL", "MSFT", "AMZN", "GOOGL", "NFLX"],
    "longterm": ["AAPL", "MSFT", "BRK-B", "JNJ", "V", "KO"],
}


def load_watchlists() -> dict:
    """Load saved watchlists, falling back to the defaults on first run."""
    if WATCHLIST_FILE.exists():
        try:
            return json.loads(WATCHLIST_FILE.read_text())
        except Exception:
            pass
    save_watchlists(DEFAULT_WATCHLISTS)
    return dict(DEFAULT_WATCHLISTS)


def save_watchlists(data: dict) -> None:
    WATCHLIST_FILE.write_text(json.dumps(data, indent=2))
