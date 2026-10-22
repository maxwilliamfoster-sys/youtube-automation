"""
@buriedcasefiles — Automated True Crime Documentary Generator
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Usage:
    py main_documentary.py               # Generate one video now
    py main_documentary.py --loop        # Infinite loop (one video every 24h)
    py main_documentary.py --every 6     # Every 6 hours
    py main_documentary.py --schedule    # Add to Windows Task Scheduler (run once daily at 9am)

Each run:
  1. Researches a real true crime case via Groq AI
  2. Writes a documentary script (190-220 words)
  3. Fact-checks: accuracy, interest, coherence — retries if below threshold
  4. Generates voice with Kokoro (natural, non-robotic)
  5. Fetches 5 real Pexels stock photos matched to each scene
  6. Composes video with smooth Ken Burns motion + crossfades
  7. Burns in TikTok-safe captions
  8. Logs everything to documentary_log.json
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, "documentary_log.json")


# ─── Smart posting scheduler ──────────────────────────────────────────────────

def _next_posting_slot(posting_times=None):
    """
    Return (seconds_to_wait, human_readable_time) until the next optimal
    posting slot in UK time.

    Posting times default to config.POSTING_TIMES ["07:30", "20:00"].
    Uses zoneinfo (Python 3.9+) → pytz fallback → UTC fallback.
    """
    if posting_times is None:
        try:
            from config import POSTING_TIMES
            posting_times = POSTING_TIMES
        except Exception:
            posting_times = ["07:30", "20:00"]

    # Get current UK time
    now_uk = None
    for _tz_loader in (
        lambda: __import__("zoneinfo").ZoneInfo("Europe/London"),
        lambda: __import__("pytz").timezone("Europe/London"),
    ):
        try:
            tz = _tz_loader()
            now_uk = datetime.now(tz)
            break
        except Exception:
            pass
    if now_uk is None:
        now_uk = datetime.now(timezone.utc)  # graceful fallback

    # Find the nearest upcoming slot
    candidates = []
    for slot_str in posting_times:
        h, m = map(int, slot_str.split(":"))
        candidate = now_uk.replace(hour=h, minute=m, second=0, microsecond=0)
        if candidate <= now_uk:
            candidate += timedelta(days=1)
        candidates.append(candidate)

    next_slot = min(candidates)
    wait_s = max(60, (next_slot - now_uk).total_seconds())
    return wait_s, next_slot.strftime("%Y-%m-%d %H:%M %Z")


# ─── Single video generation ──────────────────────────────────────────────────

def _ensure_ffmpeg_on_path():
    ffmpeg_fallback = r"C:\ffmpeg\ffmpeg-8.1.1-essentials_build\bin"
    if not shutil.which("ffmpeg") and os.path.exists(os.path.join(ffmpeg_fallback, "ffmpeg.exe")):
        os.environ["PATH"] = ffmpeg_fallback + os.pathsep + os.environ.get("PATH", "")


def _loop_values():
    """Lever values for this post from the nightly experiment loop, and its arms.

    Allowed levers: levers.json (hook_card, cta_card, music_volume). Missing or
    broken loop files give ({}, {}) and the video renders exactly as before.
    LOOP_FORCE_VALUES (JSON) previews a queued value; LOOP_FORCE_ARM pins the arm.
    """
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, os.path.join(here, "analytics"))   # growth_stats lives there
        import experiment_loop
        led, _, cfg = experiment_loop.load_repo(here)
        vals, arms = experiment_loop.values_for_post(
            led, cfg, force_arm=os.environ.get("LOOP_FORCE_ARM") or None)
        if os.environ.get("LOOP_FORCE_VALUES"):
            vals.update(json.loads(os.environ["LOOP_FORCE_VALUES"]))
        if vals or arms:
            print(f"[LOOP] arms {arms} values {vals}")
        return vals, arms
    except Exception as e:
        print(f"[LOOP] off ({e})")
        return {}, {}


def _generate_one() -> tuple:
    """
    Research → script → voice → captions → images → compose ONE video.
    Returns (story_dict, video_path). Does NOT upload.
    """
    _ensure_ffmpeg_on_path()

    from config import (
        AUDIO_DIR, NUM_SCENE_IMAGES, SCENE_IMAGES_DIR,
        TTS_DOCUMENTARY_VOICE, TTS_DOCUMENTARY_SPEED, UK_LOCATION_IMAGES,
    )
    from story_generator import generate_true_crime_story
    from tts_generator import generate_tts
    from caption_generator import get_captions
    from image_generator import generate_story_images
    from documentary_composer import compose_documentary

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix    = f"doc_{timestamp}"

    print("\n" + "=" * 60)
    print(f"  @buriedcasefiles — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # ── 1. Research & write story ─────────────────────────────────────────────
    print("\n--- STEP 1: Researching True Crime Story ---")
    # The guidelines gate legitimately rejects a lot: the Wikipedia pool is heavy with
    # graphic murders that land AGE_RESTRICTED or worse. A run that drew several dark
    # cases in a row could exhaust 5 attempts and produce nothing (as one 10am slot
    # did). 8 gives comfortable headroom; each rejected attempt is cheap.
    # A queued part of a multi-part series always goes out before any new case.
    # It was fully written and passed every gate when Part 1 was made.
    import series_queue
    story = series_queue.next_part()
    if story:
        print(f"[Series] Posting queued part {story.get('series_part')}/"
              f"{story.get('series_total')} of {story.get('case_name')}")
    else:
        story = generate_true_crime_story(max_attempts=8)
    print(f"  Case:     {story['case_name']}")
    print(f"  Title:    {story['title']}")
    print(f"  Hook:     {story.get('hook','')}")
    print(f"  Accuracy: {story['accuracy_score']}/10 | Interest: {story['interest_score']}/10")

    # ── 2. Voice ──────────────────────────────────────────────────────────────
    print("\n--- STEP 2: Generating Voice ---")
    # Chatterbox is the expressive British narrator; Kokoro/edge-tts is the fallback.
    # Chatterbox is a generative model, so a take can occasionally come out malformed
    # and it is far slower. In an unattended pipeline a bad voice must degrade to the
    # old engine rather than lose the whole video.
    tts = None
    try:
        import chatterbox_narrator
        if chatterbox_narrator.is_available():
            tts = chatterbox_narrator.generate(story["script"], filename_prefix=prefix)
    except Exception as e:
        print(f"[Voice] Chatterbox failed ({str(e)[:160]}) — falling back to Kokoro.")

    story["voice_engine"] = "chatterbox" if tts is not None else "kokoro"
    if tts is None:
        tts = generate_tts(
            story["script"],
            filename_prefix=prefix,
            voice=TTS_DOCUMENTARY_VOICE,
            speed=TTS_DOCUMENTARY_SPEED,
        )
    print(f"  Duration: {tts['duration']:.1f}s")

    # ── 3. Captions ───────────────────────────────────────────────────────────
    print("\n--- STEP 3: Building Captions ---")
    captions = get_captions(tts, AUDIO_DIR, prefix=prefix)
    print(f"  {len(captions)} caption segments")

    # ── 4. Scene images ───────────────────────────────────────────────────────
    print("\n--- STEP 4: Fetching Scene Images ---")
    image_dir = os.path.join(SCENE_IMAGES_DIR, prefix)
    # The real place carries the visuals. story['location'] comes from the Wikipedia
    # article, so it is the actual town/borough the case happened in.
    uk_place = (story.get("location") or "").strip()
    image_paths, word_segments, used_images = generate_story_images(
        story_title=story["title"],
        story_text=story["script"],
        output_dir=image_dir,
        num_images=NUM_SCENE_IMAGES,
        uk_place=uk_place,
        uk=story.get("uk", True),
    )
    # Licences travel with the story so the copyright gate can check them before send.
    story["images"] = used_images
    uk_count = len(used_images)
    print(f"  {len(image_paths)} images ready ({uk_count} real UK locations)")
    # A silent fall-through to stock is how a whole run shipped with zero UK imagery
    # while reporting success. If the feature is on and produced nothing, say so
    # loudly — it means the case was not UK, or the place name did not resolve.
    if UK_LOCATION_IMAGES and uk_count == 0:
        print(f"  [WARN] UK imagery is enabled but produced NOTHING for "
              f"place={uk_place!r} — every scene fell back to stock footage.")

    # ── 5. Compose ────────────────────────────────────────────────────────────
    print("\n--- STEP 5: Composing Video ---")
    loop_vals, story["loop_arms"] = _loop_values()
    if story.get("series_part") and "cta_card" in loop_vals:
        # A series part's closing card is "Part N next" - never drop it, and keep
        # these posts out of the cta test so they can't confound it.
        loop_vals.pop("cta_card")
        story["loop_arms"] = {k: v for k, v in story["loop_arms"].items() if "cta" not in k}
    import documentary_composer
    if "music_volume" in loop_vals:
        documentary_composer.DOC_MUSIC_VOLUME = float(loop_vals["music_volume"])
    hook_text = story.get("hook", "") if loop_vals.get("hook_card", True) else ""
    # Never wired up before, so every video fell back to the composer's default
    # "Follow for more unsolved cases" — the same generic ask on every post.
    cta_text = (story.get("cta") or None) if loop_vals.get("cta_card", True) else ""
    video_path = compose_documentary(
        image_paths=image_paths,
        audio_path=tts["audio_path"],
        caption_segments=captions,
        title=story["title"],
        audio_duration=tts["duration"],
        word_segments=word_segments,
        hook_text=hook_text,
        cta_text=cta_text,
    )

    # ── 6. Log ────────────────────────────────────────────────────────────────
    _log({
        "timestamp":  timestamp,
        "case":       story["case_name"],
        "title":      story["title"],
        "duration_s": round(tts["duration"], 1),
        "accuracy":   story["accuracy_score"],
        "interest":   story["interest_score"],
        "video":      video_path,
        "status":     "done",
    })
    print(f"\n[Done] {os.path.basename(video_path)}")
    return story, video_path


def _post(story: dict, video_path: str, schedule_time=None) -> bool:
    """Upload one video (now, or scheduled). Alerts via Telegram on failure."""
    label = "Scheduling" if schedule_time else "Uploading"
    print(f"\n--- {label} to TikTok ---")
    try:
        # Preferred: official Content Posting API (no browser, cloud-friendly) when
        # explicitly enabled and authorised. Falls back to the stealth browser poster.
        try:
            from tiktok_api_poster import api_available, post_video
            if api_available():
                print("[TikTok] Using official Content Posting API.")
                from config import TIKTOK_HASHTAGS, TIKTOK_CAPTION_TEMPLATE
                caption = TIKTOK_CAPTION_TEMPLATE.format(
                    title=story["title"], hashtags=TIKTOK_HASHTAGS,
                    story_hashtags=story.get("hashtags", ""),
                )
                return post_video(video_path, caption)
        except Exception as api_e:
            print(f"[TikTok] API path unavailable ({api_e}) — using browser poster.")

        from tiktok_poster import upload_to_tiktok
        return upload_to_tiktok(
            video_path=video_path,
            title=story["title"],
            story_hashtags=story.get("hashtags", ""),
            schedule_time=schedule_time,
        )
    except Exception as e:
        print(f"[TikTok] Upload step error: {e}")
        try:
            from notify import send_alert
            send_alert(
                "⚠️ <b>BuriedCasefiles — TikTok upload failed</b>\n\n"
                f"<b>Video:</b> {story['title']}\n"
                f"<b>Error:</b> {str(e)[:300]}\n\n"
                "Your TikTok session may have expired.\n"
                "Fix: open your PC and run  <code>py tiktok_poster.py --login</code>"
            )
        except Exception:
            pass
        return False


def run_once() -> str:
    """Generate one video and post it immediately."""
    story, video_path = _generate_one()
    _post(story, video_path)
    try:
        os.startfile(video_path)
        print("[Preview] Opening video in default media player...")
    except Exception:
        print(f"[Preview] Open manually: {video_path}")
    return video_path


def _today_slots(jitter_minutes: int = 7):
    """
    Build today's posting datetimes from config.POSTING_TIMES, each nudged by a
    small random jitter so posts never land on a robotic exact-minute pattern.
    Slots already past are rolled to tomorrow.
    """
    import random
    try:
        from config import POSTING_TIMES
    except Exception:
        POSTING_TIMES = ["07:30", "20:00"]

    now = datetime.now()
    slots = []
    for s in POSTING_TIMES:
        h, m = map(int, s.split(":"))
        dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        dt += timedelta(minutes=random.randint(-jitter_minutes, jitter_minutes))
        if dt <= now + timedelta(minutes=16):   # TikTok needs ≥15 min lead time
            dt += timedelta(days=1)
        slots.append(dt)
    return slots


def run_batch() -> None:
    """
    Generate ALL of today's videos in ONE session and hand each to TikTok's native
    scheduler. The PC only needs to wake once (morning) — TikTok's servers publish
    each video at its slot, so the machine can sleep the rest of the day.
    """
    slots = _today_slots()
    print(f"[Batch] Generating {len(slots)} videos, scheduling for: "
          + ", ".join(s.strftime('%H:%M') for s in slots))

    for i, slot in enumerate(slots, 1):
        print(f"\n[Batch] ===== Video {i}/{len(slots)} → {slot:%Y-%m-%d %H:%M} =====")
        try:
            story, video_path = _generate_one()
            ok = _post(story, video_path, schedule_time=slot)
            print(f"[Batch] Video {i}: {'scheduled' if ok else 'FAILED'}")
        except Exception as exc:
            print(f"[Batch] Video {i} errored: {exc}")
            _log({"timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
                  "status": "error", "error": str(exc)})

    print("\n[Batch] Done — PC can sleep; TikTok will publish at the scheduled times.")


def _fit_telegram_limit(video_path: str, limit_mb: int = 48) -> str:
    """
    Telegram's bot API rejects uploads over 50 MB. Re-encode to fit and return the new
    path; return the original if it already fits or if the re-encode fails.

    Previously an oversized video just told the user to fetch it from the GitHub Actions
    artifacts — which silently became a dead end once the artifact storage quota filled.
    """
    size_mb = os.path.getsize(video_path) / (1024 * 1024)
    if size_mb <= limit_mb:
        return video_path

    print(f"[Cloud] Video is {size_mb:.0f} MB — over Telegram's limit. Re-encoding...")
    try:
        from video_composer import FFMPEG, get_video_duration
        duration = get_video_duration(video_path)
        audio_kbps = 96
        # 5% headroom for container overhead so we land safely under the limit.
        total_kbps = (limit_mb * 8 * 1024 * 0.95) / duration
        video_kbps = max(300, int(total_kbps - audio_kbps))

        out = video_path.replace(".mp4", "_tg.mp4")
        subprocess.run(
            [FFMPEG, "-y", "-i", video_path,
             "-c:v", "libx264", "-preset", "veryfast",
             "-b:v", f"{video_kbps}k", "-maxrate", f"{int(video_kbps * 1.2)}k",
             "-bufsize", f"{video_kbps * 2}k",
             "-c:a", "aac", "-b:a", f"{audio_kbps}k",
             "-movflags", "+faststart", out],
            check=True, capture_output=True,
        )
        new_mb = os.path.getsize(out) / (1024 * 1024)
        if new_mb <= 50:
            print(f"[Cloud] Re-encoded to {new_mb:.0f} MB.")
            return out
        print(f"[Cloud] Re-encode still {new_mb:.0f} MB — sending original.")
    except Exception as e:
        print(f"[Cloud] Re-encode failed ({e}) — sending original.")
    return video_path


def _log_post(detail: str, story: dict, video_path: str) -> None:
    """
    One row per posted video in analytics/posts_log.csv: which format version and
    narrator settings made it. The scheduled reviews group videos into test cohorts
    from this file, so a format change can be judged on the videos it actually made.
    """
    import csv, re as _re
    import config as C
    try:
        from video_composer import get_video_duration
        dur = round(get_video_duration(video_path), 1)
    except Exception:
        dur = ""
    m = _re.search(r"/video/(\d+)", detail or "")
    row = {
        "posted_utc":   datetime.utcnow().strftime("%Y-%m-%dT%H:%M"),
        "video_id":     m.group(1) if m else "",
        "format":       C.FORMAT_VERSION,
        "narrator_ref": os.path.basename(C.CHATTERBOX_REFERENCE),
        "exaggeration": C.CHATTERBOX_EXAGGERATION,
        "cfg_weight":   C.CHATTERBOX_CFG_WEIGHT,
        "grade":        os.getenv("VISUAL_GRADE", "overcast"),
        "duration_s":   dur,
        "case":         story.get("case_name", ""),
        "series_part":  f"{story['series_part']}/{story['series_total']}" if story.get("series_part") else "",
        "uk":           story.get("uk", ""),
        "softened":     bool(story.get("softened")),
        "hook":         (story.get("hook") or "")[:120],
        "loop":         json.dumps(story.get("loop_arms") or {}),
        # the engine that ACTUALLY spoke (narrator_ref etc. are only Chatterbox settings)
        "voice":        story.get("voice_engine", ""),
    }
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analytics", "posts_log.csv")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        old = []
        if os.path.exists(path):
            with open(path, newline="", encoding="utf-8") as fh:
                old = list(csv.DictReader(fh))
        # Rewrite in full: a column added later (e.g. series_part) must not shift
        # the values of the rows already logged.
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(row))
            w.writeheader()
            for r in old:
                w.writerow({k: r.get(k, "") for k in row})
            w.writerow(row)
    except Exception as e:
        print(f"[TikTok] could not log the post ({e})")


# _autopost_tiktok result when Buffer accepted the post but it was still sending at the
# timeout. Truthy: it counts as delivered, because making another video could put two
# up in one slot.
POST_PENDING = "pending"


def _app(kind, severity, title, body="", **data):
    """Also tell Bot HQ (the phone app). Telegram alerts are unchanged."""
    try:
        import app_events
        short = " ".join(str(title).split())
        app_events.emit("bcf", kind, severity, short if len(short) <= 80 else short[:79] + "…",
                        body, data=data or None)
    except Exception as e:
        print(f"[BotHQ] emit failed: {e}")


def _autopost_tiktok(video_path: str, caption: str, story: dict, send_alert, esc):
    """
    Post the finished video to TikTok, if auto-posting is switched on: Buffer when
    BUFFER_API_KEY is set (free, official API), else Upload-Post (dormant, paid).

    Safety: this is only ever called after the guidelines gate returned OK, so nothing
    below the OK bar can reach TikTok. Re-checks the verdict once more as a backstop —
    the account's safety must not depend on the call site alone. Never raises.
    """
    try:
        import tiktok_buffer as tb
        if tb.is_configured():
            tp, via = tb, "Buffer"
        else:
            import tiktok_uploadpost as tp
            via = "Upload-Post"
    except Exception as e:
        print(f"[TikTok] poster unavailable ({e}) — skipping.")
        return False

    if not tp.is_configured():
        return False  # owner hasn't opted in; Telegram-only, exactly as before

    # Backstop guardrail: only videos that passed BOTH the guidelines and the legal
    # review may be posted, no exceptions (the upload gate is enforced inside post_video).
    if (story.get("compliance") or {}).get("verdict") != "OK":
        print("[TikTok] Refusing to post — compliance verdict is not OK.")
        return False
    if (story.get("legal") or {}).get("ok") is not True:
        print("[TikTok] Refusing to post — no passing legal review on record.")
        return False

    print(f"[TikTok] Auto-posting to TikTok via {via}...")
    ok, detail = tp.post_video(video_path, caption)
    if ok:
        print(f"[TikTok] Posted: {detail}")
        _log_post(detail, story, video_path)
        _app("posted", "info", f"BuriedCasefiles posted: {story.get('title', '?')}", str(detail),
             buffer_id=str(detail))
        try:
            send_alert(f"🚀 <b>Posted to TikTok</b>\n\n{esc(story.get('title','?'))}\n{esc(detail)}")
        except Exception:
            pass
    else:
        print(f"[TikTok] Post failed: {detail}")
        if str(detail).startswith("BLOCKED by upload gate"):
            advice = "It was NOT posted. Don't post it by hand either."
        elif "timed out" in str(detail):
            advice = ("Buffer may still publish it - check TikTok before posting anything "
                      "by hand, or it goes up twice.")
        else:
            advice = ("A catch-up run will make and post a replacement, so don't post this "
                      "one by hand (the slot would get two).")
        _app("post_failed", "warn", "BuriedCasefiles: TikTok post failed",
             f"{story.get('title', '?')}\nReason: {str(detail)[:300]}\n\n{advice}")
        try:
            send_alert(
                f"⚠️ <b>TikTok auto-post failed</b>\n\n"
                f"{esc(story.get('title','?'))}\n<b>Reason:</b> {esc(detail[:300])}\n\n{advice}"
            )
        except Exception:
            pass
        if "timed out" in str(detail):
            return POST_PENDING      # Buffer has it and may still publish: not a failure
    return ok


def _credit_line(images, limit=400) -> str:
    """CC BY / BY-SA photos must credit their author; TikTok has no credits screen, so
    the caption carries it (publish_checks used to compute this and drop it)."""
    seen, parts = set(), []
    for img in images or []:
        lic, who = (img.get("licence") or "").strip(), (img.get("credit") or "").strip()
        # Authors often sign as "Name (www.site.net)": a link in a TikTok caption is
        # an off-platform diversion and the upload gate blocks the whole video for it.
        who = re.sub(r"\s*[(\[]?\s*(?:https?://|www\.)\S+?\s*[)\]]?(?=\s|$)", "", who)
        who = re.sub(r"\s*[(\[]?\b[\w-]+\.(?:com|net|org|co\.uk|uk|de|fr|info)\b\S*[)\]]?", "", who).strip(" ,;-") or (
            "a Commons contributor" if (img.get("credit") or "").strip() else "")
        if not who or who.lower() == "public domain" or not re.search(r"cc[ -]?by", lic, re.I):
            continue
        key = (who, lic)
        if key not in seen:
            seen.add(key)
            parts.append(f"{who} ({lic})")
    if not parts:
        return ""
    line = "📷 Photos: " + "; ".join(parts) + " via Wikimedia Commons"
    return line if len(line) <= limit else line[:limit - 1] + "…"


def _gate_profile(sample=False):
    """Upload-gate settings for the documentaries (see upload_gate.DEFAULT_PROFILE)."""
    return {
        "width": 1080, "height": 1920, "fps": 30, "seconds": (15, 180),
        "min_video_kbps": 1000,
        "max_silence": 3.0,
        "speech_min": 0.7,               # Whisper tiny on clean narration measures ~0.9+
        "whisper_model": "tiny",
        "ai_review": "required",         # no coherence review = no post
        "ai_daily_cap": AI_DAILY_CAP,
        "ai_realistic": True,            # AI narrator over real places -> TikTok AI label
        "ai_context": ("BuriedCasefiles: calm, factual true-crime documentaries about real cases. "
                       "An AI narrator reads the script over sombre mood footage (fog, rain, empty "
                       "streets, police tape), photos of the real place, and archive images; a hook "
                       "card opens the video and a closing card asks viewers what they think."),
        "ledger": None if sample else os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                   "upload_gate_log.json"),
    }


# Time budget, from the start of run_cloud_deliver. A new render starts only if it can
# finish AND still leave POST_RESERVE_S to post (hosting waits up to 600s, Buffer up
# to 900s). Worst case: ~4 min setup + 105 min rendering + 25 min posting = 134 min,
# inside documentary.yml's timeout-minutes: 150.
CLOUD_BUDGET_S = 130 * 60
POST_RESERVE_S = 25 * 60
CLOUD_FIRST_RENDER_S = 18 * 60 # assumed render time until one has been measured
CLOUD_MAX_ATTEMPTS = 10        # safety cap on renders per run (+count)
# An AI-review outage blocks every new video too, so the SAME video is re-checked
# (every 5 min, up to 1h) instead of spending a new case and writer tokens on it.
AI_OUTAGE_WAIT_S = 300
AI_OUTAGE_MAX_RECHECKS = 12
# Real AI reviews per UTC day for this bot. The free Groq quota is shared by 3 bots
# (chess 10, Escape 8, BCF 20 = ~165k of 200k tokens; growth-lab PLAN_2026-10-19).
# Reaching it behaves like Groq's own "quota used up" answer: no post, stop.
AI_DAILY_CAP = 20
SYSTEMIC_STOP = "same failure 3 times in a row - stopping (not a content problem; check the log)"


def _only_ai_unavailable(gate_v) -> bool:
    """The gate's only objections are that the AI review couldn't run."""
    return bool(gate_v["failures"]) and all(f.startswith("AI review unavailable")
                                            for f in gate_v["failures"])


def _ai_outage_retryable(gate_v) -> bool:
    """Worth waiting and asking the gate again: the only objection is an AI-review outage
    the reviewer itself marked retryable (a 429 without "per day", a 5xx, a timeout).
    A 413 (request too large) or a spent daily quota never is - waiting can't fix it."""
    return (not gate_v.get("ok") and _only_ai_unavailable(gate_v)
            and bool((gate_v.get("ai") or {}).get("retryable")))


