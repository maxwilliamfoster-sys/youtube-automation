"""Autopilot 2026-11-17: the Gemini backup writer stopped at max_tokens and the cut script was posted.
Run 43982674817 logged "reply cut off at max_tokens (42 words)" and "(53 words)", and the posted
Hauwa Liman video ended its narration on "In September," at 20 s, with no payoff.
A cut-off reply is retried once with more room; a script that still ends mid-sentence loses its
unfinished tail, so no video can stop mid-sentence."""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import story_generator as sg  # noqa: E402

CASE = {"case_name": "Hauwa Liman", "year": "2018", "location": "Rann, Nigeria",
        "key_facts": ["Abducted in 2018"], "summary": "x"}

FULL = ("Nurse Hauwa Liman never came home from Rann. " * 9 + "Who failed her?").strip()
CUT = ("Nurse Hauwa Liman never came home from Rann. " * 6 + "In September,").strip()


def _resp(text, cut=False, style="wrap"):
    choice = SimpleNamespace(message=SimpleNamespace(content=text),
                             finish_reason=("length" if cut else "stop") if style == "openai" else None)
    r = SimpleNamespace(choices=[choice])
    if style == "wrap":
        r.finish_reason = "length" if cut else None
    return r


class CutReplyIsRetried(unittest.TestCase):
    def test_a_cut_reply_is_retried_with_more_room(self):
        calls = []

        def fake(client, **kw):
            calls.append(kw.get("max_tokens"))
            return _resp(CUT, cut=True) if len(calls) == 1 else _resp(FULL)
        with mock.patch.object(sg, "_groq_call", side_effect=fake):
            out = sg._write_script(None, CASE)
        self.assertEqual(out, FULL)
        self.assertEqual(len(calls), 2)
        self.assertGreater(calls[1], calls[0])

    def test_groq_style_finish_reason_counts_too(self):
        seq = [_resp(CUT, cut=True, style="openai"), _resp(FULL, style="openai")]
        with mock.patch.object(sg, "_groq_call", side_effect=lambda c, **kw: seq.pop(0)):
            self.assertEqual(sg._write_script(None, CASE), FULL)

    def test_a_whole_reply_is_not_retried(self):
        with mock.patch.object(sg, "_groq_call", return_value=_resp(FULL)) as m:
            sg._write_script(None, CASE)
        self.assertEqual(m.call_count, 1)


class NoScriptEndsMidSentence(unittest.TestCase):
    def test_unfinished_tail_is_dropped(self):
        out = sg._fit_length(CUT)
        self.assertTrue(out.endswith("Rann."), out[-40:])
        self.assertNotIn("In September", out)

    def test_finished_scripts_keep_their_ending(self):
        for end in ("Who failed her?", "Part two next.", 'He said "never."', "Nobody was charged!"):
            s = "Nurse Hauwa Liman never came home from Rann. " * 9 + end
            self.assertTrue(sg._fit_length(s).endswith(end), end)

    def test_a_cut_script_too_short_after_the_drop_is_rejected(self):
        short = "Nurse Hauwa Liman never came home from Rann. " * 4 + "In September, the army said"
        self.assertEqual(sg._fit_length(short), "")


if __name__ == "__main__":
    unittest.main()
