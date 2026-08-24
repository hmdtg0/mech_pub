"""Order from BOM — every part in the BOM, pre-filled, reviewed, then submitted.

The point is to remove the typing. A BOM already knows the part number, the
name, the material and the spec; asking someone to re-enter all of that 44 times
is how mistakes get in. So the BOM fills the form and the human's job is the
part only they can do: how many, and which ones.

**Nothing is written until Submit.** Drafts live in this page's session only —
no draft status, no half-created orders in the tab everyone reads.

Grouped by BOM Type, one editor per group under a coloured heading. Streamlit
draws data_editor cells on a canvas, so cells cannot carry colour — the
heading's tinted bar is where the colour lives (its emoji glyph was dropped
18 Aug, Hamid: the bar alone carries the identity). (A batch-fill row inside the grid was tried and rolled back on
14 Aug — Hamid preferred the plain table.)
"""
import json
import os
import sys

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.auth import require_auth
from utils import (agent_entry, app_settings, bom_sheet, bulk_orders,
                   order_drafts, parts_model, parts_tracker, project_colors,
                   project_registry, tracker_orders)
from utils.ui import project_scope, require_single_project, table_height

# Reviewer is deliberately not collected here — deferred to a later phase. The
# Orders row keeps its Reviewer column and takes it blank.

user = require_auth()

st.title("🧾 Order from BOM")
require_single_project("Order from BOM")

project, record_id = project_registry.active()
bom_id = project_registry.bom_sheet(project)

if not bom_id:
    st.info("**%s** has no BOM sheet registered, so there is nothing to order "
            "from. An admin can link one on **Tools → Projects**." % project)
    st.stop()

build_tab = bom_sheet.default_build(bom_id)
bom_rows = bom_sheet.fetch_bom(build_tab, bom_id) if build_tab else []
if not bom_rows:
    st.warning("No parts read from the BOM. Check the BOM tab set for this "
               "project on **Tools → Projects**.")
    st.stop()

project_scope("Every order submitted on this page is filed to THIS "
              "project's record and read from its BOM — check it before a "
              "mass submit, not after.")
st.markdown("Reading **%d parts** from `%s`." % (len(bom_rows), build_tab))


def _int(value) -> int:
    """A grid number cell as an int — empty and NaN count as 0."""
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _text(value) -> str:
    """A grid text cell as a clean string — None and NaN count as ''."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _eta_str(value) -> str:
    """A grid date cell as the sheet's date string — unset counts as ''."""
    try:
        if value is None or pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    try:
        return value.strftime("%d %b %Y")
    except AttributeError:
        return str(value).strip()


# A draft LOADED on the previous run is applied here, before §1's widgets
# exist — Streamlit refuses session-state writes to a widget key after the
# widget has been drawn, so the hand-off has to land first.
_pending = st.session_state.pop("ofb_pending_draft", None)
if _pending:
    st.session_state["ofb_units"] = _pending["units"]
    st.session_state["ofb_build"] = _pending["build"]
    st.session_state["ofb_seed"] = _pending["seed"]
    st.session_state["ofb_loaded_draft"] = _pending["name"]
    for _k in [k for k in st.session_state if str(k).startswith("ofb_grid_")]:
        st.session_state.pop(_k, None)
    st.session_state.pop("ofb_frames", None)
    st.session_state.pop("ofb_nonce", None)

# --- Drafts: the hand-off between whoever fills and whoever submits ---------
# An engineer fills the selection and SAVES it; the PM loads it here, reviews
# and submits (Hamid, 19 Aug). Drafts live on the main record's Order Drafts
# tab — named, visible on the sheet, per project. Nothing about a draft is an
# order: loading one only fills this page.
_drafts = order_drafts.list_drafts(project)
if st.session_state.get("ofb_loaded_draft"):
    st.info("Working from draft **%s** — submitting will clear it."
            % st.session_state["ofb_loaded_draft"])
