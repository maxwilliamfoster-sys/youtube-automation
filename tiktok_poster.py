"""
TikTok Auto-Poster — @buriedcasefiles
Uploads finished documentary videos to TikTok using saved browser cookies.
Uses Playwright (Chromium) — no Chrome install required.

Cookies are auto-refreshed from Brave each time (run refresh_tiktok_cookies.py
if you ever see login errors).
"""

import os
import sys
import time
import json
from pathlib import Path

from config import (
    TIKTOK_HASHTAGS,
    TIKTOK_CAPTION_TEMPLATE,
    TIKTOK_COOKIES_FILE,
)

_COOKIES_TXT  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tiktok_cookies.txt")
_COOKIES_JSON = TIKTOK_COOKIES_FILE


# ─── Cookie helpers ────────────────────────────────────────────────────────────

def _find_cookies() -> str | None:
    for path in [_COOKIES_TXT, _COOKIES_JSON]:
        if os.path.exists(path) and os.path.getsize(path) > 20:
            return path
    return None


def _load_netscape_cookies(path: str) -> list:
    """Parse a Netscape cookies.txt into a list of Playwright cookie dicts."""
    cookies = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            domain, _, path_, secure, expires, name, value = parts[:7]
            cookies.append({
                "name":     name,
                "value":    value,
                "domain":   domain,
                "path":     path_,
                "secure":   secure == "TRUE",
                "httpOnly": False,
                "sameSite": "None",
                # expires: 0 means session cookie (no expiry)
                **({"expires": int(expires)} if int(expires) > 0 else {}),
            })
    return cookies


def _build_caption(title: str, story_hashtags: str) -> str:
    caption = TIKTOK_CAPTION_TEMPLATE.format(
        title=title,
        hashtags=TIKTOK_HASHTAGS,
        story_hashtags=story_hashtags,
    )
    return caption[:2200]


# ─── Main uploader ─────────────────────────────────────────────────────────────

