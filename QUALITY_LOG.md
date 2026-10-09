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
plus the over-strict gate). Since 2026-10-03: 13 of 16 runs posted.

## 3. Test plan

### Test T1 — calm narrator + overcast grade + spoken numbers (format `v3-calm`)
- **Start:** first post after the commit that sets `FORMAT_VERSION=v3-calm`
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
- **Unchanged:** script prompt and structure, hook card, captions, music, length
  target (100–120 words), case source, posting schedule, gates.
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
- E2: two-part stories for cases with a lot of material (one "part 2 please").
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
| 2026-10-09 | Weekly cloud review routine (Sat 09:00 UTC) following docs/REVIEW_PROMPT.md | claude.ai routine | pause at claude.ai/code/routines |

## 5. Review attempts
