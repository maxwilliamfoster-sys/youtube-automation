"""
Adaptive Strategy — the "brain" of the growth engine.

Reads performance_history.json and decides the next video's:
  • target length   (seconds)  — argmax with exploration; consistency is fine
  • horror sub-theme            — softmax-weighted sampling; we WANT variety
  • opening hook style          — softmax-weighted sampling

Design principles:
  • The horror niche is fixed. Only flavour/length within horror is tuned.
  • Bayesian shrinkage toward a prior means low-data options aren't trusted blindly,
    so the engine behaves sensibly from the very first run and sharpens as data grows.
  • Exploration (epsilon + softmax) stops it collapsing onto one option prematurely
    while the channel is still tiny and the signal is noisy.

Reward signal (per video):
  • retention (averageViewPercentage) when the Analytics API is enabled, else
  • age-normalised views/day (removes the "older videos have more views" confound),
  • plus a small engagement (likes/view) bonus.
"""

import math
import random

from config import (
    TARGET_DURATION_CANDIDATES, TARGET_DURATION_DEFAULT,
    HORROR_THEMES, HOOK_STYLES, BACKGROUND_CATEGORIES,
    ADAPTIVE_ENABLED, ADAPTIVE_EXPLORATION, ADAPTIVE_MIN_SAMPLES,
    STORY_WORD_MIN, STORY_WORD_MAX,
)
from performance_tracker import load_history, get_words_per_second, get_word_overshoot

# Length priors in views/day units, seeded from the channel's own age-normalised
# history (60–90s clearly outperforms 120s+). Used only until real samples accrue.
_LENGTH_PRIOR = {45: 0.36, 60: 0.46, 75: 0.46, 90: 0.40}
_RETENTION_PRIOR = 0.5   # neutral 50% prior when the reward basis is retention


# ─── Reward helpers ───────────────────────────────────────────────────────────

def _uses_retention(records: list) -> bool:
    return any(r.get("avg_view_pct") is not None for r in records)


def _reward(rec: dict, retention_basis: bool) -> float:
    if retention_basis and rec.get("avg_view_pct") is not None:
        base = rec["avg_view_pct"] / 100.0
    else:
        base = rec.get("views_per_day", 0.0)
    views = rec.get("views", 0) or 0
    likes = rec.get("likes", 0) or 0
    engagement = (likes / views) if views else 0.0
    return base + 0.5 * engagement


def _shrunk(rewards: list, prior: float, k: int = ADAPTIVE_MIN_SAMPLES) -> float:
    """Bayesian shrinkage: blend observed mean with a prior, weighted by sample count."""
    n = len(rewards)
    if n == 0:
        return prior
    return (prior * k + sum(rewards)) / (k + n)


def _softmax_pick(scores: dict, temperature: float = 0.5) -> str:
    """Weighted random choice over options, biased toward higher scores."""
    keys = list(scores.keys())
    if not keys:
        return None
    mx = max(scores.values())
    weights = [math.exp((scores[k] - mx) / max(1e-6, temperature)) for k in keys]
    total = sum(weights)
    r = random.uniform(0, total)
    upto = 0.0
    for k, w in zip(keys, weights):
        upto += w
        if upto >= r:
            return k
    return keys[-1]


# ─── Length / theme / hook selection ──────────────────────────────────────────

def _choose_length(history: dict, retention_basis: bool) -> tuple:
    observed = list(history.get("observed", {}).values())
    scores = {}
    for cand in TARGET_DURATION_CANDIDATES:
        near = [r for r in observed if abs(r.get("duration", 0) - cand) <= 15 and r.get("duration", 0) <= 170]
        rewards = [_reward(r, retention_basis) for r in near]
        prior = _RETENTION_PRIOR if retention_basis else _LENGTH_PRIOR.get(cand, 0.4)
        scores[cand] = _shrunk(rewards, prior)
    # epsilon-greedy: explore sometimes, otherwise take the best
    if random.random() < ADAPTIVE_EXPLORATION:
        choice = random.choice(TARGET_DURATION_CANDIDATES)
        why = "explore"
    else:
        choice = max(scores, key=scores.get)
        why = "exploit"
    return choice, scores, why