def upload_to_tiktok(
    video_path:     str,
    title:          str,
    story_hashtags: str = "",
    retries:        int = 2,
) -> bool:
    """
    Upload a video to TikTok using Playwright + saved cookies.
    Returns True on success, False on failure (non-fatal).
    """
    cookies_path = _find_cookies()
    if not cookies_path:
        _print_setup_instructions()
        return False

    caption = _build_caption(title, story_hashtags)
    video_path = os.path.abspath(video_path)

    print(f"[TikTok] Uploading: {os.path.basename(video_path)}")
    print(f"[TikTok] Caption:   {caption[:80]}...")

    for attempt in range(1, retries + 1):
        try:
            _do_upload(video_path, caption, cookies_path)
            print(f"[TikTok] Upload successful!")
            return True
        except Exception as e:
            print(f"[TikTok] Attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                print("[TikTok] Retrying in 15s...")
                time.sleep(15)

    print("[TikTok] All attempts failed.")
    print(f"[TikTok] Upload manually: {video_path}")
    return False


def _do_upload(video_path: str, caption: str, cookies_path: str):
    """Run the Playwright upload session."""
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    cookies = _load_netscape_cookies(cookies_path)
    print(f"[TikTok] Loaded {len(cookies)} cookies from {os.path.basename(cookies_path)}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,          # visible so TikTok doesn't flag it
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-GB",
        )
        ctx.add_cookies(cookies)

        page = ctx.new_page()

        # ── Navigate to TikTok upload page ─────────────────────────────────
        print("[TikTok] Navigating to upload page...")
        # TikTok redirects creator-center/upload → tiktokstudio/upload
        page.goto(
            "https://www.tiktok.com/tiktokstudio/upload",
            wait_until="load", timeout=40_000,
        )
        time.sleep(5)   # SPA JS bootstrap

        # Check we're logged in
        if "login" in page.url.lower() or "passport" in page.url.lower():
            browser.close()
            raise RuntimeError(
                "TikTok redirected to login — cookies may have expired. "
                "Run: python refresh_tiktok_cookies.py"
            )

        print(f"[TikTok] On upload page: {page.url[:60]}")

        # ── Dismiss any consent / cookie banners ───────────────────────────
        for dismiss_sel in [
            "button:has-text('Decline optional cookies')",
            "button:has-text('Allow all')",
            "button:has-text('Cancel')",
            "[data-e2e='cookie-banner-decline']",
        ]:
            try:
                btn = page.locator(dismiss_sel).first
                if btn.is_visible(timeout=2_000):
                    btn.click()
                    time.sleep(0.5)
            except Exception:
                pass

        # ── Wait for the file input to be attached (it's hidden — that's normal) ─
        print("[TikTok] Waiting for upload UI...")
        page.wait_for_selector("input[type='file']", state="attached", timeout=30_000)

        # ── Upload the video file directly via the hidden input ─────────────
        print("[TikTok] Selecting video file...")
        file_input = page.locator("input[type='file'][accept*='video']").first
        file_input.set_input_files(video_path)

        # Wait for the processing/upload indicator to appear then disappear
        print("[TikTok] Uploading video (may take 1-3 min)...")
        try:
            # Wait for a processing indicator to show up
            page.wait_for_selector(
                "[class*='progress'], [class*='uploading'], [class*='processing']",
                timeout=15_000,
            )
            # Then wait for it to disappear (upload complete)
            page.wait_for_selector(
                "[class*='progress'], [class*='uploading'], [class*='processing']",
                state="hidden", timeout=300_000,
            )
        except Exception:
            # Indicator never showed — upload might have been instant (small file)
            time.sleep(8)
        print("[TikTok] Video upload complete.")

        # ── Dismiss any dialogs that appeared post-upload ─────────────────────
        for dismiss_sel in [
            "button:has-text('Cancel')",
            "button:has-text('Decline optional cookies')",
            "button:has-text('Got it')",
            "button:has-text('Close')",
        ]:
            try:
                btn = page.locator(dismiss_sel).first
                if btn.is_visible(timeout=1_500):
                    btn.click()
                    time.sleep(0.5)
            except Exception:
                pass

        # ── Set the caption (TikTok Studio uses a Draft.js rich text editor) ─
        print("[TikTok] Setting caption...")
        time.sleep(2)
        # Draft.js editor: class="notranslate public-DraftEditor-content"
        caption_field = page.locator(".public-DraftEditor-content").first
        caption_field.wait_for(state="visible", timeout=15_000)
        caption_field.click()
        time.sleep(0.3)
        page.keyboard.press("Control+a")
        time.sleep(0.2)
        page.keyboard.type(caption, delay=15)

        time.sleep(1)

        # ── Post ─────────────────────────────────────────────────────────────
        print("[TikTok] Clicking Post...")
        # Try several selectors TikTok has used over time
        for selector in [
            "button:has-text('Post')",
            "button:has-text('Publish')",
            "[data-e2e='post-btn']",
            "button[class*='post']",
        ]:
            try:
                btn = page.locator(selector).first
                btn.wait_for(state="visible", timeout=5_000)
                btn.click()
                break
            except Exception:
                continue

        # Wait for redirect back to creator center (indicates success)
        try:
            page.wait_for_url("**/creator-center**", timeout=30_000)
        except Exception:
            time.sleep(5)   # fallback — post may have submitted anyway

        print("[TikTok] Post submitted.")
        time.sleep(3)
        browser.close()


# ─── Cookie refresh helper ─────────────────────────────────────────────────────

def refresh_cookies_from_brave() -> bool:
    """
    Re-extract TikTok cookies from the running Brave browser.
    Call this if uploads start failing with login errors.
    """
    import subprocess
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "refresh_tiktok_cookies.py")
    if not os.path.exists(script):
        print("[TikTok] refresh_tiktok_cookies.py not found — re-run the cookie setup.")
        return False
    result = subprocess.run([sys.executable, script], capture_output=False)
    return result.returncode == 0


def _print_setup_instructions():
    print("""
[TikTok] No session cookies found.
Run:  python refresh_tiktok_cookies.py
This will extract your TikTok session from Brave automatically.
""")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def _cli():
    import argparse
    parser = argparse.ArgumentParser(description="TikTok auto-poster for @buriedcasefiles")
    parser.add_argument("--test",  action="store_true", help="Upload the latest video as a test")
    parser.add_argument("--video", type=str, help="Upload a specific video")
    parser.add_argument("--title", type=str, default="True Crime Documentary")
    parser.add_argument("--tags",  type=str, default="")
    args = parser.parse_args()

    if args.test or args.video:
        if args.video:
            video = args.video
        else:
            output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
            videos = sorted(Path(output_dir).glob("*.mp4"), key=os.path.getmtime, reverse=True)
            if not videos:
                print("[TikTok] No videos in output/ — generate one first.")
                sys.exit(1)
            video = str(videos[0])
            print(f"[TikTok] Using latest: {os.path.basename(video)}")
        ok = upload_to_tiktok(video, args.title, args.tags)
        sys.exit(0 if ok else 1)
    else:
        parser.print_help()


if __name__ == "__main__":
    _cli()
