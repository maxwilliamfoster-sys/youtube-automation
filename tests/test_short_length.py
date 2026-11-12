"""The length lever's 'short' arm (2026-11-12): a 55-65 word script and a seamless loop end."""
import inspect
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import documentary_composer as dc  # noqa: E402
import story_generator as sg  # noqa: E402
from video_composer import FFMPEG  # noqa: E402


class ShortScript(unittest.TestCase):
    def test_every_long_phrase_is_still_in_the_prompts(self):
        # if a prompt is reworded, the short arm would silently ask for the long length
        src = inspect.getsource(sg)
        for long_, _ in sg.SHORT_SUBS:
            self.assertIn(long_, src)
        self.assertIn(sg.SHORT_SUBS[0][0], sg.TRUE_CRIME_SCRIPT_PROMPT)

    def test_prompts_change_only_when_the_short_arm_is_on(self):
        self.assertEqual(sg._length_text(sg.TRUE_CRIME_SCRIPT_PROMPT), sg.TRUE_CRIME_SCRIPT_PROMPT)
        with mock.patch.object(sg, "SHORT_SCRIPT", True):
            short = sg._length_text(sg.TRUE_CRIME_SCRIPT_PROMPT)
        self.assertIn("55-65 words (~20-25 seconds spoken)", short)
        self.assertNotIn("85-100", short)

    def test_short_scripts_are_trimmed_to_65_words_keeping_the_closing_line(self):
        script = " ".join(f"Sentence number {i} has exactly seven words." for i in range(14)) + " Who did it?"
        self.assertEqual(len(sg._fit_length(script).split()), 87)        # 12 sentences + the question
        with mock.patch.object(sg, "SHORT_SCRIPT", True):
            out = sg._fit_length(script)
        self.assertLessEqual(len(out.split()), sg.SHORT_TRIM_WORDS)
        self.assertTrue(out.endswith("Who did it?"))

    def test_no_series_is_started_for_a_short_video(self):
        self.assertIn("None if SHORT_SCRIPT else _try_series(", inspect.getsource(sg))


class LoopEnd(unittest.TestCase):
    def test_filter_dissolves_the_last_half_second_into_frame_zero(self):
        f = dc.loop_filter(24.0)
        self.assertIn("trim=end_frame=1", f)
        self.assertIn("xfade=transition=fade:duration=0.5:offset=23.500", f)

    def test_off_by_default(self):
        self.assertFalse(dc.LOOP_END)

    def test_the_last_frame_matches_the_first(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "v.mp4")
            subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x568:rate=30:duration=3",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-shortest",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", path], check=True)
            dc.add_loop_end(path)
            self.assertAlmostEqual(dc.get_video_duration(path), 3.0, delta=0.15)

            def frame(args):
                return subprocess.run([FFMPEG, "-v", "error", *args, "-i", path, "-frames:v", "1", "-vf", "scale=32:56",
                                       "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
            first, last, mid = frame([]), frame(["-sseof", "-0.04"]), frame(["-ss", "1.5"])
            diff = lambda a, b: sum(abs(x - y) for x, y in zip(a, b)) / len(a)   # noqa: E731
            self.assertLess(diff(first, last), 4.0)
            self.assertGreater(diff(first, mid), 4.0)          # testsrc2 moves, so the check means something


if __name__ == "__main__":
    unittest.main()
