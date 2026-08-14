"""SEC EDGAR pollers for the filings feed — Form 4 insider buys + SC 13D stakes.

Free primary source: the getcurrent Atom feed lists filings within minutes of
acceptance. We fetch each new Form 4's XML to keep ONLY meaningful open-market
buys (transaction code P, acquired, >= the $ floor) — the tiny fraction of
Form 4 volume worth a founder's glance. 13Ds are unstructured, so we emit the
fact (new activist stake, company, link) without parsing the body.

EDGAR etiquette: declared User-Agent, sequential fetches, paced under the
published 10 req/s cap. ``type=4`` is a PREFIX match on getcurrent — with
``owner=include`` the 100-entry window fills with 424B2/425/485BXT and Form 4s
get crowded out; ``owner=only`` keeps it on ownership forms (measured).
"""

from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://www.sec.gov"
# type= is a PREFIX match. Form 4 needs owner=only so 424B2/425 don't fill the
# window; 13D is not an insider form, so it stays on owner=include.
_ATOM_URL = (
    _BASE + "/cgi-bin/browse-edgar?action=getcurrent&type={form}"
    "&company=&dateb=&owner={owner}&count={count}&output=atom"
)
_TICKERS_URL = _BASE + "/files/company_tickers.json"
_UA = {"User-Agent": "help_me_trade founder feed matt.belliveau@gmail.com"}
_TIMEOUT = 20.0
_ATOM_NS = "{http://www.w3.org/2005/Atom}"

# SEC fair access is 10 req/s. Same pacing as earnings_edgar / edgar_refresh.
_REQUEST_INTERVAL_SEC = 0.13
_RETRY_STATUSES = frozenset({429, 503})
# Bound a pass so a slow/retrying EDGAR cannot look identical to a dead job
# (watchdog threshold for this 600s job is 1800s).
_PASS_BUDGET_SEC = 90.0

# Max new accessions whose XML we fetch per pass; a burst catches up across
# consecutive passes instead of hammering EDGAR in one.
MAX_DOC_FETCHES_PER_PASS = 40
INSIDER_MIN_USD = 100_000.0
_last_request_at: float = 0.0

_ACCESSION_RE = re.compile(r"accession-number=([\d-]+)")
_TITLE_RE = re.compile(r"^(.+?)\s+\((\d{10})\)\s+\((Issuer|Subject|Reporting|Filed by)\)")

# CIK -> ticker map, refreshed daily.
_ticker_map: dict[int, str] = {}
_ticker_map_at: float = 0.0


def _throttle() -> None:
    global _last_request_at
    wait = _REQUEST_INTERVAL_SEC - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def _client() -> httpx.Client:
    return httpx.Client(timeout=_TIMEOUT, headers=_UA, follow_redirects=True)


def _get(client: httpx.Client, url: str) -> httpx.Response:
    """One EDGAR GET, paced, with a single retry on 429/503."""
    _throttle()
    resp = client.get(url)
    if resp.status_code in _RETRY_STATUSES:
        logger.warning("EDGAR %s on %s — retrying once", resp.status_code, url)
        time.sleep(1.5)
        _throttle()
        resp = client.get(url)
    resp.raise_for_status()
    return resp


def _cik_ticker_map(client: httpx.Client) -> dict[int, str]:
    global _ticker_map, _ticker_map_at
    if _ticker_map and time.monotonic() - _ticker_map_at < 86_400:
        return _ticker_map
    data = _get(client, _TICKERS_URL).json()
    mapping = {}
    for entry in data.values():
        try:
            mapping[int(entry["cik_str"])] = str(entry["ticker"]).upper()
        except (KeyError, TypeError, ValueError):
            continue
    if mapping:
        _ticker_map, _ticker_map_at = mapping, time.monotonic()
    return _ticker_map


