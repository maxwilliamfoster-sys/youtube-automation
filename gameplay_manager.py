"""
Gameplay Manager — downloads fresh background clips for every Short.
On cloud (GitHub Actions): uses Pexels API for a brand new clip every run.
On local PC: uses yt-dlp to download Minecraft/Subway Surfers clips.
"""

import os
import random
import subprocess
import shutil
import json
import urllib.request
from pathlib import Path
from typing import List
from config import GAMEPLAY_DIR

# Pexels API — free, works on cloud servers, unique clip every time
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")

# Search terms that give great vertical background footage on Pexels
PEXELS_SEARCHES = [
    "satisfying",
    "minecraft parkour",
    "colorful satisfying",
    "parkour free running",
    "satisfying sand",
    "subway city",
    "gaming",
    "satisfying slime",
    "city walk",
    "nature satisfying",
]

# FFmpeg location for yt-dlp merging — find dynamically so it works on Windows and Linux
import shutil as _shutil
_ffmpeg_which = _shutil.which("ffmpeg")
if _ffmpeg_which:
    import os as _os
    FFMPEG_DIR = _os.path.dirname(_ffmpeg_which)
else:
    FFMPEG_DIR = r"C:\ffmpeg\ffmpeg-8.1.1-essentials_build\bin"

# Minimum resolution — clips below this width are skipped
MIN_CLIP_WIDTH = 1080


def get_clip_width(clip_path: str) -> int:
    """Return the width of a video clip using FFprobe. Returns 0 on failure."""
    ffprobe = os.path.join(FFMPEG_DIR, "ffprobe.exe")
    if not os.path.exists(ffprobe):
        ffprobe = shutil.which("ffprobe") or "ffprobe"
    try:
        cmd = [
            ffprobe, "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            "-select_streams", "v:0",
            clip_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        data = json.loads(result.stdout)
        return int(data["streams"][0].get("width", 0))
    except Exception:
        return 0


def filter_hd_clips(clips: List[str]) -> List[str]:
    """Return only clips that meet the minimum resolution requirement.
    If ffprobe fails to read a clip, include it anyway (assume it's valid)."""
    hd = []
    for clip in clips:
        width = get_clip_width(clip)
        if width == 0:
            # ffprobe couldn't read it — include it rather than skip it
            print(f"[Gameplay] Could not check resolution, including: {os.path.basename(clip)}")
            hd.append(clip)
        elif width >= MIN_CLIP_WIDTH:
            hd.append(clip)
        else:
            print(f"[Gameplay] Skipping low-res clip ({width}px wide): {os.path.basename(clip)}")
    return hd

# yt-dlp executable
def _ytdlp() -> str:
    found = shutil.which("yt-dlp")
    if found:
        return found
    scripts = os.path.join(os.path.dirname(__file__), "..", "Scripts", "yt-dlp.exe")
    # Try Python Scripts folder
    import sys
    scripts2 = os.path.join(os.path.dirname(sys.executable), "Scripts", "yt-dlp.exe")
    if os.path.exists(scripts2):
        return scripts2
    # Try pip --user location
    user_scripts = os.path.expanduser(r"~\AppData\Local\Python\pythoncore-3.14-64\Scripts\yt-dlp.exe")
    if os.path.exists(user_scripts):
        return user_scripts
    return "yt-dlp"

# Search queries — yt-dlp will search YouTube for a matching video
GAMEPLAY_SEARCHES = [
    "ytsearch1:subway surfers gameplay vertical no copyright 1080p portrait",
    "ytsearch1:minecraft parkour vertical shorts no copyright 1080p",
    "ytsearch1:subway surfers gameplay no copyright vertical portrait long",
    "ytsearch1:minecraft mobile gameplay vertical no copyright shorts background",
    "ytsearch1:subway surfers vertical gameplay free use no copyright",
]


def download_clip(search_or_url: str, output_path: str) -> bool:
    """Download a clip using yt-dlp with FFmpeg for merging."""
    ytdlp = _ytdlp()

    cmd = [
        ytdlp,
        "--ffmpeg-location", FFMPEG_DIR,
        # Video-only at best quality — no audio needed (we use TTS), no merging needed
        # Prefer 1080p h264/mp4, fall back to any 1080p, then best available
        "-f", "bestvideo[height=1080][ext=mp4]/bestvideo[height=1080]/bestvideo[height<=1080][ext=mp4]/bestvideo[height<=1080]/bestvideo",
        "--no-playlist",
        "--max-downloads", "1",
        "-o", output_path,
        "--no-warnings",
        "--quiet",
        search_or_url,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        # code 0 = success, 101 = max-downloads hit (also success — we got our 1 clip)
        return result.returncode in (0, 101) and os.path.exists(output_path)
    except Exception as e:
        print(f"[Gameplay] Download error: {e}")
        return False


def ensure_gameplay_clips(min_clips: int = 3) -> List[str]:
    """
    Make sure we have at least min_clips gameplay videos.
    Downloads from YouTube if needed.
    """
    os.makedirs(GAMEPLAY_DIR, exist_ok=True)

    existing = [str(p) for p in Path(GAMEPLAY_DIR).glob("*.mp4")
                if "fallback" not in p.name.lower()]

    print(f"[Gameplay] Found {len(existing)} existing clips")

    if len(existing) >= min_clips:
        return existing

    print("[Gameplay] Downloading gameplay clips from YouTube...")
    random.shuffle(GAMEPLAY_SEARCHES)

    for i, search in enumerate(GAMEPLAY_SEARCHES):
        if len(existing) >= min_clips:
            break

        output = os.path.join(GAMEPLAY_DIR, f"clip_{i+1:03d}.mp4")
        if os.path.exists(output):
            existing.append(output)
            continue

        print(f"[Gameplay] Searching: {search.replace('ytsearch1:','')}")
        success = download_clip(search, output)

        if success:
            size_mb = os.path.getsize(output) / (1024*1024)
            print(f"[Gameplay] Downloaded clip_{i+1:03d}.mp4 ({size_mb:.0f} MB)")
            existing.append(output)
        else:
            print(f"[Gameplay] Search {i+1} failed — trying next")

    if not existing:
        print("[Gameplay] WARNING: No clips downloaded. Add .mp4 files to the gameplay/ folder manually.")

    return existing


def download_pexels_clip(output_path: str) -> bool:
    """
    Download a fresh random vertical video from Pexels API.
    Returns True if successful. Requires PEXELS_API_KEY env var.
    """
    if not PEXELS_API_KEY:
        return False

    try:
        search = random.choice(PEXELS_SEARCHES)
        print(f"[Gameplay] Fetching fresh clip from Pexels: '{search}'...")

        url = f"https://api.pexels.com/videos/search?query={urllib.parse.quote(search)}&orientation=portrait&size=large&per_page=20"
        req = urllib.request.Request(url, headers={"Authorization": PEXELS_API_KEY})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())

        videos = data.get("videos", [])
        if not videos:
            print(f"[Gameplay] No Pexels results for '{search}'")
            return False

        # Pick a random video from results, prefer HD
        random.shuffle(videos)
        for video in videos:
            files = video.get("video_files", [])
            # Find best vertical file (portrait, HD preferred)
            portrait_files = [
                f for f in files
                if f.get("width", 0) < f.get("height", 0)  # portrait = width < height
                and f.get("height", 0) >= 1080
                and f.get("file_type") == "video/mp4"
            ]
            if not portrait_files:
                portrait_files = [
                    f for f in files
                    if f.get("width", 0) < f.get("height", 0)
                    and f.get("file_type") == "video/mp4"
                ]
            if not portrait_files:
                continue

            # Pick highest quality portrait file
            best = max(portrait_files, key=lambda f: f.get("height", 0))
            video_url = best.get("link")
            if not video_url:
                continue

            print(f"[Gameplay] Downloading from Pexels ({best.get('height')}p)...")
            urllib.request.urlretrieve(video_url, output_path)
            size_mb = os.path.getsize(output_path) / (1024*1024)
            print(f"[Gameplay] Pexels clip downloaded ({size_mb:.0f} MB)")
            return True

        print("[Gameplay] No suitable portrait files found on Pexels")
        return False

    except Exception as e:
        print(f"[Gameplay] Pexels download error: {e}")
        return False


import urllib.parse

_last_clip_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".last_clip")


