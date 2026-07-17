"""
Image Generator — creates atmospheric AI images for documentary-style true crime videos.
Uses Pollinations AI (completely FREE, no API key required for basic use).

TIP: Register free at https://auth.pollinations.ai to suppress watermarks.
     After registering, set POLLINATIONS_TOKEN in your .env file.
"""

import os
import time
import urllib.parse
import subprocess
import shutil
import requests
from pathlib import Path
from groq import Groq
from config import (GROQ_API_KEY, GROQ_MODEL, POLLINATIONS_DELAY, POLLINATIONS_MODEL,
                    SCENE_IMAGES_DIR, PEXELS_API_KEY, USE_VIDEO_BROLL)

IMAGE_BASE_URL = "https://image.pollinations.ai/prompt/"

_FFMPEG = shutil.which("ffmpeg") or r"C:\ffmpeg\ffmpeg-8.1.1-essentials_build\bin\ffmpeg.exe"


# ─── Script segmentation ─────────────────────────────────────────────────────

def _even_chunks(items: list, n: int) -> list:
    """Split `items` into n groups as evenly as possible. The remainder is spread
    across the FIRST `r` groups — never dumped on the last one (which would make the
    final image linger for many seconds and tank retention)."""
    k, r = divmod(len(items), n)
    chunks, idx = [], 0
    for i in range(n):
        take = k + (1 if i < r else 0)
        chunks.append(items[idx:idx + take])
        idx += take
    return chunks


def split_story_segments(text: str, n: int) -> list:
    """
    Split a script into n segments so each image can match what's being narrated.
    Prefers natural line breaks; falls back to equal word-count splits. Both paths
    distribute evenly so no single scene absorbs a disproportionate slice of time.
    """
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if len(lines) >= n:
        return [" ".join(group) for group in _even_chunks(lines, n)]
    # fallback: even word-count split
    words = text.split()
    return [" ".join(group) for group in _even_chunks(words, n)]


# ─── Prompt generation ────────────────────────────────────────────────────────

def generate_image_prompts(
    story_title: str,
    story_text: str,
    num_images: int = 5,
    segments: list = None,
) -> list:
    """
    Use Groq/Llama to generate cinematic atmospheric image prompts.
    When `segments` is provided each prompt is tailored to that specific
    part of the narration so images match what is being said.
    """
    client = Groq(api_key=GROQ_API_KEY)

    if segments:
        seg_lines = "\n".join(
            f"Scene {i+1}: {seg[:250]}" for i, seg in enumerate(segments)
        )
        user_content = (
            f"Story title: {story_title}\n\n"
            f"Generate one image prompt per scene segment below "
            f"(the image must match WHAT IS BEING NARRATED in that segment):\n{seg_lines}"
        )
    else:
        user_content = f"Story title: {story_title}\n\nStory excerpt: {story_text[:600]}"

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        max_tokens=800,
        messages=[
            {
                "role": "system",
                "content": (
                    f"You are a cinematographer creating visual scene descriptions for a true crime documentary. "
                    f"Generate exactly {num_images} image prompts for AI image generation. "
                    "Each prompt describes a dark, cinematic, photorealistic still frame. "
                    "Rules: "
                    "- No text overlay, no faces, no gore "
                    "- Strong atmospheric lighting: single streetlamp, moonlight, desk lamp, candlelight "
                    "- Style: cinematic noir, photorealistic, ultra realistic, desaturated cold tones "
                    "- Vary the shot: wide establishing shot, close-up detail, interior, exterior "
                    "- Each prompt on its own line, NO numbering or bullet points "
                    "- End each with: cinematic noir, photorealistic, ultra realistic, 4k, no text, no people"
                ),
            },
            {"role": "user", "content": user_content},
        ],
    )

    raw = response.choices[0].message.content.strip()
    prompts = [line.strip() for line in raw.split("\n") if line.strip() and not line.strip().startswith("-")]
    return prompts[:num_images]


# ─── Pexels stock photo search ───────────────────────────────────────────────

