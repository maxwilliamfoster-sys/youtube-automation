"""
Age-matched format / A/B comparison for @buriedcasefiles (growth-lab, 2026-10-15).

Every video is scored at the SAME age (72h) from the daily snapshots, never on its
latest total, then cohorts are judged with growth_stats (bootstrap CI + Mann-Whitney):

- primary:    average % watched at 72h (Buffer averageTimeWatched / duration)
- guardrails: views at 72h, likes per 1k views at 72h
- also shown: escalation (views 72h / 24h) and the follower count trend (account.csv)

Cohorts are posts_log.csv "format" values (FORMAT_VERSION). If posts_log rows carry
an "exp" column (JSON {name: bool}), each experiment is judged on its own coin flips.
Min/max posts per arm: 15 / 30 (growth-lab README). Read-only: prints a report.

    python analytics/compare.py                    # newest format vs the one before
    python analytics/compare.py --a v3-calm-world --b v4-mood-visuals
"""
import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import growth_stats as gs  # noqa: E402

AGE = 72
MIN_N, MAX_N = 15, 30


def _t(s):
    return datetime.strptime(s[:16], "%Y-%m-%dT%H:%M")


def _read(name):
    path = os.path.join(HERE, name)
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _case_key(text):
    text = re.sub(r"^(murder|killing|disappearance|death|deaths|murders) of (the )?", "",
                  (text or "").strip(), flags=re.I)
    return re.sub(r"[^a-z]", "", text.lower())[:18]


def load_videos():
    """video_id -> {posted, dur, format, exp, views[(age,v)], likes[(age,v)], watch[(age,pct)]}"""
    snaps = _read("snapshots.csv")
    vids = {}
    for r in snaps:
        v = vids.setdefault(r["video_id"], {"posted": r["posted_utc"], "desc": r["desc"],
                                            "dur": None, "views": [], "likes": []})
        age = float(r["age_hours"])
        v["views"].append((age, int(r["views"])))
        v["likes"].append((age, int(r["likes"])))
        if r.get("duration_s"):
            v["dur"] = float(r["duration_s"]) or v["dur"]

    # Join posts_log by TikTok id; a row written before TikTok returned an id is
    # matched by case name + series part, else by post time within 30 min.
    log = _read("posts_log.csv")
    by_id = {r["video_id"]: r for r in log if r.get("video_id")}
    free = [r for r in log if not r.get("video_id")]
    for vid, v in sorted(vids.items(), key=lambda kv: kv[1]["posted"]):
        row = by_id.get(vid)
        if row is None and free:
            key = _case_key(v["desc"].split(" case.")[0])
            m = re.search(r"Part (\d+/\d+)", v["desc"])
            part = m.group(1) if m else ""
            row = next((r for r in free if key and _case_key(r.get("case")) == key
                        and (r.get("series_part") or "") == part), None)
            if row is None:
                near = [(abs((_t(r["posted_utc"]) - _t(v["posted"])).total_seconds()), i)
                        for i, r in enumerate(free) if r.get("posted_utc")]
                near = [x for x in near if x[0] <= 1800]
                row = free[min(near)[1]] if near else None
            if row is not None:
                free.remove(row)
        row = row or {}
        v["format"] = row.get("format") or (
            "unlogged" if v["posted"] >= "2026-10-09T17"
            else "v2-energetic" if v["posted"] >= "2026-08-29" else "v1")
        try:
            v["exp"] = json.loads(row.get("exp") or "{}")
        except ValueError:
            v["exp"] = {}
        try:                                   # experiment-loop arms (loop_nightly.py)
            v["loop"] = json.loads(row.get("loop") or "{}")
        except ValueError:
            v["loop"] = {}
        v["watch"] = []

    for r in _read("buffer_metrics.csv"):
        v = vids.get(r["video_id"])
        avg = r.get("averageTimeWatched")
        if not v or not v["dur"] or avg in (None, "", "0"):
            continue
        age = (_t(r["snapshot_utc"]) - _t(v["posted"])).total_seconds() / 3600
        v["watch"].append((age, 100 * float(avg) / v["dur"]))
    return vids


