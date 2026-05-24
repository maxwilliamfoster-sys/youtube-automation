"""
Music Setup — downloads real royalty-free horror/suspense tracks + clean caption font.

Sources (all copyright-safe for TikTok & YouTube):
  Kevin MacLeod (CC BY 4.0) — cinematic orchestral/horror — incompetech.com
  HoliznaCC0    (CC0)        — eerie piano, public domain — freemusicarchive.org

Add this one line to your YouTube/TikTok descriptions for MacLeod tracks:
  Music: Kevin MacLeod (incompetech.com) — Licensed under CC BY 4.0

Usage:
    py setup_music.py            # download tracks (skip existing)
    py setup_music.py --force    # re-download everything
    py setup_music.py --fallback # generate procedural drones if internet unavailable
"""

import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

MUSIC_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "music")
ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

# ── Track library ──────────────────────────────────────────────────────────────
# Multiple URL fallbacks per track — first one that returns 200 + valid MP3 wins.
TRACKS = [
    # ── Kevin MacLeod — CC BY 4.0 — orchestral cinematic horror ──────────────
    {
        "filename": "track_01_unseen_horrors.mp3",
        "name":     "Unseen Horrors",
        "artist":   "Kevin MacLeod",
        "urls": [
            "https://incompetech.com/music/royalty-free/mp3-royaltyfree/Unseen%20Horrors.mp3",
            "https://freemusicbg.com/wp-content/uploads/Unseen-Horrors.mp3",
        ],
        "license":     "CC BY 4.0",
        "attribution": '"Unseen Horrors" Kevin MacLeod (incompetech.com) CC-BY-4.0',
    },
    {
        "filename": "track_02_this_house.mp3",
        "name":     "This House",
        "artist":   "Kevin MacLeod",
        "urls": [
            "https://incompetech.com/music/royalty-free/mp3-royaltyfree/This%20House.mp3",
            "https://freemusicbg.com/wp-content/uploads/This-House.mp3",
        ],
        "license":     "CC BY 4.0",
        "attribution": '"This House" Kevin MacLeod (incompetech.com) CC-BY-4.0',
    },
    {
        "filename": "track_03_dark_walk.mp3",
        "name":     "Dark Walk",
        "artist":   "Kevin MacLeod",
        "urls": [
            "https://incompetech.com/music/royalty-free/mp3-royaltyfree/Dark%20Walk.mp3",
            "https://freemusicbg.com/wp-content/uploads/Dark-Walk.mp3",
        ],
        "license":     "CC BY 3.0",
        "attribution": '"Dark Walk" Kevin MacLeod (incompetech.com) CC-BY-3.0',
    },
    {
        "filename": "track_04_gathering_darkness.mp3",
        "name":     "Gathering Darkness",
        "artist":   "Kevin MacLeod",
        "urls": [
            "https://incompetech.com/music/royalty-free/mp3-royaltyfree/Gathering%20Darkness.mp3",
            "https://freemusicbg.com/wp-content/uploads/Gathering-Darkness.mp3",
        ],
        "license":     "CC BY 3.0",
        "attribution": '"Gathering Darkness" Kevin MacLeod (incompetech.com) CC-BY-3.0',
    },
    {
        "filename": "track_05_trepidation.mp3",
        "name":     "Trepidation",
        "artist":   "Kevin MacLeod",
        "urls": [
            "https://incompetech.com/music/royalty-free/mp3-royaltyfree/Trepidation.mp3",
            "https://freemusicbg.com/wp-content/uploads/Trepidation.mp3",
        ],
        "license":     "CC BY 3.0",
        "attribution": '"Trepidation" Kevin MacLeod (incompetech.com) CC-BY-3.0',
    },
    # ── HoliznaCC0 — CC0 Public Domain — eerie piano, zero attribution needed ─
    {
        "filename": "track_06_creepy_piano_1.mp3",
        "name":     "Creepy Piano 1",
        "artist":   "HoliznaCC0",
        "urls": [
            "https://files.freemusicarchive.org/storage-freemusicarchive-org/music/no_curator/HoliznaCC0/Background_Music/HoliznaCC0_-_01_-_Creepy_Piano_1.mp3",
            "https://freemusicarchive.org/track/creepy-piano-1/download/",
        ],
        "license":     "CC0 Public Domain",
        "attribution": None,
    },
    {
        "filename": "track_07_creepy_piano_2.mp3",
        "name":     "Creepy Piano 2",
        "artist":   "HoliznaCC0",
        "urls": [
            "https://files.freemusicarchive.org/storage-freemusicarchive-org/music/no_curator/HoliznaCC0/Background_Music/HoliznaCC0_-_02_-_Creepy_Piano_2.mp3",
            "https://freemusicarchive.org/track/creepy-piano-2/download/",
        ],
        "license":     "CC0 Public Domain",
        "attribution": None,
    },
    {
        "filename": "track_08_creepy_piano_3.mp3",
        "name":     "Creepy Piano 3",
        "artist":   "HoliznaCC0",
        "urls": [
            "https://files.freemusicarchive.org/storage-freemusicarchive-org/music/no_curator/HoliznaCC0/Background_Music/HoliznaCC0_-_03_-_Creepy_Piano_3.mp3",
            "https://freemusicarchive.org/track/creepy-piano-3/download/",
        ],
        "license":     "CC0 Public Domain",
        "attribution": None,
    },
]

