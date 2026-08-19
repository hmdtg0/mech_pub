"""Shipments, as the main record's Shipments tab records them.

One central log for everything couriered between holders (Hamid, 18 Aug
2026) — migrated from the pilot's free-text log with courier and tracking
split out, an ISO date next to the date as originally written, and an
optional `order ID` linking a shipment to its Orders row. Read-only here:
rows are entered on the sheet (and later by the app's logistics flows).

The Shipments PAGE reads this store. Per-part history stays on the part
tabs in each project record; this is the logistics view across projects.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from config import CENTRAL_SHEET_ID
from utils import data_cache
from utils.tracker_parse import norm

SHIPMENTS_TAB = "Shipments"

_TTL_SECONDS = 300

# Normalised header -> our field name. The tab's own spellings first, plus
# plainer fallbacks so a renamed column keeps working.
_FIELDS = {
    "date": "date",
    "dateaswritten": "date_text",
    "itembuild": "item",
    "item": "item",
    "orderid": "order_id",
    "qty": "qty",
    "fromholder": "from",
    "from": "from",
    "toholder": "to",
    "to": "to",
    "courier": "courier",
    "tracking": "tracking",
    "couriertracking": "tracking",
    "etaaswritten": "eta",
    "eta": "eta",
    "deliveryreceipt": "delivery",
    "status": "status",
    "flags": "flags",
    "srcrow": "src_row",
    "notesverbatim": "notes",
    "notes": "notes",
}


def _cache_key() -> str:
    return "%s:shipments" % CENTRAL_SHEET_ID


def _load() -> List[Dict[str, str]]:
    from utils.google_client import with_worksheet

    try:
        values = with_worksheet(SHIPMENTS_TAB,
                                lambda ws: ws.get_all_values(),
                                sheet_id=CENTRAL_SHEET_ID)
    except Exception:
        return []
    if not values:
        return []
    header = [norm(h) for h in values[0]]
    out = []
    for row in values[1:]:
        rec: Dict[str, str] = {}
        for i, key in enumerate(header):
            field = _FIELDS.get(key)
            if field and i < len(row) and str(row[i]).strip():
                rec[field] = str(row[i]).strip()
        if rec:
            out.append(rec)
    return out


def fetch_shipments() -> List[Dict[str, str]]:
    return data_cache.get(_cache_key(), _TTL_SECONDS, _load)


_MONTHS = {month: i + 1 for i, month in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}


def calendar_day(text: str) -> Optional[Tuple[int, int]]:
    """The (month, day) a hand-written date names, or None.

    The two logs spell dates differently because different people typed them:
    the courier tab is ISO, the ledger has `~13 Apr 2026` and `16-21 Apr
    2026`. A range takes its LAST day — that is when the consignment left.
    Only month and day are compared; both logs cover the one year, and the
    year is the part nobody mistypes.
    """
    value = str(text or "").strip().lower()
    iso = re.search(r"(\d{4})-(\d{2})-(\d{2})", value)
    if iso:
        return int(iso.group(2)), int(iso.group(3))
    named = re.search(r"(\d{1,2})\s*(?:[-–]\s*(\d{1,2}))?\s*([a-z]{3})", value)
    if named:
        month = _MONTHS.get(named.group(3)[:3])
        if month:
            return month, int(named.group(2) or named.group(1))
    return None


def same_day(date_text: str) -> List[Dict[str, str]]:
    """Courier records posted on the day a ledger event names.

    Deliberately NOT a join. One consignment carries several parts, so a
    record can belong to more than one event; the dates are hand-typed; and
    some courier records have no ledger event at all. Same-day is a lead for
    a person to confirm, never asserted as the shipment's tracking number.
    """
    wanted = calendar_day(date_text)
    if not wanted:
        return []
    return [row for row in fetch_shipments()
            if calendar_day(row.get("date", "") or row.get("date_text", "")) == wanted]


def refresh() -> None:
    data_cache.invalidate(_cache_key())
