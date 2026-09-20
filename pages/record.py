"""Record — one screen for everything that happens to parts.

The branch experiment (Hamid, 19 Sep 2026): Joe's review asked for "its own
form with a dropdown to select the component... all from a single screen"
and for entries stripped back to "movement from one place to another".
This page is that, living BESIDE the current entry forms (Process Order and
Part Detail keep theirs untouched) so the two can be compared on the same
data before anything is retired.

Two things can be done here: move parts, or add a note. Nobody picks a kind
of entry — From and To decide it (utils/record_writer has the rules and the
writers, which are the same sheet rows the current forms write). Goods on
the move follow the "middle version": both counts change the moment a move
is recorded, and a move with a tracking number reads as on its way until
someone ticks that it arrived.
"""
import re
from datetime import datetime

import streamlit as st

from utils import (holders_store, movements_store, parts_tracker,
                   project_registry, record_builder, record_writer,
                   tracker_orders, ui)
from utils.auth import require_role
from utils.orders_store import fetch_all_orders

user = require_role("admin", "engineer", "logistics")

st.title("📝 Record (beta)")
project = ui.require_single_project("Record")
record_id = project_registry.tracker_sheet(project)
ui.project_scope("Everything recorded here is filed to this project.")
# Live beside the current forms since 20 Sep (Hamid: "yes go with option 1")
# — two ways to record the same shipment is the one real hazard of that, so
# the page says so before anything else.
st.info("**Trial page.** For any one shipment, use this page **or** the "
        "Ship entry on Process Order — not both.")

_flash = st.session_state.pop("rec_flash", None)
if _flash:
    {"success": st.success, "warning": st.warning}.get(
        _flash[0], st.info)(_flash[1])
    try:
        st.toast(_flash[1])
    except Exception:
        pass

# --- what the tracker knows about this project ------------------------------
parts = parts_tracker.fetch_all_parts(record_id)
names = {str(r.get("mcode", "")).strip(): r
         for r in parts_tracker.fetch_overview(record_id)
         if str(r.get("mcode", "")).strip()}
codes = sorted(parts, key=lambda c: [int(t) if t.isdigit() else t.lower()
                                     for t in re.split(r"(\d+)", c)])
holdings = movements_store.holdings(record_id)


def holds_stock(name: str) -> bool:
    if not name or name == record_writer.SINK:
        return False
    return holders_store.get(name).get("kind", "person").lower() != "source"


def label(code: str) -> str:
    name = str(names.get(code, {}).get("part_name", "")).strip()
    return "%s · %s" % (code, name) if name else code


# The part's open orders, by the RECONCILED status every page uses, with
# how far each has got — what goods arriving land against.
_derived = {str(t.get("order_id", "")).strip(): t.get("derived", "")
            for t in tracker_orders.all_projects_orders()
            if str(t.get("order_id", "")).strip()}
orders_by_id, open_orders = {}, {}
for _o in fetch_all_orders():
    if str(_o.get("Project", "")).strip() != project:
        continue
    _oid = str(_o.get("OrderID", "")).strip()
    _code = str(_o.get("PartID", "")).strip()
    if tracker_orders.effective_status(
            (_o.get("Status") or "new").strip() or "new",
            _derived.get(_oid, "")) in ("delivered", "cancelled"):
        continue
    _ordered, _received = tracker_orders.order_progress(
        parts.get(_code, {}).get("history", []), _oid)
    orders_by_id[_oid] = _o
    open_orders.setdefault(_code, []).append({
        "id": _oid, "received": _received,
        "ordered": _ordered or record_writer.to_qty(_o.get("Quantity"))})

_directory = holders_store.names()
_keepers = [n for n in _directory if holds_stock(n)]
_suppliers = [n for n in _directory if not holds_stock(n)]
_with_stock = sorted({h for (_p, h), q in holdings.items() if q > 0})
_me = next((h for h in _with_stock if tracker_orders.holder_matches(
    user.get("name", ""), h, user.get("email", ""))), "")

