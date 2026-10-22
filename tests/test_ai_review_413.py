"""Run 39452123772: the gate's AI review asked for 7031 tokens against Groq's 7000/min cap (HTTP 413)
and, being a hard limit, every retry failed the same way, so the video was never delivered.
The review must retry with a shorter narration excerpt - same frames, same rules, same verdict bar."""
import io
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import upload_gate as ug  # noqa: E402

PASS = {"choices": [{"message": {"content": json.dumps(
    {"verdict": "PASS", "coherence": 9, "quality": 9, "policy_issues": [], "quality_issues": [], "summary": "ok"})}}],
    "usage": {"total_tokens": 6000}}


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def too_large():
    return urllib.error.HTTPError("u", 413, "Payload Too Large", {}, io.BytesIO(
        b'{"error":{"message":"Request too large ... input tokens per minute (ITPM): Limit 7000, Requested 7031"}}'))


class AiReview413(unittest.TestCase):
    def run_review(self, outcomes, transcript="word " * 600):
        bodies = []

        def urlopen(req, timeout=0):
            bodies.append(json.loads(req.data))
            o = outcomes[len(bodies) - 1]
            if isinstance(o, Exception):
                raise o
            return Resp(json.dumps(o).encode())
        profile = {"ai_context": "true crime"}
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "k"}), \
                mock.patch.object(ug, "_images_b64", lambda *a, **k: ["a", "b", "c"]), \
                mock.patch.object(ug.urllib.request, "urlopen", urlopen), \
                mock.patch.object(ug.time, "sleep", lambda s: None):
            r = ug.ai_review("v.mp4", "tiktok", profile, title="t", caption="c", transcript=transcript, seconds=40)
        return r, bodies

    @staticmethod
    def text(body):
        return body["messages"][0]["content"][0]["text"]

    def test_retries_with_a_shorter_excerpt_and_still_reviews(self):
        r, bodies = self.run_review([too_large(), PASS])
        self.assertTrue(r["ok"], r)
        self.assertEqual(len(bodies), 2)
        self.assertLess(len(self.text(bodies[1])), len(self.text(bodies[0])))
        self.assertEqual(len(bodies[1]["messages"][0]["content"]), 4)       # all three images still sent

    def test_still_fails_closed_if_it_never_fits(self):
        r, bodies = self.run_review([too_large()] * 6)
        self.assertFalse(r["ok"])
        self.assertFalse(r["available"])
        self.assertLessEqual(len(bodies), 4)

    def test_a_short_script_is_not_shrunk_pointlessly(self):
        r, bodies = self.run_review([too_large(), too_large(), too_large(), too_large()], transcript="short")
        self.assertFalse(r["ok"])
        self.assertEqual(len(bodies), 1)                                    # nothing to trim: fail at once


def http(code, body=b'{"error":{"message":"busy"}}'):
    return urllib.error.HTTPError("u", code, "err", {}, io.BytesIO(body))


class RetryableFlag(unittest.TestCase):
    """Audit 2026-10-21 #2: run 39789555396 re-checked a 413 twelve times, 5 min apart.
    Only an outage that waiting can fix may be re-checked."""
    run_review = AiReview413.run_review

    def test_413_is_not_retryable(self):
        r, _ = self.run_review([too_large()] * 4, transcript="short " * 100)
        self.assertFalse(r["available"])
        self.assertFalse(r.get("retryable"))
        self.assertTrue(r.get("too_large"))

    def test_daily_quota_is_not_retryable(self):
        r, _ = self.run_review([http(429, b'{"error":{"message":"tokens per day (TPD)"}}')])
        self.assertFalse(r.get("retryable"))

    def test_server_errors_and_rate_limits_are_retryable(self):
        for code in (429, 500, 503):
            r, _ = self.run_review([http(code)] * 3)
            self.assertFalse(r["available"])
            self.assertTrue(r.get("retryable"), code)

    def test_network_failure_is_retryable(self):
        r, _ = self.run_review([OSError("timed out")] * 3)
        self.assertTrue(r.get("retryable"))


class BotRecheck(unittest.TestCase):
    def test_bot_rechecks_only_retryable_outages(self):
        import main_documentary as md
        down = {"ok": False, "failures": ["AI review unavailable: AI review failed: HTTP 503"],
                "ai": {"available": False, "retryable": True}}
        big = {"ok": False, "failures": ["AI review unavailable: AI review failed: HTTP 413"],
               "ai": {"available": False, "retryable": False, "too_large": True}}
        quota = {"ok": False, "failures": ["AI review unavailable: quota"],
                 "ai": {"available": False, "quota": True}}
        legacy = {"ok": False, "failures": ["AI review unavailable: x"], "ai": {"available": False}}
        self.assertTrue(md._ai_outage_retryable(down))
        self.assertFalse(md._ai_outage_retryable(big))
        self.assertFalse(md._ai_outage_retryable(quota))
        self.assertFalse(md._ai_outage_retryable(legacy))
        self.assertFalse(md._ai_outage_retryable({**down, "ok": True}))


class LedgerScrub(unittest.TestCase):
    """Audit F12: upload_gate_log.json is public; AI summaries restated allegations."""

    def test_allegation_text_is_withheld_from_the_public_ledger(self):
        import tempfile
        verdict = {"checked_at": "t", "platform": "tiktok", "file": "f.mp4", "sha256": "x",
                   "head": "Theresa Allore case.", "ok": False, "gate_version": "v",
                   "failures": ["AI review: The narration implies the guilt of an uncharged suspect, "
                                "John Example | names John Example as the killer",
                                "AI review: garbled on-screen text 'V/AFTER:D'"],
                   "warnings": [], "report": {"seconds": 40},
                   "ai": {"available": True, "ok": False, "coherence": 4, "quality": 6,
                          "summary": "Blocked: it implicates John Example, who was acquitted.",
                          "tokens": 10}}
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "log.json")
            ug.record(verdict, p)
            with open(p, encoding="utf-8") as fh:
                raw = fh.read()
        self.assertNotIn("John Example", raw)
        self.assertNotIn("acquitted", raw)
        self.assertIn("V/AFTER:D", raw)                     # unrelated detail is kept
        self.assertIn("withheld", raw)
        self.assertIn("John Example", verdict["failures"][0])  # in-memory verdict untouched


if __name__ == "__main__":
    unittest.main()
