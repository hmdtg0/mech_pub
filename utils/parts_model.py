"""The bridge: one parts view merging the BOM sheet and the parts tracker.

Both sheets describe the same physical parts but disagree on shape:

- BOM build tab   — identity, CAD versions, cost, owner, lead time
- BOM lifecycle   — order/inventory counts per part
- BOM movement log— structured shipments (M-code + version on every row)
- pilot tracker   — per-part history, current holder/location, Selected flag

Records are keyed on the M-code. Suffixed codes (M101a/M101b) are matched
exactly first; when one side only has the unsuffixed base (older BOM tabs
say M101 where the pilot has M101a), the match falls back to the base code
and is marked ``approximate`` instead of being silently merged.

Every merged field keeps its source, and parts that exist on one side only
come back from `unmatched()` — shown in the UI, never swallowed.

Read + derive only: nothing in this module writes anywhere.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from utils import bom_sheet, parts_tracker, tracker_parse
from utils.project_registry import active, bom_sheet as bom_sheet_id, tracker_sheet


# --- identity ----------------------------------------------------------------

_CODE = re.compile(r"^\s*([Mm]\s*\d+)\s*([A-Za-z]?)\s*$")


def normalise_code(text: str) -> str:
    """`M101a`, `M101 a`, `m101A` -> `M101a`; anything else stripped verbatim.

    The suffix is kept — it distinguishes real variants (mirror vs black
    glass), so it must never be dropped in a "cleanup".
    """
    match = _CODE.match(str(text or ""))
    if not match:
        return str(text or "").strip()
    body, suffix = match.groups()
    return "M" + body[1:].replace(" ", "") + suffix.lower()


def base_code(code: str) -> str:
    """`M101a` -> `M101` (for the approximate fallback match)."""
    norm = normalise_code(code)
    return norm[:-1] if norm and norm[-1].isalpha() and norm[0] == "M" else norm


# --- status vocabulary --------------------------------------------------------
# One ordered pipeline for every spelling observed across the sheets. Values
# not in the map pass through labelled as themselves — never hidden.
STATUS_ORDER = ["To order", "Ordered", "In production", "In transit",
                "Received", "Assembled", "Done"]

STATUS_MAP = {
    # BOM "Ready to order?" / "Order Status" spellings
    "ready": "To order",
    "ready to order": "To order",
    "ready for order": "To order",
    "to order": "To order",
    "toorder": "To order",
    "not ready": "To order",          # not orderable yet, still pre-order
    "pending": "To order",
    "ordered": "Ordered",
    "in production": "In production",
    "producing": "In production",
    "in transit": "In transit",
    "shipping": "In transit",
    "shipped": "In transit",
    "received": "Received",
    "recieved": "Received",
    "finished": "Received",           # BOM uses Finished = order completed
    "deliveredtovendor": "Received",
    "delivered to vendor": "Received",
    "assembled": "Assembled",
    "done": "Done",
    "superseded": "Done",
    "fromstock": "Received",
}


def normalise_status(text: str) -> str:
    """Map a sheet status onto the shared vocabulary; unknowns pass through."""
    return STATUS_MAP.get(str(text or "").strip().lower(), str(text or "").strip())


def status_rank(text: str) -> int:
    """Position in the pipeline, for sorting/bucketing. Unknowns sort first."""
    status = normalise_status(text)
    return STATUS_ORDER.index(status) if status in STATUS_ORDER else -1


# --- derived: stale CAD -------------------------------------------------------

def stale_cad(part: dict) -> bool:
    """Ordered against an outdated CAD? Only fires when both version columns
    are present and disagree — blanks are unknowns, not alarms."""
    last = str(part.get("last_ordered_cad", "")).strip()
    recent = str(part.get("most_recent_cad", "")).strip()
    return bool(last) and bool(recent) and last != recent


# --- derived: stock from movements ---------------------------------------------

def stock_by_location(movements: List[dict]) -> Dict[str, Dict[str, int]]:
    """{mcode: {location: qty}} derived from the BOM movement log.

    Qty moves from `from` to `to` when the row has a received date (or a
    Received status); rows still in transit count against the origin only
    when marked shipped. Derivation, not truth — compare against the sheet's
    own counts and FLAG disagreements (see stock_mismatches), never pick a
    winner silently.
    """
    stock: Dict[str, Dict[str, int]] = {}
    for row in movements:
        code = normalise_code(row.get("mcode", ""))
        qty = tracker_parse.to_int(row.get("qty", ""))
        if not code or not qty:
            continue
        arrived = (str(row.get("date_received", "")).strip()
                   or normalise_status(row.get("status", "")) == "Received")
        origin = str(row.get("from", "")).strip()
        dest = str(row.get("to", "")).strip()
        per = stock.setdefault(code, {})
        if origin and origin.lower() != "vendor":
            per[origin] = per.get(origin, 0) - qty
        if arrived and dest:
            per[dest] = per.get(dest, 0) + qty
        elif not arrived and dest:
            per["in transit → %s" % dest] = per.get("in transit → %s" % dest, 0) + qty
    return stock


def stock_mismatches(part: dict) -> List[str]:
    """Where the derived stock disagrees with the sheet's own counts."""
    flags = []
    derived = part.get("stock", {}) or {}
    uk_sheet = str(part.get("uk_qty", "")).strip()
    if uk_sheet != "":
        uk_derived = sum(q for loc, q in derived.items() if "uk" in loc.lower())
        if tracker_parse.to_int(uk_sheet) != uk_derived and (uk_derived or tracker_parse.to_int(uk_sheet)):
            flags.append("UK qty: sheet says %s, movements add to %d"
                         % (uk_sheet, uk_derived))
    return flags


