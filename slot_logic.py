"""
Which scheduled GitHub run should make a video.

Three primary crons a day plus three backup crons. GitHub starts scheduled runs hours
late and sometimes drops them (19 Oct 2026: 2 of 3 never started), so a backup only
runs when the day is behind: it makes the missing video, then stands down.

Used by the `slot` job in documentary.yml (command line) and by run_cloud_deliver
(re-check at the start of a run, because several runs can be queued at once).
"""
import csv
import os
import sys
from datetime import datetime, timezone

LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analytics", "posts_log.csv")
DAILY_TARGET = 3
PRIMARY = {True: {8, 11, 14}, False: {9, 12, 15}}      # cron hour (UTC) by UK clock: BST?
# Backup cron hour (UTC) -> videos the day must already have, or this run makes one.
# Hours differ from every primary hour so a cron never changes meaning with the clocks.
BACKUPS = {13: 1, 16: 2, 19: 3}
DAY_START_UTC_HOUR = 11        # a backup only fills gaps in the daytime it was meant for
DAY_END_UTC_HOUR = 22          # started later than this, it would be tomorrow's problem


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def videos_made(path: str, day: datetime) -> int:
    """Rows in posts_log.csv stamped with `day`'s UTC date (made, whether posted or queued)."""
    want = day.strftime("%Y-%m-%d")
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            return sum(1 for row in csv.DictReader(fh)
                       if (row.get("posted_utc") or "").startswith(want))
    except OSError:
        return 0


def decide(sched: str, bst: bool, made: int, now: datetime) -> bool:
    sched = (sched or "").strip()
    if not sched:                                   # manual run: always go
        return True
    try:
        hour = int(sched.split()[1])
    except (IndexError, ValueError):
        return False
    if hour in PRIMARY[bst]:
        return made < DAILY_TARGET
    if hour in BACKUPS:
        return made < BACKUPS[hour] and DAY_START_UTC_HOUR <= now.hour < DAY_END_UTC_HOUR
    return False


def day_is_full(now: datetime | None = None) -> bool:
    now = now or _now_utc()
    return videos_made(LOG_PATH, now) >= DAILY_TARGET


def main() -> None:
    from zoneinfo import ZoneInfo
    now = _now_utc()
    offset = datetime.now(ZoneInfo("Europe/London")).utcoffset()
    bst = offset is not None and offset.total_seconds() == 3600
    sched = os.environ.get("SCHED", "")
    made = videos_made(LOG_PATH, now)
    run = decide(sched, bst, made, now)
    print(f"schedule={sched!r} uk_bst={bst} made_today={made} -> run={run}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"run={'true' if run else 'false'}\n")


if __name__ == "__main__":
    sys.exit(main())
