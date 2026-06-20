"""
News aggregator (RSS-first)
===========================

Per-ticker headlines for whatever is on your watchlist, from free RSS feeds:

  1. Yahoo Finance per-symbol RSS  (primary - clean, fast, no key)
       https://feeds.finance.yahoo.com/rss/2.0/headline?s=NVDA&region=US&lang=en-US
  2. Google News RSS  (fallback / breadth - searches the company by symbol)
       https://news.google.com/rss/search?q=NVDA+stock&hl=en-US&gl=US&ceid=US:en

Each headline is tagged deal / bullish / bearish / neutral using the keyword
sets from the user's existing news_scanner.py, so the UI can colour-code them.

RSS is more reliable than scraping and updates within minutes - ideal for an
intraday watchlist feed.
"""

from __future__ import annotations
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote_plus

from .. import config

logger = logging.getLogger(__name__)

DEAL_KEYWORDS = [
    "partnership", "joint venture", "collaboration", "acqui", "merger",
    "takeover", "wins contract", "awarded contract", "signs deal", "signed deal",
    "signs agreement", "license agreement", "licensing deal", "strategic alliance",
    "strategic partnership", "supply agreement", "agreement with", "contract with",
]
BULLISH_KEYWORDS = [
    "record", "beat", "beats", "exceeds", "guidance raised", "upgrade", "upgraded",
    "expands", "surge", "soar", "rally", "strong demand", "backlog", "accelerat", "all-time high",
]
BEARISH_KEYWORDS = [
    "miss", "misses", "downgrade", "downgraded", "cut", "cuts", "layoff", "loss",
    "decline", "warning", "below", "disappoint", "recall", "probe", "investigation", "lawsuit", "plunge",
]

_cache: dict[str, tuple[float, list]] = {}
_lock = threading.Lock()

YAHOO_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={sym}&region=US&lang=en-US"
GOOGLE_RSS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def _classify(title: str, summary: str = "") -> str:
    t = title.lower()
    text = (title + " " + summary).lower()
    if any(k in t for k in DEAL_KEYWORDS):
        return "deal"
    if any(k in text for k in BULLISH_KEYWORDS):
        return "bullish"
    if any(k in text for k in BEARISH_KEYWORDS):
        return "bearish"
    return "neutral"


def _parse_feed(url: str, source: str, limit: int) -> list[dict]:
    import feedparser
    feed = feedparser.parse(url)
    items = []
    for e in feed.entries[:limit]:
        title = getattr(e, "title", "")
        if not title:
            continue
        summary = getattr(e, "summary", "")
        published = getattr(e, "published", "") or getattr(e, "updated", "")
        items.append({
            "title": title,
            "link": getattr(e, "link", ""),
            "published": published,
            "source": source,
            "sentiment": _classify(title, summary),
        })
    return items


def fetch_symbol(symbol: str, limit: int = 12) -> list[dict]:
    """Headlines for one symbol (cached)."""
    symbol = symbol.upper().strip()
    with _lock:
        hit = _cache.get(symbol)
    if hit and (time.time() - hit[0]) < config.NEWS_TTL:
        return hit[1]

    items: list[dict] = []
    try:
        items = _parse_feed(YAHOO_RSS.format(sym=symbol), "Yahoo Finance", limit)
    except Exception as e:
        logger.debug("Yahoo RSS %s: %s", symbol, e)

    if len(items) < 3:  # thin coverage - augment with Google News
        try:
            q = quote_plus(f"{symbol} stock")
            items += _parse_feed(GOOGLE_RSS.format(q=q), "Google News", limit)
        except Exception as e:
            logger.debug("Google RSS %s: %s", symbol, e)

    # de-dupe by title, keep order
    seen, deduped = set(), []
    for it in items:
        key = it["title"].lower()[:80]
        if key not in seen:
            seen.add(key)
            deduped.append(it)
    deduped = deduped[:limit]

    with _lock:
        _cache[symbol] = (time.time(), deduped)
    return deduped


def fetch_many(symbols: list[str]) -> dict[str, list]:
    """Headlines for a list of symbols, fetched in parallel."""
    out: dict[str, list] = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(fetch_symbol, s): s for s in symbols}
        for f in as_completed(futs):
            sym = futs[f]
            try:
                out[sym] = f.result()
            except Exception:
                out[sym] = []
    return out