# Every widget key carries the epoch: a recorded entry bumps it and the
# whole form comes back clean (a key of its own, outside any shared prefix).
_e = st.session_state.get("rec_epoch", 0)


def _recorded(kind: str, text: str) -> None:
    st.session_state["rec_flash"] = (kind, text)
    st.session_state["rec_epoch"] = _e + 1
    st.session_state.pop("ofb_working", None)
    st.rerun()


mode = st.radio("What do you want to record?", ["Move parts", "Add a note"],
                horizontal=True, label_visibility="collapsed",
                key="rec_mode")

# =============================================================================
if mode == "Move parts":
    c1, c2 = st.columns(2)
    with c1:
        _from_opts = [""] + _keepers + _suppliers
        h_from = st.selectbox(
            "From", _from_opts,
            index=_from_opts.index(_me) if _me in _from_opts else 0,
            format_func=lambda n: (("%s · supplier" % n) if n in _suppliers
                                   else n) or "Choose…",
            key="rec_from_%d" % _e)
    with c2:
        h_to = st.selectbox(
            "To", [""] + _keepers + [record_writer.SINK] + _suppliers,
            format_func=lambda n: (("%s · supplier (going back)" % n)
                                   if n in _suppliers else n) or "Choose…",
            key="rec_to_%d" % _e)
    kind = record_writer.kind_of(h_from, h_to, holds_stock)

    # What can be picked depends on who is sending: a holder sends what
    # they hold; a supplier sends what is on order (anything else is still
    # allowed — stock does arrive without an order behind it).
    #
    # A stock-holding VENDOR is both: the assembly factory hands over what
    # it holds AND delivers what it made. No order names its vendor, so the
    # parts it could be delivering — on order, none held by it — are
    # offered too, and the person says which it is with a tick (below).
    _maker = (holders_store.get(h_from).get("kind", "").lower() == "vendor"
              if h_from else False)

    def _could_arrive(code: str) -> bool:
        return (_maker and code in open_orders
                and holdings.get((code, h_from), 0) <= 0)

    if holds_stock(h_from):
        _options = ([c for c in codes if holdings.get((c, h_from), 0) > 0]
                    + [c for c in codes if _could_arrive(c)])
    else:
        _options = ([c for c in codes if c in open_orders]
                    + [c for c in codes if c not in open_orders])

    def _hint(code: str) -> str:
        if _could_arrive(code):
            return "on order — %s holds none" % h_from
        if holds_stock(h_from):
            return "%s holds %d" % (h_from, holdings.get((code, h_from), 0))
        if h_from and code in open_orders:
            left = sum(max(o["ordered"] - o["received"], 0)
                       for o in open_orders[code])
            return "%d still on order" % left
        return ""

    picked = st.multiselect(
        "Parts in this move", _options,
        format_func=lambda c: label(c) + (" — " + _hint(c) if _hint(c)
                                          else ""),
        placeholder="Add the parts — a box with ten is still one entry",
        key="rec_parts_%d_%s" % (_e, h_from))
    if holds_stock(h_from) and not _options:
        st.caption("The count shows **%s** holding nothing in this project "
                   "— if that is behind, record the arrival first."
                   % h_from)

    lines = []
    for code in picked:
        # The one thing From and To cannot say: a vendor's own make arriving
        # on its order. Offered only where it can be true, and never
        # assumed — a holder with none is as often a count that is behind.
        _offer = kind == "move" and _could_arrive(code)
        arriving = bool(_offer and st.session_state.get(
            "rec_arr_%d_%s_%s" % (_e, h_from, code)))
        _cands = (open_orders.get(code, [])
                  if kind == "receipt" or arriving else [])
        q1, q2, q3 = st.columns([3, 1.2, 2.2], vertical_alignment="center")
        with q1:
            st.markdown("**%s**  \n:gray[%s]" % (label(code), _hint(code)))
        with q2:
            _left = (max(_cands[0]["ordered"] - _cands[0]["received"], 0)
                     if len(_cands) == 1 else None)
            qty = st.number_input(
                "How many %s" % code, min_value=0, step=1,
                value=_left or None, label_visibility="collapsed",
                placeholder="Qty", key="rec_qty_%d_%s_%s" % (_e, h_from, code))
        with q3:
            order_id = ""
            if _offer:
                st.checkbox("Arriving on the order",
                            key="rec_arr_%d_%s_%s" % (_e, h_from, code),
                            help="%s made these and is delivering them "
                                 "against the part's order — not handing "
                                 "over stock it held." % h_from)
            if len(_cands) > 1:
                order_id = st.selectbox(
                    "Which order (%s)" % code,
                    [""] + [o["id"] for o in _cands],
                    format_func=lambda i: next(
                        ("%s — %d of %d in" % (o["id"], o["received"],
                                               o["ordered"])
                         for o in _cands if o["id"] == i), "Which order?"),
                    label_visibility="collapsed",
                    key="rec_ord_%d_%s" % (_e, code))
        lines.append({"part": code, "qty": qty or 0, "order_id": order_id,
                      "arriving": arriving, "can_arrive": _offer})
    lines = record_writer.resolve_orders(lines, open_orders)

    d1, d2 = st.columns(2)
    with d1:
        date = st.date_input("Date", value=datetime.now().date(),
                             key="rec_date_%d" % _e)
    with d2:
        tracking = st.text_input("Tracking number (if it has one)",
                                 key="rec_trk_%d" % _e)
    note = st.text_input("Note (optional)", key="rec_note_%d" % _e)

    effects, problems = record_writer.plan(
        kind, lines, h_from, h_to, holdings, open_orders, note)

    # --- the slip: what this entry will do, before it is saved ------------
    with st.container(border=True):
        _counted = [fx for fx in effects if fx["qty"]]
        _all_arriving = bool(_counted) and all(
            fx.get("arriving") for fx in _counted)
        # The header names what the ENTRY is; a box whose every line is a
        # vendor's own make arriving is goods arriving, not a move.
        _word = record_writer.WORDS.get(
            "receipt" if kind == "move" and _all_arriving else kind, "")
        st.markdown("%s**%s → %s** · %s" % (
            ("`%s` · " % _word) if _word else "",
            h_from or "From…", h_to or "To…", date.strftime("%d %b %Y")))
        for fx in effects:
            if not fx["qty"]:
                continue
            bits = ["`%s` ×%d" % (fx["part"], fx["qty"])]
            if fx.get("arriving"):
                bits.append("arriving, made by %s" % h_from)
            if fx.get("order"):
                o = fx["order"]
                bits.append("order %s: %d of %d%s" % (
                    o["id"], o["after"], o["ordered"],
                    " — complete" if o["after"] == o["ordered"]
                    else (" — %d still to come" % (o["ordered"] - o["after"])
                          if o["after"] < o["ordered"] else "")))
            elif kind == "receipt" or fx.get("arriving"):
                bits.append("no open order — counted as stock arriving")
            if "from_before" in fx:
                bits.append("%s %d → %d" % (h_from, fx["from_before"],
                                           fx["from_after"]))
            if "to_before" in fx:
                bits.append("%s %d → %d" % (h_to, fx["to_before"],
                                           fx["to_after"]))
            st.markdown(" · ".join(bits))
        if kind == "move" and _all_arriving:
            st.caption("Arriving against the order — only %s's count "
                       "changes; %s is delivering what it made, not stock "
                       "it held." % (h_to, h_from))
        elif kind == "move":
            st.caption(
                "Both counts change now. Because it has a tracking number it "
                "shows as **on its way** until someone ticks that it arrived."
                if record_writer.waits_for_tick(kind, tracking) else
                "Both counts change now. No tracking number, so it reads as "
                "handed over — done the moment it is recorded.")
        elif kind == "scrap":
            st.caption("Leaves %s's count and goes nowhere. The entry stays "
                       "on the record." % h_from)
        elif kind == "return":
            st.caption("Leaves %s's count. Suppliers do not carry a count."
                       % h_from)
        elif kind == "receipt":
            st.caption("The order is matched for you — nobody has to open "
                       "it first. A part-delivery keeps it open.")
        else:
            st.caption("Choose From, To and the parts — this box spells out "
                       "what the entry will do before you save it.")

    if st.button("Record it", type="primary", key="rec_go_%d" % _e):
        if problems:
            for p in problems:
                st.error(p)
            st.stop()
        done, failed = record_writer.commit(
            kind, lines, h_from, h_to, date=date, tracking=tracking,
            note=note, user=user, project=project, record_id=record_id,
            names=names, orders_by_id=orders_by_id)
        ov = record_builder.write_overview(
            project, user.get("email", "") or user.get("name", ""),
            sheet_id=record_id, replace=True)
        _tail = ("" if not failed else " — NOT recorded: " + "; ".join(failed))
        if ov.get("problem"):
            _tail += " (Overview not refreshed: %s)" % ov["problem"]
        _recorded("warning" if failed else "success",
                  "Recorded %d part(s), %s → %s.%s"
                  % (len(done), h_from, h_to, _tail))