# --- the merge -----------------------------------------------------------------

def _index(rows: List[dict], key: str = "mcode") -> Dict[str, dict]:
    out = {}
    for row in rows:
        code = normalise_code(row.get(key, ""))
        if code and code not in out:
            out[code] = row
    return out


def unified_parts(project: Optional[str] = None,
                  build: Optional[str] = None) -> List[dict]:
    """One record per part for a project, merged from every source.

    Field origins (each record carries them in ``_sources``):
      identity / CAD / cost / owner  -> BOM build tab (`build` or the default)
      order & inventory counts       -> BOM lifecycle tab
      holder / location / selected   -> pilot tracker Overview
      stock                          -> derived from the BOM movement log
    """
    name = project or active()[0]
    bom_id = bom_sheet_id(name)
    tracker_id = tracker_sheet(name)

    bom_rows = []
    lifecycle = []
    movements = []
    if bom_id:
        tab = build or bom_sheet.default_build(bom_id)
        bom_rows = bom_sheet.fetch_bom(tab, bom_id)
        lifecycle = bom_sheet.fetch_lifecycle(bom_id)
        movements = bom_sheet.fetch_movement_log(bom_id)
    overview = parts_tracker.fetch_overview(tracker_id) if tracker_id else []

    bom_idx = _index(bom_rows)
    life_idx = _index(lifecycle)
    over_idx = _index(overview)
    stock = stock_by_location(movements)

    # Approximate fallback: base code -> exact codes present on each side.
    def base_map(idx):
        out: Dict[str, List[str]] = {}
        for code in idx:
            out.setdefault(base_code(code), []).append(code)
        return out

    over_bases = base_map(over_idx)

    records = []
    seen_over = set()
    for code, bom_row in bom_idx.items():
        rec = {"mcode": code, "_sources": {}, "approximate": False}
        rec.update({k: v for k, v in bom_row.items() if not k.startswith("_")})
        rec["_sources"]["identity"] = "BOM"

        life = life_idx.get(code)
        if life:
            for k in ("order_date", "order_status", "ordered_qty", "location",
                      "in_production_qty", "uk_qty", "assembled_qty",
                      "final_unit_qty"):
                if life.get(k, "") != "":
                    rec[k] = life[k]
            rec["_sources"]["inventory"] = "Lifecycle"

        over = over_idx.get(code)
        matched_code = code
        if over is None:
            # base-code fallback: unsuffixed BOM row vs suffixed tracker tabs
            candidates = over_bases.get(base_code(code), [])
            exact_elsewhere = [c for c in candidates if c in bom_idx and c != code]
            candidates = [c for c in candidates if c not in exact_elsewhere]
            if len(candidates) == 1 and candidates[0] != code:
                over = over_idx[candidates[0]]
                matched_code = candidates[0]
                rec["approximate"] = True
        if over is not None:
            seen_over.add(matched_code)
            for src, dst in (("holder", "holder"), ("location", "tracker_location"),
                             ("status", "tracker_status"), ("version", "tracker_version"),
                             ("notes", "tracker_notes")):
                if over.get(src, "").strip():
                    rec[dst] = over[src]
            rec["_sources"]["holder"] = ("tracker ~%s" % matched_code
                                         if rec["approximate"] else "tracker")

        if code in stock:
            rec["stock"] = stock[code]
            rec["_sources"]["stock"] = "derived from movement log"

        rec["status_norm"] = normalise_status(
            rec.get("order_status", "") or rec.get("tracker_status", ""))
        rec["stale_cad"] = stale_cad(rec)
        records.append(rec)

    # Tracker-only parts (no BOM row in this build) — still shown.
    for code, over in over_idx.items():
        if code in seen_over or code in bom_idx:
            continue
        rec = {"mcode": code, "_sources": {"identity": "tracker"},
               "approximate": False,
               "part_name": over.get("part_name", ""),
               "holder": over.get("holder", ""),
               "tracker_location": over.get("location", ""),
               "tracker_status": over.get("status", ""),
               "tracker_version": over.get("version", ""),
               "tracker_notes": over.get("notes", "")}
        if code in stock:
            rec["stock"] = stock[code]
            rec["_sources"]["stock"] = "derived from movement log"
        rec["status_norm"] = normalise_status(over.get("status", ""))
        rec["stale_cad"] = False
        records.append(rec)

    return records


def unmatched(project: Optional[str] = None,
              build: Optional[str] = None) -> Dict[str, List[dict]]:
    """Parts that did not bridge, split by which side they exist on.

    First-class output: the UI shows this, because a silent merge that drops
    parts is exactly the "something got lost" failure this app exists to stop.
    """
    parts = unified_parts(project, build)
    # Whether a part bridged is recorded in `_sources`, which is set on every
    # match. Testing for the DATA keys instead reported a bridged part as
    # BOM-only whenever its Overview row happened to be blank — which is every
    # part on a freshly built record, i.e. exactly when the list is read most.
    bom_only = [p for p in parts
                if p["_sources"].get("identity") == "BOM"
                and not p["_sources"].get("holder")
                and not p["_sources"].get("tracker_status")]
    tracker_only = [p for p in parts if p["_sources"].get("identity") == "tracker"]
    approx = [p for p in parts if p.get("approximate")]
    return {"bom_only": bom_only, "tracker_only": tracker_only,
            "approximate": approx}
