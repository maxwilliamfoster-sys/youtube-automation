"""A video may not start with the same three words as any of the last 15 posted videos."""
import csv
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import story_generator as sg  # noqa: E402

CASE = {"year": "1994", "location": "Leeds", "case_name": "Disappearance of Jane Roe"}


class OpeningGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv", encoding="utf-8", newline="")
        w = csv.writer(self.tmp)
        w.writerow(["posted_utc", "hook"])
        w.writerow(["2026-09-01", "Too old to count, far back."])
        for i in range(14):
            w.writerow([f"2026-11-{i + 1:02}", f"Filler line number {i} from Leeds."])
        w.writerow(["2026-11-20", "She vanished after leaving a Leeds pub in 1994."])
        self.tmp.close()
        self.p = mock.patch.object(sg, "POSTS_LOG", self.tmp.name)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        os.unlink(self.tmp.name)

    def codes(self, line):
        return [i.split(":")[0] for i in sg._cold_open_issues(line, CASE)]

    def test_same_first_three_words_as_a_recent_video_is_refused(self):
        self.assertIn("opening_reused", self.codes("She vanished after a night shift in Leeds."))
        self.assertIn("opening_reused", self.codes("SHE VANISHED, AFTER the Leeds match."))

    def test_a_different_start_is_fine(self):
        self.assertNotIn("opening_reused", self.codes("Her car was found running outside Leeds."))
        self.assertNotIn("opening_reused", self.codes("She vanished from Leeds in 1994."))     # third word differs

    def test_only_the_last_fifteen_count(self):
        self.assertNotIn("opening_reused", self.codes("Too old to say what happened in Leeds."))

    def test_a_missing_log_never_blocks(self):
        with mock.patch.object(sg, "POSTS_LOG", self.tmp.name + ".nope"):
            self.assertEqual(sg._recent_openings(), set())
            self.assertNotIn("opening_reused", self.codes("She vanished after a night shift in Leeds."))

    def test_the_writer_is_told_what_to_avoid(self):
        line = sg._avoid_openings_line()
        self.assertIn("'she vanished after'", line)
        self.assertTrue(line.endswith(".\n"))


class CaseTag(unittest.TestCase):
    SRC = "Jane Roe disappeared in Leeds, West Yorkshire, in March 1994 after leaving work."

    def tag(self, **case):
        return sg.case_tag({"source_text": self.SRC, **case})

    def test_place_and_year_from_the_source_make_a_tag(self):
        self.assertEqual(self.tag(location="Leeds, England", year="1994"), "Leeds, 1994")

    def test_anything_the_source_does_not_say_means_no_tag(self):
        self.assertEqual(self.tag(location="Bradford", year="1994"), "")
        self.assertEqual(self.tag(location="Leeds", year="1996"), "")
        self.assertEqual(self.tag(location="Leeds", year="1990s"), "")
        self.assertEqual(self.tag(location="", year="1994"), "")
        self.assertEqual(sg.case_tag({"location": "Leeds", "year": "1994"}), "")      # no source at all

    def test_tag_is_drawn_only_when_set_and_only_for_three_seconds(self):
        import video_composer as vc
        import inspect
        src = inspect.getsource(vc.build_filter_script)
        self.assertIn("if CASE_TAG:", src)
        self.assertIn("between(t,0,3.0)", src)
        self.assertEqual(vc.CASE_TAG, "")

    def test_lever_is_off_by_default_and_queued_after_the_length_test(self):
        # 2026-11-12: the 20-25 s looping length test went between hook_card and case_tag
        import json
        with open(os.path.join(ROOT, "levers.json"), encoding="utf-8") as fh:
            levers = json.load(fh)["levers"]
        names = [lv["lever"] for lv in levers]
        self.assertEqual(names.index("case_tag"), names.index("length") + 1)
        self.assertEqual(names.index("length"), names.index("hook_card") + 1)
        self.assertIs(next(lv for lv in levers if lv["lever"] == "case_tag")["control"], False)


if __name__ == "__main__":
    unittest.main()
