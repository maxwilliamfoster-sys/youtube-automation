"""
Story Generator — uses Groq (FREE) + Llama 3.3 to generate horror stories
and true crime documentary scripts with research + fact-checking.
"""

import re
import difflib
import json
import os
import random
import time
import requests as _requests
from groq import Groq, RateLimitError

import case_source
from config import (
    GROQ_API_KEY, GROQ_MODEL, STORY_TYPES, STORY_WORD_COUNT, OPENROUTER_API_KEY,
    STORY_WORD_MIN, STORY_WORD_MAX,
)

# ── LLM backend constants ─────────────────────────────────────────────────────
GROQ_FALLBACK_MODEL   = "llama-3.1-8b-instant"
OPENROUTER_BASE_URL   = "https://openrouter.ai/api/v1/chat/completions"

# OpenRouter free models tried in order — all 0 cost, no daily token cap.
# We PREFER plain instruction-tuned models that return the answer directly. Reasoning
# models (gpt-oss / nemotron) can leak their chain-of-thought into the output — which
# is exactly what produced the 6-minute garbled video — so they sit last and their
# output is always run through _sanitize_llm_text() regardless.
OPENROUTER_FREE_MODELS = [
    "meta-llama/llama-3.3-70b-instruct:free",    # Llama 3.3 70B — best quality, direct answers
    "meta-llama/llama-3.1-70b-instruct:free",    # Llama 3.1 70B — instruct fallback
    "qwen/qwen-2.5-72b-instruct:free",           # Qwen 2.5 72B — instruct fallback
    "nousresearch/hermes-3-llama-3.1-405b:free", # Hermes 405B — large instruct
    "nvidia/nemotron-3-super-120b-a12b:free",    # reasoning — last resort (output sanitised)
    "openai/gpt-oss-120b:free",                  # reasoning — last resort (output sanitised)
]

# Shared state — flip to OpenRouter the moment Groq's daily limit is hit
_use_openrouter: bool = False


class _FakeResponse:
    """Wraps any plain-text LLM response to look like a Groq/OpenAI response object."""
    class _Choice:
        def __init__(self, text: str):
            class _Msg:
                pass
            self.message = _Msg()
            self.message.content = text
    def __init__(self, text: str):
        self.choices = [self._Choice(text)]


def _openrouter_call(**kwargs) -> object:
    """
    Call OpenRouter with an OpenAI-style messages dict.
    Tries each free model in OPENROUTER_FREE_MODELS until one succeeds.
    Uses the requests library — no extra SDK needed.
    Free models (`:free` suffix) have no daily token cap.
    """
    if not OPENROUTER_API_KEY:
        raise RuntimeError(
            "Groq daily token limit hit and OPENROUTER_API_KEY is not set.\n"
            "Get a FREE key (no card needed) in 60 seconds:\n"
            "  1. Go to https://openrouter.ai/\n"
            "  2. Sign up with Google / GitHub / email\n"
            "  3. Dashboard -> Keys -> Create Key\n"
            "  4. Copy the key and add it to your .env:  OPENROUTER_API_KEY=sk-or-...\n"
        )

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type":  "application/json",
        "HTTP-Referer":  "https://github.com/buriedcasefiles",
        "X-Title":       "BuriedCaseFiles",
    }

    last_err = None
    for model in OPENROUTER_FREE_MODELS:
        payload = {
            "model":       model,
            "messages":    kwargs.get("messages", []),
            "max_tokens":  kwargs.get("max_tokens", 1024),
            "temperature": 0.8,
        }
        try:
            resp = _requests.post(
                OPENROUTER_BASE_URL, json=payload,
                headers=headers, timeout=90
            )
            if resp.status_code == 200:
                text = resp.json()["choices"][0]["message"]["content"]
                print(f"[LLM] OpenRouter -> {model}")
                return _FakeResponse(text)
            # 429 / 402 = upstream overloaded — try next model
            err_msg = resp.json().get("error", {}).get("message", str(resp.status_code))
            print(f"[LLM] OpenRouter {model} unavailable ({resp.status_code}) — trying next...")
            last_err = err_msg
        except Exception as e:
            print(f"[LLM] OpenRouter {model} error: {e} — trying next...")
            last_err = str(e)

    raise RuntimeError(f"All OpenRouter free models unavailable. Last error: {last_err}")