# =============================================================================
else:
    n1, n2 = st.columns([2, 3])
    with n1:
        n_part = st.selectbox("Part", codes, format_func=label,
                              key="rec_npart_%d" % _e)
    with n2:
        n_kind = st.radio("Kind of note", ["Update", "QC result", "On hold"],
                          horizontal=True, key="rec_nkind_%d" % _e)
    verdict = ""
    if n_kind == "QC result":
        verdict = st.selectbox("Result", ["Pass", "Fail", "Partial"],
                               key="rec_nverdict_%d" % _e)
    n_text = st.text_area("What should people know?", key="rec_ntext_%d" % _e)
    st.caption("A note changes no count. It appears in the part's history.")
    if st.button("Add the note", type="primary", key="rec_ngo_%d" % _e):
        if not n_part:
            st.error("Which part is the note about?")
            st.stop()
        if n_kind != "QC result" and not n_text.strip():
            st.error("Write the note — it is the whole entry.")
            st.stop()
        ok, message = record_writer.add_note(
            n_part, {"Update": "Update", "QC result": "QC",
                     "On hold": "Hold"}[n_kind], n_text, verdict,
            user=user, record_id=record_id)
        if not ok:
            st.error(message)
            st.stop()
        _recorded("success", "Note added to %s." % n_part)

