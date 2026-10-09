"""
Multi-part stories (Part 1 / 2 / 3) for the occasional long, rich case.

Owner's rule (2026-10-09): "not for every video, just some really long and
interesting ones". So a series needs a long source article, the planner's agreement
that the material genuinely splits into distinct beats, no series already in
progress, and a few days since the last one.

All parts are written AND pass every gate (fact-check, compliance, legal) before
Part 1 is posted, so a series can never stop halfway because a later part failed a
check. Parts 2..N wait in series_queue.json (committed by CI) and go out in the next
posting slots, ahead of any new case. The queue is only written after Part 1 has
actually been delivered, and a part only leaves it after it has been delivered, so a
crash can't post Part 2 without Part 1 or skip a part.
"""
import json
import os
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
QUEUE_FILE = os.path.join(BASE_DIR, "series_queue.json")

SERIES_MIN_CHARS = 12000      # article length before a series is even considered
SERIES_THREE_PARTS = 22000    # long enough for three parts
SERIES_MIN_GAP_DAYS = 3       # days between the starts of two series
PART_MAX_AGE_DAYS = 4         # a stale queued part is dropped, not posted weeks later


def _load() -> dict:
    try:
        with open(QUEUE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"pending": [], "last_series_utc": ""}


def _save(state: dict) -> None:
    with open(QUEUE_FILE, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)


def can_start_new(now=None) -> bool:
    """No series in progress, and the last one started long enough ago."""
    state = _load()
    if state.get("pending"):
        return False
    last = state.get("last_series_utc")
    if not last:
        return True
    now = now or datetime.now(timezone.utc)
    started = datetime.fromisoformat(last.replace("Z", "+00:00"))
    return now - started >= timedelta(days=SERIES_MIN_GAP_DAYS)


def parts_for(source_chars: int) -> int:
    """How many parts an article of this length can carry (0 = not a series)."""
    if source_chars >= SERIES_THREE_PARTS:
        return 3
    if source_chars >= SERIES_MIN_CHARS:
        return 2
    return 0


def next_part():
    """The next queued part (a finished story dict), or None. Does not remove it."""
    state = _load()
    pending = state.get("pending") or []
    if not pending:
        return None
    part = pending[0]
    queued = datetime.fromisoformat(part.get("queued_utc", "2000-01-01T00:00:00+00:00"))
    if datetime.now(timezone.utc) - queued > timedelta(days=PART_MAX_AGE_DAYS):
        print(f"[Series] Dropping stale queued part {part.get('series_part')} of "
              f"{part.get('case_name')!r} (queued {part.get('queued_utc')}).")
        state["pending"] = []
        _save(state)
        return None
    part = dict(part)
    part["from_series_queue"] = True
    return part


def mark_delivered(story: dict) -> None:
    """
    Call after a video has been delivered. Part 1 of a new series queues the rest;
    a queued part is removed.
    """
    state = _load()
    if story.get("from_series_queue"):
        pending = state.get("pending") or []
        if pending and pending[0].get("series_part") == story.get("series_part") \
                and pending[0].get("case_name") == story.get("case_name"):
            pending.pop(0)
        state["pending"] = pending
        _save(state)
        print(f"[Series] Part {story.get('series_part')}/{story.get('series_total')} "
              f"delivered; {len(pending)} still queued.")
        return
    rest = story.get("series_rest") or []
    if rest:
        now = datetime.now(timezone.utc).isoformat()
        for p in rest:
            p["queued_utc"] = now
        state["pending"] = rest
        state["last_series_utc"] = now
        _save(state)
        print(f"[Series] Part 1/{story.get('series_total')} delivered; "
              f"{len(rest)} part(s) queued for the next slots.")
