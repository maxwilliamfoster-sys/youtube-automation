"""
Auto-post BuriedCasefiles videos to TikTok through Buffer, with the PC off.

Buffer is an approved TikTok partner, so posts go out PUBLIC through TikTok's
official API: no browser automation, no cookies (the stealth browser poster is
what got this account shadowbanned in June). Same route Escape Cup has used
since 2026-09-29 with 99% For You traffic.

1. host_video(): Buffer only takes a public, direct (no-redirect) HTTPS URL. The
   MP4 is force-pushed to this repo's orphan `media` branch, which GitHub Pages
   serves at https://<owner>.github.io/<repo>/v/<file>.mp4. Only the last KEEP
   videos are kept, so the branch never grows.
2. post_video(): Buffer GraphQL createPost (shareNow), then waits for TikTok to
   accept it.

The Buffer account holds more than one TikTok channel (Escape Cup is there too),
so the channel is picked by handle and NEVER by "first TikTok channel": posting a
true-crime video to the wrong account is the one mistake this must not make.
"""
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

BUFFER_API = "https://api.buffer.com"
HANDLE = "buriedcasefiles"
KEEP = 6                     # ~40 MB each; Pages sites cap at 1 GB
MAX_HOST_MB = 95             # GitHub refuses files over 100 MB

# Posts only go live while the (91% UK) audience is awake. The crons aim well inside
# this, but GitHub can start a scheduled run hours late; a video finished outside the
# window is queued in Buffer for the next morning instead of going out at 2am.
UK = ZoneInfo("Europe/London")
WINDOW = (8, 22)             # UK local hours: earliest post 08:00, latest 21:59
MORNING_POST = (8, 30)       # where an out-of-window video lands