def _generate_pexels_queries(story_title: str, segments: list) -> list:
    """Use Groq to turn story segments into short Pexels search queries."""
    client = Groq(api_key=GROQ_API_KEY)
    seg_lines = "\n".join(f"Scene {i+1}: {seg[:200]}" for i, seg in enumerate(segments))
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        max_tokens=200,
        messages=[
            {
                "role": "system",
                "content": (
                    "Generate ONE short Pexels stock photo search query per scene. "
                    "Dark, atmospheric, cinematic. 2-5 words each. "
                    "No names of people, no gore. One query per line, no numbering."
                ),
            },
            {
                "role": "user",
                "content": f"True crime story: {story_title}\n\nScenes:\n{seg_lines}",
            },
        ],
    )
    raw = response.choices[0].message.content.strip()
    queries = [
        line.strip().lstrip("0123456789.-) ")
        for line in raw.split("\n")
        if line.strip()
    ]
    return queries


def fetch_pexels_video(
    query: str,
    output_path: str,
    api_key: str = None,
    seen_ids: set = None,
    min_duration: float = 3.0,
) -> bool:
    """
    Download a portrait Pexels *video* clip matching `query`.

    Real footage moves on its own. A panned still cannot look like anything but a
    panned still, which is what made the videos read as a slideshow. Pexels returns
    plenty of usable portrait clips for the atmospheric queries this pipeline builds,
    so b-roll is the default and stills are the fallback.

    Returns True on success. Callers must fall back to a still if this returns False.
    """
    key = api_key or PEXELS_API_KEY
    if not key:
        return False

    headers = {"Authorization": key}
    params = {"query": query, "orientation": "portrait", "size": "medium", "per_page": 15}

    try:
        resp = requests.get(
            "https://api.pexels.com/videos/search",
            headers=headers, params=params, timeout=25,
        )
        if resp.status_code != 200:
            print(f"[Video] Pexels HTTP {resp.status_code} for '{query}'")
            return False

        videos = [
            v for v in resp.json().get("videos", [])
            if v.get("width", 0) < v.get("height", 1)
            and v.get("duration", 0) >= min_duration
        ]
        if not videos:
            return False

        # Same repeat problem as the stills: scene queries rhyme, so without this the
        # same clip lands in several scenes of one video.
        if seen_ids is not None:
            unused = [v for v in videos if v.get("id") not in seen_ids]
            if unused:
                videos = unused

        video = min(videos, key=lambda v: abs((v["width"] / v["height"]) - (9 / 16)))

        # Pick the rendition nearest 1080 wide — smaller needs upscaling, much larger
        # is a slow download we would only crop away.
        files = [
            f for f in video.get("video_files", [])
            if f.get("width") and f.get("height") and f["width"] < f["height"]
        ]
        if not files:
            return False
        best = min(files, key=lambda f: abs(f["width"] - 1080))

        clip = requests.get(best["link"], timeout=120)
        if clip.status_code != 200:
            return False
        with open(output_path, "wb") as fh:
            fh.write(clip.content)

        if seen_ids is not None:
            seen_ids.add(video.get("id"))
        print(f"[Video] Pexels clip: '{query}' ({best['width']}x{best['height']}, "
              f"{video.get('duration')}s)")
        return True
    except Exception as e:
        print(f"[Video] Pexels video failed for '{query}': {e}")
        return False


