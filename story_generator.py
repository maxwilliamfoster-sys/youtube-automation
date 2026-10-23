"""
Story Generator — uses Groq (FREE) + gpt-oss-120b to generate horror stories
and true crime documentary scripts with research + fact-checking.
"""

import re
import difflib
import json
import os
import random
import time
from datetime import datetime, timezone
import requests as _requests
from groq import Groq, RateLimitError, APIStatusError, APIConnectionError

import case_screen
import case_source
import llm_client
from config import (
    GROQ_API_KEY, GROQ_MODEL, STORY_TYPES, STORY_WORD_COUNT,
    STORY_WORD_MIN, STORY_WORD_MAX, GROQ_REASONING_EFFORT, GOOGLE_API_KEY,
)

# ── LLM backend constants ─────────────────────────────────────────────────────
# Groq retired llama-3.1-8b-instant alongside llama-3.3-70b-versatile on 2026-08-16.
# Nothing referenced this constant, so it broke nothing — but leaving a decommissioned
# model id lying around is a trap for whoever wires up a cheap-tier call next.
GROQ_FALLBACK_MODEL   = "openai/gpt-oss-20b"   # currently unused; Groq's 8b-instant successor

# Cerebras (402 since Sept 2026) and OpenRouter (free models withdrawn) were removed
# 2026-10-19: dead fallbacks only hid outages. Groq's sibling model is the fallback.
_use_fallback: bool = False      # flips on once Groq's primary model is unusable




# Groq meters the daily token cap per MODEL, so when gpt-oss-120b runs dry (the key
# is shared with other pipelines) a sibling may still have budget left. NEVER the
# upload gate's review model: its free quota (200k tokens/day) is shared by all 3
# bots' AI reviews, and a writer falling back to it starved the reviews (audit
# 2026-10-19). Of the key's models on 2026-10-19 only gpt-oss-20b is a usable sibling.
REVIEW_ONLY_MODELS = {"qwen/qwen3.8-27b"}
GROQ_SIBLING_MODELS = [
    ("openai/gpt-oss-20b", {"reasoning_effort": "low"}),
]
_dead_groq_siblings: set = set()
_last_sibling_error = None     # kept so a later call still says WHY the siblings are unusable


def _groq_sibling_call(**kwargs) -> object:
    """Same request on Groq's other free models. Raises if none answers."""
    global _last_sibling_error
    client = Groq(api_key=GROQ_API_KEY)
    last = _last_sibling_error
    for model, extra in GROQ_SIBLING_MODELS:
        if model in _dead_groq_siblings or model in REVIEW_ONLY_MODELS:
            continue
        if _budget_spent(model):
            last = f"{model}: Groq budget spent today (ledger)"
            _dead_groq_siblings.add(model)
            continue
        try:
            resp = client.chat.completions.create(**{**kwargs, "model": model, **extra})
            _budget_record(model, resp)
            if (resp.choices[0].message.content or "").strip():
                return resp
            last = "empty reply"
        except RateLimitError as e:
            last = e
            if "tokens per day" in str(e).lower() or "tpd" in str(e).lower():
                print(f"[LLM] Groq {model} daily cap reached too.")
                _dead_groq_siblings.add(model)
                _budget_daily_cap(model, e)
        except (APIStatusError, APIConnectionError) as e:
            last = e
            print(f"[LLM] Groq {model} failed ({str(e)[:100]}).")
    _last_sibling_error = last
    raise RuntimeError(f"Groq sibling models unavailable: {str(last)[:160]}")


# Google retires Gemini models on a schedule (gemini-2.5-flash answered 404 "no longer available"
# on 2026-10-20), so the rolling -latest aliases come first (llm_client.GEMINI_MODELS).
GEMINI_MODELS = llm_client.GEMINI_MODELS


def _gemini_call(**kwargs) -> object:
    """Same request on Gemini's free OpenAI-compatible endpoint, through growth-lab llm_client:
    404/429/5xx/timeout/empty reply -> the next Gemini model at once; if every model failed for a
    passing reason, ONE back-off (Retry-After, max 30 s) and one more pass; 400/401/403 raise at
    once. Run 39810389929 died because the old loop gave up on the first 503 ("high demand").
    Raises RuntimeError on failure (the callers' contract)."""
    kw = {k: kwargs[k] for k in ("max_tokens", "temperature") if k in kwargs}
    # llm_client reads its keys from the environment; hand it config's key for this call only.
    old = os.environ.get("GOOGLE_API_KEY")
    os.environ["GOOGLE_API_KEY"] = GOOGLE_API_KEY or ""
    try:
        return llm_client.chat(
            kwargs.get("messages", []), groq_models=llm_client.GROQ_MODELS,
            exclude_models=set(llm_client.GROQ_MODELS) | {m for m, _ in GROQ_SIBLING_MODELS} | REVIEW_ONLY_MODELS,
            gemini_models=GEMINI_MODELS, max_wait=30.0, sleep=lambda s: time.sleep(s), **kw)
    except llm_client.LLMError as e:
        raise RuntimeError(f"Gemini backup failed: {str(e)[:240]}") from e
    finally:
        if old is None:
            os.environ.pop("GOOGLE_API_KEY", None)
        else:
            os.environ["GOOGLE_API_KEY"] = old


# ── Groq budget ledger (growth-lab llm_client.GroqBudget) ─────────────────────
# Groq meters tokens per MODEL per organisation per day, shared with every bot on the key.
# The cloud run enables a small JSON ledger (GROQ_BUDGET_LEDGER, committed by documentary.yml)
# so a run can stop BEFORE rendering when the writer or the gate's review model is spent.
# Disabled (None) by default: local runs and tests never write a ledger.
WRITER_NEED_TOKENS = 30_000      # one approved script costs about this much on the writer chain
REVIEW_NEED_TOKENS = 9_000       # one upload-gate AI review (the request is capped near 7k)
_budget = None
_budget_path = None
_ORG_USE_RE = re.compile(r"Limit\s+(\d+),\s*Used\s+(\d+)", re.I)


def enable_budget(path) -> None:
    """Turn the per-model Groq ledger on (path) or off (None)."""
    global _budget, _budget_path
    _budget_path, _budget = path, None


def groq_budget():
    """The run's GroqBudget, or None when the ledger is disabled. Re-read on a new UTC day."""
    global _budget
    if not _budget_path:
        return None
    if _budget is None or _budget.day != llm_client._today():
        _budget = llm_client.GroqBudget(_budget_path)
    return _budget


def _budget_save(b) -> None:
    try:
        b.save()
    except OSError as e:
        print(f"[LLM] budget ledger not saved ({e})")


def _budget_record(model: str, resp) -> None:
    b = groq_budget()
    if b is None:
        return
    tokens = getattr(getattr(resp, "usage", None), "total_tokens", 0)
    b.record(model, tokens if isinstance(tokens, int) else 0)
    _budget_save(b)


def _budget_daily_cap(model: str, err) -> None:
    b = groq_budget()
    if b is None:
        return
    b.mark_exhausted(model)
    m = _ORG_USE_RE.search(str(err))
    if m:
        b.note_org_usage(model, used=int(m.group(2)), limit=int(m.group(1)))
    _budget_save(b)


def _budget_spent(model: str) -> bool:
    b = groq_budget()
    return b is not None and b.remaining(model) <= 0


def record_review(gate_v: dict) -> None:
    """Book the upload gate's AI review (its own Groq model) into the ledger."""
    b = groq_budget()
    if b is None:
        return
    import upload_gate
    ai = (gate_v or {}).get("ai") or {}
    if ai.get("quota") and not ai.get("cap"):
        b.mark_exhausted(upload_gate.AI_MODEL)
    elif isinstance(ai.get("tokens"), int) and ai["tokens"] > 0:
        b.record(upload_gate.AI_MODEL, ai["tokens"])
    else:
        return
    _budget_save(b)


def writer_preflight():
    """Can this run still produce a postable video today? Returns SimpleNamespace(ok, reason).
    The writer needs WRITER_NEED_TOKENS on gpt-oss-120b or a sibling (or, failing both, the Gemini
    backup when GOOGLE_API_KEY is set); the upload gate's mandatory AI review needs
    REVIEW_NEED_TOKENS on its own model, which has no backup. Always ok with the ledger disabled."""
    from types import SimpleNamespace
    b = groq_budget()
    if b is None:
        return SimpleNamespace(ok=True, reason="no ledger")
    import upload_gate
    writers = [GROQ_MODEL] + [m for m, _ in GROQ_SIBLING_MODELS if m not in REVIEW_ONLY_MODELS]
    review = llm_client.preflight(b, [(REVIEW_NEED_TOKENS, [upload_gate.AI_MODEL])])
    if not review.ok:
        return SimpleNamespace(ok=False, reason=f"review: {review.reason}")
    writer = llm_client.preflight(b, [(WRITER_NEED_TOKENS, writers),
                                      (REVIEW_NEED_TOKENS, [upload_gate.AI_MODEL])])
    if writer.ok:
        return SimpleNamespace(ok=True, reason=f"ok (writer {writer.plan.get(0)})")
    if GOOGLE_API_KEY:
        return SimpleNamespace(ok=True, reason=f"writer on the Gemini backup ({writer.reason})")
    return SimpleNamespace(ok=False, reason=f"writer: {writer.reason}")


def _fallback_call(**kwargs) -> object:
    """Everything after Groq's primary model: its sibling models (separate daily
    caps), then Gemini's free tier if GOOGLE_API_KEY is set. Raises when none
    answers, so the run fails loudly and alerts (the gates still fail closed)."""
    try:
        return _groq_sibling_call(**kwargs)
    except RuntimeError as groq_err:
        if not GOOGLE_API_KEY:
            raise
        print(f"[LLM] Groq exhausted ({str(groq_err)[:80]}) - trying the Gemini backup.")
        try:
            return _gemini_call(**kwargs)
        except RuntimeError as g:
            raise RuntimeError(f"{groq_err}; {g}") from g


def _groq_kwargs(**kwargs) -> dict:
    """
    Request tweaks for the primary Groq model only (siblings carry their own extras).

    gpt-oss is a reasoning model and reasoning tokens are billed against the same
    100k/day free allowance the scripts need, so effort is pinned low. Groq accepts
    `reasoning_effort` on gpt-oss models only — hence the guard, so that pointing
    GROQ_MODEL at a non-reasoning model later does not start producing 400s.
    """
    if "gpt-oss" in str(kwargs.get("model", "")):
        kwargs.setdefault("reasoning_effort", GROQ_REASONING_EFFORT)
    return kwargs


def _groq_call(client: Groq, **kwargs) -> object:
    """
    LLM router: Groq's primary model, then its sibling models (_fallback_call).
    Only a DAILY cap (or a Groq backend that has stopped answering entirely) retires
    the primary for the session. A per-minute throttle is waited out and retried,
    because it clears in seconds.
    """
    global _use_fallback

    if _use_fallback:
        return _fallback_call(**kwargs)

    primary = str(kwargs.get("model", ""))
    if _budget_spent(primary):
        # The ledger already knows today's bucket for this model is empty: don't pay a 429 to learn it.
        print(f"[LLM] Groq {primary} budget spent today (ledger) — using the sibling model.")
        _use_fallback = True
        return _fallback_call(**kwargs)

    def _create():
        resp = client.chat.completions.create(**_groq_kwargs(**kwargs))
        _budget_record(primary, resp)
        return resp

    try:
        return _create()
    except RateLimitError as e:
        msg = str(e)
        # Hard daily token cap — switch permanently to the fallback (no wait)
        is_daily = "tokens per day" in msg.lower() or "tpd" in msg.lower()
        if is_daily:
            print("[LLM] Groq daily token limit reached — switching to the sibling model.")
            _budget_daily_cap(primary, e)
            _use_fallback = True
            return _fallback_call(**kwargs)
        # A per-minute throttle is temporary and says nothing about the day's budget,
        # so it must NOT retire Groq for the session. It used to: one blip flipped the
        # router permanently, the run fell into a fallback chain that is currently dead
        # (Cerebras 402, OpenRouter free models withdrawn) and the whole video was lost
        # while Groq was answering normally seconds later.
        for attempt in range(3):
            m = re.search(r'try again in (?:(\d+)m\s*)?(\d+(?:\.\d+)?)s', msg)
            wait = (float(m.group(1) or 0) * 60 + float(m.group(2)) + 3) if m else 20
            wait = min(wait, 75)
            print(f"[LLM] Groq RPM limit — waiting {wait:.0f}s "
                  f"(attempt {attempt + 1}/3)...")
            time.sleep(wait)
            try:
                return _create()
            except RateLimitError as again:
                msg = str(again)
                if "tokens per day" in msg.lower() or "tpd" in msg.lower():
                    print("[LLM] Groq daily cap reached — switching to the fallback chain.")
                    _budget_daily_cap(primary, again)
                    _use_fallback = True
                    return _fallback_call(**kwargs)
                continue
            except (APIStatusError, APIConnectionError):
                break
        # Still throttled after three waits: try the fallback for THIS call only,
        # and if that fails too, let the error surface rather than silently
        # abandoning a backend that is probably fine by the next call.
        print("[LLM] Groq still throttled — using the fallback for this call only.")
        return _fallback_call(**kwargs)
    except (APIStatusError, APIConnectionError) as e:
        # Anything Groq refuses that is NOT a rate limit: a retired model (404
        # model_not_found), a rejected parameter (400), an outage (5xx), a dead socket.
        #
        # This branch is the actual reason five days of runs died. Groq retired
        # llama-3.3-70b-versatile on 2026-08-16; every run since raised NotFoundError
        # here, which nothing caught, so the run aborted with Cerebras and OpenRouter
        # both configured and neither one tried. A backend that has stopped answering
        # is precisely what the fallback chain exists for, so treat it like the daily
        # cap: switch permanently and carry on. A retired model will not come back
        # mid-run, and neither will an outage worth re-probing on every call.
        print(f"[LLM] Groq unavailable ({type(e).__name__}: {str(e)[:160]}) — "
              f"switching to the fallback chain for the rest of this run.")
        _use_fallback = True
        return _fallback_call(**kwargs)


