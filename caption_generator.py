"""
Caption Generator — creates word-timed captions from TTS word boundary data.
Groups words into short bursts (like TikTok/YouTube Shorts style captions).
Falls back to faster-whisper transcription if word timing data is missing.
"""

import json
import os
from typing import List, Dict
from config import WORDS_PER_CAPTION


def build_caption_segments(word_timings: List[Dict], words_per_segment: int = WORDS_PER_CAPTION) -> List[Dict]:
    """
    Group word timings into caption segments.

    Args:
        word_timings: List of {word, start, end, duration} dicts from edge-tts
        words_per_segment: How many words to show at once

    Returns:
        List of {text, start, end} caption segments
    """
    segments = []
    i = 0

    while i < len(word_timings):
        chunk = word_timings[i : i + words_per_segment]
        text  = " ".join(w["word"] for w in chunk)
        start = chunk[0]["start"]
        end   = chunk[-1]["end"]

        # Clean up text (remove leading/trailing punctuation issues)
        text = text.strip()

        segments.append({
            "text":  text,
            "start": start,
            "end":   end,
        })
        i += words_per_segment

    return segments


def load_captions_from_timings_file(timings_path: str) -> List[Dict]:
    """Load captions from a saved word timings JSON file."""
    with open(timings_path, "r", encoding="utf-8") as f:
        word_timings = json.load(f)
    return build_caption_segments(word_timings)


def transcribe_audio_fallback(audio_path: str) -> List[Dict]:
    """
    Fallback: use faster-whisper to transcribe audio and get word timings.
    Used if edge-tts word boundary data is unavailable.
    """
    print("[Captions] Using faster-whisper fallback transcription...")

    from faster_whisper import WhisperModel

    # Use tiny model for speed (still very accurate for TTS audio)
    model = WhisperModel("tiny", device="cpu", compute_type="int8")

    segments_raw, info = model.transcribe(
        audio_path,
        word_timestamps=True,
        language="en",
    )

    word_timings = []
    for segment in segments_raw:
        if segment.words:
            for word in segment.words:
                word_timings.append({
                    "word":     word.word.strip(),
                    "start":    word.start,
                    "end":      word.end,
                    "duration": word.end - word.start,
                })

    print(f"[Captions] Transcribed {len(word_timings)} words via Whisper")
    return build_caption_segments(word_timings)


def save_srt(segments: List[Dict], output_path: str) -> None:
    """Save captions as SRT subtitle file (for reference/debugging)."""

    def format_time(seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int((seconds % 1) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    with open(output_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            f.write(f"{i}\n")
            f.write(f"{format_time(seg['start'])} --> {format_time(seg['end'])}\n")
            f.write(f"{seg['text']}\n\n")

    print(f"[Captions] SRT saved: {output_path}")


def get_captions(tts_result: Dict, audio_dir: str, prefix: str = "story") -> List[Dict]:
    """
    Main function: get captions from TTS result dict.

    Args:
        tts_result: Dict from tts_generator.generate_tts()
        audio_dir:  Directory to save caption files
        prefix:     File prefix

    Returns:
        List of caption segment dicts {text, start, end}
    """
    segments = []

    # Prefer edge-tts word boundary data (most accurate)
    if tts_result.get("word_timings") and len(tts_result["word_timings"]) > 0:
        print("[Captions] Building captions from Whisper word timestamps...")
        segments = build_caption_segments(tts_result["word_timings"])
    elif tts_result.get("timings_path") and os.path.exists(tts_result["timings_path"]):
        segments = load_captions_from_timings_file(tts_result["timings_path"])
    else:
        # Fallback to Whisper
        segments = transcribe_audio_fallback(tts_result["audio_path"])

    print(f"[Captions] {len(segments)} caption segments created")

    # Save SRT for debugging
    srt_path = os.path.join(audio_dir, f"{prefix}.srt")
    save_srt(segments, srt_path)

    return segments


if __name__ == "__main__":
    # Test with sample data
    sample_timings = [
        {"word": "Something", "start": 0.0,  "end": 0.4,  "duration": 0.4},
        {"word": "was",       "start": 0.4,  "end": 0.6,  "duration": 0.2},
        {"word": "wrong",     "start": 0.6,  "end": 0.9,  "duration": 0.3},
        {"word": "with",      "start": 0.9,  "end": 1.1,  "duration": 0.2},
        {"word": "my",        "start": 1.1,  "end": 1.3,  "duration": 0.2},
        {"word": "reflection","start": 1.3,  "end": 1.9,  "duration": 0.6},
        {"word": "It",        "start": 2.1,  "end": 2.3,  "duration": 0.2},
        {"word": "smiled",    "start": 2.3,  "end": 2.7,  "duration": 0.4},
        {"word": "back",      "start": 2.7,  "end": 2.95, "duration": 0.25},
    ]

    segments = build_caption_segments(sample_timings, words_per_segment=4)
    for seg in segments:
        print(f"  [{seg['start']:.2f}s - {seg['end']:.2f}s] {seg['text']}")
