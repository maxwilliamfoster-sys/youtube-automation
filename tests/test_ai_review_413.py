"""S1 (audit 2026-10-21 #2): the AI review's 413 and the retryable flag.

Run 39789555396: BCF's review asked for 7019 tokens against Groq's 7000-token per-request input
cap (HTTP 413). BCF's local patch only shortened scripts over 1,250 chars, so nothing shrank and
the bot re-checked the same file 12 times, 5 minutes apart. Groq counts every image as 2048 input
tokens (console.groq.com/docs/vision), so 3 images are 6144 of the 7000: trimming text alone
can't make a ~7k request fit; the retry has to send fewer images.

Ported from youtube-automation/tests/test_ai_review_413.py (c300ca5 + the 10-23 retryable tests),
with test_a_short_script_is_not_shrunk_pointlessly replaced as plan-infra.md action 1 says.
BCF copy: identical to growth-lab @ d04407c plus BotRecheck (main_documentary._ai_outage_retryable)."""
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import upload_gate as ug  # noqa: E402

PASS = {"choices": [{"message": {"content": json.dumps(
    {"verdict": "PASS", "coherence": 9, "quality": 9, "policy_issues": [], "quality_issues": [], "summary": "ok"})}}],
    "usage": {"total_tokens": 6000}}

SCRIPT_650 = ("On a cold morning in 1987 a man walked into a quiet seaside town and was never seen alive again. "
              "Police found his car three days later, keys still in the ignition, a map folded on the seat. "
              "Nobody in the town remembered him. The hotel had no booking in his name. Detectives traced the "
              "map to a shop two hundred miles away, where the owner recalled a nervous customer who paid in "
              "cash and asked about the tides. The inquest recorded an open verdict. No one was ever charged, "
              "and the case file is still open today, waiting for the one witness who saw where he went next. "
              "Police appealed again in 2019 for anyone who rented a room near the old harbours.")


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def too_large(requested=7019, limit=7000):
    return urllib.error.HTTPError("u", 413, "Payload Too Large", {}, io.BytesIO(
        ('{"error":{"message":"Request too large for model `qwen/qwen3.8-27b` in organization `org_abc` '
         f'service tier `on_demand` on input tokens per minute (ITPM): Limit {limit}, Requested {requested}, '
         'please reduce your message size and try again."}}').encode()))


def http(code, body=b'{"error":{"message":"busy"}}'):
    return urllib.error.HTTPError("u", code, "err", {}, io.BytesIO(body))


