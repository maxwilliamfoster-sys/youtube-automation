"""
TTS Generator — converts story text to speech.

Engines (in priority order):
  1. Kokoro-82M  — natural, non-robotic, runs on CPU, completely free
                   Models auto-downloaded from HuggingFace on first run (~300MB, cached)
  2. edge-tts    — fallback if Kokoro fails for any reason

Word timing always extracted via faster-whisper for accurate caption sync.
"""

import asyncio
import os
import json
import shutil
import subprocess
import edge_tts
from config import TTS_VOICE, TTS_RATE, TTS_PITCH, AUDIO_DIR

_FFMPEG = shutil.which("ffmpeg") or r"C:\ffmpeg\ffmpeg-8.1.1-essentials_build\bin\ffmpeg.exe"

# Kokoro voices: b = British, a = American, m = male, f = female
KOKORO_VOICE  = "bm_george"   # British male — deep, natural, perfect for horror
KOKORO_SPEED  = 0.88          # Slightly slower = more dread


# ─── Kokoro ONNX engine ───────────────────────────────────────────────────────

def _get_kokoro_model_paths():
    """Download Kokoro-82M from HuggingFace (cached in ~/.cache/huggingface)."""
    from huggingface_hub import hf_hub_download

    # Try both filename conventions used across different kokoro-onnx versions
    for model_name, voices_name in [
        ("kokoro-v1_0.onnx", "voices-v1_0.bin"),
        ("kokoro-v1.0.onnx", "voices-v1.0.bin"),
    ]:
        try:
            model_path  = hf_hub_download("hexgrad/Kokoro-82M", model_name)
            voices_path = hf_hub_download("hexgrad/Kokoro-82M", voices_name)
            return model_path, voices_path
        except Exception:
            continue

    raise RuntimeError("Could not download Kokoro model files from HuggingFace")


def _generate_audio_kokoro(text: str, output_path: str,
                            voice: str = KOKORO_VOICE,
                            speed: float = KOKORO_SPEED) -> None:
    """Generate audio with Kokoro-82M ONNX — sounds human, not robotic."""
    import numpy as np
    import soundfile as sf
    from kokoro_onnx import Kokoro

    lang = "en-gb" if voice.startswith("b") else "en-us"

    print(f"[TTS] Kokoro-82M — voice: {voice}, speed: {speed} (downloading model if needed...)")
    model_path, voices_path = _get_kokoro_model_paths()
    k = Kokoro(model_path, voices_path)

    # Split into sentences — Kokoro quality is better on shorter chunks
    import re
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text.strip()) if s.strip()]

    all_samples = []
    sample_rate = 24000
    for sentence in sentences:
        samples, sample_rate = k.create(sentence, voice=voice, speed=speed, lang=lang)
        all_samples.append(samples)
        # Brief natural pause between sentences
        all_samples.append(np.zeros(int(sample_rate * 0.06), dtype=samples.dtype))

    combined = np.concatenate(all_samples)

    # Write as WAV then convert to MP3 via FFmpeg
    wav_path = output_path.replace(".mp3", "_tmp.wav")
    sf.write(wav_path, combined, sample_rate)
    subprocess.run(
        [_FFMPEG, "-y", "-i", wav_path, "-b:a", "192k", output_path],
        capture_output=True, check=True,
    )
    os.remove(wav_path)
    print(f"[TTS] Kokoro audio saved: {output_path}")


# ─── edge-tts fallback ────────────────────────────────────────────────────────

async def _generate_audio_edge(text: str, output_path: str,
                                voice: str, rate: str, pitch: str) -> None:
    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    chunks = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            chunks.append(chunk["data"])
    with open(output_path, "wb") as f:
        for c in chunks:
            f.write(c)


# ─── Whisper word timing ──────────────────────────────────────────────────────

def _transcribe_for_timing(audio_path: str) -> list:
    """Use faster-whisper to get accurate word-level timestamps."""
    from faster_whisper import WhisperModel

    print("[TTS] Transcribing for word timing (Whisper tiny)...")
    model = WhisperModel("tiny", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(
        audio_path,
        word_timestamps=True,
        language="en",
        vad_filter=True,
    )

    word_timings = []
    for segment in segments:
        if segment.words:
            for word in segment.words:
                word_timings.append({
                    "word":     word.word.strip(),
                    "start":    round(word.start, 3),
                    "end":      round(word.end,   3),
                    "duration": round(word.end - word.start, 3),
                })

    print(f"[TTS] {len(word_timings)} words timed accurately")
    return word_timings


# ─── Public interface ─────────────────────────────────────────────────────────

def generate_tts(
    text: str,
    filename_prefix: str = "story",
    voice: str = None,
    rate: str = None,
    pitch: str = None,
) -> dict:
    """
    Generate TTS audio + word-level timing.
    Tries Kokoro first (natural voice), falls back to edge-tts.

    Returns dict with: audio_path, timings_path, word_timings, duration
    """
    os.makedirs(AUDIO_DIR, exist_ok=True)
    audio_path   = os.path.join(AUDIO_DIR, f"{filename_prefix}.mp3")
    timings_path = os.path.join(AUDIO_DIR, f"{filename_prefix}_timings.json")

    # --- Try Kokoro first ---
    kokoro_ok = False
    try:
        _generate_audio_kokoro(text, audio_path)
        kokoro_ok = True
    except Exception as e:
        print(f"[TTS] Kokoro unavailable ({e}) — falling back to edge-tts")

    # --- edge-tts fallback ---
    if not kokoro_ok:
        _voice = voice or TTS_VOICE
        _rate  = rate  or TTS_RATE
        _pitch = pitch or TTS_PITCH
        print(f"[TTS] edge-tts — voice: {_voice}")
        asyncio.run(_generate_audio_edge(text, audio_path, _voice, _rate, _pitch))

    word_timings = _transcribe_for_timing(audio_path)
    duration = word_timings[-1]["end"] + 0.5 if word_timings else 30.0

    with open(timings_path, "w", encoding="utf-8") as f:
        json.dump(word_timings, f, indent=2)

    print(f"[TTS] Duration: {duration:.1f}s | Words: {len(word_timings)}")
    return {
        "audio_path":   audio_path,
        "timings_path": timings_path,
        "word_timings": word_timings,
        "duration":     duration,
    }
