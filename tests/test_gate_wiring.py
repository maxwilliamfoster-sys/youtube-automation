"""
Upload gate wiring + review fixes (2026-10-16), offline: no Groq, no Buffer, no Telegram.

    py -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import image_generator as ig  # noqa: E402
import main_documentary as md  # noqa: E402
import series_queue  # noqa: E402
import tiktok_buffer  # noqa: E402
import tts_generator  # noqa: E402


class TestCaptionsCoverEveryWord(unittest.TestCase):
    def test_words_whisper_missed_still_get_captions(self):
        script = ("Ricky Reel, a 20-year-old Brunel student, vanished after a night out. "
                  "He was last seen near Options Nightclub — in October.")
        heard = [{"word": w, "start": s, "end": s + 0.3, "duration": 0.3} for w, s in
                 [("Ricky", 0.0), ("Real", 0.4), ("student", 2.5), ("vanished", 3.0),
                  ("nightclub", 8.0), ("October", 9.0)]]
        out = tts_generator._correct_against_script(heard, script)
        words = [w["word"] for w in out]
        self.assertEqual(words, [w for w in script.split() if w != "—"])
        starts = [w["start"] for w in out]
        self.assertEqual(starts, sorted(starts))              # in order, no overlaps back
        self.assertEqual(out[0]["start"], 0.0)
        self.assertLessEqual(out[-1]["end"], 9.3 + 1e-6)

    def test_wrong_audio_is_not_force_fitted(self):
        heard = [{"word": w, "start": i, "end": i + 0.5, "duration": 0.5}
                 for i, w in enumerate("completely different words about the weather".split())]
        out = tts_generator._correct_against_script(heard, "Ricky Reel vanished after a night out.")
        self.assertEqual([w["word"] for w in out], [w["word"] for w in heard])


class TestMoodFootage(unittest.TestCase):
    def test_queries_come_from_the_vetted_list_and_never_repeat(self):
        vetted = {q for _, qs in ig.MOOD_THEMES for q in qs} | set(ig.MOOD_DEFAULT)
        segs = ["On a Saturday afternoon she ate a Sunday roast with her family.",
                "Police searched the woods.", "The trial collapsed.", "He was never seen again.",
                "A sheep farmer found the bag.", "Years passed.", "Nothing.", "Nothing more."]
        qs = ig._generate_pexels_queries("x", segs)
        self.assertEqual(len(qs), len(segs))
        self.assertTrue(set(qs) <= vetted, qs)
        self.assertEqual(len(set(qs)), len(qs))
        self.assertFalse(any(w in " ".join(qs) for w in ("roast", "sheep", "food", "dinner")))


class TestCredits(unittest.TestCase):
    def test_cc_by_photos_are_credited_in_the_caption(self):
        imgs = [{"licence": "CC BY-SA 4.0", "credit": "Jane Doe"},
                {"licence": "CC BY-SA 4.0", "credit": "Jane Doe"},
                {"licence": "CC0", "credit": "public domain"},
                {"licence": "CC BY 2.0", "credit": "Geograph user"}]
        line = md._credit_line(imgs)
        self.assertEqual(line, "📷 Photos: Jane Doe (CC BY-SA 4.0); Geograph user (CC BY 2.0) via Wikimedia Commons")
        self.assertEqual(md._credit_line([{"licence": "Pexels", "credit": ""}]), "")


class TestSeriesBlocked(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False).name
        state = {"pending": [{"case_name": "X", "series_part": 2, "series_total": 3},
                             {"case_name": "X", "series_part": 3, "series_total": 3}]}
        with open(self.tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        mock.patch.object(series_queue, "_load", self.load).start()
        mock.patch.object(series_queue, "_save", self.save).start()

    def load(self):
        with open(self.tmp, encoding="utf-8") as fh:
            return json.load(fh)

    def save(self, state):
        with open(self.tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh)

    def tearDown(self):
        mock.patch.stopall()
        os.remove(self.tmp)

    def pending(self):
        return self.load()["pending"]

    def test_second_block_drops_the_rest_of_the_series(self):
        part = {"case_name": "X", "series_part": 2, "from_series_queue": True}
        series_queue.mark_blocked(part)
        self.assertEqual(len(self.pending()), 2)              # one more try
        series_queue.mark_blocked(part)
        self.assertEqual(self.pending(), [])

    def test_permanent_block_drops_at_once(self):
        series_queue.mark_blocked({"case_name": "X", "series_part": 2, "from_series_queue": True},
                                  permanent=True)
        self.assertEqual(self.pending(), [])


class TestHostingAndGate(unittest.TestCase):
    def test_prune_by_upload_order_not_name(self):
        order = ["Zeta_case.mp4", "Alpha_case.mp4", "Mid_case.mp4"]
        delete, new = tiktok_buffer.prune_plan(set(order), order, "Beta.mp4", keep=3)
        self.assertEqual(delete, ["Zeta_case.mp4"])           # oldest upload, not "Alpha"
        self.assertEqual(new, ["Alpha_case.mp4", "Mid_case.mp4", "Beta.mp4"])

    def test_post_video_refuses_a_file_the_gate_never_approved(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as fh:
            fh.write(b"never checked")
        try:
            with mock.patch.object(tiktok_buffer, "find_channel") as fc, \
                    mock.patch.object(tiktok_buffer, "host_video") as host:
                ok, detail = tiktok_buffer.post_video(fh.name, "caption")
            self.assertFalse(ok)
            self.assertTrue(detail.startswith("BLOCKED by upload gate"))
            fc.assert_not_called()
            host.assert_not_called()
        finally:
            os.remove(fh.name)

    def test_gate_profile_requires_ai_review_and_ai_label(self):
        p = md._gate_profile()
        self.assertEqual(p["ai_review"], "required")
        self.assertTrue(p["ai_realistic"])
        self.assertIsNone(md._gate_profile(sample=True)["ledger"])


if __name__ == "__main__":
    unittest.main()


class TestGitErrors(unittest.TestCase):
    def test_git_error_hides_token(self):
        bad = mock.Mock(returncode=1, stdout="",
                        stderr="fatal: https://x-access-token:SECRET123@github.com/o/r.git denied")
        with mock.patch.object(tiktok_buffer.subprocess, "run", return_value=bad), \
                mock.patch.dict(os.environ, {"GITHUB_TOKEN": "SECRET123"}):
            with self.assertRaises(tiktok_buffer.PublishError) as cm:
                tiktok_buffer._git(["push", "origin", "media"], ".")
        self.assertNotIn("SECRET123", str(cm.exception))
        self.assertIn("***", str(cm.exception))