# Prompts grouped by horror SUB-THEME. The adaptive engine picks a theme (biased
# toward whatever performs best) and we draw a random prompt from it. Keys MUST match
# config.HORROR_THEMES.
HORROR_PROMPTS_BY_THEME = {
    "supernatural": [
        "Write a horror story about a woman who notices her reflection has started moving half a second too late.",
        "Write a horror story about a teenage boy whose Ouija board begins answering questions before anyone touches the planchette.",
        "Write a terrifying story about a nurse who finds her patient in a different room each morning with no memory of moving.",
        "Write a chilling story about a child who tells her parents about the tall figure that stands at the foot of her bed every night.",
        "Write a scary story about an antique music box bought at an estate sale that plays a lullaby for a dead child.",
        "Write a horror story about a small coastal town where everyone stopped aging thirty years ago — except one woman.",
        "Write a horror story about a couple who rent a remote cottage and slowly realise the previous tenants never left.",
    ],
    "technology": [
        "Write a horror story about a man who receives a voicemail from his own number — recorded three hours after his death.",
        "Write a scary story about a woman whose smart home locks her inside and speaks in her dead mothers voice.",
        "Write a creepy story about a software engineer who finds a deepfake video of himself committing a crime he has no memory of.",
        "Write a horror story about a teenage girl who discovers a Reddit account documenting her daily routine for two years.",
        "Write a terrifying story about a security guard who notices CCTV footage shows events that havent happened yet.",
        "Write a chilling story about a grief counsellor whose AI therapy app reveals things only the deceased could have known.",
    ],
    "psychological": [
        "Write a horror story about a detective investigating a series of murders who realises all the evidence points to himself.",
        "Write a creepy story about a woman who notices her furniture has been moved by inches each night while she slept.",
        "Write a terrifying story about a man who cannot tell whether the last six months of his life were real or a coma dream.",
        "Write a horror story about a hiker found alive after eleven years missing — who has not aged a single day.",
        "Write a scary story about a grief support group where every member shares the exact same recurring nightmare.",
        "Write a horror story about a soldier who returns home only to find his family has no memory of him ever existing.",
    ],
    "wilderness": [
        "Write a horror story about a trail runner who finds a fully set campsite deep in a forest — coffee still warm, no one around.",
        "Write a creepy story about a lighthouse keeper who begins receiving Morse code from a ship that sank in 1943.",
        "Write a terrifying story about a family whose GPS leads them off the highway onto a road that does not exist on any map.",
        "Write a scary story about a research team in Antarctica who begin to suspect one of their colleagues is not human.",
        "Write a horror story about a survivalist who sets cameras around his remote cabin and reviews footage each morning.",
    ],
    "domestic": [
        "Write a horror story about a babysitter who realises the children she is watching are not the same children from the family photo.",
        "Write a chilling story about a coroner who receives a body for autopsy and recognises it as someone he spoke to that morning.",
        "Write a scary story about a woman who sees the same stranger on her commute every day — always in exactly the right place.",
        "Write a creepy story about a plumber who discovers a fully furnished living space hidden behind the walls of a family home.",
        "Write a horror story about a woman who arrives for a job interview only to find the company has no record of contacting her.",
        "Write a terrifying story about a man who steps into an elevator on the 14th floor and the doors open on a floor that should not exist.",
    ],
    "body": [
        "Write a horror story about a surgeon who wakes from a coma to find unexplained surgical scars covering his body.",
        "Write a creepy story about a woman whose shadow moves independently — always one step ahead of her.",
        "Write a terrifying story about a man who finds his own teeth sealed in jars hidden throughout his house.",
        "Write a scary story about a sleepwalker whose husband installs cameras and is horrified by where she goes each night.",
        "Write a horror story about identical twins where one begins to suspect the other was replaced by something else years ago.",
    ],
}
# Flat list kept for any caller that still wants "all prompts".
HORROR_PROMPTS = [p for prompts in HORROR_PROMPTS_BY_THEME.values() for p in prompts]

# Opening-hook styles the engine can choose. Keys MUST match config.HOOK_STYLES.
HOOK_INSTRUCTIONS = {
    "cold_detail": "OPEN on a single concrete, unsettling detail stated plainly. Example: 'The voicemail had no timestamp.'",
    "in_action":   "OPEN mid-action, already in motion. Example: 'I was halfway down the cellar stairs when the light died.'",
    "discovery":   "OPEN on the narrator discovering or noticing something wrong. Example: 'I found a second door behind the wardrobe.'",
    "overheard":   "OPEN on something heard — a sound, a voice, a message. Example: 'The knocking started at 3 a.m. Three taps. Always three.'",
}


# ─── LLM output sanitisation & validation ─────────────────────────────────────
# This is the safety layer that stops reasoning-model chain-of-thought, instruction
# echoes, or rambling from ever reaching the video. EVERY story/title/proofread
# response is passed through here.

_THINK_RE = re.compile(r"<(think|reasoning|thought|scratchpad)[^>]*>.*?</\1>",
                       re.IGNORECASE | re.DOTALL)
_PREAMBLE_RE = re.compile(
    r"^\s*(?:sure|okay|ok|alright|certainly|of course|here(?:'s| is)(?: the| a)?|"
    r"let me|i'?ll|i will|i need to|we need to|first[,]?|to write|note that|"
    r"as requested|below is)[^\n.!?]*[:.]\s*",
    re.IGNORECASE)

# Hard markers that mean the generation FAILED (instruction echo / meta / refusal).
# Any one of these in a story or title triggers a regenerate.
_HARD_META = [
    "youtube short", "the user wants", "the user asked", "word count", "max 8 word",
    "no hashtags", "no quotes", "first person", "past tense", "as an ai", "i cannot",
    "i can't write", "<think", "</think", "step 1", "here is the story",
    "here's the story", "here is a story", "here's a story", "we need to produce",
    "i'll write", "character count", "the prompt", "the instruction",
]


def _sanitize_llm_text(text: str) -> str:
    """Strip reasoning blocks, markdown, conversational preamble and wrapping quotes."""
    if not text:
        return ""
    text = _THINK_RE.sub(" ", text)
    # If a <think> opened but never closed, keep only what's after the last close tag.
    low = text.lower()
    if "</think>" in low:
        text = text[low.rfind("</think>") + len("</think>"):]
    # Drop markdown fences / headers.
    text = re.sub(r"```[a-zA-Z]*", " ", text).replace("```", " ")
    text = re.sub(r"^\s*#+\s*", "", text, flags=re.MULTILINE)
    # Strip a leading "Title:"/"Story:" style label.
    text = re.sub(r"^\s*(?:title|story|script|output)\s*[:\-]\s*", "", text, flags=re.IGNORECASE)
    # Remove conversational preamble (up to two passes).
    for _ in range(2):
        text = _PREAMBLE_RE.sub("", text.strip())
    text = text.strip().strip('"').strip("'").strip()
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _has_hard_meta(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in _HARD_META)


def _is_valid_story(text: str, min_words: int) -> bool:
    if not text:
        return False
    if len(text.split()) < max(20, min_words):
        return False
    if _has_hard_meta(text):
        return False
    return True


def _is_valid_title(title: str) -> bool:
    if not title:
        return False
    wc = len(title.split())
    if wc == 0 or wc > 12 or len(title) > 100:
        return False
    return not _has_hard_meta(title)


def _clamp_to_words(text: str, max_words: int) -> str:
    """Final safety net: never let a story exceed max_words. Trims to last full sentence."""
    words = text.split()
    if len(words) <= max_words:
        return text
    truncated = " ".join(words[:max_words])
    ends = list(re.finditer(r"[.!?]", truncated))
    if ends and ends[-1].end() > len(truncated) * 0.5:
        return truncated[:ends[-1].end()].strip()
    return truncated.strip().rstrip(",;:") + "."


# Absolute last-resort story so the pipeline NEVER posts garbage if every attempt fails.
_FALLBACK_STORY = (
    "The knocking started at 3 a.m. Three taps, always three, against the inside of "
    "the bedroom wall. I told myself it was the pipes the first night. By the third "
    "night I had pressed my ear to the plaster and heard breathing on the other side. "
    "There was no other side. My flat backed onto a solid brick wall, no cavity, no "
    "neighbour. I called a builder, who tapped the wall and frowned and asked who lived "
    "in the room behind mine. I told him no one could. He showed me the survey: a sealed "
    "space, just large enough for a person, walled up decades ago. We opened it that "
    "afternoon. Inside was a single wooden chair facing my wall, and the plaster on my "
    "side was worn smooth, as if something had sat there for years, listening to me sleep."
)

def _story_system_prompt(target_words: int, hook_instruction: str) -> str:
    """Build the storyteller system prompt for a specific target length and hook style."""
    lo, hi = target_words - 10, target_words + 15
    return f"""You are a master horror storyteller writing scripts for YouTube Shorts.

Rules:
- Write EXACTLY one complete story with a clear beginning, middle, and terrifying end
- You MUST write between {lo} and {hi} words — count carefully. Do NOT exceed {hi} words.
- Write ENTIRELY in FIRST PERSON ("I", "my", "me", "myself") — never switch to any other POV
- Write ENTIRELY in PAST TENSE — every action, thought and observation must be past tense ("I heard", "I saw", "it was", "I walked", "I noticed", "I felt")
- The story MUST make complete logical sense from start to finish — events must follow causally, no plot holes, no contradictions, no unresolved loose ends
- Each sentence must naturally follow from the one before — the reader should always know where they are and what is happening
- {hook_instruction}
- Make each story feel UNIQUE — different setting, tone, pacing and twist every time
- End with a gut-punch twist or horrifying realisation the reader never saw coming
- VARY your sentence openings — NEVER start two consecutive sentences with "I"
- NEVER open with the words "dark", "darkness", "shadow", "shadows"
- NEVER open with cliches like "It was a stormy night" or "I was alone"
- Use short punchy sentences for pacing — they build dread
- The story should feel like a viral Reddit r/nosleep post — personal, believable, terrifying
- Output ONLY the story text. No preamble, no explanation, no titles, no notes, no markdown, no XML or <think> tags. Begin directly with the first sentence of the story."""


def _story_makes_sense(client: Groq, story: str) -> tuple:
    """
    Ask the LLM to confirm the story is ONE coherent narrative before we commit to it.
    Returns (ok: bool, reason: str). Fails OPEN (returns True) on any error so a flaky
    checker can never block posting — structural validation already caught the worst.
    """
    try:
        resp = _groq_call(
            client, model=GROQ_MODEL, max_tokens=260,
            messages=[
                {"role": "system", "content": (
                    "You verify short horror stories for coherence. Reply with ONLY JSON: "
                    '{"makes_sense": true or false, "reason": "<=8 words"}. '
                    "makes_sense is TRUE only if the text is ONE coherent first-person story that "
                    "flows logically start to finish — no contradictions, no missing context, no "
                    "abrupt unexplained jumps, no repetition, and a clear ending. "
                    "It is FALSE if it rambles, repeats, contradicts itself, is cut off mid-thought, "
                    "or reads like notes or instructions rather than a story.")},
                {"role": "user", "content": story},
            ],
        )
        txt = resp.choices[0].message.content
        m = re.search(r'"makes_sense"\s*:\s*(true|false)', txt, re.IGNORECASE)
        if not m:
            return True, "unparsed (fail-open)"
        ok = m.group(1).lower() == "true"
        rm = re.search(r'"reason"\s*:\s*"([^"]*)"', txt)
        return ok, (rm.group(1) if rm else ("coherent" if ok else "incoherent"))
    except Exception as e:
        return True, f"check-skipped ({e})"


