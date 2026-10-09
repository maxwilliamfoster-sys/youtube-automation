"""
Public TikTok analytics for @buriedcasefiles — no login, no cookies, no API key.

Appends one row per video per run to analytics/snapshots.csv (views, likes, comments,
shares, saves, duration, post time) and refreshes analytics/comments.json. Repeated
snapshots are what make fair comparisons possible: a 2-day-old video and a 6-week-old
video can be compared at the same age (e.g. views at 72h) instead of raw totals.

What this CANNOT see (TikTok Studio only, needs the account login): average watch
time, % watched, completion, retention curve, traffic sources, followers gained per
video, profile views. Record those by hand in analytics/studio_manual.csv if read
from the app.

Video IDs: analytics/video_ids.txt (seeded from the profile page 2026-10-09) plus
any new post Buffer reports as sent (needs BUFFER_API_KEY; skipped without it).

Usernames are never stored: the repo is public.

    python analytics/collect.py            # stats + comments
    python analytics/collect.py --report   # print the summary table only
"""
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
IDS_FILE = os.path.join(HERE, "video_ids.txt")
SNAP_FILE = os.path.join(HERE, "snapshots.csv")
COMMENTS_FILE = os.path.join(HERE, "comments.json")
HANDLE = "buriedcasefiles"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/141.0 Safari/537.36",
      "Accept-Language": "en-GB,en;q=0.9"}
FIELDS = ["snapshot_utc", "video_id", "posted_utc", "age_hours", "duration_s", "views",
          "likes", "comments", "shares", "saves", "desc"]


def load_ids():
    ids = []
    if os.path.exists(IDS_FILE):
        ids = [l.strip() for l in open(IDS_FILE) if l.strip().isdigit()]
    try:                                   # new posts Buffer has sent since
        sys.path.insert(0, os.path.dirname(HERE))
        import tiktok_buffer as tb
        if tb.is_configured():
            chan = tb.find_channel()
            data = tb._gql("""query($o: OrganizationId!, $c: [ChannelId!]) {
              posts(first: 50, input: {organizationId: $o, filter: {status: [sent], channelIds: $c}}) {
                edges { node { externalLink } } } }""", {"o": chan["org"], "c": [chan["id"]]})
            for e in data["posts"]["edges"]:
                m = re.search(r"/video/(\d+)", e["node"].get("externalLink") or "")
                if m and m.group(1) not in ids:
                    ids.append(m.group(1))
    except Exception as e:
        print(f"[analytics] Buffer lookup skipped ({str(e)[:100]})")
    with open(IDS_FILE, "w") as fh:
        fh.write("\n".join(sorted(set(ids), reverse=True)) + "\n")
    return sorted(set(ids), reverse=True)


def video_stats(vid, session):
    r = session.get(f"https://www.tiktok.com/@{HANDLE}/video/{vid}", headers=UA, timeout=30)
    m = re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
                  r.text, re.S)
    if not m:
        raise RuntimeError(f"HTTP {r.status_code}, no data block")
    detail = json.loads(m.group(1))["__DEFAULT_SCOPE__"]["webapp.video-detail"]
    it = detail.get("itemInfo", {}).get("itemStruct")
    if not it:
        raise RuntimeError(f"status {detail.get('statusCode')} (deleted/private?)")
    s = it.get("statsV2") or it.get("stats")
    return {
        "video_id": vid,
        "posted_utc": datetime.fromtimestamp(int(it["createTime"]), timezone.utc)
                      .strftime("%Y-%m-%dT%H:%M"),
        "duration_s": it.get("video", {}).get("duration"),
        "views": int(s["playCount"]), "likes": int(s["diggCount"]),
        "comments": int(s["commentCount"]), "shares": int(s["shareCount"]),
        "saves": int(s.get("collectCount", 0)),
        "desc": (it.get("desc") or "").replace("\n", " ")[:120],
    }


def comments(vid, session):
    out, cursor = [], 0
    for _ in range(5):
        j = session.get("https://www.tiktok.com/api/comment/list/", headers=UA, timeout=30,
                        params={"aid": 1988, "aweme_id": vid, "count": 50,
                                "cursor": cursor}).json()
        for c in j.get("comments") or []:
            out.append({"video_id": vid, "cid": c["cid"], "likes": c.get("digg_count", 0),
                        "date": datetime.fromtimestamp(c["create_time"], timezone.utc)
                                .strftime("%Y-%m-%d"), "text": c.get("text", "")})
        if not j.get("has_more"):
            break
        cursor = j.get("cursor", 0)
    return out


def collect():
    ids = load_ids()
    now = datetime.now(timezone.utc)
    s = requests.Session()
    rows, allc = [], []
    for vid in ids:
        try:
            st = video_stats(vid, s)
            posted = datetime.strptime(st["posted_utc"], "%Y-%m-%dT%H:%M").replace(
                tzinfo=timezone.utc)
            st["age_hours"] = round((now - posted).total_seconds() / 3600, 1)
            st["snapshot_utc"] = now.strftime("%Y-%m-%dT%H:%M")
            rows.append(st)
        except Exception as e:
            print(f"[analytics] {vid}: {e}")
        try:
            allc += comments(vid, s)
        except Exception as e:
            print(f"[analytics] comments {vid}: {str(e)[:80]}")
        time.sleep(1.0)
    new = not os.path.exists(SNAP_FILE)
    with open(SNAP_FILE, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if new:
            w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in FIELDS})
    if allc or not os.path.exists(COMMENTS_FILE):
        json.dump(allc, open(COMMENTS_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[analytics] {len(rows)}/{len(ids)} videos, {len(allc)} comments")
    return rows


if __name__ == "__main__":
    if "--report" not in sys.argv:
        collect()
