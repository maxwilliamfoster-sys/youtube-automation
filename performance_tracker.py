"""
Performance Tracker — the "eyes" of the adaptive growth engine.

Responsibilities:
  1. Record what each posted video WAS (length, horror sub-theme, opening hook) at
     post time, keyed by its YouTube video id  ->  performance_history.json
  2. Periodically fetch how each video DID (views, likes, age-normalised views/day,
     and retention % when the Analytics API is enabled).
  3. Keep a rolling words-per-second calibration so target-length estimates stay
     accurate for the current voice.

The history file is committed back to the repo by the GitHub Action so learning
persists across runs.  Everything here fails soft: if the network or an API is
unavailable the pipeline still posts, it just doesn't learn that cycle.
"""

import os
import re
import json
from datetime import datetime, timezone

from config import PERFORMANCE_HISTORY_FILE, TOKEN_FILE, WORDS_PER_SECOND


# ─── History file I/O ─────────────────────────────────────────────────────────

def _empty_history() -> dict:
    return {
        "posts":    {},   # video_id -> {posted_at, title, theme, hook, target_seconds, actual_seconds, word_count, stats}
        "observed": {},   # video_id -> {duration, views, likes, views_per_day, age_days, fetched_at}  (ALL videos)
        "calibration": {
            "words_per_second": WORDS_PER_SECOND,  # actual spoken words per second
            "word_overshoot":   1.0,               # actual story words / words we requested
            "samples":          0,
        },
    }


def load_history() -> dict:
    if os.path.exists(PERFORMANCE_HISTORY_FILE):
        try:
            with open(PERFORMANCE_HISTORY_FILE, encoding="utf-8") as f:
                h = json.load(f)
            for k, v in _empty_history().items():
                h.setdefault(k, v)
            return h
        except Exception as e:
            print(f"[Perf] Could not read history ({e}) — starting fresh.")
    return _empty_history()


def save_history(history: dict) -> None:
    try:
        with open(PERFORMANCE_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        print(f"[Perf] Could not save history — {e}")


# ─── Recording a post ─────────────────────────────────────────────────────────

def record_post(video_id: str, meta: dict) -> None:
    """Store the generation parameters of a freshly uploaded video."""
    if not video_id:
        return
    h = load_history()
    h["posts"][video_id] = {
        "posted_at":       datetime.now(timezone.utc).isoformat(),
        "title":           meta.get("title", ""),
        "theme":           meta.get("theme"),
        "hook":            meta.get("hook"),
        "target_seconds":  meta.get("target_seconds"),
        "actual_seconds":  meta.get("actual_seconds"),
        "word_count":      meta.get("word_count"),
        "stats":           {},
    }
    # Refine the calibration from this real sample.
    wc = meta.get("word_count") or 0          # actual spoken words
    secs = meta.get("actual_seconds") or 0    # actual audio seconds
    req = meta.get("target_words") or 0       # words we asked the model for
    if wc >= 30 and secs >= 5:
        cal = h["calibration"]
        n = cal.get("samples", 0)
        # words-per-second (rolling mean)
        wps = wc / secs
        cal["words_per_second"] = round((cal["words_per_second"] * n + wps) / (n + 1), 4)
        # word-overshoot ratio (rolling mean), clamped to a sane range
        if req >= 20:
            ratio = max(0.7, min(2.0, wc / req))
            cal["word_overshoot"] = round((cal.get("word_overshoot", 1.0) * n + ratio) / (n + 1), 4)
        cal["samples"] = n + 1
        print(f"[Perf] Calibration: {wps:.2f} w/s, overshoot x{cal['word_overshoot']:.2f} "
              f"-> rolling {cal['words_per_second']:.2f} w/s (n={cal['samples']})")
    save_history(h)
    print(f"[Perf] Recorded post {video_id}: theme={meta.get('theme')} hook={meta.get('hook')} "
          f"target={meta.get('target_seconds')}s actual={meta.get('actual_seconds')}s")


# ─── Fetching performance ─────────────────────────────────────────────────────

def _parse_duration(iso: str) -> int:
    m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', iso or "")
    if not m:
        return 0
    return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)