def _github_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{name}={value}\n")


def run_cloud_deliver(count: int = 2, sample: bool = False) -> None:
    """
    Cloud mode (GitHub Actions): make `count` videos, run every gate, send each to
    Telegram, then auto-post it to TikTok via Buffer when configured.
    sample=True: same video and gates, Telegram only - never posts.
    """
    from notify import send_alert, send_video, esc
    from config import TIKTOK_HASHTAGS, TIKTOK_CAPTION_TEMPLATE, VIDEO_WIDTH, VIDEO_HEIGHT
    from video_composer import get_video_duration

    if os.environ.get("GITHUB_EVENT_NAME") == "schedule" and not sample:
        # Runs queue behind each other, so the slot job's check can be stale by now.
        import slot_logic
        if slot_logic.day_is_full():
            print(f"[Cloud] Today already has {slot_logic.DAILY_TARGET} videos - nothing to make.")
            _github_output("catchup", "false")
            return

    print(f"[Cloud] Generating {count} video(s) for manual posting...")
    delivered = 0
    # A video blocked by a gate never ends the run: new videos are made until `count`
    # pass every gate, while the time budget lasts (one render ~12-16 min on CI; the
    # job's timeout is 150 min). Only a spent daily AI-review quota stops early, since
    # every new video would hit the same wall. If the budget runs out, the workflow
    # starts a catch-up run (see documentary.yml).
    started, slowest, i, quota_out = time.monotonic(), CLOUD_FIRST_RENDER_S, 0, False
    import upload_gate
    blocked_sent = False
    keys, broken, ai_down = [], False, False  # infrastructure-failure keys (upload_gate.failure_key)
    while delivered < count:
        i += 1
        if (i > CLOUD_MAX_ATTEMPTS + count
                or time.monotonic() - started + slowest + POST_RESERVE_S > CLOUD_BUDGET_S):
            print(f"[Cloud] Out of time/attempts after {i - 1} video(s).")
            break
        print(f"\n[Cloud] ===== Video {i} (need {count - delivered} more) =====")
        t0 = time.monotonic()
        try:
            story, video_path = _generate_one()

            # Last line of defence. The generator already refuses to return anything that
            # failed the guidelines gate, so this should never fire — but it is the final
            # point before a video reaches the user's phone, and a silent regression
            # upstream must not be able to put a strike on the account.
            # Copyright, defamation and TikTok guidelines — all three before anything
            # is delivered. Each fails differently, so each is checked separately:
            # a non-free image, an unproven accusation about a named person, and
            # glorifying an offender are three distinct ways to get the account or the
            # owner in trouble, and none is caught reliably by the others.
            verdict = (story.get("compliance") or {}).get("verdict")
            try:
                import publish_checks
                gate = publish_checks.run_all(
                    story.get("script", ""),
                    story.get("caption", ""),
                    story.get("images", []),
                    verdict or "unreviewed",
                )
                if not gate["ok"]:
                    reasons = chr(10).join(f"• {b}" for b in gate["blocking"][:5])
                    _app("gate_blocked", "warn", "BuriedCasefiles: publish checks held a video back",
                         f"{story.get('title', '?')}\n\n{reasons}\n\nNot sent.")
                    send_alert(
                        "🛑 <b>Video blocked before sending</b>" + chr(10)*2
                        + esc(story.get("title", "?")) + chr(10)*2
                        + esc(reasons) + chr(10)*2 + "Not sent."
                    )
                    print("[Cloud] BLOCKED by publish checks:")
                    for b in gate["blocking"]:
                        print(f"    ! {b}")
                    import series_queue
                    series_queue.mark_blocked(story, permanent=True)   # regex checks repeat
                    continue
                story["attribution"] = gate["attribution"]
            except ImportError as e:
                # Fail closed: the copyright/defamation checks are not optional.
                raise RuntimeError(f"publish_checks could not be imported ({e}) - not delivering")

            if verdict != "OK":
                reasons = "; ".join((story.get("compliance") or {}).get("reasons", []))
                _app("gate_blocked", "warn", "BuriedCasefiles: TikTok guidelines review held a video back",
                     f"{story.get('title', '?')}\nVerdict: {verdict or 'unreviewed'}\nWhy: "
                     f"{reasons[:300] or 'no compliance review attached'}\n\nNot sent.")
                send_alert(
                    f"🛑 <b>Video blocked — TikTok guidelines</b>\n\n"
                    f"{esc(story.get('title', '?'))}\n"
                    f"<b>Verdict:</b> {esc(verdict or 'unreviewed')}\n"
                    f"<b>Why:</b> {esc(reasons[:300] or 'no compliance review attached')}\n\n"
                    f"Not sent. Nothing to post."
                )
                print(f"[Cloud] BLOCKED by guidelines gate ({verdict}) — not delivering.")
                continue

            # Lead the caption with the case-name title the working UK crime accounts
            # use, then the hook line, then tags.
            tt = story.get("tiktok_title")
            if tt and story.get("caption"):
                story["caption"] = tt + chr(10) + story["caption"]
            caption = story.get("caption") or TIKTOK_CAPTION_TEMPLATE.format(
                title=story["title"],
                hashtags=TIKTOK_HASHTAGS,
                story_hashtags=story.get("hashtags", ""),
            )
            credits = _credit_line(story.get("images", []))
            if credits:
                caption = caption + "\n" + credits
            size_mb = os.path.getsize(video_path) / (1024 * 1024)
            try:
                dur = int(round(get_video_duration(video_path)))
            except Exception:
                dur = None

            # ── Upload gate (growth-lab upload_gate.py) ────────────────────────
            # TikTok's limits and guidelines on the caption, technical quality,
            # narration vs script, and an AI review of the frames for coherence
            # (required: no review = no post). tiktok_buffer.post_video refuses any
            # file this did not approve.

            def _gate():
                return upload_gate.check(
                    video_path, "tiktok", profile=_gate_profile(sample), caption=caption,
                    script=story.get("script", ""), ai_label=True)
            gate_v = _gate()
            rechecks = 0
            while (_ai_outage_retryable(gate_v)
                   and rechecks < AI_OUTAGE_MAX_RECHECKS
                   and time.monotonic() - started + AI_OUTAGE_WAIT_S + POST_RESERVE_S <= CLOUD_BUDGET_S):
                # Nothing wrong with the video; the review couldn't run (outage). Ask
                # again about the SAME video rather than throwing it away.
                rechecks += 1
                print(f"[Cloud] AI review unavailable - re-checking in {AI_OUTAGE_WAIT_S}s "
                      f"({rechecks}/{AI_OUTAGE_MAX_RECHECKS}).")
                time.sleep(AI_OUTAGE_WAIT_S)
                gate_v = _gate()
            if _ai_outage_retryable(gate_v):
                _app("ai_down", "warn", "BuriedCasefiles: AI reviewer down, video not posted",
                     f"{story.get('title', '?')} was not posted after {rechecks} re-checks. A catch-up "
                     "run tries again later - nothing to do unless this repeats.")
                send_alert(f"⚠️ <b>BuriedCasefiles: AI reviewer still down after {rechecks} "
                           f"re-checks</b> (up to 1h)\n\n{esc(story.get('title', '?'))} was not posted. "
                           "A catch-up run tries again later — nothing to do unless this repeats.")
                print("[Cloud] AI review still unavailable - stopping (catch-up run retries).")
                ai_down = True
                break
            if not gate_v["ok"]:
                quota_out = _only_ai_unavailable(gate_v) and bool((gate_v.get("ai") or {}).get("quota"))
                too_large = _only_ai_unavailable(gate_v) and bool((gate_v.get("ai") or {}).get("too_large"))
                next_step = ("The daily AI-review quota is used up, so no new video can pass today."
                             if quota_out else
                             "AI review request too large (HTTP 413) - not an outage, so no re-check. "
                             "Making another video now." if too_large else "Making another video now.")
                v_ok = None
                if not blocked_sent:
                    # One blocked video per run is enough to see what the gate saw; a
                    # long retry run used to flood Telegram with ~50 MB videos.
                    send_path = _fit_telegram_limit(video_path)
                    v_ok = send_video(send_path, f"🛑 <b>NOT posted</b> — {esc(story.get('title', '?'))}\n"
                                                 f"(blocked by the upload gate, reasons below). {next_step}",
                                      width=VIDEO_WIDTH, height=VIDEO_HEIGHT, duration=dur)
                    blocked_sent = True
                _app("gate_blocked", "warn", "BuriedCasefiles: upload gate held a video back",
                     f"{story.get('title', '?')}\n" + "\n".join(f"- {f}" for f in gate_v.get("failures", [])[:6])
                     + f"\n\n{next_step}")
                a_ok = send_alert(upload_gate.block_message(gate_v, story.get("title", "?")))
                print(f"[Cloud] BLOCKED by the upload gate (Telegram: video "
                      f"{'not sent (one per run)' if v_ok is None else 'sent' if v_ok else 'FAILED'}, "
                      f"reasons {'sent' if a_ok else 'FAILED'}).")
                slowest = max(slowest, time.monotonic() - t0)
                if quota_out:
                    # Every new video would hit the same wall.
                    print("[Cloud] AI review quota used up - stopping.")
                    break
                keys.append(upload_gate.failure_key(gate_v["failures"]))
                if upload_gate.systemic(keys):
                    # A broken tool or secret fails every video the same way.
                    print(f"[Cloud] {SYSTEMIC_STOP}")
                    broken = True
                    break
                if _only_ai_unavailable(gate_v):
                    continue                     # the video was fine: don't mark the case blocked
                if not sample:
                    import series_queue
                    series_queue.mark_blocked(story)
                continue

            # Everything in ONE message: the video and its ready-to-paste caption.
            # Telegram renders <pre> as a code block with a copy button, so posting is
            # save video -> tap caption -> paste in TikTok. Caption cap is 1024 chars;
            # ours runs ~200, and send_video truncates as a backstop.
            try:
                import tiktok_buffer
                auto = tiktok_buffer.is_configured() and not sample
            except Exception:
                auto = False
            video_caption = (
                (f"🧪 <b>SAMPLE — not posted</b>\n" if sample else "")
                + f"🎬 <b>{esc(story['title'])}</b>  •  {dur}s  •  9:16\n"
                f"✅ TikTok guidelines: passed\n{esc(upload_gate.summary(gate_v))}\n\n"
                + ("🤖 Posting to TikTok automatically — nothing to do.\n"
                   if auto else ("" if sample else "Tap the caption to copy it 👇\n"))
                + f"<pre>{esc(caption)}</pre>"
            )
            send_path = _fit_telegram_limit(video_path)
            sent = send_video(send_path, video_caption, width=VIDEO_WIDTH, height=VIDEO_HEIGHT, duration=dur)
            if sent:
                print(f"[Cloud] Delivered to Telegram: {story['title']}")
            else:
                _app("delivery_failed", "warn", "BuriedCasefiles: video made but Telegram didn't get it",
                     f"{story['title']} ({size_mb:.0f} MB). "
                     + ("It is still being posted to TikTok automatically." if auto else
                        "Grab it from the GitHub Actions run page > Artifacts."))
                send_alert(
                    f"⚠️ <b>Video made but Telegram delivery failed</b>\n\n"
                    f"{esc(story['title'])} ({size_mb:.0f} MB).\n"
                    + ("It is still being posted to TikTok automatically." if auto else
                       "Grab it from the GitHub Actions run page → Artifacts, if that run "
                       "still has one.")
                )
                print(f"[Cloud] Telegram delivery failed for {story['title']}")
            if sample:
                delivered += bool(sent)
                continue
            # Auto-post to TikTok — only reachable AFTER the guidelines, legal and
            # upload gates passed. Independent of Telegram: a Telegram outage used to
            # mean a fully checked video was never posted. Inert unless opted in.
            posted = _autopost_tiktok(video_path, caption, story, send_alert, esc)
            # With auto-posting on, "delivered" means TikTok has it: a video that only
            # reached Telegram used to count, so a Buffer failure ended the run green
            # with no catch-up and the slot stayed empty.
            if posted or (sent and not auto):
                delivered += 1
                try:
                    import series_queue
                    series_queue.mark_delivered(story)
                except Exception as e:
                    print(f"[Series] queue update failed: {e}")
            elif auto:
                # A Buffer failure is not about this video: re-rendering here only
                # re-sent videos to Telegram and spent the shared AI quota while Buffer
                # was down. Stop; the catch-up run (catchup=true) tries again.
                print("[Cloud] Buffer did not take the video - not counted as delivered; "
                      "leaving it to the catch-up run.")
                break
        except Exception as exc:
            print(f"[Cloud] Video {i} errored: {exc}")
            _log({"timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
                  "status": "error", "error": str(exc)})
            keys.append(upload_gate.failure_key([f"pipeline step could not run: {exc}"]))
            if upload_gate.systemic(keys):
                print(f"[Cloud] {SYSTEMIC_STOP}")
                broken = True
                break
            time.sleep(60)                       # LLM/API outages: don't hammer them
        slowest = max(slowest, time.monotonic() - t0)

    # Tells documentary.yml whether a catch-up run is worth starting.
    _github_output("catchup", "true" if (delivered < count and not quota_out and not broken
                                         and not sample) else "false")

    if delivered and count > 1:
        # Only worth a summary for a batch. For the usual single-video run it would
        # just be a third message restating what is already on screen.
        send_alert(
            f"✅ <b>BuriedCasefiles — {delivered}/{count} video(s) ready</b>\n\n"
            f"They're above this message."
        )
    elif not delivered:
        _app("no_videos", "action", "BuriedCasefiles: no videos made this run",
             f"All {i} attempt(s) failed. "
             + ("The daily AI-review quota is used up; nothing can pass until it refills."
                if quota_out else "Check the run log (Bot HQ attaches it to the run alert)."))
        send_alert(
            f"❌ <b>BuriedCasefiles — no videos made</b>\n\n"
            f"All {i} attempt(s) failed. "
            + ("The daily AI-review quota is used up; nothing can pass until it refills."
               if quota_out else
               "The same technical failure hit 3 videos in a row, so the run stopped - "
               "something is broken (no catch-up run)." if broken else
               "A catch-up run starts automatically (up to 2 in a row) — nothing to do.")
            + " Details in the GitHub Actions log."
        )
    print(f"\n[Cloud] Done — {delivered}/{count} delivered to Telegram.")

    # Fail the CI run when nothing shipped. Delivering zero videos previously still
    # reported success, so a wholly broken run looked identical to a healthy one.
    if delivered == 0:
        raise SystemExit(f"[Cloud] FAILED — 0/{count} videos delivered.")


