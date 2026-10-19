"""Alerts also go to Bot HQ (shadow mode: Telegram keeps sending). Offline.

    py -m unittest tests.test_app_wiring -v
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app_events  # noqa: E402
import main_documentary as md  # noqa: E402
import tests.test_retry_until_pass as base  # noqa: E402
from tests.test_retry_until_pass import _story  # noqa: E402


class _Emits:
    def start_emits(self):
        self.emits = []
        p = mock.patch.object(app_events, "emit", lambda *a, **k: self.emits.append((a, k)) or True)
        p.start()
        self.addCleanup(p.stop)

    def kinds(self):
        return [(a[1], a[2]) for a, k in self.emits]


class TestAutopostEmits(unittest.TestCase, _Emits):
    def _post(self, result):
        import tiktok_buffer
        self.start_emits()
        alerts = []
        with mock.patch.object(tiktok_buffer, "is_configured", lambda: True), \
                mock.patch.object(tiktok_buffer, "post_video", lambda *a, **k: result), \
                mock.patch.object(md, "_log_post", lambda *a: None):
            md._autopost_tiktok("v.mp4", "cap", _story(1), alerts.append, lambda t: t)
        return alerts

    def test_posted_is_info_and_telegram_still_sends(self):
        alerts = self._post((True, "buffer-post-77"))
        self.assertEqual(self.kinds(), [("posted", "info")])
        a, k = self.emits[0]
        self.assertEqual(a[0], "bcf")
        self.assertIn("Case 1", a[3])
        self.assertEqual(k["data"], {"buffer_id": "buffer-post-77"})
        self.assertEqual(len(alerts), 1)

    def test_failure_is_warn_with_advice(self):
        self._post((False, "Buffer HTTP 500"))
        self.assertEqual(self.kinds(), [("post_failed", "warn")])
        self.assertIn("catch-up run", self.emits[0][0][4])


class TestRunEmits(unittest.TestCase, _Emits):
    # Borrow the retry harness (not its tests, which would run twice).
    tearDown = base.TestRetryUntilPass.tearDown
    _gate_seq = base.TestRetryUntilPass._gate_seq
    _output = base.TestRetryUntilPass._output

    def setUp(self):
        base.TestRetryUntilPass.setUp(self)
        self.start_emits()

    def test_gate_blocks_group_under_one_title(self):
        block = {"ok": False, "failures": ["AI review: unverified claims"], "ai": {"available": True}}
        self._gate_seq([block] * 2 + [{"ok": True, "failures": []}])
        md.run_cloud_deliver(count=1)
        blocked = [a for a, k in self.emits if a[1] == "gate_blocked"]
        self.assertEqual(len(blocked), 2)
        self.assertEqual({a[3] for a in blocked}, {"BuriedCasefiles: upload gate held a video back"})
        self.assertEqual({a[2] for a in blocked}, {"warn"})

    def test_nothing_made_is_an_action(self):
        quota = {"ok": False, "failures": ["AI review unavailable: quota"],
                 "ai": {"available": False, "quota": True}}
        self._gate_seq([quota] * 5)
        with self.assertRaises(SystemExit):
            md.run_cloud_deliver(count=1)
        self.assertIn(("no_videos", "action"), self.kinds())


if __name__ == "__main__":
    unittest.main()
