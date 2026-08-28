"""Configuration for Mech Order Helper."""
import json as _json
import os

# --- Environment detection ---
# MECH_LOCAL_DEV=1 → local mode (auto-login as admin)
# MECH_LOCAL_DEV=0 or not set on Linux → cloud mode (require Google OAuth)
# Windows always defaults to local mode unless explicitly set to 0
_is_windows = os.name == "nt"
IS_LOCAL = os.environ.get("MECH_LOCAL_DEV", "1" if _is_windows else "0") == "1"

# --- Central database sheet (all projects) ---
# "MECH Outsourcing Material Record" — the app's own Orders / Users / Messages
# tabs live HERE, one place for every project. Project sheets (tracker + BOM)
# are sources the app reads; they never get app tabs.
# Env-only, like every other sheet id here: an installation names its own
# main record, and an internal file id has no business in public source. On
# Streamlit Cloud a top-level `MECH_CENTRAL_SHEET_ID = "..."` line in the
# secrets box becomes this environment variable.
CENTRAL_SHEET_ID = os.environ.get("MECH_CENTRAL_SHEET_ID", "")

# --- Projects: EMPTY ON PURPOSE ------------------------------------------
# The live project list is the `Projects` tab of the main record — see
# utils/project_registry.py. A project hard-coded here would be a second home
# for the same fact, and the two drift apart silently: the app would offer a
# project the main record has never heard of, which is exactly what happened
# with the seed project this replaced.
#
# What is left here is an offline fallback only, for an instance that cannot
# reach Google at all. Populate it via the MECH_PROJECTS env var, a JSON object
# of {display name: sheet_id} or {display name: {"tracker": id, "bom": id}}:
# MECH_PROJECTS={"WW2": "1def...", "X1": {"tracker": "1abc...", "bom": "1ghi..."}}
PROJECTS = {}
_env_projects = os.environ.get("MECH_PROJECTS", "")
if _env_projects:
    try:
        PROJECTS.update(_json.loads(_env_projects))
    except ValueError:
        pass


def tracker_id_of(entry):
    """The tracker sheet id from either PROJECTS entry shape."""
    if isinstance(entry, dict):
        return entry.get("tracker", "")
    return entry or ""


# Fallback sheet when no project is selected yet. Empty is a valid state — a
# fresh instance has no projects until one is registered in the main record —
# so this must not assume PROJECTS has an entry to borrow.
GOOGLE_SHEET_ID = os.environ.get("MECH_SHEET_ID", "")
if not GOOGLE_SHEET_ID and PROJECTS:
    GOOGLE_SHEET_ID = tracker_id_of(next(iter(PROJECTS.values())))
SERVICE_ACCOUNT_FILE = os.path.join(os.path.dirname(__file__), "service_account.json")

# Google Drive shared folder for file uploads (CAD files, drawings).
# Must be created and shared with the service account before first upload —
# see SETUP.md.
DRIVE_FOLDER_ID = os.environ.get("MECH_DRIVE_FOLDER_ID", "")

# New projects are created by COPYING a template sheet, so nobody has to paste
# a sheet id and the app never touches a sheet it wasn't handed.
# MECH_TEMPLATE_SHEET_ID: a blank tracker (Overview / part tabs / Movement Log).
# MECH_PROJECTS_FOLDER_ID: shared Drive folder the copies land in — without it
# the new sheet sits in the service account's own Drive.
TEMPLATE_SHEET_ID = os.environ.get("MECH_TEMPLATE_SHEET_ID", "")
PROJECTS_FOLDER_ID = os.environ.get("MECH_PROJECTS_FOLDER_ID", "")

# Local download base path (admin only, local mode)
LOCAL_DOWNLOAD_BASE = os.environ.get(
    "MECH_LOCAL_DOWNLOAD_BASE",
    os.path.join(os.path.expanduser("~"), "Downloads", "Mech_Orders"),
)

# --- Sheet tab names ---
TAB_ORDERS = "Orders"

# Central-record tabs the stock pages read and write. `Holders` is THE
# directory — the one definition of every person and vendor — so the app never
# offers a free-text holder; it offers this list. `Stock` is the current count
# per part per holder, `Stock_History` the append-only log of what moved.
TAB_HOLDERS = "Holders"
TAB_STOCK = "Stock"
TAB_STOCK_HISTORY = "Stock_History"

# The merged movement log, one per PROJECT record (Hamid, 19 Aug: "the stock
# history belongs to project record ... we need to use event to categorise
# which move needs to be shown where"). It replaces the project's own
# `Movement Log` and the central `Stock_History`, which recorded the same
# events in two shapes in two files.
TAB_MOVEMENTS_MERGED = "Movements"

