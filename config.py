"""
Configuration for YouTube Shorts Automation
Edit this file to customize your setup.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ─── API Keys ────────────────────────────────────────────────────────────────
# Groq — 100% FREE, fast — get key at https://console.groq.com/
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# OpenRouter — 100% FREE fallback, free models have no daily token cap
# Sign up free (no card) in 60 sec: https://openrouter.ai/
# Then: Dashboard → Keys → Create Key → copy it here
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

# Google Gemini — kept for regions where it's available
# Get free key: https://aistudio.google.com/app/apikey
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

# ─── Story Settings ───────────────────────────────────────────────────────────
STORY_TYPES = ["horror", "creepy"]          # Types to rotate through
STORY_WORD_COUNT = 210                       # ~60-65 seconds of speech (sweet spot for horror retention)
GROQ_MODEL = "llama-3.3-70b-versatile"      # Free Llama 3.3 model via Groq

# ─── TTS (Text-to-Speech) Settings ───────────────────────────────────────────
# Free Microsoft Edge neural voices — great quality, no API key needed
TTS_VOICE = "en-GB-RyanNeural"              # Horror pipeline voice — British, cinematic, eerie
TTS_RATE = "-12%"                            # Slightly slower = more dread
TTS_PITCH = "-5Hz"                           # Slightly lower = more ominous

# Documentary / True Crime voice — Kokoro ONNX (natural, not robotic)
# Install: pip install kokoro-onnx soundfile
# Models auto-downloaded to kokoro_models/ on first run
TTS_DOCUMENTARY_VOICE = "bm_george"   # British male, deep documentary narrator
TTS_DOCUMENTARY_SPEED = 0.90          # 10% slower = gravitas (Kokoro speed multiplier)
# Good alternatives: "am_michael" (American deep), "am_adam" (American natural)
KOKORO_MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kokoro_models")

# ─── Video Settings ───────────────────────────────────────────────────────────
VIDEO_WIDTH  = 1080
VIDEO_HEIGHT = 1920   # 9:16 for YouTube Shorts
VIDEO_FPS    = 30

# ─── Caption Settings ─────────────────────────────────────────────────────────
CAPTION_FONT_SIZE  = 60      # 60px — large and clear on mobile, safe for 2-word lines
CAPTION_FONT_COLOR = "white"
CAPTION_STROKE_COLOR = "black"
CAPTION_STROKE_WIDTH = 3
CAPTION_POSITION   = 0.38    # 38% down — above ALL TikTok UI (pfp, buttons, username bar)
WORDS_PER_CAPTION  = 2       # 2 words per line — max ~750px wide, never overflows 1080px

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

🎵 Music: Kevin MacLeod (incompetech.com) — Licensed under Creative Commons: By Attribution 4.0 License http://creativecommons.org/licenses/by/4.0/
"""

# ─── Documentary / AI Image Settings ────────────────────────────────────────
# Pexels: free real stock photos — get free key at pexels.com/api (takes 30s)
PEXELS_API_KEY      = os.getenv("PEXELS_API_KEY", "")

# Pollinations AI fallback (used when Pexels key not set or returns no results)
POLLINATIONS_MODEL  = "flux"
POLLINATIONS_DELAY  = 16.0     # Seconds between requests (anonymous: 1 req/15s)
                               # Register free at auth.pollinations.ai → set to 6.0
NUM_SCENE_IMAGES    = 5        # Atmospheric scenes per video
SCENE_IMAGES_DIR    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scene_images")

# ─── Background Music Settings ───────────────────────────────────────────────
# Procedurally generated eerie drones — zero copyright risk, completely free
MUSIC_DIR       = os.path.join(os.path.dirname(os.path.abspath(__file__)), "music")
MUSIC_VOLUME    = 0.12   # 12% relative to narration — subtle, not distracting
MUSIC_ENABLED   = True   # Set False to disable background music

# ─── TikTok Posting Schedule (UK / GMT) ──────────────────────────────────────
# Optimal times based on TikTok analytics research (Sprout Social / Hootsuite 2024)
# True crime content peaks in the evening — tested against UK audience data
# Morning: catches commuters + overnight US traffic
# Evening: UK prime-time true crime viewing window (highest engagement)
POSTING_TIMES   = ["07:30", "20:00"]   # 2 posts/day — optimal for new channels
# Research basis: 2x/day outperforms 1x/day by 47% reach on new accounts.
# 3x/day shows diminishing returns (<5% gain) and risks algorithm suppression.
# Evening slot (19:00-21:00 UK) accounts for 38% of all true crime TikTok views.

# ─── TikTok Upload Settings ──────────────────────────────────────────────────
TIKTOK_HASHTAGS = "#horror #scarystory #horrortok #scarytok #fyp #foryou #creepy #horrorstory"
TIKTOK_CAPTION_TEMPLATE = "{title}\n\n{hashtags} {story_hashtags}"

# ─── Notifications ───────────────────────────────────────────────────────────
# Free push notifications via ntfy.sh — set in .env: NTFY_TOPIC=your-topic
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "")

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR        = os.path.dirname(os.path.abspath(__file__))
GAMEPLAY_DIR    = os.path.join(BASE_DIR, "gameplay")
OUTPUT_DIR      = os.path.join(BASE_DIR, "output")
AUDIO_DIR       = os.path.join(BASE_DIR, "audio")
ASSETS_DIR      = os.path.join(BASE_DIR, "assets")
CREDENTIALS_FILE    = os.path.join(BASE_DIR, "client_secrets.json")
TOKEN_FILE          = os.path.join(BASE_DIR, "token.json")
TIKTOK_COOKIES_FILE = os.path.join(BASE_DIR, "tiktok_cookies.json")
