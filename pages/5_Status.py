"""Status page - System configuration and connection status."""
import streamlit as st
import os

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.auth import require_role
from config import (SERVICE_ACCOUNT_FILE, CENTRAL_SHEET_ID, DRIVE_FOLDER_ID,
                    ALLOWED_USERS)

user = require_role("admin")

st.title("⚙️ Status")

# --- Service Account status ---
st.header("Google API Connection")

col1, col2 = st.columns(2)
with col1:
    if os.path.exists(SERVICE_ACCOUNT_FILE):
        st.success("Service Account JSON found")
    else:
        st.error("No service_account.json found")

from utils import project_registry  # noqa: E402  (page script, not a module)
from utils.ui import table_height  # noqa: E402

with col2:
    if not CENTRAL_SHEET_ID:
        st.error("No main record configured (`MECH_CENTRAL_SHEET_ID`)")
    elif project_registry.source_is_live():
        st.success("Main record readable — %d project(s) registered"
                   % len(project_registry.all_projects()))
    else:
        st.error("Main record unreachable — the app cannot read its own database")

# --- Every file the app reads or writes -----------------------------------
# This page used to print a sheet id truncated to 20 characters, which was
# neither readable nor usable. What an admin actually needs from a status page
# is "which files am I connected to" — so name them, and link the ones people
# are meant to open.
st.subheader("Files in use")

# The main record is listed but NOT linked, anywhere in the app: it is the
# app's own database, and the way to change it is through the app.
rows = ["| File | Role | Open |", "| --- | --- | --- |",
        "| **Main record** | Orders index, users, messages — all projects "
        "| _app database — not linked_ |"]
for _name, _s in project_registry.all_sheets().items():
    rows.append("| **%s** — record | Ledger: orders, receipts, movements | %s |"
                % (_name, project_registry.sheet_link(_s["tracker"], "open ↗") or "—"))
    rows.append("| **%s** — BOM | Input: what can be ordered | %s |"
                % (_name, project_registry.sheet_link(_s["bom"], "open ↗") or "_not linked_"))
st.markdown("\n".join(rows))

if DRIVE_FOLDER_ID:
    st.caption("Drive folder for uploads — https://drive.google.com/drive/folders/%s"
               % DRIVE_FOLDER_ID)
else:
    st.caption("No Drive folder configured (`MECH_DRIVE_FOLDER_ID`) — file "
               "uploads have nowhere to land.")

# --- Can the app create files in Drive? -----------------------------------
# A service account owns no Drive storage, so in a My Drive folder every
# create/copy fails with "storage quota exceeded" however the sharing is set.
# Moving the folder to a Shared Drive fixes it, because a Shared Drive owns the
# files instead of the creator. Until then this stays off and the app asks for
# the copy to be made by hand — a switch, not a guess, so nothing half-writes.
from utils import app_settings  # noqa: E402

st.subheader("Drive writes")
_can_create = app_settings.flag(app_settings.DRIVE_CAN_CREATE)
_new = st.toggle(
    "Let the app create files and folders in Drive", value=_can_create,
    help="Only works when the root folder lives in a Shared Drive. In a normal "
         "My Drive folder the service account owns nothing and every create is "
         "refused for quota reasons — leave this off and copy the record "
         "template by hand.")
if _new != _can_create:
    if app_settings.set_flag(app_settings.DRIVE_CAN_CREATE, _new, user["name"]):
        st.success("Saved — the app %s create files in Drive."
                   % ("will try to" if _new else "will not"))
        st.rerun()
    else:
        st.error("Could not save the setting to the main record.")

if _can_create:
    if st.button("Test: can the app really create here?"):
        from utils import drive_browser
        root = app_settings.get(app_settings.DRIVE_ROOT_FOLDER_ID)
        if not root:
            st.warning("No Drive root folder set — see the **Projects** page.")
        else:
            ok, result = drive_browser.create_folder(root, "ZZ write test")
            if ok:
                st.success("Created a test folder. Delete `ZZ write test` when "
                           "you have seen it — the app will not delete anything.")
            else:
                st.error(result)
else:
    st.caption("Off: the app reads Drive but never writes to it. The project "
               "record must be copied from **TEMPLATE – Project Record** by "
               "hand and renamed.")

# Test connection
st.header("Test Connection")
if st.button("Test Google Sheet Connection"):
    try:
        from utils.google_client import get_gspread_client
        client = get_gspread_client()
        if client:
            # Test the main record: it is the one sheet that must always be
            # reachable, and on a fresh instance it's the only one configured.
            ss = client.open_by_key(CENTRAL_SHEET_ID)
            st.success(f"Connected to: **{ss.title}**")
            for ws in ss.worksheets():
                st.markdown(f"- **{ws.title}**: {ws.row_count} rows x {ws.col_count} cols")
        else:
            st.error("Failed to create client")
    except Exception as e:
        st.error(f"Connection failed: {e}")