# --- What each event MEANS ---------------------------------------------------
# ONE definition, read by the stock count, the Movements page, the Shipments
# view and Part Detail. Every page having its own idea of "what counts as a
# movement" is exactly how the Overview and the stock count came to disagree
# about 2,495 pieces (19 Aug).
#
# `stock` says what the row does to a count:
#   "in"        quantity arrives at To
#   "transfer"  leaves From and arrives at To
#   "out"       leaves From and goes nowhere we count (scrapped, in transit)
#   "restate"   corrects an earlier row instead of adding to it
#   ""          changes no count at all
#
# A row only moves stock if it also carries a quantity: an `Order` raise line
# has none, so it contributes nothing until its receive line lands.
MOVEMENT_EVENTS = {
    "Order":      {"stock": "in",       "movements": False, "shipments": False},
    "Receipt":    {"stock": "in",       "movements": True,  "shipments": False},
    # "Hand delivered" replaced Movement and Delivery in the ENTRY vocabulary
    # (Hamid, 28 Aug: "movement event is very generic"). It is a transfer —
    # leaves From, arrives at To — and the smart half of the rule lives in
    # the stock count already: a From whose directory kind is "source" never
    # goes negative, so a hand-over between holders moves both counts while
    # an arrival from a registered vendor only adds at To.
    "Hand delivered": {"stock": "transfer", "movements": True, "shipments": False},
    # Movement and Delivery stay RECOGNIZED — years of ledger rows carry
    # them, and reading is forever — they are just never offered again.
    "Movement":   {"stock": "transfer", "movements": True,  "shipments": False},
    "Shipping":   {"stock": "out",      "movements": True,  "shipments": True},
    "Delivery":   {"stock": "in",       "movements": True,  "shipments": True},
    "Return":     {"stock": "transfer", "movements": True,  "shipments": True},
    "Scrap":      {"stock": "out",      "movements": True,  "shipments": False},
    "QC":         {"stock": "",         "movements": False, "shipments": False},
    "Update":     {"stock": "",         "movements": False, "shipments": False},
    "Hold":       {"stock": "",         "movements": False, "shipments": False},
    "Correction": {"stock": "restate",  "movements": False, "shipments": False},
    "Cancelled":  {"stock": "",         "movements": False, "shipments": False},
}

# What the ENTRY pickers offer, most-reached-for first — trimmed 28 Aug
# (Hamid's interview): Movement and Delivery folded into "Hand delivered";
# Correction and Order left entry entirely (Order lines are written by
# Order from BOM, Receipt lines by Receive goods; corrections get their
# own admin path per the proposal). Cancelled STAYS — it is the one way to
# call an order off ("add cancel to history entry, my bad"). Every legacy
# name above still READS correctly — this list only governs what can be
# typed in new.
EVENT_CHOICES = ["Hand delivered", "Receipt", "Shipping", "QC", "Update",
                 "Return", "Scrap", "Hold", "Cancelled"]


def event_rule(event: str) -> dict:
    """What an event does, matched case-insensitively.

    An unknown event is treated as informational — visible on the part's own
    story, counted in nothing. Silently guessing that it moves stock would let
    a typo change a quantity.
    """
    text = str(event or "").strip().lower()
    for name, rule in MOVEMENT_EVENTS.items():
        if name.lower() == text:
            return rule
    return {"stock": "", "movements": False, "shipments": False}


def moves_stock(event: str) -> bool:
    return event_rule(event)["stock"] in ("in", "transfer", "out")


def on_movements(event: str) -> bool:
    return event_rule(event)["movements"]


def on_shipments(event: str) -> bool:
    return event_rule(event)["shipments"]

# Parts Tracker tabs — maintained by the team, read-only for this app.
TAB_OVERVIEW = "Overview"
TAB_MOVEMENTS = "Movement Log"
# Everything else in a tracker sheet is one tab per part (M101a, M107, …).
# Anything else in a record sheet is a part tab, so this set has to name every
# non-part tab exactly. A miss is not cosmetic: the tab is fetched, parsed as a
# part ledger and offered in Part Detail's part dropdown.
TRACKER_NON_PART_TABS = {
    TAB_OVERVIEW, TAB_MOVEMENTS, "Legend & How To", "Location Summary",
    "By Holder", "Stock by Holder", TAB_ORDERS, "Users", "Messages",
    "Settings", "Projects", TAB_HOLDERS, TAB_STOCK, TAB_STOCK_HISTORY,
    "Shipments", TAB_MOVEMENTS_MERGED, "Order Drafts",
}

# BOM sheet tabs (the NPD "BOM&quote" workbook) — read-only, never written.
# Build tabs (T2, DVT T1, …) are detected by their header, not by name;
# these two are the fixed, named tabs.
TAB_BOM_LIFECYCLE = "Parts and Order Tracking"   # titled "Parts Lifecycle & Order Tracking"
TAB_BOM_MOVEMENTS = "Parts and Samples Movement Log"

