"""
Nightly experiment loop for @buriedcasefiles (growth-lab, 2026-10-16).

Runs in analytics.yml straight after the daily snapshot:

  1. Score every logged post at 72h with analytics/compare.py (avg % watched from
     Buffer, views@72h from the public snapshots) and map it to its loop arm
     (posts_log.csv "loop" column).
  2. experiment_loop.evaluate(): judge the running test (growth_stats, min 15 /
     max 30 per arm), PROMOTE winners / REVERT losers into loop_config.json, start
     at most one new test from levers.json.
  3. Write loop_summary.json; the combined morning Telegram digest is sent from
     the chess repo, which reads this file and the ledger from GitHub.

Writes ONLY experiments.json, loop_config.json and loop_summary.json.

    python loop_nightly.py              # real run (CI)
    python loop_nightly.py --dry-run    # decide and print; write nothing
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "analytics"))
import compare  # noqa: E402
import experiment_loop as el  # noqa: E402


def collect(now=None):
    """(posts, summary). Raises when there is no analytics data at all."""
    now = now or datetime.now(timezone.utc)
    vids = compare.load_videos()
    if not vids:
        raise RuntimeError("analytics/snapshots.csv is empty or missing")
    # Arms come from compare.load_videos' posts_log join, which also matches rows
    # logged without a TikTok id (a post scheduled for the next morning has none yet)
    # by case name / post time. Matching on video_id alone lost those posts' arms.
    posts = []
    cutoff = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M")
    week_views, week_watch, week_posts = 0, [], 0
    for vid, v in vids.items():
        s = compare.score(v)
        arms = v.get("loop") if isinstance(v.get("loop"), dict) else {}
        posts.append({"id": vid, "arms": arms, "hold": s["watch"], "views72": s["views"]})
        if v["posted"] >= cutoff:
            week_posts += 1
            week_views += max((x for _, x in v["views"]), default=0)
            if v["watch"]:
                week_watch.append(sorted(v["watch"])[-1][1])
    week_watch.sort()
    med = week_watch[len(week_watch) // 2] / 100 if week_watch else None
    summary = {"views": week_views, "retention": med, "swipe_away": None, "posts": week_posts,
               "note": "views = latest totals of posts from the last 7 days; "
                       "retention = median avg % watched; TikTok gives no swipe-away via Buffer"}
    return posts, summary


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="decide and print; write nothing")
    args = ap.parse_args(argv)
    today = datetime.now(timezone.utc).date().isoformat()

    led, lev, cfg = el.load_repo(HERE)
    try:
        posts, summary = collect()
        data_ok, err = True, None
    except Exception as exc:                          # no data -> no decisions
        posts, summary, data_ok, err = [], None, False, f"analytics error: {str(exc)[:200]}"
    led, cfg, actions = el.evaluate(led, lev, cfg, posts, today, data_ok=data_ok)
    if err:
        actions.insert(0, err)
    for a in actions:
        print("[loop]", a)
    print(el.digest_section("🔎 BuriedCasefiles", led, lev, cfg, summary, actions))
    if args.dry_run:
        print("[DRY RUN - nothing written]")
        return 0
    el.save_json(os.path.join(HERE, "experiments.json"), led)
    el.save_json(os.path.join(HERE, "loop_config.json"), cfg)
    el.save_json(os.path.join(HERE, "loop_summary.json"),
                 {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                  "summary": summary, "actions": actions})
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:          # never fail the analytics job over the loop
        print(f"[loop] fatal: {exc}")
        sys.exit(0)