if _drafts:
    with st.expander("📂 Load a saved draft (%d)" % len(_drafts)):
        _names = sorted(_drafts)
        _pick = st.selectbox(
            "Draft", _names, key="ofb_draft_pick",
            format_func=lambda n: "%s — %s, %s (%d part%s)" % (
                n, _drafts[n]["saved_by"], _drafts[n]["saved_at"],
                len(_drafts[n]["lines"]),
                "" if len(_drafts[n]["lines"]) == 1 else "s"))
        c_load, c_del = st.columns([1, 1])
        if c_load.button("Open this draft", type="primary",
                         key="ofb_draft_load"):
            _d = _drafts[_pick]
            _seed_rows = {}
            for _r in bom_rows:
                _seed_rows[str(parts_model.normalise_code(
                    str(_r.get("mcode", "") or "")))] = {"include": False}
            from datetime import datetime as _dt
            for _l in _d["lines"]:
                _entry = {"include": True}
                if str(_l.get("qty", "")).strip().isdigit():
                    _entry["Qty"] = int(_l["qty"])
                if str(_l.get("recipient", "")).strip():
                    _entry["Recipient"] = _l["recipient"].strip()
                if str(_l.get("priority", "")).strip():
                    _entry["Priority"] = _l["priority"].strip()
                if str(_l.get("notes", "")).strip():
                    _entry["Notes"] = _l["notes"].strip()
                for _fmt in ("%d %b %Y", "%Y-%m-%d"):
                    try:
                        _entry["ETA"] = _dt.strptime(
                            str(_l.get("eta", "")).strip(), _fmt).date()
                        break
                    except ValueError:
                        continue
                _seed_rows[str(_l["part"]).strip()] = _entry
            try:
                _units_val = max(1, int(str(_d.get("units") or "1")))
            except ValueError:
                _units_val = 1
            st.session_state["ofb_pending_draft"] = {
                "name": _pick, "units": _units_val,
                "build": str(_d.get("build") or ""),
                # The seed signature must match what §1 will compute for the
                # loaded units (the draft never stores a default ETA).
                "seed": {"sig": "%s_" % _units_val, "rows": _seed_rows}}
            st.rerun()
        if c_del.button("Delete this draft", key="ofb_draft_del"):
            _why = order_drafts.delete_draft(project, _pick)
            if _why:
                st.error(_why)
            else:
                st.session_state.pop("ofb_loaded_draft", None)
                st.rerun()

# ============================================================
# 1. How many
# ============================================================
st.subheader("1. How many")
q1, q2, q3, q4 = st.columns([1, 1, 1, 2], vertical_alignment="bottom")
with q1:
    # Default read FROM session state: a loaded draft sets these keys before
    # the widgets exist, and a hardcoded default alongside that write makes
    # Streamlit warn about two sources of truth.
    units = st.number_input("Units to build", min_value=1, max_value=100000,
                            value=st.session_state.get("ofb_units", 1),
                            step=1, key="ofb_units",
                            help="How many finished assemblies this batch is for.")
with q2:
    eta_date = st.date_input("ETA (optional)", value=None, key="ofb_eta",
                             help="Default ETA for every row — each part's "
                                  "ETA stays editable in the table.")
with q3:
    build_tag = st.text_input("Build (optional)",
                              value=st.session_state.get("ofb_build", ""),
                              key="ofb_build",
                              help="Batch tag stamped on every order line's "
                                   "Build column — e.g. T2.")
with q4:
    st.caption("**Qty to order = BOM Qty × %d** — every row stays editable "
               "below." % units)

# Which parts already have a tab — an order for a part without one cannot be
# filed against its history, so it is worth saying before the submit fails.
have_tabs = set(parts_tracker.part_tabs(record_id))

# Parts whose ledger already has an order ON ITS WAY are shown, not offered
# (Hamid, 19 Aug: "once it is ordered ... should not be selected"). A second
# raise for something already travelling is a double count — the exact thing
# the audit spent a day undoing. Delivered and cancelled parts stay
# selectable: a reorder is a new batch, not a duplicate.
_status_of = {o["mcode"]: (o.get("derived") or "")
              for o in tracker_orders.part_orders(record_id, project)}
