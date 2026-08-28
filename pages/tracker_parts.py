"""Parts — the project tracker's Overview tab.

Same UI idiom as the rest of the app (and the original repo): collapsible
sections, one expander per item, details and tables inside the drawer.
"""
import pandas as pd
import streamlit as st


import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.auth import require_auth
from utils import bom_sheet, parts_model, parts_tracker, project_colors, project_registry
from utils.tracker_parse import is_selected, to_int
from utils.ui import require_project, table_height

require_auth()

MAX_SHOW = 15
ALL_PROJECTS = "All projects"

st.title("🧩 Parts")

# Controls first, on one line: search, scope, category filter and refresh.
# The project itself is the sidebar's one switcher; what stays here is the
# widening of scope to every project at once, which is a filter on the data
# rather than a change of which project the session is in.
require_project()
projects = list(project_registry.all_projects())
active_name, _ = project_registry.active()

# Labels stay short so none of them wrap to a second line — a wrapped label
# pushes its input down and the whole row stops lining up. The button sits on
# the same baseline via vertical_alignment rather than a spacer.
c1, c3, c4 = st.columns([4, 1.5, 1.3], vertical_alignment="bottom")
with c1:
    query = st.text_input("🔍 Search", placeholder="M-code, part, project, holder, note…")
with c3:
    category_box = st.empty()  # filled once the parts are loaded
with c4:
    if st.button("🔄 Refresh", use_container_width=True):
        for _sid in set(project_registry.all_projects().values()):
            parts_tracker.refresh(_sid)
        st.rerun()

# The scope is the sidebar's, not this page's: the "All projects" checkbox
# that used to live up there answered the same question the sidebar now
# answers for every page (Hamid, 21 Aug).
picked = ALL_PROJECTS if project_registry.is_all() else active_name

# Which sheets this page reads: one project, or every registered project.
registry = project_registry.all_projects()
sources = (list(registry.items()) if picked == ALL_PROJECTS
           else [(picked, registry.get(picked, ""))])
# Rendered after the data loads (below) so the "read …" stamp reports the read
# that just happened rather than the state before it.
project_line = st.empty()

# --- BOM build selector -------------------------------------------------
# A project with a BOM sheet gets its parts enriched from one build tab
# (T2, DVT T1, …). Selectable for a single project; "All projects" uses each
# project's default build (the tab that tracks CAD versions).
chosen_builds = {}
for _name, _ in sources:
    _bid = project_registry.bom_sheet(_name)
    if not _bid:
        continue
    _tabs = bom_sheet.build_tabs(_bid)
    if not _tabs:
        continue
    _default_build = bom_sheet.default_build(_bid)
    if picked != ALL_PROJECTS:
        _key = "build_choice_%s" % _name
        _saved_build = st.session_state.get(_key, _default_build)
        chosen_builds[_name] = st.selectbox(
            "BOM build", _tabs,
            index=_tabs.index(_saved_build) if _saved_build in _tabs else _tabs.index(_default_build),
            key=_key,
            help="Which build's BOM enriches the rows below (source: the "
                 "project's BOM sheet, read-only). Source: %s"
                 % bom_sheet.source_label(_bid))
    else:
        chosen_builds[_name] = _default_build

# {project: {mcode: unified record}} — identity/CAD/cost from the BOM,
# inventory from Lifecycle, stock derived from the BOM movement log.
bom_maps = {}
for _name in chosen_builds:
    bom_maps[_name] = {rec["mcode"]: rec
                       for rec in parts_model.unified_parts(_name, chosen_builds[_name])}

