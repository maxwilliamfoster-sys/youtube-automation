"""
Documentary Composer — cinematic Ken Burns animation from AI-generated images.
Replaces gameplay footage with atmospheric still images + slow zoom/pan motion.
Output: 1080x1920, 30fps, with crossfade transitions between scenes.
"""

import glob
import os
import random as _random
import shutil
import subprocess
import threading
from datetime import datetime
from typing import List, Dict, Optional
from PIL import Image

from video_composer import FFMPEG, escape_ffmpeg_text, build_filter_script, get_video_duration
from config import (
    VIDEO_WIDTH, VIDEO_HEIGHT, VIDEO_FPS,
    CAPTION_FONT_SIZE, CAPTION_FONT_COLOR,
    CAPTION_STROKE_COLOR, CAPTION_STROKE_WIDTH,
    CAPTION_POSITION, OUTPUT_DIR,
    MUSIC_DIR, MUSIC_VOLUME, MUSIC_ENABLED,
)


# ─── Ken Burns motion styles ──────────────────────────────────────────────────
# Each tuple: (zoom_start, zoom_end, cx_start, cy_start, cx_end, cy_end, label)
# cx/cy are 0.0–1.0 fractions of the image width/height (centre of visible window).

# Motion is deliberately stronger than a classic documentary pan. These clips are now
# ~3-5s rather than ~9s, and the old 1.00->1.25 drift was so slow over a long hold that
# the video read as a still slideshow. Wider zoom ranges and lateral drifts give each
# cut visible movement. Keep end zoom <= ~1.45: beyond that, upscaling gets soft.
KEN_BURNS_STYLES = [
    (1.00, 1.38, 0.50, 0.50, 0.50, 0.50, "push-in center"),
    (1.38, 1.00, 0.50, 0.50, 0.50, 0.50, "pull-back center"),
    (1.05, 1.42, 0.85, 0.15, 0.70, 0.30, "push-in top-right"),
    (1.05, 1.42, 0.15, 0.85, 0.30, 0.70, "push-in bottom-left"),
    (1.40, 1.05, 0.50, 0.12, 0.50, 0.30, "pull-back upper"),
    (1.05, 1.40, 0.15, 0.15, 0.30, 0.30, "push-in top-left"),
    # Pure lateral pans. The crop window is clamped to the image, so travel is capped
    # at (1 - 1/zoom) of the width — at zoom 1.20 that is only ±8%, barely visible.
    # 1.32 buys a ~24% sweep, which actually reads as a camera move.
    (1.32, 1.32, 0.00, 0.50, 1.00, 0.50, "drift right"),
    (1.32, 1.32, 1.00, 0.50, 0.00, 0.50, "drift left"),
    (1.10, 1.35, 0.50, 0.80, 0.50, 0.25, "rise up"),
    (1.35, 1.10, 0.50, 0.25, 0.50, 0.75, "sink down"),
]


# ─── Ken Burns single clip (PIL — sub-pixel accurate, zero jitter) ────────────