open_codes = {c for c, st_ in _status_of.items()
              if st_ in ("ordered", "shipped")}

rows = []
for r in bom_rows:
    code = parts_model.normalise_code(str(r.get("mcode", "") or ""))
    base = parts_tracker.tracker_parse.to_int(r.get("bom_qty", ""))
    rows.append({
        "include": True,
        "Part ID": code,
        "Part Name": " ".join(str(r.get("part_name", "") or "").split()),
        "Type": " ".join(str(r.get("group", "") or "").split()) or "—",
        "BOM Qty": base,
        "Qty": base * units,
        # Sensible starting values so the table opens usable; every one is
        # editable per row.
        "Recipient": user["name"],
        "Priority": "Normal",
        "Material": " ".join(str(r.get("material", "") or "").split()),
        "Spec": " ".join(str(r.get("spec", "") or "").split()),
        "ETA": eta_date,
        "Notes": "",
        "_cad": r.get("cad_link") or r.get("drawing") or "",
        "_version": r.get("most_recent_cad", "") or "",
        "_has_tab": code in have_tabs,
    })

# Tick-alls re-seed the grids via a PERSISTENT seed. Every rerun rebuilds
# `rows` from the BOM, so the saved tick state must be reapplied every run —
# a one-shot override reset everything on the next unrelated rerun, which
# was Hamid's "deselecting one selects the others". The seed dies when §1
# changes, because new units/ETA are meant to rebuild the rows.
_base_sig = "%s_%s" % (units, eta_date or "")
_seed = st.session_state.get("ofb_seed")
if _seed and _seed.get("sig") == _base_sig:
    for row in rows:
        stored = _seed.get("rows", {}).get(str(row["Part ID"]))
        if stored:
            for col in ("include", "Qty", "Recipient", "Priority",
                        "Material", "Spec", "ETA", "Notes"):
                if col in stored:
                    row[col] = stored[col]
elif _seed:
    st.session_state.pop("ofb_seed", None)

already_on_order = [r for r in rows if r["Part ID"] in open_codes]
rows = [r for r in rows if r["Part ID"] not in open_codes]

if already_on_order:
    st.info("**%d part(s) already have an order on its way and are not "
            "offered below.** They come back the moment their order is "
            "delivered or cancelled." % len(already_on_order))
    with st.expander("See which (%d)" % len(already_on_order)):
        st.dataframe(pd.DataFrame([{
            "Part ID": r["Part ID"], "Part Name": r["Part Name"],
            "Status": _status_of.get(r["Part ID"], ""),
        } for r in already_on_order]), hide_index=True,
            use_container_width=True,
            height=table_height(len(already_on_order)))

types = sorted({r["Type"] for r in rows})
missing_tabs = [r["Part ID"] for r in rows if not r["_has_tab"]]
if missing_tabs:
    st.warning(
        "%d part(s) have no tab in the project record, so their history cannot "
        "be recorded: %s. Build them on **Tools → Projects → Build record from "
        "BOM** first."
        % (len(missing_tabs), ", ".join("`%s`" % c for c in missing_tabs[:10])
           + ("…" if len(missing_tabs) > 10 else "")))

# ============================================================
# 2. Parts
# ============================================================
st.subheader("2. Parts")

# The grid-state nonce and signature — defined BEFORE the Agent Entry box
# because its Apply click resets the grids through them. They used to sit
# with the grid code below, and the first cut of Agent Entry hit a NameError
# on the very click it existed for.
if "ofb_nonce" not in st.session_state:
    st.session_state["ofb_nonce"] = 0
_signature = "%s_%s" % (_base_sig, st.session_state["ofb_nonce"])

