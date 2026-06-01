"""
YouTube Shorts Automation - Main Orchestrator
==========================================
Runs the full pipeline:
  1. Generate a horror story (Groq/Llama - FREE)
  2. Convert to speech (edge-tts - FREE)
  3. Build word-timed captions
  4. Download/pick gameplay footage
  5. Compose the Short video (FFmpeg)
  6. Upload to YouTube

Usage:
  python main.py                    # Generate and upload one video
  python main.py --count 3          # Generate and upload 3 videos
  python main.py --no-upload        # Generate video but don't upload
"""

import argparse
import os
import sys
import traceback
from datetime import datetime

# Make console output UTF-8 safe everywhere (Windows defaults to cp1252, which
# raises UnicodeEncodeError on characters like ≈ or emoji and would crash a run).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from config import AUDIO_DIR, OUTPUT_DIR, MAX_VIDEO_SECONDS, MIN_VIDEO_SECONDS
from story_generator import generate_story
from tts_generator import generate_tts
from caption_generator import get_captions
from gameplay_manager import get_random_clip, cleanup_pexels_clip
from video_composer import compose_video
from youtube_uploader import upload_short
from notifier import notify_success, notify_failure
from adaptive_strategy import get_strategy
from performance_tracker import refresh_stats, record_post

# How many times to regenerate the story/voice if the result lands outside the
# allowed duration band before giving up on this cycle (rather than post a bad video).
MAX_GEN_ATTEMPTS = 3

# FFmpeg install path (fallback if not in PATH yet)
FFMPEG_FALLBACK_DIR = r"C:\ffmpeg\ffmpeg-8.1.1-essentials_build\bin"


def _ensure_ffmpeg_in_path():
    """Add FFmpeg to PATH for this session if it isn't there already."""
    import shutil
    if not shutil.which("ffmpeg") and os.path.exists(os.path.join(FFMPEG_FALLBACK_DIR, "ffmpeg.exe")):
        os.environ["PATH"] = FFMPEG_FALLBACK_DIR + os.pathsep + os.environ.get("PATH", "")


def run_pipeline(upload: bool = True, strategy: dict = None) -> dict:
    """Run the full automation pipeline once."""

    print("\n" + "="*60)
    print("  YouTube Shorts Automation - Starting Pipeline")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60 + "\n")

    results = {}

    # --- Adaptive strategy: decide length / theme / hook for this video ---
    if strategy is None:
        strategy = get_strategy()
    print("--- ADAPTIVE STRATEGY ---")
    print(f"  Target: {strategy['target_seconds']}s (~{strategy['target_words']} words) "
          f"| theme={strategy['theme']} | hook={strategy['hook']} | bg={strategy['background']}")
    print(f"  {strategy['rationale']}\n")
    results["strategy"] = strategy

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # --- Steps 1-2 with duration guard: regenerate until the voice track fits the band ---
    story_data = None
    tts_result = None
    for attempt in range(1, MAX_GEN_ATTEMPTS + 1):
        print(f"--- STEP 1: Generating Story (attempt {attempt}/{MAX_GEN_ATTEMPTS}) ---")
        story_data = generate_story(strategy)
        print(f"  Title: {story_data['title']}")
        print(f"  Words: {len(story_data['story'].split())}\n")

        print("--- STEP 2: Generating Voice ---")
        tts_result = generate_tts(story_data["story"], filename_prefix=f"story_{timestamp}")
        dur = tts_result["duration"]
        print(f"  Audio: {os.path.basename(tts_result['audio_path'])}")
        print(f"  Duration: {dur:.1f}s\n")

        if MIN_VIDEO_SECONDS <= dur <= MAX_VIDEO_SECONDS:
            break
        print(f"  [GUARD] Duration {dur:.1f}s outside allowed "
              f"{MIN_VIDEO_SECONDS}-{MAX_VIDEO_SECONDS}s band — regenerating.\n")
    else:
        # Never post a video that fails the duration guard.
        raise RuntimeError(
            f"Could not produce a video within {MIN_VIDEO_SECONDS}-{MAX_VIDEO_SECONDS}s "
            f"after {MAX_GEN_ATTEMPTS} attempts (last: {tts_result['duration']:.1f}s). "
            "Skipping this cycle instead of posting a malformed video."
        )

    results["story"] = story_data
    results["tts"] = tts_result

    # --- Step 3: Build Captions ---
    print("--- STEP 3: Building Captions ---")
    captions = get_captions(tts_result, AUDIO_DIR, prefix=f"story_{timestamp}")
    results["captions"] = captions
    print(f"  Caption segments: {len(captions)}\n")

    # --- Step 4: Get Background Footage (adaptive category) ---
    print("--- STEP 4: Getting Background Footage ---")
    gameplay_path, used_background = get_random_clip(strategy.get("background"))
    results["gameplay"] = gameplay_path
    results["used_background"] = used_background
    print(f"  Background used: {used_background}\n")

    # --- Step 5: Compose Video ---
    print("--- STEP 5: Composing Video ---")
    try:
        video_path = compose_video(
            gameplay_path=gameplay_path,
            audio_path=tts_result["audio_path"],
            caption_segments=captions,
            title=story_data["title"],
            audio_duration=tts_result["duration"],
        )
    finally:
        # Delete the temporary Pexels clip now that FFmpeg has finished reading it
        cleanup_pexels_clip(gameplay_path)
    results["video_path"] = video_path

    # --- Final duration check on the composed file (belt and braces) ---
    try:
        from video_composer import get_video_duration
        final_dur = get_video_duration(video_path)
        if final_dur > MAX_VIDEO_SECONDS:
            raise RuntimeError(
                f"Composed video is {final_dur:.1f}s (> {MAX_VIDEO_SECONDS}s cap) — refusing to upload."
            )
        print(f"  Final video duration: {final_dur:.1f}s (within cap)\n")
    except RuntimeError:
        raise
    except Exception as e:
        print(f"  [warn] could not verify final duration ({e})\n")

    # --- Step 6: Upload to YouTube ---
    if upload:
        print("--- STEP 6: Uploading to YouTube ---")
        upload_result = upload_short(
            video_path=video_path,
            title=story_data["title"],
            story_hashtags=story_data.get("hashtags", ""),
        )
        results["upload"] = upload_result

        # Record this post's parameters so the engine can learn from its performance.
        try:
            record_post(upload_result.get("video_id"), {
                "title":          story_data["title"],
                "theme":          story_data.get("theme"),
                "hook":           story_data.get("hook"),
                "background":     results.get("used_background"),
                "target_seconds": strategy.get("target_seconds"),
                "target_words":   strategy.get("target_words"),
                "actual_seconds": round(tts_result["duration"], 1),
                "word_count":     len(story_data["story"].split()),
            })
        except Exception as e:
            print(f"  [warn] could not record post for learning ({e})")
        print()
    else:
        print("--- STEP 6: Upload Skipped (--no-upload) ---")
        print(f"  Video saved to: {video_path}\n")

    print("="*60)
    print("  PIPELINE COMPLETE")
    if upload and "upload" in results:
        print(f"  YouTube URL: {results['upload']['url']}")
    print(f"  Video file:  {results.get('video_path', 'N/A')}")
    print("="*60 + "\n")

    return results