def get_random_clip() -> str:
    """
    Return a gameplay clip for this Short.

    Priority:
    1. Pexels API (if PEXELS_API_KEY is set) — downloads a fresh unique clip every run, then deletes it after use.
    2. Local/repo clips — rotates through available clips so the same one isn't reused back-to-back.
    """
    # --- Pexels: fresh clip every run ---
    if PEXELS_API_KEY:
        os.makedirs(GAMEPLAY_DIR, exist_ok=True)
        pexels_path = os.path.join(GAMEPLAY_DIR, f"_pexels_fresh_{os.getpid()}.mp4")
        if download_pexels_clip(pexels_path):
            print(f"[Gameplay] Using fresh Pexels clip")
            return pexels_path  # caller uses it; we'll clean up after compose

    # --- Fallback: rotate through local clips ---
    clips = ensure_gameplay_clips()
    hd_clips = filter_hd_clips(clips)

    if not hd_clips:
        raise RuntimeError(
            "No HD gameplay clips found (minimum 1080px wide)!\n"
            "Add 1080p Minecraft or Subway Surfers .mp4 files to:\n"
            f"  {GAMEPLAY_DIR}"
        )

    hd_clips = sorted(hd_clips)

    last_clip = None
    if os.path.exists(_last_clip_file):
        try:
            with open(_last_clip_file, "r") as f:
                last_clip = f.read().strip()
        except Exception:
            pass

    available = [c for c in hd_clips if c != last_clip]
    if not available:
        available = hd_clips

    chosen = random.choice(available)

    try:
        with open(_last_clip_file, "w") as f:
            f.write(chosen)
    except Exception:
        pass

    print(f"[Gameplay] Using: {os.path.basename(chosen)}")
    return chosen


def cleanup_pexels_clip(clip_path: str) -> None:
    """Delete a temporary Pexels clip after the video has been composed."""
    if "_pexels_fresh_" in os.path.basename(clip_path):
        try:
            os.remove(clip_path)
            print(f"[Gameplay] Cleaned up temporary Pexels clip")
        except Exception:
            pass


def add_custom_clip(source_path: str) -> str:
    """Copy your own gameplay clip into the library."""
    os.makedirs(GAMEPLAY_DIR, exist_ok=True)
    filename = os.path.basename(source_path)
    dest = os.path.join(GAMEPLAY_DIR, filename)
    shutil.copy2(source_path, dest)
    print(f"[Gameplay] Added: {filename}")
    return dest


if __name__ == "__main__":
    clips = ensure_gameplay_clips(min_clips=2)
    print(f"Available clips: {[os.path.basename(c) for c in clips]}")
