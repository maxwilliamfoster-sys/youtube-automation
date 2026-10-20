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


if __name__ == "__main__":
    unittest.main()
