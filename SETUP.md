# YouTube Shorts Automation — Setup Guide

## What This Does
Automatically creates and uploads YouTube Shorts with:
- 🧠 AI-generated horror stories (Claude)
- 🎙️ Neural voice narration (Microsoft Edge TTS — free)
- 🎮 Minecraft/Subway Surfers gameplay background
- 📝 Auto word-by-word captions (like TikTok)
- 📤 Auto-upload to your YouTube channel

---

## Step 1 — Install Python

1. Go to **https://www.python.org/downloads/**
2. Download Python **3.11** (recommended)
3. Run the installer — **CHECK "Add Python to PATH"** (very important!)
4. Verify: open Command Prompt, type `python --version`

---

## Step 2 — Install FFmpeg

FFmpeg is the video processing engine (free, open source).

1. Go to **https://www.gyan.dev/ffmpeg/builds/**
2. Download **`ffmpeg-release-essentials.zip`**
3. Extract to `C:\ffmpeg\`
4. Add FFmpeg to Windows PATH:
   - Press `Win + S` → search **"Edit environment variables for your account"**
   - Under **User variables**, click **Path** → **Edit**
   - Click **New** → type `C:\ffmpeg\bin`
   - Click **OK** on all windows
5. Open a **new** Command Prompt and verify: `ffmpeg -version`

---

## Step 3 — Run the Setup Script

1. Open Command Prompt
2. Navigate to this folder: `cd C:\Users\maxwi\youtube-automation`
3. Run: `setup_windows.bat`

This installs all Python packages automatically.

---

## Step 4 — Add Your Anthropic API Key

1. Go to **https://console.anthropic.com/**
2. Sign up / log in → **API Keys** → **Create Key**
3. Open `C:\Users\maxwi\youtube-automation\.env`
4. Replace `sk-ant-PASTE-YOUR-KEY-HERE` with your actual key

---

## Step 5 — Set Up YouTube Upload (One-Time)

This lets the script post directly to your YouTube channel.

### 5a. Create a Google Cloud Project
1. Go to **https://console.cloud.google.com/**
2. Click **New Project** → name it "YouTube Automation" → Create
3. In the search bar, search **"YouTube Data API v3"** → **Enable**

### 5b. Create OAuth Credentials
1. Go to **APIs & Services → Credentials**
2. Click **+ Create Credentials → OAuth client ID**
3. If prompted to configure consent screen:
   - Choose **External** → Create
   - Fill in App name: "YouTube Automation"
   - Add your email as test user
   - Save
4. Back in Credentials: **+ Create Credentials → OAuth client ID**
   - Application type: **Desktop app**
   - Name: "YouTube Automation"
   - Click **Create**
5. Click **Download JSON** on the credential you just created
6. **Rename** the downloaded file to `client_secrets.json`
7. **Move** it to: `C:\Users\maxwi\youtube-automation\client_secrets.json`

### 5c. First Upload (Browser Login)
The first time you upload, a browser window will open asking you to log in with your Google account. After that, the token is saved and you won't need to log in again.

---

## Step 6 — Test It!

```bash
# Activate virtual environment
cd C:\Users\maxwi\youtube-automation
venv\Scripts\activate

# Test WITHOUT uploading first
python main.py --no-upload

# Check the output/ folder for your video!
# When happy, run with upload:
python main.py
```

---

## Automate Daily Uploads (Windows Task Scheduler)

1. Press `Win + S` → search **"Task Scheduler"**
2. Click **Create Basic Task**
3. Name: "YouTube Shorts Bot"
4. Trigger: **Daily** at whatever time you want
5. Action: **Start a program**
   - Program: `C:\Users\maxwi\youtube-automation\venv\Scripts\python.exe`
   - Arguments: `main.py`
   - Start in: `C:\Users\maxwi\youtube-automation`
6. Finish!

---

## Customizing Your Videos

Edit `config.py` to change:

| Setting | What It Does |
|---------|-------------|
| `TTS_VOICE` | Change the narrator voice |
| `WORDS_PER_CAPTION` | Words shown at once in captions |
| `CAPTION_FONT_SIZE` | Caption text size |
| `CAPTION_POSITION` | Where captions appear on screen |
| `STORY_WORD_COUNT` | Length of stories (~180 = 60-70 sec) |
| `YOUTUBE_PRIVACY` | "public", "private", or "unlisted" |

### Available Free TTS Voices
- `en-US-ChristopherNeural` — Deep American male (default, great for horror)
- `en-GB-RyanNeural` — British male, eerie feel
- `en-US-GuyNeural` — American male, dramatic
- `en-IE-ConnorNeural` — Irish male, atmospheric
- `en-US-AriaNeural` — American female

---

## Adding Your Own Gameplay

Drop any `.mp4` file into the `gameplay/` folder.
The bot will use it automatically.

---

## Troubleshooting

**"FFmpeg not found"** → Make sure you added `C:\ffmpeg\bin` to PATH and opened a new terminal

**"ANTHROPIC_API_KEY not set"** → Check your `.env` file has the correct key

**"client_secrets.json not found"** → Follow Step 5 to set up Google credentials

**Upload fails with "quota exceeded"** → YouTube API free tier = 10,000 units/day. 1 upload = ~1,600 units. You can upload ~6 videos/day for free.
