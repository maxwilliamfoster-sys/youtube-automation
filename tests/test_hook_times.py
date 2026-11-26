"""Clock times on burned-in cards (autopilot 2026-11-26).

The FFmpeg escaper turns every colon into a space, so the hook card "Her watch stopped at
11:54" showed "11 54" (watched 2026-11-23). A colon between digits now becomes a dot, the UK
way of writing times ("11.54"), which FFmpeg drawtext reads safely. Other colons still go.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from video_composer import escape_ffmpeg_text  # noqa: E402


class HookCardTimes(unittest.TestCase):
    def test_time_keeps_a_separator(self):
        self.assertEqual(escape_ffmpeg_text("Her watch stopped at 11:54."), "Her watch stopped at 11.54.")

    def test_short_hour_and_two_times(self):
        self.assertEqual(escape_ffmpeg_text("Used at 8:50 and 11:03"), "Used at 8.50 and 11.03")

    def test_other_colons_still_removed(self):
        out = escape_ffmpeg_text("Case: unsolved")
        self.assertNotIn(":", out)
        self.assertEqual(out, "Case  unsolved")

    def test_no_colon_survives(self):
        for s in ("11:54", "at 7:05pm", "ratio 3:1:2", "x: 4:30"):
            self.assertNotIn(":", escape_ffmpeg_text(s))


if __name__ == "__main__":
    unittest.main()
