"""Projects — browse Drive, tie each project folder to its sheets, register it.

Setup work, done once per project by an admin, kept off the order form where it
used to sit: registering a project and raising an order are different jobs on
different clocks, and mixing them made it unclear where projects came from.

The list itself is the `Projects` tab of the main record — this page is a view
onto that tab and onto Drive, never a second copy of either.

Shape of the world this assumes (Hamid, 14 Aug):

    MECH orderTracking tool/         <- the Drive root, set once below
    ├── Projects/<name>/             <- one folder per project: its BOM + Record
    ├── Template/                    <- TEMPLATE - Project Record, copied by hand
    ├── Main database/               <- MECH Main Record
    └── Archived/

Engineering files are NOT here. CAD and drawings stay wherever the BOM's own
per-part `Drawing` / `CAD` / `Live CAD Link` columns point; the app only needs
the links, so it never browses for them.
"""
import json
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd

from utils.auth import require_role
from utils.ui import table_height
from utils import (app_settings, drive_browser, project_colors,
                   project_registry, record_builder)
from config import SERVICE_ACCOUNT_FILE

user = require_role("admin")

st.title("🗂 Projects")


def _service_account_email() -> str:
    """The account sheets must be shared with — read from the key file."""
    try:
        with open(SERVICE_ACCOUNT_FILE) as fh:
            return json.load(fh).get("client_email", "the service account")
    except Exception:
        return "the service account"


SA_EMAIL = _service_account_email()


def _guess_tab(tabs, word: str) -> str:
    """First tab whose name mentions `word` — how the cost tab is pre-picked."""
    for t in tabs:
        if word in t.strip().lower():
            return t
    return ""


def _validate(name, bom_id, rec_id):
    """Problems with a project's sheets, checked BEFORE registering so a bad
    link fails here and not on someone's first order."""
    from utils.google_client import get_spreadsheet

    problems = []
    if not (name or "").strip():
        problems.append("Give the project a name.")
    if not rec_id:
        problems.append("No project record sheet.")
    for label, sid in (("BOM sheet", bom_id), ("Project record", rec_id)):
        if not sid:
            continue
        try:
            ss = get_spreadsheet(sid)
        except Exception as exc:
            problems.append("%s: cannot open it (%s). Is it shared with `%s`?"
                            % (label, str(exc) or "permission denied", SA_EMAIL))
            continue
        if ss is None:
            problems.append("%s: no Google credentials on this instance." % label)
    return problems


registry = project_registry.all_sheets()

# ============================================================
# 1. Drive
# ============================================================
st.subheader("Drive")

root_saved = app_settings.get(app_settings.DRIVE_ROOT_FOLDER_ID)
rc1, rc2 = st.columns([4, 1.2], vertical_alignment="bottom")
with rc1:
    root_input = st.text_input(
        "Root folder link or ID", value=root_saved, key="root_folder",
        placeholder="https://drive.google.com/drive/folders/…",
        help="The folder holding the tool's own files. Projects live in its "
             "`%s` subfolder." % app_settings.get(app_settings.PROJECTS_SUBFOLDER))
with rc2:
    if st.button("💾 Save root", use_container_width=True):
        folder_id = project_registry.folder_id_from(root_input)
        if not folder_id:
            st.error("Paste a Drive folder link or ID.")
        elif not drive_browser.folder_name(folder_id):
            st.error("Cannot open that folder. Share it with `%s`." % SA_EMAIL)
        else:
            app_settings.set_value(app_settings.DRIVE_ROOT_FOLDER_ID,
                                   folder_id, user["name"])
            st.toast("Saved.")
            st.rerun()

projects_root, problem = drive_browser.projects_folder()
if problem:
    st.warning(problem)
else:
    st.caption("Browsing **%s / %s** — %s"
               % (drive_browser.folder_name(root_saved) or "root",
                  app_settings.get(app_settings.PROJECTS_SUBFOLDER),
                  project_registry.folder_link(projects_root, "open in Drive ↗")))

