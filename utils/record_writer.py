"""The Record screen's rules and writers — one sentence for everything.

Joe, 18 Sep 2026: "just strip this back to movement from one place to
another. How it gets there isn't a concern... it should be its own form
with a dropdown to select the component... all from a single screen."
Hamid, 19 Sep: "the whole thing is a bit complicated. It could be much
simpler" — and, of the two ways to treat goods on the move, "make this
middle version so I can test".

So everything the team records is ONE sentence: *N of part X went from A to
B on date D*. Nobody picks a kind of entry; From and To decide it, by the
directory kind of each name:

    supplier  -> holder     goods arriving   (against the part's open order)
    holder    -> holder     a move           (both counts, at once)
    holder    -> SINK       written off
    holder    -> supplier   going back

**The middle version.** A move changes both counts the moment it is
recorded, tracking number or not — so nobody forgetting a second step can
leave a count wrong, which is exactly what the Ship/Arrived pair allowed. A
move that carries a tracking number additionally reads as "on its way"
until someone ticks that it arrived. The tick is a label: an `Arrived` row
on the part's ledger that moves no count.

Nothing here invents a new kind of sheet row. A move is the `Movement`
event the ledger has always recognised (a transfer), a write-off is
`Scrap`, a return is `Return`, an arrival is the same receive line and the
same order write-back Process Order makes. The planning half is pure — it
takes plain dicts and returns effects and problems — so the rules are
tested without a sheet.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Tuple

SINK = "Scrapped / written off"

EVENT_OF = {"move": "Movement", "scrap": "Scrap", "return": "Return"}
WORDS = {"receipt": "Goods arriving", "move": "Move",
         "scrap": "Written off", "return": "Back to the supplier"}
TICK_EVENT = "Arrived"


def to_qty(value) -> int:
    try:
        return int(float(str(value if value is not None else "").strip() or 0))
    except (TypeError, ValueError):
        return 0


def kind_of(from_name: str, to_name: str, holds_stock) -> str:
    """What a From -> To pair means. `holds_stock(name)` says whether a name
    carries a count (people, places and named vendors do; suppliers do not).
    "" while either end is still blank."""
    a, b = str(from_name or "").strip(), str(to_name or "").strip()
    if not a or not b:
        return ""
    if a.lower() == b.lower():
        return "same"
    if b == SINK:
        return "scrap" if holds_stock(a) else "invalid"
    if holds_stock(a):
        return "move" if holds_stock(b) else "return"
    return "receipt" if holds_stock(b) else "invalid"


def plan(kind: str, lines: List[dict], from_name: str, to_name: str,
         holdings: Dict[tuple, int], open_orders: Dict[str, List[dict]],
         note: str = "") -> Tuple[List[dict], List[str]]:
    """(effects, problems) for the entry as it stands — nothing is written.

    `lines`: [{"part", "qty", "order_id"?}]. `holdings`: {(part, holder):
    qty}. `open_orders`: {part: [{"id", "ordered", "received"}]}. Every
    problem is a sentence someone can act on; an empty list means the
    entry can be recorded.
    """
    problems: List[str] = []
    effects: List[dict] = []
    if not str(from_name or "").strip():
        problems.append("Say where the parts are coming from.")
    if not str(to_name or "").strip():
        problems.append("Say where they are going.")
    if kind == "same":
        problems.append("From and To are the same place — nothing would "
                        "move.")
    elif kind == "invalid":
        problems.append("A supplier's goods have to arrive with someone "
                        "first — put a person or place in To.")
    if not lines:
        problems.append("Add at least one part.")
    if kind == "scrap" and not str(note or "").strip():
        problems.append("Say why they are being written off, in the note.")

    for line in lines:
        part = str(line.get("part", "")).strip()
        qty = to_qty(line.get("qty"))
        effect = {"part": part, "qty": qty, "order": None, "problem": ""}
        if qty <= 0:
            effect["problem"] = ("How many %s? Give a number above zero."
                                 % part)
        if kind in ("move", "scrap", "return"):
            have = int(holdings.get((part, from_name), 0))
            effect["from_before"], effect["from_after"] = have, have - qty
            if kind == "move":
                there = int(holdings.get((part, to_name), 0))
                effect["to_before"], effect["to_after"] = there, there + qty
            if qty > have and not effect["problem"]:
                effect["problem"] = (
                    "%s holds %d of %s — %d cannot leave. If the count is "
                    "behind, record the arrival first."
                    % (from_name, have, part, qty))
        elif kind == "receipt":
            there = int(holdings.get((part, to_name), 0))
            effect["to_before"], effect["to_after"] = there, there + qty
            candidates = open_orders.get(part, [])
            chosen = str(line.get("order_id", "") or "").strip()
            order = next((o for o in candidates if o["id"] == chosen), None)
            if order is None and len(candidates) == 1:
                order = candidates[0]
            if order is None and len(candidates) > 1 and not effect["problem"]:
                effect["problem"] = ("%s has %d open orders — say which one "
                                     "these belong to." % (part,
                                                           len(candidates)))
            if order is not None:
                after = order["received"] + qty
                effect["order"] = {"id": order["id"],
                                   "ordered": order["ordered"],
                                   "after": after}
                left = order["ordered"] - order["received"]
                if (order["ordered"] and qty > left
                        and not effect["problem"]):
                    effect["problem"] = (
                        "Only %d of %s are still on order %s — %d cannot "
                        "arrive against it." % (left, part, order["id"], qty))
        if effect["problem"]:
            problems.append(effect["problem"])
        effects.append(effect)
    return effects, problems


def resolve_orders(lines: List[dict],
                   open_orders: Dict[str, List[dict]]) -> List[dict]:
    """Goods arriving land against the part's open order without anyone
    opening it first: exactly one open order IS the answer, so it is filled
    in. Several stay a question for the person (plan() asks it); none means
    stock arriving with no order behind it."""
    out = []
    for line in lines:
        line = dict(line)
        candidates = open_orders.get(str(line.get("part", "")).strip(), [])
        ids = [o["id"] for o in candidates]
        if str(line.get("order_id", "") or "").strip() not in ids:
            line["order_id"] = ids[0] if len(ids) == 1 else ""
        out.append(line)
    return out


def waits_for_tick(kind: str, tracking: str) -> bool:
    """The middle version's one label: a MOVE with a tracking number reads
    as on its way until ticked. Counts never wait."""
    return kind == "move" and bool(str(tracking or "").strip())


def boxes_on_the_way(parts: Dict[str, dict]) -> List[dict]:
    """Tracked moves nobody has ticked yet, grouped back into the boxes they
    travelled in. Read from the part ledgers — `parts` is
    parts_tracker.fetch_all_parts(record): a `Movement` row with tracking
    text, written by the app, with no later `Arrived` row naming the same
    tracking text and destination."""
    from utils.tracker_parse import event_of, holder_of, place_of

    boxes: Dict[tuple, dict] = {}
    for code, part in parts.items():
        history = part.get("history", [])
        for i, row in enumerate(history):
            tracking = str(row.get("courier", "") or "").strip()
            if (event_of(row).lower() != EVENT_OF["move"].lower()
                    or not tracking
                    or not str(row.get("logged_by", "") or "").strip()):
                continue
            dest = holder_of(row)
            ticked = any(
                event_of(later).lower() == TICK_EVENT.lower()
                and str(later.get("courier", "") or "").strip().lower()
                == tracking.lower()
                and holder_of(later).strip().lower() == dest.strip().lower()
                for later in history[i + 1:])
            if ticked:
                continue
            key = (tracking.lower(), place_of(row).strip().lower(),
                   dest.strip().lower(), str(row.get("date", "")).strip())
            box = boxes.setdefault(key, {
                "tracking": tracking, "from": place_of(row), "to": dest,
                "date": str(row.get("date", "")).strip(), "parts": []})
            box["parts"].append({"part": code,
                                 "qty": to_qty(row.get("qty_moved"))})
    return list(boxes.values())


# --- writers ----------------------------------------------------------------
def _stamp(user: dict) -> Tuple[str, str]:
    who = user.get("email", "") or user.get("name", "")
    return who, datetime.now().strftime("%d %b %Y %H:%M")


def commit(kind: str, lines: List[dict], from_name: str, to_name: str, *,
           date, tracking: str, note: str, user: dict, project: str,
           record_id: str, names: Dict[str, dict],
           orders_by_id: Dict[str, dict]) -> Tuple[List[str], List[str]]:
    """Write the entry: ledger first, then the movement and the count — the
    one direction every writer in the app follows. Returns (done, failed)
    as part codes; a failure carries its reason after a colon.

    `names`: {part: {"part_name", "category"}} for the movement row.
    `orders_by_id`: the central Orders rows a receipt may land against.
    """
    from utils import (parts_tracker, stock_store, tracker_orders,
                       tracker_writer)
    from utils.google_client import get_gspread_client
    from utils.orders_store import update_order

    who, logged_at = _stamp(user)
    day = date.strftime("%d %b %Y")
    tracking = str(tracking or "").strip()
    note = str(note or "").strip()
    done: List[str] = []
    failed: List[str] = []

    for line in lines:
        part, qty = str(line["part"]).strip(), to_qty(line.get("qty"))
        ident = names.get(part, {})
        order = orders_by_id.get(str(line.get("order_id", "") or "").strip())
        if kind == "receipt" and order is not None:
            ok, message = tracker_writer.write_receipt(
                part, order_id=str(order.get("OrderID", "")).strip(),
                qty_ordered=str(order.get("Quantity", "")),
                qty_received=str(qty), received_from=from_name,
                holder=to_name, courier=tracking, date=day,
                version=order.get("Version", ""), eta=order.get("ETA", ""),
                note=note or "received %s" % day, logged_by=who,
                logged_at=logged_at, sheet_id=record_id)
        else:
            fields = {
                "event": ("Receipt" if kind == "receipt"
                          else EVENT_OF[kind]),
                "date": day, "order_id": "", "place": from_name,
                "holder": "" if kind == "scrap" else to_name,
                "courier": tracking, "selected": "FALSE",
                "logged_by": who, "logged_at": logged_at, "notes": note,
            }
            fields["qty_received" if kind == "receipt"
                   else "qty_moved"] = str(qty)
            fields["type"] = fields["event"]
            ok, message = tracker_writer.append_history(
                part, fields, sheet_id=record_id)
        if not ok:
            failed.append("%s: %s" % (part, message))
            continue
        res = stock_store.record_movement(
            part, project, qty, "" if kind == "scrap" else to_name,
            from_name,
            event="Receipt" if kind == "receipt" else EVENT_OF[kind],
            description=ident.get("part_name", ""),
            part_type=ident.get("category", ""), notes=note,
            courier=tracking,
            build=(order or {}).get("Version", "") if kind == "receipt"
            else "",
            date=day, logged_by=who)
        if res.get("ok"):
            done.append(part)
        else:
            failed.append("%s: written to the ledger, but NOT counted — %s"
                          % (part, res.get("problem", "unknown")))

    parts_tracker.refresh(record_id)
    if kind == "receipt":
        # The same write-back Process Order makes: delivered only when
        # delivered IN FULL; the tracking number rides along.
        client = get_gspread_client()
        fresh = parts_tracker.fetch_all_parts(record_id)
        for line in lines:
            order = orders_by_id.get(
                str(line.get("order_id", "") or "").strip())
            if order is None or client is None:
                continue
            oid = str(order.get("OrderID", "")).strip()
            history = fresh.get(str(line["part"]).strip(), {}).get(
                "history", [])
            ordered, received = tracker_orders.order_progress(history, oid)
            updates = {}
            if tracking:
                updates["TrackingNum"] = tracking
            if received and (received >= ordered or ordered == 0):
                updates["Status"] = "delivered"
            if updates:
                update_order(client, oid, updates)
    return done, failed


def tick_arrived(box: dict, *, user: dict, record_id: str
                 ) -> Tuple[List[str], List[str]]:
    """The tick: one `Arrived` row per part in the box. It names the
    tracking text and the destination so the reader can pair it, carries no
    quantity column, and moves no count — the counts moved when the box was
    recorded."""
    from utils import parts_tracker, tracker_writer

    who, logged_at = _stamp(user)
    done: List[str] = []
    failed: List[str] = []
    for item in box.get("parts", []):
        ok, message = tracker_writer.append_history(item["part"], {
            "event": TICK_EVENT,
            "date": datetime.now().strftime("%d %b %Y"),
            "order_id": "", "place": box.get("from", ""),
            "holder": box.get("to", ""), "courier": box.get("tracking", ""),
            "selected": "FALSE", "logged_by": who, "logged_at": logged_at,
            "notes": "arrived — %s pcs sent %s" % (item.get("qty", "?"),
                                                  box.get("date", "")),
            "type": TICK_EVENT,
        }, sheet_id=record_id)
        (done if ok else failed).append(
            item["part"] if ok else "%s: %s" % (item["part"], message))
    parts_tracker.refresh(record_id)
    return done, failed


def add_note(part: str, kind: str, text: str, verdict: str = "", *,
             user: dict, record_id: str) -> Tuple[bool, str]:
    """A note on a part — `Update`, `QC` or `Hold`. No count, no order."""
    from utils import parts_tracker, tracker_writer

    who, logged_at = _stamp(user)
    ok, message = tracker_writer.append_history(part, {
        "event": kind, "date": datetime.now().strftime("%d %b %Y"),
        "order_id": "", "status": verdict if kind == "QC" else "",
        "selected": "FALSE", "logged_by": who, "logged_at": logged_at,
        "notes": str(text or "").strip(), "type": kind,
    }, sheet_id=record_id)
    parts_tracker.refresh(record_id)
    return ok, message