# ─── Logging ──────────────────────────────────────────────────────────────────

def _log(entry: dict):
    logs = []
    if os.path.exists(LOG_PATH):
        try:
            with open(LOG_PATH, encoding="utf-8") as f:
                logs = json.load(f)
        except Exception:
            logs = []
    logs.append(entry)
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=2)


def show_log(last_n: int = 10):
    """Print recent generation history."""
    if not os.path.exists(LOG_PATH):
        print("No log file yet.")
        return
    with open(LOG_PATH, encoding="utf-8") as f:
        logs = json.load(f)
    print(f"\nLast {min(last_n, len(logs))} videos:")
    for entry in logs[-last_n:]:
        status = entry.get("status", "?")
        print(f"  {entry['timestamp']}  [{status}]  {entry.get('title','?')}  "
              f"({entry.get('duration_s','?')}s)  "
              f"acc={entry.get('accuracy','?')}  int={entry.get('interest','?')}")


# ─── Windows Task Scheduler ───────────────────────────────────────────────────

def register_scheduler(wake_time: str = "06:30"):
    """
    Register ONE daily Windows task that:
      • wakes the PC from sleep (WakeToRun) at `wake_time`,
      • runs `--batch` to generate the day's videos and hand them to TikTok's
        native scheduler for publishing at the configured POSTING_TIMES.

    Because TikTok publishes from its own servers, the PC only needs to be awake
    for this one morning window — it can sleep the rest of the day.

    Replaces the old twice-daily BuriedCasefilesAM / BuriedCasefilesPM tasks.
    """
    script = os.path.abspath(__file__)
    python = sys.executable
    task   = "BuriedCasefilesDaily"

    # Remove the legacy two-task setup if present (ignore errors).
    for old in ("BuriedCasefilesAM", "BuriedCasefilesPM"):
        subprocess.run(["schtasks", "/delete", "/tn", old, "/f"],
                       capture_output=True, text=True)

    # Build the task with PowerShell so we can set WakeToRun + run-when-on-battery.
    ps = f"""
$action  = New-ScheduledTaskAction -Execute '{python}' -Argument '"{script}" --batch'
$trigger = New-ScheduledTaskTrigger -Daily -At {wake_time}
$settings = New-ScheduledTaskSettingsSet -WakeToRun -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName '{task}' -Action $action -Trigger $trigger `
            -Settings $settings -Force | Out-Null
Write-Host 'OK'
"""
    print(f"[Scheduler] Registering single daily wake task '{task}' at {wake_time}...")
    result = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                            capture_output=True, text=True)
    if result.returncode == 0 and "OK" in result.stdout:
        print(f"[Scheduler] OK — '{task}' will wake the PC daily at {wake_time} and batch-post.")
        print("[Scheduler] Legacy AM/PM tasks removed.")
        print(f"[Scheduler] To remove: schtasks /delete /tn {task} /f")
    else:
        print(f"[Scheduler] FAILED: {(result.stderr or result.stdout).strip()}")
        print("[Scheduler] Tip: run this terminal as Administrator and retry.")


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="@buriedcasefiles — automated true crime documentary generator"
    )
    parser.add_argument("--cloud",    action="store_true",
                        help="Generate-only: make videos and deliver to Telegram for manual posting (GitHub Actions)")
    parser.add_argument("--count",    type=int, default=2,
                        help="How many videos to generate in --cloud mode (default: 2)")
    parser.add_argument("--sample",   action="store_true",
                        help="With --cloud: make + gate + Telegram only, never post")
    parser.add_argument("--batch",    action="store_true",
                        help="Generate all of today's videos and schedule them via TikTok (one wake)")
    parser.add_argument("--loop",     action="store_true",
                        help="Run continuously, generating one video per interval")
    parser.add_argument("--every",    type=int, default=24,
                        help="Hours between videos in loop mode (default: 24)")
    parser.add_argument("--schedule", action="store_true",
                        help="Register the single daily wake task in Windows Task Scheduler and exit")
    parser.add_argument("--wake-time", type=str, default="06:30",
                        help="Morning wake time for the daily batch task (default 06:30)")
    parser.add_argument("--log",      action="store_true",
                        help="Show recent generation history and exit")
    args = parser.parse_args()

    if args.log:
        show_log()
        return

    if args.schedule:
        register_scheduler(args.wake_time)
        return

    if args.cloud:
        run_cloud_deliver(count=args.count, sample=args.sample)
        return

    if args.batch:
        run_batch()
        return

    if args.loop:
        use_schedule = (args.every == 24)   # True when user hasn't overridden --every
        if use_schedule:
            print("[Loop] Starting — posting at optimal UK times: 07:30 and 20:00.")
        else:
            print(f"[Loop] Starting — generating one video every {args.every}h (manual interval).")
        print("[Loop] Press Ctrl+C to stop.\n")

        run_number = 0
        while True:
            run_number += 1
            print(f"[Loop] Run #{run_number}")
            try:
                run_once()
            except KeyboardInterrupt:
                print("\n[Loop] Stopped by user.")
                break
            except Exception as exc:
                print(f"[Loop] Error on run #{run_number}: {exc}")
                _log({"timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
                      "status": "error", "error": str(exc)})
                print("[Loop] Waiting 1 hour before retry...")
                try:
                    time.sleep(3600)
                except KeyboardInterrupt:
                    print("\n[Loop] Stopped by user.")
                    break
                continue

            if use_schedule:
                wait_s, slot_str = _next_posting_slot()
                print(f"[Loop] Next video: {slot_str}  ({wait_s / 3600:.1f}h from now)")
            else:
                wait_s = args.every * 3600
                next_dt = datetime.fromtimestamp(time.time() + wait_s)
                slot_str = next_dt.strftime("%Y-%m-%d %H:%M")
                print(f"[Loop] Next video: {slot_str}")

            try:
                time.sleep(wait_s)
            except KeyboardInterrupt:
                print("\n[Loop] Stopped by user.")
                break
    else:
        run_once()


if __name__ == "__main__":
    main()
