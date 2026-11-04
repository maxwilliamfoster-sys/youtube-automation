"""Clips must not come back in the next few videos: picks used to forget everything between runs."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import image_generator as ig

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

    def make_video(self):
        """Make one 12-scene video through the real picker; return the clip ids it used."""
        pexels = FakePexels()
        with mock.patch.object(ig.requests, "get", pexels):
            paths, _, _ = ig.generate_story_images("t", SCRIPT, self.tmp.name, num_images=12)
        self.assertEqual(len(paths), 12)
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


class TestMemoryIsKept(unittest.TestCase):
    def test_the_workflow_commits_the_memory_back(self):
        with open(os.path.join(ROOT, ".github", "workflows", "documentary.yml"), encoding="utf-8") as f:
            self.assertRegex(f.read(), r"for f in [^\n]*\brecent_clips\.json\b")


if __name__ == "__main__":
    unittest.main()
