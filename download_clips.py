"""
Downloads and compresses additional gameplay clips for use in YouTube Shorts.
Run this on your PC to add more clips to the rotation.
"""
import os
import subprocess
import shutil
from pathlib import Path

GAMEPLAY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gameplay")
FFMPEG = r"C:\ffmpeg\ffmpeg-8.1.1-essentials_build\bin\ffmpeg.exe"
MAX_SIZE_MB = 90  # Keep under GitHub's 100MB limit

SEARCHES = [
    ("ytsearch1:minecraft parkour vertical 1080p shorts no copyright free use", "mc_parkour.mp4"),
    ("ytsearch1:subway surfers gameplay vertical portrait 1080p free use", "subway_2.mp4"),
    ("ytsearch1:minecraft vertical gameplay shorts satisfying no copyright", "mc_satisfying.mp4"),
    ("ytsearch1:subway surfers vertical no copyright long gameplay 1080p", "subway_3.mp4"),
    ("ytsearch1:minecraft caves vertical shorts gameplay no copyright", "mc_caves.mp4"),
    ("ytsearch1:satisfying minecraft build vertical shorts no copyright", "mc_build.mp4"),
]

def find_ytdlp():
    found = shutil.which("yt-dlp")
    if found:
        return found
    import sys
    scripts = os.path.join(os.path.dirname(sys.executable), "Scripts", "yt-dlp.exe")
    if os.path.exists(scripts):
        return scripts
    return "yt-dlp"

def compress_clip(input_path, output_path):
    """Re-encode clip to be under 90MB while keeping 1080p quality."""
    print(f"  Compressing to stay under {MAX_SIZE_MB}MB...")
    cmd = [
        FFMPEG, "-y",
        "-i", input_path,
        "-vf", f"scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:1920,setsar=1",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "28",
        "-an",  # No audio needed
        "-movflags", "+faststart",
        output_path
    ]
    subprocess.run(cmd, capture_output=True)
    size_mb = os.path.getsize(output_path) / (1024*1024)
    print(f"  Compressed to {size_mb:.1f} MB")
    return size_mb

def download_and_prepare(search, filename):
    ytdlp = find_ytdlp()
    output = os.path.join(GAMEPLAY_DIR, filename)
    temp = os.path.join(GAMEPLAY_DIR, f"_temp_{filename}")

    if os.path.exists(output):
        print(f"  Already exists: {filename}")
        return True

    print(f"\nDownloading: {filename}")
    cmd = [
        ytdlp,
        "--ffmpeg-location", r"C:\ffmpeg\ffmpeg-8.1.1-essentials_build\bin",
        "-f", "bestvideo[height=1080][ext=mp4]/bestvideo[height=1080]/bestvideo[height<=1080]",
        "--no-playlist",
        "--max-downloads", "1",
        "-o", temp,
        "--no-warnings",
        search,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    if result.returncode not in (0, 101) or not os.path.exists(temp):
        print(f"  Download failed")
        return False

    size_mb = os.path.getsize(temp) / (1024*1024)
    print(f"  Downloaded: {size_mb:.1f} MB")

    if size_mb > MAX_SIZE_MB:
        compress_clip(temp, output)
        os.remove(temp)
    else:
        os.rename(temp, output)

    final_size = os.path.getsize(output) / (1024*1024)
    print(f"  Ready: {filename} ({final_size:.1f} MB)")
    return True

if __name__ == "__main__":
    os.makedirs(GAMEPLAY_DIR, exist_ok=True)
    success = 0
    for search, filename in SEARCHES:
        try:
            if download_and_prepare(search, filename):
                success += 1
        except Exception as e:
            print(f"  Error: {e}")

    print(f"\nDone! {success}/{len(SEARCHES)} clips ready.")
    print("Now run: git add gameplay/*.mp4 && git commit -m 'Add more gameplay clips' && git push")