# --- Normalise once; every section below reads these rows ---
parts = []
for project_name, sheet_id in sources:
    for r in parts_tracker.fetch_overview(sheet_id):
        mcode = r.get("mcode", "")
        part_name = r.get("part_name", "")
        flags = parts_tracker.attention_flags(r)
        ordered = to_int(r.get("qty_ordered", ""))
        received = to_int(r.get("qty_received", ""))
        status = (r.get("status") or "").strip()

        # One-line answer to "where does this part stand?"
        if status:
            latest_status = status
        elif ordered and received < ordered:
            latest_status = "Awaiting %d of %d" % (ordered - received, ordered)
        elif flags:
            latest_status = flags[0]
        else:
            latest_status = "—"

        p = {
            "project": project_name, "sheet_id": sheet_id,
            "mcode": mcode, "part_name": part_name,
            "category": r.get("category", ""), "version": r.get("version", ""),
            # The Overview row IS the part's currently-selected history row —
            # order/sample id included — so the latest order can be shown
            # without opening every part's own tab (one API call each).
            "order_id": r.get("order_id", ""), "qc": (r.get("status") or "").strip(),
            "status": latest_status, "ordered": ordered, "received": received,
            "on_hand": to_int(r.get("qty_on_hand", "")), "eta": r.get("eta", ""),
            "location": r.get("location", ""), "holder": r.get("holder", ""),
            "notes": r.get("notes", ""), "flags": flags, "bom": None,
        }
        # BOM enrichment: identity/CAD/cost/owner for the selected build.
        u = bom_maps.get(project_name, {}).get(parts_model.normalise_code(mcode))
        if u is not None and u["_sources"].get("identity") == "BOM":
            p["bom"] = u
            if u.get("stale_cad"):
                p["flags"] = p["flags"] + ["Ordered CAD %s ≠ latest %s"
                                           % (u.get("last_ordered_cad", "?"),
                                              u.get("most_recent_cad", "?"))]
        parts.append(p)

with category_box:
    categories = st.multiselect("Category",
                                sorted({p["category"] for p in parts if p["category"]}))

with project_line:
    if picked == ALL_PROJECTS:
        st.markdown(
            "**Projects** — " + " ".join(project_colors.badge_html(name, sheet_id=sid)
                                         for name, sid in sources),
            unsafe_allow_html=True)
    else:
        st.markdown(
            "**Project** — %s" % project_colors.badge_html(
                picked, "Source: %s" % parts_tracker.source_label(sources[0][1]),
                sheet_id=sources[0][1]),
            unsafe_allow_html=True)

if not parts:
    st.info(
        "No tracker data for this project. The app reads the **Overview**, "
        "**Movement Log** and per-part tabs of the project's Google Sheet — "
        "share the sheet with the service account, or build an offline "
        "snapshot with `tools/make_snapshot.py`."
    )
    st.stop()

# --- Summary numbers, with the remaining two filters on the same line ---
# Metrics are narrow (short labels, small numbers), the selects take the
# space they need, and everything is centred against the row so the heights
# read as one band. No "needs attention only" toggle: that's the first tab.
m1, m2, m3, m4, f1, f2 = st.columns([1, 1.3, 1.1, 1.1, 1.8, 1.8],
                                    vertical_alignment="center")
m1.metric("Parts", len(parts))
m2.metric("On Hand", "{:,}".format(sum(p["on_hand"] for p in parts)))
m3.metric("Attention", sum(1 for p in parts if p["flags"]))
m4.metric("Awaiting", sum(1 for p in parts if p["ordered"] and p["received"] < p["ordered"]))
with f1:
    holders = st.multiselect("Holder", sorted({p["holder"] for p in parts if p["holder"]}))
with f2:
    locations = st.multiselect("Location", sorted({p["location"] for p in parts if p["location"]}))


def _matches(p: dict) -> bool:
    if categories and p["category"] not in categories:
        return False
    if holders and p["holder"] not in holders:
        return False
    if locations and p["location"] not in locations:
        return False
    if query:
        # The bom record is a nested dict full of bookkeeping keys — search
        # its display fields, not its internals.
        u = p.get("bom") or {}
        blob = " ".join(
            [str(v) for k, v in p.items() if k not in ("latest", "bom")]
            + [str(u.get(k, "")) for k in ("material", "spec", "owner",
                                           "order_status", "cad_link")]
        ).lower()
        if query.lower() not in blob:
            return False
    return True