def _groq_call(client: Groq, **kwargs) -> object:
    """
    Smart LLM router:
      1. Uses Groq (fast, Llama 3.3 70B) while within its daily 100k token limit.
      2. The instant Groq hits its hard daily cap, permanently switches to
         OpenRouter (free Llama 3.3 70B, no daily token cap) for the rest of the session.
      No sleeping, no waiting — immediate failover.
    """
    global _use_openrouter

    if _use_openrouter:
        return _openrouter_call(**kwargs)

    try:
        return client.chat.completions.create(**kwargs)
    except RateLimitError as e:
        msg = str(e)
        # Hard daily token cap — switch permanently to OpenRouter (no wait)
        is_daily = "tokens per day" in msg.lower() or "tpd" in msg.lower()
        if is_daily:
            print("[LLM] Groq daily token limit reached — switching to OpenRouter (free, no cap).")
            _use_openrouter = True
            return _openrouter_call(**kwargs)
        # RPM throttle — short wait, then one retry
        m = re.search(r'try again in (?:(\d+)m\s*)?(\d+(?:\.\d+)?)s', msg)
        wait = (float(m.group(1) or 0) * 60 + float(m.group(2)) + 3) if m else 30
        print(f"[LLM] Groq RPM limit — waiting {wait:.0f}s...")
        time.sleep(wait)
        try:
            return client.chat.completions.create(**kwargs)
        except RateLimitError:
            print("[LLM] Still rate-limited — switching to OpenRouter permanently.")
            _use_openrouter = True
            return _openrouter_call(**kwargs)


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
            client, model=GROQ_MODEL, max_tokens=80,
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
            client, model=GROQ_MODEL, max_tokens=30,
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
            client, model=GROQ_MODEL, max_tokens=60,
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
1. THE HOOK — sentence 1, maximum 14 words. The single most shocking, concrete, verifiable
   fact of the case. NO date, NO location, NO "In 1982" preamble — drop the viewer straight
   into the most disturbing or impossible detail so they CANNOT scroll away. It must create an
   instant burning question that only watching on can answer.
   GOOD: "When they opened the freezer, they found seven years of his diaries — all dated after he died."
   GOOD: "Every passenger landed safely. There were three more people on the ground than had boarded."
   BANNED openings: "In [year]", "On [date]", "Imagine", "Picture this", "This is the story of" — they kill retention.
2. THE BUILD — escalating beats. Each sentence raises a NEW question before fully resolving the last,
   so an open loop is always running and there is never a clean place to swipe away.
3. THE RETENTION SPIKE — around the two-thirds mark, drop one more jaw-dropping twist that re-hooks
   anyone starting to drift. e.g. "But that wasn't the strangest part."
4. THE ENDING — last 1-2 sentences land on the haunting, still-UNRESOLVED question, then a short
   comment-bait CTA inviting the viewer to weigh in. e.g. "So what really happened to her? Comment your theory."
   or "Guilty, or framed? You decide." This CTA is the ONLY meta line allowed.

Style rules:
- Cold, authoritative documentary-narrator tone — like a Netflix true crime voiceover.
- Short, punchy sentences. Vary rhythm: a very short sentence after a longer one lands like a hammer.
- Real names, real dates, real locations — every factual claim must be accurate. The shock comes from REAL details, never invented ones.
- Write exactly 150-185 words (~60-75 seconds spoken — the highest-retention length).
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
        print(f"[TrueCrime] Sourced: {sourced['title']}  ({sourced['url']})")
    return sourced


def _extract_hook(script: str, max_words: int = 12) -> str:
    """
    Pull the opening hook (first sentence) out of a script for use as a big
    on-screen text overlay in the first ~3 seconds. Trimmed to max_words so it
    fits two lines on a 1080px-wide mobile frame.
    """
    if not script:
        return ""
    first = re.split(r"(?<=[.!?])\s+", script.strip())[0].strip().strip('"').strip()
    words = first.split()
    if len(words) > max_words:
        first = " ".join(words[:max_words]).rstrip(",;:") + "…"
    return first


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
        max_tokens=600,
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
    return case


def _write_script(client: Groq, case: dict) -> str:
    """Write a documentary script based on the researched case."""
    facts = "\n".join(f"- {f}" for f in case.get("key_facts", []))

    # Hand over the full encyclopaedia entry, not just the condensed summary. Working
    # from five key facts alone, the model had too little material for the "jaw-dropping
    # twist" this prompt demands, so it invented one — and the fact-checker then
    # rejected the script at 6/10 for claims the source did not support, burning an
    # attempt each time. The source also carries far more real detail to draw on.
    source = case.get("source_text", "")
    source_block = (
        f"SOURCE (the encyclopaedia entry — the ONLY permitted source of facts):\n"
        f"{source[:6000]}\n\n"
        if source else ""
    )
    grounding = (
        "Every factual claim — names, dates, places, events, the twist — must appear in "
        "the SOURCE above. Dramatise the TELLING, never the facts. If the source has no "
        "jaw-dropping twist, build the spike from the most unsettling detail that IS "
        "there. An invented detail fails fact-check and the script is discarded.\n\n"
        if source else ""
    )

    resp = _groq_call(client,
        model=GROQ_MODEL,
        max_tokens=600,
        messages=[
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
                    "Write the 150-185 word documentary script now. Open on the single most shocking "
                    "verifiable detail (the hook), keep an open loop running throughout, add a retention-spike "
                    "twist near the two-thirds mark, and end on the unresolved question with a comment-bait CTA."
                ),
            },
        ],
    )
    return resp.choices[0].message.content.strip()


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
        max_tokens=400,
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
    # Safe defaults if JSON parse fails
    return {
        "accuracy_score": result.get("accuracy_score", 7),
        "interest_score":  result.get("interest_score",  7),
        "makes_sense":     result.get("makes_sense",     True),
        "tiktok_safe":     result.get("tiktok_safe",     True),
        "issues":          result.get("issues",          []),
        "approved":        result.get("approved",        True),
    }


