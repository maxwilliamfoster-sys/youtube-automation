"""
refresh_tiktok_cookies.py — Re-export TikTok cookies from running Brave browser.

Run this any time TikTok uploads fail with login errors (cookies expire ~2 months).
Brave must be open and logged into TikTok.

Usage:
    python refresh_tiktok_cookies.py
"""

import asyncio
import os
import sys
import subprocess
import time
from pathlib import Path

OUTPUT_PATH  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tiktok_cookies.txt")
BRAVE_EXE    = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
DEBUG_PORT   = 9223   # separate port to avoid clashing with any existing debug session


async def _extract_via_playwright(ws_endpoint: str) -> list:
    """Connect to running Brave via CDP and extract TikTok cookies."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(ws_endpoint)
        if not browser.contexts:
            await browser.close()
            return []

        ctx = browser.contexts[0]

        # Navigate to TikTok if not already there
        tiktok_page = next(
            (pg for pg in ctx.pages if "tiktok" in pg.url.lower()), None
        )
        if not tiktok_page:
            tiktok_page = await ctx.new_page()
            await tiktok_page.goto("https://www.tiktok.com", wait_until="domcontentloaded", timeout=20_000)
            await asyncio.sleep(3)

        cookies = await ctx.cookies(["https://www.tiktok.com", "https://tiktok.com"])
        tiktok_cookies = [c for c in cookies if "tiktok" in c.get("domain", "").lower()]
        await browser.close()
        return tiktok_cookies


def _save_netscape(cookies: list, path: str):
    lines = ["# Netscape HTTP Cookie File", "# Refreshed from Brave by refresh_tiktok_cookies.py", ""]
    for c in cookies:
        domain    = c["domain"]
        subdomain = "TRUE" if domain.startswith(".") else "FALSE"
        path_     = c.get("path", "/")
        secure    = "TRUE" if c.get("secure", False) else "FALSE"
        expires   = int(c.get("expires", 0))
        if expires < 0:
            expires = 0
        name  = c["name"]
        value = c.get("value", "")
        lines.append(f"{domain}\t{subdomain}\t{path_}\t{secure}\t{expires}\t{name}\t{value}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    print("[CookieRefresh] Checking for running Brave with debug port...")

    # Try existing Brave debug instance first (port 9222)
    import urllib.request, json
    for port in [9222, DEBUG_PORT]:
        try:
            resp = urllib.request.urlopen(f"http://localhost:{port}/json/version", timeout=3)
            version = json.loads(resp.read())
            ws_url = version["webSocketDebuggerUrl"]
            print(f"[CookieRefresh] Found Brave debug on port {port}")

            cookies = asyncio.run(_extract_via_playwright(ws_url))
            if not cookies:
                print("[CookieRefresh] No TikTok cookies found on this port — trying next...")
                continue

            key_names = [c["name"] for c in cookies]
            print(f"[CookieRefresh] {len(cookies)} TikTok cookies extracted")
            if "sessionid" not in key_names:
                print("[CookieRefresh] WARNING: sessionid missing — are you logged in to TikTok in Brave?")
                sys.exit(1)

            _save_netscape(cookies, OUTPUT_PATH)
            print(f"[CookieRefresh] Saved to {OUTPUT_PATH}")
            print(f"[CookieRefresh] Key cookies: {[n for n in key_names if n in ('sessionid','sid_guard','uid_tt')]}")
            print("[CookieRefresh] Done!")
            return

        except Exception:
            pass

    # No debug port running — launch Brave with debug port temporarily
    print(f"[CookieRefresh] No Brave debug port found. Launching Brave on port {DEBUG_PORT}...")
    proc = subprocess.Popen(
        [BRAVE_EXE, f"--remote-debugging-port={DEBUG_PORT}",
         "--no-first-run", "https://www.tiktok.com"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    print("[CookieRefresh] Waiting for Brave to start (8s)...")
    time.sleep(8)

    try:
        resp = urllib.request.urlopen(f"http://localhost:{DEBUG_PORT}/json/version", timeout=5)
        version = json.loads(resp.read())
        ws_url = version["webSocketDebuggerUrl"]
    except Exception as e:
        print(f"[CookieRefresh] Could not connect to Brave: {e}")
        print("[CookieRefresh] Open Brave, log into TikTok, then run this script again.")
        sys.exit(1)

    print("[CookieRefresh] Connected. Waiting for TikTok to load (5s)...")
    time.sleep(5)

    cookies = asyncio.run(_extract_via_playwright(ws_url))
    # Kill the debug Brave instance we spawned (cleanup)
    proc.terminate()

    if not cookies:
        print("[CookieRefresh] No cookies found. Log into TikTok in Brave and retry.")
        sys.exit(1)

    key_names = [c["name"] for c in cookies]
    if "sessionid" not in key_names:
        print("[CookieRefresh] sessionid missing — please log into TikTok in Brave first.")
        sys.exit(1)

    _save_netscape(cookies, OUTPUT_PATH)
    print(f"[CookieRefresh] {len(cookies)} cookies saved to {OUTPUT_PATH}")
    print("[CookieRefresh] Done! TikTok uploads will work automatically.")


if __name__ == "__main__":
    main()
