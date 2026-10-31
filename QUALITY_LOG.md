# BuriedCasefiles — quality log, test plan and changelog

Living record for the true-crime TikTok automation. The weekly review routine
(docs/REVIEW_PROMPT.md) reads and appends to this file.

## 1. How the pipeline works (verified 2026-10-09)

| Stage | What runs | Notes |
|---|---|---|
| Schedule | `.github/workflows/documentary.yml`, crons 08:15 / 11:45 / 14:45 UTC | GitHub starts them 1–6 h late; posts land ~15:30–21:30 UK |
| Case | `case_source.py` Wikipedia UK-crime pool, scored for "intrigue" | `recent_cases.json` stops repeats |
| Script | `story_generator.py` Groq gpt-oss-120b (+ qwen3.8 / gpt-oss-20b fallback) | 100–120 words, hook → build → spike → open question + comment ask |
| Gates | fact-check (accuracy ≥7), TikTok compliance, soften-once, legal review, `publish_checks.py` | all fail-closed |
| Voice | `chatterbox_narrator.py` Chatterbox TTS cloning an edge-tts British reference clip | sentence-by-sentence, CPU on Actions |
| Numbers | `tts_generator.normalize_for_tts` | rewritten 2026-10-09 |
| Visuals | `image_generator.py` + `uk_media.py`: Wikimedia photos of the real place, Pexels fallback; Ken Burns motion | |
| Edit | `documentary_composer.py`: grade, word-by-word captions, hook card, end card, music ducked under voice | 1080×1920, 30 fps, ~6 Mb/s, −15 LUFS |
| Post | `tiktok_buffer.py` → Buffer → TikTok, AI label on, 08:00–22:00 UK window | |
| Analytics | `analytics/collect.py` daily via `analytics.yml` (public stats + comments) | added 2026-10-09 |

**Not available:** TikTok Studio metrics (watch time, % watched, completion,
retention, traffic sources, followers gained, profile views). They need the
@buriedcasefiles login; the Chrome used for automation is logged into another account.
Type them into `analytics/studio_manual.csv` from the app when possible.

## 2. Findings 2026-10-09 (61 videos, public data, totals at 1–120 days old)

**F1 — Two-tier views; the wider push doesn't convert.** 33 videos sit at 600–2,006
views (median 754), 28 at 250–380 (median 296). Likes per video are about the same in
both (21.6 vs 19.3), so the extra ~450 views a pushed video gets produce ~2 likes.
Like rate is 2.6% in the pushed group against 6.4% in the unpushed one. Likely
explanation: a small core audience likes everything; cold For You viewers leave
early. Confirming this needs retention data (Studio).

**F2 — The UK / energetic format gets pushed more but converts worse.** Baseline
v2-energetic (16 videos, 2026-08-29 to 10-08, mean 38 s): median 736 views, 12 of 16
pushed, like rate 3.0%, within-pushed like rate 2.18%, within-pushed save rate 0.22%.
Older v1 (45 videos, mean 59 s): median 374, 21 of 45 pushed, like rate 3.7%,
within-pushed like rate 2.86%, save rate 0.33%. This is a correlation. Topic (UK cases
with the case name first), length and narrator all changed together on 2026-08-28/29,
so the cause can't be separated.