# --- on its way: tracked moves waiting for their tick -----------------------
st.markdown("---")
boxes = record_writer.boxes_on_the_way(parts)
st.subheader("📬 On its way (%d)" % len(boxes))
if not boxes:
    st.caption("Nothing is waiting. A move recorded with a tracking number "
               "sits here until someone ticks that it arrived — the counts "
               "have already moved, so a forgotten tick never leaves one "
               "wrong.")
for _i, box in enumerate(boxes):
    b1, b2 = st.columns([5, 1.4], vertical_alignment="center")
    with b1:
        st.markdown("**%s → %s** · since %s · `%s`  \n%s" % (
            box["from"], box["to"], box["date"] or "?", box["tracking"],
            ", ".join("`%s` ×%d" % (x["part"], x["qty"])
                      for x in box["parts"])))
    with b2:
        if st.button("✓ It arrived", key="rec_tick_%d_%d" % (_e, _i),
                     use_container_width=True):
            done, failed = record_writer.tick_arrived(
                box, user=user, record_id=record_id)
            _recorded("warning" if failed else "success",
                      "Ticked — the box is at %s.%s" % (
                          box["to"], "" if not failed else
                          " NOT ticked: " + "; ".join(failed)))

st.caption("This screen is being tried beside the current forms — Process "
           "Order and the part pages still record the way they always did, "
           "on the same data.")
