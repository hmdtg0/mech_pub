"""The holder directory, as the main record's `Holders` tab defines it.

THE one definition of every person and vendor, shared across projects
(Hamid, 18 Aug 2026: "for the holder, we have a defined list which will be
updated regularly"). Because the list is maintained on the sheet and changes
often, nothing here hard-codes a name: every holder the app offers comes from
this tab, and a holder the tab has never heard of is reported rather than
quietly accepted.

Nearly read-only. Holders are added on the sheet, not by the app — that is
what makes the tab authoritative — with ONE exception since 28 Aug 2026
(Hamid): a vendor or new name typed on the entry form is registered here
by `register()` BEFORE the entry lands, so the directory never lags what
the ledger says and the stock count knows a "source" from a person.
"""
from __future__ import annotations

from typing import Dict, List

from config import CENTRAL_SHEET_ID, TAB_HOLDERS
from utils import data_cache
from utils.tracker_parse import norm

_TTL_SECONDS = 300

_FIELDS = {
    "holder": "name",
    "name": "name",
    "kind": "kind",
    "location": "location",
    "contact": "contact",
    "notes": "notes",
}


def _cache_key() -> str:
    return "%s:holders" % CENTRAL_SHEET_ID


def _load() -> List[Dict[str, str]]:
    from utils.google_client import with_worksheet

    try:
        values = with_worksheet(TAB_HOLDERS, lambda ws: ws.get_all_values(),
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
        if rec.get("name"):
            out.append(rec)
    return out


def fetch_holders() -> List[Dict[str, str]]:
    """Every holder in the directory, in sheet order."""
    return data_cache.get(_cache_key(), _TTL_SECONDS, _load)


def names(kind: str = "") -> List[str]:
    """Holder names for a picker — all of them, or just one kind
    ("person" / "vendor" / "site")."""
    rows = fetch_holders()
    if kind:
        rows = [r for r in rows if r.get("kind", "").lower() == kind.lower()]
    return [r["name"] for r in rows]


def get(name: str) -> Dict[str, str]:
    """One holder's record, matched case-insensitively. {} if unknown."""
    target = str(name or "").strip().lower()
    for row in fetch_holders():
        if row["name"].lower() == target:
            return row
    return {}


def location_of(name: str) -> str:
    """Where a holder physically is, per the directory."""
    return get(name).get("location", "")


def is_known(name: str) -> bool:
    return bool(get(name))


def unknown(used: List[str]) -> List[str]:
    """Which of these holder names the directory does not define.

    The app writes only directory names, but the sheets carry years of
    hand-typed ones ("Sam Smith" where the directory says "Sam"). Naming
    them is the whole point — a silent near-match is how two people end up
    counted as one.
    """
    seen, out = set(), []
    for name in used:
        text = str(name or "").strip()
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        if not is_known(text):
            out.append(text)
    return out


def register(name: str, kind: str = "person", notes: str = "") -> str:
    """Add a name to the Holders tab — the app's one write to the directory.

    The entry form calls this for a TYPED name before it writes the entry
    (Hamid, 28 Aug: "make sure the vendor will be recorded in the database
    sheet ... before user submits it"). `kind` matters to the count: a
    "source" hands goods over and never goes negative; a "person" holds
    stock. A name the directory already knows is left exactly as it is.
    Returns "" on success, the problem as text otherwise.
    """
    from utils.google_client import with_worksheet

    text = str(name or "").strip()
    if not text:
        return "no name given"
    if is_known(text):
        return ""

    def _do(ws):
        values = ws.get_all_values()
        header = [norm(h) for h in (values[0] if values else [])]
        if not header:
            return "the Holders tab has no header row"
        row = [""] * len(header)
        placed = False
        for i, key in enumerate(header):
            field = _FIELDS.get(key)
            if field == "name" and not placed:
                row[i] = text
                placed = True
            elif field == "kind":
                row[i] = kind
            elif field == "notes" and notes:
                row[i] = notes
        if not placed:
            return "the Holders tab has no name column"
        ws.append_row(row, value_input_option="USER_ENTERED")
        return ""

    try:
        problem = with_worksheet(TAB_HOLDERS, _do, sheet_id=CENTRAL_SHEET_ID)
    except Exception as exc:
        return str(exc)
    refresh()
    return problem or ""


def refresh() -> None:
    data_cache.invalidate(_cache_key())
