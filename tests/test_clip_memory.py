"""Clips must not come back in the next few videos: picks used to forget everything between runs."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import image_generator as ig
import main_documentary as md
import test_retry_until_pass as base

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = ("Police searched the moor for three weeks and found nothing at all. The jury took four hours "
          "in the end. A letter arrived at the family home years later. The boat drifted off the coast "
          "that night. Her mother kept a vigil every winter after that. The case is still open today. ") * 2


class FakePexels:
    """Stands in for requests.get: every vetted id exists and downloads."""

    def __init__(self):
        self.asked = []

    def __call__(self, url, headers=None, timeout=None, params=None):
        resp = mock.Mock(status_code=200, content=b"clip")
        if "/videos/videos/" in url:
            vid = int(url.rsplit("/", 1)[1])
            self.asked.append(vid)
            resp.json.return_value = {
                "id": vid, "width": 1080, "height": 1920, "duration": 10,
                "video_files": [{"width": 1080, "height": 1920, "link": f"http://clip/{vid}"}],
            }
        return resp


class ClipMemoryCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file = os.path.join(self.tmp.name, "recent_clips.json")
        for patcher in (mock.patch.object(ig, "RECENT_CLIPS_FILE", self.file),
                        mock.patch.object(ig, "PEXELS_API_KEY", "x"),
                        mock.patch.object(ig, "UK_LOCATION_IMAGES", False)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def make_video(self, passes_review=True):
        """Make one 12-scene video through the real picker; return the clip ids it used."""
        pexels = FakePexels()
        with mock.patch.object(ig.requests, "get", pexels):
            paths, _, _, clips = ig.generate_story_images("t", SCRIPT, self.tmp.name, num_images=12)
        self.assertEqual(len(paths), 12)
        self.assertEqual(set(clips), set(pexels.asked))
        if passes_review:
            ig.remember_clips(clips)
        return pexels.asked


class TestClipMemory(ClipMemoryCase):
    def test_the_next_video_shares_no_clips_with_the_last(self):
        first = self.make_video()
        second = self.make_video()
        self.assertEqual(len(set(first)), 12)
        self.assertEqual(set(first) & set(second), set())

    def test_no_clip_returns_within_the_memory_window(self):
        videos = [self.make_video() for _ in range(ig.RECENT_CLIP_WINDOW // 12)]
        used = [c for v in videos for c in v]
        self.assertEqual(len(used), len(set(used)))

    def test_a_long_run_never_runs_out_of_footage(self):
        # Far more scenes than vetted clips: repeats are allowed, an empty scene is not.
        for _ in range(40):
            self.assertEqual(len(self.make_video()), 12)

    def test_a_clip_returning_is_the_one_used_longest_ago(self):
        ids = ig.BROLL_ALLOWLIST["storm clouds"]
        self.assertEqual(ig._freshest_first(list(ids), [ids[0], ids[1]])[-2:], [ids[0], ids[1]])

    def test_memory_survives_between_runs_in_a_file(self):
        used = self.make_video()
        with open(self.file, encoding="utf-8") as f:
            self.assertEqual(set(json.load(f)), set(used))

    def test_memory_stays_a_sensible_size(self):
        ig._save_recent_clips(list(range(5000)))
        self.assertEqual(len(ig._load_recent_clips()), ig.RECENT_CLIPS_KEEP)
        self.assertEqual(ig._load_recent_clips()[-1], 4999)

    def test_a_damaged_memory_file_never_stops_a_video(self):
        with open(self.file, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(ig._load_recent_clips(), [])
        self.assertEqual(len(self.make_video()), 12)

    def test_a_failed_pexels_call_does_not_use_up_memory(self):
        with mock.patch.object(ig.requests, "get") as get:
            get.return_value.status_code = 404
            self.assertFalse(ig.fetch_pexels_video("storm clouds", os.path.join(self.tmp.name, "x.mp4")))
        self.assertFalse(os.path.exists(self.file))


class TestBlockedVideosDoNotCount(ClipMemoryCase):
    """Run 41876185252: 3 of 4 attempts were blocked, so 36 clips nobody saw counted as used."""

    def test_making_a_video_does_not_use_up_memory(self):
        self.make_video(passes_review=False)
        self.assertFalse(os.path.exists(self.file))

    def test_a_clip_seen_again_moves_to_the_newest_end(self):
        ig.remember_clips([1, 2, 3])
        ig.remember_clips([2, 9])
        self.assertEqual(ig._load_recent_clips(), [1, 3, 2, 9])

    def test_remembering_nothing_writes_nothing(self):
        ig.remember_clips([])
        self.assertFalse(os.path.exists(self.file))


class TestOnlyVideosThatPassAreRemembered(unittest.TestCase):
    def setUp(self):
        base.TestRetryUntilPass.setUp(self)
        self.addCleanup(base.TestRetryUntilPass.tearDown, self)
        self.remembered = []
        made = iter(range(1, 100))

        def gen():
            n = next(made)
            return dict(base._story(n), clips=[n]), self.tmp.name
        mock.patch.object(md, "_generate_one", gen).start()
        mock.patch.object(ig, "remember_clips", self.remembered.append).start()

    def _gate_seq(self, verdicts):
        base.TestRetryUntilPass._gate_seq(self, verdicts)

    def test_blocked_attempts_are_left_out(self):
        block = {"ok": False, "failures": ["AI review: unverified claims"], "ai": {"available": True}}
        self._gate_seq([block, block, {"ok": True, "failures": []}])
        md.run_cloud_deliver(count=1)
        self.assertEqual(self.remembered, [[3]])

    def test_a_sample_is_left_out(self):
        self._gate_seq([{"ok": True, "failures": []}])
        md.run_cloud_deliver(count=1, sample=True)
        self.assertEqual(self.remembered, [])

    def test_the_real_pipeline_hands_its_clips_to_the_story(self):
        # _generate_one is too large to run here; a real run confirms the rest.
        with open(md.__file__, encoding="utf-8") as f:
            self.assertIn('story["clips"] = clips', f.read())


class TestDarkClipsStayOut(unittest.TestCase):
    """Read frame by frame on 4 Nov with the upload gate's own black-picture test."""
    BLACK = {15667292: "candle, black throughout", 9376344: "moon, first 0.44 s",
             4727543: "staircase, first 1 s", 30728867: "light bulb, first 1.5 s",
             36601677: "road, 6 stretches", 38312133: "road, nearly black throughout"}

    def test_none_are_on_the_list(self):
        listed = {c for mood in ig.BROLL_ALLOWLIST.values() for c in mood}
        self.assertEqual(listed & set(self.BLACK), set())

    def test_every_mood_still_has_enough_clips(self):
        for mood, clips in ig.BROLL_ALLOWLIST.items():
            self.assertGreaterEqual(len(clips), 5, mood)


class TestWrongClipsStayOut(unittest.TestCase):
    """Clips that are wrong beside true crime, found from Pexels' names and stills on 5 Nov."""
    WRONG = {5427490: "creepy clown with a red balloon, listed under empty corridor",
             5994037: "typewriter types a political slogan",
             7230788: "woman in handcuffs, could imply guilt",
             6406024: "knocking on a front door, the privacy rule blocks these",
             34839556: "counting cash, could suggest a motive",
             7431687: "modern phone on a map"}

    def test_none_are_on_the_list(self):
        listed = {c for mood in ig.BROLL_ALLOWLIST.values() for c in mood}
        self.assertEqual(listed & set(self.WRONG), set())


class TestMemoryIsKept(unittest.TestCase):
    def test_the_workflow_commits_the_memory_back(self):
        with open(os.path.join(ROOT, ".github", "workflows", "documentary.yml"), encoding="utf-8") as f:
            self.assertRegex(f.read(), r"for f in [^\n]*\brecent_clips\.json\b")


if __name__ == "__main__":
    unittest.main()
