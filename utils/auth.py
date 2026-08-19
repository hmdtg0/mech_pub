"""Who is using the app, and what they are allowed to do.

**The app never decides who you are — something in front of it does.** In a
deployment the request has already passed an identity provider, and that
provider hands us a verified email:

- **Streamlit Community Cloud**, for a private app, exposes the signed-in
  viewer (`st.context.user` on newer builds, `st.experimental_user` on 1.38).
- **Cloudflare Access**, for the office-server plan, injects the verified
  address as a request header. That header is only trustworthy because
  Streamlit binds to 127.0.0.1 and the tunnel is the sole route in — see
  `setup_server.md` step 3.

The `Users` tab then says whether that person is known and what role they
have. Two separate questions: the provider says WHO, the sheet says WHETHER
and WHAT AS.

Until 19 Aug 2026 there was a third path — a dropdown of every name on the
Users tab, with no check at all. Anyone who reached the app picked a name and
became that person, admin included. It now defaults OFF, because writes are
stamped with the logged-in email: with a real identity the ledger says who
did something, and with a name picker it says who they claimed to be, which
is worse than saying nothing.
"""
from __future__ import annotations

import os

import streamlit as st

from config import IS_LOCAL, PRIMARY_ADMIN_EMAIL, PRIMARY_ADMIN_NAME


# Streamlit fills `experimental_user` with a stand-in when nothing has
# actually signed anybody in — a plain `streamlit run` reports
# test@example.com. Verified on 1.38 while testing this file: the deployed
# path claimed to be "signed in as test@example.com" on a local server with
# no identity provider at all. Treating that as a real address would let a
# self-hosted app hand out whatever role that address happens to hold, and
# would mask the Cloudflare header underneath it.
_PLACEHOLDER_EMAILS = {"test@example.com", "test@test.com", "user@example.com"}


def _first_email(source) -> str:
    """An `email` off a dict or an object, whichever shape the API returns."""
    if not source:
        return ""
    if isinstance(source, dict):
        value = source.get("email", "")
    else:
        value = getattr(source, "email", "")
    email = str(value or "").lower().strip()
    return "" if email in _PLACEHOLDER_EMAILS else email


def _streamlit_email() -> str:
    """The viewer Streamlit has signed in, or "".

    Both API generations are tried: `st.context.user` exists on newer builds,
    `st.experimental_user` is what 1.38 has. Neither is populated unless the
    app is deployed and private, which is exactly when we want it.
    """
    for holder, attr in ((st, "context"), (st, "experimental_user")):
        try:
            source = getattr(holder, attr, None)
            if attr == "context":
                source = getattr(source, "user", None) if source else None
            email = _first_email(source)
            if email:
                return email
        except Exception:
            continue
    return ""


def _cloudflare_email() -> str:
    """The address Cloudflare Access verified, or "" outside the tunnel."""
    try:
        headers = getattr(st.context, "headers", None) or {}
        for key in ("Cf-Access-Authenticated-User-Email",
                    "cf-access-authenticated-user-email"):
            email = str(headers.get(key, "") or "").lower().strip()
            if email:
                return email
    except Exception:
        pass
    return ""


def verified_email() -> str:
    """The email an identity provider vouched for, or "" if none did.

    Cloudflare is asked FIRST. Behind the tunnel its header is the definitive
    answer, and Streamlit's own user object is a stand-in there — letting the
    stand-in win would hide the real address.
    """
    return _cloudflare_email() or _streamlit_email()


def _name_login_allowed() -> bool:
    """The break-glass switch for the old name picker.

    Off unless deliberately turned on, and only somebody who can edit the
    app's secrets or environment can turn it on — not a visitor. It exists so
    a deployment where the verified email does not arrive is recoverable
    without a code change; it is not a login method.
    """
    if os.environ.get("MECH_ALLOW_NAME_LOGIN", "") == "1":
        return True
    # Guarded by the file check for the reason google_client documents:
    # touching `st.secrets` with no secrets.toml present makes Streamlit paint
    # a red "No secrets found" box BEFORE it raises, and try/except cannot
    # take that back. On a server with no secrets file the sign-in page would
    # otherwise greet everyone with two alarming errors about a file they do
    # not need.
    from utils.google_client import _secrets_file_exists

    if not _secrets_file_exists():
        return False
    try:
        return str(st.secrets.get("allow_name_login", "")).lower() in (
            "1", "true", "yes")
    except Exception:
        return False


def _get_allowed_users() -> dict:
    """Who may use the app, from the Users tab (config as the fallback)."""
    try:
        from utils.user_store import fetch_allowed_users
        return fetch_allowed_users()
    except Exception:
        from config import ALLOWED_USERS
        return ALLOWED_USERS


def _prompt_name_login() -> str:
    """The old picker — reachable only via the break-glass switch."""
    st.warning(
        "**Name sign-in is on.** This asks who you are and believes the "
        "answer, so anything recorded now is stamped with a name that was "
        "chosen rather than checked. Turn it off (`allow_name_login`) once "
        "the verified sign-in works.")
    allowed = _get_allowed_users()
    by_name = {}
    for email, info in allowed.items():
        by_name.setdefault(info["name"], email)
    chosen = st.selectbox("Who are you?", ["-- Select --"] + sorted(by_name))
    if chosen != "-- Select --":
        st.session_state["auth_email"] = by_name[chosen]
        return by_name[chosen]
    return st.session_state.get("auth_email", "")