def _services():
    """Build (youtube, analytics_or_None) from the saved token. Returns (None, None) on failure."""
    try:
        from googleapiclient.discovery import build
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from youtube_scopes import SCOPES

        if not os.path.exists(TOKEN_FILE):
            return None, None
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        yt = build("youtube", "v3", credentials=creds)
        try:
            ya = build("youtubeAnalytics", "v2", credentials=creds)
        except Exception:
            ya = None
        return yt, ya
    except Exception as e:
        print(f"[Perf] Could not build YouTube service ({e}) — skipping stats refresh.")
        return None, None


def _fetch_retention(ya, start="2020-01-01") -> dict:
    """video_id -> averageViewPercentage. Empty dict if Analytics API is off/unavailable."""
    if ya is None:
        return {}
    try:
        end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rep = ya.reports().query(
            ids="channel==MINE", startDate=start, endDate=end,
            metrics="averageViewPercentage,averageViewDuration",
            dimensions="video", maxResults=200,
        ).execute()
        out = {}
        for row in rep.get("rows", []):
            out[row[0]] = {"avg_view_pct": row[1], "avg_view_seconds": row[2]}
        return out
    except Exception as e:
        # 403 accessNotConfigured = Analytics API not enabled yet. Fail soft.
        msg = str(e)
        if "accessNotConfigured" in msg or "has not been used" in msg:
            print("[Perf] Analytics API not enabled — using view data only (retention will auto-activate once enabled).")
        else:
            print(f"[Perf] Retention fetch skipped — {e}")
        return {}


def refresh_stats() -> dict:
    """
    Pull current stats for every video on the channel, age-normalise, and merge into
    the history file. Returns the updated history. Safe to call every run.
    """
    h = load_history()
    yt, ya = _services()
    if yt is None:
        return h

    try:
        ch = yt.channels().list(part="contentDetails", mine=True).execute()["items"][0]
        uploads = ch["contentDetails"]["relatedPlaylists"]["uploads"]
        vid_ids = []
        req = yt.playlistItems().list(part="contentDetails", playlistId=uploads, maxResults=50)
        while req:
            r = req.execute()
            vid_ids += [i["contentDetails"]["videoId"] for i in r["items"]]
            req = yt.playlistItems().list_next(req, r)

        retention = _fetch_retention(ya)
        now = datetime.now(timezone.utc)
        fetched_at = now.isoformat()
        updated = 0

        for chunk in [vid_ids[i:i + 50] for i in range(0, len(vid_ids), 50)]:
            vids = yt.videos().list(part="contentDetails,snippet,statistics", id=",".join(chunk)).execute()
            for v in vids["items"]:
                vid = v["id"]
                dur = _parse_duration(v["contentDetails"]["duration"])
                pub = datetime.fromisoformat(v["snippet"]["publishedAt"].replace("Z", "+00:00"))
                age_days = max(0.5, (now - pub).total_seconds() / 86400)
                views = int(v["statistics"].get("viewCount", 0))
                likes = int(v["statistics"].get("likeCount", 0))
                ret = retention.get(vid, {})
                rec = {
                    "duration":      dur,
                    "views":         views,
                    "likes":         likes,
                    "views_per_day": round(views / age_days, 4),
                    "age_days":      round(age_days, 2),
                    "avg_view_pct":  ret.get("avg_view_pct"),
                    "fetched_at":    fetched_at,
                }
                h["observed"][vid] = rec
                # Mirror fresh stats into the labelled post entry too.
                if vid in h["posts"]:
                    h["posts"][vid]["stats"] = rec
                updated += 1

        print(f"[Perf] Refreshed stats for {updated} videos"
              + (f" (retention for {len(retention)})" if retention else " (view data only)"))
        save_history(h)
    except Exception as e:
        print(f"[Perf] Stats refresh failed ({e}) — continuing without it.")
    return h


def get_words_per_second() -> float:
    return load_history().get("calibration", {}).get("words_per_second", WORDS_PER_SECOND)


def get_word_overshoot() -> float:
    """How many words the model actually returns per word we request (>=1 means it overshoots)."""
    return load_history().get("calibration", {}).get("word_overshoot", 1.0)


if __name__ == "__main__":
    hist = refresh_stats()
    obs = hist["observed"]
    print(f"\nTracked videos: {len(obs)} | Labelled posts: {len(hist['posts'])}")
    print(f"Calibration: {hist['calibration']['words_per_second']} w/s (n={hist['calibration']['samples']})")
