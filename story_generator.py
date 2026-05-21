"""
Story Generator — uses Groq (FREE) + Llama 3.3 to generate horror stories.
Groq gives you free API access with no credit card needed.
Get your free key at: https://console.groq.com/
"""

import random
from groq import Groq
from config import GROQ_API_KEY, GROQ_MODEL, STORY_TYPES, STORY_WORD_COUNT


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
- Write EXACTLY one complete story with a beginning, middle, and terrifying end
- You MUST write between {STORY_WORD_COUNT - 10} and {STORY_WORD_COUNT + 10} words — count carefully, this is critical
- Write in THIRD PERSON ("he", "she", "they") throughout — NEVER use "I" or "we"
- Give the main character a name — use it naturally throughout the story
- NO headers, NO titles, NO quotation marks around the whole story, NO markdown
- Make each story feel UNIQUE — different setting, character, tone, pacing and twist every time
- Some stories should be slow and creepy, others fast and shocking — vary the style
- End with a gut-punch twist or horrifying realisation the reader never saw coming
- VARY your sentence openings — NEVER start two sentences in a row with the same word
- NEVER open with the words "dark", "darkness", "shadow", "shadows", "the night" — be more creative and specific
- NEVER open with cliches like "It was a stormy night" or "The town of X had always been strange"
- Open with something immediate and specific — a sound, an action, a discovery, a detail that hooks instantly
- Use sentence starters like: "The", "Something", "Nothing", "Every", "At", "Before", "Suddenly", "Behind", "Outside", "When", "What", "She", "He", "They", "Her", "His", "A" etc.
- Example good opening: "The voicemail had no timestamp. Sarah played it twice before she understood the voice was her own."
- Use short punchy sentences for pacing — they build dread
- The story should feel cinematic and gripping — like a thriller short film, not a diary entry
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
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        max_tokens=600,
        messages=[
            {"role": "system", "content": STORY_SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ]
    )

    story_text = response.choices[0].message.content.strip()

    # Generate a title
    title_response = client.chat.completions.create(
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

    # Proofread and fix the story — enforce consistent POV and coherence
    proofread_response = client.chat.completions.create(
        model=GROQ_MODEL,
        max_tokens=700,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a proofreader for short horror stories. "
                    "Fix the story so it: "
                    "1) Uses ONLY third person (he/she/they/his/her/their) throughout — NEVER uses 'I', 'me', 'my', or 'we'. If any first-person slips exist, rewrite them in third person. "
                    "2) NEVER starts two consecutive sentences with the same word — rewrite with varied openers like 'The', 'Something', 'She', 'He', 'Every', 'Suddenly', 'Nothing', 'Before', 'After', 'Outside', 'Behind' etc. "
                    "3) NEVER starts with the words dark, darkness, shadow or shadows — rewrite the opening if it does. "
                    "4) Makes complete logical sense from start to finish with no contradictions. "
                    "5) Flows naturally and keeps the reader hooked. "
                    "Keep the same word count, tone and ending. Output ONLY the fixed story, nothing else."
                ),
            },
            {"role": "user", "content": story_text},
        ]
    )
    story_text = proofread_response.choices[0].message.content.strip()
    print(f"[Story] Proofread complete")

    # Generate relevant hashtags for this specific story
    hashtag_response = client.chat.completions.create(
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