def parse_atom_entries(xml_text: str) -> list[dict[str, Any]]:
    """Group getcurrent Atom entries by accession number.

    Each filing appears as multiple entries (Issuer + Reporting, or Subject +
    Filed-by) sharing one accession; we fold them into one record keyed on it.
    """
    root = ET.fromstring(xml_text)
    by_accession: dict[str, dict[str, Any]] = {}
    for entry in root.findall(f"{_ATOM_NS}entry"):
        raw_id = (entry.findtext(f"{_ATOM_NS}id") or "").strip()
        m = _ACCESSION_RE.search(raw_id)
        if not m:
            continue
        accession = m.group(1)
        rec = by_accession.setdefault(
            accession,
            {
                "accession": accession,
                "url": "",
                "filed_at": "",
                "issuer": "",
                "cik": None,
                "form": "",
            },
        )
        link = entry.find(f"{_ATOM_NS}link")
        if link is not None and not rec["url"]:
            rec["url"] = link.get("href") or ""
        updated = (entry.findtext(f"{_ATOM_NS}updated") or "").strip()
        if updated and not rec["filed_at"]:
            rec["filed_at"] = updated
        title = (entry.findtext(f"{_ATOM_NS}title") or "").strip()
        if not rec["form"] and " - " in title:
            rec["form"] = title.split(" - ", 1)[0].strip()
        tm = _TITLE_RE.search(title.split(" - ", 1)[-1])
        if tm and tm.group(3) in ("Issuer", "Subject"):
            rec["issuer"] = tm.group(1).strip()
            rec["cik"] = int(tm.group(2))
    return list(by_accession.values())


def _form4_xml_url(client: httpx.Client, index_url: str) -> str | None:
    """The filing's directory index.json names the ownership XML document."""
    dir_url = index_url.rsplit("/", 1)[0]
    listing = _get(client, dir_url + "/index.json").json()
    for item in listing.get("directory", {}).get("item", []):
        name = str(item.get("name", ""))
        if name.endswith(".xml") and "index" not in name.lower():
            return f"{dir_url}/{name}"
    return None


def _text(root: ET.Element, path: str) -> str:
    return (root.findtext(path) or "").strip()


def parse_form4(xml_text: str) -> dict[str, Any]:
    """Extract symbol, owner, role, and summed open-market buy value (code P)."""
    root = ET.fromstring(xml_text)
    symbol = _text(root, "issuer/issuerTradingSymbol").upper()
    owner = _text(root, "reportingOwner/reportingOwnerId/rptOwnerName")

    rel = root.find("reportingOwner/reportingOwnerRelationship")
    role = ""
    if rel is not None:
        title = (rel.findtext("officerTitle") or "").strip()
        if title:
            role = title
        elif (rel.findtext("isDirector") or "").strip() in ("1", "true"):
            role = "Director"
        elif (rel.findtext("isTenPercentOwner") or "").strip() in ("1", "true"):
            role = "10% owner"

    buy_value = 0.0
    buy_shares = 0.0
    prices: list[float] = []
    for txn in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = _text(txn, "transactionCoding/transactionCode")
        acquired = _text(
            txn, "transactionAmounts/transactionAcquiredDisposedCode/value"
        )
        if code != "P" or acquired != "A":
            continue
        try:
            shares = float(
                _text(txn, "transactionAmounts/transactionShares/value") or 0
            )
            price = float(
                _text(txn, "transactionAmounts/transactionPricePerShare/value") or 0
            )
        except ValueError:
            continue
        if shares > 0 and price > 0:
            buy_value += shares * price
            buy_shares += shares
            prices.append(price)

    traded_at = _text(root, "periodOfReport")
    return {
        "symbol": symbol,
        "person": owner,
        "role": role,
        "buy_value": buy_value,
        "buy_shares": buy_shares,
        "avg_price": (sum(prices) / len(prices)) if prices else None,
        "traded_at": traded_at or None,
    }


def format_usd(value: float) -> str:
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"${value / 1_000:.0f}K"
    return f"${value:.0f}"


# Accessions parsed and judged not feed-worthy (sells/grants/tiny buys) —
# in-process cache for consecutive passes; `remember` persists them so a
# restart does not re-burst XML fetches.
_not_feedworthy: set[str] = set()
_NOT_FEEDWORTHY_CAP = 5_000


def _mark_not_feedworthy(uid: str, remember: Callable[[str], None] | None) -> None:
    _not_feedworthy.add(uid)
    if remember is not None:
        remember(uid)