def fetch_pexels_image(
    query: str,
    output_path: str,
    api_key: str = None,
    seen_ids: set = None,
) -> bool:
    """
    Search Pexels for a portrait stock photo matching `query` and download it.
    Returns True on success, False if no results or API key missing.

    `seen_ids` carries the photo IDs already used in this video. Scene queries are all
    variations on "dark atmospheric ...", so without it Pexels returns the same top
    photo for several scenes and the same picture appears repeatedly in one video.
    """
    key = api_key or PEXELS_API_KEY
    if not key:
        return False

    headers = {"Authorization": key}
    # 15 candidates rather than 5: enough alternatives to skip already-used photos.
    params  = {"query": query, "orientation": "portrait", "size": "large", "per_page": 15}

    try:
        resp = requests.get(
            "https://api.pexels.com/v1/search",
            headers=headers, params=params, timeout=20,
        )
        if resp.status_code != 200:
            print(f"[Images] Pexels HTTP {resp.status_code} for '{query}'")
            return False

        photos = resp.json().get("photos", [])
        if not photos:
            print(f"[Images] Pexels: no results for '{query}'")
            return False

        # Prefer photos not already used in this video; fall back to the full list
        # rather than failing the scene outright if every candidate is a repeat.
        if seen_ids is not None:
            unused = [p for p in photos if p.get("id") not in seen_ids]
            if unused:
                photos = unused
            else:
                print(f"[Images] Pexels: all results for '{query}' already used — reusing.")

        # Pick the photo closest to portrait 9:16 aspect ratio
        def portrait_score(p):
            w, h = p["width"], p["height"]
            return abs((w / h) - (9 / 16))

        photo = min(photos, key=portrait_score)
        if seen_ids is not None:
            seen_ids.add(photo.get("id"))
        img_url = photo["src"].get("large2x") or photo["src"]["original"]

        img_resp = requests.get(img_url, timeout=60)
        if img_resp.status_code == 200:
            Path(output_path).write_bytes(img_resp.content)
            size_kb = len(img_resp.content) // 1024
            print(f"[Images] Pexels: {os.path.basename(output_path)} ({size_kb}KB) — query: '{query}'")
            return True

    except Exception as e:
        print(f"[Images] Pexels error: {e}")

    return False


# ─── Pollinations fetch ───────────────────────────────────────────────────────

def fetch_image(
    prompt: str,
    output_path: str,
    width: int = 1080,
    height: int = 1920,
    seed: int = None,
    max_retries: int = 3,
) -> bool:
    """Fetch a single generated image from Pollinations AI and save it to disk."""
    import os as _os
    token = _os.environ.get("POLLINATIONS_TOKEN", "")

    encoded = urllib.parse.quote(prompt)
    url = f"{IMAGE_BASE_URL}{encoded}"

    params = {
        "width": width,
        "height": height,
        "model": POLLINATIONS_MODEL,
        "safe": "false",
        "enhance": "false",
    }
    if token:
        params["nologo"] = "true"
    if seed is not None:
        params["seed"] = seed

    headers = {"User-Agent": "TrueCrimeAutomation/1.0"}

    for attempt in range(max_retries):
        try:
            print(f"[Images] Fetching: {prompt[:70]}...")
            resp = requests.get(url, params=params, headers=headers, timeout=120, stream=True)

            if resp.status_code == 200:
                content_type = resp.headers.get("content-type", "")
                if "image" not in content_type:
                    print(f"[Images] Unexpected content-type: {content_type}")
                    time.sleep(5)
                    continue
                Path(output_path).write_bytes(resp.content)
                size_kb = len(resp.content) // 1024
                print(f"[Images] Saved: {os.path.basename(output_path)} ({size_kb} KB)")
                return True

            elif resp.status_code == 429:
                wait = 20 * (attempt + 1)
                print(f"[Images] Rate limited — waiting {wait}s...")
                time.sleep(wait)
            else:
                print(f"[Images] HTTP {resp.status_code} (attempt {attempt + 1})")
                time.sleep(5)

        except requests.Timeout:
            print(f"[Images] Timeout (attempt {attempt + 1})")
            time.sleep(15)
        except Exception as e:
            print(f"[Images] Error: {e}")
            time.sleep(5)

    return False


def _create_dark_fallback(output_path: str):
    """Create a dark cinematic fallback frame when Pollinations is unavailable."""
    subprocess.run(
        [_FFMPEG, "-y",
         "-f", "lavfi", "-i", "color=c=0x0a0a0a:size=1080x1920:rate=1",
         "-vframes", "1", output_path],
        capture_output=True,
    )
    print(f"[Images] Created dark fallback frame: {os.path.basename(output_path)}")


# ─── Main entry point ─────────────────────────────────────────────────────────

