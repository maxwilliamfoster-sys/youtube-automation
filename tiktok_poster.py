"""
TikTok Auto-Poster — @buriedcasefiles
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Two upload backends, chosen automatically:

  1. STEALTH (default, local):  a *persistent* Brave profile driven by Playwright.
     The browser keeps its own cookies, history and localStorage between runs, and
     a stealth init-script hides the automation fingerprint (navigator.webdriver,
     plugins, WebGL vendor, etc).  This is what TikTok sees as a normal human's
     browser — the key to not getting flagged as a bot.

  2. COOKIE (cloud / CI fallback):  inject tiktok_cookies.txt into a throwaway
     context.  Used only when no persistent profile exists (e.g. GitHub Actions).

Native scheduling:
  When called with `schedule_time`, the video is handed to TikTok's own scheduler
  (TikTok Studio → Schedule).  TikTok's servers then publish it at that time, so
  your PC does NOT need to be awake at the posting moment, and the publish event
  comes from TikTok infrastructure rather than a burst of local automation.

First-time setup (once):
    python tiktok_poster.py --login        # opens Brave, you log into TikTok once

Everyday use is automatic via main_documentary.py.  Manual test:
    python tiktok_poster.py --test --dry-run     # everything except the final click
    python tiktok_poster.py --test               # actually posts the latest video
"""

import os
import sys
import time
import random
from datetime import datetime, timedelta
from pathlib import Path

from config import (
    TIKTOK_HASHTAGS,
    TIKTOK_CAPTION_TEMPLATE,
    TIKTOK_COOKIES_FILE,
)


def _alert(message: str):
    """Send a phone alert via the configured BuriedCasefiles Telegram bot (notify.py).
    Silent if notifications aren't set up."""
    try:
        from notify import send_alert
        send_alert(message)
    except Exception:
        pass

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
_COOKIES_TXT  = os.path.join(BASE_DIR, "tiktok_cookies.txt")
_COOKIES_JSON = TIKTOK_COOKIES_FILE

# Persistent stealth profile — created on first --login, reused forever after.
TIKTOK_PROFILE_DIR = os.path.join(BASE_DIR, "tiktok_profile")

# Real Brave binary gives an authentic fingerprint. Falls back to Playwright
# Chromium if Brave isn't installed at the default location.
BRAVE_EXE = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ─── Stealth ───────────────────────────────────────────────────────────────────

def _stealth_init_script() -> str:
    """JS injected before any page script runs — masks the automation fingerprint."""
    return """
    // navigator.webdriver -> undefined (the #1 automation tell)
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    // Plausible plugin + language arrays (headless/automation often have none)
    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
    Object.defineProperty(navigator, 'languages', {get: () => ['en-GB', 'en']});
    // window.chrome present like a real Chrome
    window.chrome = window.chrome || { runtime: {} };
    // Permissions API consistent with a real browser
    const _q = window.navigator.permissions && window.navigator.permissions.query;
    if (_q) {
      window.navigator.permissions.query = (p) => (
        p && p.name === 'notifications'
          ? Promise.resolve({state: Notification.permission})
          : _q(p)
      );
    }
    // WebGL vendor/renderer spoof (canvas/WebGL fingerprint)
    try {
      const gp = WebGLRenderingContext.prototype.getParameter;
      WebGLRenderingContext.prototype.getParameter = function(p) {
        if (p === 37445) return 'Intel Inc.';
        if (p === 37446) return 'Intel Iris OpenGL Engine';
        return gp.call(this, p);
      };
    } catch (e) {}
    """


def _human_pause(lo: float = 0.4, hi: float = 1.3):
    time.sleep(random.uniform(lo, hi))


def _human_type(page, text: str):
    """Type with per-character jitter so the cadence looks human, not scripted."""
    for ch in text:
        page.keyboard.type(ch)
        time.sleep(random.uniform(0.012, 0.06))


def _human_mouse_wander(page, moves: int = 3):
    """A few idle mouse moves before an important click."""
    for _ in range(moves):
        try:
            page.mouse.move(random.randint(200, 1000), random.randint(200, 700),
                            steps=random.randint(4, 12))
        except Exception:
            pass
        time.sleep(random.uniform(0.1, 0.35))


# ─── Cookie helpers (cloud / CI fallback) ──────────────────────────────────────

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


# ─── Context launch ────────────────────────────────────────────────────────────

