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

def run_once() -> str:
    """Research, generate, and compose one true crime documentary video."""

    # Ensure FFmpeg is on PATH
    ffmpeg_fallback = r"C:\ffmpeg\ffmpeg-8.1.1-essentials_build\bin"
    if not shutil.which("ffmpeg") and os.path.exists(os.path.join(ffmpeg_fallback, "ffmpeg.exe")):
        os.environ["PATH"] = ffmpeg_fallback + os.pathsep + os.environ.get("PATH", "")

    from config import (
        AUDIO_DIR, NUM_SCENE_IMAGES, SCENE_IMAGES_DIR,
        TTS_DOCUMENTARY_VOICE, TTS_DOCUMENTARY_SPEED,
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
    story = generate_true_crime_story(max_attempts=3)
    print(f"  Case:     {story['case_name']}")
    print(f"  Title:    {story['title']}")
    print(f"  Accuracy: {story['accuracy_score']}/10 | Interest: {story['interest_score']}/10")

    # ── 2. Voice ──────────────────────────────────────────────────────────────
    print("\n--- STEP 2: Generating Voice ---")
    tts = generate_tts(
        story["script"],
        filename_prefix=prefix,
        voice=TTS_DOCUMENTARY_VOICE,
        speed=TTS_DOCUMENTARY_SPEED,
    )
    print(f"  Duration: {tts['duration']:.1f}s")
    if tts["duration"] < 60:
        print(f"  WARNING: {tts['duration']:.1f}s — TikTok Creator Program needs 60s+")

    # ── 3. Captions ───────────────────────────────────────────────────────────
    print("\n--- STEP 3: Building Captions ---")
    captions = get_captions(tts, AUDIO_DIR, prefix=prefix)
    print(f"  {len(captions)} caption segments")

    # ── 4. Scene images ───────────────────────────────────────────────────────
    print("\n--- STEP 4: Fetching Scene Images ---")
    image_dir = os.path.join(SCENE_IMAGES_DIR, prefix)
    image_paths, word_segments = generate_story_images(
        story_title=story["title"],
        story_text=story["script"],
        output_dir=image_dir,
        num_images=NUM_SCENE_IMAGES,
    )
    print(f"  {len(image_paths)} images ready")

    # ── 5. Compose ────────────────────────────────────────────────────────────
    print("\n--- STEP 5: Composing Video ---")
    video_path = compose_documentary(
        image_paths=image_paths,
        audio_path=tts["audio_path"],
        caption_segments=captions,
        title=story["title"],
        audio_duration=tts["duration"],
        word_segments=word_segments,
    )

    # ── 6. Log ────────────────────────────────────────────────────────────────
    _log({
        "timestamp":      timestamp,
        "case":           story["case_name"],
        "title":          story["title"],
        "duration_s":     round(tts["duration"], 1),
        "accuracy":       story["accuracy_score"],
        "interest":       story["interest_score"],
        "video":          video_path,
        "status":         "done",
    })

    print("\n" + "=" * 60)
    print(f"  DONE: {os.path.basename(video_path)}")
    print(f"  Path: {video_path}")
    print("=" * 60 + "\n")

    # ── 7. Upload to TikTok ───────────────────────────────────────────────────
    print("\n--- STEP 7: Uploading to TikTok ---")
    try:
        from tiktok_poster import upload_to_tiktok
        upload_to_tiktok(
            video_path=video_path,
            title=story["title"],
            story_hashtags=story.get("hashtags", ""),
        )
    except Exception as e:
        print(f"[TikTok] Upload step error: {e}")
        # Notify via Telegram so you know when to refresh cookies
        try:
            from notify import send_alert
            send_alert(
                "⚠️ <b>BuriedCasefiles — TikTok upload failed</b>\n\n"
                f"<b>Video:</b> {story['title']}\n"
                f"<b>Error:</b> {str(e)[:300]}\n\n"
                "Your TikTok cookies may have expired.\n"
                "Fix: open your PC and run  <code>py refresh_cookies.py</code>"
            )
        except Exception:
            pass

    # ── 8. Auto-preview ───────────────────────────────────────────────────────
    try:
        os.startfile(video_path)
        print("[Preview] Opening video in default media player...")
    except Exception as e:
        print(f"[Preview] Could not auto-open video: {e}")
        print(f"[Preview] Open manually: {video_path}")

    return video_path


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

def register_scheduler(every_hours: int = 24):
    """
    Register two Windows Task Scheduler tasks for optimal posting times:
      BuriedCasefilesAM — 07:30 daily  (morning commute + overnight US traffic)
      BuriedCasefilesPM — 20:00 daily  (UK prime-time true crime window)

    If every_hours < 24, falls back to a single hourly task instead.
    """
    script = os.path.abspath(__file__)
    python = sys.executable
    tr_cmd = f'"{python}" "{script}"'

    if every_hours < 24:
        # Manual interval mode — single HOURLY task
        task = "BuriedCasefilesGenerator"
        cmd  = ["schtasks", "/create", "/f",
                "/tn", task, "/tr", tr_cmd,
                "/sc", "HOURLY", "/mo", str(every_hours), "/st", "09:00"]
        print(f"[Scheduler] Registering '{task}' (every {every_hours}h)...")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"[Scheduler] Success.  Remove: schtasks /delete /tn {task} /f")
        else:
            print(f"[Scheduler] Failed: {result.stderr.strip()}")
            print("[Scheduler] Tip: run as Administrator for Task Scheduler access.")
        return

    # Default: two daily tasks at optimal UK posting times
    slots = [("BuriedCasefilesAM", "07:30"), ("BuriedCasefilesPM", "20:00")]
    ok = 0
    for task, start_time in slots:
        cmd = ["schtasks", "/create", "/f",
               "/tn", task, "/tr", tr_cmd,
               "/sc", "DAILY", "/st", start_time]
        print(f"[Scheduler] Registering '{task}' daily at {start_time} UK time...")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"[Scheduler]   OK '{task}' scheduled.")
            ok += 1
        else:
            print(f"[Scheduler]   FAIL: {result.stderr.strip()}")

    if ok == 2:
        print("\n[Scheduler] Both tasks active — 2 videos/day at 07:30 and 20:00.")
        print("[Scheduler] To remove:")
        print("  schtasks /delete /tn BuriedCasefilesAM /f")
        print("  schtasks /delete /tn BuriedCasefilesPM /f")
    elif ok == 0:
        print("\n[Scheduler] Failed — run this script as Administrator.")


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="@buriedcasefiles — automated true crime documentary generator"
    )
    parser.add_argument("--loop",     action="store_true",
                        help="Run continuously, generating one video per interval")
    parser.add_argument("--every",    type=int, default=24,
                        help="Hours between videos in loop mode (default: 24)")
    parser.add_argument("--schedule", action="store_true",
                        help="Register with Windows Task Scheduler and exit")
    parser.add_argument("--log",      action="store_true",
                        help="Show recent generation history and exit")
    args = parser.parse_args()

    if args.log:
        show_log()
        return

    if args.schedule:
        register_scheduler(args.every)
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
