"""
TikTok cookie refresh — run this when TikTok upload starts failing.

    py refresh_cookies.py

Opens a real Chromium browser, lets you log in to TikTok manually,
then saves the session cookies to tiktok_cookies.txt automatically.
"""

import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_DIR    = Path(__file__).parent
COOKIES_OUT = BASE_DIR / "tiktok_cookies.txt"


def main():
    print("\n" + "=" * 52)
    print("  BuriedCasefiles — TikTok Cookie Refresh")
    print("=" * 52)
    print("""
A browser window will open on TikTok's login page.

  1. Log in to your TikTok account normally
  2. Once you're on the home page / For You page,
     come back here and press Enter
""")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=50)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        page.goto("https://www.tiktok.com/login", wait_until="domcontentloaded")

        input("Press Enter once you're logged in and on the home page...")

        # Save cookies in Netscape format (what tiktok_poster.py expects)
        cookies = context.cookies()
        lines = ["# Netscape HTTP Cookie File"]
        for c in cookies:
            domain   = c.get("domain", "")
            flag     = "TRUE" if domain.startswith(".") else "FALSE"
            path     = c.get("path", "/")
            secure   = "TRUE" if c.get("secure") else "FALSE"
            expires  = int(c.get("expires", 0)) if c.get("expires") else 0
            name     = c.get("name", "")
            value    = c.get("value", "")
            lines.append(f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}")

        COOKIES_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
        browser.close()

    print(f"\n[OK] Cookies saved to: {COOKIES_OUT}")
    print("     The automation will use these on the next run.")

    # Also notify via Telegram that cookies were refreshed
    try:
        from notify import send_alert
        send_alert("✅ <b>TikTok cookies refreshed</b> — automation will resume normally.")
    except Exception:
        pass

    print("\n" + "=" * 52)
    print("  Done. You can close this window.")
    print("=" * 52 + "\n")


if __name__ == "__main__":
    main()
