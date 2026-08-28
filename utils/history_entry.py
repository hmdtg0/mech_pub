"""One room, two doors — the part ledger and THE entry point.

Process Order's opened order and Part Detail grew into near-twins that
drifted: two history tables with different columns, and Part Detail kept
two pre-app write forms ("Report location", "Hand over") that reached the
same ledger by older doors, without the entry form's guards. Hamid, 28 Aug:
"I want to unify them, the reference is '📜 Add history entry'. always.
this will be our one and only entry point to update the orderes."

So both pages draw THIS module: `history_table` is the one ledger shape,
`render_entry` is the one entry point — the exact section built on Process
Order through the 28 Aug simplifications (Receipt IS the full receive;
From/To are directory pickers with a type-a-name that registers first;
the event table decides what counts). Process Order passes its opened
order; Part Detail passes none and the module resolves the part's open
order so a Receipt or Costs still lands where it belongs.
"""
from datetime import datetime

import streamlit as st

import config
from config import CNY_TO_GBP
from utils import (holders_store, parts_tracker, record_builder,
                   stock_store, tracker_orders, tracker_writer, ui)
from utils.google_client import get_gspread_client
from utils.orders_store import fetch_orders_for_part, update_order
from utils.tracker_parse import holder_of, is_selected, place_of, to_int


def _flash_key(ns: str) -> str:
    return "he_flash_%s" % ns


def flash(ns: str, kind: str, text: str) -> None:
    """Say something that has to survive `st.rerun()` — rendered at the
    top of this namespace's entry section on the next run."""
    st.session_state[_flash_key(ns)] = (kind, text)


def history_table(mcode: str, record_id: str) -> None:
    """The part's ledger, ONE shape everywhere — both doors draw this."""
    if not (record_id and mcode):
        return
    _hist = parts_tracker.fetch_all_parts(record_id).get(mcode, {}).get(
        "history", [])
    st.subheader("📜 %s history (%d)" % (mcode, len(_hist)))
    if not _hist:
        st.caption("No history rows on this part's tab yet — the entries "
                   "you add below become its first.")
        return
    ui.native_table(
        ["Date", "Event", "Order / Sample ID", "Version", "Build",
         "Qty ordered", "Qty moved", "Qty received", "From", "To",
         "Vendor / Source", "ETA", "QC", "Courier / Tracking", "Selected",
         "Notes"],
        [[r.get("date", ""), r.get("event", "") or r.get("type", ""),
          r.get("order_id", ""), r.get("version", ""), r.get("build", ""),
          r.get("qty_ordered", ""), r.get("qty_moved", ""),
          r.get("qty_received", ""), place_of(r), holder_of(r),
          r.get("vendor", ""), r.get("eta", ""), r.get("status", ""),
          r.get("courier", ""), "✅" if is_selected(r) else "",
          r.get("notes", "")] for r in _hist])


def open_orders(orders, derived_of: dict) -> list:
    """The orders an entry can still land against — RECONCILED status not
    delivered/cancelled (the ledger moves an order forward past a lagging
    Orders-tab cell, the same rule as every page)."""
    out = []
    for o in orders:
        oid = str(o.get("OrderID", "")).strip()
        eff = tracker_orders.effective_status(
            (o.get("Status") or "new").strip() or "new",
            derived_of.get(oid, ""))
        if eff not in ("delivered", "cancelled"):
            out.append(o)
    return out


