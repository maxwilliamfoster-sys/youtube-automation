"""
2026-11-04: loudnorm resamples to 192 kHz internally and, with no output rate set,
FFmpeg's AAC encoder settled on 96 kHz. media_qc warned on the 23 Oct smoke render
(96000 Hz). YouTube recommends 48 kHz, so every step that writes audio must set it.
These run real FFmpeg, because the fault only shows in what the encoder writes.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import documentary_composer as dc  # noqa: E402
from video_composer import FFPROBE  # noqa: E402


def sample_rate(path):
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=sample_rate", "-of", "json", path],
        capture_output=True, text=True, check=True).stdout
    return int(json.loads(out)["streams"][0]["sample_rate"])


class AudioRateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # 24 kHz is what the narrator's TTS writes, so the mux test below also covers
        # the fallback where the raw narration reaches the final step unmastered.
        self.voice = os.path.join(self.tmp.name, "voice.wav")
        subprocess.run([dc.FFMPEG, "-y", "-f", "lavfi", "-i",
                        "sine=frequency=220:duration=3:sample_rate=24000", self.voice],
                       capture_output=True, check=True)

    def test_music_mix_is_48k(self):
        out = dc.mix_background_music(self.voice, 3.0, self.tmp.name)
        self.assertTrue(out.endswith("mixed_audio.aac"), out)
        self.assertEqual(sample_rate(out), 48000)

    def test_narration_only_master_is_48k(self):
        out = dc._master_narration_only(self.voice, 3.0, self.tmp.name)
        self.assertTrue(out.endswith("narration_master.aac"), out)
        self.assertEqual(sample_rate(out), 48000)

    def test_ambient_pad_fallback_mix_is_48k(self):
        with mock.patch.object(dc, "DOC_MUSIC_DIR", os.path.join(self.tmp.name, "none")):
            out = dc.mix_background_music(self.voice, 3.0, self.tmp.name)
        self.assertTrue(out.endswith("mixed_audio.aac"), out)
        self.assertEqual(sample_rate(out), 48000)

    def test_final_video_is_48k_even_from_raw_narration(self):
        bg = os.path.join(self.tmp.name, "bg.mp4")
        subprocess.run([dc.FFMPEG, "-y", "-f", "lavfi", "-i",
                        f"color=c=gray:s=360x640:r={dc.VIDEO_FPS}:d=4", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", bg], capture_output=True, check=True)
        out = os.path.join(self.tmp.name, "final.mp4")
        dc.add_audio_and_captions(bg, self.voice, [], 3.0, out)
        self.assertEqual(sample_rate(out), 48000)


if __name__ == "__main__":
    unittest.main()
