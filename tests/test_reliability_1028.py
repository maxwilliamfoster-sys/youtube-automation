"""Fixes from the 2026-10-28 failure review (19 of 23 runs since 10-24 delivered nothing)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import case_screen
import config
import image_generator as ig
import video_composer


class TestVettedBroll(unittest.TestCase):
    def test_every_mood_query_has_a_vetted_clip_list(self):
        queries = {q for _, qs in ig.MOOD_THEMES for q in qs} | set(ig.MOOD_DEFAULT)
        missing = sorted(q for q in queries if len(ig.BROLL_ALLOWLIST.get(q, [])) < 3)
        self.assertEqual(missing, [])

    def test_risky_queries_are_gone(self):
        queries = {q for _, qs in ig.MOOD_THEMES for q in qs} | set(ig.MOOD_DEFAULT)
        for bad in ("abandoned house", "empty street at night", "car headlights at night",
                    "motorway at night", "streetlight at night", "wet pavement at night"):
            self.assertNotIn(bad, queries)

    def test_unvetted_query_is_never_searched(self):
        with mock.patch.object(ig, "PEXELS_API_KEY", "x"), mock.patch.object(ig.requests, "get") as get:
            self.assertFalse(ig.fetch_pexels_video("sunny beach party", "out.mp4"))
            get.assert_not_called()

    def test_fetch_asks_for_a_vetted_id(self):
        with mock.patch.object(ig, "PEXELS_API_KEY", "x"), \
                mock.patch.object(ig.requests, "get") as get:
            get.return_value.status_code = 404
            ig.fetch_pexels_video("storm clouds", "out.mp4")
            url = get.call_args_list[0].args[0]
            vid = int(url.rsplit("/", 1)[1])
            self.assertIn(vid, ig.BROLL_ALLOWLIST["storm clouds"])

    def test_real_place_photos_are_off(self):
        self.assertFalse(config.UK_LOCATION_IMAGES)


class TestCardText(unittest.TestCase):
    def test_no_newline_inside_any_drawtext(self):
        # A newline inside one drawtext drew a box glyph at each line end on CI FFmpeg 6.1.
        s = video_composer.build_filter_script(
            [{"text": "hello", "start": 0, "end": 1}], "scale=1080:1920",
            hook_text="Two police officers crossed paths with poachers and never returned",
            cta_text="Do you know this case? Comment below.", total_duration=30)
        for part in s.split(",drawtext=")[1:]:
            self.assertNotIn("\n", part)
        self.assertEqual(s.count("between(t,0,1.8)"), 4)        # 4 hook lines

    def test_long_hook_stays_above_the_captions(self):
        s = video_composer.build_filter_script(
            [], "scale=1080:1920",
            hook_text="one two three four five six seven eight nine ten eleven twelve thirteen")
        ys = [int(p.split(":y=")[1].split(":")[0]) for p in s.split(",drawtext=")[1:]]
        cap_y = int(video_composer.VIDEO_HEIGHT * video_composer.CAPTION_POSITION)
        self.assertLess(max(ys) + 76, cap_y)


class TestFullTextScreen(unittest.TestCase):
    def test_acquitted_person_deep_in_article_is_skipped(self):
        text = ("In 1990 Smith was convicted of the murder. " + "Detail. " * 400 +
                "A second man, Jones, was charged but acquitted at trial.")
        self.assertEqual(case_screen.screen_text("Murder of X", text), "")     # intro looks fine
        self.assertEqual(case_screen.screen_full_text(text), "unconvicted_suspect")

    def test_not_proven_and_never_charged(self):
        self.assertTrue(case_screen.screen_full_text("The verdict was not proven."))
        self.assertTrue(case_screen.screen_full_text("Her stepfather was never charged."))

    def test_clean_solved_case_passes(self):
        self.assertEqual(case_screen.screen_full_text(
            "In 2004 Brown was found guilty of murder and sentenced to life imprisonment."), "")


class KeyWordCaptions(unittest.TestCase):
    def test_numbers_and_mid_sentence_names_are_gold(self):
        from video_composer import _is_key_word
        for word, prev in (("1951", "in"), ("Nineteen", "in"), ("Six-year-old", "."), ("Albino", "Luis"), ("Oakland,", "in")):
            self.assertTrue(_is_key_word(word, prev), word)

    def test_plain_words_and_sentence_starts_stay_white(self):
        from video_composer import _is_key_word
        for word, prev in (("vanished", "Luis"), ("Police", "gone."), ("She", "and"), ("The", "of")):
            self.assertFalse(_is_key_word(word, prev), word)


class ColdOpenDates(unittest.TestCase):
    CASE = {"case_name": "Ann Lee", "location": "Leeds", "year": 1978}

    def test_date_openings_are_rejected(self):
        import story_generator as sg
        for line in ("On 3 December 1926, her car was found.", "In 2020, two sisters left Leeds.", "On third November, 1978, Ann Lee left.",
                     "On the twenty-third of May, Ann Lee left.", "Back in 1978 Ann Lee left Leeds.", "On Tuesday Ann Lee left Leeds."):
            self.assertTrue(any("dated_open" in i for i in sg._cold_open_issues(line, self.CASE)), line)

    def test_fact_led_openings_pass_the_date_rule(self):
        import story_generator as sg
        for line in ("Ann Lee vanished from Leeds in 1978.", "In Leeds, one car was left running.", "On her desk, 3 letters stayed unopened."):
            self.assertFalse(any("dated_open" in i for i in sg._cold_open_issues(line, self.CASE)), line)

    def test_lurid_openings_are_rejected(self):
        import story_generator as sg
        self.assertTrue(any("lurid_open" in i for i in sg._cold_open_issues("Ann Lee's body was found in Leeds, naked.", self.CASE)))
        self.assertFalse(any("lurid_open" in i for i in sg._cold_open_issues("Ann Lee vanished from Leeds in 1978.", self.CASE)))


class NarrationTempo(unittest.TestCase):
    def test_speed_up_shortens_audio_and_tempo_one_is_a_no_op(self):
        import subprocess
        import tempfile
        import chatterbox_narrator as cn
        from video_composer import FFMPEG
        with tempfile.TemporaryDirectory() as d:
            wav = os.path.join(d, "a.wav")
            subprocess.run([FFMPEG, "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=6", wav], capture_output=True, check=True)
            self.assertEqual(cn._speed_up(wav, 1.0, 6.0), 6.0)
            self.assertAlmostEqual(cn._speed_up(wav, 1.2, 6.0), 5.0)
            out = subprocess.run([FFMPEG, "-hide_banner", "-i", wav], capture_output=True, text=True).stderr
            self.assertIn("Duration: 00:00:05", out)

    def test_failed_ffmpeg_keeps_the_original(self):
        import chatterbox_narrator as cn
        self.assertEqual(cn._speed_up("/no/such/file.wav", 1.2, 6.0), 6.0)


if __name__ == "__main__":
    unittest.main()
