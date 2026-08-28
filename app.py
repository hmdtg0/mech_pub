"""Mech Order Helper - Main entry point with role-based navigation."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st

st.set_page_config(page_title="Mech Order Helper", page_icon="🔩", layout="wide")

from utils.auth import (get_current_user, impersonation, is_admin,
                        is_logistics, require_auth)
from utils import project_registry

# --- Active project (one Google Sheet per project) ---
# Resolve before anything touches the stores so auth/user lookups hit the
# right sheet from the first line of this rerun. Projects are chosen where the
# work happens — on Submit Order (new or existing) and as a filter on Parts —
# not from a global sidebar control.
_current_project, _current_sheet = project_registry.active()
st.session_state["active_project"] = _current_project
st.session_state["active_sheet_id"] = _current_sheet

# No idle handling here on purpose: nothing runs while a page sits untouched,
# so an idle session already costs nothing. The TTL is a staleness check made
# when a page asks for data, not a timer — no question, no call.

# --- Auth gate ---
# Peek WITHOUT drawing anything: whether to build the real navigation is
# decided first, and only then does require_auth render the login screen —
# the picker, or the explanation of which gate refused. Drawing from both
# places put two identical pickers on the page and collided their widget ids.
user = get_current_user(prompt=False)

if user is None:
    # Nobody signed in that we recognise. Show ONLY this page — the nav is
    # hidden so the page list cannot be discovered by someone the app has
    # not identified. require_auth stops the script unless a name is picked
    # on this very run, in which case rerun into the real app.
    st.navigation([st.Page(lambda: None, title="Login", icon="🔒")],
                  position="hidden")
    st.title("🔩 Mech Order Helper")
    require_auth()
    st.rerun()

# Store user in session state
st.session_state["user"] = user

# --- Build page list based on role ---
# The project's own Parts Tracker sheet, read-only (Overview / part tabs /
# Movement Log). Everyone sees it — it's the shared picture of the build.
tracker_pages = [
    st.Page("pages/tracker_parts.py", title="Parts", icon="🧩"),
    st.Page("pages/tracker_part_detail.py", title="Part Detail", icon="🔍"),
    st.Page("pages/tracker_movements.py", title="Movements", icon="🚚"),
    # From the project's BOM sheet (read-only): the structured movement log,
    # and stock derived from it.
    st.Page("pages/tracker_shipments.py", title="Shipments", icon="🚢"),
    st.Page("pages/tracker_stock.py", title="Stock", icon="📦"),
]

shared_pages = [
    st.Page("pages/order_from_bom.py", title="Order from BOM", icon="🧾"),
    st.Page("pages/ee_submit_order.py", title="Submit Order", icon="📋"),
    st.Page("pages/ee_my_orders.py", title="My Orders", icon="📦"),
    st.Page("pages/ee_messages.py", title="My Messages", icon="💬"),
]

tools_common = [
    st.Page("pages/4_Translator.py", title="Translator", icon="🌐"),
]

status_pages = []

# Logistics keeps its own dashboard, as in the PCB tool — shipping/receiving
# is a different job from raising and processing orders.
logistics_pages = [
    st.Page("pages/logistics_dashboard.py", title="Logistics", icon="📦"),
]

# The WORK pages — receiving goods, recording movements, correcting history —
# belong to everyone who handles parts, not only admins (19 Aug 2026). The
# page-level require_role was opened to engineer + logistics, but a page
# st.navigation does not register is unreachable no matter what the page
# itself would allow: the nav list IS the outer gate, and the two must agree.
work_pages = [
    # Overview is the landing page: the one screen that answers "where does
    # everything stand" (Hamid, 21 Aug). Since 28 Aug it also carries the
    # orders desk (the old All Orders page) behind its Board/Orders switch —
    # "less pages to track".
    st.Page("pages/admin_overview.py", title="Overview", icon="🗂", default=True),
    st.Page("pages/admin_process_order.py", title="Process Order", icon="🔧"),
]

if is_admin(user):
    # Admin-only: these rebuild the Overview, create tabs, and decide who can
    # log in.
    status_pages = [
        st.Page("pages/admin_projects.py", title="Projects", icon="🗂"),
        st.Page("pages/admin_user_management.py", title="User Management", icon="👥"),
        st.Page("pages/5_Status.py", title="Status", icon="⚙️"),
    ]

# Navigation structure — same groups and order as the PCB tool, with the
# project tracker added as one extra group.
if is_admin(user):
    pages = {
        "Admin": work_pages,
        "Logistics": logistics_pages,
        "Orders": shared_pages,
        "Tracker": tracker_pages,
        "Tools": tools_common + status_pages,
    }
elif is_logistics(user):
    pages = {
        "Process": work_pages,
        "Logistics": logistics_pages,
        "Orders": shared_pages,
        "Tracker": tracker_pages,
        "Tools": tools_common,
    }
else:
    pages = {
        "Process": work_pages,
        "Orders": shared_pages,
        "Tracker": tracker_pages,
        "Tools": tools_common,
    }

pg = st.navigation(pages)

# Streamlit 1.38 folds the nav behind a "View more" once the page count
# passes its threshold — with three roles' worth of pages that hid half the
# app and buried the project switcher under it (Hamid, 19 Aug). The
# `expanded=` switch that fixes this properly arrived in a later Streamlit,
# so on 1.38 the collapse is undone directly: the nav list is clipped with a
# max-height and the button toggles it. Safe to pin CSS to internals ONLY
# because requirements pins streamlit==1.38.0 — revisit this when that pin
# moves.
st.markdown("""<style>
[data-testid="stSidebarNav"] ul { max-height: none !important; }
[data-testid="stSidebarNavViewButton"] { display: none !important; }
/* Streamlit renders its page list ABOVE any custom sidebar content, which
   put the one control that decides what every page shows below nineteen
   links. The three sidebar regions each carry a stable data-testid, so the
   column is simply re-ordered: header, our block, then the page list
   (Hamid, 21 Aug: "this block should be on the very top"). Safe under the
   pinned 1.38.0; revisit on any Streamlit upgrade. */
