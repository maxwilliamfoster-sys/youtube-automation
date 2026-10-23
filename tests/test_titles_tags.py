"""
Audit 2026-10-21 #12 (bot side, no shared modules): every post used the same
"<case> case. | UK crime story." line ("Peter Bergmann case case." on 18 Oct), the
same four tags including #fyp, and edge-tts ran with sentence boundaries.
"""
import asyncio
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import publish_checks  # noqa: E402
import story_generator as sg  # noqa: E402

DOUBLED = re.compile(r"\b(\w+)\s+\1\b", re.I)


class TagTests(unittest.TestCase):
    def test_no_fyp_in_core_tags(self):
        self.assertNotIn("#fyp", [t.lower() for t in sg.CORE_TAGS])

    def test_built_tags_have_no_generic_reach_tags(self):
        for uk in (True, False):
            tags = sg.build_hashtags("#fyp #foryou #coldcase #bergmann", "Peter Bergmann case",
                                     uk=uk).lower().split()
            self.assertNotIn("#fyp", tags)
            self.assertNotIn("#foryou", tags)
            self.assertLessEqual(len(tags), 5)


class TitleTests(unittest.TestCase):
    CASES = ["Peter Bergmann case", "Murder of Matthew Kitandwe", "Münsterland murders",
             "Disappearance of Cherrie Mahan", "The death of Sheila Fox", "Etan Patz Case"]

    def test_case_label_never_doubles_case(self):
        self.assertEqual(sg.case_label("Peter Bergmann case"), "Peter Bergmann")
        self.assertEqual(sg.case_label("Murder of Matthew Kitandwe"), "Matthew Kitandwe")
        for i in range(len(sg.TITLE_STRUCTURES)):
            t = sg.build_tiktok_title("Peter Bergmann case", uk=False, index=i)
            self.assertNotRegex(t.lower(), r"case\W+case", t)

    def test_rotation_is_deterministic_and_varied(self):
        n = len(sg.TITLE_STRUCTURES)
        self.assertTrue(4 <= n <= 5)
        titles = [sg.build_tiktok_title("Murder of Matthew Kitandwe", uk=True, index=i)
                  for i in range(n)]
        self.assertEqual(len(set(titles)), n)
        self.assertEqual(sg.build_tiktok_title("Murder of Matthew Kitandwe", uk=True, index=n),
                         titles[0])
        self.assertEqual(titles, [sg.build_tiktok_title("Murder of Matthew Kitandwe", uk=True,
                                                        index=i) for i in range(n)])

    def test_every_structure_is_clean_and_legally_neutral(self):
        for name in self.CASES:
            for uk in (True, False):
                for part in (None, (2, 3)):
                    for i in range(len(sg.TITLE_STRUCTURES)):
                        t = sg.build_tiktok_title(name, uk=uk, part=part, index=i)
                        self.assertIsNone(DOUBLED.search(t), t)
                        self.assertIn(sg.case_label(name), t)
                        self.assertLessEqual(len(t), 90, t)
                        self.assertNotIn("#", t)
                        self.assertTrue(publish_checks.check_defamation("", t)[0], t)
                        self.assertTrue(publish_checks.check_glorification("", t)[0], t)
                        self.assertNotRegex(t.lower(), r"\b(who|guilt|suspect|killer|did it|"
                                                       r"cover|truth|really)\b", t)
                        if part:
                            self.assertIn("Part 2/3", t)

    def test_index_defaults_to_posts_logged(self):
        d = tempfile.mkdtemp()
        log = os.path.join(d, "posts_log.csv")
        with open(log, "w", encoding="utf-8") as f:
            f.write("header\nrow1\nrow2\n")
        with mock.patch.object(sg, "POSTS_LOG", log):
            self.assertEqual(sg._post_count(), 2)
            self.assertEqual(sg.build_tiktok_title("Etan Patz", uk=False),
                             sg.build_tiktok_title("Etan Patz", uk=False, index=2))
        with mock.patch.object(sg, "POSTS_LOG", os.path.join(d, "missing.csv")):
            self.assertEqual(sg._post_count(), 0)


class EdgeTtsTests(unittest.TestCase):
    def test_edge_tts_asks_for_word_boundaries(self):
        import tts_generator as tg
        seen = {}

        class FakeCommunicate:
            def __init__(self, *a, **k):
                seen.update(k)

            async def stream(self):
                yield {"type": "audio", "data": b"x"}

        out = os.path.join(tempfile.mkdtemp(), "a.mp3")
        with mock.patch.object(tg.edge_tts, "Communicate", FakeCommunicate):
            asyncio.run(tg._generate_audio_edge("Hello there.", out, "v", "+0%", "+0Hz"))
        self.assertEqual(seen.get("boundary"), "WordBoundary")


if __name__ == "__main__":
    unittest.main()