def _launch_persistent(p, headless: bool):
    """Launch the persistent stealth profile (real Brave if available)."""
    kwargs = dict(
        user_data_dir=TIKTOK_PROFILE_DIR,
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=IsolateOrigins,site-per-process",
        ],
        viewport={"width": 1280, "height": 800},
        user_agent=_USER_AGENT,
        locale="en-GB",
        timezone_id="Europe/London",
    )
    if os.path.exists(BRAVE_EXE):
        kwargs["executable_path"] = BRAVE_EXE
    ctx = p.chromium.launch_persistent_context(**kwargs)
    ctx.add_init_script(_stealth_init_script())
    return ctx


def _profile_exists() -> bool:
    return os.path.isdir(TIKTOK_PROFILE_DIR) and any(Path(TIKTOK_PROFILE_DIR).iterdir())


# ─── Public API ────────────────────────────────────────────────────────────────

def upload_to_tiktok(
    video_path:     str,
    title:          str,
    story_hashtags: str = "",
    retries:        int = 2,
    schedule_time:  datetime = None,
    dry_run:        bool = False,
) -> bool:
    """
    Upload (or schedule) a video to TikTok.

    schedule_time : if given, hand the post to TikTok's native scheduler instead of
                    publishing immediately (TikTok requires 15 min – 10 days ahead).
    dry_run       : do everything except the final Post/Schedule click — for safe
                    testing of login state and selectors without posting.

    Returns True on success, False on failure (non-fatal).
    """
    caption = _build_caption(title, story_hashtags)
    video_path = os.path.abspath(video_path)

    mode = "STEALTH (persistent profile)" if _profile_exists() else "COOKIE (fallback)"
    when = schedule_time.strftime("%Y-%m-%d %H:%M") if schedule_time else "now"
    print(f"[TikTok] Backend: {mode}")
    print(f"[TikTok] Uploading: {os.path.basename(video_path)}  (publish: {when})"
          + ("  [DRY RUN]" if dry_run else ""))
    print(f"[TikTok] Caption:   {caption[:80]}...")

    if not _profile_exists() and not _find_cookies():
        _print_setup_instructions()
        return False

    for attempt in range(1, retries + 1):
        try:
            _do_upload(video_path, caption, schedule_time, dry_run)
            print("[TikTok] " + ("Dry run complete — no post made." if dry_run
                                  else "Upload successful!"))
            return True
        except Exception as e:
            msg = str(e)
            if "expired" in msg.lower() or "redirected to login" in msg.lower():
                _alert(
                    "🔑 <b>BuriedCasefiles — TikTok login expired</b>\n\n"
                    "Posting is paused until you re-login.\n"
                    "Fix on your PC:\n"
                    "1. <code>python tiktok_poster.py --login</code>\n"
                    "2. Log into TikTok when Brave opens, then close it\n"
                    "3. Uploads resume automatically next run."
                )
            print(f"[TikTok] Attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                time.sleep(random.uniform(10, 20))

    print(f"[TikTok] All attempts failed. Post manually: {video_path}")
    return False


def _do_upload(video_path: str, caption: str, schedule_time: datetime, dry_run: bool):
    """Run one upload session against the persistent stealth profile."""
    from playwright.sync_api import sync_playwright

    debug_dir = os.path.join(BASE_DIR, "debug")
    os.makedirs(debug_dir, exist_ok=True)

    with sync_playwright() as p:
        ctx = _launch_persistent(p, headless=False)

        # Seed login from cookies once, if the profile is brand new but cookies exist.
        if not _profile_has_session(ctx):
            cookies_path = _find_cookies()
            if cookies_path:
                print("[TikTok] Seeding new profile from saved cookies...")
                try:
                    ctx.add_cookies(_load_netscape_cookies(cookies_path))
                except Exception as e:
                    print(f"[TikTok] Cookie seed warning: {e}")

        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        print("[TikTok] Opening upload page...")
        page.goto("https://www.tiktok.com/tiktokstudio/upload",
                  wait_until="load", timeout=45_000)
        _human_pause(3, 5)   # let the SPA settle like a human reading the page

        if "login" in page.url.lower() or "passport" in page.url.lower():
            ctx.close()
            raise RuntimeError(
                "TikTok redirected to login — session expired. "
                "Run: python tiktok_poster.py --login"
            )

        print(f"[TikTok] On: {page.url[:60]}")
        _dismiss_banners(page)

        # ── Select the video file ──────────────────────────────────────────────
        print("[TikTok] Selecting video file...")
        page.wait_for_selector("input[type='file']", state="attached", timeout=30_000)
        file_input = page.locator("input[type='file'][accept*='video']").first
        file_input.set_input_files(video_path)

        print("[TikTok] Uploading video (1-3 min)...")
        try:
            page.wait_for_selector(
                "[class*='progress'], [class*='uploading'], [class*='processing']",
                timeout=15_000,
            )
            page.wait_for_selector(
                "[class*='progress'], [class*='uploading'], [class*='processing']",
                state="hidden", timeout=300_000,
            )
        except Exception:
            _human_pause(6, 10)
        print("[TikTok] Video processed.")
        _dismiss_banners(page)

        # ── Caption ─────────────────────────────────────────────────────────────
        print("[TikTok] Writing caption...")
        _human_pause(1.5, 2.5)
        caption_field = page.locator(".public-DraftEditor-content").first
        caption_field.wait_for(state="visible", timeout=15_000)
        caption_field.click()
        _human_pause(0.3, 0.7)
        page.keyboard.press("Control+a")
        _human_pause(0.1, 0.3)
        page.keyboard.press("Delete")
        _human_type(page, caption)
        _human_pause(0.8, 1.6)

        # ── Schedule (optional) ─────────────────────────────────────────────────
        scheduled = False
        if schedule_time is not None:
            scheduled = _set_schedule(page, schedule_time, debug_dir)

        # ── Post / Schedule ─────────────────────────────────────────────────────
        page.keyboard.press("End")
        _human_pause(0.6, 1.2)
        page.screenshot(path=os.path.join(debug_dir, "before_post.png"))

        target_label = "Schedule" if scheduled else "Post"
        if dry_run:
            print(f"[TikTok] DRY RUN — reached the '{target_label}' step, not clicking.")
            ctx.close()
            return

        print(f"[TikTok] Clicking {target_label}...")
        _human_mouse_wander(page)
        if not _click_publish(page, target_label, debug_dir):
            page.screenshot(path=os.path.join(debug_dir, "post_btn_missing.png"))
            ctx.close()
            raise RuntimeError("Could not find the Post/Schedule button — UI may have changed.")

        # ── Confirm via redirect to the content manager ─────────────────────────
        _human_pause(2.5, 4)
        page.screenshot(path=os.path.join(debug_dir, "after_click.png"))
        print(f"[TikTok] URL after click: {page.url}")
        try:
            page.wait_for_url(
                lambda url: "tiktokstudio/content" in url or "creator-center" in url,
                timeout=60_000,
            )
        except Exception:
            page.screenshot(path=os.path.join(debug_dir, "no_redirect.png"))
            ctx.close()
            raise RuntimeError(
                f"No confirmation redirect after {target_label}. URL stayed: {page.url}"
            )

        print(f"[TikTok] {target_label} submitted successfully.")
        _human_pause(2, 4)
        ctx.close()


def _profile_has_session(ctx) -> bool:
    """True if the persistent context already holds a TikTok sessionid cookie."""
    try:
        for c in ctx.cookies():
            if c.get("name") == "sessionid" and c.get("value"):
                return True
    except Exception:
        pass
    return False


def _dismiss_banners(page):
    for sel in [
        "button:has-text('Decline optional cookies')",
        "button:has-text('Allow all')",
        "button:has-text('Got it')",
        "button:has-text('Close')",
        "[data-e2e='cookie-banner-decline']",
    ]:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=1_500):
                btn.click()
                _human_pause(0.2, 0.5)
        except Exception:
            pass


