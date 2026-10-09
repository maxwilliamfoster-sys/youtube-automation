# TRUE CRIME FORMAT REVIEW — ANALYSE, LEARN AND IMPROVE

This is the prompt the scheduled cloud routine runs (weekly, Saturday 09:00 UTC).
It is the owner's review brief (2026-10-09) plus the operating facts of this repo.
Edit this file to change what the review does — the routine just says "follow it".

## Operating facts (read first)

- Repo: `maxwilliamfoster-sys/youtube-automation` (PUBLIC — never commit usernames,
  secrets or tokens). True-crime TikTok @buriedcasefiles. Pipeline:
  `.github/workflows/documentary.yml` → `main_documentary.py --cloud` →
  `story_generator.py` (case, script, fact-check, compliance + legal gates) →
  `chatterbox_narrator.py` (voice) → `documentary_composer.py` (visuals, grade,
  captions, music) → `tiktok_buffer.py` (Buffer → TikTok).
- Data you have:
  - `analytics/snapshots.csv` — daily public stats per video (views, likes, comments,
    shares, saves, duration, post time, age_hours). One row per video per day, so
    compare videos at the SAME AGE (e.g. latest snapshot with age_hours 72–120), not
    raw totals of videos of different ages.
  - `analytics/comments.json` — all public comments (no usernames).
  - `analytics/posts_log.csv` — per post: `format` tag, narrator reference /
    exaggeration / cfg, grade, duration, case, softened flag, hook. Cohorts come from
    the `format` column. Videos before posts_log existed are cohort `v2-energetic`
    (2026-08-29 .. 2026-10-09) or `v1` (before 2026-08-29).
  - `analytics/studio_manual.csv` — TikTok Studio figures the owner typed in by hand
    (watch time, completion, retention, traffic, followers gained), if any. Studio data
    is NOT otherwise available: never invent it, say it is missing.
  - `QUALITY_LOG.md` — baseline, findings, test plan and changelog. Read it fully.
- You may run `python analytics/collect.py` for a fresh snapshot (public, no login).
  If tiktok.com blocks the request, use the committed snapshots instead.
- Known structural pattern (2026-10-09): videos land either ~250–380 views with a high
  like rate, or ~700–1,000 views with a low like rate. Likes per video are similar in
  both (~20), so the extra views from a wider push convert to almost no engagement.
  The main question is whether a change raises engagement WITHIN pushed videos and the
  share of videos that break past ~1,000.

## Step 0: Gate — is there enough data?

The current test cohort is the newest `format` value in `analytics/posts_log.csv`
(see QUALITY_LOG.md → Test plan for its start date). Proceed to a full review only if:
- at least 14 days have passed since the cohort's first post, AND
- at least 8 cohort videos are 72+ hours old.
If not: write nothing except one short line appended to QUALITY_LOG.md → "Review
attempts" (date, cohort size, what is missing), commit it, and stop. The routine runs
again in 7 days. Do not manufacture conclusions from thin data.

## Step 1–7 (owner's brief)

You are now conducting a scheduled evaluation of my true crime TikTok automation.
Retrieve all available analytics and comments since the last review.
Compare performance against the original baseline and previous review periods.
Focus especially on: new-format performance, narrator feedback, retention and
completion rate (only if Studio data exists), likes/comments/shares/saves, follower
conversion (only if data exists), story quality, hook effectiveness, script pacing,
visual quality, audio quality, topic selection.

1. **Is the data sufficient?** Number of videos, their age, available metrics. If the
   sample is too small, state what is missing and stop (Step 0).
2. **What changed?** Which metrics improved, declined or stayed similar. Account for
   duration, topic and video age. Do not attribute a change to one cause without
   evidence. Report sample sizes. Treat differences within ~20% on n<15 as noise.
3. **Comments.** Repeated feedback about the narrator, storytelling, pacing, visuals.
   Compare with previous feedback. Separate recurring trends from isolated opinions;
   discount emoji-only and spam comments. Quote real comments only.
4. **Decide.** Classify each candidate change as Retain / Improve / Test / Revert.
5. **Edit** only justified, low-risk, reversible changes. Preserve working features.
   Never touch: the compliance gate, the legal gate (`_legal_review`), the fact-check,
   the posting window, Buffer channel pinning, secrets. Never delete published videos.
   If you change the format or narrator, bump `FORMAT_VERSION` in config.py so the next
   cohort is separable, and record the old value so it can be reverted.
   Change one major variable at a time where possible.
6. **Report**: write `reviews/YYYY-MM-DD.md` with: overall channel health; new format vs
   baseline (table with n, median views, % pushed ≥600, like/comment/share/save rates,
   within-pushed like/save rates, mean duration); narrator feedback; strongest and
   weakest videos; three biggest findings; exact changes made (file, old → new); changes
   recommended but not made; evidence still needed; next review trigger. Append a dated
   entry to QUALITY_LOG.md → Changelog for every change.
7. **Continue the cycle.** The routine fires again next week; nothing to schedule.
   If performance is strong, change nothing. If inconclusive, keep collecting.

Commit with a clear message. Push to `main`; if the push is rejected, push a branch
`review/YYYY-MM-DD` and open a pull request instead, and say so in the report.