# --- Order statuses ---
# "processing" left the ladder on 28 Aug 2026 (Hamid: "too many unnecessary
# stuffs") — an order is new until it is ordered. Rows written before that
# keep their sheet cell untouched; orders_store reads a stored "processing"
# as "new".
ORDER_STATUSES = ["new", "ordered", "shipped", "delivered"]
STATUS_COLORS = {
    "new": "gray",
    "ordered": "orange",
    "shipped": "violet",
    "delivered": "green",
    "cancelled": "red",   # terminal, outside the ORDER_STATUSES ladder
}

# --- Manufacturing processes ---
PROCESS_OPTIONS = [
    "CNC Machining",
    "3D Print (FDM)",
    "3D Print (SLA/Resin)",
    "3D Print (SLS/MJF)",
    "Sheet Metal",
    "Injection Molding",
    "Silicone / Casting",
    "Off-the-shelf Part",
    "Other",
]

# --- Orders tab column headers (auto-created on first run) ---
# ChecklistJSON stays in this list although the checklist FEATURE was removed
# (28 Aug 2026): the live sheet has the column, and rows are written
# positionally against these headers — dropping it would shift every value
# after it into the wrong column. New rows write it blank.
# PartID/Version follow the NPD team's part-identity convention:
# M-code matches the BOM numbering (e.g. M107), Version is
# Major(CAD).Minor(tolerance/tooling).Batch — e.g. 2.1.1.
ORDERS_HEADERS = [
    "OrderID", "CreatedAt", "EngineerEmail", "EngineerName", "Status",
    "Project",
    "PartName", "PartID", "Version", "Process", "Material", "Finish",
    "Quantity", "Priority", "Recipient",
    "Vendor", "VendorOrderNum", "TrackingNum", "ETA",
    "Notes", "DriveFileLink", "ChecklistJSON", "Inspection",
    "PartsCostCNY", "ShippingCostCNY", "Reviewer",
]

# Currency conversion (manual rate, update as needed)
CNY_TO_GBP = 0.11

# --- Caching ---
# Seconds each data set is reused before the Sheet is read again. Lower =
# fresher data, more API calls. These are the defaults; an admin can change
# them at runtime on the Status page, which prices each choice.
TRACKER_TTL_SECONDS = int(os.environ.get("MECH_TRACKER_TTL", "120"))
ORDERS_TTL_SECONDS = int(os.environ.get("MECH_ORDERS_TTL", "60"))
USERS_TTL_SECONDS = int(os.environ.get("MECH_USERS_TTL", "60"))
MESSAGES_TTL_SECONDS = int(os.environ.get("MECH_MESSAGES_TTL", "30"))

# --- User configuration ---
# The live user list is the Users tab in the Google Sheet, managed from the
# User Management page. Everything below is only what happens when that tab
# cannot be read.
#
# Who the primary admin is comes from the ENVIRONMENT, not from this file. It
# used to be a hardcoded address, which meant one person's email shipped with
# the source — fine in a private repo, not fine in a public one, and wrong in
# either if somebody else installs this (19 Aug 2026).
#
#   MECH_ADMIN_EMAIL   the address(es) that always keep admin — one person
#                      often has two spellings across sister domains, so this
#                      takes a comma-separated list. The FIRST is the identity
#                      local development runs as and the one a fresh Users tab
#                      is seeded with; every listed one is protected from
#                      removal and kept admin by the fallback.
#   MECH_ADMIN_NAME    how that person is shown
#
# Unset is a valid state and fails CLOSED: no fallback admin, so an
# unreadable Users tab lets nobody in rather than letting in whoever the
# source happens to name. That costs nothing real — every page needs the
# sheets anyway, so if they are unreachable the app has nothing to show.
PRIMARY_ADMIN_EMAILS = tuple(
    e.strip().lower() for e in os.environ.get("MECH_ADMIN_EMAIL", "").split(",")
    if e.strip())
PRIMARY_ADMIN_EMAIL = PRIMARY_ADMIN_EMAILS[0] if PRIMARY_ADMIN_EMAILS else ""
PRIMARY_ADMIN_NAME = os.environ.get("MECH_ADMIN_NAME", "").strip() or "Admin"

# Some teams use two interchangeable email domains for the same people, and
# the user-management page offers to add both spellings of a new user. Which
# pair — if any — is the installation's business, not the source code's:
#   MECH_DOMAIN_ALIASES = "example.co.uk,example.com"
# Unset (or malformed) simply hides the offer.
_aliases = [d.strip().lower().lstrip("@")
            for d in os.environ.get("MECH_DOMAIN_ALIASES", "").split(",")
            if d.strip()]
DOMAIN_ALIASES = tuple(_aliases) if len(_aliases) == 2 else None

ALLOWED_USERS = {email: {"name": PRIMARY_ADMIN_NAME, "role": "admin"}
                 for email in PRIMARY_ADMIN_EMAILS}
