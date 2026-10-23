"""#12 cold open (MASTER_PLAN #12, plan-bcf action 6), with growth-lab S5 hook_lint and S4 captions.

* The cold open is ONE complete sentence of 10 words or fewer, linted by hook_lint (true-crime
  mode, a guilt-wording warning counts as a fail). It is never truncated: a line that fails is
  rewritten once by the writer, else the video has no hook card at all (old code cut at 12
  words and added "...", 14 of 24 hooks ended that way).
* The cold open is the first spoken line of the script, so the fact-check, the TikTok
  compliance review and the legal review all read it.
* Word captions run from frame 0 (no text-free gap at ~2 s under the hook card); with no word
  timings at all, S4 estimates them from the script.
"""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import story_generator as sg  # noqa: E402

CASE = {"case_name": "Disappearance of Suzy Lamplugh", "location": "Fulham, London", "year": "1986",
        "summary": "s", "key_facts": []}
BODY = ("She left the office in Fulham to meet a client called Mr Kipper. "
        "Her car was found that evening. She has never been found. What do you think happened?")


def _reply(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


class ColdOpenLine(unittest.TestCase):
    def test_a_short_specific_first_sentence_is_kept_without_a_call(self):
        script = "Suzy Lamplugh left her Fulham office in 1986 and vanished. " + BODY
        with mock.patch.object(sg, "_groq_call") as call:
            line, out = sg._cold_open(None, CASE, script)
        call.assert_not_called()
        self.assertEqual(line, "Suzy Lamplugh left her Fulham office in 1986 and vanished.")
        self.assertEqual(out, script)

    def test_a_long_first_sentence_gets_a_separate_cold_open(self):
        script = ("When the estate agent left her office that afternoon nobody imagined it would be "
                  "the last time she was seen. " + BODY)
        new = "In 1986, a Fulham estate agent went to a viewing."
        with mock.patch.object(sg, "_groq_call", return_value=_reply(new)) as call:
            line, out = sg._cold_open(None, CASE, script)
        call.assert_called_once()
        self.assertEqual(line, new)
        self.assertTrue(out.startswith(new + " "))
        self.assertTrue(out.endswith(script))

    def test_never_truncated_no_hook_when_the_rewrite_fails_too(self):
        script = ("When the estate agent left her office that afternoon nobody imagined it would be "
                  "the last time she was seen. " + BODY)
        bad = "In 1986 a young estate agent from Fulham left her office and walked away..."
        with mock.patch.object(sg, "_groq_call", return_value=_reply(bad)):
            line, out = sg._cold_open(None, CASE, script)
        self.assertEqual(line, "")
        self.assertEqual(out, script)
        self.assertNotIn("…", out)

    def test_a_line_that_states_guilt_about_a_named_person_is_rejected(self):
        script = "John Smith murdered his wife in Leeds in 1990. " + BODY
        safe = "In 1990, a Leeds woman vanished from her home."
        with mock.patch.object(sg, "_groq_call", return_value=_reply(safe)):
            line, out = sg._cold_open(None, CASE, script)
        self.assertEqual(line, safe)
        self.assertTrue(out.startswith(safe))

    def test_writer_failure_means_no_hook_not_a_crash(self):
        script = ("When the estate agent left her office that afternoon nobody imagined it would be "
                  "the last time she was seen. " + BODY)
        with mock.patch.object(sg, "_groq_call", side_effect=RuntimeError("llm down")):
            line, out = sg._cold_open(None, CASE, script)
        self.assertEqual((line, out), ("", script))

    def test_hook_for_reads_only_the_first_sentence_and_never_cuts_it(self):
        self.assertEqual(sg._hook_for("Suzy Lamplugh vanished in Fulham in 1986. More text.", CASE),
                         "Suzy Lamplugh vanished in Fulham in 1986.")
        long = "When the estate agent left her office that afternoon nobody imagined anything. x"
        self.assertEqual(sg._hook_for(long, CASE), "")


class ColdOpenIsReviewed(unittest.TestCase):
    """The cold open is part of what the compliance and legal reviews read."""

    def _finalise(self, script, cold_reply=None):
        seen = {}

        def compliance(client, s, caption, case, cta=""):
            seen["compliance"] = s
            return {"verdict": sg.COMPLIANCE_OK, "reasons": []}

        def legal(client, s, caption, cta, case):
            seen["legal"] = s
            return {"ok": True, "reasons": []}

        def fact(client, case, s):
            seen.setdefault("fact", s)
            return {"accuracy_score": 9, "interest_score": 9, "makes_sense": True,
                    "approved": True, "tiktok_safe": True, "issues": []}

        def groq(client, **kw):
            text = kw["messages"][-1]["content"]
            if "cold open" in text.lower() and cold_reply:
                return _reply(cold_reply)
            return _reply("A title")
        with mock.patch.object(sg, "_fact_check", fact), \
                mock.patch.object(sg, "compliance_review", compliance), \
                mock.patch.object(sg, "_legal_review", legal), \
                mock.patch.object(sg, "_groq_call", groq), \
                mock.patch.object(sg, "_generate_hashtags", lambda c, case: "#ukcrime #coldcase"), \
                mock.patch.object(sg, "_write_caption", lambda c, case, s: "A description."), \
                mock.patch.object(sg, "_write_engagement_cta", lambda c, case, s: "What do you think?"), \
                mock.patch.object(sg, "build_tiktok_title", lambda *a, **k: "Suzy Lamplugh."):
            result, status = sg._finalise(None, CASE, CASE["case_name"], script)
        return result, status, seen

    def test_a_prepended_cold_open_reaches_every_review(self):
        script = ("When the estate agent left her office that afternoon nobody imagined it would be "
                  "the last time she was seen. " + BODY)
        new = "In 1986, a Fulham estate agent went to a viewing."
        result, status, seen = self._finalise(script, cold_reply=new)
        self.assertEqual(status, "approved")
        self.assertEqual(result["hook"], new)
        self.assertEqual(result["cold_open"], new)
        for stage in ("fact", "compliance", "legal"):
            self.assertTrue(seen[stage].startswith(new), stage)
        self.assertTrue(result["script"].startswith(new))

    def test_a_kept_first_sentence_is_the_hook(self):
        script = "Suzy Lamplugh left her Fulham office in 1986 and vanished. " + BODY
        result, status, seen = self._finalise(script)
        self.assertEqual(result["hook"], "Suzy Lamplugh left her Fulham office in 1986 and vanished.")
        self.assertEqual(seen["legal"], script)


class Prompts(unittest.TestCase):
    def test_script_prompt_asks_for_a_ten_word_cold_open(self):
        self.assertIn("10 words or fewer", sg.TRUE_CRIME_SCRIPT_PROMPT)
        self.assertNotIn("maximum 14 words", sg.TRUE_CRIME_SCRIPT_PROMPT)

    def test_later_series_parts_get_a_real_cold_open(self):
        part = {"k": 2, "n": 3, "beat": "the search", "beats": ["a", "the search", "c"]}
        text = sg._part_instruction(part)
        self.assertNotIn("Open with 'Part two.'", text)
        self.assertIn("10 words or fewer", text)


class CaptionsFromFrameZero(unittest.TestCase):
    def test_captions_are_drawn_under_the_hook_card(self):
        from video_composer import build_filter_script
        seg = [{"text": "Suzy", "start": 0.0, "end": 0.4}, {"text": "Lamplugh", "start": 0.4, "end": 0.9}]
        f = build_filter_script(seg, "scale=1080:1920", hook_text="SHE VANISHED IN 1986")
        self.assertIn("text='SUZY'", f)
        self.assertIn("text='LAMPLUGH'", f)

    def test_the_first_caption_starts_at_frame_zero(self):
        from video_composer import build_filter_script
        seg = [{"text": "Suzy", "start": 0.22, "end": 0.6}]
        f = build_filter_script(seg, "scale=1080:1920", hook_text="X")
        self.assertIn("between(t,0.000,0.600)", f)

    def test_no_word_timings_fall_back_to_s4_estimates(self):
        import tempfile
        import caption_generator as cg
        d = tempfile.mkdtemp()
        with mock.patch.object(cg, "transcribe_audio_fallback", side_effect=RuntimeError("no whisper")):
            segs = cg.get_captions({"audio_path": "x.wav", "duration": 6.0}, d, prefix="t",
                                   script_text="Suzy Lamplugh vanished in 1986. Nobody knows why.")
        self.assertEqual(segs[0]["start"], 0.0)
        self.assertEqual(" ".join(s["text"] for s in segs).split()[0], "Suzy")
        self.assertLessEqual(segs[-1]["end"], 6.0 + 0.01)
        self.assertTrue(all(s.get("approx") for s in segs))


if __name__ == "__main__":
    unittest.main()