def _choose_attribute(history: dict, attr: str, options: list, retention_basis: bool) -> tuple:
    posts = [p for p in history.get("posts", {}).values() if p.get(attr) and p.get("stats")]
    # neutral prior = mean reward across all labelled posts (or 0 if none yet)
    all_rewards = [_reward(p["stats"], retention_basis) for p in posts if p.get("stats")]
    prior = (sum(all_rewards) / len(all_rewards)) if all_rewards else (_RETENTION_PRIOR if retention_basis else 0.4)
    scores = {}
    for opt in options:
        rewards = [_reward(p["stats"], retention_basis) for p in posts if p.get(attr) == opt and p.get("stats")]
        scores[opt] = _shrunk(rewards, prior)
    if random.random() < ADAPTIVE_EXPLORATION:
        choice = random.choice(options)
    else:
        choice = _softmax_pick(scores)
    return choice, scores


# ─── Public entry point ───────────────────────────────────────────────────────

def _words_for(seconds: float, wps: float, overshoot: float) -> int:
    """Words to REQUEST so the final spoken story lands near `seconds`.
    We want actual_words = seconds * wps, but the model returns ~overshoot× what we ask,
    so request that many divided by the overshoot ratio."""
    return _clamp_words(int(seconds * wps / max(0.5, overshoot)))


def get_strategy() -> dict:
    """
    Decide parameters for the next video.
    Returns: {target_seconds, target_words, theme, hook, words_per_second, rationale}
    """
    wps = get_words_per_second()
    overshoot = get_word_overshoot()

    if not ADAPTIVE_ENABLED:
        secs = TARGET_DURATION_DEFAULT
        return {
            "target_seconds": secs,
            "target_words":   _words_for(secs, wps, overshoot),
            "theme":          random.choice(HORROR_THEMES),
            "hook":           random.choice(HOOK_STYLES),
            "background":     random.choice(BACKGROUND_CATEGORIES),
            "words_per_second": wps,
            "rationale":      "adaptive disabled — defaults",
        }

    history = load_history()
    records = list(history.get("observed", {}).values())
    retention_basis = _uses_retention(records)

    secs, len_scores, why = _choose_length(history, retention_basis)
    theme, theme_scores   = _choose_attribute(history, "theme",      HORROR_THEMES,        retention_basis)
    hook, hook_scores     = _choose_attribute(history, "hook",       HOOK_STYLES,          retention_basis)
    background, bg_scores = _choose_attribute(history, "background", BACKGROUND_CATEGORIES, retention_basis)

    basis = "retention%" if retention_basis else "views/day"
    rationale = (
        f"len={secs}s ({why}, basis={basis}) | theme={theme} | hook={hook} | bg={background} | "
        f"len_scores={ {k: round(v,3) for k,v in len_scores.items()} }"
    )

    return {
        "target_seconds":   secs,
        "target_words":     _words_for(secs, wps, overshoot),
        "theme":            theme,
        "hook":             hook,
        "background":       background,
        "words_per_second": wps,
        "rationale":        rationale,
    }


def _clamp_words(w: int) -> int:
    return max(STORY_WORD_MIN, min(STORY_WORD_MAX, w))


if __name__ == "__main__":
    from collections import Counter
    print("Sample strategy:")
    s = get_strategy()
    for k, v in s.items():
        print(f"  {k}: {v}")
    print("\nDistribution over 200 draws (shows exploration spread):")
    c_secs, c_theme, c_hook, c_bg = Counter(), Counter(), Counter(), Counter()
    for _ in range(200):
        d = get_strategy()
        c_secs[d["target_seconds"]] += 1
        c_theme[d["theme"]] += 1
        c_hook[d["hook"]] += 1
        c_bg[d["background"]] += 1
    print("  length:", dict(c_secs.most_common()))
    print("  theme :", dict(c_theme.most_common()))
    print("  hook  :", dict(c_hook.most_common()))
    print("  bg    :", dict(c_bg.most_common()))
