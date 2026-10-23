"""
Reliability fixes of 2026-10-12: case pre-filter + rejected-case memory, oversized
image guard, committed calm music with graceful fallback.

Run:  py -m unittest discover -s tests -v
No network, no LLM calls, nothing is posted.
"""

import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import case_screen  # noqa: E402
import image_safety  # noqa: E402


class ScreenTextTests(unittest.TestCase):
    def test_child_murder_is_flagged(self):
        t = "Murder of X"
        x = "On 5 May 1997, X, a 9-year-old girl, was found murdered near her home in Kent."
        self.assertEqual(case_screen.screen_text(t, x), "minor_victim")

    def test_child_disappearance_without_killing_passes(self):
        t = "Disappearance of Y"
        x = ("Y is an English missing person. A 14-year-old schoolboy, he vanished in 2007 "
             "after travelling to London. He has never been found and no one has been charged.")
        self.assertEqual(case_screen.screen_text(t, x), "")

    def test_sexual_violence_in_lead(self):
        x = "The murder of Z occurred in 1990, when Z, a 73-year-old, was sexually assaulted and murdered."
        self.assertEqual(case_screen.screen_text("Murder of Z", x), "sexual_violence")

    def test_suspect_record_later_in_intro_is_not_sexual_violence(self):
        x = ("W is an English estate agent who was reported missing in 1986, aged 25. Her body "
             "has never been found and she was declared dead in 1994. " + "Filler text. " * 30 +
             "A man who was later convicted of an unrelated murder and was a sex offender "
             "was jailed for life.")
        self.assertEqual(case_screen.screen_text("Disappearance of W", x), "")

    def test_gore(self):
        x = "The case concerns the dismembered remains of a woman found in 1964 in a river."
        self.assertEqual(case_screen.screen_text("Torso case", x), "gore")

    def test_no_one_charged_is_not_active_proceedings(self):
        x = "V vanished in 1975 while hitchhiking. No one has been charged in connection with it."
        self.assertEqual(case_screen.screen_text("Disappearance of V", x), "")

    def test_awaiting_trial_is_active_proceedings(self):
        x = "U was killed in 2019. A man has been charged with murder and is awaiting trial."
        self.assertEqual(case_screen.screen_text("Killing of U", x), "active_proceedings")

    def test_acquitted_suspect_with_no_conviction(self):
        x = ("T was murdered in 1980 in her home in Leeds. Her husband was tried for the murder "
             "and acquitted. The case remains unsolved.")
        self.assertEqual(case_screen.screen_text("Murder of T", x), "unconvicted_suspect")

    def test_acquittal_then_real_killer_convicted_passes_regex(self):
        x = ("S was murdered in 1988 in Leeds. A neighbour was acquitted in 1990. In 2010 DNA "
             "evidence identified another man, who was convicted of the murder.")
        self.assertEqual(case_screen.screen_text("Murder of S", x), "")


class RejectedMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "rejected_cases.json")
        self.p1 = mock.patch.object(case_screen, "REJECTED_FILE", self.path)
        self.p1.start()
        case_screen._cache = None

    def tearDown(self):
        self.p1.stop()
        case_screen._cache = None
        self.tmp.cleanup()

    def test_hard_stage_excluded_immediately_and_persisted(self):
        self.assertFalse(case_screen.is_rejected("Murder of A"))
        case_screen.record_rejection("Murder of A", "legal", "named suspect")
        self.assertTrue(case_screen.is_rejected("Murder of A"))
        case_screen._cache = None                       # reload from disk
        self.assertTrue(case_screen.is_rejected("Murder of A"))

    def test_soft_stage_needs_two_strikes(self):
        case_screen.record_rejection("Murder of B", "empty_script")
        self.assertFalse(case_screen.is_rejected("Murder of B"))
        case_screen.record_rejection("Murder of B", "quality")
        self.assertTrue(case_screen.is_rejected("Murder of B"))

    def test_soft_strike_never_downgrades_hard(self):
        case_screen.record_rejection("Murder of C", "compliance", "AGE_RESTRICTED")
        case_screen.record_rejection("Murder of C", "empty_script")
        self.assertEqual(case_screen.load_rejected()["Murder of C"]["stage"], "compliance")

    def test_public_file_keeps_only_stage_and_code(self):
        """Audit F12: the public file restated allegations against acquitted people."""
        case_screen.record_rejection(
            "Disappearance of K", "legal",
            "The script identifies Terri Moulton, a real person who has not been convicted, "
            "and implicates her in alleged wrongdoing")
        case_screen.record_rejection("Murder of P", "prescreen",
                                     "unconvicted_suspect: names Ram Puneet Tiwary (acquitted)")
        case_screen.record_rejection("Murder of Q", "compliance", "AGE_RESTRICTED: disturbing detail")
        case_screen.record_rejection("Murder of R", "prefilter", "gore")
        case_screen.record_rejection("Murder of S", "quality", "accuracy 4/10")
        with open(self.path, encoding="utf-8") as f:
            raw = f.read()
        for word in ("Moulton", "Tiwary", "acquitted", "implicates", "disturbing", "reason"):
            self.assertNotIn(word, raw)
        data = case_screen.load_rejected()
        self.assertEqual(data["Disappearance of K"]["code"], "unconvicted_suspect")
        self.assertEqual(data["Murder of P"]["code"], "unconvicted_suspect")
        self.assertEqual(data["Murder of Q"]["code"], "age_restricted")
        self.assertEqual(data["Murder of R"]["code"], "gore")
        self.assertEqual(data["Murder of S"]["code"], "low_accuracy")
        for entry in data.values():
            self.assertLessEqual(set(entry), {"stage", "code", "date", "strikes"})
            self.assertRegex(entry["code"], r"^[a-z_]{2,40}$")
        self.assertTrue(case_screen.is_rejected("Disappearance of K"))

    def test_old_entries_with_reasons_are_scrubbed_on_next_save(self):
        import json
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"Disappearance of Kyron Horman": {
                "stage": "legal", "date": "2026-10-12", "strikes": 1,
                "reason": "The script identifies Terri Moulton, offering a landscaper money"}}, f)
        case_screen._cache = None
        case_screen.record_rejection("Murder of New", "prefilter", "gore")
        with open(self.path, encoding="utf-8") as f:
            raw = f.read()
        self.assertNotIn("Moulton", raw)
        self.assertNotIn("landscaper", raw)
        self.assertEqual(case_screen.load_rejected()["Disappearance of Kyron Horman"]["code"],
                         "unconvicted_suspect")

    def test_pick_case_skips_rejected(self):
        import case_source
        case_screen.record_rejection("Murder of Rejected", "legal")
        seen = []

        def fake_summary(title):
            seen.append(title)
            return {"title": title, "extract": "x" * 300, "url": ""}

        with mock.patch.object(case_source, "load_pool",
                               return_value=["Murder of Rejected", "Murder of Fine"]), \
             mock.patch.object(case_source, "get_summary", side_effect=fake_summary), \
             mock.patch.object(case_source, "intrigue_score", return_value=30.0), \
             mock.patch.object(case_source, "monthly_pageviews", return_value=0):
            picked = case_source.pick_case(lambda t: False)
        self.assertEqual(picked["title"], "Murder of Fine")
        self.assertNotIn("Murder of Rejected", seen)


def _png_header_only(width: int, height: int) -> bytes:
    """A PNG whose header claims width x height, with a token IDAT (never decoded)."""
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"\x00")) + chunk(b"IEND", b""))


class ImageGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, data):
        p = os.path.join(self.tmp.name, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def test_normal_image_ok(self):
        from PIL import Image
        p = os.path.join(self.tmp.name, "ok.jpg")
        Image.new("RGB", (1920, 1280), (40, 40, 40)).save(p)
        self.assertEqual(image_safety.oversized_reason(p), "")

    def test_282mp_png_like_2026_10_10_is_refused_without_decoding(self):
        p = self._write("bomb.png", _png_header_only(20000, 14130))   # ~282.6 MP
        self.assertNotEqual(image_safety.oversized_reason(p), "")

    def test_above_our_limit_below_pillows(self):
        p = self._write("big.png", _png_header_only(8000, 6000))      # 48 MP
        self.assertIn("48MP", image_safety.oversized_reason(p))

    def test_pillow_limit_left_enabled(self):
        from PIL import Image
        self.assertIsNotNone(Image.MAX_IMAGE_PIXELS)

    def test_ken_burns_survives_oversized_image(self):
        import documentary_composer as dc
        p = self._write("bomb.png", _png_header_only(20000, 14130))
        out = os.path.join(self.tmp.name, "clip.mp4")
        dc.make_ken_burns_clip(p, 0.3, 0, out)            # must not raise
        self.assertTrue(os.path.getsize(out) > 0)

    def test_commons_query_prefers_thumbnail(self):
        import uk_media
        fake = {"query": {"pages": {
            "1": {"title": "File:Street.jpg", "imageinfo": [{
                "url": "https://upload.wikimedia.org/orig/Street.jpg",
                "thumburl": "https://upload.wikimedia.org/thumb/Street.jpg/1920px-Street.jpg",
                "width": 6000, "height": 4000, "thumbwidth": 1920, "thumbheight": 1280,
                "size": 30_000_000,
                "extmetadata": {"License": {"value": "cc-by-sa-4.0"},
                                "LicenseShortName": {"value": "CC BY-SA 4.0"},
                                "Artist": {"value": "A. Photographer"}}}]},
            "2": {"title": "File:Huge scan.png", "imageinfo": [{
                "url": "https://upload.wikimedia.org/orig/Huge.png",
                "width": 20000, "height": 14130, "size": 14_000_000,
                "extmetadata": {"License": {"value": "cc0"}}}]},
        }}}
        with mock.patch.object(uk_media, "_get", return_value=fake) as g:
            out = uk_media._image_details(["File:Street.jpg", "File:Huge scan.png"])
        self.assertEqual(g.call_args[0][1]["iiurlwidth"], uk_media.THUMB_WIDTH)
        self.assertEqual(len(out), 1)
        self.assertIn("1920px", out[0]["url"])


class MusicTests(unittest.TestCase):
    def setUp(self):
        import documentary_composer as dc
        self.dc = dc
        self.tmp = tempfile.TemporaryDirectory()
        self.voice = os.path.join(self.tmp.name, "voice.wav")
        subprocess.run([dc.FFMPEG, "-y", "-f", "lavfi", "-i",
                        "sine=frequency=220:duration=4", self.voice],
                       capture_output=True, check=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_committed_calm_tracks_exist_and_are_small(self):
        from config import DOC_MUSIC_DIR
        tracks = [f for f in os.listdir(DOC_MUSIC_DIR) if f.endswith(".mp3")]
        self.assertGreaterEqual(len(tracks), 3)
        total = sum(os.path.getsize(os.path.join(DOC_MUSIC_DIR, f)) for f in tracks)
        self.assertLess(total, 15 * 1024 * 1024)

    def test_mix_uses_calm_track(self):
        out = self.dc.mix_background_music(self.voice, 4.0, self.tmp.name)
        self.assertTrue(out.endswith("mixed_audio.aac"))

    def test_no_tracks_falls_back_to_generated_pad(self):
        empty = os.path.join(self.tmp.name, "none")
        os.makedirs(empty)
        with mock.patch.object(self.dc, "DOC_MUSIC_DIR", empty):
            out = self.dc.mix_background_music(self.voice, 4.0, self.tmp.name)
        self.assertTrue(out.endswith("mixed_audio.aac"))
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, "ambient_pad.wav")))

    def test_pad_failure_falls_back_to_mastered_narration(self):
        empty = os.path.join(self.tmp.name, "none")
        os.makedirs(empty)
        with mock.patch.object(self.dc, "DOC_MUSIC_DIR", empty), \
             mock.patch.object(self.dc, "_generate_ambient_pad", return_value=""):
            out = self.dc.mix_background_music(self.voice, 4.0, self.tmp.name)
        self.assertTrue(out.endswith("narration_master.aac"))


