"""Per-sentence narration check (2026-11-12): score what Whisper heard against the sentence."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chatterbox_narrator import sentence_match, SENTENCE_MIN_MATCH  # noqa: E402

S = "Tina Baker left Brookfield farm on a cold March morning and was never seen again."


class SentenceMatch(unittest.TestCase):
    def test_clean_take_passes(self):
        self.assertGreaterEqual(sentence_match(S, S), 0.99)

    def test_misspelt_names_dont_count(self):
        heard = "Tina Baker left Brookfeld farm on a cold March morning and was never seen again."
        self.assertGreaterEqual(sentence_match(heard, S), SENTENCE_MIN_MATCH)
        heard = "Teena Bacre left Brockfield farm on a cold March morning and was never seen again."
        self.assertGreaterEqual(sentence_match(heard, S), SENTENCE_MIN_MATCH)

    def test_skipped_half_fails(self):
        self.assertLess(sentence_match("Tina Baker left Brookfield farm on a cold", S), SENTENCE_MIN_MATCH)

    def test_repeats_fail(self):
        heard = "Tina Baker left left left Brookfield farm farm on a cold cold March morning morning and and was never seen again again."
        self.assertLess(sentence_match(heard, S), SENTENCE_MIN_MATCH)

    def test_mumble_fails(self):
        self.assertLess(sentence_match("Tina mm left uh on a morning", S), SENTENCE_MIN_MATCH)
        self.assertEqual(sentence_match("", S), 0.0)

    def test_numbers_as_words(self):
        s = "She was twenty three years old."
        self.assertGreaterEqual(sentence_match("She was twenty-three years old.", s), SENTENCE_MIN_MATCH)


if __name__ == "__main__":
    unittest.main()