def _generate_hashtags(client: Groq, case: dict) -> str:
    """
    Generate case-specific TikTok hashtags.

    Returns a string of 7 hashtags:
      2 evergreen base tags + location + crime type + decade + 2 case-specific
    """
    resp = _groq_call(client,
        model=GROQ_MODEL,
        max_tokens=80,
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
_JUNK_TAGS = {"#fyp", "#fypage", "#foryou", "#foryoupage", "#viral", "#viralvideo",
              "#trending", "#tiktok", "#video"}


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
            seen.add(key)
            out.append(tag)
            if len(out) >= limit:
                return " ".join(out)
    return " ".join(out)


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
            max_tokens=110,
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
                    "- Sentence 2: an open question asking viewers what they think "
                    "happened, so they reply in the comments.\n"
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

    for attempt in range(max_attempts):
        print(f"\n[TrueCrime] Attempt {attempt + 1}/{max_attempts} — researching case...")

        # ── Step 1: Source a real, unused case, then research it ──────────────
        sourced = _pick_fresh_case()
        if not sourced:
            print("[TrueCrime] Could not source a fresh case from Wikipedia — retrying.")
            continue

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

        # ── Step 2: Write script ──────────────────────────────────────────────
        print("[TrueCrime] Writing script...")
        script = _write_script(client, case)
        word_count = len(script.split())
        print(f"[TrueCrime] Script: {word_count} words")

        # ── Step 3: Fact-check ────────────────────────────────────────────────
        print("[TrueCrime] Fact-checking...")
        check = _fact_check(client, case, script)
        acc     = check["accuracy_score"]
        interest = check["interest_score"]
        sense   = check["makes_sense"]
        approved = check["approved"]

        tiktok_safe = check["tiktok_safe"]
        print(f"[TrueCrime] Accuracy: {acc}/10 | Interest: {interest}/10 | "
              f"Coherent: {sense} | TikTok-safe: {tiktok_safe} | Approved: {approved}")
        for issue in check.get("issues", []):
            print(f"[TrueCrime]   ! {issue}")

        # ── Step 4: Title ─────────────────────────────────────────────────────
        title_resp = _groq_call(client,
            model=GROQ_MODEL,
            max_tokens=25,
            messages=[{
                "role": "user",
                "content": (
                    f"Write a short, gripping TikTok title for this true crime story "
                    f"(max 7 words, no quotes, no hashtags):\n{script[:200]}"
                ),
            }],
        )
        title = title_resp.choices[0].message.content.strip().strip('"\'')

        # ── Step 5: Case-specific hashtags ────────────────────────────────────
        hashtags = _generate_hashtags(client, case)

        # ── Step 6: Ready-to-paste TikTok caption ─────────────────────────────
        from config import TIKTOK_HASHTAGS
        description = _write_caption(client, case, script) or title
        tags = merge_hashtags(hashtags, TIKTOK_HASHTAGS)
        caption = f"{description}\n\n{tags}"
        print(f"[TrueCrime] Hashtags: {tags}")
        print(f"[TrueCrime] Caption: {description}")

        last_result = {
            "script":         script,
            "title":          title,
            "case_name":      case_name,
            "hashtags":       tags,
            "caption":        caption,
            "accuracy_score": acc,
            "interest_score": interest,
            "tiktok_safe":    tiktok_safe,
            "hook":           _extract_hook(script),
        }

        if approved and acc >= 7 and interest >= 7 and sense and tiktok_safe:
            _USED_CASES.append(case_name)
            _save_recent_case(case_name)
            print(f"[TrueCrime] APPROVED: '{title}'")
            return last_result

        reason = []
        if not tiktok_safe:
            reason.append("TikTok guidelines violation")
        if acc < 7:
            reason.append(f"accuracy {acc}/10")
        if interest < 7:
            reason.append(f"interest {interest}/10")
        if not sense:
            reason.append("incoherent")
        print(f"[TrueCrime] Rejected ({', '.join(reason)}) — trying a different case...")
        _USED_CASES.append(case_name)  # avoid repeating it

    if not last_result:
        raise RuntimeError(
            f"No usable case found in {max_attempts} attempts — every candidate was a "
            f"repeat or unusable. Check the LLM backend is responding."
        )

    # Use the best attempt even if it didn't hit the full quality threshold. Persist it
    # regardless: an unsaved case is picked again next run, which is what made the
    # channel loop the same three videos.
    print(f"[TrueCrime] Using best result after {max_attempts} attempts.")
    _save_recent_case(last_result.get("case_name"))
    return last_result