def _generate_title(client: Groq, story_text: str) -> str:
    """Generate a short creepy title, sanitised and validated, with a safe fallback."""
    for _ in range(2):
        resp = _groq_call(
            client, model=GROQ_MODEL, max_tokens=200,
            messages=[{
                "role": "user",
                "content": (
                    "Output ONLY a short, creepy YouTube Short title of at most 8 words. "
                    "No quotes, no hashtags, no explanation, no preamble — just the title text.\n\n"
                    f"Story:\n{story_text[:300]}"
                ),
            }],
        )
        raw = resp.choices[0].message.content.strip()
        # If the model echoed the instructions / leaked reasoning, reject the whole
        # response — sanitising can leave a meaningless fragment that looks valid.
        if _has_hard_meta(raw):
            continue
        title = _sanitize_llm_text(raw)
        title = title.splitlines()[0].strip() if title else ""   # first line only
        if _is_valid_title(title):
            return title
    # Fallback: build a title from the story's first sentence.
    first = re.split(r"[.!?]", story_text.strip())[0].split()
    return " ".join(first[:7]).title() or "A Horror Story"


def generate_story(strategy: dict = None) -> dict:
    """
    Generate a horror story, hardened against reasoning-leak / garbled / over-long output.

    Args:
        strategy: optional dict from adaptive_strategy.get_strategy() with keys
                  'theme', 'hook', 'target_words'. If omitted, picks at random.

    Returns dict: story, title, type, hashtags, theme, hook, target_words, word_count
    """
    client = Groq(api_key=GROQ_API_KEY)
    story_type = random.choice(STORY_TYPES)

    # Resolve adaptive parameters (fall back to random / defaults).
    theme = (strategy or {}).get("theme") or random.choice(list(HORROR_PROMPTS_BY_THEME))
    if theme not in HORROR_PROMPTS_BY_THEME:
        theme = random.choice(list(HORROR_PROMPTS_BY_THEME))
    hook = (strategy or {}).get("hook") or random.choice(list(HOOK_INSTRUCTIONS))
    if hook not in HOOK_INSTRUCTIONS:
        hook = random.choice(list(HOOK_INSTRUCTIONS))
    target_words = int((strategy or {}).get("target_words") or STORY_WORD_COUNT)
    target_words = max(STORY_WORD_MIN, min(STORY_WORD_MAX, target_words))

    system_prompt = _story_system_prompt(target_words, HOOK_INSTRUCTIONS[hook])
    theme_prompts = HORROR_PROMPTS_BY_THEME[theme]
    max_tokens = min(900, target_words * 5 + 150)
    min_acceptable = max(STORY_WORD_MIN, target_words - 40)

    print(f"[Story] Generating {story_type} story - theme={theme}, hook={hook}, "
          f"target~{target_words} words")

    def _build_one() -> str:
        """Generate one fully-processed story: validated -> proofread -> clamped."""
        story = None
        raw = ""
        for attempt in range(1, 4):
            prompt = random.choice(theme_prompts)
            resp = _groq_call(
                client, model=GROQ_MODEL, max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": prompt},
                ],
            )
            raw = resp.choices[0].message.content.strip()
            cand = _sanitize_llm_text(raw)
            if _is_valid_story(cand, min_acceptable):
                story = cand
                break
            print(f"[Story] Attempt {attempt}/3 rejected (meta/garbled/short) — regenerating")

        if story is None:
            salvaged = _sanitize_llm_text(raw)
            story = salvaged if _is_valid_story(salvaged, STORY_WORD_MIN) else _FALLBACK_STORY
            print("[Story] Using salvaged response." if story is salvaged else "[Story] Using safe fallback story.")

        # Proofread (POV / tense / coherence). Keep only if still valid.
        try:
            proof_resp = _groq_call(
                client, model=GROQ_MODEL, max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": (
                        "You are a strict editor for short horror stories. Rewrite the story fixing ALL of the following:\n"
                        "1) FIRST PERSON ONLY — every sentence must use I/my/me/myself. "
                        "Remove any 'he', 'she', 'they', 'you', 'we' that refer to the narrator.\n"
                        "2) PAST TENSE THROUGHOUT — convert every present-tense verb to past tense. "
                        "'I see' → 'I saw', 'it is' → 'it was', 'I hear' → 'I heard'. No exceptions.\n"
                        "3) COHERENCE — events must follow a logical order, nothing contradicts, no unresolved threads.\n"
                        "4) VARIED SENTENCE STARTS — never two consecutive sentences starting with 'I'.\n"
                        "5) Keep the same word count, tone, and ending twist.\n"
                        "Output ONLY the corrected story text — no preamble, no notes, no tags.")},
                    {"role": "user", "content": story},
                ],
            )
            proofed = _sanitize_llm_text(proof_resp.choices[0].message.content.strip())
            if _is_valid_story(proofed, min_acceptable):
                story = proofed
        except Exception as e:
            print(f"[Story] Proofread skipped ({e})")

        return _clamp_to_words(story, STORY_WORD_MAX)

    # ── Build + coherence gate: accept the first story that fully makes sense ──
    story_text = _build_one()
    for c_attempt in range(1, 3):     # up to 2 coherence-driven rebuilds
        ok, reason = _story_makes_sense(client, story_text)
        if ok:
            print(f"[Story] Coherence check passed ({reason})")
            break
        print(f"[Story] Coherence check FAILED ({reason}) — rebuilding ({c_attempt}/2)")
        story_text = _build_one()

    # ── Title ──
    title = _generate_title(client, story_text)

    # ── Hashtags ──
    try:
        hashtag_response = _groq_call(
            client, model=GROQ_MODEL, max_tokens=220,
            messages=[{
                "role": "user",
                "content": (
                    "Output ONLY 5 relevant YouTube hashtags for this horror story, separated by "
                    "spaces, no explanation:\n\n" + story_text[:200]
                ),
            }],
        )
        raw_tags = _sanitize_llm_text(hashtag_response.choices[0].message.content.strip())
        tag_words = raw_tags.replace("\n", " ").split()
        cleaned_tags = [
            w if w.startswith("#") else f"#{w}"
            for w in tag_words if w.replace("#", "").isalnum()
        ]
        story_hashtags = " ".join(cleaned_tags[:5])
    except Exception:
        story_hashtags = "#horror #scary #creepy #nosleep #horrorstory"

    word_count = len(story_text.split())
    print(f"[Story] Generated: '{title}' ({word_count} words, theme={theme}, hook={hook})")

    return {
        "story":        story_text,
        "title":        title,
        "type":         story_type,
        "hashtags":     story_hashtags,
        "theme":        theme,
        "hook":         hook,
        "target_words": target_words,
    }


if __name__ == "__main__":
    result = generate_story()
    print("\n" + "="*60)
    print(f"TITLE: {result['title']}")
    print("="*60)
    print(result['story'])
    print("="*60)
    print(f"Word count: {len(result['story'].split())}")


# ═══════════════════════════════════════════════════════════════════════════════
# TRUE CRIME DOCUMENTARY GENERATOR
# ═══════════════════════════════════════════════════════════════════════════════

TRUE_CRIME_SCRIPT_PROMPT = """You are the scriptwriter for @buriedcasefiles — a true crime documentary TikTok channel.
Your scripts are engineered for RETENTION: every sentence must earn the next second of watch time.
The single most important metric is how many people watch past the first 3 seconds, and how many watch to the very end.

MANDATORY STRUCTURE (in this exact order):
1. THE COLD OPEN — sentence 1: ONE complete sentence of 10 words or fewer. It is also shown on
   screen in full, so it must stand alone and never trail off with "...". The single most
   shocking, concrete, verifiable fact of the case, with one concrete specific (a number, a
   name or a place) inside it — but NOT a "In 1982" / "On [date]" preamble at the start. Drop
   the viewer straight into the most impossible or unexplained detail so they CANNOT scroll
   away. Never state or imply that anyone not convicted is guilty.
   GOOD: "His diary entries continued for seven years after his death."
   GOOD: "The plane landed with three more passengers than had boarded."
   BANNED openings: "In [year]", "On [date]", "Imagine", "Picture this", "This is the story of" — they kill retention.
2. THE BUILD — escalating beats. Each sentence raises a NEW question before fully resolving the last,
   so an open loop is always running and there is never a clean place to swipe away.
3. THE RETENTION SPIKE — around the two-thirds mark, re-hook anyone starting to drift with the
   single most unsettling fact THE SOURCE ACTUALLY CONTAINS. e.g. "But that wasn't the strangest part."
   CRITICAL: if the source has no dramatic twist, use its most unusual real detail — an odd
   timing, an unexplained gap, a thing that was never found. Do NOT invent a twist to fill this
   slot. A quiet true script beats a thrilling false one; inventing here fails fact-check and the
   whole script is discarded.
4. THE ENDING — last 1-2 sentences land on the haunting, still-UNRESOLVED question, then a short
   open question plus an invitation to comment. e.g. "The case has never been solved. What do you think happened? Comment below."
   Ask what the viewer THINKS about the unanswered question and invite a comment. Do NOT ask them to name, guess or accuse a suspect, and do not frame the death itself as a quiz — a moderator reads that as sensationalising a real tragedy. This closing line is the ONLY meta line allowed.

CONTENT SAFETY (TikTok - breaking this gets the video blocked before it is ever posted):
- The shock comes from MYSTERY (what was never found, never explained, never solved), NEVER
  from injuries. No wounds, blows, weapons striking, blood, the state of a body, how someone
  died in physical terms, or anything sexual. "She was found dead" is the most detail allowed.
- Never describe a child victim's death beyond the bare fact of it.
- Respectful to victims and their families; never make the offender sound impressive.

Style rules:
- Cold, authoritative documentary-narrator tone — like a Netflix true crime voiceover.
- Short, punchy sentences. Vary rhythm: a very short sentence after a longer one lands like a hammer.
- Real names, real dates, real locations — every factual claim must be accurate. The shock comes from REAL details, never invented ones.
- NEVER invent: names of relatives or suspects, autopsy or forensic findings, quotes, times of
  day, weather, what someone was wearing or thinking. If the source does not state it, it does
  not go in the script. These are real people with real families.
- Being sparse is allowed. If the source is thin, write a shorter script rather than padding it
  with plausible-sounding detail.
- Write 100-120 words (~40-45 seconds spoken). NEVER fewer than 95 — a script under
  that is too thin to tell the case and leaves the viewer with nothing. Completion
  rate drives reach, and a viewer who finishes a 45-second video is worth far more
  than one who abandons a 70-second one. Cut every sentence that is not the hook,
  a hard fact, the turn, or the closing question.
- NO "In this video", NO "In this short", NO channel name, NO "subscribe". The only meta line allowed is the final comment-bait CTA.
- Output ONLY the spoken script text. No headings, no labels, no stage directions."""


# Track used cases across the session to avoid repeats
_USED_CASES: list = []

# Persisted across runs (committed back in CI) so the daily videos never repeat a case.
RECENT_CASES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recent_cases.json")