[data-testid="stSidebarContent"] { display: flex; flex-direction: column; }
[data-testid="stSidebarHeader"] { order: 0; }
[data-testid="stSidebarUserContent"] { order: 1; }
[data-testid="stSidebarNav"] { order: 2; }
/* And tight, because every pixel it takes is a pixel of page list pushed
   down. Streamlit's default 1rem gap between sidebar elements is generous
   for a form and wasteful for three controls. */
[data-testid="stSidebarUserContent"] { padding: 0.5rem 1rem 0.25rem; }
[data-testid="stSidebarUserContent"] [data-testid="stVerticalBlock"] { gap: 0.3rem; }
[data-testid="stSidebarUserContent"] [data-testid="stWidgetLabel"] p { font-size: 0.8rem; }
</style>""", unsafe_allow_html=True)

# The custom sidebar block, CSS-lifted above the page list (see the style
# above). Order is by how often a thing is needed: the project switch decides
# what every page shows, so it is the first thing in the sidebar, and identity
# follows it. The whole block rides up together — it pushes the page list down
# by its own height, which Hamid weighed against splitting it and preferred
# (21 Aug: "its okay to keep them together").
with st.sidebar:
    from config import IS_LOCAL
    from utils import parts_tracker, user_store
    from utils.google_client import credentials_status

    # Who you are, in one line, above the project switch: role, name, email.
    # st.html rather than st.caption, because Streamlit's markdown turns any
    # bare address into a mailto link and a mail client is no use to anyone
    # reading their own address (Hamid, 21 Aug). The sentence explaining
    # where sign-in lives is the line's tooltip.
    _role = ("🔑 Admin" if is_admin(user) else
             "📦 Logistics" if is_logistics(user) else "👤 Engineer")
    st.html(
        '<div title="%s" style="font-size:0.78rem;color:rgba(49,51,63,0.7);'
        'line-height:1.3;margin:0 0 0.25rem 0;">%s <span style="opacity:.5">|'
        '</span> %s <span style="opacity:.5">|</span> %s</div>'
        % ("Signed in automatically in local development." if IS_LOCAL else
           "Signed in through your Google account. To switch person, sign "
           "out there — the app follows whatever the sign-in in front of it "
           "says.",
           _role, user["name"], user["email"]))

    # --- View as: see the app as a teammate does (admins only) --------------
    # The PCB tool answered this need by letting anyone LOG IN as anyone —
    # which meant the ledger recorded whoever was claimed, not whoever acted.
    # This is the honest version: the rendering, role and matching become the
    # target's, the writers all refuse while it is active, and the real
    # identity stays pinned underneath.
    _real = impersonation() or user
    if _real.get("role") == "admin":
        from utils.user_store import fetch_allowed_users

        _by_name = {}
        for _e, _i in sorted(fetch_allowed_users().items()):
            if _e == _real.get("email"):
                continue
            _by_name.setdefault("%s (%s)" % (_i["name"], _i["role"]), _e)
        _me = "— myself —"
        _options = [_me] + sorted(_by_name)
        _active = st.session_state.get("view_as", "")
        _index = 0
        for _n, _e in _by_name.items():
            if _e == _active:
                _index = _options.index(_n)
        _picked_view = st.selectbox(
            "👁 View as", _options, index=_index, key="view_as_picker",
            help="Renders the app exactly as this person sees it — their "
                 "pages, their My Orders. Recording anything is disabled "
                 "until you switch back: the ledger only ever carries the "
                 "name that actually acted.")
        _target = "" if _picked_view == _me else _by_name[_picked_view]
        if _target != _active:
            if _target:
                st.session_state["view_as"] = _target
            else:
                st.session_state.pop("view_as", None)
                st.session_state.pop("view_as_real", None)
            st.rerun()

    # ONE project switcher for the whole app. It used to be repeated on five
    # pages, each with its own state: switching project on Parts also silently
    # redirected your next order, because they all wrote the same session key
    # from different places. Chosen once here, every page follows.
    #
    # One row, and everything that used to sit under it is folded into that
    # row (Hamid, 21 Aug: "keep project in single row... shrink it as much as
    # the tool allows"). The badge went because it repeated the name already
    # showing in the box; the label went because a list of project names does
    # not need telling; the data-source line went into the help tooltip,
    # where it is still one hover away. It sits last of the three, with a gap
    # above it: who you are, who you are looking as, then what you are
    # looking at.
    st.html('<div style="height:0.55rem"></div>')
    _names = list(project_registry.all_projects())
    _source = parts_tracker.source_label(with_read=False)
    if _names:
        # First landing of a session goes to this person's pinned project, so
        # the app opens where they work rather than on whichever project the
        # Projects tab happens to list first.
        if "project_scope" not in st.session_state:
            _pin = user_store.pinned_project(user.get("email", ""))
            st.session_state["project_scope"] = ""
            if _pin == project_registry.ALL and len(_names) > 1:
                st.session_state["project_scope"] = project_registry.ALL
            elif _pin in _names:
                project_registry.set_active(_pin)

        _options = ([project_registry.ALL] + _names if len(_names) > 1
                    else list(_names))
        _now = project_registry.scope()
        _pinned_now = user_store.pinned_project(user.get("email", ""))

        # Streamlit offers no way to style one option of a selectbox — the
        # list is plain strings — so the pin travels IN the label, where it
        # shows both in the open list and in the closed box. The value is the
        # name; the marker is stripped straight back off.
        def _label(name):
            return "📌 %s" % name if name and name == _pinned_now else name

        def _plain(label):
            return label[2:] if label.startswith("📌 ") else label

        _pick_col, _link_col, _pin_col = st.columns(
            [3, 1.1, 0.6], vertical_alignment="center")
        with _pick_col:
            _labels = [_label(o) for o in _options]
            _picked = _plain(st.selectbox(
                "Project", _labels,
                index=_options.index(_now) if _now in _options else 0,
                key="sidebar_project", label_visibility="collapsed",
                help="What every page shows. \"%s\" widens the listings; the "
                     "pages that write to one record ask you to choose. "
                     "📌 marks the one you land on. Data: %s."
                     % (project_registry.ALL, _source)))
        if _picked == _pinned_now:
            # Red and bold while the box is showing the pinned project, so
            # the answer to "what am I pinned to" is visible without opening
            # the list (Hamid, 21 Aug: "its not clear what is pinned").
            st.html('<style>[data-testid="stSidebarUserContent"] '
                    '[data-testid="stHorizontalBlock"] [data-testid="stSelectbox"] '
                    'div[data-baseweb="select"] div[value] '
                    '{ color:#b3261e !important; font-weight:600 !important; }'
                    '</style>')
        if _picked != _now:
            project_registry.set_scope(_picked)
            st.rerun()
        with _link_col:
            _sheet = ("" if _picked == project_registry.ALL else
                      project_registry.sheet_link(
                          project_registry.tracker_sheet(_picked), "sheet ↗"))
            if _sheet:
                st.markdown(_sheet, unsafe_allow_html=True)
        with _pin_col:
            # One cell on your own row of the Users tab, written on the click
            # — not on every project switch, which would put a sheet write
            # behind an ordinary control.
            _is_pinned = bool(_pinned_now) and _pinned_now == _picked
            if st.button("📌" if _is_pinned else "📍", key="pin_project",
                         help=("Pinned — you land here. Click to unpin."
                               if _is_pinned else
                               "Land on %s when you next open the app."
                               % _picked)):
                _problem = user_store.set_pinned_project(
                    user.get("email", ""), "" if _is_pinned else _picked)
                if _problem:
                    st.warning(_problem)
                else:
                    st.rerun()
    elif project_registry.source_is_live():
        st.caption("No projects registered yet.")
    else:
        st.caption("⚠️ Can't read the project list from the main record.")
    _cred_err = credentials_status()
    if _cred_err:
        st.caption(f"⚠️ Read-only — no Google credentials ({_cred_err})")


    if impersonation():
        st.warning("Viewing as **%s** — writes are disabled. You are %s."
                   % (user["name"], impersonation().get("email", "?")))
    # A Logout button only exists where logging out is a real thing the app
    # can do: name login keeps the identity in this session, so ending the
    # session ends it. Local dev signs you straight back in, and a verified
    # Google session can only be ended at Google — in both of those the
    # button would be a lie, so the email's tooltip says where sign-in lives
    # instead. (The old unconditional `del st.session_state["auth_email"]`
    # crashed the app whenever that key had never been set.)
    if not IS_LOCAL and "auth_email" in st.session_state:
        if st.button("Logout", type="secondary", use_container_width=True):
            st.session_state.pop("auth_email", None)
            st.session_state.pop("user", None)
            st.rerun()

pg.run()
