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
import re
import json
import shutil
import subprocess
import edge_tts
from config import TTS_VOICE, TTS_RATE, TTS_PITCH, AUDIO_DIR, KOKORO_MODEL_DIR

_FFMPEG = shutil.which("ffmpeg") or r"C:\ffmpeg\ffmpeg-8.1.1-essentials_build\bin\ffmpeg.exe"

# Kokoro voices: b = British, a = American, m = male, f = female
KOKORO_VOICE  = "af_nicole"   # American female — soft, breathy, naturally unsettling for horror
KOKORO_SPEED  = 0.82          # Slower = more dread and emotional weight


# ─── Text normalisation ───────────────────────────────────────────────────────

_ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight",
         "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
         "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty",
         "sixty", "seventy", "eighty", "ninety"]
_ORDINALS = {
    1:"first", 2:"second", 3:"third", 4:"fourth", 5:"fifth",
    6:"sixth", 7:"seventh", 8:"eighth", 9:"ninth", 10:"tenth",
    11:"eleventh", 12:"twelfth", 13:"thirteenth", 14:"fourteenth",
    15:"fifteenth", 16:"sixteenth", 17:"seventeenth", 18:"eighteenth",
    19:"nineteenth", 20:"twentieth", 21:"twenty-first", 22:"twenty-second",
    23:"twenty-third", 24:"twenty-fourth", 25:"twenty-fifth", 26:"twenty-sixth",
    27:"twenty-seventh", 28:"twenty-eighth", 29:"twenty-ninth", 30:"thirtieth",
    31:"thirty-first",
}


def _two_digit(n: int) -> str:
    if n < 20:
        return _ONES[n]
    t, o = divmod(n, 10)
    return _TENS[t] + ("-" + _ONES[o] if o else "")


def _year_to_words(y: int) -> str:
    """Convert a 4-digit year to how it's spoken in English."""
    if 1100 <= y <= 1999:
        hi, lo = divmod(y, 100)
        hi_w = _ONES[hi] if hi < 20 else _TENS[hi // 10] + ("-" + _ONES[hi % 10] if hi % 10 else "")
        if lo == 0:
            return f"{hi_w} hundred"
        if lo < 10:
            return f"{hi_w} oh {_ONES[lo]}"
        return f"{hi_w} {_two_digit(lo)}"
    if 2000 <= y <= 2009:
        return "two thousand" + (f" and {_ONES[y % 10]}" if y % 10 else "")
    if 2010 <= y <= 2099:
        return f"twenty {_two_digit(y % 100)}"
    return str(y)


def normalize_for_tts(text: str) -> str:
    """
    Convert years, ordinals, times, and common abbreviations to spoken form
    so TTS engines pronounce them correctly.
    """
    # Ordinals: 1st, 2nd, 3rd, 4th … 31st
    def _ord(m):
        n = int(m.group(1))
        return _ORDINALS.get(n, m.group(0))
    text = re.sub(r'\b(\d{1,2})(st|nd|rd|th)\b', _ord, text, flags=re.IGNORECASE)

    # Years in date context: "April 18, 2016" or standalone 4-digit years
    text = re.sub(r'\b(1[0-9]{3}|20[0-9]{2})\b', lambda m: _year_to_words(int(m.group(0))), text)

    # Times: "4 am" / "2 pm" → "four AM" / "two PM"
    def _time(m):
        h = int(m.group(1))
        period = m.group(2).upper()
        return f"{_ONES[h] if h <= 19 else _two_digit(h)} {period}"
    text = re.sub(r'\b(\d{1,2})\s*(am|pm)\b', _time, text, flags=re.IGNORECASE)

    # "62 miles" / "74 years" — keep as-is (spoken naturally as cardinals)
    return text


# ─── Kokoro engine (PyTorch KPipeline — replaces old ONNX approach) ──────────

def _generate_audio_kokoro(text: str, output_path: str,
                            voice: str = KOKORO_VOICE,
                            speed: float = KOKORO_SPEED) -> None:
    """
    Generate audio with Kokoro-82M via KPipeline.
    Models auto-download from HuggingFace on first run (~300 MB, then cached).
    af_nicole — soft, breathy American female, naturally unsettling for horror.
    """
    import numpy as np
    import soundfile as sf
    from kokoro import KPipeline

    # 'a' = American English, 'b' = British English
    lang_code = "b" if voice.startswith("b") else "a"
    print(f"[TTS] Kokoro KPipeline — voice: {voice}, speed: {speed}, lang: {lang_code}")

    pipeline = KPipeline(lang_code=lang_code)

    all_audio = []
    for _, _, audio in pipeline(text, voice=voice, speed=speed):
        if audio is not None and len(audio) > 0:
            all_audio.append(audio)

    if not all_audio:
        raise RuntimeError("Kokoro produced no audio output")

    combined = np.concatenate(all_audio)

    # Write WAV then encode to MP3 via FFmpeg
    wav_path = output_path.replace(".mp3", "_tmp.wav")
    sf.write(wav_path, combined, 24000)
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
    speed: float = None,
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

    text = normalize_for_tts(text)

    # --- Try Kokoro first ---
    kokoro_ok = False
    try:
        _kokoro_voice = voice if voice and not voice.startswith("en-") else KOKORO_VOICE
        _kokoro_speed = speed if speed is not None else KOKORO_SPEED
        _generate_audio_kokoro(text, audio_path, voice=_kokoro_voice, speed=_kokoro_speed)
        kokoro_ok = True
    except Exception as e:
        print(f"[TTS] Kokoro unavailable ({e}) — falling back to edge-tts")

    # --- edge-tts fallback ---
    if not kokoro_ok:
        # Use TTS_VOICE default — never pass a Kokoro voice name to edge-tts
        _voice = TTS_VOICE if (not voice or not voice.startswith("en-")) else voice
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