class RejectionStageTests(unittest.TestCase):
    def test_stages(self):
        import story_generator as sg
        self.assertEqual(sg._rejection_stage(
            {"compliance": {"verdict": "OK"}, "legal": {"ok": False, "reasons": ["x"]}})[0], "legal")
        self.assertEqual(sg._rejection_stage(
            {"compliance": {"verdict": "AGE_RESTRICTED", "reasons": ["child"]}})[0], "compliance")
        self.assertEqual(sg._rejection_stage(
            {"compliance": {"verdict": "BANNED", "reasons": ["review error: boom"]}})[0],
            "review_error")
        self.assertEqual(sg._rejection_stage({"tiktok_safe": False})[0], "factcheck_unsafe")
        self.assertEqual(sg._rejection_stage(
            {"compliance": {"verdict": "OK"}, "legal": {"ok": True}, "accuracy_score": 3})[0],
            "quality")

    def test_prescreen_fails_open(self):
        import story_generator as sg
        with mock.patch.object(sg, "_groq_call", side_effect=RuntimeError("down")):
            self.assertTrue(sg._prescreen_case(None, {"title": "X", "extract": "y"})["go"])

    def test_prescreen_skip_parsed(self):
        import story_generator as sg
        reply = mock.Mock()
        reply.choices = [mock.Mock()]
        reply.choices[0].message.content = (
            '{"verdict":"SKIP","category":"minor_victim","reason":"a child was killed"}')
        with mock.patch.object(sg, "_groq_call", return_value=reply):
            r = sg._prescreen_case(None, {"title": "X", "extract": "y"})
        self.assertFalse(r["go"])
        self.assertEqual(r["category"], "minor_victim")


if __name__ == "__main__":
    unittest.main()


class WriterModelTests(unittest.TestCase):
    """Audit 2026-10-19 (X1, B1): the writer must never spend the upload gate's AI
    review model (its free daily quota is shared by all 3 bots), and the dead
    Cerebras/OpenRouter fallbacks are gone."""

    def test_writer_never_uses_reviewer_model(self):
        import story_generator as sg
        self.assertFalse(any(m.startswith("qwen/") for m, _ in sg.GROQ_SIBLING_MODELS))

    def test_no_dead_providers_in_chain(self):
        import httpx
        import story_generator as sg
        req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
        daily = sg.RateLimitError("Rate limit reached: tokens per day (TPD)",
                                  response=httpx.Response(429, request=req), body=None)
        client = mock.Mock()
        client.chat.completions.create.side_effect = daily
        urls = []

        def post(url, **kw):
            urls.append(url)
            return mock.Mock(status_code=429, json=lambda: {"error": {"message": "x"}}, text="x")
        import llm_client
        with mock.patch.object(sg, "Groq", return_value=client), \
                mock.patch.object(sg._requests, "post", side_effect=post), \
                mock.patch.object(llm_client, "_http_post",
                                  side_effect=lambda url, body, h, t: urls.append(url) or (429, {}, "x")), \
                mock.patch.object(sg, "_dead_groq_siblings", set()), \
                mock.patch.object(sg, "CEREBRAS_API_KEY", "k", create=True), \
                mock.patch.object(sg, "OPENROUTER_API_KEY", "k", create=True), \
                mock.patch.object(sg.time, "sleep", lambda s: None):
            for flag in ("_use_openrouter", "_use_fallback", "_use_cerebras"):
                if hasattr(sg, flag):
                    mock.patch.object(sg, flag, False).start()
            try:
                with self.assertRaises(RuntimeError):
                    sg._groq_call(client, model="openai/gpt-oss-120b",
                                  messages=[{"role": "user", "content": "hi"}])
            finally:
                mock.patch.stopall()
        self.assertFalse([u for u in urls if "cerebras.ai" in u or "openrouter.ai" in u])
        models = [c.kwargs.get("model") for c in client.chat.completions.create.call_args_list]
        self.assertFalse([m for m in models if m and m.startswith("qwen/")])


