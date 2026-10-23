"""growth-lab S7 (buffer_client) wired into the poster and analytics/collect.py (MASTER_PLAN #4).

Buffer Free = 100 calls / 15 min, 250 / 24 h, 3,000 / 30 days per API key, shared by every bot on
the key. Every call now goes through buffer_client: RateLimit headers are logged, a 429 is a
BufferRateLimited error (never "no posts"), analytics reads sent posts + metrics in ONE query, and
the poster polls TikTok's status on a backing-off schedule instead of every 20 s.
No network: the client's transport is faked.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "analytics"))

import buffer_client  # noqa: E402
import tiktok_buffer as tb  # noqa: E402

RL = [("RateLimit", '"100-in-15min";r=97;t=800'),
      ("RateLimit-Policy", '"100-in-15min";q=100;w=900'),
      ("RateLimit", '"250-in-24h";r=200;t=80000'),
      ("RateLimit-Policy", '"250-in-24h";q=250;w=86400'),
      ("RateLimit", '"3000-in-30d";r=2900;t=2000000'),
      ("RateLimit-Policy", '"3000-in-30d";q=3000;w=2592000')]

CHAN = {"id": "c1", "name": "buriedcasefiles", "displayName": "Buried Casefiles",
        "externalLink": "https://www.tiktok.com/@buriedcasefiles", "service": "tiktok",
        "isQueuePaused": False, "isDisconnected": False}


class FakeBuffer:
    """Answers Buffer GraphQL by looking at the query text."""

    def __init__(self, posts=(), rate_limited=False, statuses=None):
        self.posts, self.rate_limited = list(posts), rate_limited
        self.statuses = list(statuses or [])
        self.queries = []

    def __call__(self, url, body, headers, timeout):
        q = body["query"]
        self.queries.append(q)
        if self.rate_limited and "posts(" in q:
            return 429, RL[:1] + [("Retry-After", "120")], json.dumps(
                {"errors": [{"message": "Too many requests",
                             "extensions": {"code": "RATE_LIMIT_EXCEEDED", "window": "24h"}}]})
        if "organizations" in q:
            data = {"account": {"organizations": [{"id": "o1", "name": "Org"}]}}
        elif "channels(" in q:
            data = {"channels": [CHAN]}
        elif "posts(" in q:
            data = {"posts": {"edges": [{"node": p} for p in self.posts],
                              "pageInfo": {"hasNextPage": False, "endCursor": None}}}
        elif "post(input" in q:
            st = self.statuses.pop(0) if self.statuses else "sending"
            data = {"post": {"id": "p1", "status": st, "externalLink": None, "error": None}}
        else:
            data = {}
        return 200, RL, json.dumps({"data": data})


def _post(vid, views=100):
    return {"id": f"p{vid}", "status": "sent", "dueAt": None, "sentAt": None, "text": "t",
            "externalLink": f"https://www.tiktok.com/@buriedcasefiles/video/{vid}",
            "metricsUpdatedAt": None,
            "metrics": [{"type": "views", "value": views}, {"type": "averageTimeWatched", "value": 9.5}]}


class PosterGoesThroughBufferClient(unittest.TestCase):
    def setUp(self):
        mock.patch.dict(os.environ, {"BUFFER_API_KEY": "k"}).start()
        os.environ.pop("BUFFER_CHANNEL_ID", None)
        tb.reset_client()

    def tearDown(self):
        mock.patch.stopall()
        tb.reset_client()

    def test_quota_headers_are_read(self):
        fake = FakeBuffer()
        with mock.patch.object(buffer_client, "_http_post", fake):
            tb.find_channel()
            left = tb.client().remaining()
        self.assertEqual(left["24h"]["remaining"], 200)
        self.assertEqual(left["30d"]["limit"], 3000)

    def test_a_429_is_a_rate_limit_error_not_an_empty_list(self):
        fake = FakeBuffer(rate_limited=True)
        with mock.patch.object(buffer_client, "_http_post", fake):
            chan = tb.find_channel()
            with self.assertRaises(buffer_client.BufferRateLimited) as cm:
                tb.sent_posts(chan)
        self.assertEqual(cm.exception.window, "24h")
        self.assertEqual(cm.exception.retry_after, 120.0)

    def test_other_buffer_errors_stay_publish_errors(self):
        with mock.patch.object(buffer_client, "_http_post",
                               lambda u, b, h, t: (500, [], "boom")):
            with self.assertRaises(tb.PublishError):
                tb.find_channel()

    def test_status_polling_backs_off(self):
        """A post stuck in 'sending' for the whole 900 s used to cost 45 status calls."""
        fake = FakeBuffer()
        clock = [0.0]

        def sleep(s):
            clock[0] += s
        with mock.patch.object(buffer_client, "_http_post", fake), \
                mock.patch.object(tb.time, "sleep", sleep), \
                mock.patch.object(tb.time, "time", lambda: clock[0]):
            post = tb.wait_for_post("p1", timeout=900)
        self.assertEqual(post["status"], "sending")
        polls = sum(1 for q in fake.queries if "post(input" in q)
        self.assertLessEqual(polls, 12)
        self.assertGreaterEqual(clock[0], 900)

    def test_status_polling_stops_when_sent(self):
        fake = FakeBuffer(statuses=["sending", "sent"])
        with mock.patch.object(buffer_client, "_http_post", fake), \
                mock.patch.object(tb.time, "sleep", lambda s: None):
            post = tb.wait_for_post("p1", timeout=900)
        self.assertEqual(post["status"], "sent")


class CollectUsesOneQuery(unittest.TestCase):
    def setUp(self):
        import collect
        self.collect = collect
        self.dir = tempfile.mkdtemp()
        mock.patch.dict(os.environ, {"BUFFER_API_KEY": "k"}).start()
        os.environ.pop("BUFFER_CHANNEL_ID", None)
        for attr, name in (("IDS_FILE", "video_ids.txt"), ("BUFFER_FILE", "buffer_metrics.csv")):
            mock.patch.object(collect, attr, os.path.join(self.dir, name)).start()
        with open(collect.IDS_FILE, "w") as fh:
            fh.write("111\n")
        tb.reset_client()

    def tearDown(self):
        mock.patch.stopall()
        tb.reset_client()
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_metrics_and_ids_from_one_posts_query(self):
        fake = FakeBuffer(posts=[_post(222, 500), _post(333, 40)])
        with mock.patch.object(buffer_client, "_http_post", fake):
            posts = self.collect.buffer_posts()
            n = self.collect.buffer_metrics(posts)
            ids = self.collect.load_ids(posts)
        self.assertEqual(n, 2)
        self.assertEqual(sorted(ids), ["111", "222", "333"])
        self.assertEqual(sum(1 for q in fake.queries if "posts(" in q), 1)
        self.assertLessEqual(len(fake.queries), 3)
        rows = open(self.collect.BUFFER_FILE, encoding="utf-8").read()
        self.assertIn("222,500,9.5", rows)

    def test_a_429_keeps_known_ids_and_writes_no_metrics(self):
        fake = FakeBuffer(rate_limited=True)
        with mock.patch.object(buffer_client, "_http_post", fake):
            posts = self.collect.buffer_posts()
            n = self.collect.buffer_metrics(posts)
            ids = self.collect.load_ids(posts)
        self.assertIsNone(posts)
        self.assertEqual(n, 0)
        self.assertEqual(ids, ["111"])
        self.assertFalse(os.path.exists(self.collect.BUFFER_FILE))

    def test_no_key_means_no_calls(self):
        os.environ.pop("BUFFER_API_KEY", None)
        fake = FakeBuffer()
        with mock.patch.object(buffer_client, "_http_post", fake):
            self.assertIsNone(self.collect.buffer_posts())
        self.assertEqual(fake.queries, [])


if __name__ == "__main__":
    unittest.main()
