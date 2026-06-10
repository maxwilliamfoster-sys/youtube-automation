"""
TikTok Content Posting API — official, server-to-server video publishing.

This is the only path that needs NO browser and NO PC uptime: TikTok pulls/receives
the file and publishes it from their own infrastructure, so it works from the cloud
(GitHub Actions) and carries zero browser-automation fingerprint.

⚠️  Until your developer app is audited, posts are restricted to SELF_ONLY (private).
    See TIKTOK_API_SETUP.md. After audit approval, set privacy to PUBLIC_TO_EVERYONE.

Used automatically by main_documentary only when TIKTOK_API_ENABLED=1 and tokens exist;
otherwise the stealth browser poster (tiktok_poster.py) is used.
"""

import os
import time

import requests

from tiktok_oauth import get_valid_access_token

INIT_URL   = "https://open.tiktokapis.com/v2/post/publish/video/init/"
STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

# Until the app is audited this MUST be SELF_ONLY. After approval use PUBLIC_TO_EVERYONE.
DEFAULT_PRIVACY = os.getenv("TIKTOK_API_PRIVACY", "SELF_ONLY")

_CHUNK = 10_000_000  # 10 MB chunks (TikTok requires 5-64 MB except the final chunk)


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8"}


def post_video(video_path: str, caption: str, privacy: str = None) -> bool:
    """
    Publish a local video via the Content Posting API (Direct Post).
    Returns True on success. Non-fatal: returns False with a printed reason on failure.
    """
    token = get_valid_access_token()
    if not token:
        print("[TikTokAPI] No valid token — run: python tiktok_oauth.py")
        return False

    privacy = privacy or DEFAULT_PRIVACY
    size = os.path.getsize(video_path)
    total_chunks = max(1, (size + _CHUNK - 1) // _CHUNK)

    # ── 1. Initialise the upload ───────────────────────────────────────────────
    init_body = {
        "post_info": {
            "title": caption[:2200],
            "privacy_level": privacy,
            "disable_comment": False,
            "disable_duet": False,
            "disable_stitch": False,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": size,
            "chunk_size": min(_CHUNK, size),
            "total_chunk_count": total_chunks,
        },
    }
    r = requests.post(INIT_URL, headers=_headers(token), json=init_body, timeout=30)
    if r.status_code != 200:
        print(f"[TikTokAPI] init failed ({r.status_code}): {r.text[:300]}")
        return False
    data = r.json().get("data", {})
    publish_id = data.get("publish_id")
    upload_url = data.get("upload_url")
    if not upload_url or not publish_id:
        print(f"[TikTokAPI] init missing upload_url/publish_id: {r.text[:300]}")
        return False

    # ── 2. Upload the file in chunks ───────────────────────────────────────────
    print(f"[TikTokAPI] Uploading {size/1_000_000:.1f} MB in {total_chunks} chunk(s)...")
    with open(video_path, "rb") as f:
        for idx in range(total_chunks):
            start = idx * _CHUNK
            chunk = f.read(_CHUNK)
            end = start + len(chunk) - 1
            put = requests.put(
                upload_url,
                headers={
                    "Content-Range": f"bytes {start}-{end}/{size}",
                    "Content-Type": "video/mp4",
                },
                data=chunk,
                timeout=120,
            )
            if put.status_code not in (200, 201, 206):
                print(f"[TikTokAPI] chunk {idx+1}/{total_chunks} failed "
                      f"({put.status_code}): {put.text[:200]}")
                return False

    # ── 3. Poll until TikTok finishes processing ───────────────────────────────
    print("[TikTokAPI] Uploaded — waiting for TikTok to process...")
    for _ in range(40):                       # ~2 min max
        s = requests.post(STATUS_URL, headers=_headers(token),
                          json={"publish_id": publish_id}, timeout=30)
        status = s.json().get("data", {}).get("status", "")
        if status in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
            print(f"[TikTokAPI] Success — status: {status} (privacy: {privacy})")
            return True
        if status == "FAILED":
            print(f"[TikTokAPI] Processing failed: {s.text[:300]}")
            return False
        time.sleep(3)

    print("[TikTokAPI] Timed out waiting for processing (it may still complete).")
    return False


def api_available() -> bool:
    """True if API posting is switched on and authorised."""
    if os.getenv("TIKTOK_API_ENABLED", "") not in ("1", "true", "True"):
        return False
    return get_valid_access_token() is not None


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python tiktok_api_poster.py <video.mp4> [caption]")
        sys.exit(1)
    cap = sys.argv[2] if len(sys.argv) > 2 else "True crime documentary #truecrime"
    ok = post_video(sys.argv[1], cap)
    sys.exit(0 if ok else 1)