# ============================================================
# 2. Project folders in Drive
# ============================================================
if projects_root:
    st.markdown("---")
    fh1, fh2 = st.columns([4, 1.2], vertical_alignment="bottom")
    with fh2:
        if st.button("🔄 Rescan", use_container_width=True):
            project_registry.refresh()
            app_settings.refresh()
            st.rerun()

    folders = drive_browser.subfolders(projects_root)
    by_folder_id = {s["folder"]: name for name, s in registry.items() if s["folder"]}
    registered_names = set(registry)

    with fh1:
        st.markdown("**Project folders** — %d found. Each should hold that "
                    "project's BOM and its Record." % len(folders))

    if not folders:
        st.info("No project folders yet. Create one per project inside `%s/`, "
                "put its BOM sheet in it, and rescan."
                % app_settings.get(app_settings.PROJECTS_SUBFOLDER))

    for folder in folders:
        fid, fname = folder["id"], folder["name"]
        linked_as = by_folder_id.get(fid, "")
        # A folder whose name matches a registered project is almost certainly
        # that project, even before anyone links it.
        guess = linked_as or (fname if fname in registered_names else "")
        found = drive_browser.classify(fid, fname)

        bom_id = found["bom"]
        rec_id = found["record"]
        names = {f["id"]: f["name"] for f in found["sheets"]}

        # The project IS the folder name — no name field to keep in step with
        # it. Both sheets are named in the title, so the body is one row of
        # choices and nothing else.
        proj_name = fname
        state = ("linked" if linked_as else
                 "registered, not linked to this folder" if guess else
                 "not registered")
        title = "📁 **%s**: %s  ·  BOM: %s  ·  Record: %s" % (
            fname, state,
            project_registry.sheet_link(bom_id, names.get(bom_id, "open"))
            if bom_id else "not found",
            project_registry.sheet_link(rec_id, names.get(rec_id, "open"))
            if rec_id else "not found")

        with st.expander(title, expanded=not linked_as):
            known_now = registry.get(proj_name, {})
            tabs = drive_browser.tabs_of(bom_id) if bom_id else []
            # The cost estimate normally lives in the BOM file itself (the pilot's
            # BOM has a `Cost Estimate` tab), so its tabs are the options unless
            # a separate cost sheet has been linked.
            cost_src = known_now.get("cost") or bom_id
            cost_tabs = (tabs if cost_src == bom_id
                         else drive_browser.tabs_of(cost_src))

            NONE = "— none —"
            bom_default = known_now.get("bom_tab") or drive_browser.pick_bom_tab(tabs)
            cost_default = known_now.get("cost_tab") or _guess_tab(cost_tabs, "cost")

            c1, c2, c3, c4 = st.columns([2, 2, 2, 1.6],
                                        vertical_alignment="bottom")
            with c1:
                tab_choice = st.selectbox(
                    "BOM tab", tabs or ["—"],
                    index=tabs.index(bom_default) if bom_default in tabs else 0,
                    key="tab_%s" % fid, disabled=not tabs,
                    help="Which tab in the BOM file holds the parts. The app "
                         "picks one called `BOM` when there is one.")
            with c2:
                _opts = [NONE] + cost_tabs
                cost_choice = st.selectbox(
                    "Cost tab", _opts,
                    index=_opts.index(cost_default) if cost_default in _opts else 0,
                    key="cost_%s" % fid, disabled=not cost_tabs,
                    help="Which tab holds the cost estimate — normally a tab in "
                         "the BOM file. Link a separate sheet under *Registered "
                         "projects* if it lives elsewhere.")
            with c3:
                st.selectbox(
                    "PCB tab", ["— from PCB tool —"], index=0,
                    key="pcb_%s" % fid, disabled=True,
                    help="Comes from the PCB order tool. Wired up once the mech "
                         "workflow is finalised.")
            with c4:
                do_save = st.button(
                    "✅ Save", key="save_%s" % fid, type="primary",
                    use_container_width=True, disabled=not (bom_id and rec_id))
            if not tabs:
                tab_choice = known_now.get("bom_tab", "")
            cost_tab = "" if cost_choice == NONE else cost_choice

            # Anything blocking registration is said here, below the row, so the
            # healthy case stays exactly one row.
            if not rec_id:
                template = app_settings.get(app_settings.RECORD_TEMPLATE_ID)
                if drive_browser.can_create() and template:
                    if st.button("📄 Create record from template",
                                 key="mk_rec_%s" % fid):
                        ok, result = drive_browser.copy_file(
                            template, "%s Record" % fname, fid)
                        (st.success if ok else st.error)(
                            "Created." if ok else result)
                        if ok:
                            st.rerun()
                else:
                    st.caption(
                        "No record here. Copy **TEMPLATE – Project Record** into "
                        "this folder, rename it `%s Record`, then rescan — the "
                        "app cannot copy it (a service account owns no Drive "
                        "storage)." % fname)
            if bom_id and not tabs:
                st.caption("Cannot read the BOM file's tabs — is it shared with "
                           "`%s`?" % SA_EMAIL)

            if do_save:
                # A folder with no record must not blank a record already
                # registered — the scan not finding one means "not in this
                # folder", not "this project has none".
                known = registry.get(proj_name, {})
                save_rec = rec_id or known.get("tracker", "")
                problems = _validate(proj_name, bom_id, save_rec)
                # The name comes from the folder, so two folders of the same
                # name would silently overwrite each other's registration.
                # Refuse, and say which folder already holds the name.
                if known and known.get("folder") and known["folder"] != fid:
                    problems.append(
                        "**%s** is already registered from a different folder "
                        "(%s). Rename this folder, or remove that registration "
                        "under *Registered projects* — the project name is the "
                        "folder name, so two folders cannot share one."
                        % (proj_name,
                           project_registry.folder_link(known["folder"], "open ↗")))
                for p in problems:
                    st.error(p)
                if not problems:
                    fields = {"tracker": save_rec, "bom": bom_id,
                              "bom_tab": tab_choice, "folder": fid,
                              "cost_tab": cost_tab,
                              # A cost tab is only meaningful with the sheet it
                              # sits in, so the two are always written together.
                              "cost": (cost_src if cost_tab else "")}
                    if proj_name in registry:
                        ok, message = project_registry.update_project(
                            proj_name, fields, updated_by=user["name"])
                    else:
                        ok, message = project_registry.add_project(
                            proj_name, save_rec, bom_id,
                            registered_by=user["name"], **fields)
                    (st.success if ok else st.error)(message)
                    if ok:
                        st.rerun()

