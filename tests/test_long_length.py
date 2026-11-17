"""The length lever's 'long' arm (autopilot 2026-11-17, stage 3 new format): a 'full case file' of
190-220 words (~70-80 s) that tells the whole case: setup, the turn, the court outcome, the open question.
Why: every 20-40 s post lands at ~700 views (n=52, 28 d) whatever its hold, while the UK true-crime
TikTok accounts that break out tell whole cases over minutes (truecrimecaitlyn 50K-330K on 4-8 min,
charleybournetruecrime 183K on 4 min)."""
import inspect
import json
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import story_generator as sg  # noqa: E402


class LongScript(unittest.TestCase):
    def test_every_normal_phrase_is_still_in_the_prompts(self):
        src = inspect.getsource(sg)
        for normal, _ in sg.LONG_SUBS:
            self.assertIn(normal, src)

    def test_prompts_change_only_when_the_long_arm_is_on(self):
        self.assertEqual(sg._length_text(sg.TRUE_CRIME_SCRIPT_PROMPT), sg.TRUE_CRIME_SCRIPT_PROMPT)
        with mock.patch.object(sg, "LONG_SCRIPT", True):
            long_ = sg._length_text(sg.TRUE_CRIME_SCRIPT_PROMPT)
        self.assertIn("190-220 words (~70-80 seconds spoken)", long_)
        self.assertNotIn("85-100", long_)

    def test_long_scripts_keep_up_to_220_words_and_the_closing_line(self):
        script = " ".join(f"Sentence number {i} has exactly seven words." for i in range(40)) + " Who did it?"
        self.assertLessEqual(len(sg._fit_length(script).split()), sg.TRIM_SCRIPT_WORDS)
        with mock.patch.object(sg, "LONG_SCRIPT", True):
            out = sg._fit_length(script)
        self.assertGreater(len(out.split()), 180)
        self.assertLessEqual(len(out.split()), sg.LONG_TRIM_WORDS)
        self.assertTrue(out.endswith("Who did it?"))

    def test_long_fits_under_the_hard_maximum(self):
        self.assertLess(sg.LONG_TRIM_WORDS, sg.MAX_SCRIPT_WORDS)

    def test_no_series_is_started_for_a_full_case_file(self):
        self.assertIn("None if (SHORT_SCRIPT or LONG_SCRIPT) else _try_series(", inspect.getsource(sg))


class Wiring(unittest.TestCase):
    def test_main_sets_the_long_flag_from_the_loop(self):
        import main_documentary
        self.assertIn('story_generator.LONG_SCRIPT = loop_vals.get("length") == "long"',
                      inspect.getsource(main_documentary))

    def test_long_is_queued_before_short(self):
        with open(os.path.join(ROOT, "levers.json"), encoding="utf-8") as f:
            levers = json.load(f)["levers"]
        length = next(x for x in levers if x["lever"] == "length")
        self.assertEqual(length["variants"][:2], ["long", "short"])
        self.assertEqual(length.get("primary"), "views72")


if __name__ == "__main__":
    unittest.main()