class DeadSiblingKeepsItsReason(unittest.TestCase):
    """Run 39464392079: once gpt-oss-20b hit its daily cap it was skipped on later calls and the
    error became "Groq sibling models unavailable: None", hiding why the compliance review failed."""

    def test_second_call_still_says_why(self):
        import httpx
        import story_generator as sg
        req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
        daily = sg.RateLimitError("Rate limit reached: tokens per day (TPD)",
                                  response=httpx.Response(429, request=req), body=None)
        client = mock.Mock()
        client.chat.completions.create.side_effect = daily
        with mock.patch.object(sg, "Groq", return_value=client),                 mock.patch.object(sg, "_dead_groq_siblings", set()),                 mock.patch.object(sg, "_last_sibling_error", None, create=True):
            msgs = []
            for _ in range(2):
                with self.assertRaises(RuntimeError) as cm:
                    sg._groq_sibling_call(model="x", messages=[])
                msgs.append(str(cm.exception))
        self.assertNotIn("None", msgs[1])
        self.assertIn("tokens per day", msgs[1].lower())


class GeminiBackupReviewer(unittest.TestCase):
    """Free backup when every Groq model is out of daily tokens. Dormant without GOOGLE_API_KEY,
    so a run still fails closed exactly as before. Since 2026-10-23 it goes through growth-lab
    llm_client, so the fake transport is llm_client._http_post (url, body, headers, timeout)."""

    def _reply(self, text="APPROVED", status=200):
        import json
        return status, {}, (json.dumps({"choices": [{"message": {"content": text}}]})
                            if status == 200 else text)

    def _groq_dead(self, sg):
        return mock.patch.object(sg, "_groq_sibling_call", side_effect=RuntimeError("tokens per day"))

    def _post(self, side_effect=None, return_value=None):
        import llm_client
        if side_effect is None:
            side_effect = lambda url, body, headers, timeout: return_value  # noqa: E731
        return mock.patch.object(llm_client, "_http_post", side_effect=side_effect)

    def setUp(self):
        import story_generator as sg
        mock.patch.object(sg.time, "sleep", lambda s: None).start()

    def tearDown(self):
        mock.patch.stopall()

    def test_used_when_groq_is_out_and_a_key_is_set(self):
        import story_generator as sg
        seen = {}

        def post(url, body, headers, timeout):
            seen.update(url=url, json=body, headers=headers)
            return self._reply("VERDICT")
        with self._groq_dead(sg), mock.patch.object(sg, "GOOGLE_API_KEY", "k", create=True), self._post(post):
            resp = sg._fallback_call(model="openai/gpt-oss-120b", max_tokens=50,
                                     messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(resp.choices[0].message.content, "VERDICT")
        self.assertIn("generativelanguage.googleapis.com", seen["url"])
        self.assertEqual(seen["json"]["model"], sg.GEMINI_MODELS[0])
        self.assertNotIn("k", seen["url"])                      # key travels in a header, not the URL
        self.assertEqual(seen["headers"]["Authorization"], "Bearer k")

    def test_a_retired_model_falls_through_to_the_next_one(self):
        """Run 39488228312: gemini-2.5-flash answered 404 'no longer available', which killed the backup."""
        import story_generator as sg
        tried = []

        def post(url, body, headers, timeout):
            tried.append(body["model"])
            return self._reply("x", status=404) if len(tried) == 1 else self._reply("VERDICT")
        with self._groq_dead(sg), mock.patch.object(sg, "GOOGLE_API_KEY", "k", create=True), self._post(post):
            resp = sg._fallback_call(model="m", messages=[])
        self.assertEqual(resp.choices[0].message.content, "VERDICT")
        self.assertEqual(tried, list(sg.GEMINI_MODELS[:2]))

    def test_a_reply_with_no_content_field_is_a_clean_failure(self):
        """Live check 2026-10-20: a thinking model that ran out of tokens returned a message with no 'content'."""
        import json
        import story_generator as sg
        bare = (200, {}, json.dumps({"choices": [{"message": {"role": "assistant"}}]}))
        with self._groq_dead(sg), mock.patch.object(sg, "GOOGLE_API_KEY", "k", create=True), \
                self._post(return_value=bare):
            with self.assertRaises(RuntimeError) as cm:
                sg._fallback_call(model="m", messages=[])
        self.assertIn("empty reply", str(cm.exception))

    def test_leaves_room_to_answer_after_thinking(self):
        import story_generator as sg
        seen = {}
        with self._groq_dead(sg), mock.patch.object(sg, "GOOGLE_API_KEY", "k", create=True), \
                self._post(lambda u, body, h, t: seen.update(body) or self._reply("ok")):
            sg._fallback_call(model="m", messages=[], max_tokens=20)
        self.assertGreaterEqual(seen["max_tokens"], 1024)
        self.assertEqual(seen["reasoning_effort"], "low")

    def test_not_used_without_a_key_so_the_run_still_fails_closed(self):
        import story_generator as sg
        with self._groq_dead(sg), mock.patch.object(sg, "GOOGLE_API_KEY", "", create=True), \
                self._post(return_value=self._reply()) as post:
            with self.assertRaises(RuntimeError):
                sg._fallback_call(model="m", messages=[])
        post.assert_not_called()

    def test_a_failing_or_empty_gemini_still_raises(self):
        import story_generator as sg
        for bad in (self._reply("x", status=429), self._reply("   ")):
            with self._groq_dead(sg), mock.patch.object(sg, "GOOGLE_API_KEY", "k", create=True), \
                    self._post(return_value=bad):
                with self.assertRaises(RuntimeError):
                    sg._fallback_call(model="m", messages=[])

    def test_network_error_raises_runtimeerror_not_a_crash_type(self):
        import story_generator as sg
        with self._groq_dead(sg), mock.patch.object(sg, "GOOGLE_API_KEY", "k", create=True), \
                self._post(side_effect=OSError("down")):
            with self.assertRaises(RuntimeError):
                sg._fallback_call(model="m", messages=[])

    def test_the_key_is_not_left_in_the_environment(self):
        import story_generator as sg
        before = os.environ.get("GOOGLE_API_KEY")
        with self._groq_dead(sg), mock.patch.object(sg, "GOOGLE_API_KEY", "k-test", create=True), \
                self._post(return_value=self._reply()):
            sg._fallback_call(model="m", messages=[])
        self.assertEqual(os.environ.get("GOOGLE_API_KEY"), before)


class PrescreenPromptTests(unittest.TestCase):
    def test_prescreen_prompt_has_date_and_cold_case_rule(self):
        from datetime import datetime, timezone
        import story_generator as sg
        seen = {}

        def fake(client, **kw):
            seen["messages"] = kw["messages"]
            raise RuntimeError("stop")
        with mock.patch.object(sg, "_groq_call", side_effect=fake):
            sg._prescreen_case(None, {"title": "X", "extract": "y"})
        system = seen["messages"][0]["content"]
        self.assertIn(f"{datetime.now(timezone.utc):%d %B %Y}", system)
        # UK contempt law: proceedings are active from an arrest or a warrant, not only a charge
        self.assertIn("someone is under arrest, wanted on a warrant, charged", system)
        self.assertIn("An open police investigation with nobody arrested, wanted or charged is "
                      "NOT active proceedings", system)