def impersonation() -> "dict | None":
    """The REAL person behind a "View as", or None when nobody is pretending.

    "View as" lets an admin see the app exactly as a teammate does — their
    role's navigation, their My Orders — without what the PCB tool did, which
    was let anyone BE anyone. The difference is enforced one layer down:
    while a view-as is active, every writer refuses (see impersonation_block),
    so the ledger can never carry a name that did not act.
    """
    return st.session_state.get("view_as_real") or None


def impersonation_block() -> str:
    """Why writes are refused right now — "" when they are allowed.

    Called by every function that writes to a sheet, as its first line. The
    guard lives in the writers rather than the pages so that a page nobody
    remembered to update still cannot write somebody else's name into the
    ledger.
    """
    real = impersonation()
    if not real:
        return ""
    return ("Viewing as someone else (you are %s) — writes are disabled "
            "while a View-as is active. Switch back to yourself to record "
            "anything." % real.get("email", "?"))


def _viewed_user() -> "dict | None":
    """The person an admin is currently viewing as, or None.

    Only an admin can view-as, the target must be on the Users tab, and the
    REAL identity is kept in session state for the writers' guard and the
    sidebar's "viewed by" line.
    """
    target = st.session_state.get("view_as", "")
    if not target:
        return None
    info = _get_allowed_users().get(target)
    if info is None:
        st.session_state.pop("view_as", None)
        st.session_state.pop("view_as_real", None)
        return None
    return {"email": target, "name": info["name"], "role": info["role"]}


def get_current_user(prompt: bool = True) -> dict | None:
    """{email, name, role} for the current person, or None.

    `prompt=False` only LOOKS — it never draws the name picker. The app shell
    peeks first to decide whether to build the real navigation; drawing from
    both the peek and the later `require_auth` put two identical pickers on
    the page, which collides their widget ids and crashes the login screen.
    """
    if IS_LOCAL:
        # Local development runs as whoever the installation names, falling
        # back to a generic identity so a fresh clone starts without any
        # setup. Set MECH_ADMIN_EMAIL to your own address: every write is
        # stamped with it, and "dev@localhost" in the ledger tells nobody
        # anything.
        real = {"email": PRIMARY_ADMIN_EMAIL or "dev@localhost",
                "name": PRIMARY_ADMIN_NAME, "role": "admin"}
        return _apply_view_as(real)

    email = verified_email()
    if not email and _name_login_allowed():
        # A name picked on an earlier run lives in the session either way;
        # only the widget itself is prompt-gated.
        email = st.session_state.get("auth_email", "")
        if not email and prompt:
            email = _prompt_name_login()
    if not email:
        return None

    info = _get_allowed_users().get(email)
    if info is None:
        return None
    return _apply_view_as(
        {"email": email, "name": info["name"], "role": info["role"]})


def _apply_view_as(real: dict) -> dict:
    """The user the app should RENDER for — the view-as target when an admin
    has one active, the real person otherwise.

    The target's name, email and role are returned whole, because seeing the
    app as someone means matching as them too (My Orders matches on name and
    contact email). What keeps this honest is the writers' guard: the real
    identity is pinned in session state and every write refuses while it is
    there.
    """
    if real.get("role") != "admin":
        # Only admins view-as; anything a non-admin managed to leave in
        # session state is cleared rather than honoured.
        st.session_state.pop("view_as", None)
        st.session_state.pop("view_as_real", None)
        return real
    viewed = _viewed_user()
    if viewed is None or viewed["email"] == real["email"]:
        st.session_state.pop("view_as_real", None)
        return real
    st.session_state["view_as_real"] = real
    return viewed


def require_auth() -> dict:
    """Stop the page unless a known person is signed in.

    Fails CLOSED, and says which of the two gates refused — being unknown to
    the sheet and being unverified need different fixes, and "access denied"
    sends people to the wrong one.
    """
    user = get_current_user()
    if user is not None:
        return user

    email = verified_email()
    if not email and _name_login_allowed():
        # The picker is already on screen (drawn by get_current_user just
        # now) and nothing is chosen yet. The picker IS the message — an
        # error underneath it would tell the person something is wrong when
        # they simply have not answered yet.
        st.stop()
    if email:
        st.error(
            "You are signed in as **%s**, but that address is not on the "
            "app's user list — so there is nothing here for you yet. Ask "
            "an administrator to add it on the Users tab." % email)
    else:
        st.error(
            "**Could not confirm who you are.** This app takes your identity "
            "from the sign-in in front of it, and no verified address "
            "arrived with this request.")
        st.caption(
            "If you are the person deploying: the app must be **private** "
            "with your team on its viewer list, so the signed-in address "
            "reaches it. To get in while sorting that out, set "
            "`allow_name_login = \"1\"` in the app's secrets — and turn it "
            "off afterwards.")
    st.stop()


def require_role(*roles: str) -> dict:
    """Stop the page unless the person holds one of `roles`.

    Admin passes everything. Several roles can be named because the work
    pages — receiving goods, recording movements, correcting history — are
    done by the people who handle the parts, not only by admins. Reserving
    them for admin meant the two people who know where things went, Ryan and
    Jimmy, could not reach the page where it gets recorded (19 Aug 2026).
    """
    user = require_auth()
    if is_admin(user) or user["role"] in roles:
        return user
    st.error(
        "This page is for %s. Your account is **%s** — ask an administrator if that is "
        "wrong." % (" or ".join("**%s**" % r for r in roles), user["role"]))
    st.stop()


def is_admin(user: dict) -> bool:
    """Check if user has admin role."""
    return user.get("role") == "admin"


def is_logistics(user: dict) -> bool:
    """Check if user has logistics role."""
    return user.get("role") == "logistics"
