"""The merged movement log — one per project record (Hamid, 19 Aug 2026).

Replaces the split between a project's own `Movement Log` and the central
`Stock_History`, which recorded the same events in two shapes in two files.
Every row carries an **Event**, and the event decides where the row shows up:
`config.MOVEMENT_EVENTS` is the one table, so the stock count, the Movements
page, the Shipments view and Part Detail cannot disagree about what counts as
a movement.

The old free-text `Stage` column is split in two: `Event` says what kind of
thing happened, `Build` says which batch it belonged to. Stage tried to be
both and could drive neither.

`Flag` is deliberately separate from `Event`: an event is what happened, a
flag is how sure we are. `TBC` marks the one thing a calculation cannot
settle — a movement that shifts stock but states no quantity. Everything else
worth flagging is derived, and a manual flag that duplicates a derived check
goes stale the moment the data is fixed.

Read-only. Rows are appended by Process Order's movement form.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from config import (TAB_MOVEMENTS_MERGED, event_rule, moves_stock,
                    on_movements, on_shipments)
from utils import data_cache
from utils.tracker_parse import norm, to_int

_TTL_SECONDS = 120

_FIELDS = {
    "date": "date",
    "event": "event",
    "partid": "part_id",
    "mcode": "part_id",
    "description": "description",
    "itembuild": "description",
    "item": "description",
    "type": "type",
    "qty": "qty",
    "qtyonhand": "qty",
    "from": "from",
    "to": "to",
    "sentto": "to",
    "couriertracking": "courier",
    "courier": "courier",
    "build": "build",
    "stage": "build",
    "loggedby": "logged_by",
    "loggedat": "logged_at",
    "flag": "flag",
    "notes": "notes",
}


def _key(sheet_id: str) -> str:
    return "%s:merged_movements" % sheet_id


def _load(sheet_id: Optional[str]) -> List[Dict[str, str]]:
    from utils.google_client import with_worksheet

    try:
        values = with_worksheet(TAB_MOVEMENTS_MERGED,
                                lambda ws: ws.get_all_values(),
                                sheet_id=sheet_id)
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


def fetch(sheet_id: Optional[str] = None) -> List[Dict[str, str]]:
    """Every row of a project's movement log, in sheet order."""
    from utils.google_client import active_sheet_id
    sid = sheet_id or active_sheet_id()
    return data_cache.get(_key(sid), _TTL_SECONDS, lambda: _load(sid),
                          spinner="Loading movements…")


def has_log(sheet_id: Optional[str] = None) -> bool:
    """Whether this project HAS the merged log — tab present, rows or not.

    Deliberately not `bool(fetch(...))`. A record built today has the tab and
    nothing in it, and treating empty as "not migrated" would send its very
    first movement to the retired central log — the one case where getting it
    wrong is hardest to notice, because there is nothing there to compare
    against.
    """
    from utils.google_client import active_sheet_id, with_worksheet

    try:
        values = with_worksheet(TAB_MOVEMENTS_MERGED,
                                lambda ws: ws.get_all_values(),
                                sheet_id=sheet_id or active_sheet_id())
    except Exception:
        return False
    return bool(values) and any(str(c).strip() for c in values[0])


def exists(sheet_id: Optional[str] = None) -> bool:
    """Whether this project has the merged log yet — false for a record still
    on the old two-tab shape, so callers can fall back rather than show
    nothing."""
    return has_log(sheet_id)


def physical(sheet_id: Optional[str] = None) -> List[Dict[str, str]]:
    """Rows the Movements page shows: goods actually moving."""
    return [r for r in fetch(sheet_id) if on_movements(r.get("event", ""))]


def shipments(sheet_id: Optional[str] = None) -> List[Dict[str, str]]:
    """Rows the Shipments view shows: anything that travelled by courier."""
    return [r for r in fetch(sheet_id) if on_shipments(r.get("event", ""))]


def shipments_across_projects() -> List[Dict[str, str]]:
    """Every courier-worthy event in every project, tagged with its project.

    Shipments is the one genuinely cross-project log — a single consignment
    can carry parts for two projects — so it is DERIVED from the per-project
    logs rather than hand-kept as a separate tab (Hamid, 19 Aug 2026). One
    log per project plus one view across them, instead of four tabs that
    drift apart.
    """
    from utils import project_registry

    out: List[Dict[str, str]] = []
    for name, sheet_id in sorted(project_registry.all_projects().items()):
        if not sheet_id:
            continue
        for row in shipments(sheet_id):
            tagged = dict(row)
            tagged["project"] = name
            out.append(tagged)
    return out


def for_part(part_id: str, sheet_id: Optional[str] = None) -> List[Dict[str, str]]:
    """A part's movements, matched on the Part ID column — exactly, not by
    hunting for the code in free text. That guessing is what this column was
    added to end."""
    code = str(part_id or "").strip().lower()
    if not code:
        return []
    return [r for r in fetch(sheet_id)
            if r.get("part_id", "").strip().lower() == code]


def flagged(sheet_id: Optional[str] = None) -> List[Dict[str, str]]:
    """Rows a person still has to settle."""
    return [r for r in fetch(sheet_id) if r.get("flag", "").strip()]


def holdings(sheet_id: Optional[str] = None,
             holds_stock=None) -> Dict[tuple, int]:
    """What the log implies each holder has, keyed by (part, holder).

    `holds_stock(name)` decides whether a name is a place we count — supplier
    categories hand stock over and never hold a negative balance of it. The
    caller supplies it so this module needs no view of the Holders directory.
    """
    if holds_stock is None:
        from utils import holders_store

        def holds_stock(name: str) -> bool:
            if not name or name == "(external)":
                return False
            return holders_store.get(name).get("kind", "person").lower() != "source"

    out: Dict[tuple, int] = {}
    for row in fetch(sheet_id):
        part = row.get("part_id", "")
        qty = to_int(row.get("qty", ""))
        if not part or qty <= 0 or not moves_stock(row.get("event", "")):
            continue
        effect = event_rule(row.get("event", ""))["stock"]
        src, dst = row.get("from", ""), row.get("to", "")
        if effect in ("transfer", "out") and holds_stock(src):
            out[(part, src)] = out.get((part, src), 0) - qty
        if effect in ("transfer", "in") and holds_stock(dst):
            out[(part, dst)] = out.get((part, dst), 0) + qty
    return out


def refresh(sheet_id: Optional[str] = None) -> None:
    from utils.google_client import active_sheet_id
    data_cache.invalidate(_key(sheet_id or active_sheet_id()))
