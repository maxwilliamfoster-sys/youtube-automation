"""
rejected_cases.json is public. Security review 2026-10-23: _known() accepted ANY
lowercase word containing "_" as a code, so a reviewer reason such as
"john_smith" or "killed_by_her_husband" would be stored verbatim. Only an explicit
allowlist of codes may be stored; anything else becomes '<stage>_other'.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import case_screen  # noqa: E402


class ReasonCodeAllowlistTests(unittest.TestCase):
    def test_known_codes_pass_through(self):
        for code in ("gore", "minor_victim", "unconvicted_suspect", "fyf_ineligible"):
            self.assertEqual(case_screen.reason_code("prefilter", code), code)
        self.assertEqual(case_screen.reason_code("compliance", "AGE_RESTRICTED: body cam"),
                         "age_restricted")

    def test_unknown_underscore_word_is_not_a_code(self):
        self.assertEqual(case_screen.reason_code("prefilter", "john_smith"), "prefilter_other")
        self.assertEqual(case_screen.reason_code("prescreen", "killed_by_husband: x"),
                         "prescreen_other")
        self.assertEqual(case_screen.reason_code("compliance", "jane_doe_did_it"),
                         "compliance_other")

    def test_unknown_stage_falls_back_to_stage_other(self):
        self.assertEqual(case_screen.reason_code("quality", "low score"), "low_accuracy")
        self.assertEqual(case_screen.reason_code("weird", "smith_family"), "weird_other")
        self.assertEqual(case_screen.reason_code("Not A Stage!", "x"), "other")

    def test_every_produced_code_is_allowlisted(self):
        for stage in ("prefilter", "prescreen", "compliance", "legal", "quality",
                      "empty_script", "review_error", "factcheck_unsafe"):
            for reason in ("", "terri_x", "named the suspect", "contempt", "speculate"):
                code = case_screen.reason_code(stage, reason)
                self.assertTrue(case_screen.is_allowed_code(code, stage), (stage, reason, code))
        self.assertFalse(case_screen.is_allowed_code("smith_other", "legal"))
        self.assertFalse(case_screen.is_allowed_code("john_smith"))

    def test_stored_free_code_is_scrubbed_on_save(self):
        e = case_screen._public_entry({"stage": "legal", "code": "terri_moulton",
                                       "reason": "names an acquitted person"})
        self.assertEqual(e["code"], "unconvicted_suspect")
        e = case_screen._public_entry({"stage": "prefilter", "code": "smith_family"})
        self.assertEqual(e["code"], "prefilter_other")

    def test_record_rejection_writes_only_allowlisted(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "rej.json")
        with mock.patch.object(case_screen, "REJECTED_FILE", path):
            case_screen._cache = None
            case_screen.record_rejection("Murder of Q", "prescreen", "alan_jones_did_it: x")
            case_screen._cache = None
        with open(path, encoding="utf-8") as f:
            stored = json.load(f)
        self.assertEqual(stored["Murder of Q"]["code"], "prescreen_other")
        self.assertNotIn("alan", json.dumps(stored))


if __name__ == "__main__":
    unittest.main()