class PublishError(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(os.environ.get("BUFFER_API_KEY"))


# ---------------------------------------------------------------- hosting --

def _git(args, cwd):
    r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        err = r.stderr.strip()
        token = os.environ.get("GITHUB_TOKEN")
        if token:                        # public repo: never print the token in a log
            err = err.replace(token, "***")
        raise PublishError(f"git {' '.join(args[:2])} failed: {err[:300]}")
    return r.stdout


def _pages_base():
    owner, name = os.environ["GITHUB_REPOSITORY"].split("/")
    return f"https://{owner.lower()}.github.io/{name}"


def prune_plan(present, order, filename, keep=None):
    """(files to delete, new upload order ending with filename). Oldest uploads go
    first; files missing from the manifest (pre-manifest uploads) count as oldest."""
    keep = keep or KEEP
    order = sorted(set(present) - set(order) - {filename}) + \
        [f for f in order if f in present and f != filename]
    delete = []
    while len(order) > keep - 1:
        delete.append(order.pop(0))
    return delete, order + [filename]


def host_video(path, filename):
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        raise PublishError("GITHUB_TOKEN / GITHUB_REPOSITORY not set (hosting only runs in CI)")
    remote = f"https://x-access-token:{token}@github.com/{repo}.git"
    tmp = tempfile.mkdtemp()
    try:
        _git(["init", "-q"], tmp)
        _git(["checkout", "-q", "--orphan", "media"], tmp)
        os.makedirs(os.path.join(tmp, "v"), exist_ok=True)
        # carry over the most recent videos (an in-flight Buffer post may still need one)
        if subprocess.run(["git", "fetch", "-q", "--depth", "1", remote, "media"], cwd=tmp,
                          capture_output=True).returncode == 0:
            subprocess.run(["git", "checkout", "-q", "FETCH_HEAD", "--", "."], cwd=tmp,
                           capture_output=True)
        # Prune by UPLOAD ORDER (v/order.txt), not by name: names start with the
        # title, so sorting deleted e.g. a video still queued for 08:30 next morning.
        vdir = os.path.join(tmp, "v")
        manifest = os.path.join(vdir, "order.txt")
        order = []
        if os.path.exists(manifest):
            with open(manifest, encoding="utf-8") as fh:
                order = [ln.strip() for ln in fh if ln.strip()]
        present = {f for f in os.listdir(vdir) if f.endswith(".mp4")}
        delete, order = prune_plan(present, order, filename)
        for f in delete:
            os.remove(os.path.join(vdir, f))
        shutil.copy(path, os.path.join(vdir, filename))
        with open(manifest, "w", encoding="utf-8") as fh:
            fh.write("\n".join(order) + "\n")
        open(os.path.join(tmp, ".nojekyll"), "w").close()
        with open(os.path.join(tmp, "index.html"), "w") as fh:
            fh.write("<!doctype html><title>BuriedCasefiles media</title>")
        _git(["add", "-A"], tmp)
        _git(["-c", "user.name=github-actions[bot]",
              "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
              "commit", "-q", "-m", f"media: {filename}"], tmp)
        _git(["push", "-q", "-f", remote, "media"], tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    url = f"{_pages_base()}/v/{filename}"
    size = os.path.getsize(path)
    deadline = time.time() + 600
    while time.time() < deadline:
        try:
            r = requests.head(url, timeout=20, allow_redirects=False)
            if r.status_code == 200 and int(r.headers.get("content-length", 0)) == size:
                print(f"[host] live: {url}")
                return url
        except requests.RequestException:
            pass
        time.sleep(15)
    raise PublishError(f"Pages never served {url} (is Pages enabled on the media branch?)")


# ----------------------------------------------------------------- buffer --

# Every Buffer call goes through growth-lab buffer_client (S7, MASTER_PLAN #4): Free is
# 100 calls / 15 min, 250 / 24 h and 3,000 / 30 days PER API KEY, shared with Escape Cup
# and space. The client logs the RateLimit headers on every reply and turns a 429 into
# BufferRateLimited, which is never mistaken for "no posts".
_client = None


def client():
    """The run's BufferClient (key read from BUFFER_API_KEY when first used)."""
    global _client
    import buffer_client
    if _client is None or _client.api_key != os.environ.get("BUFFER_API_KEY", ""):
        _client = buffer_client.BufferClient()
    return _client


def reset_client():
    global _client
    _client = None


def _gql(query, variables=None):
    import buffer_client
    if not os.environ.get("BUFFER_API_KEY"):
        raise PublishError("BUFFER_API_KEY not set")
    try:
        return client().gql(query, variables)
    except buffer_client.BufferRateLimited:
        raise                           # its own type: callers must not read it as "missing"
    except buffer_client.BufferError as e:
        raise PublishError(str(e)) from e


def sent_posts(chan, first=100):
    """Sent posts on this channel, newest first, with metrics, in ONE query (one page of 100).
    Raises BufferRateLimited on a 429."""
    import buffer_client
    if not os.environ.get("BUFFER_API_KEY"):
        raise PublishError("BUFFER_API_KEY not set")
    try:
        return client().sent_posts(chan["org"], chan["id"], first=first, max_pages=1)
    except buffer_client.BufferRateLimited:
        raise
    except buffer_client.BufferError as e:
        raise PublishError(str(e)) from e


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def tiktok_channels():
    """Every TikTok channel on the Buffer account, as dicts."""
    out = []
    orgs = _gql("query { account { organizations { id name } } }")["account"]["organizations"]
    for org in orgs:
        chans = _gql("query($o: OrganizationId!) { channels(input: {organizationId: $o}) "
                     "{ id name displayName externalLink service isQueuePaused isDisconnected } }",
                     {"o": org["id"]})["channels"]
        out += [dict(c, org=org["id"]) for c in chans if c["service"] == "tiktok"]
    return out


def find_channel():
    """The BuriedCasefiles TikTok channel. Raises unless exactly one matches."""
    want = os.environ.get("BUFFER_CHANNEL_ID")
    chans = tiktok_channels()
    if want:
        hits = [c for c in chans if c["id"] == want]
    else:
        hits = [c for c in chans
                if HANDLE in _norm(c["name"]) + "|" + _norm(c["displayName"])
                + "|" + _norm(c["externalLink"])]
    if len(hits) != 1:
        seen = ", ".join(f"{c['name']} ({c['id']})" for c in chans) or "none"
        raise PublishError(f"expected 1 @{HANDLE} TikTok channel in Buffer, found "
                           f"{len(hits)}. TikTok channels: {seen}")
    c = hits[0]
    if c.get("isDisconnected"):
        raise PublishError(f"Buffer channel {c['name']} is disconnected - reconnect it in Buffer")
    if c.get("isQueuePaused"):
        raise PublishError(f"Buffer queue for {c['name']} is paused")
    return c


def _get_post(post_id):
    return _gql("""query($i: PostInput!) { post(input: $i) {
        id status externalLink error { message } } }""", {"i": {"id": post_id}})["post"]


# Seconds between status checks after a shareNow. TikTok usually takes it in 1-3 min; the
# old fixed 20 s poll spent up to 45 calls of the shared Buffer quota on one stuck post.
POLL_SCHEDULE = (30, 30, 45, 60, 90, 120, 120, 120, 120, 120)


def wait_for_post(post_id, timeout=900):
    """Poll a just-shared post until it leaves scheduled/sending or `timeout` s pass."""
    deadline = time.time() + timeout
    post = _get_post(post_id)
    step = 0
    while time.time() < deadline and post["status"] in ("scheduled", "sending"):
        wait = POLL_SCHEDULE[min(step, len(POLL_SCHEDULE) - 1)]
        step += 1
        time.sleep(max(1, min(wait, deadline - time.time())))
        post = _get_post(post_id)
        print(f"[buffer] {post_id}: {post['status']}")
    return post


def post_time(now=None):
    """None = post now (inside the UK window), else the UTC datetime to schedule for."""
    now = (now or datetime.now(timezone.utc)).astimezone(UK)
    if WINDOW[0] <= now.hour < WINDOW[1]:
        return None
    day = now.date() if now.hour < WINDOW[0] else now.date() + timedelta(days=1)
    due = datetime(day.year, day.month, day.day, *MORNING_POST, tzinfo=UK)
    return due.astimezone(timezone.utc)


def post_video(video_path, caption, timeout=900):
    """
    Host the MP4 and share it to TikTok: now if it's a sensible UK hour (then wait for
    TikTok to take it), otherwise scheduled in Buffer for the next morning.
    Returns (ok, detail). Never raises.
    """
    try:
        import upload_gate
        try:
            upload_gate.require(video_path, "tiktok")     # only gate-approved files go out
        except upload_gate.GateBlocked as e:
            return False, f"BLOCKED by upload gate: {e}"
        chan = find_channel()
        if os.path.getsize(video_path) / 1048576 > MAX_HOST_MB:
            raise PublishError(f"video is over {MAX_HOST_MB} MB - too big to host on Pages")
        due = post_time()
        filename = os.path.basename(video_path)
        url = host_video(video_path, filename)
        inp = {
            "text": caption,
            "channelId": chan["id"],
            "schedulingType": "automatic",
            "mode": "customScheduled" if due else "shareNow",
            "needsApproval": False,
            "assets": [{"video": {"url": url, "metadata": {"thumbnailOffset": 1500}}}],
            # The narration is an AI voice and the script is LLM-written, so the
            # AIGC label goes on. Undisclosed AI content is what TikTok penalises.
            "metadata": {"tiktok": {"isAiGenerated": True}},
        }
        if due:
            inp["dueAt"] = due.isoformat().replace("+00:00", "Z")
        data = _gql("""mutation($i: CreatePostInput!) { createPost(input: $i) {
            ... on PostActionSuccess { post { id status } }
            ... on MutationError { message } } }""", {"i": inp})
        res = data["createPost"]
        if "post" not in res:
            raise PublishError(f"Buffer rejected the post: {res.get('message')}")
        post_id = res["post"]["id"]
        if due:
            when = due.astimezone(UK).strftime("%a %d %b %H:%M")
            print(f"[buffer] outside UK posting hours - scheduled {when} UK -> {chan['name']}: {post_id}")
            return True, f"Scheduled for {when} UK (finished outside posting hours)"
        print(f"[buffer] shareNow -> {chan['name']}: {post_id}")

        post = wait_for_post(post_id, timeout)
        print(f"[buffer] calls this run: {client().calls}")
        if post["status"] == "sent":
            return True, post.get("externalLink") or f"Buffer post {post_id}"
        if post["status"] in ("scheduled", "sending"):
            # Still in flight: it may yet publish, so the caller must NOT say "post it
            # by hand" (that double-posted risk). The analytics run picks it up later.
            return False, f"Buffer post {post_id} timed out while {post['status']} - it may still publish"
        err = (post.get("error") or {}).get("message") or post["status"]
        return False, f"Buffer post {post_id} ended {post['status']}: {err}"
    except Exception as e:
        return False, str(e)


if __name__ == "__main__":
    # `python tiktok_buffer.py` - list the TikTok channels Buffer can see (safe, read-only).
    for c in tiktok_channels():
        print(c["id"], c["name"], c["displayName"], c["externalLink"],
              "DISCONNECTED" if c["isDisconnected"] else "")
    print("BuriedCasefiles channel ->", find_channel()["id"])
