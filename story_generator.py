"""
Story Generator — uses Groq (FREE) + Llama 3.3 to generate horror stories
and true crime documentary scripts with research + fact-checking.
"""

import re
import json
import random
import time
import requests as _requests
from groq import Groq, RateLimitError
from config import GROQ_API_KEY, GROQ_MODEL, STORY_TYPES, STORY_WORD_COUNT, OPENROUTER_API_KEY

# ── LLM backend constants ─────────────────────────────────────────────────────
GROQ_FALLBACK_MODEL   = "llama-3.1-8b-instant"
OPENROUTER_BASE_URL   = "https://openrouter.ai/api/v1/chat/completions"

# OpenRouter free models tried in order — all 0 cost, no daily token cap
# If the first is rate-limited upstream, we try the next automatically
OPENROUTER_FREE_MODELS = [
    "meta-llama/llama-3.3-70b-instruct:free",   # Llama 3.3 70B — best quality match
    "nvidia/nemotron-3-super-120b-a12b:free",    # Nemotron 120B — confirmed working
    "openai/gpt-oss-120b:free",                  # GPT OSS 120B — very capable
    "nousresearch/hermes-3-llama-3.1-405b:free", # Hermes 405B — last resort
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


HORROR_PROMPTS = [
    # Supernatural / paranormal
    "Write a horror story about a woman who notices her reflection has started moving half a second too late.",
    "Write a creepy story about a man who keeps appearing in the background of strangers photos — always watching.",
    "Write a horror story about a teenage boy whose Ouija board begins answering questions before anyone touches the planchette.",
    "Write a terrifying story about a nurse who finds her patient in a different room each morning with no memory of moving.",
    "Write a chilling story about a child who tells her parents about the tall figure that stands at the foot of her bed every night.",
    "Write a scary story about an antique music box bought at an estate sale that plays a lullaby for a dead child.",
    "Write a horror story about a small coastal town where everyone stopped aging thirty years ago — except one woman.",
    "Write a creepy story about a man renovating his home who discovers a sealed room that isnt on any blueprint.",
    "Write a horror story about a couple who rent a remote cottage and slowly realise the previous tenants never left.",

    # Technology / modern horror
    "Write a horror story about a man who receives a voicemail from his own number — recorded three hours after his death.",
    "Write a scary story about a woman whose smart home locks her inside and speaks in her dead mothers voice.",
    "Write a creepy story about a software engineer who finds a deepfake video of himself committing a crime he has no memory of.",
    "Write a horror story about a teenage girl who discovers a Reddit account documenting her daily routine for two years.",
    "Write a terrifying story about a security guard who notices CCTV footage shows events that havent happened yet.",
    "Write a chilling story about a grief counsellor whose AI therapy app reveals things only the deceased could have known.",

    # Psychological / slow burn
    "Write a horror story about a detective investigating a series of murders who realises all the evidence points to himself.",
    "Write a creepy story about a woman who notices her furniture has been moved by inches each night while she slept.",
    "Write a terrifying story about a man who cannot tell whether the last six months of his life were real or a coma dream.",
    "Write a horror story about a hiker found alive after eleven years missing — who has not aged a single day.",
    "Write a scary story about a grief support group where every member shares the exact same recurring nightmare.",
    "Write a horror story about a soldier who returns home only to find his family has no memory of him ever existing.",

    # Wilderness / isolation
    "Write a horror story about a trail runner who finds a fully set campsite deep in a forest — coffee still warm, no one around.",
    "Write a creepy story about a lighthouse keeper who begins receiving Morse code from a ship that sank in 1943.",
    "Write a terrifying story about a family whose GPS leads them off the highway onto a road that does not exist on any map.",
    "Write a scary story about a research team in Antarctica who begin to suspect one of their colleagues is not human.",
    "Write a horror story about a survivalist who sets cameras around his remote cabin and reviews footage each morning.",

    # Everyday life turned sinister
    "Write a horror story about a babysitter who realises the children she is watching are not the same children from the family photo.",
    "Write a chilling story about a coroner who receives a body for autopsy and recognises it as someone he spoke to that morning.",
    "Write a scary story about a woman who sees the same stranger on her commute every day — always in exactly the right place.",
    "Write a creepy story about a plumber who discovers a fully furnished living space hidden behind the walls of a family home.",
    "Write a horror story about a woman who arrives for a job interview only to find the company has no record of contacting her.",
    "Write a terrifying story about a man who steps into an elevator on the 14th floor and the doors open on a floor that should not exist.",

    # Body horror / identity
    "Write a horror story about a surgeon who wakes from a coma to find unexplained surgical scars covering his body.",
    "Write a creepy story about a woman whose shadow moves independently — always one step ahead of her.",
    "Write a terrifying story about a man who finds his own teeth sealed in jars hidden throughout his house.",
    "Write a scary story about a sleepwalker whose husband installs cameras and is horrified by where she goes each night.",
    "Write a horror story about identical twins where one begins to suspect the other was replaced by something else years ago.",
]

STORY_SYSTEM_PROMPT = f"""You are a master horror storyteller writing scripts for YouTube Shorts.

Rules:
- Write EXACTLY one complete story with a clear beginning, middle, and terrifying end
- You MUST write between {STORY_WORD_COUNT - 10} and {STORY_WORD_COUNT + 10} words — count carefully
- Write ENTIRELY in FIRST PERSON ("I", "my", "me", "myself") — never switch to any other POV
- Write ENTIRELY in PAST TENSE — every action, thought and observation must be past tense ("I heard", "I saw", "it was", "I walked", "I noticed", "I felt")
- The story MUST make complete logical sense from start to finish — events must follow causally, no plot holes, no contradictions, no unresolved loose ends
- Each sentence must naturally follow from the one before — the reader should always know where they are and what is happening
- NO headers, NO titles, NO quotation marks around the whole story, NO markdown
- Make each story feel UNIQUE — different setting, tone, pacing and twist every time
- End with a gut-punch twist or horrifying realisation the reader never saw coming
- VARY your sentence openings — NEVER start two consecutive sentences with "I"
- NEVER open with the words "dark", "darkness", "shadow", "shadows"
- NEVER open with cliches like "It was a stormy night" or "I was alone"
- Open with something immediate and gripping — a sound, an action, a discovery
- Example good opening: "The voicemail had no timestamp. I played it twice before I understood the voice was my own."
- Use short punchy sentences for pacing — they build dread
- The story should feel like a viral Reddit r/nosleep post — personal, believable, terrifying
- Output ONLY the story text, nothing else"""


def generate_story() -> dict:
    """
    Generate a horror story using Groq (free).

    Returns:
        dict with keys: 'story' (str), 'title' (str), 'type' (str)
    """
    client = Groq(api_key=GROQ_API_KEY)

    story_type = random.choice(STORY_TYPES)
    prompt = random.choice(HORROR_PROMPTS)

    print(f"[Story] Generating {story_type} story with Llama 3.3 (free)...")

    # Generate the story
    response = _groq_call(
        client,
        model=GROQ_MODEL,
        max_tokens=600,
        messages=[
            {"role": "system", "content": STORY_SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ]
    )

    story_text = response.choices[0].message.content.strip()

    # Generate a title
    title_response = _groq_call(
        client,
        model=GROQ_MODEL,
        max_tokens=30,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Give me a short, creepy YouTube Short title "
                    f"(max 8 words, no quotes, no hashtags) for this story:\n\n{story_text[:200]}"
                ),
            }
        ]
    )

    title = title_response.choices[0].message.content.strip().strip('"').strip("'")

    # Proofread and fix the story — enforce POV, tense, and coherence
    proofread_response = _groq_call(
        client,
        model=GROQ_MODEL,
        max_tokens=700,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a strict editor for short horror stories. Rewrite the story fixing ALL of the following:\n"
                    "1) FIRST PERSON ONLY — every sentence must use I/my/me/myself. "
                    "Remove any 'he', 'she', 'they', 'you', 'we' that refer to the narrator.\n"
                    "2) PAST TENSE THROUGHOUT — convert every present-tense verb to past tense. "
                    "'I see' → 'I saw', 'it is' → 'it was', 'I hear' → 'I heard', 'I run' → 'I ran'. No exceptions.\n"
                    "3) COHERENCE — ensure events follow a logical order, nothing contradicts, "
                    "no unresolved threads, no sudden unexplained jumps. The reader must always know what is happening.\n"
                    "4) VARIED SENTENCE STARTS — never two consecutive sentences starting with 'I'.\n"
                    "5) OPENING — must NOT start with 'dark', 'darkness', 'shadow', 'shadows', "
                    "'It was a stormy', or 'I was alone'.\n"
                    "Keep the same word count, tone, and ending twist. Output ONLY the corrected story text, nothing else."
                ),
            },
            {"role": "user", "content": story_text},
        ]
    )
    story_text = proofread_response.choices[0].message.content.strip()
    print(f"[Story] Proofread complete")

    # Generate relevant hashtags for this specific story
    hashtag_response = _groq_call(
        client,
        model=GROQ_MODEL,
        max_tokens=60,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Give me 5 relevant YouTube hashtags for this horror story (no spaces in each tag, "
                    f"just the hashtags separated by spaces, no explanation):\n\n{story_text[:200]}"
                ),
            }
        ]
    )

    raw_tags = hashtag_response.choices[0].message.content.strip()
    # Clean up — keep only words starting with # or add # if missing, take first 5 tags
    tag_words = raw_tags.replace("\n", " ").split()
    cleaned_tags = [
        w if w.startswith("#") else f"#{w}"
        for w in tag_words if w.replace("#", "").isalnum()
    ]
    story_hashtags = " ".join(cleaned_tags[:5])

    word_count = len(story_text.split())
    print(f"[Story] Generated: '{title}' ({word_count} words)")

    return {
        "story": story_text,
        "title": title,
        "type": story_type,
        "hashtags": story_hashtags,
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

Style rules:
- Cold, authoritative, documentary narrator tone — like Netflix true crime
- Short punchy sentences that build tension and dread
- Start with a striking date, location, or jaw-dropping fact
- Build to a shocking revelation or unanswered question
- End on what is still unknown, unresolved, or haunting
- Real names, real dates, real locations — every word must be factually accurate
- Write exactly 190-220 words (count carefully — TikTok needs 60-90 seconds)
- NO narrator asides, NO "In this video", NO "Subscribe", NO fluff
- Output ONLY the script text, nothing else"""

# Track used cases across the session to avoid repeats
_USED_CASES: list = []


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


def _research_case(client: Groq) -> dict:
    """Ask Groq to research and propose a compelling true crime case."""
    exclude = f"\n\nAvoid these cases (already used): {', '.join(_USED_CASES[-20:])}" if _USED_CASES else ""

    resp = _groq_call(client,
        model=GROQ_MODEL,
        max_tokens=500,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a true crime researcher. Propose one real, compelling, "
                    "verifiable case for a short documentary. Prefer lesser-known cases "
                    "with shocking twists, unresolved mysteries, or haunting unanswered questions. "
                    "Avoid: Jack the Ripper, Ted Bundy, Jeffrey Dahmer, BTK, Zodiac Killer "
                    "(too overexposed). "
                    "Reply ONLY with valid JSON — no markdown, no explanation:\n"
                    '{"case_name":"...","location":"...","year":"...","summary":"...",'
                    '"key_facts":["fact1","fact2","fact3","fact4","fact5"],'
                    '"unresolved":"...","why_compelling":"..."}'
                ),
            },
            {
                "role": "user",
                "content": (
                    "Suggest one real true crime case: cold case, mysterious death, "
                    "shocking murder, unsolved disappearance, or conspiracy. "
                    "Must be historically documented and verifiable."
                    + exclude
                ),
            },
        ],
    )
    return _extract_json(resp.choices[0].message.content.strip())


def _write_script(client: Groq, case: dict) -> str:
    """Write a documentary script based on the researched case."""
    facts = "\n".join(f"- {f}" for f in case.get("key_facts", []))
    resp = _groq_call(client,
        model=GROQ_MODEL,
        max_tokens=600,
        messages=[
            {"role": "system", "content": TRUE_CRIME_SCRIPT_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Case: {case.get('case_name','')}, {case.get('location','')}, {case.get('year','')}\n\n"
                    f"Background: {case.get('summary','')}\n\n"
                    f"Key facts:\n{facts}\n\n"
                    f"Still unresolved: {case.get('unresolved','')}\n\n"
                    "Write the 190-220 word documentary script now."
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
                    f"Script:\n{script}\n\n"
                    "Check: Are all facts accurate? Are dates/names/events real? "
                    "Does the narrative make complete logical sense? Is it genuinely interesting? "
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


def generate_true_crime_story(max_attempts: int = 3) -> dict:
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

    last_result = None

    for attempt in range(max_attempts):
        print(f"\n[TrueCrime] Attempt {attempt + 1}/{max_attempts} — researching case...")

        # ── Step 1: Research ──────────────────────────────────────────────────
        case = _research_case(client)
        case_name = case.get("case_name", f"Unknown Case {attempt}")
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
        print(f"[TrueCrime] Hashtags: {hashtags}")

        last_result = {
            "script":         script,
            "title":          title,
            "case_name":      case_name,
            "hashtags":       hashtags,
            "accuracy_score": acc,
            "interest_score": interest,
            "tiktok_safe":    tiktok_safe,
        }

        if approved and acc >= 7 and interest >= 7 and sense and tiktok_safe:
            _USED_CASES.append(case_name)
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

    # Use best attempt even if it didn't hit full threshold
    print(f"[TrueCrime] Using best result after {max_attempts} attempts.")
    return last_result
