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
TTS_VOICE = "en-US-AndrewNeural"            # Natural, clear American male voice
TTS_RATE = "+0%"                             # Natural speed (no distortion)
TTS_PITCH = "+0Hz"                           # Natural pitch (no distortion)

# Other good horror voices to try:
# "en-GB-RyanNeural"       — British male, eerie
# "en-US-GuyNeural"        — American male, dramatic
# "en-IE-ConnorNeural"     — Irish male, atmospheric

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

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR        = os.path.dirname(os.path.abspath(__file__))
GAMEPLAY_DIR    = os.path.join(BASE_DIR, "gameplay")
OUTPUT_DIR      = os.path.join(BASE_DIR, "output")
AUDIO_DIR       = os.path.join(BASE_DIR, "audio")
ASSETS_DIR      = os.path.join(BASE_DIR, "assets")
CREDENTIALS_FILE = os.path.join(BASE_DIR, "client_secrets.json")
TOKEN_FILE       = os.path.join(BASE_DIR, "token.json")
