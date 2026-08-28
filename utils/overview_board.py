"""Every part, every order, and everything moving — one table.

Grew out of the In Transit view (20 Aug 2026) when Hamid asked for the whole
picture rather than only what was in the air: "this should be a dashboard
that covers all the parts, not only in transit... include all the orders".

A row is one of four things, and each part's rows sit together as a block:

    order       one per Order / Sample ID thread, so M101a's seven threads
                are seven rows — different versions and recipients are
                different orders and must not be summed into one line
    leg         a Shipping / Delivery / Return movement
    remainder   what the Stock count says stayed with a sender who part-
                shipped. NOT arithmetic done here: a Shipping event already
                took the quantity out of the sender's count, so this is that
                count read back. Subtracting again would be a second opinion
                on a number the count owns, and the two would drift
    not ordered a part in the project's Overview with no thread and no
                movement — the row that proves nothing was missed

Colour comes from Status alone, never from the text beside it, so a row can
never read green and say "overdue".

Courier facts stay a lead, not a join: matched to a leg by date AND route,
and two candidates on one day print nothing and say how many were found. One
box carries several parts, and the wrong tracking number is worse than none
(`shipments_store.same_day` has the long version).
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, List, Optional

from utils import (holders_store, movements_store, parts_tracker,
                   project_registry, shipments_store, stock_store,
                   tracker_orders)

# --- what a row can be ------------------------------------------------------
ORDERED = "Ordered"
SHIPPED = "Shipped"
DELIVERED = "Delivered"
CANCELLED = "Cancelled"
IN_TRANSIT = "In transit"
STILL_HELD = "Still with sender"
OVERDUE = "Overdue"
UNTRACKED = "Untracked"
SHORT = "Short at sender"
NOT_ORDERED = "Not ordered"

COLOURS = {
    ORDERED:     "#e8f6ec",   # light green — open, nothing wrong
    SHIPPED:     "#e8f6ec",
    IN_TRANSIT:  "#e8f6ec",
    STILL_HELD:  "#ffe8cc",   # orange — the part of the pile that stayed
    DELIVERED:   "#d4edda",   # green — landed
    CANCELLED:   "#e4e6e8",   # grey — called off, kept for the record
    OVERDUE:     "#f8d7da",
    UNTRACKED:   "#f8d7da",
    SHORT:       "#f8d7da",
    NOT_ORDERED: "#ffffff",   # nothing has happened to this part yet
}
BAD = (OVERDUE, UNTRACKED, SHORT)
MOVING = (IN_TRANSIT, OVERDUE, UNTRACKED, SHIPPED)
CLOSED = (DELIVERED, CANCELLED)

COLUMNS = ["Project", "M-Code", "Part", "Version", "Owner", "Qty", "Received",
           "On hand", "From", "From location", "Heading to", "Date", "Courier",
           "Tracking", "ETA / arrived", "Status", "Logged by", "Attention"]

# Within a part: its orders, then what moved, then what stayed behind.
_KIND = {"order": 0, "none": 0, "leg": 1, "rest": 2}


def to_int(text) -> int:
    try:
        return int(float(str(text).replace(",", "").strip() or 0))
    except (TypeError, ValueError):
        return 0


def qty_cell(raw):
    """The quantity as the sheet has it: a number when it is one, the words
    otherwise. Migrated movement rows say "batch" or "set" where nobody
    counted, and printing 0 for those would invent a fact — the cell has to
    keep saying that nobody wrote a number down."""
    text = str(raw or "").strip()
    if not text:
        return ""
    number = to_int(text)
    return number if str(number) == text.replace(",", "") else text


def merge_qty(cells):
    """Total of several legs, keeping any un-counted ones visible."""
    numbers = [c for c in cells if isinstance(c, int)]
    words = sorted({str(c) for c in cells if c != "" and not isinstance(c, int)})
    if numbers and not words:
        return sum(numbers)
    if words and not numbers:
        return ", ".join(words)
    if not numbers and not words:
        return ""
    return "%d + %s" % (sum(numbers), ", ".join(words))


def _day(text):
    return shipments_store.calendar_day(text)


def _today():
    now = datetime.now()
    return (now.month, now.day)


def is_overdue(text, today=None) -> bool:
    """The ETA has gone by. Month and day only, as everywhere else here: both
    logs cover the one year, and the year is the part nobody mistypes."""
    day = _day(text)
    return bool(day) and day < (today or _today())


def same_name(a, b) -> bool:
    a, b = str(a or "").strip().lower(), str(b or "").strip().lower()
    return bool(a) and bool(b) and (a == b or a in b or b in a)


def sort_code(code: str):
    """M101a before M102 before M214 — digits compared as numbers, not text."""
    parts = re.split(r"(\d+)", str(code or ""))
    return [int(p) if p.isdigit() else p.lower() for p in parts]


def route_agrees(courier: dict, leg: dict) -> bool:
    """A courier record naming a different sender or recipient is a different
    consignment. One that names neither stays a candidate."""
    for key in ("from", "to"):
        cv, lv = courier.get(key, ""), leg.get(key, "")
        if str(cv).strip() and str(lv).strip() and not same_name(cv, lv):
            return False
    return True


def courier_of(leg: dict):
    """(courier, tracking, eta, note) — filled only when one record fits."""
    own = str(leg.get("courier", "") or "").strip()
    found = [c for c in shipments_store.same_day(leg.get("date", ""))
             if route_agrees(c, leg)]
    if len(found) == 1:
        one = found[0]
        return (one.get("courier", "") or own, one.get("tracking", ""),
                one.get("eta", ""), "")
    if len(found) > 1:
        return own, "", "", ("%d courier records that day — confirm which"
                             % len(found))
    return own, "", "", "no courier record"


def match_arrivals(sent: List[dict], arrived: List[dict]):
    """Pair each Shipping leg with the Delivery/Return that closes it.

    Matched on recipient and date order, preferring an equal quantity. Hand-
    typed rows carry no consignment id, so this is the only honest key there
    is; an arrival that matches nothing is kept and shown, never dropped.
    """
    free = list(arrived)
    pairs = []
    for leg in sorted(sent, key=lambda r: _day(r.get("date", "")) or (0, 0)):
        left = _day(leg.get("date", "")) or (0, 0)
        want = to_int(leg.get("qty"))
        best = None
        for cand in free:
            if not same_name(cand.get("to", ""), leg.get("to", "")):
                continue
            if (_day(cand.get("date", "")) or (99, 99)) < left:
                continue
            if best is None or (to_int(cand.get("qty")) == want
                                and to_int(best.get("qty")) != want):
                best = cand
        if best is not None:
            free.remove(best)
        pairs.append((leg, best))
    return pairs, free


def _held(part_id: str, project: str, holder: str = ""):
    """(units, counted) from the Stock tab — for one holder, or the part's
    whole count when no holder is named. `counted` keeps "no count row" and
    "none left" apart."""
    rows = [r for r in stock_store.for_part(part_id, project)
            if not holder or same_name(r.get("holder", ""), holder)]
    return sum(to_int(r.get("qty")) for r in rows), bool(rows)


def _blank(project, mcode, part, version, owner, kind):
    return {"Project": project, "M-Code": mcode or "—", "Part": part or "—",
            "Version": version, "Owner": owner, "Qty": "", "Received": "",
            "On hand": "", "From": "—", "From location": "", "Heading to": "—",
            "Date": "", "Courier": "", "Tracking": "", "ETA / arrived": "",
            "Status": "", "Logged by": "", "Attention": "", "_kind": kind}


def _overviews(projects) -> Dict[tuple, dict]:
    """Every part the project records know about, with owner and version —
    the part list this board is measured against."""
    sheets = project_registry.all_projects()
    out: Dict[tuple, dict] = {}
    for project in projects:
        sid = sheets.get(project, "")
        if not sid:
            # A project named only by data rows, with no registered record —
            # fetch_overview("") would silently fall back to the ACTIVE
            # sheet and tag its parts with the wrong project.
            continue
        for row in parts_tracker.fetch_overview(sid):
            code = row.get("mcode", "").strip()
            if not code:
                continue
            out[(project, code.lower())] = {
                "mcode": code,
                "version": row.get("version", ""),
                "part_name": row.get("part_name", ""),
                "owner": row.get("owner", ""),
                "project": project,
            }
    return out


def rows(orders: Optional[List[dict]] = None, legs: Optional[List[dict]] = None,
         today=None) -> List[dict]:
    """Every order, leg, remainder and untouched part, ready for the table."""
    orders = tracker_orders.all_projects_orders() if orders is None else orders
    legs = movements_store.shipments_across_projects() if legs is None else legs
    today = today or _today()

    # The REGISTRY is the project list, not the data: a project registered
    # yesterday with no orders and no movements yet must still show its parts
    # as "Not ordered" — deriving the list from data tags made a brand-new
    # project invisible here until its first order. Data tags are unioned in
    # so rows naming an unregistered project still render.
    projects = sorted(set(project_registry.all_projects())
                      | {r.get("project", "") for r in list(orders) + list(legs)
                         if r.get("project")})
    known = _overviews(projects)
    out: List[dict] = []
    seen_parts = set()
    on_hand_cache: Dict[tuple, str] = {}

    def on_hand(project, code):
        if not code:
            return ""
        if (project, code) not in on_hand_cache:
            units, counted = _held(code, project)
            on_hand_cache[(project, code)] = units if counted else ""
        return on_hand_cache[(project, code)]

    # --- one row per order thread -------------------------------------------
    for order in orders:
        project, code = order.get("project", ""), order.get("mcode", "")
        ident = known.get((project, code.lower()), {})
        seen_parts.add((project, code.lower()))
        derived = str(order.get("derived", "")).lower()
        status = {"delivered": DELIVERED, "cancelled": CANCELLED,
                  "shipped": SHIPPED}.get(derived, ORDERED)
        note = ""
        if status in (ORDERED, SHIPPED) and is_overdue(order.get("eta", ""), today):
            # Said, not coloured: most open threads carry an ETA that has
            # gone by, and a page painted red throughout says nothing.
            note = "ETA %s has passed" % order.get("eta", "")
        row = _blank(project, code, order.get("part_name", "")
                     or ident.get("part_name", ""),
                     order.get("version", "") or ident.get("version", ""),
                     ident.get("owner", ""), "order")
        row.update({
            "Qty": qty_cell(order.get("qty_ordered", "")),
            "Received": qty_cell(order.get("qty_received", "")),
            "On hand": on_hand(project, code),
            "From": order.get("ordered_by", "") or "—",
            "From location": holders_store.location_of(order.get("ordered_by", "")),
            "Heading to": order.get("recipient", "") or "—",
            "Date": order.get("date", ""),
            "ETA / arrived": order.get("eta", ""),
            "Status": status,
            "Logged by": order.get("logged_by", ""),
            "Attention": note,
        })
        out.append(row)

    # --- movements: legs, then what stayed behind ---------------------------
    grouped: Dict[tuple, List[dict]] = {}
    for leg in legs:
        grouped.setdefault(
            (leg.get("project", ""), str(leg.get("part_id", "")).strip()),
            []).append(leg)

    for (project, code), members in grouped.items():
        ident = known.get((project, code.lower()), {})
        if code:
            seen_parts.add((project, code.lower()))
        sent = [r for r in members
                if str(r.get("event", "")).lower() == "shipping"]
        arrived = [r for r in members
                   if str(r.get("event", "")).lower() in ("delivery", "return")]
        pairs, spare = match_arrivals(sent, arrived)
        open_legs = [leg for leg, arr in pairs if arr is None]

        def leg_row(leg, **over):
            row = _blank(project, code,
                         ident.get("part_name") or leg.get("description", ""),
                         ident.get("version", ""), ident.get("owner", ""),
                         "leg")
            row.update({
                "Qty": qty_cell(leg.get("qty")),
                "On hand": on_hand(project, code),
                "From": leg.get("from", "") or "—",
                "From location": holders_store.location_of(leg.get("from", "")),
                "Heading to": leg.get("to", "") or "—",
                "Date": leg.get("date", ""),
                "Logged by": leg.get("logged_by", ""),
            })
            row.update(over)
            return row

        if not open_legs and (pairs or spare):
            # Nothing of this part is moving, so its journeys collapse into
            # one green row PER DESTINATION (Hamid, 20 Aug: "once all
            # delivered, merge and have one green"). Per destination, not per
            # part: collapsing across them would lose where the parts went.
            landed = {}
            for leg, arr in pairs:
                landed.setdefault(leg.get("to", ""), []).append((leg, arr))
            for arr in spare:
                landed.setdefault(arr.get("to", ""), []).append((None, arr))
            for dest, group in landed.items():
                first = next((l for l, _a in group if l is not None),
                             group[0][1])
                courier, tracking, _eta, _note = courier_of(first)
                out.append(leg_row(first, Courier=courier, Tracking=tracking, **{
                    "Heading to": dest or "—",
                    "Qty": merge_qty([qty_cell((arr or l).get("qty"))
                                      for l, arr in group]),
                    "Date": ", ".join(sorted({l.get("date", "")
                                              for l, _a in group if l})) or "—",
                    "ETA / arrived": ", ".join(sorted(
                        {a.get("date", "") for _l, a in group if a})),
                    "Status": DELIVERED,
                    "Attention": ("arrival with no shipping row"
                                  if any(l is None for l, _a in group) else ""),
                }))
            continue

        for leg, arr in pairs + [(None, a) for a in spare]:
            if leg is None:                       # an arrival nobody shipped
                out.append(leg_row(arr, **{
                    "ETA / arrived": arr.get("date", ""), "Status": DELIVERED,
                    "Attention": "arrival with no shipping row"}))
                continue
            courier, tracking, eta, note = courier_of(leg)
            if arr is not None:
                out.append(leg_row(leg, Courier=courier, Tracking=tracking,
                                   **{"ETA / arrived": arr.get("date", ""),
                                      "Status": DELIVERED}))
                continue
            status = IN_TRANSIT
            if is_overdue(eta, today):
                status = OVERDUE
                note = "ETA %s has passed%s" % (eta, " · " + note if note else "")
            elif not eta and not tracking:
                # Nothing to chase, and no date to chase it by. Common on
                # migrated rows; it is the state, not a page failure.
                status = UNTRACKED
                if not note:
                    note = "courier record has no tracking or ETA"
            out.append(leg_row(leg, Courier=courier, Tracking=tracking,
                               **{"ETA / arrived": eta, "Status": status,
                                  "Attention": note}))

        for sender in dict.fromkeys(leg.get("from", "") for leg in open_legs):
            if not sender or not code:
                continue
            left, counted = _held(code, project, sender)
            if not counted or left == 0:
                continue
            row = _blank(project, code, ident.get("part_name", ""),
                         ident.get("version", ""), ident.get("owner", ""),
                         "rest")
            row.update({
                "Qty": left, "On hand": on_hand(project, code),
                "From": sender,
                "From location": holders_store.location_of(sender),
                "Status": SHORT if left < 0 else STILL_HELD,
                "Attention": ("sent more than the count says they hold"
                              if left < 0 else ""),
            })
            out.append(row)

    # --- parts nothing has happened to --------------------------------------
    for (project, low), ident in known.items():
        if (project, low) in seen_parts:
            continue
        row = _blank(project, ident["mcode"], ident.get("part_name", ""),
                     ident.get("version", ""), ident.get("owner", ""), "none")
        row.update({"On hand": on_hand(project, ident["mcode"]),
                    "Status": NOT_ORDERED})
        out.append(row)

    # Stable sort, and deliberately no date key: the dates are hand-typed
    # ("22 Jun", "2026 (T2)", "13-14 Nov 2025") and sorting on them puts a
    # part's story in an order nobody wrote. Insertion order is the ledger's
    # own order, which is the story.
    out.sort(key=lambda r: (r["Project"], sort_code(r["M-Code"]),
                            _KIND.get(r["_kind"], 9)))
    return out