if st.button("Test Google Drive Connection"):
    try:
        from utils.google_client import get_drive_service
        service = get_drive_service()
        if service:
            folder = service.files().get(fileId=DRIVE_FOLDER_ID, fields="name", supportsAllDrives=True).execute()
            st.success(f"Connected to Drive folder: **{folder.get('name')}**")
        else:
            st.error("Failed to create Drive service")
    except Exception as e:
        st.error(f"Drive connection failed: {e}")

# ============================================================
# Data freshness — every cache TTL, and what each one costs
# ============================================================
st.header("Data Freshness")

import math

import pandas as pd

from utils import project_registry, settings

st.markdown(
    "Each data set is re-read when its cached copy is older than its **TTL**. "
    "Lower = fresher data, more API calls. **Refresh** on any tracker page "
    "ignores the TTL and re-reads immediately.\n\n"
    "There is no idle setting, and none is needed: a TTL is a staleness check "
    "made *when a page asks for data*, not a timer. An untouched page asks for "
    "nothing, so an idle session costs zero calls."
)

# --- Assumptions the numbers are priced against ---
project_count = max(1, len(project_registry.all_projects()))
uc1, uc2, uc3 = st.columns(3)
with uc1:
    users = st.number_input("Active users", min_value=1, max_value=200, value=5,
                            help="People using the app in the same hour.")
with uc2:
    clicks = st.number_input("Interactions per user/hour", min_value=0, max_value=600,
                             value=20,
                             help="Page loads, filter changes, expander clicks — "
                                  "anything that reruns the script. 0 = idle.")
with uc3:
    projects_open = st.number_input("Projects being viewed", min_value=1,
                                    max_value=max(1, project_count), value=1,
                                    help="1 normally; the Parts page's "
                                         "'All projects' mode reads every one.")

# Each row is a cached data set: which setting drives it, and whether its cost
# multiplies by the number of projects on screen.
DATASETS = [
    ("Parts — Overview tab", "tracker_ttl", True),
    ("Parts — every part tab (batched)", "tracker_ttl", True),
    ("Movements — merged Movements tab", "tracker_ttl", True),
    ("Orders tab", "orders_ttl", False),
    ("Users tab", "users_ttl", False),
    ("Messages tab", "messages_ttl", False),
]

saved_ttls = settings.all_ttls()

# One table, not two: the edits are read out of the widget's own state BEFORE
# it renders, so the computed columns beside the TTL are already correct for
# what was just typed. Three rows share `tracker_ttl`, so only a row whose
# value actually CHANGED may set it — otherwise editing the first tracker row
# would be undone by the untouched second and third.
edited = dict(saved_ttls)
_editor_state = st.session_state.get("ttl_editor", {})
for _row_idx, _changes in (_editor_state.get("edited_rows") or {}).items():
    if "✏️ TTL" not in _changes:
        continue
    _key = DATASETS[int(_row_idx)][1]
    try:
        edited[_key] = max(settings.TTL_MIN,
                           min(settings.TTL_MAX, int(_changes["✏️ TTL"])))
    except (TypeError, ValueError):
        pass

interactions = users * clicks  # script reruns per hour, across everyone


def expected_reads(ttl: int) -> float:
    """Reads/hour actually expected for one cached data set.

    A read happens only when someone asks for data whose TTL window has
    expired. With `windows` windows in the hour and interactions landing at
    random, the share of windows touched at least once is
    1 - e^-(interactions/windows) — so an idle app reads nothing, a busy one
    approaches one read per window, and nothing in between is double-counted.
    """
    windows = 3600.0 / ttl
    if interactions <= 0:
        return 0.0
    return windows * (1 - math.exp(-interactions / windows))


rows = []
for label, key, per_project in DATASETS:
    ttl = edited[key]
    multiplier = projects_open if per_project else 1
    ceiling = (3600.0 / ttl) * multiplier
    expected = expected_reads(ttl) * multiplier
    rows.append({
        "Data set": label,
        "✏️ TTL": ttl,
        "× projects": multiplier,
        "Expected reads/hour": round(expected, 1),
        "Max reads/hour": round(ceiling),
        "Max reads/min": round(ceiling / 60, 2),
        "Per user/hour": round(expected / users, 1),
    })

total_hour = sum(r["Max reads/hour"] for r in rows)
total_expected = sum(r["Expected reads/hour"] for r in rows)

st.markdown("**✏️ TTL** is the only editable column — everything else "
            "recalculates from it. (Streamlit draws this grid on a canvas, so "
            "the cells can't take colour or bold; the pencil and the white "
            "background are the whole vocabulary available.)")