# ============================================================
# 3. Registered projects
# ============================================================
st.markdown("---")
st.subheader("Registered projects")

if not project_registry.source_is_live():
    st.error(
        "**Cannot read the project list.** It lives in the `Projects` tab of "
        "the main record, which this instance cannot open — check the Google "
        "credentials on the **Status** page."
    )
elif not registry:
    st.info("**Nothing registered yet.** Save a project folder above, or add "
            "one by hand below.")
else:
    active_name, _ = project_registry.active()
    for name, s in registry.items():
        with st.container(border=True):
            c1, c2 = st.columns([2, 3])
            with c1:
                st.markdown(project_colors.badge_html(name), unsafe_allow_html=True)
                if name == active_name:
                    st.caption("Active in this session")
                if s["folder"]:
                    st.caption(project_registry.folder_link(s["folder"], "folder ↗"))
            with c2:
                def _line(label, sheet_id, tab=""):
                    link = project_registry.sheet_link(sheet_id) or "_not linked_"
                    return "**%s:** %s%s" % (
                        label, link, "  ·  tab `%s`" % tab if tab else "")

                st.markdown("  \n".join([
                    _line("Record", s["tracker"]) if s["tracker"]
                    else "**Record:** _missing_",
                    _line("BOM", s["bom"], s["bom_tab"]),
                    _line("Cost estimate", s["cost"], s["cost_tab"]),
                    _line("PCB record", s["pcb"], s["pcb_tab"]),
                ]))

            # --- Build the record's part tabs from the BOM ------------------
            # Creating these by hand was the tool's biggest pain point. This is
            # deliberately its own action rather than a side effect of ordering:
            # a tab is created only from a BOM row, never from a typed code.
            with st.expander("🏗 Build record from BOM"):
                if st.button("🔍 Check what's missing", key="plan_%s" % name):
                    st.session_state["rb_plan_%s" % name] = record_builder.plan(name)

                _plan = st.session_state.get("rb_plan_%s" % name)
                if _plan is None:
                    st.caption(
                        "Creates one tab per BOM part in the project record — "
                        "Part ID, Part Name, Type, Material, Spec and the "
                        "history columns. Existing tabs are never touched, so "
                        "this is safe to run again.")
                elif _plan["problem"]:
                    st.error(_plan["problem"])
                else:
                    st.markdown("**%s**" % record_builder.summary(_plan))
                    if _plan["invalid"]:
                        st.warning("Cannot be used as tab names: "
                                   + "; ".join("`%s` (%s)" % (c, w)
                                               for c, w in _plan["invalid"]))
                    if _plan["duplicate"]:
                        st.warning(
                            "Repeated Part IDs — only the first of each becomes "
                            "a tab: " + ", ".join("`%s`" % d for d in _plan["duplicate"]))
                    if _plan["to_create"]:
                        st.dataframe(
                            pd.DataFrame([{"Part ID": p["title"],
                                           "Part Name": p["part_name"],
                                           "Type": p["type"],
                                           "Material": p["material"],
                                           "Spec": p["spec"]}
                                          for p in _plan["to_create"]]),
                            hide_index=True, use_container_width=True,
                            height=table_height(len(_plan["to_create"])))
                        st.caption("Nothing has been written yet.")
                        if st.button("🏗 Create %d tab(s)" % len(_plan["to_create"]),
                                     type="primary", key="build_%s" % name):
                            res = record_builder.build(name, created_by=user["name"])
                            if res["error"]:
                                st.error(res["error"])
                            if res["created"]:
                                st.success("Created %d tab(s): %s"
                                           % (len(res["created"]),
                                              ", ".join(res["created"][:8])
                                              + ("…" if len(res["created"]) > 8 else "")))
                            st.session_state.pop("rb_plan_%s" % name, None)
                            st.rerun()
                    elif _plan["movement_log"]:
                        if st.button("🏗 Create the Movement Log tab",
                                     type="primary", key="buildlog_%s" % name):
                            res = record_builder.build(name, created_by=user["name"])
                            (st.error if res["error"] else st.success)(
                                res["error"] or "Movement Log created.")
                            st.session_state.pop("rb_plan_%s" % name, None)
                            st.rerun()
                    else:
                        st.success("Every BOM part already has a tab.")

                # Overview is derived from the part tabs, so it is rebuilt
                # rather than edited — and rebuilding is a full-block write,
                # hence the explicit replace tick.
                st.markdown("---")
                st.caption(
                    "**Overview** is the summary the Parts page reads. It is "
                    "derived from the part tabs, so rebuild it after creating "
                    "tabs or logging events.")
                o1, o2 = st.columns([1, 2], vertical_alignment="bottom")
                with o1:
                    _replace = st.checkbox("Replace existing rows",
                                           key="ovr_replace_%s" % name)
                with o2:
                    if st.button("♻ Rebuild Overview", key="ovr_%s" % name):
                        res = record_builder.write_overview(
                            name, built_by=user["name"], replace=_replace)
                        if res["problem"]:
                            st.warning(res["problem"])
                        else:
                            # No st.rerun() here. It used to follow this line
                            # and it threw the result away: rerun restarts the
                            # script immediately, so the message never painted
                            # and the expanders snapped shut — the button
                            # looked dead while the rebuild had in fact run
                            # (Hamid, 19 Aug: "not responding to my click").
                            # write_overview already refreshed the caches, so
                            # there is nothing a rerun would fix.
                            st.success("Overview rebuilt — %d part(s) written "
                                       "from the part tabs." % res["rows"])

            with st.expander("Edit links"):
                e1, e2 = st.columns(2)
                with e1:
                    cost = st.text_input("Cost estimate sheet", value=s["cost"],
                                         key="cost_%s" % name,
                                         placeholder="link or ID")
                with e2:
                    pcb = st.text_input(
                        "PCB record sheet", value=s["pcb"], key="pcb_%s" % name,
                        placeholder="link or ID",
                        help="The PCB order sheet for this project — read-only "
                             "for now, kept so the two tools can be joined up.")
                if st.button("💾 Save links", key="savelinks_%s" % name):
                    ok, message = project_registry.update_project(
                        name, {"cost": cost, "pcb": pcb},
                        updated_by=user["name"])
                    (st.success if ok else st.error)(message)
                    if ok:
                        st.rerun()

                # Un-registering is a sheet write like any other, so it belongs
                # here rather than being done by hand in the Projects tab.
                st.markdown("---")
                st.caption(
                    "Removing **%s** deletes only its row in the main record's "
                    "index. Its BOM and record sheets are untouched, and its "
                    "orders keep the project name — re-registering restores it."
                    % name)
                confirm = st.checkbox("Yes, un-register %s" % name,
                                      key="rmok_%s" % name)
                if st.button("🗑 Remove from main record", key="rm_%s" % name,
                             disabled=not confirm):
                    ok, message = project_registry.remove_project(name)
                    (st.success if ok else st.error)(message)
                    if ok:
                        st.rerun()

