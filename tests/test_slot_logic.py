"""Which scheduled runs make a video: the 3 daily slots plus the backup runs that fill gaps."""
import csv
import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import slot_logic  # noqa: E402

NOON = datetime(2026, 10, 19, 13, 0)


def _log(*stamps):
    f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="", encoding="utf-8")
    w = csv.writer(f)
    w.writerow(["posted_utc", "video_id", "case"])
    for s in stamps:
        w.writerow([s, "", "Case"])
    f.close()
    return f.name


class TestVideosMade(unittest.TestCase):
    def test_counts_only_todays_utc_date(self):
        p = _log("2026-10-18T17:57", "2026-10-19T08:10", "2026-10-19T12:00")
        self.assertEqual(slot_logic.videos_made(p, datetime(2026, 10, 19, 15, 0)), 2)

    def test_missing_log_is_zero(self):
        self.assertEqual(slot_logic.videos_made("no-such-file.csv", NOON), 0)

    def test_blank_and_garbled_rows_are_ignored(self):
        p = _log("", "garbage", "2026-10-19T09:00")
        self.assertEqual(slot_logic.videos_made(p, NOON), 1)


class TestDecide(unittest.TestCase):
    def run_(self, sched, made=0, bst=True, now=NOON):
        return slot_logic.decide(sched, bst, made, now)

    def test_manual_runs_always_go(self):
        self.assertTrue(self.run_("", made=3))

    def test_primary_slot_runs_in_its_offset_only(self):
        self.assertTrue(self.run_("15 8 * * *", bst=True))
        self.assertFalse(self.run_("15 9 * * *", bst=True))
        self.assertTrue(self.run_("15 9 * * *", bst=False))
        self.assertFalse(self.run_("15 8 * * *", bst=False))

    def test_primary_slot_stops_at_the_daily_target(self):
        self.assertTrue(self.run_("45 14 * * *", made=2))
        self.assertFalse(self.run_("45 14 * * *", made=3))

    def test_backup_runs_only_when_the_day_is_behind(self):
        self.assertTrue(self.run_("20 13 * * *", made=0))
        self.assertFalse(self.run_("20 13 * * *", made=1))
        self.assertTrue(self.run_("20 16 * * *", made=1))
        self.assertFalse(self.run_("20 16 * * *", made=2))
        self.assertTrue(self.run_("20 19 * * *", made=2))
        self.assertFalse(self.run_("20 19 * * *", made=3))

    def test_backup_works_in_either_clock_setting(self):
        self.assertTrue(self.run_("20 13 * * *", made=0, bst=False))

    def test_backup_that_starts_after_the_day_is_over_does_nothing(self):
        late = datetime(2026, 10, 20, 0, 40)      # GitHub started a 19:20 cron 5h late
        self.assertFalse(self.run_("20 19 * * *", made=0, now=late))

    def test_unknown_cron_never_runs(self):
        self.assertFalse(self.run_("0 5 * * *"))


class TestCloudRunStopsAtTheCap(unittest.TestCase):
    def test_scheduled_run_with_a_full_day_makes_nothing(self):
        import main_documentary as md
        p = _log(*[f"2026-10-19T{h:02d}:00" for h in (8, 12, 15)])
        env = {"GITHUB_EVENT_NAME": "schedule"}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(slot_logic, "LOG_PATH", p), \
                mock.patch.object(slot_logic, "_now_utc", lambda: datetime(2026, 10, 19, 17, 0)), \
                mock.patch.object(md, "_generate_one") as gen:
            md.run_cloud_deliver(count=1)
        gen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
