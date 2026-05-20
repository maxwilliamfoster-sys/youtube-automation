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
    "Write a horror story about a person who discovers their reflection has been acting on its own.",
    "Write a creepy story about a ghost that only appears in the background of photos.",
    "Write a horror story about a Ouija board that starts responding before anyone touches it.",
    "Write a terrifying story about a person who keeps waking up in a different room with no memory.",
    "Write a chilling story about a figure that stands at the end of the bed every night.",
    "Write a scary story about a cursed object bought at a garage sale.",
    "Write a horror story about a town where everyone stops aging — except one person.",
    "Write a creepy story about a person who realises their house has a room that shouldnt exist.",

    # Technology / modern horror
    "Write a horror story about a person who gets a text from their own number.",
    "Write a scary story about a smart home device that starts speaking on its own.",
    "Write a creepy story about a deepfake video of yourself doing things you never did.",
    "Write a horror story about a person who finds their exact daily routine posted online by a stranger.",
    "Write a terrifying story about a security camera that shows footage from the future.",
    "Write a chilling story about an AI chatbot that knows things it should never know.",

    # Psychological / slow burn
    "Write a horror story where the narrator slowly realises they are the monster.",
    "Write a creepy story about a person who starts noticing small things in their home being moved.",
    "Write a terrifying story about a person who cant tell if they are dreaming or awake.",
    "Write a horror story about someone who has been missing for years but doesnt know it.",
    "Write a scary story about a support group where everyone shares the same nightmare.",

    # Wilderness / isolation
    "Write a horror story about a hiker who finds a campsite that shouldnt exist deep in the woods.",
    "Write a creepy story about a lighthouse keeper who starts hearing voices in the fog.",
    "Write a terrifying story about a family road trip where the GPS leads somewhere terrifying.",
    "Write a scary story about a cabin in the woods where the previous guests never left.",

    # Everyday life turned sinister
    "Write a horror story about a babysitter who notices the children are not who they seem.",
    "Write a chilling story about a doctor who realises their patient died days before the appointment.",
    "Write a scary story about a person who keeps seeing the same stranger everywhere they go.",
    "Write a creepy story about a neighbour who seems to know everything that happens inside your house.",
    "Write a horror story about a job interview that turns into something deeply wrong.",
    "Write a terrifying story about an elevator that opens on a floor that doesnt exist.",

    # Body horror / identity
    "Write a horror story about a person who wakes up and something feels wrong with their body.",
    "Write a creepy story about a person whose shadow does not match their movements.",
    "Write a terrifying story about a person who starts finding teeth in unexpected places around their home.",
    "Write a scary story about a person who discovers they have been sleepwalking somewhere horrifying.",
]

STORY_SYSTEM_PROMPT = f"""You are a master horror storyteller writing scripts for YouTube Shorts.

Rules:
- Write EXACTLY one complete story with a beginning, middle, and terrifying end
- You MUST write between {STORY_WORD_COUNT - 10} and {STORY_WORD_COUNT + 10} words — count carefully, this is critical
- Alternate between first person ("I") and second person ("You") — vary it each time
- NO headers, NO titles, NO quotation marks around the whole story, NO markdown
- Make each story feel UNIQUE — different setting, tone, pacing and twist every time
- Some stories should be slow and creepy, others fast and shocking — vary the style
- End with a gut-punch twist or horrifying realization the reader never saw coming
- Use short sentences for pacing. Like this. It builds dread.
- The story should feel like a Reddit r/nosleep post — personal, believable, terrifying
- NEVER start with "I was" or "It was" — open with something that immediately grabs attention
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
