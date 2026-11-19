"""Cartoon visuals (cartoon/): planner rules, legal guards, timing and a render smoke test."""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cartoon import engine, planner  # noqa: E402
from cartoon.kit import ICONS, ROLES  # noqa: E402
from cartoon.sets import SETS  # noqa: E402

SCRIPT = ("Sarah Jones vanished on a cold night in 1998. Her car was found by the river at 9pm. "
          "Police questioned her neighbour Mark Hill, who was released without charge. "
          "Years later DNA from the car led detectives to a stranger. "
          "In 2019 he was convicted and sentenced to life in prison.")


def _words(script, step=0.33):
    return [{"word": w, "start": i * step, "end": i * step + 0.3} for i, w in enumerate(planner.tokens_of(script))]


class TestSentences(unittest.TestCase):
    def test_split_and_merge(self):
        toks, spans = planner.split_sentences("Short one. This is a longer sentence about the case. Ok.")
        self.assertEqual(spans[0][0], 0)
        self.assertEqual(spans[-1][1], len(toks))
        for a, b in spans:
            self.assertGreaterEqual(b - a, 5)

    def test_cap_on_scene_count(self):
        script = " ".join(f"This is sentence number {i} of the story." for i in range(40))
        _, spans = planner.split_sentences(script)
        self.assertLessEqual(len(spans), 18)


class TestTiming(unittest.TestCase):
    def test_align_exact(self):
        toks = planner.tokens_of(SCRIPT)
        times = planner.align_times(toks, _words(SCRIPT), 30)
        self.assertAlmostEqual(times[5], 5 * 0.33, places=3)

    def test_align_survives_bad_transcript(self):
        toks = planner.tokens_of(SCRIPT)
        heard = [{"word": "zzz", "start": 1.0, "end": 1.2}]
        times = planner.align_times(toks, heard, 20.0)
        self.assertEqual(len(times), len(toks))
        self.assertEqual(times, sorted(times))
        self.assertLessEqual(times[-1], 20.0)

    def test_no_timings_at_all(self):
        toks = planner.tokens_of(SCRIPT)
        times = planner.align_times(toks, [], 20.0)
        self.assertEqual(times, sorted(times))


class TestRulePlan(unittest.TestCase):
    def setUp(self):
        self.plan = planner.plan(SCRIPT, _words(SCRIPT), 25.0, use_llm=False, log=lambda *a: None)

    def test_scenes_cover_the_audio(self):
        sc = self.plan["scenes"]
        self.assertEqual(sc[0]["t0"], 0.0)
        for a, b in zip(sc, sc[1:]):
            self.assertEqual(a["t1"], b["t0"])
        self.assertGreater(sc[-1]["t1"], 25.0)

    def test_vocabulary(self):
        for sc in self.plan["scenes"]:
            self.assertIn(sc["set"], SETS)
            for a in sc["actors"]:
                self.assertIn(a["role"], ROLES)
            for p in sc["props"]:
                if p["kind"] == "icon":
                    self.assertIn(p["value"], ICONS)

    def test_missing_stamp_and_court(self):
        stamps = [p["value"] for sc in self.plan["scenes"] for p in sc["props"] if p["kind"] == "stamp"]
        self.assertIn("MISSING", stamps)
        self.assertIn("courtroom", [sc["set"] for sc in self.plan["scenes"]])


class TestLegalGuards(unittest.TestCase):
    def _v(self, raw, sentence, script):
        toks = planner.tokens_of(sentence)
        return planner.validate_scene(raw, 0, sentence, toks, (0, len(toks)), script,
                                      bool(planner.CONVICTED_RE.search(script)))

    def test_released_suspect_never_a_villain(self):
        s = "Police questioned her neighbour Mark Hill, who was released without charge."
        out = self._v({"set": "interview_room", "actors": [{"role": "shadow", "mood": "smug", "spot": "right"}]}, s, SCRIPT)
        self.assertEqual(out["actors"][0]["role"], "man")
        self.assertEqual(out["actors"][0]["mood"], "neutral")

    def test_no_convict_without_conviction(self):
        script = "Tom Reed was charged with murder. The trial is due next year."
        s = "Tom Reed was charged with murder."
        out = self._v({"set": "courtroom", "actors": [{"role": "convict", "spot": "left"}],
                       "props": [{"stamp": "GUILTY", "cue": "murder"}]}, s, script)
        self.assertEqual(out["actors"][0]["role"], "man")
        self.assertEqual(out["props"], [])

    def test_unknown_culprit_may_be_shadow(self):
        script = "A masked figure was seen leaving the shop. The case was never solved."
        s = "A masked figure was seen leaving the shop."
        out = self._v({"set": "shop", "actors": [{"role": "shadow", "spot": "center"}]}, s, script)
        self.assertEqual(out["actors"][0]["role"], "shadow")

    def test_invented_label_dropped(self):
        s = "Her car was found by the river at 9pm."
        out = self._v({"set": "road_night", "actors": [{"role": "police"}],
                       "props": [{"label": "9pm", "cue": "9pm"}, {"label": "the killer", "cue": "car"}]}, s, SCRIPT)
        self.assertEqual([p["value"] for p in out["props"]], ["9pm"])

    def test_unknown_set_rejected(self):
        self.assertIsNone(self._v({"set": "crime_scene", "actors": [{"role": "police"}]}, "A test sentence here.", SCRIPT))

    def test_cue_resolves_to_word(self):
        s = "Her car was found by the river at 9pm."
        toks = planner.tokens_of(s)
        out = self._v({"set": "road_night", "actors": [{"role": "police", "action": "point", "cue": "river"}]}, s, SCRIPT)
        self.assertEqual(toks[out["actors"][0]["cue"]].strip(".,"), "river")


class TestRender(unittest.TestCase):
    def test_every_set_and_role_draws(self):
        import cairo
        from cartoon.kit import SafeCtx, person
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1080, 1920)
        for name, spec in SETS.items():
            ctx = SafeCtx(surf)
            spec["bg"](ctx, 3, 1.0)
            for draw in (spec.get("mid"), spec.get("fg")):
                if draw:
                    draw(ctx, 3, 1.0)
        for role in ROLES:
            for mood in ("neutral", "shocked", "smug"):
                person(SafeCtx(surf), 540, 1600, role=role, mood=mood, hold="phone", walk=0.5, t=1.0)

    def test_render_short_video(self):
        script = "Police found a letter at the house. Then the court said guilty."
        pl = planner.plan(script, _words(script), 3.0, use_llm=False, log=lambda *a: None)
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "bg.mp4")
            engine.render(pl, out, seed_text="t", fps=10, log=lambda *a: None)
            self.assertGreater(os.path.getsize(out), 1000)
            ff = engine._ffmpeg()
            ffprobe = os.path.join(os.path.dirname(ff), os.path.basename(ff).replace("ffmpeg", "ffprobe"))
            dur = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", out],
                                 capture_output=True, text=True).stdout.strip()
            self.assertGreaterEqual(float(dur), 3.4)


if __name__ == "__main__":
    unittest.main()
