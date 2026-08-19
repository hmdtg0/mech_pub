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
    """Say — changeably — which project this page WRITES to.

    The sidebar switcher is global but easy to miss at the exact moment it
    matters: a mass submit. "Which record did that just go to" is the wrong
    question to be asking after the fact (Hamid, 19 Aug), so a page that
    files orders states its target where the orders are, with the switch in
    arm's reach. One truth underneath — this and the sidebar move the same
    session state.
    """
    import streamlit as st

    from utils import project_colors, project_registry

    active, _sheet = project_registry.active()
    names = list(project_registry.all_projects())
    if not names:
        return active
    c1, c2 = st.columns([1.6, 3.4], vertical_alignment="center")
    with c1:
        picked = st.selectbox(
            "Project", names,
            index=names.index(active) if active in names else 0, key=key)
    with c2:
        st.markdown(project_colors.badge_html(picked), unsafe_allow_html=True)
        st.caption(purpose)
    if picked != active:
        project_registry.set_active(picked)
        # Keep the sidebar switcher agreeing, or its stale widget state
        # flips the project straight back on the next rerun.
        st.session_state["sidebar_project"] = picked
        st.rerun()
    return picked


def project_filter(rows, key: str, fields=("project", "Project")):
    """A cross-project listing, narrowed to one project by selectbox.

    Rendered even while a single project is live: the control tells the
    reader this listing spans projects, and the day a second project lands
    it already works (Hamid, 19 Aug — pages that show every project need a
    filter).
    """
    import streamlit as st

    def _of(row):
        for f in fields:
            value = str(row.get(f) or "").strip()
            if value:
                return value
        return ""

    names = sorted({_of(r) for r in rows if _of(r)})
    if not names:
        return rows
    picked = st.selectbox("Project", ["all projects"] + names, key=key)
    if picked == "all projects":
        return rows
    return [r for r in rows if _of(r) == picked]
