"""
SEC EDGAR fundamentals fallback (the never-blocks source)
=========================================================

Unlike Yahoo, the SEC's EDGAR APIs are public, official, and do NOT block
datacenter IPs - so this is the robust guaranteed floor for the Long-term tab
on Render. It computes the fundamental ratios the scorecard needs directly from
companies' filed XBRL financials.

  ticker -> CIK   : https://www.sec.gov/files/company_tickers.json
  company facts   : https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json

SEC requires a descriptive User-Agent with contact info on every request
(config.SEC_USER_AGENT). US filers only (no CIK -> {}). Price-derived metrics
(P/E, P/B, market cap) need a live price and are left to Finnhub/yfinance; what
EDGAR provides is margins, ROE, revenue/earnings growth, debt/equity and the
current ratio - enough to compute a meaningful composite that never goes blank.
"""

from __future__ import annotations
import re
import threading
import logging
import requests

from .. import config

logger = logging.getLogger(__name__)

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
_CY = re.compile(r"^CY\d{4}$")  # SEC "calendar year" frame = a clean annual value

_ticker_cik: dict[str, int] = {}
_lock = threading.Lock()

# Concept fallbacks - filers tag the same line item under different XBRL names.
_REVENUE = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
            "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"]
_NET_INCOME = ["NetIncomeLoss", "ProfitLoss"]
_EQUITY = ["StockholdersEquity",
           "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]
_LIABILITIES = ["Liabilities"]
_ASSETS_CUR = ["AssetsCurrent"]
_LIAB_CUR = ["LiabilitiesCurrent"]


def _headers() -> dict:
    return {"User-Agent": config.SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}


def _ticker_map() -> dict[str, int]:
    with _lock:
        if _ticker_cik:
            return _ticker_cik
    try:
        r = requests.get(_TICKERS_URL, headers=_headers(), timeout=12)
        data = r.json() if r.ok else {}
        m = {}
        for row in data.values():
            t = str(row.get("ticker", "")).upper().strip()
            if t:
                m[t] = int(row["cik_str"])
        with _lock:
            _ticker_cik.update(m)
        return m
    except Exception as e:
        logger.debug("edgar ticker map: %s", e)
        return {}


def _annual_by_year(facts: dict, concepts: list[str]) -> dict[int, float]:
    """{fiscal_year: value} for the first matching concept, preferring SEC's
    standardized full-year (CYxxxx) frames and de-duplicating by year."""
    us = facts.get("facts", {}).get("us-gaap", {})
    for concept in concepts:
        node = us.get(concept)
        if not node:
            continue
        units = node.get("units", {})
        series = units.get("USD") or next(iter(units.values()), [])
        out: dict[int, float] = {}
        # First pass: clean annual frames (most reliable, already deduped by SEC).
        for e in series:
            frame = e.get("frame")
            if frame and _CY.match(frame) and e.get("val") is not None:
                out[int(frame[2:])] = float(e["val"])
        # Fallback: annual 10-K values keyed by fiscal year.
        if not out:
            for e in series:
                if e.get("form") == "10-K" and e.get("fp") == "FY" and e.get("val") is not None and e.get("fy"):
                    out[int(e["fy"])] = float(e["val"])
        if out:
            return out
    return {}


def _latest_two(by_year: dict[int, float]):
    if not by_year:
        return None, None
    years = sorted(by_year, reverse=True)
    latest = by_year[years[0]]
    prev = by_year[years[1]] if len(years) > 1 else None
    return latest, prev


def _safe_div(a, b):
    if a is None or b is None or b == 0:
        return None
    return a / b


def get_fundamentals(symbol: str) -> dict:
    """Standard fundamentals dict computed from EDGAR filings, or {} if no CIK/data."""
    sym = symbol.upper().strip()
    cik = _ticker_map().get(sym)
    if not cik:
        return {}
    try:
        r = requests.get(_FACTS_URL.format(cik=cik), headers=_headers(), timeout=15)
        if not r.ok:
            return {}
        facts = r.json()
    except Exception as e:
        logger.debug("edgar facts %s: %s", sym, e)
        return {}

    rev, rev_prev = _latest_two(_annual_by_year(facts, _REVENUE))
    ni, ni_prev = _latest_two(_annual_by_year(facts, _NET_INCOME))
    equity, _ = _latest_two(_annual_by_year(facts, _EQUITY))
    liab, _ = _latest_two(_annual_by_year(facts, _LIABILITIES))
    cur_a, _ = _latest_two(_annual_by_year(facts, _ASSETS_CUR))
    cur_l, _ = _latest_two(_annual_by_year(facts, _LIAB_CUR))

    profit_margin = _safe_div(ni, rev)
    roe = _safe_div(ni, equity)
    rev_growth = _safe_div((rev - rev_prev) if (rev is not None and rev_prev is not None) else None, rev_prev)
    eps_growth = (_safe_div((ni - ni_prev), ni_prev)
                  if (ni is not None and ni_prev is not None and ni_prev > 0) else None)
    dte = _safe_div(liab, equity)
    current_ratio = _safe_div(cur_a, cur_l)

    out = {
        "symbol": sym,
        "name": facts.get("entityName"),
        "marketCap": None,        # needs a live price -> filled by Finnhub/yfinance
        "trailingPE": None,
        "pegRatio": None,
        "priceToBook": None,
        "profitMargins": round(profit_margin, 6) if profit_margin is not None else None,
        "returnOnEquity": round(roe, 6) if roe is not None else None,
        "revenueGrowth": round(rev_growth, 6) if rev_growth is not None else None,
        "earningsGrowth": round(eps_growth, 6) if eps_growth is not None else None,
        "debtToEquity": round(dte * 100, 2) if dte is not None else None,
        "currentRatio": round(current_ratio, 4) if current_ratio is not None else None,
        "source": "sec-edgar",
    }
    # Only return if we actually computed something usable.
    if any(out[k] is not None for k in
           ("profitMargins", "returnOnEquity", "revenueGrowth", "debtToEquity", "currentRatio")):
        return out
    return {}