def poll_form4_once(
    *,
    seen: Any,  # callable uid -> bool (db.filings.has_uid)
    remember: Callable[[str], None] | None = None,
    min_usd: float = INSIDER_MIN_USD,
    count: int = 100,
) -> list[dict[str, Any]]:
    """One feed pass: new Form 4 accessions -> insider_buy events >= min_usd."""
    events: list[dict[str, Any]] = []
    if len(_not_feedworthy) > _NOT_FEEDWORTHY_CAP:
        _not_feedworthy.clear()
    deadline = time.monotonic() + _PASS_BUDGET_SEC
    with _client() as client:
        atom = _get(client, _ATOM_URL.format(form="4", owner="only", count=count)).text
        try:
            recs = parse_atom_entries(atom)
        except ET.ParseError as exc:
            raise RuntimeError(
                f"EDGAR Form 4 atom was not XML ({len(atom)} bytes)"
            ) from exc
        fetched = 0
        for rec in recs:
            uid = f"form4:{rec['accession']}"
            # Amendments (4/A) restate an already-reported trade — skip.
            if rec["form"] != "4" or uid in _not_feedworthy or not rec["url"]:
                continue
            if seen(uid):
                continue
            if fetched >= MAX_DOC_FETCHES_PER_PASS:
                break
            if time.monotonic() >= deadline:
                logger.warning(
                    "Form 4 pass hit %.0fs budget after %d fetches — catching up next pass",
                    _PASS_BUDGET_SEC,
                    fetched,
                )
                break
            fetched += 1
            try:
                xml_url = _form4_xml_url(client, rec["url"])
                if not xml_url:
                    _mark_not_feedworthy(uid, remember)
                    continue
                parsed = parse_form4(_get(client, xml_url).text)
            except Exception as exc:
                logger.warning("Form 4 fetch/parse failed %s: %s", rec["accession"], exc)
                continue
            if not parsed["symbol"] or parsed["buy_value"] < min_usd:
                _mark_not_feedworthy(uid, remember)
                continue
            events.append(
                {
                    "uid": uid,
                    "kind": "insider_buy",
                    "symbol": parsed["symbol"],
                    "person": parsed["person"],
                    "role": parsed["role"],
                    "side": "buy",
                    "value_usd": parsed["buy_value"],
                    "value_label": format_usd(parsed["buy_value"]),
                    "filed_at": rec["filed_at"],
                    "traded_at": parsed["traded_at"],
                    "url": rec["url"],
                    "detail": {
                        "issuer": rec["issuer"],
                        "shares": parsed["buy_shares"],
                        "avg_price": parsed["avg_price"],
                    },
                }
            )
    return events


def poll_13d_once(*, seen: Any, count: int = 40) -> list[dict[str, Any]]:
    """One feed pass over new SC 13D filings (initial activist stakes only —
    amendments /A excluded; the initial filing is the event)."""
    events: list[dict[str, Any]] = []
    with _client() as client:
        atom = _get(client, _ATOM_URL.format(form="SC+13D", owner="include", count=count)).text
        try:
            recs = parse_atom_entries(atom)
        except ET.ParseError as exc:
            raise RuntimeError(
                f"EDGAR 13D atom was not XML ({len(atom)} bytes)"
            ) from exc
        tickers = _cik_ticker_map(client)
        for rec in recs:
            uid = f"13d:{rec['accession']}"
            # Amendments (SC 13D/A) restate an existing stake — initial only.
            if rec["form"] != "SC 13D" or seen(uid):
                continue
            symbol = tickers.get(rec["cik"] or -1, "")
            if not symbol:
                continue  # funds/private targets — not chartable, skip
            events.append(
                {
                    "uid": uid,
                    "kind": "stake_13d",
                    "symbol": symbol,
                    "person": rec["issuer"],
                    "role": "new >5% stake",
                    "side": "buy",
                    "value_usd": None,
                    "value_label": "13D",
                    "filed_at": rec["filed_at"],
                    "traded_at": None,
                    "url": rec["url"],
                    "detail": {"subject": rec["issuer"]},
                }
            )
    return events