def score(v):
    """Per-video values at AGE hours (None = too young / missing)."""
    views = gs.value_at_age(v["views"], AGE, zero_at_birth=True)
    likes = gs.value_at_age(v["likes"], AGE, zero_at_birth=True)
    # Watch % is an average, not a running total; the nearest later snapshot
    # is fine when nothing brackets 72h (Buffer only started on 2026-10-13).
    watch = gs.value_at_age(v["watch"], AGE)
    if watch is None:
        later = sorted(p for p in v["watch"] if p[0] >= AGE)
        watch = later[0][1] if later and later[0][0] <= AGE + 48 else None
    return {"watch": watch, "views": views,
            "likes_1k": 1000 * likes / views if views and likes is not None else None,
            "esc": gs.escalation(v["views"])}


def judge(arm_a, arm_b, label):
    a = [score(v) for v in arm_a]
    b = [score(v) for v in arm_b]
    res = gs.judge([x["watch"] for x in a], [x["watch"] for x in b], MIN_N, MAX_N,
                   guardrails={"views@72h": ([x["views"] for x in a], [x["views"] for x in b], True),
                               "likes/1k": ([x["likes_1k"] for x in a], [x["likes_1k"] for x in b], True)})
    lines = [gs.format_result(label, "avg % watched @72h", res)]
    for name, key in (("views@72h", "views"), ("likes per 1k", "likes_1k"), ("escalation 72h/24h", "esc")):
        ma = [x[key] for x in a if x[key] is not None]
        mb = [x[key] for x in b if x[key] is not None]
        if ma and mb:
            import statistics
            lines.append(f"  - {name}: {statistics.median(mb):.3g} vs {statistics.median(ma):.3g}"
                         f" (n={len(mb)} vs {len(ma)})")
    return res, lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", help="control format (default: the format before the newest)")
    ap.add_argument("--b", help="treatment format (default: the newest format)")
    args = ap.parse_args()
    vids = load_videos()
    cohorts = defaultdict(list)
    for v in sorted(vids.values(), key=lambda v: v["posted"]):
        cohorts[v["format"]].append(v)
    order = sorted(cohorts, key=lambda f: max(v["posted"] for v in cohorts[f]))
    order = [f for f in order if f != "unlogged"]
    out = [f"# BuriedCasefiles: age-matched comparison ({AGE}h)", ""]
    out.append("| format | posts | scored @72h | median watch % | median views@72h | escalation |")
    out.append("|---|---|---|---|---|---|")
    import statistics
    for f in order:
        sc = [score(v) for v in cohorts[f]]
        w = [x["watch"] for x in sc if x["watch"] is not None]
        vw = [x["views"] for x in sc if x["views"] is not None]
        es = [x["esc"] for x in sc if x["esc"] is not None]
        md = lambda xs, f="{:.3g}": f.format(statistics.median(xs)) if xs else "-"
        out.append(f"| {f} | {len(cohorts[f])} | {len(vw)} | {md(w)} | {md(vw, '{:.0f}')} | {md(es, '{:.2f}')} |")
    a = args.a or (order[-2] if len(order) >= 2 else None)
    b = args.b or (order[-1] if order else None)
    out.append("")
    if a and b:
        _, lines = judge(cohorts[a], cohorts[b], f"{b} vs {a}")
        out += ["## Format cohorts (before/after, so time effects are mixed in)", ""] + lines
    exps = sorted({k for v in vids.values() for k in v["exp"]})
    for name in exps:
        on = [v for v in vids.values() if v["exp"].get(name) is True]
        off = [v for v in vids.values() if v["exp"].get(name) is False]
        _, lines = judge(off, on, f"{name} on vs off")
        out += ["", f"## Experiment {name} (per-post coin flip)", ""] + lines
    acct = _read("account.csv")
    if len(acct) >= 2:
        out += ["", f"Followers: {acct[0]['followers']} ({acct[0]['snapshot_utc'][:10]}) -> "
                    f"{acct[-1]['followers']} ({acct[-1]['snapshot_utc'][:10]})"]
    print("\n".join(out))


if __name__ == "__main__":
    main()
