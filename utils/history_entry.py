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
from utils import (holders_store, movements_store, parts_tracker,
                   record_builder, stock_store, tracker_orders,
                   tracker_writer, ui)
from utils.auth import is_admin
from utils.google_client import get_gspread_client
from utils.orders_store import (fetch_all_orders,
                                fetch_orders_for_part, update_order)
from utils.tracker_parse import (display_event, event_day, holder_of,
                                 is_selected, newest_first, place_of, to_int)


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
    # Newest on top, by the date each line HAPPENED (Hamid, 19 Sep) — the
    # sheet keeps append order, and a backfill is typed long after its day.
    _hist = newest_first(_hist)
    st.caption("Newest first — ordered by the date each line happened, not "
               "by when it was typed in.")
    ui.native_table(
        ["Date", "Event", "Order / Sample ID", "Version", "Build",
         "Qty ordered", "Qty moved", "Qty received", "From", "To",
         "Vendor / Source", "ETA", "QC", "Courier / Tracking", "Selected",
         "Notes"],
        [[r.get("date", ""), display_event(r),
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


def part_holdings(mcode: str, record_id: str) -> dict:
    """{holder: qty>0} for ONE part - the From rule's raw material."""
    out = {}
    for (p, h), q in movements_store.holdings(record_id).items():
        if p == mcode and q > 0:
            out[h] = q
    return out


def all_holdings_of(holder: str, record_id: str) -> dict:
    """{part: qty>0} one holder has - the batch consignment's menu."""
    out = {}
    for (p, h), q in movements_store.holdings(record_id).items():
        if h == holder and q > 0:
            out[p] = q
    return out


def default_sender(holdings: dict, my_names) -> str:
    """The From rule (Hamid, 19 Sep): you first - people record what
    they themselves are doing - else the largest holding."""
    if not holdings:
        return ""
    for name in holdings:
        if name in my_names:
            return name
    return max(holdings, key=lambda h: holdings[h])


def open_legs(record_id: str, project: str) -> dict:
    """{part code, lower-cased: [legs]} — shipments still on their way, the
    ones 📬 Arrived can book in (Hamid, 19 Sep: "build the arrived action").

    🚚 Ship only takes goods OUT of the sender's count; until this verb
    nothing put them INTO the receiver's, so a shipped leg stayed "in
    transit" for ever and the destination never counted them.

    A leg is offered when the board would still call it open (no Delivery
    pairs with it) AND it is safe to count:
      - the app wrote it (`logged_by`) — a migrated leg's arrival may already
        sit in the counts some other way, and booking it in would count twice;
      - it states a number, a sender and a destination;
      - the sender holds stock — a leg FROM a vendor/source is an order
        arriving, which is 📥 Receive's job (it moves the order's received
        total; this verb must not);
      - the part's ledger records it — an orphan row is a drift to settle,
        not goods to book in.
    """
    from utils import overview_board

    by_part = {}
    for row in movements_store.shipments(record_id):
        code = str(row.get("part_id", "")).strip()
        if code:
            by_part.setdefault(code.lower(), []).append(
                dict(row, project=project))
    out = {}
    for code, rows in by_part.items():
        sent = [r for r in rows
                if str(r.get("event", "")).strip().lower() == "shipping"]
        # Only a Delivery closes a leg — a Return is its own whole journey
        # (the board applies the same rule).
        arrived = [r for r in rows
                   if str(r.get("event", "")).strip().lower() == "delivery"]
        pairs, _spare = overview_board.match_arrivals(sent, arrived)
        legs = []
        for leg, arrival in pairs:
            sender = str(leg.get("from", "")).strip()
            if (arrival is not None
                    or not str(leg.get("logged_by", "")).strip()
                    or to_int(leg.get("qty", "")) <= 0
                    or not str(leg.get("to", "")).strip()
                    or not sender or sender == "(external)"
                    or holders_store.get(sender).get(
                        "kind", "person").lower() == "source"
                    or overview_board.is_orphan(leg)):
                continue
            legs.append(leg)
        if legs:
            out[code] = legs
    return out


def leg_label(leg: dict) -> str:
    """One open leg, said the way the person who shipped it would."""
    bits = ["%s pcs" % to_int(leg.get("qty", "")),
            "%s → %s" % (leg.get("from", "?"), leg.get("to", "?")),
            "shipped %s" % (leg.get("date", "") or "?")]
    if str(leg.get("courier", "")).strip():
        bits.append(str(leg.get("courier", "")).strip())
    return " · ".join(bits)


def consignment_siblings(leg: dict, legs_by_part: dict) -> list:
    """Other parts' open legs that travelled in the SAME box: same tracking
    text, same day, same sender and destination. Batch shipping writes one
    leg per part under one tracking number; this is how the arrival finds
    them again. No tracking text, no siblings — nothing else ties legs
    together honestly."""
    from utils import shipments_store

    tracking = str(leg.get("courier", "")).strip().lower()
    if not tracking:
        return []
    day = shipments_store.calendar_day(leg.get("date", ""))
    mine = str(leg.get("part_id", "")).strip().lower()
    out = []
    for code, legs in legs_by_part.items():
        if code == mine:
            continue
        for other in legs:
            if (str(other.get("courier", "")).strip().lower() == tracking
                    and shipments_store.calendar_day(
                        other.get("date", "")) == day
                    and str(other.get("from", "")).strip().lower()
                    == str(leg.get("from", "")).strip().lower()
                    and str(other.get("to", "")).strip().lower()
                    == str(leg.get("to", "")).strip().lower()):
                out.append(other)
    return out


@st.dialog("➕ Add a name to the directory")
def _add_name_dialog():
    """Admin-only: the ONE way a new holder/vendor enters the directory
    (Hamid, 19 Sep - the 28 Aug type-a-name escape is revoked)."""
    name = st.text_input("Name")
    kind = st.radio("Kind", ["person", "vendor", "source"],
                    horizontal=True,
                    help="A 'source' (an off-the-shelf supplier) never "
                         "holds a balance; a named vendor does.")
    notes = st.text_input("Notes (optional)")
    if st.button("Add to the directory", type="primary"):
        if not name.strip():
            st.error("A name is needed.")
        else:
            why = holders_store.register(
                name.strip(), kind=kind,
                notes=notes.strip() or "added via the entry form")
            if why:
                st.error(why)
            else:
                st.rerun()


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
        # Mirrored as a toast too (19 Sep user test: confirmations rendered
        # below long pages went unseen and users re-submitted). Fired on the
        # RENDER run, so the rerun cannot tear it down.
        try:
            st.toast(_f[1])
        except Exception:
            pass

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
            # No OPEN order: fall back to the LATEST one (19 Sep user test,
            # bug 1). Without this, an admin restate on a delivered order
            # wrote a part-level row with a BLANK order id and no cell
            # write-back — the order could never re-open, and outstanding
            # goods could never be booked in again.
            _closed = sorted(_cands,
                             key=lambda o: str(o.get("CreatedAt", "")))
            if _closed:
                order = _closed[-1]
                _cell = (str(order.get("Status", "")).strip().lower()
                         or "new")
                st.caption("No open order — entries land on the latest "
                           "one, **%s** (%s). A **Receipt** books more "
                           "goods against it; an admin **Update** "
                           "restating the received total re-opens it."
                           % (order.get("OrderID", "?"), _cell))
            else:
                st.caption("No order for this part yet — entries are "
                           "recorded part-level. Raise one on **Order "
                           "from BOM** for a Receipt or Costs.")
    order_id = str(order.get("OrderID", "")).strip() if order else ""

    # ------------------------------------------------------------------
    # The five verbs (Hamid, 19 Sep: "pickers and from rule are approved").
    # Joe's review: fewer, plainer choices; per-event fields only; the app
    # fills what it already knows. The ledger vocabulary is UNCHANGED —
    # each verb writes the same precise event as before; a Note's kind
    # picks Update / QC / Hold. New names are admin-only (the 28 Aug
    # type-a-name escape is revoked): everyone else picks from dropdowns.
    # ------------------------------------------------------------------
    _holdings = part_holdings(mcode, record_id)
    _me = {h for h in _holdings
           if tracker_orders.holder_matches(user.get("name", ""), h,
                                            user.get("email", ""))}
    _sender_default = default_sender(_holdings, _me)
    _directory = holders_store.names()

    def _fmt_holder(h):
        return "%s — %s" % (h, _holdings[h]) if h in _holdings else (h or "—")

    def _dir_index(name):
        """Index of `name` in [""] + directory — 0 when absent/blank."""
        name = str(name or "").strip()
        opts = [""] + _directory
        return opts.index(name) if name in opts else 0

    # 📬 Arrived is the sixth verb and the only conditional one: it shows
    # when THIS part has a shipment on its way, and is gone once it landed
    # — the list stays as short as Joe asked whenever nothing is in transit.
    _legs_by_part = open_legs(record_id, project)
    _legs = _legs_by_part.get(mcode.strip().lower(), [])

    verbs = []
    if order:
        verbs.append(("receive", "📥 Receive"))
    verbs.append(("ship", "🚚 Ship"))
    if _legs:
        verbs.append(("arrived", "📬 Arrived"))
    verbs += [("hand", "🤝 Hand over"),
              ("scrap", "🗑 Scrap"), ("note", "📝 Note"),
              ("more", "➕ More…")]
    if order:
        verbs.append(("costs", "💰 Costs"))

    _vc1, _vc2 = st.columns([5, 1.2], vertical_alignment="center")
    with _vc1:
        _verb = st.radio("What happened?", [v[1] for v in verbs],
                         horizontal=True, key="he_verb_%s" % ns,
                         label_visibility="collapsed")
    verb = next(k for k, label in verbs if label == _verb)
    with _vc2:
        if is_admin(user):
            if st.button("➕ New name", key="he_addname_%s" % ns,
                         use_container_width=True,
                         help="Add a person, vendor or source to the "
                              "Holders directory."):
                _add_name_dialog()
        else:
            st.caption("New name? Ask an admin.")

    if verb == "more":
        _more = [("Return", "↩ Return")]
        if order and str(order.get("Status", "")).strip().lower() != "cancelled":
            _more.append(("Cancelled", "🚫 Cancel order"))
        _mpick = st.radio("More", [m[1] for m in _more], horizontal=True,
                          key="he_more_%s" % ns, label_visibility="collapsed")
        verb = {"↩ Return": "return", "🚫 Cancel order": "cancel"}[_mpick]

    note_kind = ""
    if verb == "note":
        _nk = st.radio("Kind of note",
                       ["🛈 Update", "🔍 QC verdict", "⏸ Hold"],
                       horizontal=True, key="he_notekind_%s" % ns,
                       label_visibility="collapsed")
        note_kind = {"🛈 Update": "Update", "🔍 QC verdict": "QC",
                     "⏸ Hold": "Hold"}[_nk]

    _today = datetime.now().date()

    # --- shared write helpers (the exact 28 Aug writer sequences) --------
    def _after_ledger_write():
        parts_tracker.refresh(record_id)
        ov = record_builder.write_overview(
            project, user.get("email", "") or user.get("name", ""),
            sheet_id=record_id, replace=True)
        if ov.get("problem"):
            st.warning("Overview not refreshed: %s" % ov["problem"])

    def _generic_write(event, date, h_from="", h_to="", qty_ordered="",
                       qty_moved="", qty_received="", courier="", eta="",
                       qc="", build="", lead="", notes="", selected=False,
                       count=True, after_write=None):
        """Append the ledger line; count it when the event moves stock."""
        now = datetime.now()
        ok, message = tracker_writer.append_history(mcode, {
            "event": event, "date": date.strftime("%d %b %Y"),
            "order_id": order_id,
            "version": (order or {}).get("Version", "") or version,
            "build": build.strip(), "qty_ordered": qty_ordered.strip(),
            "qty_moved": qty_moved.strip(),
            "qty_received": qty_received.strip(),
            "place": h_from.strip(), "holder": h_to.strip(),
            "eta": eta, "lead_time": lead.strip(), "status": qc.strip(),
            "courier": courier.strip(),
            "selected": "TRUE" if selected else "FALSE",
            "logged_by": user.get("email", "") or user.get("name", ""),
            "logged_at": now.strftime("%d %b %Y %H:%M"),
            "notes": notes.strip(), "type": event,
        }, sheet_id=record_id)
        if not ok:
            st.error(message)
            return
        stock_note = ""
        _q = to_int(qty_moved.strip() or qty_received.strip())
        if count and config.moves_stock(event) and _q > 0:
            res = stock_store.record_movement(
                mcode, project, _q, h_to.strip(), h_from.strip(),
                event=event,
                description=(order or {}).get("PartName", "") or part_name,
                part_type=(order or {}).get("Process", "") or part_type,
                notes=notes.strip(), courier=courier.strip(),
                build=build.strip(), date=date.strftime("%d %b %Y"),
                logged_by=user.get("email", "") or user.get("name", ""))
            stock_note = (" · logged and counted" if res.get("ok") else
                          " — but the movement log and count were NOT "
                          "updated: %s" % res.get("problem", "unknown"))
        _after_ledger_write()
        if after_write:
            after_write()
        flash(ns, "warning" if "NOT" in stock_note else "success",
              message + stock_note)
        st.rerun()

    def _outbound_guard(h_from, qty_text):
        """From must hold enough — said plainly before anything writes."""
        q = to_int(qty_text)
        if not h_from:
            st.error("Who is sending? Pick the holder in **From**.")
            return 0
        if q <= 0:
            st.error("How many? The quantity is needed.")
            return 0
        have = _holdings.get(h_from, 0)
        if q > have:
            st.error("**%s** holds %s of this part — %s cannot leave."
                     % (h_from, have or "none", q))
            return 0
        return q

    _senders = list(_holdings)
    _sender_i = (_senders.index(_sender_default)
                 if _sender_default in _senders else 0)

    # === 📥 Receive ======================================================
    if verb == "receive":
        _ordered_q, _received_q = tracker_orders.order_progress(
            parts_tracker.fetch_all_parts(record_id).get(mcode, {}).get(
                "history", []), order_id)
        _outstanding = max(_ordered_q - _received_q, 0)
        st.caption("Books the goods in against **%s** — %s of %s received, "
                   "**%s outstanding**. Closes the order only when "
                   "everything ordered has arrived."
                   % (order_id, _received_q, _ordered_q or "?",
                      _outstanding or "none"))
        with st.form("he_form_receive_%s" % ns):
            r1, r2, r3 = st.columns(3)
            with r1:
                _from = st.selectbox(
                    "From (the vendor)", [""] + _directory,
                    index=_dir_index((order or {}).get("Vendor", "")),
                    format_func=lambda h: h or "— the order's vendor —")
                _date = st.date_input("Date", value=_today)
            with r2:
                _to = st.selectbox(
                    "Received by", [""] + _directory,
                    index=_dir_index((order or {}).get("Recipient", "")),
                    format_func=lambda h: h or "—")
                _qty = st.text_input("Qty received",
                                     value=str(_outstanding or ""))
            with r3:
                _courier = st.text_input(
                    "Courier / Tracking",
                    value=str((order or {}).get("TrackingNum", "")).strip())
                _notes = st.text_input("Notes")
            _go = st.form_submit_button("📥 Book the goods in",
                                        type="primary")
        if _go:
            if not _to:
                st.error("Received by is needed — pick who holds the "
                         "parts now.")
                st.stop()
            _qty_rec = to_int(_qty)
            if not _qty_rec:
                try:
                    _qty_rec = int(float(order.get("Quantity", 1) or 1))
                except (TypeError, ValueError):
                    _qty_rec = 1
            _recv_from = _from or str(order.get("Vendor", "")).strip()
            _note = (_notes.strip()
                     or "received %s" % _date.strftime("%d %b %Y"))
            now = datetime.now()
            ok, message = tracker_writer.write_receipt(
                mcode, order_id=order_id,
                qty_ordered=str(order.get("Quantity", "")),
                qty_received=str(_qty_rec), received_from=_recv_from,
                holder=_to, courier=_courier.strip(),
                date=_date.strftime("%d %b %Y"),
                version=order.get("Version", ""), eta=order.get("ETA", ""),
                note=_note,
                logged_by=user.get("email", "") or user.get("name", ""),
                logged_at=now.strftime("%d %b %Y %H:%M"),
                sheet_id=record_id)
            if not ok:
                st.error(message)
                st.stop()
            stock_note = ""
            if _qty_rec > 0:
                res = stock_store.record_movement(
                    mcode, project, _qty_rec, _to, _recv_from,
                    event="Receipt",
                    description=order.get("PartName", "") or part_name,
                    part_type=order.get("Process", "") or part_type,
                    notes=_note, courier=_courier.strip(),
                    build=order.get("Version", ""),
                    date=_date.strftime("%d %b %Y"),
                    logged_by=user.get("email", "") or user.get("name", ""))
                stock_note = ("" if res.get("ok") else
                              " The stock count was NOT updated: %s"
                              % res.get("problem", "unknown error"))
            parts_tracker.refresh(record_id)
            if client:
                # Delivered only when delivered IN FULL (19 Sep 2026).
                updates = {}
                if _courier.strip():
                    updates["TrackingNum"] = _courier.strip()
                _hist = parts_tracker.fetch_all_parts(record_id).get(
                    mcode, {}).get("history", [])
                _o_q, _r_q = tracker_orders.order_progress(_hist, order_id)
                if _r_q and (_r_q >= _o_q or _o_q == 0):
                    updates["Status"] = "delivered"
                if updates:
                    update_order(client, order_id, updates)
                    st.session_state.pop("ofb_working", None)
            ov = record_builder.write_overview(
                project, user.get("email", "") or user.get("name", ""),
                sheet_id=record_id, replace=True)
            if ov.get("problem"):
                st.warning("Overview not refreshed: %s" % ov["problem"])
            flash(ns, "warning" if stock_note else "success",
                  message + stock_note)
            st.rerun()

    # === 🚚 Ship =========================================================
    elif verb == "ship":
        _batch = st.checkbox("📦 Several parts together (one sender, one "
                             "destination)", key="he_batch_%s" % ns)
        if not _senders:
            st.info("The count shows nobody holding **%s** — record the "
                    "arrival first (📥 Receive), then ship." % mcode)
        elif _batch:
            _from = st.selectbox(
                "From (who is sending)", _senders, index=_sender_i,
                format_func=_fmt_holder, key="he_bfrom_%s" % ns)
            _their = all_holdings_of(_from, record_id)
            st.caption("Everything **%s** holds — tick what goes in this "
                       "consignment." % _from)
            with st.form("he_form_bship_%s" % ns):
                _rows = []
                for _pm in sorted(_their):
                    c1, c2 = st.columns([3, 1])
                    with c1:
                        _on = st.checkbox(
                            "%s — %s available" % (_pm, _their[_pm]),
                            value=(_pm == mcode))
                    with c2:
                        _q = st.text_input("Qty", value=str(_their[_pm]),
                                           key="he_bq_%s_%s" % (_pm, ns),
                                           label_visibility="collapsed")
                    _rows.append((_pm, _on, _q))
                b1, b2, b3 = st.columns(3)
                with b1:
                    _to = st.selectbox("To / recipient", [""] + _directory,
                                       format_func=lambda h: h or "—")
                with b2:
                    _eta = st.date_input("ETA (optional)", value=None)
                    _date = st.date_input("Date", value=_today)
                with b3:
                    _courier = st.text_input("Courier / Tracking")
                    _notes = st.text_input("Notes")
                _go = st.form_submit_button("🚚 Ship the consignment",
                                            type="primary")
            if _go:
                _picked = [(p, to_int(q)) for p, on, q in _rows if on]
                if not _picked:
                    st.error("Nothing ticked — nothing ships.")
                    st.stop()
                # Every batched part gets ITS OWN open order id stamped
                # (19 Sep user test: only the anchor part carried one, so
                # a batched part's open order never derived "shipped").
                _b_derived = {}
                for _t in tracker_orders.all_projects_orders():
                    _boid = str(_t.get("order_id", "")).strip()
                    if _boid:
                        _b_derived[_boid] = _t.get("derived", "")
                _open_of = {}
                for _bo in fetch_all_orders():
                    if str(_bo.get("Project", "")).strip() != project:
                        continue
                    _beff = tracker_orders.effective_status(
                        (_bo.get("Status") or "new").strip() or "new",
                        _b_derived.get(
                            str(_bo.get("OrderID", "")).strip(), ""))
                    if _beff not in ("delivered", "cancelled"):
                        _open_of.setdefault(
                            str(_bo.get("PartID", "")).strip(),
                            str(_bo.get("OrderID", "")).strip())
                _bad = [p for p, q in _picked
                        if q <= 0 or q > _their.get(p, 0)]
                if _bad:
                    st.error("Check the quantities for: %s — each must be "
                             "at least 1 and at most what %s holds."
                             % (", ".join("`%s`" % b for b in _bad), _from))
                    st.stop()
                now = datetime.now()
                _done, _failed = [], []
                for _pm, _q in _picked:
                    ok, message = tracker_writer.append_history(_pm, {
                        "event": "Shipping",
                        "date": _date.strftime("%d %b %Y"),
                        "order_id": (order_id if _pm == mcode
                                     else _open_of.get(_pm, "")),
                        "qty_moved": str(_q), "place": _from, "holder": _to,
                        "eta": _eta.strftime("%d %b %Y") if _eta else "",
                        "courier": _courier.strip(), "selected": "FALSE",
                        "logged_by": user.get("email", "")
                        or user.get("name", ""),
                        "logged_at": now.strftime("%d %b %Y %H:%M"),
                        "notes": (_notes.strip() or "batch consignment"),
                        "type": "Shipping",
                    }, sheet_id=record_id)
                    if ok:
                        res = stock_store.record_movement(
                            _pm, project, _q, _to, _from, event="Shipping",
                            notes=_notes.strip() or "batch consignment",
                            courier=_courier.strip(),
                            date=_date.strftime("%d %b %Y"),
                            logged_by=user.get("email", "")
                            or user.get("name", ""))
                        (_done if res.get("ok") else _failed).append(_pm)
                    else:
                        _failed.append(_pm)
                _after_ledger_write()
                flash(ns, "warning" if _failed else "success",
                      "Shipped %d part(s)%s." % (len(_done),
                      " — FAILED: %s" % ", ".join(_failed)
                      if _failed else ""))
                st.rerun()
        else:
            st.caption("Takes the quantity out of the sender's count — "
                       "goods on their way out. When they land, **📬 "
                       "Arrived** (it appears here while a shipment is on "
                       "its way) books them in at the destination.")
            with st.form("he_form_ship_%s" % ns):
                s1, s2, s3 = st.columns(3)
                with s1:
                    _from = st.selectbox(
                        "From (who is sending)", _senders, index=_sender_i,
                        format_func=_fmt_holder)
                    _date = st.date_input("Date", value=_today)
                with s2:
                    _to = st.selectbox("To / recipient", [""] + _directory,
                                       format_func=lambda h: h or "—")
                    _qty = st.text_input("Qty")
                with s3:
                    _courier = st.text_input("Courier / Tracking")
                    _eta = st.date_input("ETA (optional)", value=None)
                _notes = st.text_input("Notes")
                _go = st.form_submit_button("🚚 Ship it", type="primary")
            if _go:
                _q = _outbound_guard(_from, _qty)
                if not _q:
                    st.stop()
                _generic_write("Shipping", _date, h_from=_from, h_to=_to,
                               qty_moved=str(_q), courier=_courier,
                               eta=_eta.strftime("%d %b %Y") if _eta
                               else "", notes=_notes)

    # === 📬 Arrived ======================================================
    # The other half of 🚚 Ship (Hamid, 19 Sep: "build the arrived action").
    # Writes the Delivery event the ledger has always recognised: stock IN
    # at the destination, and the board pairs it with its Shipping leg. The
    # app knows everything but the day and the count, so that is all it asks.
    elif verb == "arrived":
        st.caption("Books a shipment in where it was going — adds it to the "
                   "receiver's count and closes the leg. The other half of "
                   "🚚 Ship.")
        _leg_labels = {}
        for _l in _legs:
            _label = leg_label(_l)
            while _label in _leg_labels:          # two identical legs
                _label += " ·"
            _leg_labels[_label] = _l
        if len(_legs) > 1:
            _leg = _leg_labels[st.selectbox(
                "Which shipment arrived?", list(_leg_labels),
                key="he_leg_%s" % ns)]
        else:
            _leg = _legs[0]
            st.markdown("**%s**" % next(iter(_leg_labels)))
        _shipped = to_int(_leg.get("qty", ""))
        _sibs = consignment_siblings(_leg, _legs_by_part)
        with st.form("he_form_arrived_%s" % ns):
            a1, a2, a3 = st.columns(3)
            with a1:
                _qty = st.text_input("Qty arrived", value=str(_shipped))
            with a2:
                _date = st.date_input("Date arrived", value=_today)
            with a3:
                _notes = st.text_input("Notes")
            _whole = False
            if _sibs:
                _whole = st.checkbox(
                    "📦 The whole consignment arrived — also books in: %s"
                    % ", ".join("%s ×%s" % (s.get("part_id", "?"),
                                            to_int(s.get("qty", "")))
                                for s in _sibs), value=True)
            _go = st.form_submit_button("📬 Book it in", type="primary")
        if _go:
            _q = to_int(_qty)
            if _q <= 0:
                st.error("How many arrived? The quantity is needed.")
                st.stop()
            if _q > _shipped:
                st.error("%s were shipped — %s cannot arrive. If more turned "
                         "up than the sender recorded, the shipment is what "
                         "needs correcting." % (_shipped, _q))
                st.stop()
            if _q < _shipped and not _notes.strip():
                st.error("Fewer arrived than the %s shipped — say why in "
                         "**Notes** (lost, damaged, a second box to follow). "
                         "The shortfall stays out of every count."
                         % _shipped)
                st.stop()
            _left = event_day(_leg.get("date", ""))
            if (_left and all(_left)
                    and (_date.year, _date.month, _date.day) < _left):
                st.error("It shipped on %s — it cannot arrive before that."
                         % _leg.get("date", ""))
                st.stop()
            now = datetime.now()
            _who = user.get("email", "") or user.get("name", "")
            _done, _failed = [], []
            for _l, _n in [(_leg, _q)] + (
                    [(s, to_int(s.get("qty", ""))) for s in _sibs]
                    if _whole else []):
                _mine = _l is _leg
                _pm = mcode if _mine else str(_l.get("part_id", "")).strip()
                _twin = movements_store.ledger_twin(_l) or {}
                _why = (_notes.strip() or "arrived — shipped %s"
                        % (_l.get("date", "") or "earlier"))
                ok, message = tracker_writer.append_history(_pm, {
                    "event": "Delivery",
                    "date": _date.strftime("%d %b %Y"),
                    # The arrival belongs to the thread its shipment is on.
                    "order_id": (str(_twin.get("order_id", "")).strip()
                                 or (order_id if _mine else "")),
                    "version": (_twin.get("version", "")
                                or (((order or {}).get("Version", "")
                                     or version) if _mine else "")),
                    "build": _twin.get("build", ""),
                    "qty_moved": str(_n),
                    "place": str(_l.get("from", "")).strip(),
                    "holder": str(_l.get("to", "")).strip(),
                    "courier": str(_l.get("courier", "")).strip(),
                    "selected": "FALSE", "logged_by": _who,
                    "logged_at": now.strftime("%d %b %Y %H:%M"),
                    "notes": _why, "type": "Delivery",
                }, sheet_id=record_id)
                if not ok:
                    _failed.append(_pm)
                    continue
                res = stock_store.record_movement(
                    _pm, project, _n, str(_l.get("to", "")).strip(),
                    str(_l.get("from", "")).strip(), event="Delivery",
                    description=_l.get("description", ""),
                    part_type=_l.get("type", ""), notes=_why,
                    courier=str(_l.get("courier", "")).strip(),
                    build=_l.get("build", ""),
                    date=_date.strftime("%d %b %Y"), logged_by=_who)
                (_done if res.get("ok") else _failed).append(_pm)
            _after_ledger_write()
            flash(ns, "warning" if _failed else "success",
                  "Booked in %d part(s) at %s%s."
                  % (len(_done), _leg.get("to", "?"),
                     " — NOT counted: %s" % ", ".join(_failed)
                     if _failed else ""))
            st.rerun()

    # === 🤝 Hand over ====================================================
    elif verb == "hand":
        if not _senders:
            st.info("The count shows nobody holding **%s** — record the "
                    "arrival first (📥 Receive)." % mcode)
        else:
            st.caption("Moves the count from one holder to the other — "
                       "an in-person transfer, no courier.")
            with st.form("he_form_hand_%s" % ns):
                h1, h2, h3 = st.columns(3)
                with h1:
                    _from = st.selectbox(
                        "From (who is handing over)", _senders,
                        index=_sender_i, format_func=_fmt_holder)
                with h2:
                    _to = st.selectbox("To (new holder)", [""] + _directory,
                                       format_func=lambda h: h or "—")
                    _qty = st.text_input(
                        "Qty", value=str(_holdings.get(_sender_default, "")
                                         or ""))
                with h3:
                    _date = st.date_input("Date", value=_today)
                    _notes = st.text_input("Notes")
                _go = st.form_submit_button("🤝 Hand it over",
                                            type="primary")
            if _go:
                if not _to:
                    st.error("Who receives? Pick the new holder in **To**.")
                    st.stop()
                _q = _outbound_guard(_from, _qty)
                if not _q:
                    st.stop()
                _generic_write("Hand delivered", _date, h_from=_from,
                               h_to=_to, qty_moved=str(_q), notes=_notes)

    # === 🗑 Scrap ========================================================
    elif verb == "scrap":
        if not _senders:
            st.info("The count shows nobody holding **%s** — nothing to "
                    "scrap." % mcode)
        else:
            st.caption("Takes the quantity out of the count for good — "
                       "the row stays on the record forever.")
            with st.form("he_form_scrap_%s" % ns):
                c1, c2, c3 = st.columns(3)
                with c1:
                    _from = st.selectbox(
                        "From (whose stock)", _senders, index=_sender_i,
                        format_func=_fmt_holder)
                with c2:
                    _qty = st.text_input("Qty")
                    _date = st.date_input("Date", value=_today)
                with c3:
                    _notes = st.text_input("Reason")
                _go = st.form_submit_button("🗑 Scrap it", type="primary")
            if _go:
                if not _notes.strip():
                    st.error("A scrap needs a reason — one line will do.")
                    st.stop()
                _q = _outbound_guard(_from, _qty)
                if not _q:
                    st.stop()
                _generic_write("Scrap", _date, h_from=_from,
                               qty_moved=str(_q), notes=_notes)

    # === 📝 Note (Update / QC / Hold) ====================================
    elif verb == "note":
        if note_kind == "QC":
            st.caption("A verdict on the record — counts nothing.")
            with st.form("he_form_qc_%s" % ns):
                q1, q2, q3 = st.columns(3)
                with q1:
                    _verdict = st.selectbox("Verdict",
                                            ["Pass", "Fail", "Partial"])
                with q2:
                    _qty = st.text_input("Qty tested (optional)")
                with q3:
                    _date = st.date_input("Date", value=_today)
                _notes = st.text_input("Notes")
                _go = st.form_submit_button("🔍 Record the verdict",
                                            type="primary")
            if _go:
                _n = _notes.strip()
                if to_int(_qty):
                    _n = (_n + " (%s tested)" % to_int(_qty)).strip()
                _generic_write("QC", _date, qc=_verdict, notes=_n)
        elif note_kind == "Hold":
            st.caption("A pause on the record — counts nothing.")
            with st.form("he_form_hold_%s" % ns):
                _notes = st.text_input("Why on hold?")
                _date = st.date_input("Date", value=_today)
                _go = st.form_submit_button("⏸ Put it on hold",
                                            type="primary")
            if _go:
                if not _notes.strip():
                    st.error("Say why — a Hold without a reason tells "
                             "nobody anything.")
                    st.stop()
                _generic_write("Hold", _date, notes=_notes)
        else:   # plain Update
            st.caption("A note on the record — can carry a new ETA, a "
                       "build tag, the MP flag"
                       + (", and the admin restate of the received total "
                          "(what re-opens or closes an order after a "
                          "mistake or a rejection)." if is_admin(user)
                          else "."))
            with st.form("he_form_upd_%s" % ns):
                u1, u2, u3 = st.columns(3)
                with u1:
                    _date = st.date_input("Date", value=_today)
                    _eta = st.date_input("New ETA (optional)", value=None)
                with u2:
                    _build = st.text_input("Build (optional)")
                    _lead = st.text_input("Lead time, days (optional)")
                with u3:
                    _restate = (st.text_input(
                        "Restate qty received (admin)",
                        help="The order's received total becomes exactly "
                             "this number — below ordered re-opens it, at "
                             "ordered closes it.")
                        if is_admin(user) else "")
                    _selected = st.checkbox("Selected for MP?")
                _notes = st.text_input("Notes")
                _go = st.form_submit_button("🛈 Add the note",
                                            type="primary")
            if _go:
                if not (_notes.strip() or _eta or _build.strip()
                        or _restate.strip() or _selected or _lead.strip()):
                    st.error("An empty note records nothing — write "
                             "something, or fill one of the fields.")
                    st.stop()
                def _sync_restate():
                    if not (_restate.strip() and order and client):
                        return
                    _hist = parts_tracker.fetch_all_parts(record_id).get(
                        mcode, {}).get("history", [])
                    _o_q, _r_q = tracker_orders.order_progress(
                        _hist, order_id)
                    _new = ("delivered"
                            if _r_q and (_r_q >= _o_q or _o_q == 0)
                            else "ordered")
                    update_order(client, order_id, {"Status": _new})
                    st.session_state.pop("ofb_working", None)
                _generic_write("Update", _date,
                               qty_received=_restate.strip(),
                               eta=_eta.strftime("%d %b %Y") if _eta
                               else "", build=_build, lead=_lead,
                               notes=_notes, selected=_selected,
                               after_write=_sync_restate)

    # === ↩ Return ========================================================
    elif verb == "return":
        if not _senders:
            st.info("The count shows nobody holding **%s** — nothing to "
                    "return." % mcode)
        else:
            st.caption("Sends goods back where they came from — the "
                       "rejection recipe is QC Fail → Return → an admin "
                       "Update restating the received total.")
            with st.form("he_form_return_%s" % ns):
                r1, r2, r3 = st.columns(3)
                with r1:
                    _from = st.selectbox(
                        "From (whose stock)", _senders, index=_sender_i,
                        format_func=_fmt_holder)
                with r2:
                    _to = st.selectbox(
                        "Back to", [""] + _directory,
                        index=_dir_index((order or {}).get("Vendor", "")),
                        format_func=lambda h: h or "—")
                    _qty = st.text_input("Qty")
                with r3:
                    _courier = st.text_input(
                        "Courier / Tracking (optional)")
                    _date = st.date_input("Date", value=_today)
                _notes = st.text_input("Reason")
                _go = st.form_submit_button("↩ Send it back",
                                            type="primary")
            if _go:
                if not _to:
                    st.error("Back to whom? Pick the destination.")
                    st.stop()
                _q = _outbound_guard(_from, _qty)
                if not _q:
                    st.stop()
                _generic_write("Return", _date, h_from=_from, h_to=_to,
                               qty_moved=str(_q), courier=_courier,
                               notes=_notes)

    # === 🚫 Cancel order =================================================
    elif verb == "cancel":
        st.caption("Calls order **%s** off — terminal, though goods that "
                   "later actually arrive in full still outrank it."
                   % order_id)
        with st.form("he_form_cancel_%s" % ns):
            _notes = st.text_input("Why is it cancelled?")
            _sure = st.checkbox("I mean it — cancel this order.")
            _date = st.date_input("Date", value=_today)
            _go = st.form_submit_button("🚫 Cancel the order",
                                        type="primary")
        if _go:
            if not _notes.strip():
                st.error("Say why — the reason is the record.")
                st.stop()
            if not _sure:
                st.error("Tick the confirmation — cancelling is terminal.")
                st.stop()
            if client:
                update_order(client, order_id, {"Status": "cancelled"})
                st.session_state.pop("ofb_working", None)
            _generic_write("Cancelled", _date, notes=_notes, count=False)

    # === 💰 Costs ========================================================
    elif verb == "costs":
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
