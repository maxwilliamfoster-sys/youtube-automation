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

def _ensure_ffmpeg_on_path():
    ffmpeg_fallback = r"C:\ffmpeg\ffmpeg-8.1.1-essentials_build\bin"
    if not shutil.which("ffmpeg") and os.path.exists(os.path.join(ffmpeg_fallback, "ffmpeg.exe")):
        os.environ["PATH"] = ffmpeg_fallback + os.pathsep + os.environ.get("PATH", "")


def _generate_one() -> tuple:
    """
    Research → script → voice → captions → images → compose ONE video.
    Returns (story_dict, video_path). Does NOT upload.
    """
    _ensure_ffmpeg_on_path()

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
    print(f"  Hook:     {story.get('hook','')}")
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
        hook_text=story.get("hook", ""),
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
