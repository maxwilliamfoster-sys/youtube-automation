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

import requests

BUFFER_API = "https://api.buffer.com"
HANDLE = "buriedcasefiles"
KEEP = 6                     # ~40 MB each; Pages sites cap at 1 GB
MAX_HOST_MB = 95             # GitHub refuses files over 100 MB


class PublishError(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(os.environ.get("BUFFER_API_KEY"))


# ---------------------------------------------------------------- hosting --

def _git(args, cwd):
    r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise PublishError(f"git {' '.join(args[:2])} failed: {r.stderr.strip()[:300]}")
    return r.stdout


def _pages_base():
    owner, name = os.environ["GITHUB_REPOSITORY"].split("/")
    return f"https://{owner.lower()}.github.io/{name}"


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
        old = sorted(f for f in os.listdir(os.path.join(tmp, "v")) if f.endswith(".mp4"))
        for f in old[: max(0, len(old) - (KEEP - 1))]:
            os.remove(os.path.join(tmp, "v", f))
        shutil.copy(path, os.path.join(tmp, "v", filename))
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

def _gql(query, variables=None):
    key = os.environ.get("BUFFER_API_KEY")
    if not key:
        raise PublishError("BUFFER_API_KEY not set")
    r = requests.post(BUFFER_API, json={"query": query, "variables": variables or {}},
                      headers={"Authorization": f"Bearer {key}"}, timeout=60)
    if r.status_code != 200:
        raise PublishError(f"Buffer HTTP {r.status_code}: {r.text[:300]}")
    body = r.json()
    if body.get("errors"):
        raise PublishError(f"Buffer error: {body['errors'][0].get('message')}")
    return body["data"]


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


def post_video(video_path, caption, timeout=900):
    """
    Host the MP4, share it to TikTok now, wait for TikTok to take it.
    Returns (ok, detail). Never raises.
    """
    try:
        chan = find_channel()
        if os.path.getsize(video_path) / 1048576 > MAX_HOST_MB:
            raise PublishError(f"video is over {MAX_HOST_MB} MB - too big to host on Pages")
        filename = os.path.basename(video_path)
        url = host_video(video_path, filename)
        inp = {
            "text": caption,
            "channelId": chan["id"],
            "schedulingType": "automatic",
            "mode": "shareNow",
            "needsApproval": False,
            "assets": [{"video": {"url": url, "metadata": {"thumbnailOffset": 1500}}}],
            # The narration is an AI voice and the script is LLM-written, so the
            # AIGC label goes on. Undisclosed AI content is what TikTok penalises.
            "metadata": {"tiktok": {"isAiGenerated": True}},
        }
        data = _gql("""mutation($i: CreatePostInput!) { createPost(input: $i) {
            ... on PostActionSuccess { post { id status } }
            ... on MutationError { message } } }""", {"i": inp})
        res = data["createPost"]
        if "post" not in res:
            raise PublishError(f"Buffer rejected the post: {res.get('message')}")
        post_id = res["post"]["id"]
        print(f"[buffer] shareNow -> {chan['name']}: {post_id}")

        deadline = time.time() + timeout
        post = _get_post(post_id)
        while time.time() < deadline and post["status"] in ("scheduled", "sending"):
            time.sleep(20)
            post = _get_post(post_id)
            print(f"[buffer] {post_id}: {post['status']}")
        if post["status"] == "sent":
            return True, post.get("externalLink") or f"Buffer post {post_id}"
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
