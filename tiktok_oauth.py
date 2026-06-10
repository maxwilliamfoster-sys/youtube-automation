"""
TikTok OAuth helper — one-time authorisation for the official Content Posting API.

This is the ONLY fully hands-off path (PC can be completely off / runs from the
cloud), because publishing happens server-to-server via TikTok's API rather than
by driving a browser.

⚠️  IMPORTANT — app audit:
    Until your TikTok developer app passes audit, the Content Posting API can only
    publish as PRIVATE / SELF_ONLY (visible to you only). Public posting unlocks
    after you submit the app for review and it's approved. See TIKTOK_API_SETUP.md.

Run once:
    python tiktok_oauth.py
It opens TikTok's consent screen, captures the redirect code on a tiny local
server, exchanges it for tokens, and stores them in tiktok_tokens.json.
Tokens auto-refresh from tiktok_api_poster.py — you should not need to repeat this.
"""

import json
import os
import secrets
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
TOKENS_FILE = os.path.join(BASE_DIR, "tiktok_tokens.json")

CLIENT_KEY    = os.getenv("TIKTOK_CLIENT_KEY", "")
CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "")

# Must EXACTLY match a redirect URI registered in your TikTok app settings.
REDIRECT_URI = os.getenv("TIKTOK_REDIRECT_URI", "http://localhost:8723/callback")

# video.publish = direct post; video.upload = upload to drafts. user.info.basic for open_id.
SCOPES = "user.info.basic,video.publish,video.upload"

AUTH_URL  = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"


def _save_tokens(data: dict):
    data["_obtained_at"] = int(time.time())
    with open(TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"[OAuth] Tokens saved to {TOKENS_FILE}")


def load_tokens() -> dict | None:
    if not os.path.exists(TOKENS_FILE):
        return None
    with open(TOKENS_FILE, encoding="utf-8") as f:
        return json.load(f)


def _exchange_code(code: str) -> dict:
    resp = requests.post(
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key":    CLIENT_KEY,
            "client_secret": CLIENT_SECRET,
            "code":          code,
            "grant_type":    "authorization_code",
            "redirect_uri":  REDIRECT_URI,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def refresh_access_token(refresh_token: str) -> dict:
    """Exchange a refresh token for a fresh access token."""
    resp = requests.post(
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key":     CLIENT_KEY,
            "client_secret":  CLIENT_SECRET,
            "grant_type":     "refresh_token",
            "refresh_token":  refresh_token,
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    _save_tokens(data)
    return data


def get_valid_access_token() -> str | None:
    """
    Return a non-expired access token, refreshing automatically if needed.
    Returns None if no tokens exist yet (run this module once to authorise).
    """
    tokens = load_tokens()
    if not tokens:
        return None
    obtained = tokens.get("_obtained_at", 0)
    expires_in = tokens.get("expires_in", 0)
    # Refresh a few minutes early to avoid edge-of-expiry failures.
    if time.time() >= obtained + expires_in - 300:
        rt = tokens.get("refresh_token")
        if not rt:
            return None
        print("[OAuth] Access token expired — refreshing...")
        tokens = refresh_access_token(rt)
    return tokens.get("access_token")


class _CallbackHandler(BaseHTTPRequestHandler):
    captured = {}

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != urllib.parse.urlparse(REDIRECT_URI).path:
            self.send_response(404)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(parsed.query)
        _CallbackHandler.captured = {k: v[0] for k, v in params.items()}
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h2>TikTok authorisation received. You can close this tab.</h2>")

    def log_message(self, *_):
        pass  # silence the default request logging


def authorize():
    if not CLIENT_KEY or not CLIENT_SECRET:
        print("[OAuth] Set TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET in .env first.")
        print("[OAuth] See TIKTOK_API_SETUP.md for how to obtain them.")
        return False

    state = secrets.token_urlsafe(16)
    query = urllib.parse.urlencode({
        "client_key":    CLIENT_KEY,
        "scope":         SCOPES,
        "response_type": "code",
        "redirect_uri":  REDIRECT_URI,
        "state":         state,
    })
    url = f"{AUTH_URL}?{query}"

    parsed = urllib.parse.urlparse(REDIRECT_URI)
    server = HTTPServer((parsed.hostname, parsed.port or 80), _CallbackHandler)

    print("[OAuth] Opening TikTok consent screen in your browser...")
    webbrowser.open(url)
    print(f"[OAuth] Waiting for the redirect on {REDIRECT_URI} ...")

    # Serve until the callback populates `captured` (or ~5 min pass).
    deadline = time.time() + 300
    while not _CallbackHandler.captured and time.time() < deadline:
        server.handle_request()

    captured = _CallbackHandler.captured
    if not captured.get("code"):
        print(f"[OAuth] No authorisation code received. Got: {captured}")
        return False
    if captured.get("state") != state:
        print("[OAuth] State mismatch — aborting for safety.")
        return False

    print("[OAuth] Code received — exchanging for tokens...")
    tokens = _exchange_code(captured["code"])
    if "access_token" not in tokens:
        print(f"[OAuth] Token exchange failed: {tokens}")
        return False
    _save_tokens(tokens)
    print("[OAuth] Done! The API poster can now publish on your behalf.")
    return True


if __name__ == "__main__":
    authorize()