st.data_editor(
    pd.DataFrame(rows),
    height=table_height(len(rows)),
    column_config={
        # Only the TTL is writable; Streamlit renders the rest greyed.
        "Data set": st.column_config.TextColumn(disabled=True),
        "✏️ TTL": st.column_config.NumberColumn(
            min_value=settings.TTL_MIN, max_value=settings.TTL_MAX, step=5,
            format="%d s",
            help="Seconds before this data set is read again. The three Parts/"
                 "Movements rows share one setting, so they move together."),
        "× projects": st.column_config.NumberColumn(disabled=True, width="small"),
        "Expected reads/hour": st.column_config.NumberColumn(disabled=True),
        "Max reads/hour": st.column_config.NumberColumn(disabled=True),
        "Max reads/min": st.column_config.NumberColumn(disabled=True),
        "Per user/hour": st.column_config.NumberColumn(disabled=True),
    },
    hide_index=True, use_container_width=True, key="ttl_editor",
)

t1, t2, t3 = st.columns(3)
t1.metric("TOTAL expected reads/hour", "%.0f" % total_expected)
t2.metric("TOTAL max reads/hour", total_hour)
t3.metric("Per user/hour (expected)", "%.0f" % (total_expected / users))

QUOTA_PER_MIN = 60  # Google Sheets: read requests per minute per user (the
                    # service account counts as one user)
peak_per_min = total_hour / 60.0

# What is on screen vs what is actually in force.
pending = {k: v for k, v in edited.items() if v != saved_ttls.get(k)}

m1, m2, m3 = st.columns(3)
m1.metric("Peak reads/min", "%.1f" % peak_per_min)
m2.metric("Sheets quota/min", QUOTA_PER_MIN)
m3.metric("Headroom", "%.0f%%" % max(0.0, (1 - peak_per_min / QUOTA_PER_MIN) * 100))

if peak_per_min > QUOTA_PER_MIN * 0.8:
    st.error("Close to Google's %d reads/minute limit — raise the TTLs."
             % QUOTA_PER_MIN)
elif peak_per_min > QUOTA_PER_MIN * 0.5:
    st.warning("Using more than half the read quota. Fine for now; watch it if "
               "more projects come online.")
else:
    st.success("Comfortably inside Google's read quota.")

sc1, sc2 = st.columns([3, 1], vertical_alignment="bottom")
with sc1:
    if pending:
        st.warning("Unsaved: " + ", ".join(
            "%s %ds → %ds" % (k, saved_ttls[k], v) for k, v in pending.items()))
    else:
        st.caption("Saved values are in force. Stored in `data/settings.json` — "
                   "ephemeral on Streamlit Cloud, so set `MECH_TRACKER_TTL`, "
                   "`MECH_ORDERS_TTL`, `MECH_USERS_TTL`, `MECH_MESSAGES_TTL` "
                   "for values that survive a restart.")
with sc2:
    if st.button("💾 Save settings", type="primary", use_container_width=True,
                 disabled=not pending):
        settings.save(edited)
        # Clear the widget's pending edits, otherwise it would keep replaying
        # them over the newly saved values and always look "unsaved".
        st.session_state.pop("ttl_editor", None)
        st.toast("Saved.")
        st.rerun()

st.caption(
    "**Max** is the ceiling if the app is used continuously; **Expected** "
    "models interactions landing at random across the TTL windows. The cache "
    "is shared by the whole app process, so ten people browsing cost far less "
    "than ten times one person. Writes (submitting an order, saving a status) "
    "are extra and use a separate quota. Each **Refresh** click adds up to 3 "
    "reads per project on top."
)

# Orders status
st.header("Orders Data")
from utils.orders_store import fetch_all_orders
orders = fetch_all_orders()
from config import ORDER_STATUSES
st.markdown(f"**{len(orders)} total orders**")
for status in ORDER_STATUSES:
    count = sum(1 for o in orders if o.get("Status") == status)
    if count > 0:
        st.markdown(f"- {status}: {count}")

# User configuration
st.header("Allowed Users")
admins = {k: v for k, v in ALLOWED_USERS.items() if v["role"] == "admin"}
engineers = {k: v for k, v in ALLOWED_USERS.items() if v["role"] == "engineer"}

st.markdown("**Admins:**")
seen = set()
for email, info in admins.items():
    if info["name"] not in seen:
        st.markdown(f"- {info['name']} ({email})")
        seen.add(info["name"])

st.markdown("**Engineers:**")
seen = set()
for email, info in engineers.items():
    if info["name"] not in seen:
        st.markdown(f"- {info['name']} ({email})")
        seen.add(info["name"])