# ── Caption font ───────────────────────────────────────────────────────────────
FONT_FILENAME = "MontserratBold.ttf"
FONT_URLS = [
    # Original Montserrat author repo (Julieta Ulanovsky) — most stable TTF source
    "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-Bold.ttf",
    # Google Fonts GitHub (master branch)
    "https://raw.githubusercontent.com/google/fonts/master/ofl/montserrat/static/Montserrat-Bold.ttf",
    # jsDelivr CDN mirror
    "https://cdn.jsdelivr.net/gh/JulietaUla/Montserrat@master/fonts/ttf/Montserrat-Bold.ttf",
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def _download(urls: list, dest_path: str, label: str, is_audio: bool = True) -> bool:
    """
    Try each URL in order.  Returns True on first success, False if all fail.
    is_audio=True  — validates MP3 header and enforces 64 KB minimum size.
    is_audio=False — only checks the file is non-empty (for fonts, images, etc).
    """
    for url in urls:
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()

            if is_audio:
                if len(data) < 65536:
                    print(f"[Setup]   SKIP too small ({len(data)}B) - redirect page, not audio.")
                    continue
                # MP3 files start with ID3 tag or MPEG sync word
                if not (data[:3] == b"ID3" or
                        (len(data) >= 2 and data[0] == 0xFF and
                         data[1] in (0xFB, 0xF3, 0xF2, 0xFA))):
                    print(f"[Setup]   SKIP invalid MP3 header - probably an HTML page.")
                    continue
            else:
                if len(data) < 1024:
                    print(f"[Setup]   SKIP too small ({len(data)}B).")
                    continue

            with open(dest_path, "wb") as f:
                f.write(data)
            kb = len(data) // 1024
            print(f"[Setup]   OK  {label}  ({kb} KB)")
            return True
        except Exception as e:
            print(f"[Setup]   FAIL {url[:80]}  ({e})")
    return False


# ── Font download ──────────────────────────────────────────────────────────────

def download_font(force: bool = False):
    Path(ASSETS_DIR).mkdir(parents=True, exist_ok=True)
    dest = os.path.join(ASSETS_DIR, FONT_FILENAME)
    if os.path.exists(dest) and not force:
        kb = os.path.getsize(dest) // 1024
        print(f"[Setup] Font already exists: {FONT_FILENAME} ({kb} KB) - skip")
        return True
    print(f"[Setup] Downloading caption font: {FONT_FILENAME}...")
    ok = _download(FONT_URLS, dest, FONT_FILENAME, is_audio=False)
    if not ok:
        print("[Setup] WARNING: Font download failed - captions will use Arial Bold.")
    return ok


# ── Music download ─────────────────────────────────────────────────────────────

def download_tracks(force: bool = False):
    Path(MUSIC_DIR).mkdir(parents=True, exist_ok=True)

    # Remove old procedurally-generated drone WAVs
    import glob
    old_drones = glob.glob(os.path.join(MUSIC_DIR, "drone_*.wav"))
    if old_drones:
        print(f"[Setup] Removing {len(old_drones)} old drone WAV(s)...")
        for f in old_drones:
            os.remove(f)

    downloaded = 0
    failed     = []

    for track in TRACKS:
        dest = os.path.join(MUSIC_DIR, track["filename"])
        if os.path.exists(dest) and not force:
            kb = os.path.getsize(dest) // 1024
            print(f"[Setup] Already exists: {track['name']} ({kb} KB) — skip")
            downloaded += 1
            continue

        print(f"\n[Setup] Downloading: {track['name']} ({track['artist']})  [{track['license']}]")
        ok = _download(track["urls"], dest, track["name"])
        if ok:
            downloaded += 1
        else:
            failed.append(track)

    # Summary
    print(f"\n[Setup] Downloaded {downloaded}/{len(TRACKS)} tracks.")

    if failed:
        print("\n[Setup] WARNING: The following tracks could not be downloaded automatically:")
        for t in failed:
            print(f"  - {t['name']} ({t['artist']})")
            print(f"    Download manually: https://incompetech.com  (search '{t['name']}')")
            print(f"    Save as: music/{t['filename']}")
        print("\n[Setup] To generate procedural fallback drones for missing tracks:")
        print("  py setup_music.py --fallback")

    # Print attribution block
    needs_credit = [t for t in TRACKS
                    if t["attribution"] and os.path.exists(os.path.join(MUSIC_DIR, t["filename"]))]
    if needs_credit:
        print("\n" + "-" * 60)
        print("ATTRIBUTION -- add this to your video descriptions:")
        print("-" * 60)
        for t in needs_credit:
            print(f"  {t['attribution']}")
        print("-" * 60)

    total = len(list(Path(MUSIC_DIR).glob("track_*.mp3")))
    print(f"\n[Setup] {total} track(s) ready in music/")
    return downloaded


# ── Procedural fallback ────────────────────────────────────────────────────────

def generate_fallback_drones(n_tracks: int = 8, force: bool = False):
    """
    Generate procedural WAV drones — used only when real tracks can't be downloaded.
    Improved synthesis: sawtooth blend + Schroeder reverb + more musical frequences.
    """
    import numpy as np
    try:
        import soundfile as sf
    except ImportError:
        print("[Setup] soundfile not installed — run: pip install soundfile")
        return

    SAMPLE_RATE = 44100
    DURATION_S  = 150.0

    Path(MUSIC_DIR).mkdir(parents=True, exist_ok=True)

    def _make_drone(seed: int) -> np.ndarray:
        rng = np.random.default_rng(seed)
        n   = int(SAMPLE_RATE * DURATION_S)
        t   = np.arange(n) / SAMPLE_RATE
        out = np.zeros(n, dtype=np.float64)

        def _osc(f, amp, wtype="sine"):
            lfo = f * (1 + rng.uniform(0.002, 0.005) * np.sin(
                2 * np.pi * rng.uniform(0.03, 0.08) * t + rng.uniform(0, 6.28)))
            phase = 2 * np.pi * np.cumsum(lfo) / SAMPLE_RATE
            if wtype == "sine":
                return amp * np.sin(phase)
            if wtype == "saw":
                return amp * (2 * (phase / (2 * np.pi) % 1) - 1) * 0.5
            if wtype == "tri":
                return amp * (2 * np.abs(2 * (phase / (2 * np.pi) % 1) - 1) - 1) * 0.7
            return amp * np.sin(phase)

        # Pad / sub-bass (cinematic low end)
        for f, amp in [(rng.choice([41.2, 43.7, 55.0]), 0.12),
                       (rng.choice([65.4, 69.3, 73.4]), 0.07)]:
            out += _osc(f, amp, "sine")

        # Eerie mid tones — minor 2nds & tritones for tension
        for f, wt in [(rng.choice([185.0, 196.0, 207.7]), "sine"),
                      (rng.choice([277.2, 293.7, 311.1]), "tri"),
                      (rng.choice([369.9, 392.0, 415.3]), "saw")]:
            out += _osc(f, rng.uniform(0.025, 0.04), wt)

        # High shimmer (very quiet)
        out += _osc(rng.choice([880.0, 987.8, 1046.5]), 0.010, "sine")

        # Comb reverb (Schroeder: prime delays)
        delays_ms = [rng.integers(180, 380) for _ in range(5)]
        fb        = rng.uniform(0.32, 0.48)
        wet = out.copy()
        for d in delays_ms:
            d_samp = int(d * SAMPLE_RATE / 1000)
            buf = np.zeros(n)
            buf[d_samp:] = out[:-d_samp]
            wet += fb * buf
        out = 0.5 * out + 0.5 * wet

        # Amplitude breathing
        breath_hz = rng.uniform(0.05, 0.09)
        am_depth  = rng.uniform(0.15, 0.25)
        am        = 1 - am_depth + am_depth * np.sin(
            2 * np.pi * breath_hz * t + rng.uniform(0, 6.28))
        out *= am

        # Fade in / out
        fade_n = int(3 * SAMPLE_RATE)
        out[:fade_n]  *= np.linspace(0, 1, fade_n)
        out[-fade_n:] *= np.linspace(1, 0, fade_n)

        peak = np.max(np.abs(out))
        if peak > 1e-6:
            out = out / peak * 0.22
        return out.astype(np.float32)

    generated = 0
    for i in range(1, n_tracks + 1):
        path = os.path.join(MUSIC_DIR, f"drone_{i:02d}.wav")
        if os.path.exists(path) and not force:
            print(f"[Setup] Already exists: drone_{i:02d}.wav — skip")
            continue
        print(f"[Setup] Generating drone_{i:02d}.wav  (seed={i * 7})...")
        sf.write(path, _make_drone(seed=i * 7), SAMPLE_RATE)
        kb = os.path.getsize(path) // 1024
        print(f"[Setup]   ✓ drone_{i:02d}.wav  ({kb} KB)")
        generated += 1

    total = len(list(Path(MUSIC_DIR).glob("drone_*.wav")))
    print(f"\n[Setup] Done — {total} procedural drone tracks ready in music/")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Download music tracks + caption font")
    p.add_argument("--force",    action="store_true", help="Re-download even if files exist")
    p.add_argument("--fallback", action="store_true", help="Generate procedural drones instead of downloading")
    args = p.parse_args()

    print("=" * 60)
    print("  @buriedcasefiles — Music + Font Setup")
    print("=" * 60)

    # Always try to download / verify the font
    download_font(force=args.force)

    if args.fallback:
        print("\n[Setup] Fallback mode — generating procedural drones...")
        generate_fallback_drones(force=args.force)
    else:
        print("\n[Setup] Downloading music tracks...")
        n = download_tracks(force=args.force)
        if n == 0:
            print("\n[Setup] All downloads failed. Generating procedural fallback drones...")
            generate_fallback_drones(force=True)

    print("\n[Setup] Complete.")
