"""2026-11-20 (run 44787096162): 11 of 24 scripts came back empty (9 still empty after a retry at
the same 600 tokens), and three "No guidelines-safe case found" batches in a row tripped the
"same failure 3 times = broken" stop, so no catch-up run started and nothing posted that day.
An empty reply is now retried with much more room, and a no-safe-case batch counts as a
content failure (never systemic), so the workflow's catch-up run can still start."""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import story_generator as sg  # noqa: E402
import upload_gate  # noqa: E402

CASE = {"case_name": "Asad Shah", "year": "2016", "location": "Glasgow",
        "key_facts": ["Killed in 2016"], "summary": "x"}
FULL = ("Asad Shah ran a newsagent in Glasgow. " * 10 + "Why was he targeted?").strip()


def _resp(text):
    r = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])
    r.finish_reason = None
    return r


class EmptyScriptRetry(unittest.TestCase):
    def test_empty_reply_is_retried_with_a_bigger_budget(self):
        calls = []

        def fake(client, **kw):
            calls.append(kw.get("max_tokens"))
            return _resp("") if len(calls) == 1 else _resp(FULL)
        with mock.patch.object(sg, "_groq_call", side_effect=fake):
            out = sg._write_script(None, CASE)
        self.assertEqual(out, FULL)
        self.assertEqual(len(calls), 2)
        self.assertGreater(calls[1], calls[0] * 2)


class NoSafeCaseIsContent(unittest.TestCase):
    def test_it_is_still_a_runtime_error(self):
        self.assertTrue(issubclass(sg.NoSafeCaseError, RuntimeError))

    def test_three_content_batches_are_not_systemic(self):
        self.assertFalse(upload_gate.systemic(["", "", ""]))

    def test_the_old_key_was_systemic(self):
        # what main_documentary used to append for this error: proves the bug was real
        msg = "pipeline step could not run: No guidelines-safe case found in 8 attempts."
        k = upload_gate.failure_key([msg])
        self.assertTrue(upload_gate.systemic([k, k, k]))

    def test_cloud_loop_treats_it_as_content(self):
        src = open(os.path.join(ROOT, "main_documentary.py"), encoding="utf-8").read()
        block = src[src.index("isinstance(exc, story_generator.NoSafeCaseError)"):]
        block = block[:block.index("time.sleep(60)")]
        self.assertIn('keys.append("")', block)
        self.assertIn("CONTENT_EXHAUSTED_STOP", block)
        self.assertNotIn("broken = True", block.split("else:")[0])


if __name__ == "__main__":
    unittest.main()