def make_ken_burns_clip(
    image_path: str,
    duration: float,
    style_idx: int,
    output_path: str,
) -> str:
    """
    Apply Ken Burns motion to a still image using PIL affine transforms.

    PIL renders each frame with sub-pixel accuracy (no integer-rounding jitter).
    Frames are piped directly to FFmpeg for H.264 encoding.
    """
    total_frames = max(1, int(round(duration * VIDEO_FPS)))
    out_w, out_h = VIDEO_WIDTH, VIDEO_HEIGHT
    zs, ze, cxs, cys, cxe, cye, label = KEN_BURNS_STYLES[style_idx % len(KEN_BURNS_STYLES)]

    print(f"[DocCompose] Ken Burns ({label}) -- {os.path.basename(image_path)} ({duration:.1f}s)...")

    img = Image.open(image_path).convert("RGB")
    iw, ih = img.size

    # Crop source to 9:16 aspect ratio
    target_ar = out_w / out_h
    if iw / ih > target_ar:
        nw = int(ih * target_ar)
        img = img.crop(((iw - nw) // 2, 0, (iw - nw) // 2 + nw, ih))
    elif iw / ih < target_ar:
        nh = int(iw / target_ar)
        img = img.crop((0, (ih - nh) // 2, iw, (ih - nh) // 2 + nh))
    iw, ih = img.size

    # Ensure source is large enough for the maximum zoom level
    min_dim = max(out_w, out_h) * max(zs, ze) * 1.05
    if max(iw, ih) < min_dim:
        scale = min_dim / max(iw, ih)
        img = img.resize((int(iw * scale), int(ih * scale)), Image.LANCZOS)
        iw, ih = img.size

    # Pipe raw RGB frames into FFmpeg for encoding
    cmd = [
        FFMPEG, "-y",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-s", f"{out_w}x{out_h}", "-pix_fmt", "rgb24",
        "-r", str(VIDEO_FPS), "-i", "pipe:0",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", str(VIDEO_FPS),
        os.path.abspath(output_path).replace("\\", "/"),
    ]
    # Read stderr in a background thread to avoid deadlock when pipe buffer fills
    stderr_chunks: list = []

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    def _read_stderr():
        try:
            while True:
                chunk = proc.stderr.read(4096)
                if not chunk:
                    break
                stderr_chunks.append(chunk)
        except Exception:
            pass

    stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
    stderr_thread.start()

    try:
        for n in range(total_frames):
            t = n / max(1, total_frames - 1)

            zoom = zs + (ze - zs) * t
            cx = (cxs + (cxe - cxs) * t) * iw
            cy = (cys + (cye - cys) * t) * ih
            cw = iw / zoom
            ch = ih / zoom

            # Clamp so the crop window never goes outside the image
            cx = max(cw / 2, min(cx, iw - cw / 2))
            cy = max(ch / 2, min(cy, ih - ch / 2))
            tx = cx - cw / 2
            ty = cy - ch / 2

            # PIL affine: output pixel (x,y) maps to source pixel (sx*x+tx, sy*y+ty)
            sx = cw / out_w
            sy = ch / out_h
            frame = img.transform(
                (out_w, out_h), Image.AFFINE,
                (sx, 0, tx, 0, sy, ty),
                Image.BICUBIC,
            )
            try:
                proc.stdin.write(frame.tobytes())
            except (BrokenPipeError, OSError):
                break   # FFmpeg exited early; let the finally block clean up

    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        stderr_thread.join(timeout=30)
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    stderr = b"".join(stderr_chunks)
    if proc.returncode != 0:
        print(f"[DocCompose] Ken Burns error:\n{stderr[-2000:].decode(errors='replace')}")
        raise RuntimeError(f"Ken Burns failed for {image_path}")

    return output_path


# ─── Crossfade chain ──────────────────────────────────────────────────────────

def crossfade_clips(
    clip_paths: List[str],
    clip_durations,          # float (equal) or List[float] (per-clip)
    fade_duration: float,
    output_path: str,
) -> str:
    """
    Chain multiple Ken Burns clips with smooth crossfade transitions.
    clip_durations can be a single float (all clips equal) or a list.
    """
    n = len(clip_paths)
    if n == 0:
        raise ValueError("No clips provided")
    if n == 1:
        shutil.copy(clip_paths[0], output_path)
        return output_path

    # Normalise to a list
    if isinstance(clip_durations, (int, float)):
        clip_durations = [float(clip_durations)] * n

    print(f"[DocCompose] Crossfading {n} clips ({fade_duration}s fade)...")

    inputs = []
    for p in clip_paths:
        inputs += ["-i", os.path.abspath(p).replace("\\", "/")]

    # Build xfade filter chain.
    # offset_i = time in the merged timeline where transition i begins
    #           = sum(clip_durations[0..i-1]) - i * fade_duration
    filter_parts = []
    last_label = "[0:v]"
    cumulative_offset = 0.0
    for i in range(1, n):
        out_label = f"[x{i}]" if i < n - 1 else "[vout]"
        cumulative_offset += clip_durations[i - 1] - fade_duration
        offset = round(cumulative_offset, 4)
        filter_parts.append(
            f"{last_label}[{i}:v]xfade=transition=fade"
            f":duration={fade_duration:.2f}:offset={offset:.4f}{out_label}"
        )
        last_label = f"[x{i}]"

    cmd = (
        [FFMPEG, "-y"]
        + inputs
        + [
            "-filter_complex", ";".join(filter_parts),
            "-map", "[vout]",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-r", str(VIDEO_FPS),
            os.path.abspath(output_path).replace("\\", "/"),
        ]
    )

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[DocCompose] Crossfade error:\n{result.stderr[-2000:]}")
        raise RuntimeError("Crossfade composition failed")

    return output_path


# ─── Word-timed scene duration calculator ────────────────────────────────────

def _segment_durations(
    caption_segments: List[Dict],
    word_segments: List[str],
    audio_duration: float,
    fade_duration: float,
) -> List[float]:
    """
    Calculate how long each image scene should display, derived from the
    fraction of script words it covers mapped onto the caption timeline.

    Returns a list of scene durations that sum to audio_duration.
    """
    n = len(word_segments)
    if not caption_segments or n <= 1:
        return [audio_duration / n] * n

    # Word count per scene segment
    scene_word_counts = [len(seg.split()) for seg in word_segments]
    total_script_words = max(1, sum(scene_word_counts))

    # Build a flat word-time list from captions (one timestamp per spoken word)
    word_times: List[float] = []
    for cap in sorted(caption_segments, key=lambda s: s["start"]):
        words = cap["text"].split()
        nw = len(words)
        for i in range(nw):
            frac = i / max(1, nw - 1) if nw > 1 else 0.0
            word_times.append(cap["start"] + (cap["end"] - cap["start"]) * frac)

    total_timed = len(word_times)
    if total_timed == 0:
        return [audio_duration / n] * n

    # Scene boundary timestamps based on cumulative word-count fractions
    scene_starts = [0.0]
    cumulative = 0
    for i in range(n - 1):
        cumulative += scene_word_counts[i]
        frac = cumulative / total_script_words
        idx = min(int(frac * total_timed), total_timed - 1)
        scene_starts.append(word_times[idx])
    scene_starts.append(audio_duration)

    # Raw durations, enforce a minimum so short clips don't break xfade
    min_dur = fade_duration + 1.5
    raw = [max(scene_starts[i + 1] - scene_starts[i], min_dur) for i in range(n)]

    # Rescale so durations sum exactly to audio_duration
    total = sum(raw)
    durations = [d * audio_duration / total for d in raw]

    # Retention safety net: cap any single scene at ~1.8x the average so one image
    # can never sit nearly-static for half the video, then redistribute the excess
    # to the scenes that have room. Capacity (n * cap) always exceeds audio_duration.
    avg = audio_duration / n
    cap = max(min_dur, avg * 1.8)
    for _ in range(6):
        excess = sum(max(0.0, d - cap) for d in durations)
        if excess < 0.05:
            break
        durations = [min(d, cap) for d in durations]
        room = [cap - d for d in durations]
        total_room = sum(room) or 1.0
        durations = [d + excess * (r / total_room) for d, r in zip(durations, room)]
    return durations


# ─── Background music mixer ──────────────────────────────────────────────────

def mix_background_music(narration_path: str, duration: float, work_dir: str) -> str:
    """
    Mix a quiet procedural drone track under the narration audio.

    Picks a random drone_*.wav from music/ and blends it at MUSIC_VOLUME
    (default 12% — subtle but present).  Returns path to the mixed AAC file.
    Falls back silently to the original narration if no tracks exist.
    """
    tracks = (sorted(glob.glob(os.path.join(MUSIC_DIR, "track_*.mp3")))
              or sorted(glob.glob(os.path.join(MUSIC_DIR, "drone_*.wav"))))
    if not tracks:
        print("[DocCompose] No music tracks found in music/ — run: py setup_music.py")
        return narration_path

    drone_path = _random.choice(tracks)
    mixed_path = os.path.join(work_dir, "mixed_audio.aac")

    print(f"[DocCompose] Music: {os.path.basename(drone_path)} @ {MUSIC_VOLUME * 100:.0f}% vol")

    cmd = [
        FFMPEG, "-y",
        "-i",             os.path.abspath(narration_path).replace("\\", "/"),
        "-stream_loop",   "-1",
        "-i",             os.path.abspath(drone_path).replace("\\", "/"),
        "-filter_complex",
        f"[1:a]volume={MUSIC_VOLUME}[bg];[0:a][bg]amix=inputs=2:duration=first[aout]",
        "-map",   "[aout]",
        "-t",     str(round(duration + 1.5, 2)),
        "-c:a",   "aac",
        "-b:a",   "192k",
        mixed_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[DocCompose] Music mix skipped (FFmpeg error): {result.stderr[-400:]}")
        return narration_path

    return mixed_path


# ─── Audio + caption overlay ──────────────────────────────────────────────────

def add_audio_and_captions(
    bg_video: str,
    audio_path: str,
    caption_segments: List[Dict],
    audio_duration: float,
    output_path: str,
    hook_text: str = None,
    cta_text: str = None,
) -> str:
    """Burn captions (plus optional hook + follow CTA overlays) and mix audio
    onto the background documentary video."""

    scale_pass = f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT},setsar=1"
    vf_filter = build_filter_script(
        caption_segments, scale_pass,
        hook_text=hook_text, cta_text=cta_text, total_duration=audio_duration,
    )

    cmd = [
        FFMPEG, "-y",
        "-i", os.path.abspath(bg_video).replace("\\", "/"),
        "-i", os.path.abspath(audio_path).replace("\\", "/"),
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
        os.path.abspath(output_path).replace("\\", "/"),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[DocCompose] Caption/audio error:\n{result.stderr[-2000:]}")
        raise RuntimeError("Audio + caption overlay failed")

    return output_path


# ─── Main composer ────────────────────────────────────────────────────────────

def compose_documentary(
    image_paths: List[str],
    audio_path: str,
    caption_segments: List[Dict],
    title: str = "true_crime",
    audio_duration: float = None,
    work_dir: str = None,
    # 0.5s, not 1.0s: scenes are now ~3-5s, and a 1s crossfade spent a quarter of each
    # one dissolving, which blunted exactly the snappier cutting the extra scenes buy.
    fade_duration: float = 0.5,
    word_segments: Optional[List[str]] = None,
    hook_text: str = None,
    cta_text: str = "Follow for more unsolved cases",
) -> str:
    """
    Full documentary composition pipeline.

    Flow:
      AI images -> Ken Burns clips -> crossfade background -> audio + captions -> final MP4

    Args:
        image_paths:       List of atmospheric scene image paths
        audio_path:        TTS narration audio (.mp3)
        caption_segments:  Word-timed caption data from caption_generator
        title:             Video title (used for filename)
        audio_duration:    Duration of narration in seconds (auto-detected if None)
        work_dir:          Temp directory for intermediate clips (auto-cleaned)
        fade_duration:     Crossfade duration in seconds between scenes
        word_segments:     Script text for each image scene (enables word-timed
                           display so images match what is being said). If None,
                           falls back to equal-duration distribution.

    Returns:
        Path to the final composed MP4 file
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_title = "".join(c for c in title if c.isalnum() or c in " _-").replace(" ", "_")[:40]
    output_path = os.path.join(OUTPUT_DIR, f"{safe_title}_{timestamp}.mp4")

    if work_dir is None:
        work_dir = os.path.join(OUTPUT_DIR, f"_tmp_{timestamp}")
    os.makedirs(work_dir, exist_ok=True)

    if audio_duration is None:
        audio_duration = get_video_duration(audio_path)

    n = len(image_paths)
    print(f"\n[DocCompose] Building documentary: {n} scenes x {audio_duration:.1f}s total")

    # ── Calculate per-scene display durations ──────────────────────────────────
    if word_segments and len(word_segments) == n:
        print("[DocCompose] Using word-timed scene durations...")
        scene_durations = _segment_durations(
            caption_segments, word_segments, audio_duration, fade_duration
        )
        for i, d in enumerate(scene_durations):
            print(f"  Scene {i+1}: {d:.1f}s")
    else:
        base = audio_duration / n
        scene_durations = [base] * n
        print(f"[DocCompose] Equal scene durations: {base:.1f}s each")

    # Each clip is the scene duration + fade overlap so there are no gaps
    clip_durations = [round(d + fade_duration + 0.1, 2) for d in scene_durations]

    # ── Step 1: Ken Burns clips ────────────────────────────────────────────────
    print("\n[DocCompose] Step 1/4 -- Applying Ken Burns motion to scenes...")
    clip_paths = []
    for i, img_path in enumerate(image_paths):
        clip_path = os.path.join(work_dir, f"clip_{i:02d}.mp4")
        make_ken_burns_clip(img_path, clip_durations[i], i, clip_path)
        clip_paths.append(clip_path)

    # ── Step 2: Crossfade ──────────────────────────────────────────────────────
    print("\n[DocCompose] Step 2/4 -- Crossfading clips...")
    bg_path = os.path.join(work_dir, "background.mp4")
    crossfade_clips(clip_paths, clip_durations, fade_duration, bg_path)

    # ── Step 3: Background music ───────────────────────────────────────────────
    if MUSIC_ENABLED:
        print("\n[DocCompose] Step 3/4 -- Mixing background music...")
        final_audio = mix_background_music(audio_path, audio_duration, work_dir)
    else:
        print("\n[DocCompose] Step 3/4 -- Music disabled, skipping...")
        final_audio = audio_path

    # ── Step 4: Audio + captions (+ hook & follow CTA overlays) ─────────────────
    print("\n[DocCompose] Step 4/4 -- Adding narration, captions, hook & CTA...")
    add_audio_and_captions(
        bg_path, final_audio, caption_segments, audio_duration, output_path,
        hook_text=hook_text, cta_text=cta_text,
    )

    # ── Cleanup ────────────────────────────────────────────────────────────────
    try:
        shutil.rmtree(work_dir)
    except Exception:
        pass

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f"\n[DocCompose] Complete: {output_path} ({size_mb:.1f} MB)")
    return output_path
