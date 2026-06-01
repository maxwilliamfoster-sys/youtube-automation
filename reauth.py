"""
One-time re-authentication to expand the YouTube OAuth token's scopes.

Adds read + analytics + manage access on top of the existing upload scope so the
automation can:
  - read each video's stats (views, likes, duration)        -> youtube.readonly / force-ssl
  - read retention metrics (avg view duration, % viewed)    -> yt-analytics.readonly
  - set a video's privacy (e.g. hide a bad upload)          -> youtube.force-ssl

Run:  python reauth.py
A browser window opens — log in with the CHANNEL's Google account and approve.
The refreshed token.json is written in place (then push it to the YOUTUBE_TOKEN secret).
"""

from google_auth_oauthlib.flow import InstalledAppFlow
from config import CREDENTIALS_FILE, TOKEN_FILE
from youtube_scopes import SCOPES

if __name__ == "__main__":
    print("Opening browser for YouTube re-authentication...")
    print("Scopes requested:")
    for s in SCOPES:
        print(f"  - {s}")
    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
    creds = flow.run_local_server(
        port=8080,
        open_browser=True,
        access_type="offline",     # ensure we get a refresh_token back
        prompt="consent",          # force the consent screen so refresh_token is returned
        include_granted_scopes="true",
    )
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    print("\n[OK] token.json updated with expanded scopes.")
    print("Granted scopes:", creds.scopes)
