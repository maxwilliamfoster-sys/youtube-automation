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


class TestTextZones(unittest.TestCase):
    def test_zones_follow_the_composer(self):
        from cartoon import layout
        z = {d["name"]: d for d in layout.text_zones(40.0, "A serial killer asked police for advice", "Would you?")}
        self.assertEqual(z["captions"]["box"][1], 729 - layout.PAD)      # CAPTION_POSITION 0.38 of 1920
        self.assertEqual((z["hook card"]["t0"], z["hook card"]["t1"]), (0.0, 1.8))
        self.assertAlmostEqual(z["end card"]["t0"], 37.4)
        self.assertNotIn("case tag", z)

    def test_stamp_beats_an_icon_on_the_same_word(self):
        # BTK sample: the gavel took the top slot first and pushed CASE CLOSED under the captions.
        from cartoon import layout
        zones = layout.text_zones(65.0, "A serial killer asked police for advice", "Would you?")
        props = [{"kind": "icon", "value": "gavel", "cue": 1, "t": 58.6},
                 {"kind": "stamp", "value": "CASE CLOSED", "cue": 1, "t": 58.6}]
        placed = dict((p["kind"], s) for p, s in engine._assign_slots(props, zones, 60.0))
        self.assertEqual(placed["stamp"], engine.STAMP_SLOT)
        for p, s in engine._assign_slots(props, zones, 60.0):
            box = engine.prop_box(p, s)
            for z in layout.active(zones, p["t"], 60.0):
                self.assertLessEqual(layout.overlap(box, z["box"]),
                                     engine.TEXT_COVER_OK * layout.area(box), (p["value"], z["name"]))


def _court_plan(duration=6.0):
    script = "The trial lasted a week. In 2019 he was convicted and sentenced to life in prison."
    pl = planner.plan(script, _words(script, 0.4), duration, use_llm=False, log=lambda *a: None)
    return script, pl


class TestCartoonGate(unittest.TestCase):
    def test_engine_keeps_faces_off_the_text(self):
        from cartoon import layout, qc
        _, pl = _court_plan()
        zones = layout.text_zones(6.0, "A hook card that runs over a few lines", "Would you have believed him?")
        engine.prepare(pl, zones, seed=1)
        failures, stats = qc.check_layout(pl)
        self.assertEqual(failures, [])
        self.assertGreater(stats["frames_checked"], 50)

    def test_gate_catches_text_over_a_face(self):
        from cartoon import qc
        _, pl = _court_plan()
        engine.prepare(pl, [], seed=1)                     # rendered without avoiding anything...
        pl["zones"] = [{"name": "captions", "t0": 0, "t1": 99, "box": (0, 800, 1080, 1400)}]   # ...text over the heads
        failures, _ = qc.check_layout(pl)
        self.assertTrue(any("covers" in f and "face" in f for f in failures), failures)

    def test_sync_passes_when_in_step(self):
        from cartoon import qc
        script, pl = _court_plan()
        engine.prepare(pl, [], seed=1)
        failures, stats = qc.check_sync(pl, _words(script, 0.4))
        self.assertEqual(failures, [])
        self.assertEqual(stats["coverage"], 1.0)

    def test_sync_catches_picture_running_ahead_of_the_voice(self):
        from cartoon import qc
        script, pl = _court_plan(9.0)
        engine.prepare(pl, [], seed=1)
        late = [{**w, "start": w["start"] + 2.5, "end": w["end"] + 2.5} for w in _words(script, 0.4)]
        failures, _ = qc.check_sync(pl, late)
        self.assertTrue(any("scene 2" in f for f in failures), failures)

    def test_scene_may_start_a_little_early_but_not_late(self):
        from cartoon import qc
        script, pl = _court_plan(9.0)
        engine.prepare(pl, [], seed=1)
        words = _words(script, 0.4)
        early = [{**w, "start": w["start"] + 0.9, "end": w["end"] + 0.9} for w in words]   # scene 0.9 s early
        self.assertFalse(any("starts" in f for f in qc.check_sync(pl, early)[0]))
        late = [{**w, "start": w["start"] - 0.9, "end": w["end"] - 0.9} for w in words]    # scene 0.9 s late
        self.assertTrue(any("after its first word" in f for f in qc.check_sync(pl, late)[0]))

    def test_sync_fails_closed_without_a_transcript(self):
        from cartoon import qc
        _, pl = _court_plan()
        failures, _ = qc.check_sync(pl, [])
        self.assertTrue(failures and "heard" in failures[0])

    def test_content_catches_unrelated_popups_and_generic_sets(self):
        from cartoon import qc
        _, pl = _court_plan()
        engine.prepare(pl, [], seed=1)
        sc = pl["scenes"][1]
        sc["props"].append({"kind": "icon", "value": "boat", "cue": sc["span"][0], "t": sc["t0"], "slot": [250, 905]})
        sc["set"] = "office"
        failures = qc.check_content(pl)
        self.assertTrue(any("boat icon" in f for f in failures), failures)
        self.assertTrue(any("courtroom scene" in f for f in failures), failures)


