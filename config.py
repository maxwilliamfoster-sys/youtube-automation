"""
Configuration for YouTube Shorts Automation
Edit this file to customize your setup.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ─── API Keys ────────────────────────────────────────────────────────────────
# Groq is 100% FREE — get your key at https://console.groq.com/
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# ─── Story Settings ───────────────────────────────────────────────────────────
STORY_TYPES = ["horror", "creepy"]          # Types to rotate through
STORY_WORD_COUNT = 210                       # ~60-65 seconds of speech (sweet spot for horror retention)
GROQ_MODEL = "llama-3.3-70b-versatile"      # Free Llama 3.3 model via Groq

# ─── TTS (Text-to-Speech) Settings ───────────────────────────────────────────
# Free Microsoft Edge neural voices — great quality, no API key needed
TTS_VOICE = "en-GB-RyanNeural"              # Horror pipeline voice — British, cinematic, eerie
TTS_RATE = "-12%"                            # Slightly slower = more dread
TTS_PITCH = "-5Hz"                           # Slightly lower = more ominous

# Documentary / True Crime voice — deeper, more gravitas
TTS_DOCUMENTARY_VOICE = "en-US-GuyNeural"   # Documentary narrator feel
TTS_DOCUMENTARY_RATE  = "-5%"               # Slightly slower = more deliberate
# Alternatives: "en-GB-RyanNeural" (British, authoritative), "en-IE-ConnorNeural" (Irish, atmospheric)

# ─── Video Settings ───────────────────────────────────────────────────────────
VIDEO_WIDTH  = 1080
VIDEO_HEIGHT = 1920   # 9:16 for YouTube Shorts
VIDEO_FPS    = 30

# ─── Caption Settings ─────────────────────────────────────────────────────────
CAPTION_FONT_SIZE  = 65      # Slightly smaller to prevent long words overflowing 1080px width
CAPTION_FONT_COLOR = "white"
CAPTION_STROKE_COLOR = "black"
CAPTION_STROKE_WIDTH = 3
CAPTION_POSITION   = 0.52    # 52% down — centre of screen, above YouTube Shorts UI buttons
WORDS_PER_CAPTION  = 3       # 3 words per line — safer fit, less overflow risk

# ─── Gameplay Sources ─────────────────────────────────────────────────────────
# These are free-to-use gameplay URLs (Creative Commons / no copyright)
GAMEPLAY_SEARCH_QUERIES = [
    "minecraft parkour gameplay no copyright",
    "subway surfers gameplay no copyright free use",
    "satisfying minecraft gameplay no copyright",
]

# ─── YouTube Upload Settings ──────────────────────────────────────────────────
YOUTUBE_CATEGORY_ID = "22"     # People & Blogs (good for storytelling)
YOUTUBE_PRIVACY     = "public" # "public", "private", or "unlisted"
YOUTUBE_TAGS = [
    "horror story", "scary story", "reddit horror", "shorts",
    "creepy", "true scary stories", "horror shorts", "scary shorts"
]
YOUTUBE_DESCRIPTION_TEMPLATE = """#Shorts #Horror #ScaryStory #CreepyStory #HorrorShorts #scary #scarystory #horrortok {story_hashtags}

{title}

Like & Subscribe for daily scary stories!
"""

# ─── Documentary / AI Image Settings ────────────────────────────────────────
POLLINATIONS_MODEL  = "flux"   # Best free model — FLUX via Pollinations
POLLINATIONS_DELAY  = 16.0     # Seconds between requests (anonymous: 1 req/15s)
                               # Register free at auth.pollinations.ai → set to 6.0
NUM_SCENE_IMAGES    = 5        # Atmospheric scenes per video
SCENE_IMAGES_DIR    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scene_images")

# ─── TikTok Upload Settings ──────────────────────────────────────────────────
TIKTOK_HASHTAGS = "#horror #scarystory #horrortok #scarytok #fyp #foryou #creepy #horrorstory"
TIKTOK_CAPTION_TEMPLATE = "{title}\n\n{hashtags} {story_hashtags}"

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR        = os.path.dirname(os.path.abspath(__file__))
GAMEPLAY_DIR    = os.path.join(BASE_DIR, "gameplay")
OUTPUT_DIR      = os.path.join(BASE_DIR, "output")
AUDIO_DIR       = os.path.join(BASE_DIR, "audio")
ASSETS_DIR      = os.path.join(BASE_DIR, "assets")
CREDENTIALS_FILE    = os.path.join(BASE_DIR, "client_secrets.json")
TOKEN_FILE          = os.path.join(BASE_DIR, "token.json")
TIKTOK_COOKIES_FILE = os.path.join(BASE_DIR, "tiktok_cookies.json")
