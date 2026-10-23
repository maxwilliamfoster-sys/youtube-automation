"""
Audit 2026-10-21 #7: zoompan at 30 fps on a 24/25 fps Pexels clip came out ~20% short,
so the background ended seconds before the narration and the gate blocked finished
videos ("could not decode a frame at 48.3s"). Every b-roll clip must be exactly the
requested length, and the background must outlast the audio by 0.5 s.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import documentary_composer as dc  # noqa: E402
from video_composer import FFPROBE, get_stream_durations  # noqa: E402

FRAME = 1.0 / dc.VIDEO_FPS


def _src(path, seconds, fps):
    subprocess.run([dc.FFMPEG, "-y", "-f", "lavfi", "-i",
                    f"testsrc=size=320x568:rate={fps}:duration={seconds}",
                    "-pix_fmt", "yuv420p", path], capture_output=True, check=True)


def _frames(path):
    out = subprocess.run([FFPROBE, "-v", "error", "-count_frames", "-select_streams",
                          "v:0", "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0",
                          path], capture_output=True, text=True)
    return int(out.stdout.strip() or 0)


class BrollLengthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # Small frame so the test is fast; frame counts do not depend on size.
        for attr, val in (("VIDEO_WIDTH", 108), ("VIDEO_HEIGHT", 192)):
            p = mock.patch.object(dc, attr, val)
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def _p(self, name):
        return os.path.join(self.tmp.name, name)

    def test_24fps_source_gives_exact_length(self):
        _src(self._p("s24.mp4"), 5.2, 24)
        dc.make_broll_clip(self._p("s24.mp4"), 5.0, self._p("c24.mp4"))
        v, _ = get_stream_durations(self._p("c24.mp4"))
        self.assertAlmostEqual(v, 5.0, delta=FRAME + 0.001)
        self.assertEqual(_frames(self._p("c24.mp4")), 150)

    def test_short_25fps_source_is_looped_to_length(self):
        _src(self._p("s25.mp4"), 2.0, 25)
        dc.make_broll_clip(self._p("s25.mp4"), 4.6, self._p("c25.mp4"))
        v, _ = get_stream_durations(self._p("c25.mp4"))
        self.assertAlmostEqual(v, 4.6, delta=FRAME + 0.001)

    def test_every_source_is_looped(self):
        _src(self._p("s.mp4"), 6.0, 24)
        calls = []
        real = subprocess.run

        def spy(cmd, *a, **k):
            calls.append(cmd)
            return real(cmd, *a, **k)
        with mock.patch.object(dc.subprocess, "run", spy):
            dc.make_broll_clip(self._p("s.mp4"), 3.0, self._p("c.mp4"))
        cmd = next(c for c in calls if isinstance(c, list) and "-vf" in c)
        self.assertIn("-stream_loop", cmd)
        self.assertLess(cmd.index("-stream_loop"), cmd.index("-i"))
        vf = cmd[cmd.index("-vf") + 1]
        self.assertTrue(vf.startswith(f"fps={dc.VIDEO_FPS},"), vf)

    def test_pad_video_to_clones_last_frame(self):
        _src(self._p("short.mp4"), 4.0, 30)
        dc._pad_video_to(self._p("short.mp4"), 5.0)
        v, _ = get_stream_durations(self._p("short.mp4"))
        self.assertAlmostEqual(v, 5.0, delta=FRAME + 0.001)

    def test_background_slightly_short_is_padded(self):
        _src(self._p("bg.mp4"), 4.8, 30)
        dc._ensure_background_covers(self._p("bg.mp4"), 4.5)
        v, _ = get_stream_durations(self._p("bg.mp4"))
        self.assertGreaterEqual(v, 5.0 - FRAME)

    def test_background_far_too_short_fails_before_the_gate(self):
        _src(self._p("bg2.mp4"), 3.0, 30)
        with self.assertRaises(RuntimeError):
            dc._ensure_background_covers(self._p("bg2.mp4"), 4.5)

    def test_background_long_enough_is_untouched(self):
        _src(self._p("bg3.mp4"), 6.0, 30)
        before = os.path.getmtime(self._p("bg3.mp4"))
        dc._ensure_background_covers(self._p("bg3.mp4"), 4.5)
        self.assertEqual(os.path.getmtime(self._p("bg3.mp4")), before)


if __name__ == "__main__":
    unittest.main()