# Agent Entry: one line per order, applied INTO the grids below — so the
# review, the problem checks and the confirm tick stay the single path to a
# submit whichever way the orders were typed. This is also the entry built
# for working with an agent: forty checkbox clicks are hard to drive and
# harder to audit; a pasted list is both.
with st.expander("🤖 Agent Entry — paste order lines instead of ticking"):
    st.caption("One per line: `Part[, qty[, recipient[, ETA[, priority[, "
               "notes]]]]]` — commas, pipes or tabs. Shorthand `M105 x120` "
               "works. Blank fields keep the grid's defaults; `#` starts a "
               "comment.")
    # A FORM, deliberately: Streamlit only commits a text area on blur or
    # Ctrl-Enter, so a plain button next to one fires with the text the
    # server had BEFORE the click — the first cut applied an empty page and
    # said nothing. A form submits the text and the click as one event.
    with st.form("ofb_agent_form"):
        agent_text = st.text_area(
            "Order lines", key="ofb_agent",
            placeholder="M105, 120, Ryan Wong, 25 Aug 2026" + chr(10) + "M213 x40",
            label_visibility="collapsed", height=120)
        replace_sel = st.checkbox(
            "Untick everything else (the pasted lines become the whole "
            "selection)", value=True, key="ofb_agent_replace")
        agent_apply = st.form_submit_button("Apply to the tables below")
if agent_apply:
    # Known codes include the withheld open-order parts, so a line naming
    # one gets the TRUE refusal ("already on its way"), not "not in this BOM".
    parsed, errors = agent_entry.parse_lines(
        agent_text,
        [r["Part ID"] for r in rows]
        + [r["Part ID"] for r in already_on_order],
        open_codes, default_recipient=user["name"])
    for e in errors:
        st.error(e)
    if not parsed and not errors:
        st.info("Nothing to apply — the box is empty.")
    if parsed:
        seed_rows = {}
        if replace_sel:
            for r in rows:
                seed_rows[str(r["Part ID"])] = {"include": False}
        by_code_now = {r["Part ID"]: r for r in rows}
        for q in parsed:
            base = by_code_now[q["code"]]
            entry = {"include": True,
                     "Qty": q["qty"] if q["qty"] is not None
                     else base["Qty"],
                     "Recipient": q["recipient"] or base["Recipient"]}
            if q["eta"] is not None:
                entry["ETA"] = q["eta"]
            if q["priority"]:
                entry["Priority"] = q["priority"]
            if q["notes"]:
                entry["Notes"] = q["notes"]
            seed_rows[str(q["code"])] = entry
        # Reapply onto THIS run's rows (the grids render below us), and
        # persist as the seed for later runs. No st.rerun: the first cut
        # rebuilt the page and wiped its own error messages.
        for row in rows:
            stored = seed_rows.get(str(row["Part ID"]))
            if stored:
                for col, val in stored.items():
                    row[col] = val
        st.session_state["ofb_seed"] = {"sig": _base_sig,
                                        "rows": seed_rows}
        for type_name in types:
            st.session_state.pop("ofb_grid_%s_%s"
                                 % (type_name, _signature), None)
        st.session_state.pop("ofb_frames", None)
        st.session_state["ofb_nonce"] += 1
        _signature = "%s_%s" % (_base_sig, st.session_state["ofb_nonce"])
        st.success("Applied %d line(s) — review below, then submit."
                   % len(parsed))

st.caption("Recipient and Priority are per part — a batch can go to more than "
           "one person.")

VISIBLE = ["include", "Part ID", "Part Name", "BOM Qty", "Qty", "Recipient",
           "Priority", "Material", "Spec", "ETA", "Notes"]

# One set of column widths for every group table, and they act as WEIGHTS:
# the grids fill the page exactly, so a wider screen scales every column up
# in proportion and nothing can fall off the edge. Streamlit's docs say width
# takes small/medium/large, but the 1.38 frontend passes a bare number
# through as pixels — that is what lets a saved layout be exact.
DEFAULT_WIDTHS = {
    "include": 40, "Part ID": 65, "Part Name": 230, "BOM Qty": 45, "Qty": 55,
    "Recipient": 120, "Priority": 85, "Material": 130, "Spec": 140,
    "ETA": 110, "Notes": 180,
}
# Titles are deliberately short (Hamid, 17 Aug) so the narrow columns can
# actually be narrow; the tick column has no title at all. The layout panel
# needs printable names, hence the separate labels here.
COLUMN_LABELS = {"include": "Tick", "BOM Qty": "BOM", "Qty": "Order"}