def _load_recent_cases() -> list:
    try:
        with open(RECENT_CASES_FILE, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_recent_case(name: str, keep: int = 60) -> None:
    """Append a case to the persisted recent-cases list (de-duplicated, capped)."""
    if not name:
        return
    cases = [c for c in _load_recent_cases() if not _same_case(c, name)]
    cases.append(name)
    try:
        with open(RECENT_CASES_FILE, "w", encoding="utf-8") as f:
            json.dump(cases[-keep:], f, indent=2)
    except Exception:
        pass


# ─── Case de-duplication ──────────────────────────────────────────────────────
# The model is asked to avoid recent cases, but a 70B model ignores that hint and
# keeps returning its handful of favourites. Exclusion is therefore enforced HERE,
# in code, not left to the prompt.

# Words that carry no identifying information — stripped before comparing, so
# "The Murder of the Osborne Family" and "The Murders of the Osborne Family"
# collapse to the same key ("osborne family") and count as one case.
_FILLER_RE = re.compile(
    r"\b(the|a|an|of|in|at|on|case|files?|murders?|killings?|deaths?|disappearances?|"
    r"vanishings?|mystery|mysteries|mysterious|incident|affair|massacres?|"
    r"poisonings?|unsolved|strange)\b",
    re.I,
)

# The model sometimes answers the exclusion instruction conversationally and the
# reply lands in case_name, e.g. "X is excluded, instead: Y". Never accept those.
_BAD_NAME_RE = re.compile(
    r"(excluded|exclude|instead|already used|as requested|avoid|cannot|sorry|"
    r"here (is|are)|i (will|can|have))",
    re.I,
)


def _norm_case(name: str) -> str:
    """Reduce a case name to a comparable key: lowercase, no punctuation, no filler."""
    s = re.sub(r"[^\w\s]", " ", (name or "").lower())
    s = _FILLER_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _same_case(a: str, b: str) -> bool:
    """True if two case names refer to the same case (exact, or near-identical)."""
    ka, kb = _norm_case(a), _norm_case(b)
    if not ka or not kb:
        return False
    if ka == kb or ka in kb or kb in ka:
        return True
    return difflib.SequenceMatcher(None, ka, kb).ratio() >= 0.85


def _is_duplicate(name: str, recent: list) -> bool:
    return any(_same_case(name, r) for r in recent)


def _valid_case_name(name: str) -> bool:
    """Reject empty, over-long, or model-chatter case names."""
    if not name or not name.strip():
        return False
    if len(name) > 90 or len(name.split()) > 12:
        return False
    return not _BAD_NAME_RE.search(name)


def _pick_fresh_case() -> dict:
    """
    A real, previously-unused case from Wikipedia: {title, extract, url}.

    The model is never asked to name a case. Left to choose it either repeats its
    favourites forever or invents one outright — see case_source for the details.
    Returns {} if no case could be sourced.
    """
    sourced = case_source.pick_case(lambda t: _is_duplicate(t, _USED_CASES))
    if sourced:
        # Filtering and scoring ran on the intro (cheap, across a whole shortlist).
        # Now that one case is chosen, pull the FULL article: intros run ~300 chars
        # while the articles run 4,000-7,000. Writing a 170-word script from 300
        # characters is what forced the model to invent relatives' names, autopsy
        # results and weather, and held fact-check accuracy at 3-4/10.
        # Uncapped here (24k) so a long case can be considered for a multi-part series;
        # single videos still only ever see the first 7,000 characters, as before.
        full = case_source.get_full_text(sourced["title"], cap=24000)
        if len(full) > len(sourced.get("extract", "")):
            print(f"[TrueCrime] Source: {len(sourced['extract'])} -> {len(full)} chars "
                  f"(full article)")
            sourced["full_text"] = full
            sourced["extract"] = full[:7000]
        print(f"[TrueCrime] Sourced: {sourced['title']}  ({sourced['url']})")
    return sourced


def _extract_hook(script: str, max_words: int = 12) -> str:
    """Legacy: the first sentence, cut at max_words with '...'. NOT used for the hook card any
    more (14 of 24 hooks ended in '...'); kept for old callers. Use _hook_for / _cold_open."""
    if not script:
        return ""
    first = _first_sentence(script)
    words = first.split()
    if len(words) > max_words:
        first = " ".join(words[:max_words]).rstrip(",;:") + "…"
    return first


# ── Cold open (MASTER_PLAN #12): one complete line of <= 10 words, never truncated ──────
COLD_OPEN_MAX_WORDS = 10


def _first_sentence(script: str) -> str:
    if not script:
        return ""
    return re.split(r"(?<=[.!?])\s+", script.strip())[0].strip().strip('"').strip()


def _cold_open_issues(line: str, case: dict) -> list:
    """growth-lab hook_lint in true-crime mode. Fails, plus the guilt-wording warning, which
    is a hard no for an on-screen line about a real case. Returns issue messages ([] = ok)."""
    import hook_lint
    specifics = [s for s in (str(case.get("year") or ""), str(case.get("location") or ""),
                             str(case.get("case_name") or "")) if s]
    r = hook_lint.lint(hook=line, true_crime=True, max_words=COLD_OPEN_MAX_WORDS,
                       specifics=specifics)
    bad = list(r.fails) + [w for w in r.warns if w.code == "legal_guilt_unqualified"]
    return [f"{i.code}: {i.message}" for i in bad]


def _hook_for(script: str, case: dict) -> str:
    """The hook card text: the script's first sentence if it is a valid cold open, else ""
    (no card). Never shortened."""
    first = _first_sentence(script)
    return first if first and not _cold_open_issues(first, case) else ""


def _write_cold_open(client, case: dict, script: str) -> str:
    resp = _groq_call(client, model=GROQ_MODEL, max_tokens=300, messages=[{
        "role": "user",
        "content": (
            "Write the COLD OPEN for this true crime TikTok narration: the first line the "
            "narrator says, also shown on screen.\n"
            f"- ONE complete sentence of {COLD_OPEN_MAX_WORDS} words or fewer. Never trail off "
            "with '...'.\n"
            "- It must contain one concrete specific from the script: a number or year, a "
            "place, or a name.\n"
            "- Use ONLY facts stated in the script below. No greeting, no 'in this video'.\n"
            "- Never state or imply that anyone not convicted by a court is guilty; use "
            "'accused' or the court outcome if a suspect is mentioned at all. Prefer the "
            "victim, the place or the mystery.\n"
            "- No injuries, weapons or the state of a body.\n"
            "Output ONLY the sentence.\n\n"
            f"Case: {case.get('case_name', '')}, {case.get('location', '')}, {case.get('year', '')}\n"
            f"Script:\n{script}"),
    }])
    line = _sanitize_llm_text(resp.choices[0].message.content or "")
    line = line.strip().splitlines()[0].strip().strip('"“”\'').strip() if line.strip() else ""
    return line


def _cold_open(client, case: dict, script: str):
    """(cold_open, script). Keeps the script's first sentence when it already is a valid cold
    open; otherwise asks the writer for one (one cheap call) and puts it in front of the script,
    so it is spoken, captioned from frame 0 and read by every later review. If that line fails
    the lint too, returns ("", script): no hook card rather than a cut-off one."""
    first = _first_sentence(script)
    issues = _cold_open_issues(first, case) if first else ["hook_empty"]
    if not issues:
        return first, script
    print(f"[ColdOpen] first sentence is not a cold open ({'; '.join(issues)[:160]}) - asking for one")
    try:
        line = _write_cold_open(client, case, script)
    except Exception as e:
        print(f"[ColdOpen] writer failed ({str(e)[:120]}) - no hook card")
        return "", script
    issues = _cold_open_issues(line, case) if line else ["hook_empty"]
    if issues:
        print(f"[ColdOpen] rewrite rejected ({'; '.join(issues)[:160]}): {line[:120]!r} - no hook card")
        return "", script
    if not line.endswith((".", "!", "?")):
        line += "."
    print(f"[ColdOpen] {line}")
    return line, f"{line} {script}"


def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of an LLM response."""
    try:
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
    except Exception:
        pass
    return {}


def _research_case(client: Groq, sourced: dict) -> dict:
    """
    Turn a sourced Wikipedia case into the structured JSON the pipeline expects.

    The model summarises supplied source text — it is not asked to recall anything.
    That is the whole point: recall is where both the repetition and the invented
    cases came from.
    """
    resp = _groq_call(client,
        model=GROQ_MODEL,
        max_tokens=1600,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a true crime researcher. You will be given the encyclopaedia "
                    "entry for a real case. Extract its details for a short documentary, "
                    "focusing on shocking twists, unresolved questions and haunting details.\n"
                    "Use ONLY facts present in the supplied text. Never add details from "
                    "memory. If the text does not state something, leave that field vague "
                    "rather than inventing it.\n"
                    "Reply ONLY with valid JSON — no markdown, no explanation:\n"
                    '{"case_name":"...","location":"...","year":"...","summary":"...",'
                    '"key_facts":["fact1","fact2","fact3","fact4","fact5"],'
                    '"unresolved":"...","why_compelling":"..."}'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Case: {sourced['title']}\n\n"
                    f"Encyclopaedia entry:\n{sourced['extract'][:6000]}"
                ),
            },
        ],
    )
    case = _extract_json(resp.choices[0].message.content.strip())
    if case:
        # Always the sourced title, never the model's paraphrase — de-duplication is
        # tracked against these titles, and a reworded name would defeat it.
        case["case_name"] = sourced["title"]
        case["source_url"] = sourced.get("url", "")
        case["source_text"] = sourced["extract"]
        case["full_text"] = sourced.get("full_text") or sourced["extract"]
    return case


SERIES_SOURCE_CHARS = 9000     # a series covers more of the article than one video


def _part_instruction(part: dict) -> str:
    k, n = part["k"], part["n"]
    plan = "\n".join(f"  Part {i}: {b}" for i, b in enumerate(part["beats"], 1))
    words = {1: "one", 2: "two", 3: "three"}
    if k == 1:
        opening = ("Open with a cold open: ONE complete sentence of 10 words or fewer on the "
                   "single most shocking verifiable detail of THIS part, with a number, name or "
                   "place in it. Do not say 'part one'.")
    else:
        # A real cold open, not "Part two." (plan-bcf action 6): the first second decides
        # whether a viewer who never saw part one stays.
        opening = ("Open with a cold open: ONE complete sentence of 10 words or fewer on the "
                   "most shocking verifiable detail of THIS part's new material, with a "
                   "number, name or place in it (do not open with the part number). Then ONE "
                   "short sentence recapping where the story stood, then move straight into "
                   "new material. Do not repeat facts from earlier parts beyond that line.")
    if k < n:
        ending = (f"End on an unresolved cliffhanger from THIS part's material, then the "
                  f"exact line 'Part {words[k + 1]} next.' No comment request, and never "
                  f"ask viewers to follow.")
    else:
        ending = ("End on the case's still-unresolved question, then a short invitation "
                  "to comment on what viewers think happened (never ask them to accuse a "
                  "named person).")
    return (
        f"This case is told in {n} parts. Series plan:\n{plan}\n\n"
        f"Write PART {k} of {n} ONLY - cover only: {part['beat']}. 100-120 words (never "
        f"fewer than 95). {opening} Keep an open loop running throughout. {ending}"
    )


def _write_script(client: Groq, case: dict, part: dict = None) -> str:
    """
    Write a documentary script based on the researched case.

    `part` (series only): {"k": 2, "n": 3, "beat": "...", "beats": [...]} — write just
    that part of a multi-part story, from the longer source.
    """
    facts = "\n".join(f"- {f}" for f in case.get("key_facts", []))

    # Hand over the full encyclopaedia entry, not just the condensed summary. Working
    # from five key facts alone, the model had too little material for the "jaw-dropping
    # twist" this prompt demands, so it invented one — and the fact-checker then
    # rejected the script at 6/10 for claims the source did not support, burning an
    # attempt each time. The source also carries far more real detail to draw on.
    source = case.get("source_text", "")
    if part:
        source = case.get("full_text") or source
    source_block = (
        f"SOURCE (the encyclopaedia entry — the ONLY permitted source of facts):\n"
        f"{source[:SERIES_SOURCE_CHARS if part else SCRIPT_SOURCE_CHARS]}\n\n"
        if source else ""
    )
    grounding = (
        "Every factual claim — names, dates, places, events, the twist — must appear in "
        "the SOURCE above. Dramatise the TELLING, never the facts. If the source has no "
        "jaw-dropping twist, build the spike from the most unsettling detail that IS "
        "there. An invented detail fails fact-check and the script is discarded.\n\n"
        if source else ""
    )

    messages_for_retry = [
            {"role": "system", "content": TRUE_CRIME_SCRIPT_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Case: {case.get('case_name','')}, {case.get('location','')}, {case.get('year','')}\n\n"
                    f"{source_block}"
                    f"Background: {case.get('summary','')}\n\n"
                    f"Key facts:\n{facts}\n\n"
                    f"Still unresolved: {case.get('unresolved','')}\n\n"
                    f"{grounding}"
                    + (_part_instruction(part) if part else
                       "Write the 100-120 word documentary script now (never fewer than 95). Open with the cold open "
                       "(one complete sentence of 10 words or fewer on the single most shocking verifiable detail), "
                       "keep an open loop running throughout, add a retention-spike "
                       "twist near the two-thirds mark, and end on the unresolved question with a comment-bait CTA.")
                ),
            },
    ]

    # 600 deliberately. At 900 the model rambled to ~600 words (a 3.5-minute video);
    # at 600 it lands on 180-210 and scores 9/10. It ignores the word count in the
    # prompt, so the token ceiling is what actually holds the script to length.
    resp = _groq_call(client, model=GROQ_MODEL, max_tokens=600,
                      messages=messages_for_retry)
    script = (resp.choices[0].message.content or "").strip()
    if not script:
        # gpt-oss occasionally spends its whole budget on hidden reasoning and returns
        # an empty string. One retry is almost always enough.
        print("[TrueCrime] Script came back empty — retrying once...")
        resp = _groq_call(client, model=GROQ_MODEL, max_tokens=600,
                          messages=messages_for_retry)
        script = (resp.choices[0].message.content or "").strip()
    return script


def _fact_check(client: Groq, case: dict, script: str) -> dict:
    """
    Fact-check, quality-score, and TikTok community-guidelines check.

    tiktok_safe is False if the script:
      - describes minor victims in a graphic or exploitative way
      - glorifies or celebrates the perpetrator
      - contains gratuitous gore or torture detail
      - discusses self-harm or suicide methods in detail
    """
    resp = _groq_call(client,
        model=GROQ_MODEL,
        max_tokens=1400,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a senior fact-checker and compliance reviewer for a true crime "
                    "documentary TikTok channel. Evaluate the script on accuracy, quality, "
                    "AND TikTok Community Guidelines. Reply ONLY with valid JSON:\n"
                    '{"accuracy_score":1-10,"interest_score":1-10,"makes_sense":true/false,'
                    '"tiktok_safe":true/false,"issues":["issue1"],"approved":true/false}\n\n'
                    "Approve ONLY if ALL of these are true:\n"
                    "  - accuracy >= 7 (no invented names/dates/events)\n"
                    "  - interest >= 7 (genuinely compelling)\n"
                    "  - makes_sense = true (coherent narrative start to finish)\n"
                    "  - tiktok_safe = true\n\n"
                    "Set tiktok_safe = false if the script:\n"
                    "  - describes minor victims in a graphic or exploitative manner\n"
                    "  - glorifies, celebrates, or sensationalises the perpetrator\n"
                    "  - contains gratuitous gore, torture, or graphic violence details\n"
                    "  - discusses methods of self-harm or suicide in detail\n"
                    "  - dehumanises victims or promotes hate based on identity"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Case being covered: {case.get('case_name','')}\n\n"
                    + (
                        # Score accuracy against the real source, not the model's memory.
                        # Graded from memory it once passed a wholly invented case at 8/10.
                        f"SOURCE OF TRUTH (encyclopaedia entry for this case):\n"
                        f"{case['source_text'][:6000]}\n\n"
                        f"Script:\n{script}\n\n"
                        "Check the script ONLY against the source of truth above. "
                        "accuracy_score must reflect how well the script's names, dates and "
                        "events match that source. Any claim in the script that the source "
                        "does not support is an invented claim — list it in issues and score "
                        "accuracy below 7. "
                        if case.get("source_text") else
                        f"Script:\n{script}\n\n"
                        "Check: Are all facts accurate? Are dates/names/events real? "
                    )
                    + "Does the narrative make complete logical sense? Is it genuinely interesting? "
                      "Is it compliant with TikTok Community Guidelines? "
                      "Flag any invented, embellished, or policy-violating claims."
                ),
            },
        ],
    )
    result = _extract_json(resp.choices[0].message.content.strip())
    # Fail CLOSED. These defaults previously assumed a script was safe and approved when
    # the model's JSON failed to parse — a garbled reply silently became a pass on the
    # guidelines gate. An unparseable review is not evidence of safety.
    parsed = bool(result)
    if not parsed:
        print("[TrueCrime] Fact-check reply unparseable — treating as a rejection.")
    return {
        "accuracy_score": result.get("accuracy_score", 0),
        "interest_score":  result.get("interest_score",  0),
        "makes_sense":     result.get("makes_sense",     False),
        "tiktok_safe":     result.get("tiktok_safe",     False),
        "issues":          result.get("issues",          [] if parsed else ["fact-check reply unparseable"]),
        "approved":        result.get("approved",        False),
    }


# ─── TikTok Community Guidelines gate ─────────────────────────────────────────
# TikTok sorts sensitive content into tiers, and only the top one is worth posting:
#   OK              — recommendable in the For You feed.
#   FYF_INELIGIBLE  — allowed to exist, but never recommended. Effectively zero reach.
#   AGE_RESTRICTED  — 18+ only. Craters reach and signals borderline content.
#   BANNED          — removal and a strike against the account.
# Anything below OK is refused: FYF_INELIGIBLE gets no views, and repeatedly posting
# borderline material is what accumulates strikes. Better no video than a strike.
# A script below this is factually unsound — it invents detail about real victims.
# Never publishable, not even as a last-resort fallback.
MIN_FALLBACK_ACCURACY = 6

# A narration below this is not a script. Anything shorter means the model
# returned nothing usable, and every downstream stage — fact-check, title, TTS —
# behaves unpredictably when handed it.
MIN_SCRIPT_WORDS = 40
# ~250 words is about 95 seconds narrated — already past the sweet spot.
MAX_SCRIPT_WORDS = 260
# Trim to this before rejecting. ~115 words is about 45 seconds narrated.
# Short-form reach follows completion rate, and a 45s video is far likelier
# to be watched to the end than a 70s one — the previous target.
TRIM_SCRIPT_WORDS = 115
# How much source the SCRIPT WRITER sees. The fact-checker still gets the full
# article. With 6000 characters in front of it the model tried to cover the whole
# piece and ran to 380-620 words against a 150-185 target — it treats a long source
# as a brief to summarise rather than a well to draw from. 2800 is ample for 60
# seconds and removes the temptation to ramble.
SCRIPT_SOURCE_CHARS = 2800

COMPLIANCE_OK = "OK"
_COMPLIANCE_TIERS = ("OK", "FYF_INELIGIBLE", "AGE_RESTRICTED", "BANNED")


def compliance_review(client: Groq, script: str, caption: str, case: dict,
                      cta: str = "") -> dict:
    """
    Final, independent Community Guidelines review of exactly what will be posted.

    Deliberately separate from _fact_check: that call is asked to judge accuracy and
    entertainment value at the same time, and a reviewer scoring "is this compelling?"
    is a poor judge of "will this get the account struck". This one does nothing else,
    and sees the caption too — the caption is posted alongside the video and is just as
    capable of breaking a rule.

    Fails closed: an unparseable or errored review is a BANNED verdict, never a pass —
    but only AFTER a retry. A single transient glitch (the model occasionally returns
    an empty or non-JSON reply) was enough to discard a case that would have passed,
    and a run of those helped a whole slot produce no video. The retry keeps the
    fail-closed guarantee while not throwing away a good case over one bad response.
    """
    messages = [
        {
            "role": "system",
            "content": (
                "You are a TikTok Trust & Safety moderator reviewing a true crime "
                "video before it is posted. Be strict: the account's survival "
                "depends on you, and a false pass is far more costly than a false "
                "reject.\n\n"
                "Classify into exactly one verdict:\n"
                "- BANNED: would be removed / earn a strike. Extremely graphic, "
                "gory or disturbing detail; glorifying, celebrating or promoting "
                "the perpetrator or the crime; instructions for a harmful act; "
                "hate or dehumanisation; sexualising anyone; sexual or graphic "
                "detail involving a minor; self-harm or suicide methods; targeted "
                "harassment of a real identifiable living person; graphic detail "
                "of a real named victim's injuries or death.\n"
                "- AGE_RESTRICTED: mature themes fit only for 18+ — significant "
                "violence, disturbing imagery, or frank discussion of killing.\n"
                "- FYF_INELIGIBLE: not removable, but too shocking, distressing "
                "or sensationalised for the For You feed. Includes dwelling on a "
                "real tragedy for shock value.\n"
                "- OK: suitable for general recommendation. Covers the case "
                "factually and respectfully, without graphic detail.\n\n"
                "Judge the SCRIPT, the CAPTION and the ON-SCREEN CARD together "
                "— all three are published.\n"
                "Reply ONLY with valid JSON:\n"
                '{"verdict":"OK|FYF_INELIGIBLE|AGE_RESTRICTED|BANNED",'
                '"reasons":["..."],"worst_line":"..."}'
            ),
        },
        {
            "role": "user",
            "content": (
                f"Case: {case.get('case_name','')}\n\n"
                f"SCRIPT (spoken aloud over the video):\n{script}\n\n"
                f"CAPTION (posted with the video):\n{caption}\n\n"
                f"ON-SCREEN CARD (burned into the final seconds):\n{cta or '(none)'}"
            ),
        },
    ]

    # Two tries: the model occasionally returns an empty or non-JSON reply, and one
    # such glitch used to discard a case that would have passed. Retry once, then fail
    # closed — the guarantee is preserved, a good case is not thrown away over a blip.
    for parse_attempt in range(2):
        try:
            resp = _groq_call(client, model=GROQ_MODEL, max_tokens=1400, messages=messages)
            result = _extract_json(resp.choices[0].message.content.strip())
            verdict = str(result.get("verdict", "")).upper().strip()
            if verdict in _COMPLIANCE_TIERS:
                return {
                    "verdict": verdict,
                    "reasons": result.get("reasons", []) or [],
                    "worst_line": result.get("worst_line", "") or "",
                }
            if parse_attempt == 0:
                print("[Compliance] Unparseable verdict — retrying once...")
                continue
            print(f"[Compliance] Unparseable verdict {verdict!r} again — refusing the video.")
            return {"verdict": "BANNED",
                    "reasons": ["compliance review returned no usable verdict"],
                    "worst_line": ""}
        except Exception as e:
            if parse_attempt == 0:
                print(f"[Compliance] Review error ({str(e)[:80]}) — retrying once...")
                continue
            print(f"[Compliance] Review failed ({e}) — refusing the video.")
            return {"verdict": "BANNED", "reasons": [f"review error: {e}"], "worst_line": ""}


def _generate_hashtags(client: Groq, case: dict) -> str:
    """
    Generate case-specific TikTok hashtags.

    Returns a string of 7 hashtags:
      2 evergreen base tags + location + crime type + decade + 2 case-specific
    """
    resp = _groq_call(client,
        model=GROQ_MODEL,
        max_tokens=260,
        messages=[{
            "role": "user",
            "content": (
                f"Generate exactly 7 TikTok hashtags for this true crime case:\n"
                f"Case: {case.get('case_name', '')}\n"
                f"Location: {case.get('location', '')}\n"
                f"Year: {case.get('year', '')}\n\n"
                "Rules (one hashtag per category, no spaces inside a tag):\n"
                "1. #truecrime\n"
                "2. #documentary\n"
                "3. Location tag — city or country (e.g. #chicago #uk #australia)\n"
                "4. Crime type (e.g. #coldcase #murder #disappearance #conspiracy #unsolved)\n"
                "5. Decade/era (e.g. #1980s #1990s #2000s #1970s)\n"
                "6. A specific keyword from the case name (no spaces)\n"
                "7. One more relevant discovery/topic tag\n"
                "Output ONLY the 7 hashtags separated by spaces, nothing else."
            ),
        }],
    )
    raw = resp.choices[0].message.content.strip()
    words = raw.replace("\n", " ").split()
    tags = []
    for w in words:
        clean = re.sub(r"[^a-zA-Z0-9]", "", w)
        if clean:
            tags.append(w if w.startswith("#") else f"#{clean}")
    return " ".join(tags[:7])


# Reach tags that no longer do anything — TikTok's algorithm ignores them and they
# read as spam to viewers, crowding out tags that actually describe the video.
# #fyp and #foryou are deliberately NOT here: the UK crime accounts performing best
# use them, so they are treated as part of the working formula rather than spam.
_JUNK_TAGS = {"#viral", "#viralvideo", "#trending", "#tiktok", "#video"}

# The tags that put a video in front of true-crime viewers. These are communities
# people actually browse; a geographic or decade tag is not.
# Copied from UK crime accounts that are consistently performing (76K followers,
# 2.4M likes). #fyp was dropped on 2026-10-23 (audit 2026-10-21 #12): no reach
# benefit, it can read as spam, and the upload gate warned on 37 of 37 posts. The
# type tag and the case-specific tag below are the 2 specific tags.
CORE_TAGS = ["#ukcrime", "#crimetok", "#truecrime"]
_GENERIC_REACH_TAGS = {"#fyp", "#foryou", "#foryoupage", "#fy", "#fypシ"}

# One of these describes what kind of case it is. Also browsable.
_TYPE_TAGS = {
    "#coldcase", "#unsolved", "#unsolvedmystery", "#unsolvedmysteries", "#missingperson",
    "#missing", "#murder", "#murdermystery", "#disappearance", "#kidnapping",
    "#coldcasefiles", "#mystery", "#crimestory", "#wrongfulconviction", "#serialkiller",
}

# Tags that fill a slot without earning it: decades nobody browses, and #documentary,
# which is enormous, generic, and competes with actual documentary channels.
_WEAK_TAG = re.compile(r"^#(\d{3,4}s|\d{4}|documentary|history|story|crime)$", re.I)

# Stopwords that reached real captions as "#The" and "#We" — the case-specific slot
# grabbed the first word of a sentence instead of a name or place.
_STOPWORD_TAG = {
    "the","a","an","and","but","or","we","he","she","they","it","this","that",
    "his","her","their","was","were","is","are","had","has","been","when","then",
    "after","before","police","case","body","found","new","two","one",
}


# ─── TikTok title line (first caption line, and the upload gate's "head") ────────
# Audit 2026-10-21 #12: every post opened with the identical "<case> case. | UK crime
# story." line, which reads as a template, and a case name already ending in "case"
# came out "Peter Bergmann case case.". These structures rotate deterministically,
# one step per posted video (rows in analytics/posts_log.csv). Every structure is
# neutral: the case name plus a format label, never a claim about any person, so it
# cannot imply guilt; the line is also part of the caption the legal review reads.
TITLE_STRUCTURES = (
    "{label} case.{part} | {kind} story.",
    "The {label} case, explained.{part}",
    "{label}: the full story.{part}",
    "{kind}: the {label} case.{part}",
    "Inside the {label} case.{part}",
)
POSTS_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analytics", "posts_log.csv")
# Only the template's own words can double up; real names ("Sirhan Sirhan") are kept.
_DOUBLED_WORD = re.compile(r"\b(case|the|story)(\s+\1\b)+", re.I)


def case_label(case_name: str) -> str:
    """'Murder of Ava White' -> 'Ava White'; 'Peter Bergmann case' -> 'Peter Bergmann'."""
    label = re.sub(r"^(the\s+)?(murder|murders|killing|killings|death|disappearance)\s+of\s+",
                   "", (case_name or "").strip(), flags=re.I).strip()
    label = re.sub(r"\s+case(s)?\.?$", "", label, flags=re.I).strip()
    return label or (case_name or "").strip()


def _post_count() -> int:
    """Videos posted so far (data rows in posts_log.csv); 0 if the log is missing."""
    try:
        with open(POSTS_LOG, encoding="utf-8") as f:
            return max(0, sum(1 for line in f if line.strip()) - 1)
    except Exception:
        return 0


def build_tiktok_title(case_name: str, uk: bool = True, part=None, index=None) -> str:
    """The rotating first caption line. `part` is (k, n) for a series."""
    if index is None:
        index = _post_count()
    fmt = TITLE_STRUCTURES[index % len(TITLE_STRUCTURES)]
    k, n = part or (None, None)
    title = fmt.format(label=case_label(case_name),
                       part=f" Part {k}/{n}" if k else "",
                       kind="UK crime" if uk else "True crime")
    return _DOUBLED_WORD.sub(r"\1", title)


def build_hashtags(story_tags: str, case_name: str = "", uk: bool = True) -> str:
    """
    Five tags: the true-crime communities first, then the case type, then one
    case-specific tag for search.

    This ordering is drawn from the account's own analytics. The best-performing post
    by a factor of ~2.6 carried only broad community tags, while posts led by narrow
    tags (#sudan, #Florida, #ireland, #2000s) trailed it. That matches how the tags
    work: nobody browses #sudan looking for true crime, so a narrow tag spends one of
    only five slots without reaching anyone. The single specific tag is kept because
    ~1.7% of traffic arrives from search, on queries like "tara calico".
    """
    # #ukcrime only on UK cases; the channel covers cases worldwide since 2026-10-09.
    picked = [t if uk or t != "#ukcrime" else "#crimestory" for t in CORE_TAGS]
    seen = {t.lower() for t in picked}

    candidates = []
    for word in (story_tags or "").split():
        body = re.sub(r"\W", "", word.lstrip("#"), flags=re.UNICODE)
        if not body:
            continue
        tag = f"#{body}"
        if (tag.lower() in seen or tag.lower() in _JUNK_TAGS
                or tag.lower() in _GENERIC_REACH_TAGS or len(tag) < 3):
            continue
        candidates.append(tag)

    # Slot 4 — what kind of case this is.
    type_tag = next((t for t in candidates if t.lower() in _TYPE_TAGS), None)
    if type_tag:
        picked.append(type_tag)
        seen.add(type_tag.lower())

    # Slot 5 — something searchable. Prefer a tag drawn from the case name over a
    # place: people search the case, not the country.
    key = re.sub(r"\W", "", (case_name or "")).lower()
    rest = [t for t in candidates if t.lower() not in seen and not _WEAK_TAG.match(t)]
    specific = next((t for t in rest if t.lstrip("#").lower() in key), None) or \
               (rest[0] if rest else None)
    if specific:
        picked.append(specific)

    # Backfill only if the model gave us nothing usable.
    for t in ("#unsolved", "#coldcase", "#mystery"):
        if len(picked) >= 5:
            break
        if t not in {p.lower() for p in picked}:
            picked.append(t)

    return " ".join(picked[:5])


def merge_hashtags(*groups: str, limit: int = 5) -> str:
    """
    Combine hashtag groups into one deduplicated set, capped at TikTok's 5-hashtag
    limit for descriptions.

    The old caption pasted a fixed tag list next to the generated one, so every post
    shipped ~15 tags with #truecrime and #coldcase appearing twice. Specific tags are
    kept first: they are what the video is actually about, and a handful of relevant
    tags reaches further than a wall of generic ones.
    """
    seen, out = set(), []
    for group in groups:
        for word in (group or "").split():
            # \w is Unicode-aware, so accented names survive. Stripping to [A-Za-z0-9]
            # turned "#Cédrika" into "#Cdrika" — and this pool is full of non-English
            # names (Cédrika Provencher, Sophie Toscan du Plantier, Tair Rada).
            body = re.sub(r"\W", "", word.lstrip("#"), flags=re.UNICODE)
            if not body:
                continue
            tag = f"#{body}"
            key = tag.lower()
            if len(tag) < 3 or key in _JUNK_TAGS or key in seen:
                continue
            if tag.lstrip("#").lower() in _STOPWORD_TAG:
                continue
            seen.add(key)
            out.append(tag)
            if len(out) >= limit:
                return " ".join(out)
    return " ".join(out)


def _write_engagement_cta(client: Groq, case: dict, script: str) -> str:
    """
    The final on-screen card: what is still unresolved about the case.

    Every video used to end on the same static "Follow for more unsolved cases".
    That asks for a follow — high effort, and identical on every post. A binary
    question tied to this specific case can be answered with one word, which is the
    lowest-effort comment there is, and comments are the strongest reach signal
    TikTok has. It renders ~15 characters per line, so it has to be short.
    """
    try:
        resp = _groq_call(client,
            model=GROQ_MODEL,
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": (
                    "Write ONE short closing line for a true crime video that asks the "
                    "viewer to comment.\n\n"
                    f"Case: {case.get('case_name','')}\n"
                    f"Unresolved: {case.get('unresolved','')}\n"
                    f"Script: {script[:400]}\n\n"
                    "Rules:\n"
                    "- Maximum 7 words. Must include the word 'comment'.\n"
                    "- Ask what the viewer THINKS about the unanswered question, or "
                    "whether they remember or know the case.\n"
                    "- Do NOT ask them to name, guess or accuse a suspect, and do NOT "
                    "frame it as a two-option quiz about how a real person died — a "
                    "moderator reads that as sensationalising a real death.\n"
                    "- Examples: 'What do you think? Comment below.' "
                    "'Comment if you remember this case.' "
                    "'Should this case be reopened? Comment.'\n"
                    "- No hashtags, no quotes, no emojis.\n"
                    "Output ONLY the line."
                ),
            }],
        )
        text = _sanitize_llm_text(resp.choices[0].message.content).strip().strip('"')
        # Must fit the card and actually ask for a comment. This check used to require
        # the line to END in "?", which silently rejected every valid output once the
        # prompt changed to "...? Comment below." — so every video fell back to the
        # generic default and the case-specific card never shipped.
        words = len(text.split())
        if text and "comment" in text.lower() and words <= 8 and len(text) <= 42:
            return text
    except Exception as e:
        print(f"[TrueCrime] CTA generation failed ({e}) — using default.")
    return "What do you think? Comment below."


def _write_caption(client: Groq, case: dict, script: str) -> str:
    """
    The TikTok description: a curiosity hook plus a question.

    The caption used to be just the video title, which gives nobody a reason to stop
    or reply. Comments are the strongest reach signal on TikTok, so it ends on an
    open question aimed squarely at the unresolved part of the case.
    """
    try:
        resp = _groq_call(client,
            model=GROQ_MODEL,
            max_tokens=320,
            messages=[{
                "role": "user",
                "content": (
                    "Write a TikTok caption for this true crime video.\n\n"
                    f"Case: {case.get('case_name','')}\n"
                    f"Still unresolved: {case.get('unresolved','')}\n"
                    f"Script: {script[:500]}\n\n"
                    "Rules:\n"
                    "- Sentence 1: a hook that creates curiosity. Max 14 words. "
                    "Do NOT reveal the twist or the ending.\n"
                    "- Sentence 2: invite discussion by naming what is still unknown. "
                    "Do NOT ask readers to guess who killed a real person and do NOT "
                    "make them pick between suspects — a moderator reads that as "
                    "sensationalising a real death and suppresses the video.\n"
                    "  GOOD: 'Accident, or did someone push him?'\n"
                    "  GOOD: 'Was the husband covering for someone?'\n"
                    "  BAD: 'What do you think happened?' — too open, nobody replies "
                    "to it.\n"
                    "  BAD: 'Who was responsible?' — same problem.\n"
                    "- Under 180 characters total. No hashtags. No quotation marks. "
                    "No emojis.\n"
                    "Output ONLY the caption text."
                ),
            }],
        )
        text = _sanitize_llm_text(resp.choices[0].message.content)
        if text and len(text) <= 300:
            return text
    except Exception as e:
        print(f"[TrueCrime] Caption generation failed ({e}) — using title.")
    return ""


def _legal_review(client: Groq, script: str, caption: str, cta: str, case: dict) -> dict:
    """
    Defamation / contempt review against the source. Fails closed (twice-tried).
    Returns {"ok": bool, "reasons": [...]}.
    """
    # Series scripts are written from up to 9000 chars (SERIES_SOURCE_CHARS); an
    # acquittal or quashed conviction past char 6000 used to be invisible here.
    source = (case.get("source_text") or "")[:9500]
    messages = [
        {"role": "system", "content": (
            "You are a UK media lawyer clearing a true crime video before publication. "
            "Be strict: a libel claim against a teenager's channel is ruinous.\n"
            "BLOCK if ANY of these is true:\n"
            "1. The text names, or makes identifiable (e.g. 'her foster father', 'the "
            "neighbour'), any real person who was NOT finally convicted of the crime "
            "(never charged, charged but acquitted, conviction quashed, suspect, witness, "
            "relative) AND the text points suspicion at them in any way: evidence "
            "'linked' to them, traces on their clothing, motives, odd behaviour, or "
            "inviting viewers to guess about them.\n"
            "2. The SOURCE records an acquittal, a quashed conviction or a later "
            "exoneration that the text leaves out while still describing the case "
            "against that person.\n"
            "3. It states as fact something the SOURCE does not support about a real person.\n"
            "4. It asks viewers to name or guess WHO did it, or to accuse an "
            "identifiable person. (Asking what viewers think HAPPENED - accident or "
            "crime, their theories about an unsolved event - is normal and fine when no "
            "unconvicted person is identified.)\n"
            "PASS only if every person connected to wrongdoing in the text was "
            "convicted of it, or nobody is identified at all. A description of an "
            "UNIDENTIFIED person that no viewer could link to a specific real individual "
            "(e.g. 'a man seen in a white car', 'the killer') does not identify anyone.\n"
            'Reply ONLY with JSON: {"verdict":"PASS|BLOCK","reasons":["..."]}')},
        {"role": "user", "content": (
            f"SOURCE (encyclopaedia entry):\n{source or '(none available)'}\n\n"
            f"SCRIPT (spoken):\n{script}\n\nCAPTION:\n{caption}\n\nON-SCREEN CARD:\n{cta}")},
    ]
    for attempt in range(2):
        try:
            resp = _groq_call(client, model=GROQ_MODEL, max_tokens=1400, messages=messages)
            out = _extract_json(resp.choices[0].message.content or "")
            v = str(out.get("verdict", "")).upper().strip()
            if v in ("PASS", "BLOCK"):
                return {"ok": v == "PASS", "reasons": out.get("reasons") or []}
        except Exception as e:
            print(f"[Legal] Review error ({str(e)[:80]})" + (" — retrying..." if not attempt else ""))
    return {"ok": False, "reasons": ["legal review returned no usable verdict"]}


PRESCREEN_SOURCE_CHARS = 4500


def _prescreen_case(client: Groq, sourced: dict) -> dict:
    """
    One short call, BEFORE any research or writing: would this case fail the final
    compliance or legal gate however carefully it was written?

    Same criteria as compliance_review and _legal_review, applied to the case rather
    than to a script. It exists because those gates were rejecting cases for reasons
    plain in the source (a murdered child, an acquitted suspect at the centre of the
    story) only after ~8 calls had been spent on them.

    Fails OPEN: an error or unparseable reply returns go=True. That is safe because
    this is only an early filter — the fail-closed compliance and legal gates still
    run on every script. Returns {"go": bool, "category": str, "reason": str}.
    """
    source = (sourced.get("full_text") or sourced.get("extract") or "")[:PRESCREEN_SOURCE_CHARS]
    messages = [
        {"role": "system", "content": (
            # Without the date the model can't apply "the last two years" below.
            f"Today's date is {datetime.now(timezone.utc):%d %B %Y}.\n"
            "You screen real criminal cases for a respectful 45-second true crime TikTok "
            "(factual, no graphic detail). Two strict reviewers check every finished "
            "video: a TikTok Trust & Safety moderator and a UK media lawyer. Predict "
            "whether this CASE would fail them even if the script were written carefully.\n"
            "The script NEVER describes injuries, weapons striking or the body ('was "
            "killed' / 'was found dead' at most), so a violent death alone is not a "
            "problem. Judge what the story IS, not the source's level of detail.\n"
            "SKIP only if one of these cannot be avoided in telling the case:\n"
            "- minor_victim: a child or under-18 was KILLED - a confirmed murder or "
            "killing (moderators age-restrict any child killing). NOT this category: a "
            "missing child whose fate is unknown (e.g. a 14-year-old who vanished and was "
            "never found) - that is GO.\n"
            "- sexual_violence: rape, sexual assault or sexual abuse is part of what "
            "happened to the victim, or any sexual abuse of a child.\n"
            "- gore: the case is KNOWN FOR dismemberment, torture, mutilation or the "
            "state of the body, so the story cannot be told without it. A stabbing, "
            "shooting or beating is NOT gore.\n"
            "- unconvicted_suspect: a named or identifiable real person who was NOT "
            "finally convicted (suspect, arrested and released, acquitted, conviction "
            "quashed) is the heart of the story, so it cannot be told without pointing "
            "suspicion at them. A suspect who is only a side detail is fine (the script "
            "can leave them out). Also fine if a different person was later convicted, "
            "or if nobody is identified at all.\n"
            "- active_proceedings: someone is under arrest, wanted on a warrant, charged, "
            "awaiting trial or appeal, or the events are from the last two years. An open "
            "police investigation with nobody arrested, wanted or charged is NOT active "
            "proceedings (cold cases are GO).\n"
            "- other: terrorism, a mass shooting, a political killing, or suicide as "
            "the subject.\n"
            "Otherwise GO. Most unsolved murders and disappearances, and cases resolved "
            "by a conviction, are GO. When unsure, GO: stricter checks follow.\n"
            'Reply ONLY with JSON: {"verdict":"GO|SKIP","category":"minor_victim|'
            'sexual_violence|gore|unconvicted_suspect|active_proceedings|other|none",'
            '"reason":"one sentence"}')},
        {"role": "user", "content": f"Case: {sourced.get('title','')}\n\nSOURCE:\n{source}"},
    ]
    try:
        resp = _groq_call(client, model=GROQ_MODEL, max_tokens=600, messages=messages)
        out = _extract_json(resp.choices[0].message.content or "")
        v = str(out.get("verdict", "")).upper().strip()
        if v in ("GO", "SKIP"):
            return {"go": v == "GO",
                    "category": str(out.get("category") or "").strip()[:40],
                    "reason": str(out.get("reason") or "").strip()[:200]}
        print("[Prescreen] Unparseable reply — letting the case through to the full gates.")
    except Exception as e:
        print(f"[Prescreen] Error ({str(e)[:80]}) — letting the case through to the full gates.")
    return {"go": True, "category": "", "reason": ""}


def _soften_for_guidelines(client: Groq, script: str, description: str, cta: str,
                           title: str, verdict: dict):
    """
    Rewrite a blocked script/caption/card/title to remove what the reviewer objected
    to. Removal only: no new facts may be added, so the fact-check still holds.
    Returns (script, description, cta, title) or None if the rewrite is unusable.
    """
    reasons = "\n".join(f"- {r}" for r in verdict.get("reasons", [])[:5])
    worst = verdict.get("worst_line") or ""
    try:
        resp = _groq_call(client, model=GROQ_MODEL, max_tokens=1400, messages=[
            {"role": "system", "content": (
                "You edit true crime TikTok scripts so they pass TikTok's Community "
                "Guidelines and stay eligible for the For You feed. Remove or neutralise "
                "every graphic, gory, sexual or sensationalised detail: injuries, weapons "
                "striking, blood, the state of a body, how a death physically happened. "
                "'Was killed' / 'was found dead' is the most detail allowed. Keep the "
                "mystery, the open questions and the structure (hook first, unresolved "
                "question + invitation to comment last). Do NOT add any fact, name, date or "
                "detail that is not already in the text. NEVER remove a court outcome "
                "(acquitted, conviction quashed, never charged) - that context is what "
                "keeps the video from being defamatory. Keep the script 95-120 words.\n"
                'Reply ONLY with JSON: {"script":"...","caption":"...","card":"...","title":"..."}')},
            {"role": "user", "content": (
                f"Moderator's objections:\n{reasons}\nWorst line: {worst}\n\n"
                f"SCRIPT:\n{script}\n\nCAPTION:\n{description}\n\nCARD:\n{cta}\n\n"
                f"TITLE (max 7 words):\n{title}")},
        ])
        out = _extract_json(resp.choices[0].message.content or "")
    except Exception as e:
        print(f"[Compliance] Softening failed ({str(e)[:100]}).")
        return None
    new_script = _sanitize_llm_text(str(out.get("script") or "")).strip()
    n = len(new_script.split())
    if not (MIN_SCRIPT_WORDS <= n <= MAX_SCRIPT_WORDS):
        print(f"[Compliance] Softened script unusable ({n} words).")
        return None
    print(f"[Compliance] Softened script: {n} words.")
    return (new_script,
            str(out.get("caption") or description).strip() or description,
            str(out.get("card") or cta).strip() or cta,
            str(out.get("title") or title).strip().strip("\"'") or title)


_UK_PLACE = re.compile(r"\b(england|scotland|wales|northern ireland|united kingdom|uk|"
                       r"britain|london|manchester|birmingham|liverpool|leeds|glasgow|"
                       r"edinburgh|cardiff|belfast|yorkshire|lancashire|kent|essex|"
                       r"sussex|devon|cornwall)\b", re.I)


def _is_uk(case: dict) -> bool:
    return bool(_UK_PLACE.search(f"{case.get('location','')} {case.get('case_name','')}"))


def _fit_length(script: str) -> str:
    """
    Bring a script into the allowed length, or return "" if it can't be.

    Never let an empty or stunted script reach the fact-checker: a run once shipped
    zero narration because gpt-oss returned an empty script and the fact-checker
    scored the nothing 9/10. Over-long scripts are TRIMMED on sentence boundaries,
    always keeping the last sentence (the closing question / "Part two next."), because
    the model reliably ignores the word count and rejecting cost whole runs.
    """
    script = (script or "").strip()
    word_count = len(script.split())
    print(f"[TrueCrime] Script: {word_count} words")
    if word_count < MIN_SCRIPT_WORDS:
        print(f"[TrueCrime] Rejected — script too short ({word_count} words, "
              f"need {MIN_SCRIPT_WORDS}).")
        return ""
    if word_count > TRIM_SCRIPT_WORDS:
        sentences = re.split(r"(?<=[.!?])\s+", script)
        if len(sentences) > 2:
            closing = sentences[-1]
            kept, running = [], len(closing.split())
            for sent in sentences[:-1]:
                n = len(sent.split())
                if running + n > TRIM_SCRIPT_WORDS:
                    break
                kept.append(sent)
                running += n
            if kept:
                trimmed = " ".join(kept + [closing])
                # Only accept a trim that leaves a usable script: one long early
                # sentence could otherwise cut a 300-word script to 27.
                if len(trimmed.split()) >= MIN_SCRIPT_WORDS:
                    script, word_count = trimmed, len(trimmed.split())
                    print(f"[TrueCrime] Trimmed script to {word_count} words "
                          f"(kept the closing line).")
    if word_count > MAX_SCRIPT_WORDS:
        print(f"[TrueCrime] Rejected — script too long ({word_count} words, "
              f"max {MAX_SCRIPT_WORDS}).")
        return ""
    return script


def _finalise(client: Groq, case: dict, case_name: str, script: str, part: tuple = None):
    """
    Everything after the script is written: fact-check, title, hashtags, caption,
    end card, the TikTok compliance gate (with one soften), and the legal gate.

    Returns (result, status): "approved" (safe and good), "safe_lowq" (safe and
    factually sound but below the quality bar — usable as a fallback), or "rejected".
    `part` = (k, n) for a series part.
    """
    k, n = part or (None, None)

    # ── Cold open (#12): settled BEFORE any review, so the fact-check, the TikTok
    # compliance review and the legal review all read the line that opens the video.
    cold_open, script = _cold_open(client, case, script)

    # ── Fact-check ────────────────────────────────────────────────────────────
    print("[TrueCrime] Fact-checking...")
    check = _fact_check(client, case, script)
    acc, interest = check["accuracy_score"], check["interest_score"]
    sense, approved = check["makes_sense"], check["approved"]
    tiktok_safe = check["tiktok_safe"]
    print(f"[TrueCrime] Accuracy: {acc}/10 | Interest: {interest}/10 | "
          f"Coherent: {sense} | TikTok-safe: {tiktok_safe} | Approved: {approved}")
    for issue in check.get("issues", []):
        print(f"[TrueCrime]   ! {issue}")

    # ── Title, hashtags, caption, end card ───────────────────────────────────
    title_resp = _groq_call(client, model=GROQ_MODEL, max_tokens=200, messages=[{
        "role": "user",
        "content": ("Write a short, gripping TikTok title for this true crime story "
                    f"(max 7 words, no quotes, no hashtags):\n{script[:200]}"),
    }])
    title = title_resp.choices[0].message.content.strip().strip('"\'')
    if k:
        title = f"{title} (Part {k})"

    uk = _is_uk(case)
    hashtags = _generate_hashtags(client, case)
    description = _write_caption(client, case, script) or title
    # The case name up front is a search hook and sets expectations; the structure
    # around it rotates per posted video so the feed does not read as a template.
    tiktok_title = build_tiktok_title(case_name, uk=uk, part=part)
    tags = build_hashtags(hashtags, case_name, uk=uk)
    caption = f"{description}\n\n{tags}"
    print(f"[TrueCrime] Hashtags: {tags}")
    print(f"[TrueCrime] Caption: {description}")

    if k and k < n:
        cta = f"Part {k + 1} next."
    else:
        cta = _write_engagement_cta(client, case, script)
    print(f"[TrueCrime] CTA card: {cta}")

    result = {
        "script":         script,
        "title":          title,
        "case_name":      case_name,
        # The place the case happened: the image layer searches it for real
        # photographs of the location.
        "location":       (case.get("location") or "").strip(),
        "year":           (str(case.get("year") or "")).strip(),
        "uk":             uk,
        "hashtags":       tags,
        "caption":        caption,
        "tiktok_title":   tiktok_title,
        "cta":            cta,
        "accuracy_score": acc,
        "interest_score": interest,
        "tiktok_safe":    tiktok_safe,
        # The on-screen hook card = the cold open, whole (<= 10 words), or no card.
        "hook":           cold_open,
        "cold_open":      cold_open,
    }

    # ── Guidelines gate — a HARD requirement ─────────────────────────────────
    if not tiktok_safe:
        print("[TrueCrime] Rejected (TikTok guidelines violation).")
        return result, "rejected"

    print("[Compliance] Reviewing against TikTok Community Guidelines...")
    verdict = compliance_review(client, script, caption, case, cta=cta)
    softened = False
    if verdict["verdict"] != COMPLIANCE_OK and verdict["reasons"] and not any(
            "review error" in r or "no usable verdict" in r for r in verdict["reasons"]):
        # Most blocks were the WRITING, not the case: a gory phrase ("twenty-two
        # hammer blows") or a sensational hook. One rewrite that only removes
        # material, then the same strict review again: the reviewer has the final say.
        for r in verdict["reasons"][:3]:
            print(f"[Compliance]   ! {r}")
        print(f"[Compliance] {verdict['verdict']} - softening the script once and re-reviewing...")
        soft = _soften_for_guidelines(client, script, description, cta, title, verdict)
        softened = bool(soft)
        if soft:
            script, description, new_cta, title = soft
            if not (k and k < n):          # a series' "Part N next." card stays as is
                cta = new_cta
            caption = f"{description}\n\n{tags}"
            verdict = compliance_review(client, script, caption, case, cta=cta)
            # The softened script was reviewed again below; its own first sentence is the
            # hook card only if it is still a valid cold open (never re-cut, never re-asked).
            hook = _hook_for(script, case)
            result.update(script=script, caption=caption, cta=cta, title=title,
                          hook=hook, cold_open=hook)
    result["compliance"] = verdict
    result["softened"] = softened
    if verdict["verdict"] != COMPLIANCE_OK:
        for r in verdict["reasons"][:3]:
            print(f"[Compliance]   ! {r}")
        print(f"[Compliance] BLOCKED — {verdict['verdict']}.")
        return result, "rejected"
    print(f"[Compliance] PASSED ({verdict['verdict']}) — eligible for the For You feed.")

    # A softened script is a NEW script: the first live post (Billie-Jo Jenkins,
    # 2026-10-03) lost the facts that her foster father's conviction was quashed and
    # he was acquitted, and kept the evidence pointing at him. Fact-check it afresh.
    if softened:
        print("[TrueCrime] Re-fact-checking the softened script...")
        check = _fact_check(client, case, script)
        acc, interest = check["accuracy_score"], check["interest_score"]
        sense, approved = check["makes_sense"], check["approved"]
        result.update(accuracy_score=acc, interest_score=interest)
        print(f"[TrueCrime] Softened: Accuracy {acc}/10 | Interest {interest}/10 | "
              f"Approved: {approved}")

    # Legal gate: nothing may point suspicion at a real person a court has not
    # convicted. Separate from compliance (TikTok rules) and from the regex check
    # in publish_checks (which only catches words like "suspected").
    # The title line is posted as the first caption line, so the legal review reads it too.
    legal = _legal_review(client, script, f"{result['tiktok_title']}\n{caption}", cta, case)
    result["legal"] = legal
    if not legal["ok"]:
        for r in legal["reasons"][:3]:
            print(f"[Legal]   ! {r}")
        print("[Legal] BLOCKED — implicates an unconvicted person or misstates the "
              "legal outcome.")
        return result, "rejected"
    print("[Legal] PASSED — no unconvicted person implicated.")

    if approved and acc >= 7 and interest >= 7 and sense:
        return result, "approved"
    # Publishing fabrications about real people is worse than publishing nothing, so
    # a weak-but-true script may be the fallback and a false one may never be.
    reason = [f"accuracy {acc}/10"] if acc < 7 else []
    reason += [f"interest {interest}/10"] if interest < 7 else []
    reason += ["incoherent"] if not sense else []
    print(f"[TrueCrime] Below the quality bar ({', '.join(reason) or 'not approved'}).")
    return result, ("safe_lowq" if acc >= MIN_FALLBACK_ACCURACY else "rejected")


def _plan_series(client: Groq, case: dict, max_parts: int):
    """Ask whether this case genuinely carries a multi-part story; return its beats."""
    source = (case.get("full_text") or "")[:SERIES_SOURCE_CHARS]
    try:
        resp = _groq_call(client, model=GROQ_MODEL, max_tokens=1200, messages=[
            {"role": "system", "content": (
                "You plan short true crime TikTok series. Each part is a ~45-second "
                "narration (100-120 words). Decide whether this case has enough DISTINCT, "
                "well-documented developments in the source to fill 2 or 3 parts without "
                "padding or repetition, each part with its own revelation and an "
                "unresolved cliffhanger. Most cases do NOT - say no unless it clearly "
                f"does. At most {max_parts} parts. Use only facts in the source.\n"
                'Reply ONLY with JSON: {"worth_series": true|false, "beats": ["what part '
                '1 covers", "what part 2 covers", ...], "why": "..."}')},
            {"role": "user", "content": f"Case: {case.get('case_name','')}\n\nSOURCE:\n{source}"},
        ])
        plan = _extract_json(resp.choices[0].message.content or "")
    except Exception as e:
        print(f"[Series] Planning failed ({str(e)[:100]}).")
        return None
    beats = [str(b).strip() for b in (plan.get("beats") or []) if str(b).strip()]
    if not plan.get("worth_series") or not (2 <= len(beats) <= max_parts):
        print(f"[Series] Not a series: {str(plan.get('why') or 'planner said no')[:160]}")
        return None
    return beats


def _try_series(client: Groq, case: dict, case_name: str):
    """
    Part 1 of a fully written and fully checked series, with Parts 2..N attached as
    "series_rest" — or None to make a normal single video.
    """
    import series_queue
    n_max = series_queue.parts_for(len(case.get("full_text") or ""))
    if not n_max or not series_queue.can_start_new():
        return None
    print(f"[Series] Long source ({len(case['full_text'])} chars) — considering up to "
          f"{n_max} parts...")
    beats = _plan_series(client, case, n_max)
    if not beats:
        return None
    n = len(beats)
    print(f"[Series] Planned {n} parts: " + " | ".join(b[:60] for b in beats))
    parts = []
    for k in range(1, n + 1):
        print(f"\n[Series] ── Writing part {k}/{n} ──")
        script = _fit_length(_write_script(
            client, case, part={"k": k, "n": n, "beat": beats[k - 1], "beats": beats}))
        if not script:
            print(f"[Series] Part {k} unusable — making a single video instead.")
            return None
        result, status = _finalise(client, case, case_name, script, part=(k, n))
        if status != "approved":
            # Every part must clear every gate before Part 1 goes out, so a series
            # can never stall halfway.
            print(f"[Series] Part {k} did not pass ({status}) — making a single video instead.")
            return None
        result.update(series_part=k, series_total=n)
        parts.append(result)
    parts[0]["series_rest"] = parts[1:]
    print(f"[Series] APPROVED: {n}-part series on {case_name}.")
    return parts[0]


MAX_PRESCREEN_SKIPS = 10


def _rejection_stage(result: dict) -> tuple:
    """Which gate refused a script, for the rejected-case memory: (stage, reason)."""
    result = result or {}
    legal = result.get("legal")
    if legal is not None and not legal.get("ok"):
        reasons = legal.get("reasons", [])
        # A legal review that errored (Groq outage, bad JSON) says nothing about the
        # case; a hard "legal" strike would blacklist it forever. Soft strike instead.
        if any("no usable verdict" in str(r) or "review error" in str(r) for r in reasons):
            return "review_error", str(reasons[:1])
        return "legal", "; ".join(map(str, reasons[:1]))
    verdict = result.get("compliance") or {}
    if verdict and verdict.get("verdict") != COMPLIANCE_OK:
        reasons = verdict.get("reasons", [])
        # A review that errored says nothing about the case — only a soft strike.
        if any("review error" in str(r) or "no usable verdict" in str(r) for r in reasons):
            return "review_error", str(reasons[:1])
        return "compliance", f"{verdict.get('verdict')}: " + "; ".join(map(str, reasons[:1]))
    if result and result.get("tiktok_safe") is False:
        # The fact-checker judges the SCRIPT's wording here; soft strike.
        return "factcheck_unsafe", "fact-checker flagged the script as not TikTok-safe"
    return "quality", f"accuracy {result.get('accuracy_score')}/10"


def generate_true_crime_story(max_attempts: int = 5) -> dict:
    """
    Research, write, and fact-check a true crime documentary script.

    Multi-step pipeline:
      1. Research: Groq picks a real, compelling case
      2. Write:    Groq scripts it in documentary style
      3. Check:    Groq fact-checks accuracy + interest + coherence
      4. Approve or retry with a different case

    Returns dict with: script, title, case_name, hashtags,
                       accuracy_score, interest_score
    """
    client = Groq(api_key=GROQ_API_KEY)

    # Seed exclusions from prior runs (persisted file) so cases never repeat day-to-day.
    for _name in _load_recent_cases():
        if _name not in _USED_CASES:
            _USED_CASES.append(_name)

    last_result = None
    best_safe = None          # best candidate that PASSED the guidelines gate
    best_safe_score = -1

    # A pre-screen skip costs one short call, not a whole attempt, so it does not use
    # one up — but it is capped so a run can never loop forever.
    attempt, prescreen_skips = 0, 0
    while attempt < max_attempts:
        attempt += 1
        print(f"\n[TrueCrime] Attempt {attempt}/{max_attempts} — researching case...")

        # ── Step 1: Source a real, unused case, then research it ──────────────
        sourced = _pick_fresh_case()
        if not sourced:
            print("[TrueCrime] Could not source a fresh case from Wikipedia — retrying.")
            continue

        # ── Step 1b: Pre-screen BEFORE spending calls on research and a script ─
        if prescreen_skips < MAX_PRESCREEN_SKIPS:
            screen = _prescreen_case(client, sourced)
            if not screen["go"]:
                prescreen_skips += 1
                attempt -= 1
                print(f"[Prescreen] SKIP '{sourced['title']}' — {screen['category']}: "
                      f"{screen['reason'][:140]}")
                case_screen.record_rejection(sourced["title"], "prescreen",
                                             f"{screen['category']}: {screen['reason']}")
                _USED_CASES.append(sourced["title"])
                continue
            print(f"[Prescreen] GO '{sourced['title']}'.")

        case = _research_case(client, sourced)
        case_name = (case.get("case_name") or "").strip()
        if not _valid_case_name(case_name):
            print(f"[TrueCrime] Rejected — unusable case name: {case_name!r}")
            continue

        # Belt and braces: the pool was already filtered against _USED_CASES, but a
        # near-duplicate title ("Murder of X" vs "Murders of X") can still slip in.
        if _is_duplicate(case_name, _USED_CASES):
            print(f"[TrueCrime] Rejected — '{case_name}' has been used before. Retrying.")
            _USED_CASES.append(case_name)
            continue

        _USED_CASES.append(case_name)  # never re-pick within this run either
        print(f"[TrueCrime] Case: {case_name} ({case.get('year','?')}, {case.get('location','?')})")

        # ── Series: an occasional long, rich case is told in 2-3 parts ────────
        series = _try_series(client, case, case_name)
        if series:
            _save_recent_case(case_name)
            return series

        # ── Step 2: Write script ──────────────────────────────────────────────
        print("[TrueCrime] Writing script...")
        script = _fit_length(_write_script(client, case))
        if not script:
            _USED_CASES.append(case_name)
            # Usually a flaky model reply, so a soft strike: excluded after two.
            case_screen.record_rejection(case_name, "empty_script", "script unusable")
            continue

        result, status = _finalise(client, case, case_name, script)
        if status == "approved":
            _USED_CASES.append(case_name)
            _save_recent_case(case_name)
            print(f"[TrueCrime] APPROVED: '{result['title']}'")
            return result
        if result:
            last_result = result
        # Safe but below the quality bar. Keep the best one as a fallback so a run still
        # ships something rather than nothing — but only ever from safe, factually
        # sound candidates (see _finalise).
        if status == "safe_lowq":
            score = result["accuracy_score"] + result["interest_score"]
            if score > best_safe_score:
                best_safe, best_safe_score = result, score
        else:
            stage, why = _rejection_stage(result)
            case_screen.record_rejection(case_name, stage, why)
        _USED_CASES.append(case_name)  # avoid repeating it

    # Fall back only to a candidate that PASSED the guidelines gate. This previously
    # returned last_result — the final attempt, whatever it was — so a script the
    # reviewer had just rejected for a guidelines violation got published anyway. The
    # safety check ran, failed, and was then ignored.
    if best_safe:
        print(f"[TrueCrime] Using best guidelines-safe result after {max_attempts} attempts.")
        _save_recent_case(best_safe.get("case_name"))
        return best_safe

    # Nothing both safe and accurate. No video is the correct outcome.
    raise RuntimeError(
        f"No guidelines-safe case found in {max_attempts} attempts. No video produced — "
        f"refusing to post content that failed the TikTok Community Guidelines review."
    )
