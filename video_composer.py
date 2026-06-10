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
    """Get duration of a video file using FFprobe (container/format duration)."""
    cmd = [
        FFPROBE, "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def get_stream_durations(video_path: str) -> tuple:
    """
    Return (video_stream_seconds, audio_stream_seconds) for a file.
    The container duration equals the LONGER stream, so a frozen background (video
    stream ends early while audio continues) is only visible by comparing the two.
    Missing values fall back to 0.0.
    """
    cmd = [
        FFPROBE, "-v", "quiet", "-print_format", "json",
        "-show_streams", "-show_format", video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        data = json.loads(result.stdout)
    except Exception:
        return 0.0, 0.0
    fmt = float(data.get("format", {}).get("duration", 0) or 0)
    vdur = adur = 0.0
    for s in data.get("streams", []):
        d = float(s.get("duration", 0) or 0)
        if s.get("codec_type") == "video":
            vdur = d or fmt
        elif s.get("codec_type") == "audio":
            adur = d or fmt
    return vdur, adur


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
def _wrap_ffmpeg_text(text: str, max_chars: int = 16) -> str:
    """Greedily wrap text to lines of at most max_chars, joined by real newlines
    (FFmpeg drawtext renders an embedded newline as a line break)."""
    words, out, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > max_chars:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        out.append(cur)
    return "\n".join(out)


def build_filter_script(
    caption_segments: List[Dict],
    scale_crop: str,
    hook_text: str = None,
    cta_text: str = None,
    total_duration: float = None,
) -> str:
    """
    Build the complete FFmpeg filter chain as a script string.
    Writing it to a file avoids all shell/comma escaping issues.

    Optional retention overlays:
      hook_text:      big headline card shown for the first ~3.6s (the scroll-stopper).
      cta_text:       follow call-to-action shown in the final ~2.6s.
      total_duration: required for cta timing (video length in seconds).
    """
    y_pos = int(VIDEO_HEIGHT * CAPTION_POSITION)

    # Montserrat Bold — clean, modern, widely used on social media.
    # Falls back to Arial Bold if the font hasn't been downloaded yet.
    # Run:  py setup_music.py  to download MontserratBold.ttf to assets/
    montserrat = os.path.join(ASSETS_DIR, "MontserratBold.ttf")
    arial_bd   = os.path.join(ASSETS_DIR, "arialbd.ttf")
    if os.path.exists(montserrat):
        _font_file = "assets/MontserratBold.ttf"
    elif os.path.exists(arial_bd):
        _font_file = "assets/arialbd.ttf"
    else:
        _font_file = None

    font_option = f"fontfile={_font_file}:" if _font_file else ""

    lines = [scale_crop]

    for seg in caption_segments:
        text  = escape_ffmpeg_text(seg["text"].upper())
        start = seg["start"]
        end   = seg["end"]

        # Single-quoted text (apostrophes already stripped, safe on Windows subprocess)
        # x centres the text; fix_bounds=1 clamps to frame if any word still runs wide
        drawtext = (
            f"drawtext={font_option}"
            f"text='{text}':"
            f"fontsize={CAPTION_FONT_SIZE}:"
            f"fontcolor={CAPTION_FONT_COLOR}:"
            f"borderw={CAPTION_STROKE_WIDTH}:"
            f"bordercolor={CAPTION_STROKE_COLOR}:"
            f"x=(w-text_w)/2:"
            f"y={y_pos}:"
            f"fix_bounds=1:"
            f"enable='between(t,{start:.3f},{end:.3f})'"
        )
        lines.append(drawtext)

    # ── Retention overlay 1: the HOOK headline card (first ~3.6s) ──────────────
    # A bold, boxed title at the top third that states the most shocking fact while
    # the narrator says it — this is the single biggest lever on 3-second retention.
    if hook_text:
        hook = _wrap_ffmpeg_text(escape_ffmpeg_text(hook_text.upper()), 16)
        lines.append(
            f"drawtext={font_option}"
            f"text='{hook}':"
            f"fontsize=74:"
            f"fontcolor=white:"
            f"borderw=6:bordercolor=black:"
            f"box=1:boxcolor=black@0.55:boxborderw=30:"
            f"line_spacing=12:"
            f"x=(w-text_w)/2:"
            f"y=h*0.15:"
            f"enable='between(t,0,3.6)'"
        )

    # ── Retention overlay 2: FOLLOW call-to-action (final ~2.6s) ───────────────
    if cta_text and total_duration:
        cta = escape_ffmpeg_text(cta_text.upper())
        cta_start = max(0.0, float(total_duration) - 2.6)
        lines.append(
            f"drawtext={font_option}"
            f"text='{cta}':"
            f"fontsize=64:"
            f"fontcolor=white:"
            f"borderw=5:bordercolor=black:"
            f"box=1:boxcolor=red@0.65:boxborderw=26:"
            f"x=(w-text_w)/2:"
            f"y=h*0.70:"
            f"enable='between(t,{cta_start:.3f},{float(total_duration):.3f})'"
        )

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

    # Convert all paths to absolute + forward slashes (FFmpeg needs this on Windows)
    def absfwd(p): return os.path.abspath(p).replace("\\", "/")

    abs_gameplay  = absfwd(gameplay_path)
    abs_audio     = absfwd(audio_path)
    abs_output    = absfwd(output_path)

    # ── Background input: loop short clips so the video NEVER freezes ──────────
    # If the background clip is long enough we seek to a random start for variety.
    # If it is shorter than the narration (common for atmospheric Pexels clips),
    # we loop it from the start so it fills the whole video instead of freezing on
    # its last frame while the audio keeps playing.
    try:
        clip_dur = get_video_duration(gameplay_path)
    except Exception:
        clip_dur = 0.0
    needed = audio_duration + 1.0
    if clip_dur >= needed:
        start_time = get_random_start_time(gameplay_path, audio_duration)
        bg_input = ["-ss", str(start_time), "-i", abs_gameplay]
        print(f"[Video] Background: {clip_dur:.1f}s clip, seek to {start_time:.1f}s")
    else:
        bg_input = ["-stream_loop", "-1", "-i", abs_gameplay]
        print(f"[Video] Background: {clip_dur:.1f}s clip (< {needed:.1f}s) — looping to fill")

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
            *bg_input,                                # background (looped if short)
            "-i", abs_audio,
            "-stream_loop", "-1", "-i", abs_music,    # loop music to fill video length
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
            *bg_input,                                # background (looped if short)
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

    # ── Freeze guard: the video stream MUST cover the audio (no frozen last frame) ──
    vdur, adur = get_stream_durations(output_path)
    print(f"[Video] Stream check — video {vdur:.1f}s vs audio {adur:.1f}s")
    if adur > 0 and vdur > 0 and (adur - vdur) > 1.5:
        raise RuntimeError(
            f"Background froze: video stream is {vdur:.1f}s but audio is {adur:.1f}s "
            f"({adur - vdur:.1f}s of frozen frame). Refusing to output a frozen video."
        )

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f"[Video] Done! {output_path}")
    print(f"[Video] File size: {size_mb:.1f} MB")

    return output_path