def _saved_layout() -> dict:
    """The installation-wide layout, from the main record's Settings tab."""
    out = dict(DEFAULT_WIDTHS)
    raw = app_settings.get(app_settings.ORDER_GRID_LAYOUT)
    if raw:
        try:
            saved = json.loads(raw)
            for key in out:
                out[key] = max(20, min(800, int(saved.get(key, out[key]))))
        except (ValueError, TypeError):
            pass
    return out


def _reset_widths() -> None:
    for key in DEFAULT_WIDTHS:
        st.session_state["ofb_w_%s" % key] = DEFAULT_WIDTHS[key]


saved_widths = _saved_layout()
with st.expander("⚖ Column layout — shared by everyone"):
    st.caption("Widths behave as proportions: the table always fits the page, "
               "so a wider screen scales every column up together. Edits here "
               "preview live; **Save** writes them to the main record so every "
               "user gets this layout. Column edges in the table itself can't "
               "be dragged — this panel is the only width control.")
    width_cols = st.columns(5)
    widths = {}
    for i, key in enumerate(DEFAULT_WIDTHS):
        with width_cols[i % 5]:
            # Floor 20: typed widths render exactly; only drag-resize is
            # clamped higher (50) by the grid frontend.
            widths[key] = st.number_input(
                COLUMN_LABELS.get(key, key), min_value=20, max_value=800,
                step=5, value=saved_widths[key], key="ofb_w_%s" % key)
    b1, b2 = st.columns(2)
    if b1.button("💾 Save layout for everyone", key="ofb_layout_save"):
        if app_settings.set_value(app_settings.ORDER_GRID_LAYOUT,
                                  json.dumps(widths), user["name"]):
            st.success("Saved to the main record — this is now every user's "
                       "layout.")
        else:
            st.error("Could not write the Settings tab — the layout below is "
                     "a preview for this session only.")
    b2.button("↩ Back to defaults", key="ofb_layout_reset",
              on_click=_reset_widths)

COLUMN_CONFIG = {
    "include": st.column_config.CheckboxColumn(" ", default=True,
                                               width=widths["include"],
                                               help="Untick to leave this part out."),
    "Part ID": st.column_config.TextColumn(disabled=True,
                                           width=widths["Part ID"]),
    "Part Name": st.column_config.TextColumn(disabled=True,
                                             width=widths["Part Name"]),
    # Shown so the computed Order qty can be checked against the BOM at a
    # glance.
    "BOM Qty": st.column_config.NumberColumn("BOM", disabled=True,
                                             width=widths["BOM Qty"],
                                             format="%d",
                                             help="Per assembly, from the BOM."),
    "Qty": st.column_config.NumberColumn("Order", min_value=0, step=1,
                                         width=widths["Qty"], format="%d",
                                         help="Quantity to order."),
    "Recipient": st.column_config.TextColumn(width=widths["Recipient"],
                                             help="Who receives this part."),
    "Priority": st.column_config.SelectboxColumn(options=["Normal", "URGENT"],
                                                 width=widths["Priority"]),
    "Material": st.column_config.TextColumn(width=widths["Material"]),
    "Spec": st.column_config.TextColumn(width=widths["Spec"]),
    "ETA": st.column_config.DateColumn(width=widths["ETA"],
                                       format="DD MMM YYYY",
                                       help="Pre-filled from the ETA in "
                                            "section 1; editable per part."),
    "Notes": st.column_config.TextColumn(width=widths["Notes"]),
}

# The editors are keyed on the §1 inputs plus a nonce, so changing units or
# the default ETA rebuilds the tables with re-seeded rows, and a tick-all
# click can force a rebuild that keeps the rows' edits. Streamlit keeps
# widget state against the key, and without this the grid would keep showing
# the values it was first drawn with.
# (nonce and _signature are initialised above the Agent Entry box,
# which needs them on its Apply click.)


