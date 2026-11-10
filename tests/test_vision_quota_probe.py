"""main_documentary._vision_quota_left: skip a run only when the daily AI-review quota is truly gone."""
import contextlib
import io
import os
import sys
import unittest
import urllib.error
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import main_documentary as md  # noqa: E402


def http_error(code, text):
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(text.encode()))


class Probe(unittest.TestCase):
    def ask(self, outcome):
        def opener(req, timeout=0):
            if isinstance(outcome, Exception):
                raise outcome
            return contextlib.nullcontext(outcome)
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "k"}):
            return md._vision_quota_left(opener)

    def test_normal_reply_means_go(self):
        self.assertTrue(self.ask(object()))

    def test_daily_quota_means_skip(self):
        self.assertFalse(self.ask(http_error(429, "Rate limit reached ... tokens per day (TPD): Limit 200000, Used 199800")))

    def test_a_busy_minute_is_not_a_reason_to_skip(self):
        self.assertTrue(self.ask(http_error(429, "input tokens per minute (ITPM): Limit 7000")))

    def test_server_and_network_trouble_do_not_skip(self):
        self.assertTrue(self.ask(http_error(503, "busy")))
        self.assertTrue(self.ask(TimeoutError("slow")))

    def test_no_key_does_not_skip(self):
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": ""}):
            self.assertTrue(md._vision_quota_left(lambda *a, **k: self.fail("must not call")))


if __name__ == "__main__":
    unittest.main()


class CourtRecordRules(unittest.TestCase):
    """The fact-checker is told to fail a script that adds a mystery the court settled,
    or that calls an unconvicted person guilty."""

    def test_rules_reach_the_fact_checker(self):
        import story_generator as sg
        seen = {}

        def fake_call(client, **kw):
            seen["user"] = kw["messages"][1]["content"]
            msg = mock.Mock()
            msg.choices = [mock.Mock(message=mock.Mock(content='{"accuracy_score": 3, "approved": false}'))]
            return msg
        with mock.patch.object(sg, "_groq_call", fake_call):
            out = sg._fact_check(None, {"case_name": "X", "source_text": "A court convicted Y."}, "script")
        self.assertIn(sg.COURT_RECORD_RULES, seen["user"])
        self.assertIn("convicted", sg.COURT_RECORD_RULES)
        self.assertIn("acquitted", sg.COURT_RECORD_RULES)
        self.assertFalse(out["approved"])