**F3 — Narrator.** The one comment about the voice in 46: "What kind of voice over is
that ? It sounds like a children's entertainer giving kids a quiz 🤣" (Andrew
Cunningham, 2026-10-08). The owner independently judged the tone wrong. Config at the
time: reference `narrator_ref_energetic_ryan.wav`, exaggeration 1.0 (the top of the
range; Chatterbox's default is 0.5), cfg 0.42. Repeated? No: one viewer plus the owner.
Supported by the content fit, and research favours a restrained, journalistic read.

**F4 — Numbers read unnaturally.** The old normaliser handled only years, 1st–31st and
"4 pm". Dates ("April 18"), ages ("13-year-old"), decades, money, clock times ("09:10"
showed and was read as "09 10") and plain numbers were left to the TTS.

**F5 — Visuals undercut the tone.** Recent videos are mostly bright, sunny Wikimedia
photos (cottages, blue skies) under a grade tuned for dark night footage. Some photos
were off-topic: a blue tit, a white double-decker bus (Nicola Bulley video).

**F6 — Comments are thin and noisy.** 46 comments from 27 accounts. One account wrote
14, 13 of them emoji-only. About 10 comments have actual text. One "part 2 please"
(Alex Sloley, 67 s) is an isolated signal.

**F7 — Neither length nor posting time shows a clear effect** (26–82 s; morning to
evening). Sample sizes are small.

**F8 — Reliability.** Sept: 23/30 scheduled runs produced no video (Groq daily cap
plus the over-strict gate). Since 2026-10-03: 14 of 18 runs posted. Most misses are
now the legal gate blocking scripts that name people who were never convicted
(e.g. three cases in the 2026-10-09 15:18 UTC run; the names are deliberately not
recorded in this public file). That is intended; a missed slot is better than a libel risk. Watch the miss
rate: above ~1 in 3, prefer cases with a conviction or no identifiable suspects in
`case_source.py` scoring rather than loosening the gate.

## 3. Test plan

### Test T1 — calm narrator + overcast grade + worldwide cases + occasional series (format `v3-calm-world`)
- **Start:** first post after the commit that sets `FORMAT_VERSION=v3-calm-world`
  (expected 2026-10-10). The exact first video is in `analytics/posts_log.csv`.
- **Changed together** (recorded as one bundle, so the effects can't be fully
  separated):
  1. Narrator: `assets/narrator_ref_sombre_ryan.wav`, exaggeration 0.45, cfg 0.5,
     gap 0.5 s (was energetic_ryan 1.0 / 0.42 / 0.42). Side-effect: about 14% slower
     speech, so videos run roughly 15–20% longer (est. 44–50 s against 38 s). Compare
     like-for-like length where possible.
  2. Grade: `overcast` (was `night`).
  3. Number normaliser (always-on correctness fix, not a stylistic variable).
  4. Off-topic photo filter (correctness fix).
  5. Topics: cases worldwide (owner, 2026-10-09: "the most interesting ones
     available"), not UK-only. Ranked by intrigue plus Wikipedia pageviews (0–6
     bonus). Three scoring bonuses that had silently never applied since 2026-08-22
     (backspace characters in the regexes) now work.
  6. Series: some long, well-sourced cases (≥12k-char article, planner agrees, ≤1
     series per 3 days) go out as 2–3 parts in consecutive slots. Every part passes
     every gate before Part 1 posts.
- **Exclude from both cohorts:** video 7694727875798306080 (Keith Lyon, 2026-10-09
  17:55 UTC). It was rendered with the old energetic narrator but the new overcast
  grade (the run checked out before the narrator commit). It is tagged v2-energetic in
  posts_log.csv.
- **Unchanged:** single-video script prompt and structure, hook card, captions,
  music, length target (100–120 words), posting schedule, gates.
- **Analyse separately inside the cohort:** UK vs non-UK cases (`uk` = caption says
  "UK crime story"), series parts vs single videos (`series_part` in posts_log), and
  Part 1 vs later parts (later parts are mainly seen by people who saw Part 1).
- **Baseline:** v2-energetic, figures in F2.
- **Primary metric:** within-pushed like rate (videos ≥600 views) and within-pushed
  save rate, compared at a similar age (72–120 h).
- **Secondary metrics:** share of videos ≥600 views; share ≥1,000; comment rate;
  narrator comments (any repeat of the "children's entertainer" type = fail).
  Studio average watch time and completion, if the owner adds them.
- **Success (directional):** within-pushed like rate ≥2.7% (up from 2.18%) with the
  pushed share not falling below ~60%, over ≥8 videos aged 72 h+. Not statistically
  significant at this n; treat as direction only.
- **First review:** ≥14 days after the first v3 post, with ≥8 videos aged 72 h+.
  Earliest is about 2026-10-24. Weekly re-checks until then.
- **Revert:** set the env/config values back (see Changelog) and bump FORMAT_VERSION.

### Experiments queued (not defaults; need evidence first)
- E1: a specific, case-linked comment question instead of the generic "What do you
  think? Comment below" (comment rate is 0.11%).
- E2: (promoted into T1 by the owner on 2026-10-09: occasional 2–3 part series.)
- E3: shorter, quieter hook card vs none, once retention data is available.

## 4. Changelog

| Date | Change | Files | Revert |
|---|---|---|---|
| 2026-10-03 | Buffer auto-posting; 08:00–22:00 UK window | tiktok_buffer.py, documentary.yml | remove BUFFER_API_KEY secret → Telegram-only |
| 2026-10-03 | Soften-once on compliance block; Groq sibling fallback | story_generator.py | git revert 99323f8 |
| 2026-10-03 | Legal review gate; re-fact-check softened scripts | story_generator.py | do not revert (libel protection) |
| 2026-10-09 | Full British number normaliser | tts_generator.py, requirements.txt (num2words) | git revert the commit |
| 2026-10-09 | `overcast` grade default | documentary_composer.py | env `VISUAL_GRADE=night` |
| 2026-10-09 | Filter birds/vehicles/sport out of location photos | uk_media.py | remove the added alternations |
| 2026-10-09 | Daily public analytics + per-post format log | analytics/, analytics.yml, main_documentary.py | disable analytics.yml |
| 2026-10-09 | Voice test round 2 (5 variants, same script, loudness-matched) | voice_test.yml, assets/narrator_ref_sombre_*.wav | n/a |
| 2026-10-09 | **Narrator → calm (variant C)**: sombre_ryan ref, exag 1.0→0.45, cfg 0.42→0.5, gap 0.42→0.5; FORMAT_VERSION v2-energetic→v3-calm | config.py | env vars listed in config.py "REVERT" comment |
| 2026-10-09 | Worldwide case pool + pageview popularity bonus; fixed 8 backspace-corrupted `` regexes in intrigue_score | case_source.py, case_pool.json | remove the worldwide roots from ROOT_CATEGORIES (pool rebuilds) |
| 2026-10-09 | UK wording/hashtag/photo search only for UK cases | story_generator.py, uk_media.py, image_generator.py | n/a (correctness) |
| 2026-10-09 | Multi-part series (2–3 parts, all pre-gated, queued in series_queue.json) | series_queue.py, story_generator.py (_try_series, _finalise), main_documentary.py | set SERIES_MIN_CHARS very high in series_queue.py |
| 2026-10-09 | FORMAT_VERSION v3-calm → v3-calm-world (before any v3 post) | config.py | — |
| 2026-10-09 | Weekly cloud review routine (Sat 09:00 UTC) following docs/REVIEW_PROMPT.md | claude.ai routine | pause at claude.ai/code/routines |
| 2026-10-13 | **Visuals → v4-mood-visuals.** Found in the 10-12 Imbo video: hook frame was a festive-lit station, then Amman (ancient 'Philadelphia'), two rat photos and a SIDEWAYS Parkway photo; the 12 per-line mood queries were never used because Commons place photos won every scene. Now: hook + 2/3 of scenes use mood footage, place photos only scenes 3/6/9/12, file title must name the place, animals/paintings/shops/hotels refused, vivid-pixel gate (>20% refused), geograph photos ranked first, EXIF orientation applied. tests/test_visuals.py | image_generator.py, uk_media.py, image_safety.py, documentary_composer.py, config.py | `PLACE_SCENE_EVERY = 1` restores places on every scene |
| 2026-10-13 | **Wrong-place / people photos.** First Buffer watch-time data: the two 10-12 US videos had the month's worst retention (Imbo 15%, Maitland 12% vs 29-46% for other October posts). Maitland (Montgomery, VERMONT) showed Montgomery, Alabama + a Greetings-from-Montgomery postcard x3; searches also returned Field Marshal Montgomery and President Biden. Now: non-UK searches carry the case's state/country, titles must name it (or its US postal code), titles naming a different region are refused, people/event photos refused (defamation risk). | uk_media.py, image_generator.py, tests/test_visuals.py | git revert |
| 2026-10-13 | Buffer watch-time metrics → analytics/buffer_metrics.csv | analytics/collect.py, docs/REVIEW_PROMPT.md | delete buffer_metrics() call |

## 5. Review attempts
- 2026-10-10: gate not met. Newest cohort v3-calm (first post 2026-10-09 20:07 UTC) has 1 video, 0 aged 72h+, 0 days old (need 14 days and 8 videos aged 72h+). Earliest review about 2026-10-24.
- 2026-10-17: gate not met. Newest cohort v4-mood-visuals (first post 2026-10-13 16:24 UTC) has 8 videos, 3 aged 72h+, 4 days old (need 14 days and 8 videos aged 72h+). Earliest review about 2026-10-27; the 2026-10-24 run will also be too early.
- 2026-10-24: gate not met. Newest cohort v4-mood-visuals (first post 2026-10-13 16:24 UTC) has 15 videos, 12 posted ≥72h before the latest snapshot (2026-10-24 00:54 UTC) but only 10 days old (need 14 days). Earliest review: 2026-10-31 (Saturday run), when the cohort will be 14+ days old.
- 2026-10-31 (REPORT ONLY, no code/config changed): **gate met.** Cohort v4-mood-visuals (first post 2026-10-13 16:24 UTC) is 18 days old, 26 posts logged (2 without a video_id), 15 with a snapshot at 72–120 h (primary-metric sample); `studio_manual.csv` is empty, so no Studio watch time/completion/followers (missing, not invented).
  - **Table (views at 72–120 h; pushed = ≥600 views):** v4 n=15: median 312, pushed 40% (6/15), ≥1,000 views 0%, like 4.1%, comment 0.06%, share 0.10%, save 0.39%, **within-pushed like 2.63% / save 0.30% (n=6)**, mean 47.9 s. v3-calm-world n=6: median 762, pushed 83% (5/6), like 2.39%, comment 0.07%, save 0.30%, within-pushed like 2.03% / save 0.28% (n=5), 48.4 s. v2-energetic baseline (F2, 2026-10-09): median 736, 12/16 pushed, within-pushed like 2.18% / save 0.22%, 38 s. (v3-calm-world sample is small because only videos with a snapshot in the 72–120 h window count; v3-calm and v2 have 1 each, ignore.)
  - **Against the T1 success rule** (within-pushed like ≥2.7% AND pushed share ≥~60%): like rate 2.63% is just under 2.7% on n=6 (noise), and **pushed share fell to 40% from 83% (v3) / 75% (v2)**, so the rule is NOT met. Direction: within-pushed engagement slightly up, but far fewer videos get pushed and none passes 1,000 views. The higher overall like rate (4.1%) comes from the unpushed ~250–320-view bucket (the known two-bucket pattern), not from better conversion.
  - **Retention (Buffer averageTimeWatched ÷ duration, latest snapshot, posts before 2026-10-28; ages differ so indicative only):** v1 median 22% (n=30), v2 33% (n=17), v3 29% (n=8), **v4 27% (n=18)**; v4 range 15%–59%, the two lowest (15–16%) were pushed videos (Ricky Reel 10-15, Jean Sampare 10-14), the two highest were 10-18 posts. v4 mood visuals have NOT improved retention over v3 (27% vs 29%, inside noise at n<20). Watch time in seconds is flat (~12–13 s median) across v2/v3/v4, so most viewers leave in the first ~12 s regardless of narrator/visuals; the hook is the likelier lever (E3 and E1 remain untested).
  - **Comments since 10-09 with real text (6 of ~56; rest emoji-only):** "cover up me thinks" (10-30), "was this a true story 🤔" (10-14), a view on parole for killers who withhold a body's location (10-14), a theory the victim was abducted from her bike (10-12), "Unbelievable, & Heartbreaking" (10-09), "May they rest in peace?" (10-11). **No narrator, pacing or visual complaints; no repeat of the "children's entertainer" comment** (narrator test passes by absence). Comments are thin; engagement is about the story, not the production.
  - **Classification:** Retain narrator (calm) and overcast grade (no complaints; v3/v4 retention below v2's 33% but v2 was ~38 s vs ~48 s now, length confound). Test (not Retain) v4 mood visuals: no retention gain, pushed share down; it cannot be separated from topic mix (worldwide cases, series) or posting-time changes. Test E3/E1 (hook/comment question) next.
  - **Recommendations for the nightly experiment loop (not made here):** (1) check whether the drop in pushed share coincides with the v4 visuals or with the topic mix (UK vs non-UK, `uk` column) before reverting `PLACE_SCENE_EVERY`; (2) next single variable should target the first 12 s (shorter hook card or a stronger first line), measured on averageTimeWatched ÷ duration; (3) owner to type TikTok Studio retention/followers into `analytics/studio_manual.csv`; (4) two v4 posts have no video_id in posts_log.csv (10-19 21:14, 10-27 22:57); the 10-27 post is only 22 s, worth checking for a truncated render; one 10-30 post is 19 s.
  - **Next review:** 2026-11-07, or after the next FORMAT_VERSION change once it has 14 days and ≥8 videos aged 72 h+.