def render_entry(user, mcode: str, record_id: str, project: str,
                 order: dict = None, key_ns: str = "",
                 part_name: str = "", part_type: str = "",
                 version: str = "") -> None:
    """📜 Add history entry — the one and only entry point (Hamid, 28 Aug).

    `order` is the central Orders row the entry belongs to. Process Order
    passes its opened order; Part Detail passes None and the part's OPEN
    order is resolved here (one → used, several → a picker, none → the
    entry is part-level and Receipt/Costs are refused/hidden, because a
    receive must land against an order or Parts Short lies).
    """
    ns = key_ns or (str(order.get("OrderID", "")).strip() if order else mcode)
    _f = st.session_state.pop(_flash_key(ns), None)
    if _f:
        {"success": st.success, "warning": st.warning,
         "error": st.error}.get(_f[0], st.info)(_f[1])

    st.subheader("📜 Add history entry")

    client = get_gspread_client()
    if order is None and mcode:
        _cands = fetch_orders_for_part(mcode, part_name, project=project)
        _derived = {}
        for _t in tracker_orders.all_projects_orders():
            _oid = str(_t.get("order_id", "")).strip()
            if _oid:
                _derived[_oid] = _t.get("derived", "")
        _open = open_orders(_cands, _derived)
        if len(_open) == 1:
            order = _open[0]
            st.caption("Entries land on this part's open order "
                       "**%s** (qty %s)." % (order.get("OrderID", "?"),
                                             order.get("Quantity", "?")))
        elif len(_open) > 1:
            _labels = {}
            for o in _open:
                _labels["%s — qty %s, %s" % (
                    o.get("OrderID", "?"), o.get("Quantity", "?"),
                    o.get("CreatedAt", ""))] = o
            _sel = st.selectbox(
                "This entry belongs to order", ["(no specific order)"]
                + sorted(_labels), key="he_ord_%s" % ns,
                help="The part has more than one open order — a Receipt "
                     "or Costs needs to know which.")
            order = _labels.get(_sel)
        else:
            st.caption("No open order on this part — entries are recorded "
                       "part-level. A **Receipt** or **Costs** needs an "
                       "open order (raise one on **Order from BOM**).")
    order_id = str(order.get("OrderID", "")).strip() if order else ""

    _entry_kind = st.radio(
        "What are you recording?",
        ["📜 History entry"] + (["💰 Costs"] if order else []),
        horizontal=True, key="he_kind_%s" % ns, label_visibility="collapsed")

    if _entry_kind.endswith("History entry"):
        st.caption("Appends one line to **%s**'s history in the project "
                   "record — the same ledger the board, Parts, Part Detail "
                   "and Movements read. A **Receipt** is the full receive: "
                   "it writes the paired receive line, counts the stock, "
                   "and marks the order DELIVERED — quantity and vendor "
                   "default from the order when left blank."
                   % (mcode or "the part"))
        # The whole vocabulary, straight from the event table — not a second
        # list typed out here (19 Aug: the old hardcoded pair got out of
        # step with it).
        HISTORY_EVENTS = list(config.EVENT_CHOICES)

        if not record_id or not mcode:
            st.info("Needs a registered project record and a Part ID.")
            return
        with st.form("history_form_%s" % (order_id or ns)):
            h1, h2, h3 = st.columns(3)
            with h1:
                h_event = st.selectbox("Event", HISTORY_EVENTS)
                h_date = st.date_input("Date", value=datetime.now().date())
                h_eta = st.date_input("New ETA (optional)", value=None,
                                      help="Lands in the row's ETA column — "
                                           "the order's shown ETA follows "
                                           "the latest history row that "
                                           "carries one.")
            with h2:
                # Directory pickers with a type-a-name escape (Hamid, 28 Aug
                # interview). A TYPED name is registered in the Holders
                # directory BEFORE the entry is written — From as a "source"
                # (a vendor; never counted negative), To as a "person".
                _holders = holders_store.names()
                h_from_pick = st.selectbox(
                    "From", [""] + _holders,
                    format_func=lambda h: h or "—")
                h_from_typed = st.text_input(
                    "…or type a vendor / outside source",
                    help="A new name is saved to the Holders directory as "
                         "a 'source' before the entry is recorded.")
                h_to_pick = st.selectbox(
                    "To", [""] + _holders,
                    format_func=lambda h: h or "—")
                h_to_typed = st.text_input(
                    "…or type a new name",
                    help="A new name is saved to the Holders directory as "
                         "a 'person' before the entry is recorded.")
                h_courier = st.text_input("Courier / Tracking", value="")
            with h3:
                h_qty_ordered = st.text_input("Qty ordered", value="")
                h_qty_moved = st.text_input("Qty moved", value="")
                h_qty_received = st.text_input("Qty received", value="")
            h4, h5, h6 = st.columns(3)
            with h4:
                h_qc = st.text_input("QC Pass?",
                                     placeholder="e.g. Pass / FAILED QC")
            with h5:
                h_build = st.text_input("Build", value="")
            with h6:
                h_lead = st.text_input("Lead time (days)", value="")
            h_notes = st.text_input("Notes")
            h_selected = st.checkbox("Selected for MP?", value=False)
            add_entry = st.form_submit_button("➕ Append to part history",
                                              type="primary")
        if not add_entry:
            return
        h_from = (h_from_typed.strip() or h_from_pick).strip()
        h_to = (h_to_typed.strip() or h_to_pick).strip()
        # Only a STOCK-MOVING entry has to name somebody — a Cancelled,
        # QC, Update or Hold line legitimately names nobody (28 Aug; the
        # old blanket guard would have blocked every cancellation).
        if config.moves_stock(h_event) and not h_to and not h_from:
            st.error("From or To is needed — a stock-moving entry that "
                     "names nobody records nothing.")
            st.stop()
        # A typed name reaches the directory FIRST (Hamid: "make sure the
        # vendor will be recorded ... before user submits it") — if that
        # write fails, no entry is recorded at all.
        _reg = ""
        if h_from and not holders_store.is_known(h_from):
            _reg = holders_store.register(
                h_from, kind="source", notes="added from Add history entry")
        if not _reg and h_to and not holders_store.is_known(h_to):
            _reg = holders_store.register(
                h_to, kind="person", notes="added from Add history entry")
        if _reg:
            st.error("Not recorded — the new name could not be saved to "
                     "the Holders directory first: %s" % _reg)
            st.stop()
        now = datetime.now()
        if h_event == "Receipt":
            # A Receipt IS the full receive (Hamid, 28 Aug: "add recievd
            # goods to entry items no need for extra tab"): the paired
            # receive line, the movement log + count, Status=delivered
            # and the tracking sync — its prefills are submit-time
            # defaults from the order (quantity; the vendor as From).
            if not order:
                st.error("A Receipt books goods against an order — none "
                         "is open (or picked) for this part. Raise one on "
                         "**Order from BOM** first.")
                st.stop()
            if not h_to:
                st.error("Received by is needed — pick who holds the "
                         "parts now in **To**.")
                st.stop()
            _qty_rec = to_int(h_qty_received)
            if not _qty_rec:
                try:
                    _qty_rec = int(float(order.get("Quantity", 1) or 1))
                except (TypeError, ValueError):
                    _qty_rec = 1
            _recv_from = h_from or str(order.get("Vendor", "")).strip()
            _note = (h_notes.strip()
                     or "received %s" % h_date.strftime("%d %b %Y"))
            ok, message = tracker_writer.write_receipt(
                mcode, order_id=order_id,
                qty_ordered=str(order.get("Quantity", "")),
                qty_received=str(_qty_rec),
                received_from=_recv_from,
                holder=h_to,
                courier=h_courier.strip(),
                date=h_date.strftime("%d %b %Y"),
                version=order.get("Version", ""),
                eta=order.get("ETA", ""),
                note=_note,
                logged_by=user.get("email", "") or user.get("name", ""),
                logged_at=now.strftime("%d %b %Y %H:%M"),
                sheet_id=record_id)
            if ok:
                # Goods arriving ARE stock arriving — one call writes the
                # merged movement log and the count.
                stock_note = ""
                if _qty_rec > 0:
                    res = stock_store.record_movement(
                        mcode, project, _qty_rec,
                        h_to, _recv_from, event="Receipt",
                        description=order.get("PartName", "") or part_name,
                        part_type=order.get("Process", "") or part_type,
                        notes=_note, courier=h_courier.strip(),
                        build=order.get("Version", ""),
                        date=h_date.strftime("%d %b %Y"),
                        logged_by=user.get("email", "")
                        or user.get("name", ""))
                    stock_note = ("" if res.get("ok") else
                                  " The stock count was NOT updated: %s"
                                  % res.get("problem", "unknown error"))
                if client:
                    updates = {"Status": "delivered"}
                    if h_courier.strip():
                        updates["TrackingNum"] = h_courier.strip()
                    update_order(client, order_id, updates)
                parts_tracker.refresh(record_id)
                # The Overview is derived — recompute it so the receipt
                # shows everywhere immediately, not only on the part tab.
                ov = record_builder.write_overview(
                    project,
                    user.get("email", "") or user.get("name", ""),
                    sheet_id=record_id, replace=True)
                if ov.get("problem"):
                    st.warning("Overview not refreshed: %s" % ov["problem"])
                flash(ns, "warning" if stock_note else "success",
                      message + stock_note)
                st.rerun()
            else:
                st.error(message)
            st.stop()
        else:   # every other event: the generic ledger append
            ok, message = tracker_writer.append_history(mcode, {
                "event": h_event,
                "date": h_date.strftime("%d %b %Y"),
                "order_id": order_id,
                "version": (order or {}).get("Version", "") or version,
                "build": h_build.strip(),
                "qty_ordered": h_qty_ordered.strip(),
                "qty_moved": h_qty_moved.strip(),
                "qty_received": h_qty_received.strip(),
                "place": h_from.strip(),
                "holder": h_to.strip(),
                "eta": h_eta.strftime("%d %b %Y") if h_eta else "",
                "lead_time": h_lead.strip(),
                "status": h_qc.strip(),
                "courier": h_courier.strip(),
                "selected": "TRUE" if h_selected else "FALSE",
                "logged_by": user.get("email", "") or user.get("name", ""),
                "logged_at": now.strftime("%d %b %Y %H:%M"),
                "notes": h_notes.strip(),
                # The pilot ledger kept the kind of row in `Type`.
                "type": h_event,
            }, sheet_id=record_id)
            # Goods on the move go through stock_store.record_movement —
            # the ONE writer for the merged movement log and the count —
            # so a moves-stock event with a quantity reaches both. This is
            # what made the old separate movement, handover and
            # location-report forms redundant (28 Aug).
            stock_note = ""
            h_qty = to_int(h_qty_moved.strip() or h_qty_received.strip())
            if ok and config.moves_stock(h_event) and h_qty > 0:
                res = stock_store.record_movement(
                    mcode, project, h_qty,
                    h_to.strip(), h_from.strip(), event=h_event,
                    description=(order or {}).get("PartName", "")
                    or part_name,
                    part_type=(order or {}).get("Process", "") or part_type,
                    notes=h_notes.strip(), courier=h_courier.strip(),
                    build=h_build.strip(),
                    date=h_date.strftime("%d %b %Y"),
                    logged_by=user.get("email", "") or user.get("name", ""))
                stock_note = (" · logged and counted" if res.get("ok") else
                              " — but the movement log and count were NOT "
                              "updated: %s"
                              % res.get("problem", "unknown error"))
            elif ok and config.moves_stock(h_event):
                stock_note = (" · no quantity given, so nothing was "
                              "counted — fill in Qty moved to have it "
                              "reach the count")
            if ok:
                parts_tracker.refresh(record_id)
                ov = record_builder.write_overview(
                    project,
                    user.get("email", "") or user.get("name", ""),
                    sheet_id=record_id, replace=True)
                if ov.get("problem"):
                    st.warning("Overview not refreshed: %s" % ov["problem"])
                (st.warning if "NOT" in stock_note else st.success)(
                    message + stock_note)
            else:
                st.error(message)

    else:   # 💰 Costs — order guaranteed: the option only shows with one
        def _to_float(v):
            try:
                return float(str(v).strip()) if str(v).strip() else 0.0
            except (ValueError, TypeError):
                return 0.0

        current_parts = _to_float(order.get("PartsCostCNY", ""))
        current_ship = _to_float(order.get("ShippingCostCNY", ""))

        with st.form("cost_form_%s" % ns):
            cc1, cc2 = st.columns(2)
            with cc1:
                parts_cost = st.number_input("Parts (CNY)", min_value=0.0,
                                             value=current_parts, step=1.0,
                                             format="%.2f")
            with cc2:
                ship_cost = st.number_input("Shipping (CNY)", min_value=0.0,
                                            value=current_ship, step=1.0,
                                            format="%.2f")

            total_cny = parts_cost + ship_cost
            total_gbp = total_cny * CNY_TO_GBP

            st.markdown("**Total: ¥%s CNY ≈ £%s GBP**  *(rate: 1 CNY = %s "
                        "GBP)*" % ("{:,.2f}".format(total_cny),
                                   "{:,.2f}".format(total_gbp), CNY_TO_GBP))

            if st.form_submit_button("Save Costs", type="primary"):
                if client:
                    cost_updates = {}
                    if parts_cost != current_parts:
                        cost_updates["PartsCostCNY"] = str(parts_cost)
                    if ship_cost != current_ship:
                        cost_updates["ShippingCostCNY"] = str(ship_cost)
                    if cost_updates:
                        update_order(client, order_id, cost_updates)
                        flash(ns, "success", "Costs saved.")
                        st.rerun()
                    else:
                        st.info("No changes.")