def _eta_cell(value):
    """An ETA value from editor state, as something a DateColumn can seed.
    Committed-but-unrendered edits arrive as ISO strings."""
    if isinstance(value, str):
        try:
            return pd.to_datetime(value).date()
        except (ValueError, TypeError):
            return None
    return value


def _tick_group(type_name: str, box_key: str) -> None:
    """Tick or untick every row of one group, keeping EVERY group's edits.

    st.data_editor state cannot be written from Python, so the change is
    applied by re-seeding, and the nonce bump re-draws every grid — so the
    override must carry the current rows of ALL groups, not just the clicked
    one, or setting one group silently resets the others (Hamid caught
    exactly that: deselecting one group re-selected the rest). Each group's
    own widget state is merged on top of its last-drawn rows, because an
    edit committed instants before this click has reached that state but not
    yet a finished run.
    """
    ticked = bool(st.session_state.get(box_key))
    frames = st.session_state.get("ofb_frames") or {}
    seed_rows = {}
    for group, blob in frames.get("groups", {}).items():
        state = st.session_state.get(blob.get("key", ""))
        edited = state.get("edited_rows", {}) if isinstance(state, dict) else {}
        for i, rec in enumerate(blob.get("rows", [])):
            rec = dict(rec)
            for k in (i, str(i)):
                if k in edited and isinstance(edited[k], dict):
                    rec.update(edited[k])
            rec["ETA"] = _eta_cell(rec.get("ETA"))
            if group == type_name:
                rec["include"] = ticked
            seed_rows[str(rec.get("Part ID", ""))] = rec
    st.session_state["ofb_seed"] = {"sig": frames.get("sig", ""),
                                    "rows": seed_rows}
    st.session_state["ofb_nonce"] += 1


def _group_all_ticked(editor_key: str, subset) -> bool:
    """Whether every row of a group is currently ticked, edits included —
    read from the editor's own widget state so the mirror has no lag."""
    state = st.session_state.get(editor_key)
    edited = state.get("edited_rows", {}) if isinstance(state, dict) else {}
    for i, row in enumerate(subset):
        cell = None
        for k in (i, str(i)):
            if k in edited and "include" in edited[k]:
                cell = edited[k]["include"]
        if not (row["include"] if cell is None else cell):
            return False
    return True

edited_by_type = {}
for type_name in types:
    subset = [r for r in rows if r["Type"] == type_name]
    slot = project_colors.PALETTE[types.index(type_name) % len(project_colors.PALETTE)]
    editor_key = "ofb_grid_%s_%s" % (type_name, _signature)
    # The grid header is a canvas and cannot hold a widget, so the group's
    # tick-all box leads its title row instead — leftmost in the coloured
    # title bar (Hamid), which also keeps it clear of the grid's hover
    # toolbar on the right. Its key carries the current all-ticked state: when that
    # changes, the box is re-seeded to match, which is what keeps it
    # mirroring the rows.
    tick, head = st.columns([0.5, 11], vertical_alignment="center",
                            gap="small")
    with tick:
        all_now = _group_all_ticked(editor_key, subset)
        box_key = "ofb_all_%s_%s_%s" % (type_name, _signature, all_now)
        st.checkbox("tick all", value=all_now, key=box_key,
                    label_visibility="collapsed",
                    on_change=_tick_group,
                    args=(type_name, box_key),
                    help="Tick or untick every part in this group.")
    with head:
        st.markdown(
            '<div style="background:%s26;border-left:5px solid %s;padding:4px 10px;'
            'border-radius:4px;font-weight:600;">%s '
            '<span style="font-weight:400;opacity:.7;">— %d part(s)</span></div>'
            % (slot["hex"], slot["hex"], type_name, len(subset)),
            unsafe_allow_html=True)
    # Fills the page width exactly — the pinned widths act as proportions.
    # Height fits every row: tables never scroll inside themselves (Hamid).
    edited_by_type[type_name] = st.data_editor(
        pd.DataFrame(subset)[VISIBLE],
        column_config=COLUMN_CONFIG, hide_index=True,
        use_container_width=True, num_rows="fixed",
        height=table_height(len(subset)),
        key=editor_key,
    )