def fake_images(path, dur, sheets=2, cols=3, rows=2, tile=(216, 384), first=(360, 640), q=5):
    """One fake base64 string per image, sized by its pixel area (so bytes shrink with geometry)."""
    out = []
    if first:
        out.append("f" * (first[0] * first[1] // 100))
    for _ in range(sheets):
        out.append("s" * (cols * tile[0] * rows * tile[1] // 100))
    return out


def text_of(body):
    return body["messages"][0]["content"][0]["text"]


def images_of(body):
    return [c for c in body["messages"][0]["content"] if c.get("type") == "image_url"]


def image_bytes(body):
    return sum(len(c["image_url"]["url"]) for c in images_of(body))


def run_review(outcomes, transcript=SCRIPT_650, caption="Gone at low tide.\n#ukcrime #coldcase #truecrime",
               images=fake_images):
    bodies, sleeps, grabs = [], [], []

    def urlopen(req, timeout=0):
        bodies.append(json.loads(req.data))
        o = outcomes[min(len(bodies), len(outcomes)) - 1]
        if isinstance(o, Exception):
            raise o
        return Resp(json.dumps(o).encode())

    def grab(*a, **k):
        grabs.append(k)
        return images(*a, **k)
    with mock.patch.dict(os.environ, {"GROQ_API_KEY": "k"}), \
            mock.patch.object(ug, "_images_b64", grab), \
            mock.patch.object(ug.urllib.request, "urlopen", urlopen), \
            mock.patch.object(ug.time, "sleep", lambda s: sleeps.append(s)):
        r = ug.ai_review("v.mp4", "tiktok", {"ai_context": "true crime"}, title="t", caption=caption,
                         transcript=transcript, seconds=48)
    return r, bodies, sleeps, grabs


class Shrink413(unittest.TestCase):
    def test_650_char_script_plus_413_gives_a_second_request_under_the_limit(self):
        self.assertEqual(len(SCRIPT_650), 650)
        r, bodies, sleeps, _ = run_review([too_large(7019), PASS])
        self.assertTrue(r["ok"], r)
        self.assertEqual(len(bodies), 2)
        first, second = bodies
        self.assertEqual(len(images_of(first)), 3)                   # a normal review is unchanged
        # strictly smaller in both text and image bytes
        self.assertLess(len(text_of(second)), len(text_of(first)))
        self.assertLess(image_bytes(second), image_bytes(first))
        self.assertLess(len(images_of(second)), len(images_of(first)))
        # and under the 7,000-token limit the 413 reported, by Groq's own counting:
        # 2048 per image + the text share calibrated from "Requested 7019".
        text_tokens_1 = 7019 - 2048 * len(images_of(first))
        predicted = (2048 * len(images_of(second))
                     + text_tokens_1 * len(text_of(second)) / len(text_of(first)))
        self.assertLess(predicted, 7000)
        self.assertLess(ug.predict_tokens(second, calibrate=(first, 7019)), 7000)
        self.assertEqual(sleeps, [])                                  # a 413 never waits
        self.assertEqual(r["shrunk"]["limit"], 7000)
        self.assertLess(r["shrunk"]["predicted_tokens"], 7000)

    def test_normal_review_uses_the_full_size_frames(self):
        r, bodies, _, grabs = run_review([PASS])
        self.assertTrue(r["ok"])
        self.assertEqual(len(bodies), 1)
        self.assertEqual(len(images_of(bodies[0])), 3)
        self.assertEqual(bodies[0]["max_tokens"], 900)
        self.assertEqual(grabs[0].get("sheets", 2), 2)
        self.assertEqual(grabs[0].get("tile", (216, 384)), (216, 384))
        self.assertIn("Images 2-3 are contact sheets of 12 frames", text_of(bodies[0]))
        self.assertNotIn("shrunk", r)

    def test_retry_prompt_describes_the_images_it_actually_sends(self):
        _, bodies, _, _ = run_review([too_large(7019), PASS])
        t = text_of(bodies[1])
        self.assertIn("Image 2 is a contact sheet of 12 frames", t)
        self.assertNotIn("Images 2-3", t)
        self.assertIn("excerpt", t)                       # the model must not call the narration "cut off"

    def test_still_fails_closed_if_it_never_fits(self):
        r, bodies, sleeps, _ = run_review([too_large()] * 6)
        self.assertFalse(r["ok"])
        self.assertFalse(r["available"])
        self.assertTrue(r["too_large"])
        self.assertEqual(r["reason"], "too_large")
        self.assertFalse(r["retryable"])
        self.assertLessEqual(len(bodies), 4)
        self.assertEqual(sleeps, [])
        self.assertIn("too large", " ".join(r["problems"]))

    def test_a_second_413_recalibrates_and_shrinks_further(self):
        r, bodies, _, _ = run_review([too_large(7019), too_large(7200), PASS])
        self.assertTrue(r["ok"], r)
        self.assertEqual(len(bodies), 3)
        self.assertLess(len(images_of(bodies[2])), len(images_of(bodies[1])))

    def test_no_request_is_sent_when_no_stage_can_fit(self):
        # A request 3k over the cap: even one image + a trimmed script can't be predicted under it.
        r, bodies, _, _ = run_review([too_large(12000)] * 3, transcript="word " * 500)
        self.assertEqual(len(bodies), 1)
        self.assertTrue(r["too_large"])
        self.assertFalse(r["retryable"])

    def test_short_script_still_gets_a_smaller_second_request(self):
        r, bodies, _, _ = run_review([too_large(7019), PASS], transcript="short")
        self.assertTrue(r["ok"], r)
        self.assertEqual(len(bodies), 2)
        self.assertLess(image_bytes(bodies[1]), image_bytes(bodies[0]))

    def test_413_without_figures_assumes_the_7000_cap(self):
        err = urllib.error.HTTPError("u", 413, "Payload Too Large", {}, io.BytesIO(b'{"error":{}}'))
        r, bodies, _, _ = run_review([err, PASS])
        self.assertTrue(r["ok"], r)
        self.assertLess(ug.predict_tokens(bodies[1]), 7000)

    def test_ledger_keeps_the_shrink_and_retryable_fields(self):
        r, _, _, _ = run_review([too_large()] * 6)
        verdict = {"checked_at": "t", "platform": "tiktok", "file": "f.mp4", "sha256": "x", "head": "h",
                   "ok": False, "failures": ["AI review unavailable: x"], "warnings": [],
                   "gate_version": ug.GATE_VERSION, "report": {}, "ai": r}
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "log.json")
            ug.record(verdict, p)
            with open(p, encoding="utf-8") as fh:
                e = json.load(fh)["entries"][0]
        self.assertIs(e["ai"]["retryable"], False)
        self.assertEqual(e["ai"]["reason"], "too_large")
        self.assertEqual(e["gate_version"], ug.GATE_VERSION)


class RetryableFlag(unittest.TestCase):
    """Run 39789555396 re-checked a 413 twelve times, 5 min apart. Only an outage that waiting can
    fix is retryable: a 429 without "per day", a 5xx, a timeout."""

    def test_413_is_not_retryable(self):
        r, _, _, _ = run_review([too_large()] * 4, transcript="short " * 100)
        self.assertFalse(r["available"])
        self.assertFalse(r.get("retryable"))
        self.assertTrue(r.get("too_large"))

    def test_daily_quota_is_not_retryable(self):
        r, _, _, _ = run_review([http(429, b'{"error":{"message":"Limit 200000 tokens per day (TPD)"}}')])
        self.assertFalse(r["retryable"])
        self.assertTrue(r["quota"])
        self.assertEqual(r["reason"], "quota")

    def test_server_errors_and_rate_limits_are_retryable(self):
        for code in (429, 500, 502, 503, 504):
            r, bodies, sleeps, _ = run_review([http(code)] * 3)
            self.assertFalse(r["available"])
            self.assertTrue(r.get("retryable"), code)
            self.assertEqual(len(bodies), 3, code)

    def test_network_timeout_is_retryable(self):
        import socket
        for exc in (OSError("timed out"), socket.timeout("timed out"),
                    urllib.error.URLError(socket.timeout("timed out"))):
            r, _, _, _ = run_review([exc] * 3)
            self.assertTrue(r.get("retryable"), exc)
            self.assertEqual(r["reason"], "timeout")

    def test_client_errors_and_bad_replies_are_not_retryable(self):
        r, bodies, _, _ = run_review([http(400)] * 3)
        self.assertFalse(r["retryable"])
        self.assertEqual(len(bodies), 1)
        nonsense = {"choices": [{"message": {"content": "I cannot help"}}]}
        r, _, _, _ = run_review([nonsense] * 3)
        self.assertFalse(r["available"])
        self.assertFalse(r["retryable"])
        self.assertEqual(r["reason"], "no_verdict")

    def test_missing_key_is_not_retryable(self):
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": ""}):
            r = ug.ai_review("v.mp4", "tiktok", {})
        self.assertFalse(r["retryable"])

    def test_success_is_marked_not_retryable(self):
        r, _, _, _ = run_review([PASS])
        self.assertTrue(r["available"])
        self.assertFalse(r["retryable"])


class LedgerScrub(unittest.TestCase):
    """review-bcf F12: upload_gate_log.json is public; AI summaries restated allegations."""

    def test_allegation_text_is_withheld_from_the_public_ledger(self):
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
        # Security review 2026-10-23: every AI-review reason is a fixed code now (the keyword
        # regex was bypassable), so even this harmless detail stays out; the scores stay in.
        self.assertNotIn("V/AFTER:D", raw)
        self.assertNotIn("Theresa Allore", raw)             # blocked entry: no head
        self.assertIn("withheld", raw)
        self.assertIn('"coherence": 4', raw)
        self.assertIn("John Example", verdict["failures"][0])  # in-memory verdict untouched


class PrivacyRule(unittest.TestCase):
    def test_prompt_blocks_private_homes_and_legible_plates(self):
        _, bodies, _, _ = run_review([PASS])
        t = text_of(bodies[0])
        self.assertIn("PRIVACY", t)
        self.assertIn("number plate", t)


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



if __name__ == "__main__":
    unittest.main()