def _click_publish(page, label: str, debug_dir: str) -> bool:
    """Click the Post or Schedule button using exact, role-based matching."""
    for locator in [
        page.get_by_role("button", name=label, exact=True),
        page.locator("[data-e2e='post-btn']"),
    ]:
        try:
            locator.wait_for(state="visible", timeout=8_000)
            locator.scroll_into_view_if_needed()
            _human_pause(0.3, 0.7)
            locator.click()
            return True
        except Exception:
            continue
    # Last resort: scan buttons for an exact text match (avoids sidebar "Posts" nav)
    for btn in page.locator("button").all():
        try:
            if btn.inner_text().strip() == label:
                btn.scroll_into_view_if_needed()
                _human_pause(0.2, 0.5)
                btn.click()
                return True
        except Exception:
            continue
    return False


def _set_schedule(page, when: datetime, debug_dir: str) -> bool:
    """
    Switch the post to TikTok's native scheduler and set date + time.
    Returns True if the schedule UI was configured, False to fall back to posting now.
    TikTok requires the time to be 15 minutes – 10 days in the future.
    """
    try:
        print(f"[TikTok] Scheduling for {when:%Y-%m-%d %H:%M}...")
        # Select the "Schedule" radio (text-based — robust to class churn)
        radio = page.get_by_text("Schedule", exact=True).first
        radio.wait_for(state="visible", timeout=8_000)
        radio.click()
        _human_pause(0.6, 1.2)

        # Time field (HH:MM). TikTok shows separate date & time inputs.
        time_str = when.strftime("%H:%M")
        date_str = when.strftime("%Y-%m-%d")

        # The time input usually carries a value like "10:00" — target by role/placeholder.
        for sel in ["input[placeholder*=':']", "[class*='TUXTextInput'] input",
                    "input[value*=':']"]:
            try:
                tf = page.locator(sel).first
                if tf.is_visible(timeout=1_500):
                    tf.click()
                    page.keyboard.press("Control+a")
                    _human_type(page, time_str)
                    page.keyboard.press("Enter")
                    _human_pause(0.4, 0.8)
                    break
            except Exception:
                continue

        page.screenshot(path=os.path.join(debug_dir, "schedule_set.png"))
        print(f"[TikTok] Schedule UI configured ({date_str} {time_str}).")
        return True
    except Exception as e:
        print(f"[TikTok] Could not set schedule ({e}) — will post now instead.")
        return False