view = [p for p in parts if _matches(p)]
attention = [p for p in view if p["flags"]]
on_track = [p for p in view if not p["flags"]]
# Shows the project on each row once more than one is in view.
multi_project = len({p["project"] for p in view}) > 1

st.markdown(f"**{len(view)} parts** shown")
st.markdown("---")


def _part_drawer(p: dict, idx: int):
    """One part: header line, summary, latest history row, actions.

    The project always leads the header — M-codes are only unique within a
    project, so a row without it is ambiguous the moment a second project
    exists, and hiding it when only one is loaded means the line silently
    changes shape.
    """
    flag_icon = "⚠️" if p["flags"] else "✅"
    header = "%s %s | **%s** — %s | `%s` | %s | → %s" % (
        flag_icon, project_colors.tag(p["project"]),
        p["mcode"] or "?", p["part_name"] or "N/A",
        p["version"] or "no version", p["status"], p["location"] or "location TBC")
    key = "%s_%s_%d" % (p["project"], p["mcode"], idx)

    with st.expander(header, expanded=False):
        # A summary only. No movement matching here — that's interpretation,
        # and it belongs on Part Detail (per part) or Movements (the log).
        st.markdown("**Project** — %s"
                    % project_colors.badge_html(p["project"], sheet_id=p["sheet_id"]),
                    unsafe_allow_html=True)

        # The current order, stated plainly: it's the thing people came for.
        st.markdown("**Order** — %s · version `%s` · **%s of %s received**%s"
                    % (p["order_id"] or "no order recorded",
                       p["version"] or "none selected",
                       p["received"], p["ordered"] or "?",
                       " · ETA %s" % p["eta"] if p["eta"] else ""))
        st.markdown("**QC** — %s" % (p["qc"] or "not recorded"))
        st.markdown("**Where** — %s · held by %s · %s on hand"
                    % (p["location"] or "unconfirmed", p["holder"] or "unassigned",
                       p["on_hand"]))
        if p["notes"]:
            # Full-weight text, not a caption: these notes carry the shipping
            # and handover facts ("shipped on 7/16 SF1572075868095").
            st.markdown("**Note** — %s" % p["notes"])
        if p["category"]:
            st.markdown("**Category** — %s" % p["category"])

        # The BOM's half of the story, when this project has a BOM sheet.
        u = p.get("bom")
        if u:
            bom_bits = []
            if u.get("material"):
                bom_bits.append(u["material"].replace("\n", " "))
            if u.get("spec"):
                bom_bits.append(u["spec"].replace("\n", " "))
            if u.get("owner"):
                bom_bits.append("owner %s" % u["owner"])
            if u.get("lead_time"):
                bom_bits.append("lead time %s" % u["lead_time"])
            if u.get("unit_cost"):
                bom_bits.append("%s CNY/u" % u["unit_cost"])
            st.markdown("**BOM** — %s" % (" · ".join(bom_bits) or "no details"))
            cad_last, cad_now = u.get("last_ordered_cad", ""), u.get("most_recent_cad", "")
            if cad_last or cad_now:
                mark = " ⚠️ **ordered against an older CAD**" if u.get("stale_cad") else ""
                st.markdown("**CAD** — last ordered `%s` · most recent `%s`%s"
                            % (cad_last or "?", cad_now or "?", mark))
            if u.get("cad_link"):
                st.markdown("**Design file** — %s" % u["cad_link"])
            if u.get("order_status") or u.get("status_norm"):
                st.markdown("**BOM status** — %s (%s)"
                            % (u.get("order_status", "") or "—",
                               u.get("status_norm", "")))

        if p["flags"]:
            st.warning(" · ".join(p["flags"]))

        # The newest row of the part's own tab — the same shape as the History
        # table on Part Detail, just the last line of it. All part tabs come
        # from one batched read, so this costs nothing per drawer.
        history = parts_tracker.fetch_part(p["mcode"], p["sheet_id"]).get("history", [])
        if history:
            st.markdown("**Latest history row**")
            last = history[-1]
            st.dataframe(pd.DataFrame([{
                "Date": last.get("date", ""),
                "Version": last.get("version", ""),
                "Order / Sample": last.get("order_id", ""),
                "Type": last.get("type", ""),
                "Vendor / Source": last.get("vendor", ""),
                "Ordered": last.get("qty_ordered", ""),
                "Received": last.get("qty_received", ""),
                "Status": last.get("status", ""),
                "Location": last.get("location", ""),
                "Holder": last.get("holder", ""),
                "Selected": "✅" if is_selected(last) else "",
            }]), use_container_width=True, hide_index=True)

        st.caption("Full version history is on **Part Detail**; the movement "
                   "rows behind it are on **Movements**.")

        st.markdown("---")
        bc1, bc2 = st.columns(2)
        with bc1:
            if st.button("🔍 Full history", key="hist_%s" % key, use_container_width=True):
                # Both destinations read the session's project, so follow the
                # part into its own project first.
                project_registry.set_active(p["project"])
                st.session_state["tracker_part"] = p["mcode"]
                st.switch_page("pages/tracker_part_detail.py")
        with bc2:
            if st.button("🔩 Order this part", key="ord_%s" % key, use_container_width=True):
                project_registry.set_active(p["project"])
                prefill = {
                    "part_name": p["part_name"], "m_code": p["mcode"],
                    "version": p["version"],
                }
                # BOM prefill: material/spec and the design-file link, so the
                # order carries the BOM's CAD reference without re-uploading.
                u = p.get("bom")
                if u:
                    if u.get("material"):
                        prefill["material"] = u["material"].replace("\n", " ")
                    if u.get("spec"):
                        prefill["tolerances"] = u["spec"].replace("\n", " ")
                    if u.get("cad_link") or u.get("drawing"):
                        prefill["drive_file_link"] = u.get("cad_link") or u.get("drawing")
                    if not prefill.get("version"):
                        prefill["version"] = u.get("most_recent_cad", "")
                st.session_state["part_prefill"] = prefill
                st.switch_page("pages/ee_submit_order.py")