# Every group's rows as last drawn, with its editor key — what a tick-all
# click builds the persistent seed from.
st.session_state["ofb_frames"] = {
    "sig": _base_sig,
    "groups": {t: {"key": "ofb_grid_%s_%s" % (t, _signature),
                   "rows": f.to_dict("records")}
               for t, f in edited_by_type.items()}}

# What the user actually chose, read back from each editor.
chosen = []
by_code = {r["Part ID"]: r for r in rows}
for type_name, frame in edited_by_type.items():
    for rec in frame.to_dict("records"):
        if not bool(rec.get("include")):
            continue
        source = by_code.get(rec["Part ID"], {})
        chosen.append({
            "m_code": rec["Part ID"],
            "part_name": rec["Part Name"],
            "process": type_name,          # the BOM's Type is the category
            "quantity": _int(rec.get("Qty")),
            "material": _text(rec.get("Material")),
            "tolerances": _text(rec.get("Spec")),
            "notes": _text(rec.get("Notes")),
            "recipient": _text(rec.get("Recipient")),
            # Location was dropped from the grid (17 Aug): the receipt row's
            # location stays blank until the part is actually received.
            "priority": _text(rec.get("Priority")) or "Normal",
            "eta": _eta_str(rec.get("ETA")),
            "version": source.get("_version", ""),
            "drive_file_link": source.get("_cad", ""),
            "has_tab": source.get("_has_tab", False),
        })

st.markdown("**%d of %d parts selected.**" % (len(chosen), len(rows)))

# ============================================================
# 3. Submit
# ============================================================
st.subheader("3. Submit")

problems = []
if not chosen:
    problems.append("No parts are ticked.")
missing_recipient = [c["m_code"] for c in chosen if not c["recipient"]]
if missing_recipient:
    problems.append("Recipient is empty on %d row(s): %s"
                    % (len(missing_recipient),
                       ", ".join("`%s`" % c for c in missing_recipient[:8])))
bad_qty = [c["m_code"] for c in chosen if c["quantity"] < 1]
if bad_qty:
    problems.append("Quantity must be at least 1: %s"
                    % ", ".join("`%s`" % c for c in bad_qty[:8]))
now_open = [c["m_code"] for c in chosen if c["m_code"] in open_codes]
if now_open:
    problems.append("Already on order (raised since this page loaded?): %s"
                    % ", ".join("`%s`" % c for c in now_open[:8]))
no_tab = [c["m_code"] for c in chosen if not c["has_tab"]]
if no_tab:
    problems.append("No part tab for: %s — build the record from the BOM first."
                    % ", ".join("`%s`" % c for c in no_tab[:8]))

for p in problems:
    st.error(p)

# Save the selection as a draft for someone ELSE to submit — the checks
# above may still list problems; a draft is allowed to be unfinished, that
# is what makes it a draft.
d1, d2 = st.columns([2, 1.2], vertical_alignment="bottom")
with d1:
    draft_name = st.text_input(
        "Save as draft (name)", key="ofb_draft_name",
        value=st.session_state.get("ofb_loaded_draft", ""),
        placeholder="e.g. T2 top-up — for PM review")
with d2:
    if st.button("💾 Save draft", key="ofb_draft_save",
                 use_container_width=True):
        _why = order_drafts.save_draft(
            project, draft_name,
            user.get("email", "") or user.get("name", ""),
            units, build_tag.strip(),
            [{"part": c["m_code"], "qty": c["quantity"],
              "recipient": c["recipient"], "eta": c["eta"],
              "priority": c["priority"], "notes": c["notes"]}
             for c in chosen])
        if _why:
            st.error(_why)
        else:
            st.success("Draft **%s** saved — %d part(s). Anyone can load it "
                       "from 📂 at the top of this page."
                       % (draft_name.strip(), len(chosen)))

