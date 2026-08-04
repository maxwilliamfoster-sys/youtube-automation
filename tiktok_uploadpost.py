"""
Fully-automated TikTok posting via Upload-Post (upload-post.com).

Why this and not the official API directly
------------------------------------------
TikTok's own Content Posting API can only post PUBLICLY once the calling app passes
an audit, and that audit *requires* a per-post human-consent UI (a preview, a privacy
selector with no default, unchecked interaction toggles). A GitHub Actions cron has
none of that, so an unattended app of our own cannot legitimately post public video —
faking the audit would risk the account, which is the opposite of the goal.

Upload-Post already holds audited Content Posting API access and posts through the
official API, so driving it from cron is compliant: it is the official API, not
browser automation, and carries no bot fingerprint (the thing that shadowbanned this
account before). The trade-off is trust — a third party holds a posting token for the
account. That token is revocable at any time from TikTok's own connected-apps settings.

Contract verified against https://docs.upload-post.com/openapi.json:
  POST https://api.upload-post.com/api/upload   (multipart/form-data)
  Header: Authorization: Apikey <key>
  Required: user, platform[]=tiktok, video (binary)
  TikTok:  title, privacy_level, disable_comment/duet/stitch
  Status:  GET https://api.upload-post.com/api/uploadposts/status?request_id=...

Inert unless UPLOADPOST_API_KEY, UPLOADPOST_USER and TIKTOK_AUTOPOST are all set, so
adding this cannot change a working manual/Telegram setup until the owner opts in.
"""

import os
import time

import requests

from config import (
    UPLOADPOST_API_KEY, UPLOADPOST_USER, TIKTOK_AUTOPOST,
    TIKTOK_PRIVACY_LEVEL, TIKTOK_LABEL_AI,
)

UPLOAD_URL = "https://api.upload-post.com/api/upload"
STATUS_URL = "https://api.upload-post.com/api/uploadposts/status"


def is_configured() -> bool:
    """True only when the owner has opted in AND supplied credentials."""
    return bool(TIKTOK_AUTOPOST and UPLOADPOST_API_KEY and UPLOADPOST_USER)


def _headers() -> dict:
    return {"Authorization": f"Apikey {UPLOADPOST_API_KEY}"}


def post_video(video_path: str, caption: str) -> tuple[bool, str]:
    """
    Publish one video to TikTok through Upload-Post. Returns (ok, detail).

    Never raises — a posting failure must not crash the run, because the video is
    also delivered to Telegram as a fallback. The caller decides what to tell the user.
    """
    if not is_configured():
        return False, "Upload-Post not configured (need UPLOADPOST_API_KEY, UPLOADPOST_USER, TIKTOK_AUTOPOST=1)"

    if not os.path.exists(video_path):
        return False, f"video not found: {video_path}"

    data = {
        "user":          UPLOADPOST_USER,
        "platform[]":    "tiktok",
        "title":         caption[:2200],
        "tiktok_title":  caption[:2200],
        # PUBLIC_TO_EVERYONE is what makes the post reach anyone. Passed straight
        # through to TikTok's API; the other privacy levels exist if ever needed.
        "privacy_level": TIKTOK_PRIVACY_LEVEL,
        # Comments MUST stay on — they are the channel's strongest reach signal, and
        # the whole engagement strategy (the "X or Y?" card) depends on them.
        "disable_comment": "false",
        "disable_duet":    "false",
        "disable_stitch":  "false",
        # Declare AI involvement per TikTok's synthetic-media policy when enabled.
        # Default off: the visuals are real stock footage, only the voiceover is
        # synthetic. See config for the reasoning and how to flip it on.
        "is_aigc":        "true" if TIKTOK_LABEL_AI else "false",
        # Return immediately with a request_id; we poll for the real outcome.
        "async_upload":   "true",
    }

    try:
        with open(video_path, "rb") as f:
            resp = requests.post(
                UPLOAD_URL, headers=_headers(),
                data=data, files={"video": f}, timeout=180,
            )
    except Exception as e:
        return False, f"request failed: {e}"

    if resp.status_code not in (200, 201, 202):
        return False, f"HTTP {resp.status_code}: {resp.text[:300]}"

    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    request_id = body.get("request_id") or body.get("job_id")
    if not request_id:
        # Some plans upload synchronously and report success inline.
        if body.get("success"):
            return True, "posted (synchronous)"
        return False, f"no request_id in response: {str(body)[:300]}"

    return _poll_status(request_id)


def _poll_status(request_id: str, tries: int = 30, interval: float = 6.0) -> tuple[bool, str]:
    """Poll until Upload-Post reports the TikTok post finished (or ~3 min pass)."""
    for _ in range(tries):
        try:
            r = requests.get(
                STATUS_URL, headers=_headers(),
                params={"request_id": request_id}, timeout=30,
            )
            if r.status_code == 200:
                d = r.json()
                # Status shape varies; look for a terminal state anywhere sensible.
                blob = str(d).lower()
                if any(k in blob for k in ("completed", "published", "success", "posted")):
                    if "fail" not in blob and "error" not in blob:
                        return True, "published to TikTok"
                if any(k in blob for k in ("failed", "error", "rejected")):
                    return False, f"Upload-Post reported failure: {str(d)[:300]}"
        except Exception:
            pass
        time.sleep(interval)
    # Timed out waiting — likely still processing. Report unknown, not success, so the
    # caller can fall back to the Telegram copy rather than assume it went live.
    return False, f"timed out waiting on Upload-Post (request_id {request_id}); it may still complete"


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python tiktok_uploadpost.py <video.mp4> [caption]")
        sys.exit(1)
    ok, detail = post_video(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "test #truecrime")
    print(("OK: " if ok else "FAIL: ") + detail)
    sys.exit(0 if ok else 1)