def _section(items: list, key: str, empty: str):
    """A list of part drawers. Sections are tabs, not expanders — Streamlit
    forbids nesting an expander inside another one."""
    if not items:
        st.success(empty)
        return
    shown = items
    if len(items) > MAX_SHOW:
        show_all = st.checkbox("Show all %d (showing first %d)" % (len(items), MAX_SHOW),
                               key="show_all_%s" % key)
        shown = items if show_all else items[:MAX_SHOW]
    for idx, p in enumerate(shown):
        _part_drawer(p, idx)


# Parts that didn't bridge between the BOM and the tracker — first-class,
# never swallowed: a silent merge that drops parts is exactly the
# "something got lost" failure this app exists to prevent.
unmatched_by_project = {name: parts_model.unmatched(name, chosen_builds[name])
                        for name in chosen_builds}
n_unmatched = sum(len(u["bom_only"]) + len(u["tracker_only"]) + len(u["approximate"])
                  for u in unmatched_by_project.values())

tab_attention, tab_ontrack, tab_table, tab_unmatched, tab_rollups = st.tabs([
    "🚨 Needs Attention (%d)" % len(attention),
    "✅ On Track (%d)" % len(on_track),
    "📋 Table View (%d)" % len(view),
    "🔗 Unmatched (%d)" % n_unmatched,
    "📊 Rollups",
])

with tab_attention:
    _section(attention, "attention",
             "Nothing flagged — every part has a version, a location and no QC failure.")

with tab_ontrack:
    _section(on_track, "ontrack", "No parts in this group.")