class TestSentenceMatch(unittest.TestCase):
    def _v(self, raw, sentence, script=SCRIPT, prev=None):
        toks = planner.tokens_of(sentence)
        return planner.validate_scene(raw, 0, sentence, toks, (0, len(toks)), script,
                                      bool(planner.CONVICTED_RE.search(script)), prev)

    def test_generic_office_moves_to_the_named_place(self):
        out = self._v({"set": "office", "actors": [{"role": "detective", "spot": "center"}]},
                      "Years later DNA from the car led detectives to a stranger.")
        self.assertEqual(out["set"], "lab")
        self.assertEqual(out["actors"][0]["role"], "forensic")

    def test_unrelated_icon_and_hold_dropped(self):
        out = self._v({"set": "road_night", "actors": [{"role": "police", "hold": "boat"}],
                       "props": [{"icon": "boat", "cue": "car"}, {"icon": "car", "cue": "car"}]},
                      "Her car was found by the river at 9pm.")
        self.assertEqual([p["value"] for p in out["props"]], ["car"])
        self.assertIsNone(out["actors"][0]["hold"])

    def test_label_must_be_in_its_own_sentence(self):
        out = self._v({"set": "road_night", "actors": [{"role": "police"}],
                       "props": [{"label": "Mark Hill", "cue": "car"}]}, "Her car was found by the river at 9pm.")
        self.assertEqual(out["props"], [])

    def test_house_exterior_never_used(self):
        out = self._v({"set": "house", "actors": [{"role": "police"}]}, "Police knocked on the front door of her house.")
        self.assertEqual(out["set"], "street_day")
        pl = planner.plan("Police went to the house where she lived. Her neighbour saw nothing.",
                          [], 6.0, use_llm=False, log=lambda *a: None)
        self.assertNotIn("house", [sc["set"] for sc in pl["scenes"]])

    def test_guilt_stamps_become_case_closed(self):
        script = "In 2019 he pleaded guilty to murder and was sentenced to life in prison."
        out = self._v({"set": "courtroom", "actors": [{"role": "judge"}], "props": [{"stamp": "GUILTY", "cue": "guilty"}]},
                      script, script)
        self.assertEqual([p["value"] for p in out["props"]], ["CASE CLOSED"])
        charged = "He was charged with murder."
        out = self._v({"set": "courtroom", "actors": [{"role": "judge"}], "props": [{"stamp": "CHARGED", "cue": "charged"}]},
                      charged, charged)
        self.assertEqual(out["props"], [])

    def test_captions_never_overlap(self):
        import re as _re
        import video_composer
        segs = [{"text": "moved", "start": 0.0, "end": 0.6}, {"text": "from", "start": 0.4, "end": 0.9},
                {"text": "Canada", "start": 0.9, "end": 1.5}]
        vf = video_composer.build_filter_script(segs, "null")
        spans = [tuple(map(float, m)) for m in _re.findall(r"between\(t,([\d.]+),([\d.]+)\)", vf)]
        for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
            self.assertLessEqual(a1, b0)

    def test_icon_fits(self):
        self.assertTrue(planner.icon_fits("map_pin", "He murdered ten people in Kansas."))
        self.assertTrue(planner.icon_fits("floppy", "He mailed a purple floppy disk."))
        self.assertFalse(planner.icon_fits("boat", "A DNA test matched him."))


if __name__ == "__main__":
    unittest.main()
