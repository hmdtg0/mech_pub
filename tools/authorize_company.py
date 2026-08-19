"""Mint the app's Google token from the company's OAuth client — run ONCE.

Why this exists: Google meters API calls per GCP project, and a credential
decides whose project pays. The service account bills the development
project, which the developer's other tools share — so a busy day for this
app could starve them. A token minted from the COMPANY's OAuth client bills
the company's project instead, isolating the quota (Hamid, 19 Aug 2026).

What this script does:

1. Finds the company's OAuth client file (`client_secret_*.json`) next to
   the app — the file IS NOT a credential, it is the key-maker.
2. Opens a browser for a one-time Google consent. **Sign in with an account
   that can edit the project sheets.** Whichever account consents is the
   name Google's own edit history will show for the app's writes — a
   neutral/shared account keeps that history from reading as one person.
   (Who did what in the APP is separate: every ledger row is stamped with
   the signed-in user regardless.)
3. Saves the resulting token to `data/google_token.json` — inside the
   gitignored data folder, so it cannot be committed. From the next start,
   the app prefers this token automatically; `service_account.json` stays
   untouched as the fallback.

For a Streamlit Cloud deployment, copy the matching fields out of the saved
file into the secrets box (values are in the file — deliberately not printed
here):

    [google_oauth_token]
    client_id = "...";  client_secret = "...";  refresh_token = "..."
    token_uri = "https://oauth2.googleapis.com/token"

One trap worth knowing BEFORE consenting: if the OAuth consent screen of the
company project is still in "Testing" status, Google expires the refresh
token after 7 days and the app dies weekly. Set the consent screen to
"Internal" (Workspace) or publish it, and the token lives until revoked.

Usage:  python tools/authorize_company.py [path-to-client_secret.json]
"""
import glob
import os
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_DIR)

TOKEN_FILE = os.path.join(APP_DIR, "data", "google_token.json")
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def find_client_file() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    hits = sorted(glob.glob(os.path.join(APP_DIR, "client_secret_*.json")))
    if not hits:
        sys.exit("No client_secret_*.json found next to the app. Pass the "
                 "path as an argument.")
    if len(hits) > 1:
        print("Several client files found; using the first:")
        for h in hits:
            print("  ", os.path.basename(h))
    return hits[0]


def main() -> None:
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        sys.exit("google-auth-oauthlib is not installed. It is only needed "
                 "for this one-time step:  pip install google-auth-oauthlib")

    client_file = find_client_file()
    print("OAuth client:", os.path.basename(client_file))
    print()
    print("A browser window will open. Sign in with an account that can EDIT")
    print("the project sheets — that account becomes the name on Google's own")
    print("edit history for everything the app writes.")
    print()

    flow = InstalledAppFlow.from_client_secrets_file(client_file, SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)

    if not creds.refresh_token:
        # Google only hands the refresh token out on the FIRST consent for a
        # client+account pair; a re-consent without revoking returns none and
        # the token would die within the hour.
        sys.exit("Google returned no refresh token. Remove this app's access "
                 "at myaccount.google.com/permissions, then run this again.")

    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    with open(TOKEN_FILE, "w", encoding="utf-8") as fh:
        fh.write(creds.to_json())
    print()
    print("Token saved:", TOKEN_FILE)
    print("The app will prefer it from the next start; service_account.json")
    print("is untouched and remains the fallback. For Streamlit Cloud, copy")
    print("client_id / client_secret / refresh_token from that file into a")
    print("[google_oauth_token] block in the secrets box.")

    # Prove the token works before calling it done: one metadata read of the
    # main record, through the app's own loader.
    sheet_id = os.environ.get("MECH_CENTRAL_SHEET_ID", "")
    if sheet_id:
        import gspread
        title = gspread.authorize(creds).open_by_key(sheet_id).title
        print()
        print("Verified — read the main record as the new identity: %r" % title)
    else:
        print()
        print("MECH_CENTRAL_SHEET_ID not set in this shell, so the read-back "
              "check was skipped.")


if __name__ == "__main__":
    main()