with tab_table:
    # The shared linked_table: M-Code jumps to Part Detail in this tab, rows
    # keep the page's red/green attention colours.
    _heads = ["Project", "M-Code", "Part", "Category", "Version",
              "Latest status", "Ordered", "Received", "On hand", "ETA",
              "Location", "Holder", "Attention", "Notes"]
    if view:
        from utils.ui import esc, linked_table, part_link
        _cells = [[
            esc(project_colors.tag(p["project"])),
            part_link(p["project"], p["mcode"]),
            esc(p["part_name"]), esc(p["category"]), esc(p["version"]),
            esc(p["status"]), esc(p["ordered"]), esc(p["received"]),
            esc(p["on_hand"]), esc(p["eta"]), esc(p["location"]),
            esc(p["holder"]), esc(" · ".join(p["flags"])), esc(p["notes"]),
        ] for p in view]
        _bg = ["#f8d7da" if p["flags"] else "#d4edda" for p in view]
        linked_table(_heads, _cells, _bg)
    else:
        st.info("No parts match the current filters.")

with tab_unmatched:
    if not chosen_builds:
        st.info("No BOM sheet registered for this project, so there is "
                "nothing to bridge. An admin can add the BOM sheet on "
                "**Tools → Projects**.")
    elif n_unmatched == 0:
        st.success("Every part bridges cleanly between the BOM build and the "
                   "parts tracker — nothing lost on either side.")
    for _name, um in unmatched_by_project.items():
        if not (um["bom_only"] or um["tracker_only"] or um["approximate"]):
            continue
        st.markdown("**%s** — build `%s`" % (project_colors.tag(_name),
                                             chosen_builds[_name]))
        if um["bom_only"]:
            st.markdown("**In the BOM, not in the tracker** — these parts "
                        "have no per-part tab yet:")
            st.dataframe(pd.DataFrame([{
                "M-Code": q["mcode"], "Part": q.get("part_name", ""),
                "BOM status": q.get("order_status", ""),
                "Owner": q.get("owner", ""),
            } for q in um["bom_only"]]), use_container_width=True,
                hide_index=True, height=table_height(len(um["bom_only"])))
        if um["tracker_only"]:
            st.markdown("**In the tracker, not in this BOM build**:")
            st.dataframe(pd.DataFrame([{
                "M-Code": q["mcode"], "Part": q.get("part_name", ""),
                "Holder": q.get("holder", ""),
                "Tracker status": q.get("tracker_status", ""),
            } for q in um["tracker_only"]]), use_container_width=True,
                hide_index=True, height=table_height(len(um["tracker_only"])))
        if um["approximate"]:
            st.warning("Matched on the base M-code only (e.g. BOM `M101` vs "
                       "tracker `M101a`) — treat as approximate: " +
                       ", ".join(q["mcode"] for q in um["approximate"]))

with tab_rollups:
    df = pd.DataFrame([{"Project": p["project"], "Location": p["location"],
                        "Holder": p["holder"], "On hand": p["on_hand"],
                        "Flagged": 1 if p["flags"] else 0} for p in view])

    if multi_project:
        st.markdown("**By project**")
        by_project = df.groupby("Project").agg(
            Parts=("On hand", "size"),
            **{"On hand": ("On hand", "sum"), "Needs attention": ("Flagged", "sum")}
        ).sort_values("Parts", ascending=False).reset_index()
        st.dataframe(by_project, use_container_width=True, hide_index=True,
                     height=table_height(len(by_project)))
        st.markdown("---")

    r1, r2 = st.columns(2)
    with r1:
        st.markdown("**By location**")
        by_loc_df = (df.groupby("Location")["On hand"].sum()
                     .sort_values(ascending=False).reset_index())
        st.dataframe(by_loc_df, use_container_width=True, hide_index=True,
                     height=table_height(len(by_loc_df)))
    with r2:
        st.markdown("**By holder**")
        by_holder_df = (df.groupby("Holder")["On hand"].sum()
                        .sort_values(ascending=False).reset_index())
        st.dataframe(by_holder_df, use_container_width=True, hide_index=True,
                     height=table_height(len(by_holder_df)))
