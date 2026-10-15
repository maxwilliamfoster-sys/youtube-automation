# Experiments: @buriedcasefiles (TikTok)

These rules come from `Projects/Content-Bots/growth-lab` (README, EXPERIMENT_TEMPLATE, growth_stats.py).
- Judge with `python analytics/compare.py`, which scores every video at 72h on average % watched.
- Guardrails: views@72h and likes per 1k.
- Each arm needs at least 15 and at most 30 posts. At 15 per arm, watch % can detect a lift of about 1.49x; views would need about 1.67x.
- Findings and the changelog stay in QUALITY_LOG.md. This file holds the pre-registrations.
- Nothing live changes without the owner's yes.

**From now on: one variable per test, assigned per post at random** (an `exp` JSON column in posts_log.csv; compare.py already reads it). No more before/after format bundles.

## T1 / v4 cohort review (before/after; legacy)         status: running
- What: `v3-calm-world` (T1, from 10-09) was followed on 10-13 by `v4-mood-visuals`, before T1 was judged.
- Primary metric: median avg % watched at 72h. Guardrails: views@72h, likes per 1k.
- Caveat: these are date cohorts, so time effects are mixed in. Treat the result as direction only.
- Judge: ~2026-10-24, once each cohort has ≥8 videos past 72h (QUALITY_LOG gate).
- Result: —

## E1: case-specific comment question                    status: planned (after the T1/v4 review)
- Hypothesis: A question tied to the specific case, instead of the generic "What do you think? Comment below", raises comments per 1k (currently 0.11%) without hurting watch %.
- Variable: the CTA line only. Arms: per-post coin flip p=0.5, recorded in posts_log `exp`.
- Primary metric: comments per 1k views at 72h. Guardrail: avg % watched.
- Min/max n per arm: 15 / 30.

## Queue (research 2026-10-15, growth-lab/research/reports/TikTok growth and testing methods.md)
1. A spoken cold open ("[place], [year], [odd fact]") with word-by-word captions vs the 3.6s boxed hook card (E3).
2. Narrator 15–25% faster (Scary Interesting speaks at 3.9–4.2 words/s).
3. End on the payoff line, with no outro.
4. Case mix weighted toward strange historical deaths with no living suspects.
5. Planned 2-part series with a pinned link from Part 1.

**First, at no cost:** in TikTok Studio, check the lowest-view posts for a "not eligible for For You" notice. The ~300 floor might be that, not the hook.