confirmed = st.checkbox(
    "I have reviewed all %d order(s) — they will be filed to %s's record"
    % (len(chosen), project),
    key="ofb_confirm", disabled=bool(problems))
if st.button("📤 Submit %d order(s)" % len(chosen), type="primary",
             disabled=bool(problems) or not confirmed, key="ofb_submit"):
    from utils import bulk_orders

    with st.spinner("Writing %d order(s)…" % len(chosen)):
        result = bulk_orders.submit(
            project=project, record_id=record_id, user=user, rows=chosen,
            ordered_from="", build=build_tag.strip(),
        )

    if result["orders"]:
        st.success("Created %d order(s) in the main record." % len(result["orders"]))
    if result["filed"]:
        st.success("Recorded %d order(s) against their part tabs." % len(result["filed"]))
    for message in result["errors"]:
        st.error(message)
    if result["orders"] and st.session_state.get("ofb_loaded_draft"):
        _done = st.session_state.pop("ofb_loaded_draft")
        if not order_drafts.delete_draft(project, _done):
            st.info("Draft **%s** cleared — it became these orders." % _done)
    if result["orders"]:
        # Clear the editors, or they replay the old ticks over the next batch.
        for type_name in types:
            st.session_state.pop("ofb_grid_%s_%s" % (type_name, _signature), None)
        st.session_state.pop("ofb_frames", None)
        st.session_state.pop("ofb_seed", None)
        st.session_state["ofb_nonce"] += 1
        # The Overview is derived from the part tabs — recompute so the new
        # raise lines show there immediately.
        from utils import record_builder

        ov = record_builder.write_overview(
            project, user.get("email", "") or user.get("name", ""),
            sheet_id=record_id, replace=True)
        if ov.get("problem"):
            st.warning("Overview not refreshed: %s" % ov["problem"])
        st.info("See them on **My Orders**.")

# ------------------------------------------------------------
# Kill header-click sorting on the part grids. Streamlit 1.38 has no switch
# for it — only num_rows="dynamic" turns it off, and that adds add/delete-row
# UI. So a same-origin script swallows pointer events over the header band:
# glide draws the header exactly one row-pitch tall (headerHeight == rowHeight
# in the JS bundle; 35 CSS px — measure in CSS units, screenshots lie when
# the window is scaled). Sorting mid-review makes rows jump
# under the reviewer. Drag-to-resize is blocked too (18 Aug, Hamid): drags
# were temporary to one browser and clamped to ≥50px by the frontend, while
# the layout panel goes to 20 and saves for everyone — one width control,
# not two that disagree. Scoped by
# pathname — the app is a single page in the browser, so the listeners
# survive navigation and must stand down on every other page. The handler is
# kept on the parent window so each rerun replaces it instead of stacking.
components.html("""<script>
(function () {
  var p = window.parent;
  var types = ["pointerdown", "pointerup", "mousedown", "mouseup", "click",
               "dblclick"];
  if (p.__ofb_sort_fn) {
    types.forEach(function (t) {
      p.document.removeEventListener(t, p.__ofb_sort_fn, true);
    });
  }
  var HEADER_PX = 35;
  function swallow(e) {
    if (p.location.pathname.indexOf("order_from_bom") === -1) return;
    if (!e.target || !e.target.closest) return;
    var grid = e.target.closest('[data-testid="stDataFrame"]');
    if (!grid) return;
    var canvas = grid.querySelector("canvas");
    if (!canvas) return;
    var rel = e.clientY - canvas.getBoundingClientRect().top;
    if (rel >= 0 && rel < HEADER_PX) {
      e.stopPropagation();
      e.preventDefault();
    }
  }
  p.__ofb_sort_fn = swallow;
  types.forEach(function (t) {
    p.document.addEventListener(t, swallow, true);
  });
})();
</script>""", height=0)
