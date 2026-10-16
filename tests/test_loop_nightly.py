"""
Experiment-loop wiring for BuriedCasefiles, on fake data (growth-lab, 2026-10-16).

Run:  py -m unittest discover -s tests -v
No network, no LLM calls, nothing is posted.
"""
import json
import os
import random
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "analytics"))
import experiment_loop as el  # noqa: E402
import loop_nightly as ln  # noqa: E402


def fake_posts(exp_id, n, hold_c, hold_v, seed=0):
    rng = random.Random(seed)
    return ([{"id": f"c{i}", "arms": {exp_id: "control"}, "hold": hold_c + rng.uniform(-2, 2),
              "views72": 400} for i in range(n)] +
            [{"id": f"v{i}", "arms": {exp_id: "variant"}, "hold": hold_v + rng.uniform(-2, 2),
              "views72": 400} for i in range(n)])


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        for f in ("experiments.json", "levers.json", "loop_config.json"):
            shutil.copy(os.path.join(ROOT, f), self.tmp)
        mock.patch.object(ln, "HERE", self.tmp).start()

    def tearDown(self):
        mock.patch.stopall()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def read(self, name):
        with open(os.path.join(self.tmp, name), encoding="utf-8") as fh:
            return json.load(fh)

    def on(self, day):
        dt = mock.patch.object(ln, "datetime").start()
        dt.now.return_value = datetime(*day, 12, tzinfo=timezone.utc)
        dt.strptime = datetime.strptime


class TestNightly(Sandbox):
    def test_api_failure_changes_nothing_and_says_so(self):
        self.on((2026, 10, 26))
        with mock.patch.object(ln, "collect", side_effect=RuntimeError("snapshots empty")):
            self.assertEqual(ln.main([]), 0)
        self.assertEqual(self.read("experiments.json")["experiments"], [])
        acts = " ".join(self.read("loop_summary.json")["actions"])
        self.assertIn("snapshots empty", acts)
        self.assertIn("no decisions", acts)

    def test_no_new_test_before_not_before(self):
        self.on((2026, 10, 20))
        with mock.patch.object(ln, "collect", return_value=([], {})):
            ln.main([])
        self.assertEqual(self.read("experiments.json")["experiments"], [])

    def test_starts_exactly_one_test_from_the_levers_file(self):
        self.on((2026, 10, 25))
        for _ in range(2):                                   # second night: no second test
            with mock.patch.object(ln, "collect", return_value=([], {})):
                ln.main([])
        exps = self.read("experiments.json")["experiments"]
        self.assertEqual(len(exps), 1)
        self.assertEqual((exps[0]["lever"], exps[0]["control"], exps[0]["variant"]),
                         ("hook_card", True, False))

    def test_winner_promoted_loser_reverted(self):
        self.on((2026, 10, 25))
        with mock.patch.object(ln, "collect", return_value=([], {})):
            ln.main([])
        eid = self.read("experiments.json")["experiments"][0]["id"]
        self.on((2026, 11, 20))
        with mock.patch.object(ln, "collect", return_value=(fake_posts(eid, 16, 30, 45), {})):
            ln.main([])
        exps = self.read("experiments.json")["experiments"]
        self.assertEqual(exps[0]["status"], "promoted")
        self.assertIs(self.read("loop_config.json")["values"]["hook_card"], False)
        self.assertEqual(exps[1]["lever"], "cta_card")       # next in the queue
        with mock.patch.object(ln, "collect",
                               return_value=(fake_posts(exps[1]["id"], 16, 45, 30), {})):
            ln.main([])
        exps = self.read("experiments.json")["experiments"]
        self.assertEqual(exps[1]["status"], "reverted")
        self.assertIs(self.read("loop_config.json")["values"]["cta_card"], True)
        self.assertIs(self.read("loop_config.json")["values"]["hook_card"], False)

    def test_dry_run_writes_nothing(self):
        self.on((2026, 10, 26))
        with mock.patch.object(ln, "collect", return_value=([], {})):
            ln.main(["--dry-run"])
        self.assertEqual(self.read("experiments.json")["experiments"], [])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "loop_summary.json")))

    def test_collect_on_real_files(self):
        posts, summary = ln.collect()
        self.assertTrue(posts)
        self.assertIn("views", summary)


class TestRenderSide(unittest.TestCase):
    def test_broken_loop_files_render_as_before(self):
        import main_documentary as md
        with mock.patch.object(el, "load_repo", side_effect=ValueError("bad json")):
            self.assertEqual(md._loop_values(), ({}, {}))

    def test_force_values_for_previews(self):
        import main_documentary as md
        with mock.patch.dict(os.environ, {"LOOP_FORCE_VALUES": '{"hook_card": false}'}):
            vals, _ = md._loop_values()
        self.assertIs(vals["hook_card"], False)

    def test_cards_off_leave_no_overlay(self):
        from video_composer import build_filter_script
        seg = [{"text": "hello", "start": 0.0, "end": 1.0}]
        on = build_filter_script(seg, "scale=1080:1920", hook_text="HE VANISHED",
                                 cta_text="Who did it?", total_duration=40)
        off = build_filter_script(seg, "scale=1080:1920", hook_text="", cta_text="",
                                  total_duration=40)
        self.assertIn("HE VANISHED", on)
        self.assertNotIn("HE VANISHED", off)
        self.assertNotIn("WHO DID IT", off)


if __name__ == "__main__":
    unittest.main()
