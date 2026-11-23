"""Cases outside living memory are skipped (case_source._TOO_OLD).

2026-11-23 (autopilot): the 1848 Stanfield Hall murders were posted (18.6 s, no court outcome)
because the year pattern only covered 1000-1799.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import case_source  # noqa: E402


class TooOld(unittest.TestCase):
    def test_nineteenth_century_years_are_too_old(self):
        self.assertTrue(case_source._TOO_OLD.search("The Stanfield Hall murders took place in 1848 in Norfolk."))
        self.assertTrue(case_source._TOO_OLD.search("In 1888 five women were killed in Whitechapel."))

    def test_older_dates_still_too_old(self):
        self.assertTrue(case_source._TOO_OLD.search("Cleopatra died in 30 BC."))
        self.assertTrue(case_source._TOO_OLD.search("A 17th century poisoning."))

    def test_living_memory_cases_pass(self):
        for text in ("Suzy Lamplugh vanished in Fulham in 1986.", "The Rettendon murders of 1995.",
                     "She was last seen in 1962 near Leeds.", "The 2003 disappearance."):
            self.assertIsNone(case_source._TOO_OLD.search(text), text)


if __name__ == "__main__":
    unittest.main()
