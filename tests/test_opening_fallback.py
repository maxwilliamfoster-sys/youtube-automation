"""Autopilot 2026-11-16: two posted videos opened on lines the cold-open rules ban.
- "On the 11th of August 2009, a car trunk was found..." (dated open): the rewrite was rejected, so the
  card was dropped but the script still began with the date.
- "Her body was found wearing only underwear, ..." (lurid open): 'underwear' was not in the lurid list.
When no valid cold open can be found, the spoken script must not begin with the banned line either."""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import story_generator as sg  # noqa: E402

CASE = {"year": "2009", "location": "Grozny", "case_name": "Zarema Sadulayeva"}


class LuridWords(unittest.TestCase):
    def test_underwear_and_similar_are_lurid(self):
        for line in ("Her body was found wearing only underwear, near home.",
                     "She was found in her lingerie by the lake.",
                     "He was found half-dressed on the moor."):
            self.assertTrue(any("lurid_open" in i for i in sg._cold_open_issues(line, CASE)), line)

    def test_ordinary_clothing_is_not_lurid(self):
        self.assertFalse(any("lurid_open" in i for i in
                             sg._cold_open_issues("Her red coat was found on a Grozny bus.", CASE)))


class Fallback(unittest.TestCase):
    def test_dated_first_sentence_loses_its_date(self):
        script = ("On the 11th of August 2009, two aid workers vanished from their Grozny office. "
                  "Six men came in at two in the afternoon. Nobody has been convicted.")
        out = sg._strip_banned_opening(script, CASE)
        self.assertTrue(out.startswith("Two aid workers vanished from their Grozny office."), out)
        self.assertIn("Nobody has been convicted.", out)

    def test_lurid_first_sentence_is_dropped(self):
        script = ("Her body was found wearing only underwear, a kilometre from home. "
                  "Theresa Allore vanished from Champlain College in 1978. Police called her a runaway. "
                  "Five months later she was found in a stream.")
        out = sg._strip_banned_opening(script, CASE)
        self.assertTrue(out.startswith("Theresa Allore vanished"), out)

    def test_short_scripts_and_valid_openings_are_left_alone(self):
        ok = "Theresa Allore vanished from Champlain College. Police called her a runaway. She was found later."
        self.assertEqual(sg._strip_banned_opening(ok, CASE), ok)
        short = "Her body was found in underwear. Police called it nothing."
        self.assertEqual(sg._strip_banned_opening(short, CASE), short)

    def test_rejected_rewrite_still_removes_the_date_from_the_script(self):
        script = ("On the 11th of August 2009, two aid workers vanished from their Grozny office. "
                  "Six men came in at two in the afternoon. Nobody has been convicted.")
        with mock.patch.object(sg, "_write_cold_open", return_value="On 11 August 2009 they vanished."), \
             mock.patch.object(sg, "_recent_openings", return_value=set()):
            hook, out = sg._cold_open(None, CASE, script)
        self.assertEqual(hook, "")
        self.assertFalse(out.lower().startswith("on the 11th"), out)


if __name__ == "__main__":
    unittest.main()