# ============================================================
# 4. Register by hand
# ============================================================
with st.expander("➕ Register a project by hand (no Drive browsing)"):
    st.caption(
        "For a project whose sheets live outside the root folder. Share both "
        "with `%s` first, otherwise the app cannot read them." % SA_EMAIL)
    h1, h2 = st.columns(2)
    with h1:
        new_name = st.text_input("Project name *", key="np_name",
                                 placeholder="e.g. WW2 T1")
    with h2:
        new_bom = st.text_input("BOM sheet link or ID *", key="np_bom",
                                placeholder="https://docs.google.com/spreadsheets/d/…")
    new_rec = st.text_input(
        "Project record sheet link or ID *", key="np_rec",
        placeholder="https://docs.google.com/spreadsheets/d/…",
        help="Copy TEMPLATE – Project Record, rename it, share it, paste it "
             "here — service accounts have no Drive storage of their own, so "
             "the app cannot create one for you.")

    if st.button("🔗 Check & register", type="primary", key="np_add"):
        bom_id = project_registry.sheet_id_from(new_bom)
        rec_id = project_registry.sheet_id_from(new_rec)
        problems = _validate(new_name, bom_id, rec_id)
        for p in problems:
            st.error(p)
        if not problems:
            tab = drive_browser.pick_bom_tab(drive_browser.tabs_of(bom_id))
            ok, message = project_registry.add_project(
                new_name, rec_id, bom_id, registered_by=user["name"],
                bom_tab=tab)
            if ok:
                project_registry.set_active(new_name)
                st.toast(message)
                st.rerun()
            else:
                st.error(message)
