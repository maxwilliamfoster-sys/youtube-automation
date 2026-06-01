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
    "misty forest",
    "rain window night",
    "dark forest walk",
    "abandoned building",
    "night city rain",
    "campfire dark",
    "storm lightning",
    "candle flame dark",
    "foggy road",
    "underwater dark",
    "fire embers",
    "old graveyard",
    "snow blizzard",
    "sunset dramatic",
    "waterfall nature",
    "mountain fog",
    "ocean waves night",
    "parkour rooftop",
    "satisfying kinetic sand",
    "city timelapse night",
]

# Pexels (and its CDN) reject the default Python-urllib User-Agent with HTTP 403,
# so every request must look like a browser. This was why fresh backgrounds stopped
# downloading and every video reused the same handful of gameplay clips.
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Background CATEGORIES the adaptive engine A/B-tests. Each maps to either a set of
# Pexels search terms (atmospheric, horror-fitting footage) or the gameplay clips
# (the high-retention "brainrot" style). Keys MUST match config.BACKGROUND_CATEGORIES.
BACKGROUND_CATEGORIES = {
    "fog":       {"source": "pexels",   "queries": ["misty forest", "dark forest walk", "foggy road", "mountain fog"]},
    "rain":      {"source": "pexels",   "queries": ["rain window night", "night city rain"]},
    "fire":      {"source": "pexels",   "queries": ["campfire dark", "candle flame dark", "fire embers"]},
    "storm":     {"source": "pexels",   "queries": ["storm lightning", "snow blizzard"]},
    "abandoned": {"source": "pexels",   "queries": ["abandoned building", "old graveyard"]},
    "water":     {"source": "pexels",   "queries": ["ocean waves night", "underwater dark", "waterfall nature"]},
    "city":      {"source": "pexels",   "queries": ["city timelapse night", "parkour rooftop"]},
    "gameplay":  {"source": "gameplay", "queries": []},
}

_last_search_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".last_search")

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


import urllib.parse


def _pexels_get(url: str) -> dict:
    """GET the Pexels API with the auth + browser User-Agent (avoids 403)."""
    req = urllib.request.Request(url, headers={"Authorization": PEXELS_API_KEY, "User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _download_file(url: str, output_path: str) -> None:
    """Stream a file to disk with a browser User-Agent (the CDN also 403s default urllib)."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=90) as r, open(output_path, "wb") as f:
        shutil.copyfileobj(r, f)


def download_pexels_clip(output_path: str, queries: list = None) -> bool:
    """
    Download a fresh random vertical video from Pexels API.

    Args:
        output_path: where to save the .mp4
        queries:     restrict to these search terms (a background category). If None,
                     uses the full PEXELS_SEARCHES list.
    Returns True on success. Requires PEXELS_API_KEY.
    """
    if not PEXELS_API_KEY:
        return False

    search_pool = queries if queries else PEXELS_SEARCHES
    try:
        # Rotate search terms — avoid repeating the exact same one back-to-back.
        last_search = None
        if os.path.exists(_last_search_file):
            try:
                with open(_last_search_file, "r") as f:
                    last_search = f.read().strip()
            except Exception:
                pass
        available_searches = [s for s in search_pool if s != last_search] or search_pool
        search = random.choice(available_searches)
        try:
            with open(_last_search_file, "w") as f:
                f.write(search)
        except Exception:
            pass
        print(f"[Gameplay] Fetching fresh clip from Pexels: '{search}'...")

        url = (f"https://api.pexels.com/videos/search?query={urllib.parse.quote(search)}"
               f"&orientation=portrait&size=large&per_page=20")
        data = _pexels_get(url)

        videos = data.get("videos", [])
        if not videos:
            print(f"[Gameplay] No Pexels results for '{search}'")
            return False

        random.shuffle(videos)
        for video in videos:
            files = video.get("video_files", [])
            portrait_files = [
                f for f in files
                if f.get("width", 0) < f.get("height", 0)
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

            best = max(portrait_files, key=lambda f: f.get("height", 0))
            video_url = best.get("link")
            if not video_url:
                continue

            print(f"[Gameplay] Downloading from Pexels ({best.get('height')}p)...")
            _download_file(video_url, output_path)
            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            print(f"[Gameplay] Pexels clip downloaded ({size_mb:.0f} MB)")
            return True

        print("[Gameplay] No suitable portrait files found on Pexels")
        return False

    except Exception as e:
        print(f"[Gameplay] Pexels download error: {e}")
        return False

_last_clip_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".last_clip")


def _local_gameplay_clip() -> str:
    """Rotate through the local/yt-dlp gameplay clips (Subway Surfers / Minecraft)."""
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

    available = [c for c in hd_clips if c != last_clip] or hd_clips
    chosen = random.choice(available)
    try:
        with open(_last_clip_file, "w") as f:
            f.write(chosen)
    except Exception:
        pass
    print(f"[Gameplay] Using local clip: {os.path.basename(chosen)}")
    return chosen


def get_random_clip(background: str = None):
    """
    Return (clip_path, used_background) for this Short.

    `background` is a category from BACKGROUND_CATEGORIES (chosen by the adaptive
    engine). Atmospheric categories pull a fresh Pexels clip; "gameplay" (or any
    Pexels failure) falls back to the local Subway Surfers / Minecraft clips. The
    SECOND return value is the background actually used, so the engine learns from
    reality even when a fallback kicks in.
    """
    os.makedirs(GAMEPLAY_DIR, exist_ok=True)

    cat = background if background in BACKGROUND_CATEGORIES else random.choice(list(BACKGROUND_CATEGORIES))
    spec = BACKGROUND_CATEGORIES[cat]

    # --- Atmospheric (Pexels): fresh clip every run ---
    if spec["source"] == "pexels" and PEXELS_API_KEY:
        pexels_path = os.path.join(GAMEPLAY_DIR, f"_pexels_fresh_{os.getpid()}.mp4")
        if download_pexels_clip(pexels_path, queries=spec["queries"]):
            print(f"[Gameplay] Using fresh Pexels clip (background={cat})")
            return pexels_path, cat
        print(f"[Gameplay] Pexels '{cat}' unavailable — falling back to gameplay clips")

    # --- Gameplay category, or Pexels fallback ---
    chosen = _local_gameplay_clip()
    used = cat if spec["source"] == "gameplay" else "gameplay"
    return chosen, used


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