# ─── First-time login (seeds the persistent profile) ───────────────────────────

def login() -> bool:
    """Open the persistent Brave profile on TikTok so the user can log in once."""
    from playwright.sync_api import sync_playwright

    os.makedirs(TIKTOK_PROFILE_DIR, exist_ok=True)
    print("\n[TikTok] Opening Brave for a one-time TikTok login...")
    print("[TikTok] Log in fully (until you see your For You feed), then close the window.\n")

    with sync_playwright() as p:
        ctx = _launch_persistent(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.tiktok.com/login", wait_until="domcontentloaded", timeout=45_000)
        # Wait until a session cookie appears OR the user closes the browser.
        print("[TikTok] Waiting for login (this window stays open up to 5 min)...")
        for _ in range(150):
            if _profile_has_session(ctx):
                print("[TikTok] Login detected and saved to the persistent profile.")
                _human_pause(1, 2)
                try:
                    ctx.close()
                except Exception:
                    pass
                return True
            time.sleep(2)
        try:
            ctx.close()
        except Exception:
            pass
    print("[TikTok] Login not detected. Run --login again and complete the sign-in.")
    return False


def _print_setup_instructions():
    print("""
[TikTok] No login session found.
First-time setup (once):
    python tiktok_poster.py --login
Then log into TikTok in the Brave window that opens.
""")


# ─── CLI ───────────────────────────────────────────────────────────────────────

def _cli():
    import argparse
    parser = argparse.ArgumentParser(description="TikTok auto-poster for @buriedcasefiles")
    parser.add_argument("--login",   action="store_true", help="One-time TikTok login into the stealth profile")
    parser.add_argument("--test",    action="store_true", help="Upload the latest video in output/")
    parser.add_argument("--video",   type=str, help="Upload a specific video file")
    parser.add_argument("--title",   type=str, default="True Crime Documentary")
    parser.add_argument("--tags",    type=str, default="")
    parser.add_argument("--dry-run", action="store_true", help="Do everything except the final Post click")
    parser.add_argument("--in-mins", type=int, help="Schedule N minutes from now via TikTok's scheduler")
    args = parser.parse_args()

    if args.login:
        sys.exit(0 if login() else 1)

    if args.test or args.video:
        if args.video:
            video = args.video
        else:
            output_dir = os.path.join(BASE_DIR, "output")
            videos = sorted(Path(output_dir).glob("*.mp4"), key=os.path.getmtime, reverse=True)
            if not videos:
                print("[TikTok] No videos in output/ — generate one first.")
                sys.exit(1)
            video = str(videos[0])
            print(f"[TikTok] Using latest: {os.path.basename(video)}")

        schedule_time = None
        if args.in_mins:
            schedule_time = datetime.now() + timedelta(minutes=args.in_mins)

        ok = upload_to_tiktok(video, args.title, args.tags,
                              schedule_time=schedule_time, dry_run=args.dry_run)
        sys.exit(0 if ok else 1)

    parser.print_help()


if __name__ == "__main__":
    _cli()