def check_prerequisites() -> bool:
    """Check that all required tools and configs are in place."""
    import shutil
    issues = []

    # Ensure FFmpeg is in PATH for this session
    _ensure_ffmpeg_in_path()

    # Check FFmpeg
    if not shutil.which("ffmpeg"):
        issues.append(
            "FFmpeg not found.\n"
            "  It should be at: " + FFMPEG_FALLBACK_DIR + "\n"
            "  Please re-run the setup or check the SETUP.md file."
        )

    # Check Groq API key (free at console.groq.com)
    from config import GROQ_API_KEY
    if not GROQ_API_KEY or GROQ_API_KEY == "PASTE-YOUR-GROQ-KEY-HERE":
        issues.append(
            "GROQ_API_KEY not set. Add it to the .env file.\n"
            "  Get your FREE key at: https://console.groq.com/"
        )

    if issues:
        print("\n[FAIL] Prerequisites check failed:")
        for issue in issues:
            print(f"\n  - {issue}")
        print("\nSee SETUP.md for help.\n")
        return False

    print("[OK] Prerequisites check passed\n")
    return True


def main():
    parser = argparse.ArgumentParser(description="YouTube Shorts Horror Story Automation")
    parser.add_argument("--count", type=int, default=1,
                        help="Number of videos to generate (default: 1)")
    parser.add_argument("--no-upload", action="store_true",
                        help="Generate video but skip YouTube upload")
    parser.add_argument("--skip-prereq-check", action="store_true",
                        help="Skip prerequisite checking")
    args = parser.parse_args()

    if not args.skip_prereq_check:
        if not check_prerequisites():
            sys.exit(1)

    upload = not args.no_upload
    successful = 0
    failed = 0

    # Pull the latest performance numbers so the adaptive engine learns from how
    # previously-posted videos are actually doing. Fails soft (offline / no token).
    print("--- Refreshing performance history ---")
    try:
        refresh_stats()
    except Exception as e:
        print(f"[warn] performance refresh skipped ({e})")
    print()

    for i in range(args.count):
        if args.count > 1:
            print(f"\n{'='*60}")
            print(f"  VIDEO {i+1} of {args.count}")
            print(f"{'='*60}")

        try:
            result = run_pipeline(upload=upload)
            successful += 1
            if upload and result.get("upload"):
                notify_success(
                    title=result["upload"]["title"],
                    url=result["upload"]["url"],
                )
        except KeyboardInterrupt:
            print("\nInterrupted by user.")
            break
        except Exception as e:
            failed += 1
            print(f"\n[FAIL] Pipeline failed: {e}")
            traceback.print_exc()
            notify_failure(str(e))
            if args.count > 1:
                print("Continuing with next video...\n")

    if args.count > 1:
        print(f"\nResult: {successful} succeeded, {failed} failed out of {args.count}")


if __name__ == "__main__":
    main()
