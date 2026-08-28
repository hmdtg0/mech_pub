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
        from utils import user_store

        ecols = st.columns(len(extras))
        for col, (key, value) in zip(ecols, extras):
            if "logged" in str(key).lower() and "at" not in str(key).lower():
                # The sheet keeps the email; the screen shows the person.
                value = user_store.name_of(value)
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


def native_table(columns, rows, backgrounds=None, link_col: str = "",
                 index: bool = False) -> None:
    """The ONE table renderer: the native grid, app-wide (Hamid, 28 Aug \u2014
    "make sure the native table view is globally across the app").

    `rows` is a list of lists in `columns` order, plain values throughout \u2014
    no HTML. `link_col` names the column whose cells are ABSOLUTE part urls
    (build them with ui.part_url()); the grid shows the ?part= code instead
    of the url and opens it in a NEW tab \u2014 the widget hard-codes that at
    any version, and it ignores relative urls entirely. A non-url cell in
    that column ("\u2014", "") stays plain text. `backgrounds` paints whole rows
    (css colour per row, "" for none). `index` numbers rows 1..n in a
    leading "#" column. Full height always \u2014 tables never scroll inside
    themselves, the page scrolls (house rule, 18 Aug).

    A column whose every value is an int stays numeric so the grid's
    sort-by-click sorts 20 before 100; anything mixed becomes text.
    """
    import pandas as pd
    import streamlit as st

    columns = list(columns)
    rows = [list(r) for r in rows]
    if index:
        columns = ["#"] + columns
        rows = [[i] + r for i, r in enumerate(rows, start=1)]
    df = pd.DataFrame(rows, columns=columns) if rows else \
        pd.DataFrame(columns=columns)
    for col in df.columns:
        vals = df[col].tolist()
        if not all(isinstance(v, int) for v in vals):
            df[col] = ["" if v is None else str(v) for v in vals]

    styler = df.style
    if backgrounds:
        _bg = list(backgrounds)

        def _paint(row):
            colour = _bg[row.name] if row.name < len(_bg) else ""
            return (["background-color: %s" % colour if colour else ""]
                    * len(row))

        styler = styler.apply(_paint, axis=1)
    config = {}
    if link_col:
        def _code_of(url):
            # The Styler's display layer overrides LinkColumn.display_text
            # on this Streamlit, so the code-instead-of-URL text comes from
            # the Styler itself \u2014 the one place that coexists with the row
            # colours. Non-url cells ("\u2014", "") pass through unchanged.
            from urllib.parse import parse_qs, urlparse
            try:
                return (parse_qs(urlparse(str(url)).query)
                        .get("part", [""])[0] or str(url))
            except Exception:
                return str(url)

        styler = styler.format(_code_of, subset=[link_col])
        config[link_col] = st.column_config.LinkColumn(
            link_col, help="Opens the part on Part Detail \u2014 the grid always "
                           "opens links in a new tab.")
    st.dataframe(styler, hide_index=True, height=table_height(len(df)),
                 use_container_width=True, column_config=config or None)


def absolute_url(path_and_query: str) -> str:
    """This request's own origin, in front of an app path.

    The native grid's LinkColumn only linkifies ABSOLUTE URLs — a relative
    one degrades to raw text (learned the hard way, 24 Aug). The origin is
    read per-request from the headers, so the same code addresses
    localhost:8502 and mech-order.streamlit.app without knowing either.
    """
    import streamlit as st

    try:
        headers = st.context.headers
        host = headers.get("Host", "")
        scheme = headers.get("X-Forwarded-Proto", "") or (
            "https" if not host.startswith("localhost") else "http")
    except Exception:
        host, scheme = "", ""
    if not host:
        return path_and_query
    return "%s://%s/%s" % (scheme, host, path_and_query.lstrip("/"))


def part_url(project: str, code: str) -> str:
    """A part's absolute address — what native_table's link_col cells hold."""
    from urllib.parse import quote

    code = str(code or "").strip()
    if not code or code == "\u2014":
        return ""
    return absolute_url("tracker_part_detail?project=%s&part=%s"
                        % (quote(str(project or "")), quote(code)))
