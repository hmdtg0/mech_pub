"""Mech Order Helper - Main entry point with role-based navigation."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st

st.set_page_config(page_title="Mech Order Helper", page_icon="🔩", layout="wide")

from utils.auth import get_current_user, is_admin, is_logistics, require_auth
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
    st.Page("pages/admin_all_orders.py", title="All Orders", icon="📊", default=True),
    st.Page("pages/admin_process_order.py", title="Process Order", icon="🔧"),
    st.Page("pages/admin_order_history.py", title="Order History", icon="📚"),
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
</style>""", unsafe_allow_html=True)

# The custom sidebar block. Streamlit pins its navigation above ANY custom
# sidebar content, so the picker cannot sit between nav groups — first place
# it can be is directly under the nav, which is where it is. Order is by how
# often a thing is needed: the project switch is a working control, identity
# is something you check once a day, so the picker comes first and who-am-I
# moves to the bottom.
with st.sidebar:
    from config import IS_LOCAL
    from utils import parts_tracker, project_colors
    from utils.google_client import credentials_status

    # ONE project switcher for the whole app. It used to be repeated on five
    # pages, each with its own state: switching project on Parts also silently
    # redirected your next order, because they all wrote the same session key
    # from different places. Chosen once here, every page follows.
    _names = list(project_registry.all_projects())
    if _names:
        _picked = st.selectbox(
            "Project", _names,
            index=_names.index(_current_project) if _current_project in _names else 0,
            key="sidebar_project",
            help="Every page reads this project. Parts can still show all "
                 "projects at once as a filter.")
        if _picked != _current_project:
            project_registry.set_active(_picked)
            st.rerun()
        st.markdown(project_colors.badge_html(_picked,
                                              sheet_id=project_registry.tracker_sheet(_picked)),
                    unsafe_allow_html=True)
    elif project_registry.source_is_live():
        st.caption("No projects registered yet.")
    else:
        st.caption("⚠️ Can't read the project list from the main record.")
    # No read stamp here: the sidebar renders before the page loads its data,
    # so it would always show the previous read. The page's own project line
    # carries the stamp.
    st.caption(f"Data: {parts_tracker.source_label(with_read=False)}")
    _cred_err = credentials_status()
    if _cred_err:
        st.caption(f"⚠️ Read-only — no Google credentials ({_cred_err})")

    st.markdown("---")
    role_badge = "🔑 Admin" if is_admin(user) else "📦 Logistics" if is_logistics(user) else "👤 Engineer"
    st.markdown(f"{role_badge} **{user['name']}**")
    st.caption(user["email"])
    # Logout has to tell the truth about WHERE the identity lives, because it
    # is different in each mode — and the old unconditional
    # `del st.session_state["auth_email"]` crashed the whole app the moment
    # that key did not exist (it was only ever set by the name picker).
    if IS_LOCAL:
        # Local dev signs the configured admin in on every run; deleting the
        # session just signs them straight back in. A button that pretends
        # otherwise is worse than none. Real sign-in/out is exercised on the
        # deployed-mode server (port 8503).
        st.caption("Local development — signed in automatically. Logout "
                   "exists on the deployed app, where sign-in is real.")
    elif "auth_email" in st.session_state:
        # Name login (the break-glass): the identity lives in this session,
        # so ending the session genuinely logs out.
        if st.button("Logout", type="secondary", use_container_width=True):
            st.session_state.pop("auth_email", None)
            st.session_state.pop("user", None)
            st.rerun()
    else:
        # Verified sign-in: the identity arrives with every request from
        # Streamlit / Cloudflare. The app cannot end that session — only the
        # provider can — so say where, rather than offering a logout that
        # would quietly not work.
        st.caption("Signed in through your Google account. To switch person, "
                   "sign out there — the app follows whatever the sign-in "
                   "in front of it says.")

pg.run()
