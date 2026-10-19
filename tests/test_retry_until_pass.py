"""run_cloud_deliver keeps making videos until one passes the upload gate."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main_documentary as md  # noqa: E402
import notify  # noqa: E402
import publish_checks  # noqa: E402
import series_queue  # noqa: E402
import upload_gate  # noqa: E402


def _story(n):
    return {"title": f"Case {n}", "script": "words", "caption": "cap", "images": [],
            "compliance": {"verdict": "OK"}, "legal": {"ok": True}}


class TestRetryUntilPass(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        self.tmp.write(b"x"); self.tmp.close()
        self.out = tempfile.NamedTemporaryFile(delete=False); self.out.close()
        self.made = 0

        def gen():
            self.made += 1
            return _story(self.made), self.tmp.name
        self.sent, self.posted = [], []
        for target, attr, val in [
            (md, "_generate_one", gen),
            (md, "_fit_telegram_limit", lambda p: p),
            (md, "_autopost_tiktok", lambda *a: self.posted.append(a[2]["title"]) or True),
            (md, "AI_OUTAGE_WAIT_S", 0),
            (notify, "send_alert", lambda *a, **k: True),
            (notify, "send_video", lambda p, cap, **k: self.sent.append(cap) or True),
            (publish_checks, "run_all", lambda *a: {"ok": True, "attribution": "", "blocking": []}),
            (series_queue, "mark_blocked", lambda *a, **k: None),
            (series_queue, "mark_delivered", lambda *a, **k: None),
            (upload_gate, "block_message", lambda v, t: "blocked"),
            (upload_gate, "summary", lambda v: "ok"),
            (md.time, "sleep", lambda s: None),
        ]:
            mock.patch.object(target, attr, val).start()
        mock.patch.dict(os.environ, {"GITHUB_OUTPUT": self.out.name}).start()
        import video_composer
        mock.patch.object(video_composer, "get_video_duration", lambda p: 50.0).start()

    def tearDown(self):
        mock.patch.stopall()
        os.remove(self.tmp.name); os.remove(self.out.name)

    def _gate_seq(self, verdicts):
        it = iter(verdicts)
        mock.patch.object(upload_gate, "check", lambda *a, **k: next(it)).start()

    def _output(self):
        return open(self.out.name, encoding="utf-8").read()

    def test_four_blocked_videos_then_a_pass_is_posted(self):
        block = {"ok": False, "failures": ["AI review: unverified claims"], "ai": {"available": True}}
        self._gate_seq([block] * 4 + [{"ok": True, "failures": []}])
        md.run_cloud_deliver(count=1)
        self.assertEqual(self.made, 5)
        self.assertEqual(self.posted, ["Case 5"])
        self.assertIn("catchup=false", self._output())

    def test_ai_outage_rechecks_the_same_video(self):
        down = {"ok": False, "failures": ["AI review unavailable: HTTP 503"], "ai": {"available": False}}
        self._gate_seq([down] * 3 + [{"ok": True, "failures": []}])
        md.run_cloud_deliver(count=1)
        self.assertEqual(self.made, 1)              # same video, asked again
        self.assertEqual(self.posted, ["Case 1"])

    def test_spent_quota_stops_without_a_catchup(self):
        quota = {"ok": False, "failures": ["AI review unavailable: quota"],
                 "ai": {"available": False, "quota": True}}
        self._gate_seq([quota] * 5)
        with self.assertRaises(SystemExit):
            md.run_cloud_deliver(count=1)
        self.assertEqual(self.made, 1)
        self.assertIn("catchup=false", self._output())

    def test_out_of_time_asks_for_a_catchup_run(self):
        block = {"ok": False, "failures": ["video check: black frames"], "ai": None}
        self._gate_seq([block] * 50)
        with mock.patch.object(md, "CLOUD_BUDGET_S", 0), self.assertRaises(SystemExit):
            md.run_cloud_deliver(count=1)
        self.assertEqual(self.made, 0)          # no render that couldn't finish posting
        self.assertIn("catchup=true", self._output())

    def test_budget_reserves_posting_time(self):
        late = md.CLOUD_BUDGET_S - md.POST_RESERVE_S - 60
        clock = iter([0.0] + [float(late)] * 50)
        self._gate_seq([{"ok": True, "failures": []}] * 5)
        with mock.patch.object(md.time, "monotonic", lambda: next(clock)),                 mock.patch.object(md, "CLOUD_FIRST_RENDER_S", 120), self.assertRaises(SystemExit):
            md.run_cloud_deliver(count=1)
        self.assertEqual(self.made, 0)
        self.assertIn("catchup=true", self._output())

    def test_long_ai_outage_stops_and_asks_for_catchup(self):
        down = {"ok": False, "failures": ["AI review unavailable: HTTP 503"], "ai": {"available": False}}
        self._gate_seq([down] * 50)
        alerts = []
        with mock.patch.object(notify, "send_alert", lambda t, **k: alerts.append(t) or True),                 self.assertRaises(SystemExit):
            md.run_cloud_deliver(count=1)
        self.assertEqual(self.made, 1)          # the same video, re-checked; no new renders
        self.assertIn("catchup=true", self._output())
        self.assertTrue(any("AI reviewer" in a for a in alerts))

    def test_attempt_cap_bounds_a_broken_pipeline(self):
        block = {"ok": False, "failures": ["video check: black frames"], "ai": None}
        self._gate_seq([block] * 50)
        with mock.patch.object(md, "CLOUD_FIRST_RENDER_S", 0), self.assertRaises(SystemExit):
            md.run_cloud_deliver(count=1)
        self.assertEqual(self.made, md.CLOUD_MAX_ATTEMPTS + 1)

    def test_same_infrastructure_failure_three_times_stops(self):
        broken = {"ok": False, "failures": ["Video check could not run: ffprobe missing"], "ai": None}
        self._gate_seq([broken] * 50)
        with mock.patch.object(md, "CLOUD_FIRST_RENDER_S", 0), self.assertRaises(SystemExit):
            md.run_cloud_deliver(count=1)
        self.assertEqual(self.made, 3)
        self.assertIn("catchup=false", self._output())

    def test_same_crash_three_times_stops(self):
        def boom():
            self.made += 1
            raise RuntimeError("No such file or directory: 'ffmpeg'")
        with mock.patch.object(md, "_generate_one", boom), \
                mock.patch.object(md, "CLOUD_FIRST_RENDER_S", 0), self.assertRaises(SystemExit):
            md.run_cloud_deliver(count=1)
        self.assertEqual(self.made, 3)
        self.assertIn("catchup=false", self._output())

    def test_buffer_failure_is_not_delivered(self):
        import tiktok_buffer
        self._gate_seq([{"ok": True, "failures": []}] * 50)
        with mock.patch.object(tiktok_buffer, "is_configured", lambda: True), \
                mock.patch.object(md, "_autopost_tiktok", lambda *a: False), \
                mock.patch.object(md, "CLOUD_MAX_ATTEMPTS", 0), self.assertRaises(SystemExit):
            md.run_cloud_deliver(count=1)
        self.assertTrue(self.sent)                  # Telegram got it...
        self.assertIn("catchup=true", self._output())   # ...but nothing was posted

    def test_telegram_only_mode_counts_telegram_delivery(self):
        import tiktok_buffer
        self._gate_seq([{"ok": True, "failures": []}])
        with mock.patch.object(tiktok_buffer, "is_configured", lambda: False), \
                mock.patch.object(md, "_autopost_tiktok", lambda *a: False):
            md.run_cloud_deliver(count=1)
        self.assertIn("catchup=false", self._output())

    def test_only_first_block_sends_video(self):
        block = {"ok": False, "failures": ["AI review: unrelated footage"], "ai": {"available": True}}
        self._gate_seq([block] * 3 + [{"ok": True, "failures": []}])
        alerts = []
        with mock.patch.object(notify, "send_alert", lambda t, **k: alerts.append(t) or True):
            md.run_cloud_deliver(count=1)
        self.assertEqual(len(self.sent), 2)          # first block + the posted video
        self.assertEqual(alerts.count("blocked"), 3)  # every block still explained

    def test_gate_profile_caps_daily_ai_reviews(self):
        self.assertEqual(md._gate_profile()["ai_daily_cap"], 20)


if __name__ == "__main__":
    unittest.main()
