"""Shared renderers for showing tracker rows.

Rule for anything sourced from the sheet: **show the cell, don't retell it.**
No truncation, no summarising, no stitching fields into a sentence — the log
is the record of what happened and paraphrasing it loses exactly the detail
(tracking numbers, quantities, QC verdicts) people open it for.

Interpretation — which movement belongs to which part, what a note implies —
is kept separate and always labelled as such.
"""
from __future__ import annotations

import streamlit as st

# Columns the drawer label accounts for; anything else in a row is shown in
# the body so a new sheet column can't go unnoticed.
_KNOWN = {"date", "item", "from", "qty", "to", "stage", "notes"}

# Backslash-escapable ASCII punctuation (CommonMark). Escaping these lets a
# raw cell go through st.markdown untouched — so it wraps like normal text
# instead of scrolling sideways in a <pre> block — while still displaying
# exactly the characters the sheet holds.
_MD_PUNCT = set("\\`*_{}[]()<>#+-.!|~&\"'$%^=:;,?/@")


def table_height(n_rows: int) -> int:
    """The height that shows EVERY row of a table.

    House rule (Hamid, 18 Aug 2026): tables never clip or scroll inside
    themselves — the page scrolls. 35px per row (the grid's row pitch),
    one header row, and a few px of border.
    """
    return 35 * (max(int(n_rows), 1) + 1) + 3


def literal(text: str) -> str:
    """Sheet text, safe to hand to st.markdown: escaped, line breaks kept."""
    out = "".join("\\" + ch if ch in _MD_PUNCT else ch for ch in str(text))
    return out.replace("\n", "  \n")


def require_project() -> None:
    """Stop the page when no project is registered, and say what to do.

    Every project page indexes into the project list, so zero projects would
    otherwise be an IndexError rather than an answer. It is also a state the
    app now genuinely starts in: the list is the main record's `Projects` tab,
    and a fresh one is empty.
    """
    from utils import project_registry

    if project_registry.all_projects():
        return
    if project_registry.source_is_live():
        st.info(
            "**No projects yet.** The project list is the `Projects` tab of "
            "the main record, and it is empty.\n\n"
            "An admin registers the first one on **Tools → Projects**: a name, "
            "its BOM sheet and its project record sheet."
        )
    else:
        st.warning(
            "**Cannot read the project list.** It lives in the `Projects` tab "
            "of the main record, which this instance cannot open — check the "
            "Google credentials on the **Status** page."
        )
    st.stop()


def _label_value(row: dict, key: str) -> str:
    """Cell value for the drawer label. A lone dash is the sheet's way of
    writing "not applicable", so it's left out of the summary line rather than
    printed as "Qty —". The cell itself is untouched and still shows in the
    table view.
    """
    value = (row.get(key) or "").strip()
    return "" if value in ("-", "--", "—", "–") else value


def movement_header(row: dict) -> str:
    """Collapsed-drawer label: what happened on the first line, the shipping
    particulars on a second, so a closed list still reads as a log."""
    date = _label_value(row, "date") or "(no date)"
    item = _label_value(row, "item") or "(no item)"
    frm = _label_value(row, "from")

    first = "**%s — %s%s**" % (literal(date), literal(item),
                               " — from %s" % literal(frm) if frm else "")
    second = " · ".join(
        "%s %s" % (label, literal(value))
        for label, value in (("Qty", _label_value(row, "qty")),
                             ("To", _label_value(row, "to")),
                             ("Stage", _label_value(row, "stage")))
        if value
    )
    return first + ("  \n" + second if second else "")


def render_movement(row: dict, notes: bool = True) -> None:
    """The body of one Movement Log row — the note, and any column the sheet
    has grown that this app doesn't know by name.

    Date, Item, From, Qty, To and Stage are all in movement_header(), which is
    the drawer's label, so the body doesn't repeat them.
    """
    extras = [(k, v) for k, v in row.items() if k not in _KNOWN and str(v).strip()]
    if extras:
        ecols = st.columns(len(extras))
        for col, (key, value) in zip(ecols, extras):
            col.markdown("**%s:** %s" % (key, literal(value)))

    if notes:
        note = (row.get("notes") or "").strip()
        if note:
            st.markdown(literal(note))
        else:
            st.caption("No notes.")


def project_scope(purpose: str, key: str = "page_project") -> str:
    """State — not offer — which project this page writes to.

    The sidebar switcher is global but easy to miss at the exact moment it
    matters: a mass submit. "Which record did that just go to" is the wrong
    question to be asking after the fact (Hamid, 19 Aug), so a page that
    files orders says its target where the orders are. It used to carry a
    selectbox too; that went with every other per-page picker on 21 Aug —
    one control, in the sidebar, and this line reports what it says.
    """
    import streamlit as st

    from utils import project_colors, project_registry

    active, _sheet = project_registry.active()
    if not project_registry.all_projects():
        return active
    st.markdown(project_colors.badge_html(active), unsafe_allow_html=True)
    st.caption("%s Change it in the sidebar." % purpose)
    return active


def in_scope(rows, fields=("project", "Project")):
    """A cross-project listing, narrowed to whatever the sidebar is showing.

    This used to be a selectbox on each page. Five pages meant five controls
    answering the same question, and the sidebar answering it a sixth time
    (Hamid, 21 Aug: "when it is set all pages should follow it, this will
    help removing the project picker from each page"). The scope lives in one
    place now; a listing just filters by it.
    """
    from utils import project_registry

    if project_registry.is_all():
        return list(rows)

    def _of(row):
        for f in fields:
            value = str(row.get(f) or "").strip()
            if value:
                return value
        return ""

    wanted = project_registry.active()[0]
    # A row with no project named belongs to whoever is looking: it came from
    # a source that only ever holds one project's data.
    return [r for r in rows if _of(r) in ("", wanted)]


def require_single_project(purpose: str = "This page"):
    """Stop a page that needs ONE project while the scope is every project.

    A modal rather than a line of text, and a modal with the picker IN it:
    the answer to "select a project to continue" is a project, so asking the
    question and sending the reader to the sidebar for the answer is one trip
    too many (Hamid, 21 Aug).
    """
    import streamlit as st

    from utils import project_registry

    require_project()
    if not project_registry.is_all():
        return project_registry.active()[0]

    @st.dialog("Select a project to continue")
    def _ask():
        st.write("%s writes to — or reads — one project record, so it needs "
                 "to know which one." % purpose)
        names = list(project_registry.all_projects())
        picked = st.selectbox("Project", names, key="dialog_project")
        if st.button("Continue", type="primary", use_container_width=True):
            project_registry.set_scope(picked)
            st.rerun()

    _ask()
    st.stop()