def generate_story_images(
    story_title: str,
    story_text: str,
    output_dir: str,
    num_images: int = 5,
    delay: float = None,
) -> tuple:
    """
    Generate all atmospheric scene images for a story.

    The script is split into `num_images` segments so each image prompt
    reflects what is being narrated at that moment (word-sync).

    Returns:
        (image_paths, word_segments) — word_segments can be passed directly
        to compose_documentary() as `word_segments` for timed scene display.
    """
    if delay is None:
        delay = POLLINATIONS_DELAY

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Split script into per-scene segments for word-timed image display
    word_segments = split_story_segments(story_text, num_images)

    use_pexels = bool(PEXELS_API_KEY)
    if use_pexels:
        print(f"\n[Images] Pexels mode — fetching {num_images} real stock photos...")
        pexels_queries = _generate_pexels_queries(story_title, word_segments)
        print(f"[Images] Search queries: {pexels_queries}")
    else:
        print(f"\n[Images] Pollinations mode — generating {num_images} AI images...")
        print("[Images] TIP: Set PEXELS_API_KEY in .env for real stock photos (pexels.com/api)")
        prompts = generate_image_prompts(story_title, story_text, num_images, segments=word_segments)

    # Generic Pollinations fallback prompts
    generic_fallbacks = [
        "dark empty alley at night, single flickering streetlamp, wet pavement, fog, cinematic noir, 4k, no people",
        "old detective corkboard red string dim desk lamp dark room, cinematic noir, 4k, no people",
        "abandoned building moonlight broken windows crime scene, cinematic noir, 4k, no people",
        "weathered case file documents dark desk dramatic lighting, cinematic noir, 4k",
        "foggy empty street midnight distant streetlamp, cinematic noir, 4k, no people",
    ]
    if not use_pexels:
        while len(prompts) < num_images:
            prompts.append(generic_fallbacks[len(prompts) % len(generic_fallbacks)])

    saved_paths = []
    seen_photo_ids: set = set()   # so no photo repeats within one video
    seen_video_ids: set = set()
    broll_count = 0
    for i in range(num_images):
        output_path = os.path.join(output_dir, f"scene_{i + 1:02d}.jpg")
        print(f"\n[Images] Scene {i + 1}/{num_images}...")
        success = False

        if use_pexels:
            query = pexels_queries[i] if i < len(pexels_queries) else f"dark atmospheric {story_title}"

            # Prefer real footage; fall back to a still for this scene if none exists.
            # The composer decides what to do with each by file extension, so a mixed
            # list of .mp4 and .jpg is expected and fine.
            if USE_VIDEO_BROLL:
                video_path = os.path.join(output_dir, f"scene_{i + 1:02d}.mp4")
                if fetch_pexels_video(query, video_path, seen_ids=seen_video_ids):
                    saved_paths.append(video_path)
                    broll_count += 1
                    continue

            success = fetch_pexels_image(query, output_path, seen_ids=seen_photo_ids)
            if not success:
                # Pexels failed — fall back to Pollinations for this scene
                print(f"[Images] Pexels failed, falling back to Pollinations...")
                fallback_prompt = generic_fallbacks[i % len(generic_fallbacks)]
                success = fetch_image(fallback_prompt, output_path, seed=i * 37 + 100)
                if i < num_images - 1:
                    time.sleep(delay)
        else:
            success = fetch_image(prompts[i], output_path, seed=i * 37 + 100)
            if i < num_images - 1:
                print(f"[Images] Waiting {delay}s (rate limit)...")
                time.sleep(delay)

        if not success:
            print(f"[Images] All sources failed — using dark fallback")
            _create_dark_fallback(output_path)

        saved_paths.append(output_path)

    print(f"\n[Images] Done: {len(saved_paths)} scene images")
    return saved_paths, word_segments


if __name__ == "__main__":
    # Quick test
    paths = generate_story_images(
        story_title="The Somerton Man",
        story_text="A dead man found on a beach with no identity, a hidden pocket containing Persian words meaning 'it is finished', and an undeciphered code.",
        output_dir="./test_images",
        num_images=3,
    )
    print(f"\nTest complete: {paths}")
