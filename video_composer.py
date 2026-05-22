"""
Video Composer — combines gameplay footage + TTS audio + captions into a YouTube Short.
Output: 1080x1920 (9:16), 30fps, high quality MP4 with burned-in captions.
"""

import os
import random
import glob
import subprocess
import json
import shutil
from typing import List, Dict
from datetime import datetime
from config import (
    VIDEO_WIDTH, VIDEO_HEIGHT, VIDEO_FPS,
    CAPTION_FONT_SIZE, CAPTION_FONT_COLOR,
    CAPTION_STROKE_COLOR, CAPTION_STROKE_WIDTH,
    CAPTION_POSITION, OUTPUT_DIR, ASSETS_DIR
)

# ─── Background music ─────────────────────────────────────────────────────────
MUSIC_DIR = os.path.join(ASSETS_DIR, "music")

def get_random_music_track() -> str | None:
    """Return a random horror ambient MP3 from assets/music/, or None if none found."""
    if not os.path.isdir(MUSIC_DIR):
        return None
    tracks = glob.glob(os.path.join(MUSIC_DIR, "*.mp3"))
    return random.choice(tracks) if tracks else None

# Find FFmpeg — check PATH first, then the known install location
def _find_ffmpeg(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    fallback = rf"C:\ffmpeg\ffmpeg-8.1.1-essentials_build\bin\{name}.exe"
    if os.path.exists(fallback):
        return fallback
    return name

FFMPEG  = _find_ffmpeg("ffmpeg")
FFPROBE = _find_ffmpeg("ffprobe")


def get_video_duration(video_path: str) -> float:
    """Get duration of a video file using FFprobe."""
    cmd = [
        FFPROBE, "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def get_random_start_time(video_path: str, clip_duration: float) -> float:
    """Pick a random start time so we use a different part of the gameplay each video."""
    try:
        video_duration = get_video_duration(video_path)
    except Exception:
        return 0
    # Leave 5s buffer at end; skip first 10s (intros)
    skip_start = min(10, video_duration * 0.05)
    max_start  = max(skip_start, video_duration - clip_duration - 5)
    if max_start <= skip_start:
        return 0
    return random.uniform(skip_start, max_start)


def escape_ffmpeg_text(text: str) -> str:
    # Strip EVERY character that can break FFmpeg drawtext parsing
    bad_chars = [
        (chr(0x2018), ''),  # left curly apostrophe  -> remove
        (chr(0x2019), ''),  # right curly apostrophe -> remove
        (chr(0x201C), ''),  # left double quote      -> remove
        (chr(0x201D), ''),  # right double quote     -> remove
        (chr(39),     ''),  # straight apostrophe    -> remove (CRITICAL: breaks single-quote parsing)
        (chr(92),  ' '),    # backslash -> space
        (chr(34),  ' '),    # double quote -> space
        (chr(58),  ' '),    # colon -> space
        (chr(37),  ''),     # percent -> remove
    ]
    for bad, good in bad_chars:
        text = text.replace(bad, good)
    return text.strip()
def build_filter_script(caption_segments: List[Dict], scale_crop: str) -> str:
    """
    Build the complete FFmpeg filter chain as a script string.
    Writing it to a file avoids all shell/comma escaping issues.
    """
    y_pos = int(VIDEO_HEIGHT * CAPTION_POSITION)

    # Arial Bold — use a relative path so there's no drive-letter colon to escape
    # FFmpeg resolves this relative to the working directory (the project folder)
    font_option = "fontfile=assets/arialbd.ttf:"

    lines = [scale_crop]

    for seg in caption_segments:
        text  = escape_ffmpeg_text(seg["text"].upper())
        start = seg["start"]
        end   = seg["end"]

        # Single-quoted text (apostrophes already stripped, safe on Windows subprocess)
        drawtext = (
            f"drawtext={font_option}"
            f"text='{text}':"
            f"fontsize={CAPTION_FONT_SIZE}:"
            f"fontcolor={CAPTION_FONT_COLOR}:"
            f"borderw={CAPTION_STROKE_WIDTH}:"
            f"bordercolor={CAPTION_STROKE_COLOR}:"
            f"x=max(20\\,(w-text_w)/2):"
            f"y={y_pos}:"
            f"enable='between(t,{start:.3f},{end:.3f})'"
        )
        lines.append(drawtext)

    # Join with comma — write as single filter chain
    return ",".join(lines)


def compose_video(
    gameplay_path: str,
    audio_path: str,
    caption_segments: List[Dict],
    title: str = "horror_short",
    audio_duration: float = None,
) -> str:
    """
    Compose the final YouTube Short video.

    Returns:
        Path to the output MP4 file
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_title = "".join(c for c in title if c.isalnum() or c in " _-").replace(" ", "_")[:40]
    output_path      = os.path.join(OUTPUT_DIR, f"{safe_title}_{timestamp}.mp4")
    filter_script    = os.path.join(OUTPUT_DIR, f"filter_{timestamp}.txt")

    if audio_duration is None:
        audio_duration = get_video_duration(audio_path)

    print(f"[Video] Composing Short ({audio_duration:.1f}s)...")

    start_time = get_random_start_time(gameplay_path, audio_duration)
    print(f"[Video] Gameplay start: {start_time:.1f}s")

    # Convert all paths to absolute + forward slashes (FFmpeg needs this on Windows)
    def absfwd(p): return os.path.abspath(p).replace("\\", "/")

    abs_gameplay  = absfwd(gameplay_path)
    abs_audio     = absfwd(audio_path)
    abs_output    = absfwd(output_path)

    # Scale height to 1920 with lanczos (sharpest), then crop width to 1080
    # Smart scale: portrait clips just get resized cleanly to 1080x1920.
    # Landscape clips get scaled to fill height then cropped — but we avoid those now.
    scale_crop = (
        f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}"
        f":force_original_aspect_ratio=increase"
        f":flags=lanczos"
        f",crop={VIDEO_WIDTH}:{VIDEO_HEIGHT}"
        f",setsar=1"
    )

    # Build filter string and pass directly as -vf (subprocess list = no shell, no comma escaping)
    vf_filter = build_filter_script(caption_segments, scale_crop)

    # ── Background music ──────────────────────────────────────────────────────
    music_path = get_random_music_track()
    if music_path:
        abs_music = absfwd(music_path)
        music_name = os.path.basename(music_path)
        print(f"[Video] Background music: {music_name}")
        # Loop music so it covers the whole video, mix at low volume under narration
        audio_filter = (
            "[2:a]volume=0.13,afade=t=in:st=0:d=2,afade=t=out:st={fade_out}:d=3[music];"
            "[1:a][music]amix=inputs=2:duration=first:weights=1 1[aout]"
        ).format(fade_out=max(0, audio_duration - 3))
        cmd = [
            FFMPEG, "-y",
            "-ss", str(start_time),
            "-i", abs_gameplay,
            "-i", abs_audio,
            "-stream_loop", "-1", "-i", abs_music,   # loop music to fill video length
            "-filter_complex", audio_filter,
            "-vf", vf_filter,
            "-map", "0:v:0",
            "-map", "[aout]",
            "-t", str(audio_duration + 0.5),
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "18",
            "-c:a", "aac",
            "-b:a", "192k",
            "-r", str(VIDEO_FPS),
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            abs_output,
        ]
    else:
        # No music files found — narration only
        print("[Video] No music files found in assets/music/ — narration only")
        cmd = [
            FFMPEG, "-y",
            "-ss", str(start_time),
            "-i", abs_gameplay,
            "-i", abs_audio,
            "-vf", vf_filter,
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-t", str(audio_duration + 0.5),
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "18",
            "-c:a", "aac",
            "-b:a", "192k",
            "-r", str(VIDEO_FPS),
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            abs_output,
        ]

    print("[Video] Running FFmpeg (high quality, may take ~60s)...")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print("[Video] FFmpeg error:")
        print(result.stderr[-3000:])
        raise RuntimeError(f"FFmpeg failed with code {result.returncode}")

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f"[Video] Done! {output_path}")
    print(f"[Video] File size: {size_mb:.1f} MB")

    return output_path
