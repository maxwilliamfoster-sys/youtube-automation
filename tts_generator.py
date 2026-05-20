"""
TTS Generator — converts story text to speech using edge-tts (free).
After generating audio, uses faster-whisper to get accurate word-level
timestamps so captions stay perfectly in sync.
"""

import asyncio
import os
import json
import edge_tts
from config import TTS_VOICE, TTS_RATE, TTS_PITCH, AUDIO_DIR


async def _generate_audio(text: str, output_path: str) -> None:
    """Generate TTS audio and write to file."""
    communicate = edge_tts.Communicate(text, TTS_VOICE, rate=TTS_RATE, pitch=TTS_PITCH)
    chunks = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            chunks.append(chunk["data"])
    with open(output_path, "wb") as f:
        for c in chunks:
            f.write(c)


def _transcribe_for_timing(audio_path: str) -> list:
    """
    Use faster-whisper to get accurate word-level timestamps from the audio.
    This is much more accurate than estimating from sentence boundaries.
    Downloads the 'tiny' model (~75MB) on first run — cached after that.
    """
    from faster_whisper import WhisperModel

    print("[TTS] Transcribing audio for accurate word timing (first run downloads ~75MB)...")

    # tiny model: fast, accurate enough for TTS audio, runs on CPU
    model = WhisperModel("tiny", device="cpu", compute_type="int8")

    segments, _ = model.transcribe(
        audio_path,
        word_timestamps=True,
        language="en",
        vad_filter=True,      # skip silence gaps
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

    print(f"[TTS] Word timing: {len(word_timings)} words mapped accurately")
    return word_timings


def generate_tts(text: str, filename_prefix: str = "story") -> dict:
    """
    Generate TTS audio and get accurate word-level timing via Whisper.

    Returns:
        dict with 'audio_path', 'timings_path', 'word_timings', 'duration'
    """
    os.makedirs(AUDIO_DIR, exist_ok=True)

    audio_path   = os.path.join(AUDIO_DIR, f"{filename_prefix}.mp3")
    timings_path = os.path.join(AUDIO_DIR, f"{filename_prefix}_timings.json")

    # Step 1: Generate the audio
    print(f"[TTS] Generating voice ({TTS_VOICE})...")
    asyncio.run(_generate_audio(text, audio_path))
    print(f"[TTS] Audio saved: {audio_path}")

    # Step 2: Transcribe with Whisper for accurate word timing
    word_timings = _transcribe_for_timing(audio_path)

    # Step 3: Calculate total duration
    duration = word_timings[-1]["end"] + 0.5 if word_timings else 30.0

    # Step 4: Save timings
    with open(timings_path, "w", encoding="utf-8") as f:
        json.dump(word_timings, f, indent=2)

    print(f"[TTS] Duration: {duration:.1f}s | Words: {len(word_timings)}")

    return {
        "audio_path":   audio_path,
        "timings_path": timings_path,
        "word_timings": word_timings,
        "duration":     duration,
    }


if __name__ == "__main__":
    test_text = (
        "I should have never gone back to that house. "
        "The door was open, just like I left it. "
        "But the lights inside were on. I never turned them on. "
        "Something was waiting for me in the dark. "
        "I could feel it before I could see it."
    )
    result = generate_tts(test_text, "test_andrew")
    print("\nFirst 8 word timings:")
    for w in result["word_timings"][:8]:
        print(f"  '{w['word']}' {w['start']:.2f}s -> {w['end']:.2f}s")
