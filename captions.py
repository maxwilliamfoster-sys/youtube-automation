# vendored from growth-lab/captions.py @ 016fdd6
"""captions: word-timed, spring-pop burned-in captions (growth-lab S4, master copy).

Edit it HERE, then roll it out with sync_vendored.py. Needs Pillow; ffmpeg only
for burning the overlay track in.

Inputs: word timings [(word, start_s, end_s), ...] from
  * edge-tts WordBoundary events  -> from_edge_tts(events)
    (pass Communicate(text, voice, boundary="WordBoundary"): edge-tts 7.x
    defaults to SentenceBoundary and then sends no word events at all)
  * Kokoro token timestamps        -> from_kokoro(tokens)
  * no timestamps (Chatterbox)     -> estimate_word_timings(text, audio_duration)
    APPROXIMATE: spreads the audio over the words in proportion to syllables,
    with pauses at punctuation. Every Word it returns has approx=True, so a
    caller can log or gate on it. Good enough for 2-3 word chunks at normal
    pace; not for karaoke-precise highlighting.

Pipeline:
    words  = captions.from_edge_tts(events)            # or estimate_word_timings(...)
    chunks = captions.chunk_words(words)               # 2-3 words, <= 20 chars, clause breaks
    img    = captions.render_frame(chunks, t)          # RGBA frame for a PIL renderer
    track  = captions.write_overlay_track(chunks, "work/caps", fps=30, duration=dur)
    # ffmpeg -i bg.mp4 -f concat -safe 0 -i track.ffconcat \
    #   -filter_complex "[0:v][1:v]overlay=0:0:format=auto:eof_action=pass" ...

The pop is a damped spring: scale = 1 - e^(-6p) * cos(2.5*pi*p), p = progress
through the first `pop` seconds (0.18 s), clamped to [0.05, 1.35].

Ported logic, both under the MIT licence:
  * harry0703/MoneyPrinterTurbo, app/services/video.py::_get_subtitle_spring_scale
    (the spring formula and its clamps)
        Copyright (c) 2024 Harry
  * gyoridavid/short-video-maker, src/components/utils.ts::createCaptionPages
    (paging by line length, line count and time gap) -> caption_pages()
        Copyright (c) 2025 David Gyori

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.
"""
import functools
import math
import os
import re
import shutil
from collections import namedtuple

Word = namedtuple("Word", "text start end approx", defaults=(False,))
Chunk = namedtuple("Chunk", "text start end words")

POP_SECONDS = 0.18          # MoneyPrinterTurbo _SUBTITLE_SPRING_DURATION_SECONDS
MIN_SCALE, MAX_SCALE = 0.05, 1.35
MAX_WORDS, MAX_CHARS, MAX_GAP = 3, 20, 0.5

_CLAUSE_END = re.compile(r"[,;:.!?\u2014\u2013\u2026]['\")\]\u2019\u201d]*$")
_SENTENCE_END = re.compile(r"[.!?\u2026]['\")\]\u2019\u201d]*$")
_PUNCT_ONLY = re.compile(r"^[^\w]+$", re.U)
# Words a chunk should not END on when the break is only for length.
WEAK = {"a", "an", "the", "of", "to", "and", "or", "but", "in", "on", "at", "by", "for",
        "from", "with", "his", "her", "their", "my", "your", "our", "its", "this", "that"}


# ------------------------------------------------------------------ spring --

def spring_scale(t, duration=POP_SECONDS):
    """Scale of the pop animation `t` seconds after a chunk appears."""
    if duration <= 0 or t >= duration:
        return 1.0
    p = max(0.0, min(t / duration, 1.0))
    s = 1.0 - math.exp(-6.0 * p) * math.cos(2.5 * math.pi * p)
    return max(MIN_SCALE, min(s, MAX_SCALE))


# ------------------------------------------------------------------ inputs --

def as_words(items):
    """[(text, start, end)] / Word / dicts with text,start,end -> [Word]."""
    out = []
    for it in items:
        if isinstance(it, Word):
            out.append(it)
        elif isinstance(it, dict):
            out.append(Word(str(it["text"]), float(it["start"]), float(it["end"]),
                            bool(it.get("approx", False))))
        else:
            out.append(Word(str(it[0]), float(it[1]), float(it[2]),
                            bool(it[3]) if len(it) > 3 else False))
    return out


def from_edge_tts(events):
    """edge-tts stream chunks -> [Word]. offset/duration are 100 ns ticks."""
    out = []
    for e in events:
        if e.get("type") != "WordBoundary":
            continue
        s = e["offset"] / 1e7
        out.append(Word(str(e["text"]), round(s, 4), round((e["offset"] + e["duration"]) / 1e7, 4)))
    return out


def from_kokoro(tokens):
    """Kokoro (misaki MToken-like: text, start_ts, end_ts, whitespace) -> [Word].
    Punctuation tokens join the word before them; a token with no whitespace
    after it joins the next one into the same word."""
    out = []
    glue = False
    for tok in tokens:
        text = str(getattr(tok, "text", "") or "")
        if not text.strip():
            continue
        s, e = getattr(tok, "start_ts", None), getattr(tok, "end_ts", None)
        if out and (_PUNCT_ONLY.match(text) or glue):
            last = out[-1]
            out[-1] = last._replace(text=last.text + text,
                                    end=float(e) if e is not None else last.end)
        elif s is not None and e is not None:
            out.append(Word(text, float(s), float(e)))
        glue = getattr(tok, "whitespace", " ") == "" and bool(out)
    return out


def syllables(word):
    """Rough spoken-syllable count (numbers read aloud are long: 1983 -> 6)."""
    w = word.lower()
    digits = re.sub(r"\D", "", w)
    letters = re.sub(r"[^a-z]", "", w)
    n = 0
    if digits:
        n += max(1, round(1.5 * len(digits)))
    if letters:
        groups = re.findall(r"[aeiouy]+", letters)
        k = len(groups)
        if letters.endswith("e") and k > 1 and not letters.endswith(("le", "ee")):
            k -= 1
        n += max(1, k)
    return max(1, n)


def estimate_word_timings(text, duration, start=0.0, comma_pause=0.75, sentence_pause=1.5):
    """APPROXIMATE word timings for audio without timestamps (e.g. Chatterbox).
    Splits `duration` seconds across the words of `text` in proportion to their
    syllables, plus pauses (in syllable units) after commas and sentence ends.
    Every Word has approx=True."""
    toks = text.split()
    if not toks or duration <= 0:
        return []
    weights = [syllables(t) for t in toks]
    pauses = []
    for i, t in enumerate(toks):
        if i == len(toks) - 1:
            pauses.append(0.0)
        elif _SENTENCE_END.search(t):
            pauses.append(sentence_pause)
        elif _CLAUSE_END.search(t):
            pauses.append(comma_pause)
        else:
            pauses.append(0.0)
    unit = duration / (sum(weights) + sum(pauses))
    out, t = [], float(start)
    for tok, w, p in zip(toks, weights, pauses):
        end = t + w * unit
        out.append(Word(tok, round(t, 4), round(end, 4), True))
        t = end + p * unit
    out[-1] = out[-1]._replace(end=round(float(start) + duration, 4))
    return out


# ---------------------------------------------------------------- chunking --

def _mk(ws):
    return Chunk(" ".join(w.text for w in ws), ws[0].start, ws[-1].end, list(ws))


def chunk_words(words, max_words=MAX_WORDS, max_chars=MAX_CHARS, max_gap=MAX_GAP):
    """Caption chunks of up to `max_words` words and `max_chars` characters.
    A chunk ends after clause punctuation or before a pause longer than
    `max_gap` s. When a chunk ends only because it is full, a trailing weak word
    ("the", "of"...) moves on to the next chunk."""
    ws = as_words(words)
    chunks, cur = [], []
    for w in ws:
        if cur:
            joined = len(" ".join(x.text for x in cur)) + 1 + len(w.text)
            natural = bool(_CLAUSE_END.search(cur[-1].text)) or (w.start - cur[-1].end) > max_gap
            full = len(cur) >= max_words or joined > max_chars
            if natural or full:
                carry = []
                if (not natural and len(cur) >= 2 and cur[-1].text.lower() in WEAK
                        and len(cur[-1].text) + 1 + len(w.text) <= max_chars):
                    carry = [cur.pop()]
                chunks.append(_mk(cur))
                cur = carry
        cur.append(w)
    if cur:
        chunks.append(_mk(cur))
    return chunks


def caption_pages(words, line_max_length=MAX_CHARS, line_count=1, max_gap=MAX_GAP):
    """Port of short-video-maker createCaptionPages (times in seconds).
    Returns [{"start", "end", "lines": [[Word, ...], ...]}, ...]."""
    ws = as_words(words)
    pages = []
    page = {"start": 0.0, "end": 0.0, "lines": []}
    line = []
    for i, w in enumerate(ws):
        if i > 0 and w.start - page["end"] > max_gap:      # time gap: new page
            if line:
                page["lines"].append(line)
            if page["lines"]:
                pages.append(page)
            page = {"start": w.start, "end": w.end, "lines": []}
            line = []
        text = " ".join(x.text for x in line)
        if line and len(text) + 1 + len(w.text) > line_max_length:  # line full
            page["lines"].append(line)
            line = []
            if len(page["lines"]) >= line_count:                     # page full
                pages.append(page)
                page = {"start": w.start, "end": w.end, "lines": []}
        line.append(w)
        page["end"] = w.end
        if i == 0 or page["start"] == 0:
            page["start"] = w.start
        else:
            page["start"] = min(page["start"], w.start)
    if line:
        page["lines"].append(line)
    if page["lines"]:
        pages.append(page)
    return pages


# --------------------------------------------------------------- rendering --

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/Library/Fonts/Arial Bold.ttf",
)


def default_font_path():
    env = os.environ.get("CAPTION_FONT")
    if env and os.path.exists(env):
        return env
    return next((p for p in FONT_CANDIDATES if os.path.exists(p)), None)


@functools.lru_cache(maxsize=256)
def _font(path, size):
    from PIL import ImageFont
    size = max(1, int(size))
    if path:
        return ImageFont.truetype(path, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _active(chunks, t):
    for c in chunks:
        if c.start <= t < c.end:
            return c
    return None


def _fit_size(chunk, path, base, max_w, upper, stroke_frac):
    size = base
    while size > 8:
        f = _font(path, size)
        text = chunk.text.upper() if upper else chunk.text
        if f.getlength(text) + 2 * size * stroke_frac <= max_w:
            return size
        size = int(size * 0.92)
    return size


def render_chunk(chunk, t, size=(1080, 1920), font_path=None, font_size=None, y=None,
                 fill=(255, 255, 255, 255), stroke=(0, 0, 0, 255), stroke_frac=0.09,
                 highlight=(255, 214, 0, 255), uppercase=False, pop=POP_SECONDS, max_width=0.88):
    """One RGBA canvas with `chunk` drawn at time `t` (spring-scaled, active word
    highlighted). Centred horizontally; `y` is the text's vertical centre."""
    from PIL import Image, ImageDraw
    w_px, h_px = size
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    path = font_path or default_font_path()
    base = _fit_size(chunk, path, font_size or int(w_px * 0.085), w_px * max_width,
                     uppercase, stroke_frac)
    scale = spring_scale(t - chunk.start, pop)
    fs = max(1, int(round(base * scale)))
    font = _font(path, fs)
    sw = max(0, int(round(fs * stroke_frac)))
    words = [(w.text.upper() if uppercase else w.text) for w in chunk.words]
    space = font.getlength(" ")
    widths = [font.getlength(x) for x in words]
    total = sum(widths) + space * (len(words) - 1)
    x = (w_px - total) / 2
    cy = y if y is not None else int(h_px * 0.62)
    draw = ImageDraw.Draw(img)
    active = None
    if highlight:
        for i, w in enumerate(chunk.words):
            if w.start <= t:
                active = i
    for i, (text, wd) in enumerate(zip(words, widths)):
        colour = highlight if (highlight and i == active) else fill
        draw.text((x, cy), text, font=font, fill=colour, anchor="lm",
                  stroke_width=sw, stroke_fill=stroke)
        x += wd + space
    return img


def render_frame(chunks, t, size=(1080, 1920), **style):
    """RGBA frame for time `t`: the active chunk, or fully transparent."""
    c = _active(chunks, t)
    if c is None:
        from PIL import Image
        return Image.new("RGBA", size, (0, 0, 0, 0))
    return render_chunk(c, t, size=size, **style)


def write_overlays(chunks, out_dir, size=(1080, 1920), **style):
    """One settled (scale 1) PNG per chunk, for ffmpeg overlay with
    enable='between(t,start,end)'. Returns [{"path","start","end","text"}]."""
    os.makedirs(out_dir, exist_ok=True)
    out = []
    for i, c in enumerate(chunks):
        p = os.path.join(out_dir, f"cap_{i:04d}.png")
        render_chunk(c, c.start + POP_SECONDS, size=size, highlight=None,
                     **{k: v for k, v in style.items() if k != "highlight"}).save(p)
        out.append({"path": p, "start": c.start, "end": c.end, "text": c.text})
    return out


def write_overlay_track(chunks, out_dir, size=(1080, 1920), fps=30, duration=None,
                        pop=POP_SECONDS, **style):
    """Whole caption timeline as PNGs + an ffconcat list (one ffmpeg input):
    blank gaps, `pop` seconds of per-frame spring frames, then one still per
    highlighted word. Returns the .ffconcat path."""
    from PIL import Image
    os.makedirs(out_dir, exist_ok=True)
    blank = os.path.join(out_dir, "blank.png")
    Image.new("RGBA", size, (0, 0, 0, 0)).save(blank)
    end_all = max([c.end for c in chunks] + [0.0])
    duration = float(duration if duration is not None else end_all)
    entries = []          # (path, start_time)
    t = 0.0
    for i, c in enumerate(chunks):
        c_end = min(c.end, chunks[i + 1].start if i + 1 < len(chunks) else c.end, duration)
        if c.start >= duration or c_end <= c.start:
            continue
        if c.start > t:
            entries.append((blank, t))
        times = [c.start + k / fps for k in range(int(math.ceil(pop * fps)))
                 if c.start + k / fps < c_end]
        settled = c.start + len(times) / fps
        times += [s for s in sorted({w.start for w in c.words}) if settled < s < c_end]
        if settled < c_end:
            times.append(settled)
        for k, ts in enumerate(sorted(set(times))):
            p = os.path.join(out_dir, f"cap_{i:04d}_{k:03d}.png")
            render_chunk(c, ts, size=size, pop=pop, **style).save(p)
            entries.append((p, ts))
        t = c_end
    if t < duration:
        entries.append((blank, t))
    lines = ["ffconcat version 1.0"]
    for k, (p, ts) in enumerate(entries):
        nxt = entries[k + 1][1] if k + 1 < len(entries) else duration
        lines.append(f"file '{os.path.abspath(p).replace(os.sep, '/')}'")
        lines.append(f"duration {max(nxt - ts, 0.0):.6f}")
    if entries:  # the concat demuxer ignores the last duration unless the file repeats
        lines.append(f"file '{os.path.abspath(entries[-1][0]).replace(os.sep, '/')}'")
    path = os.path.join(out_dir, "captions.ffconcat")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def _ffmpeg():
    env = os.environ.get("FFMPEG")
    if env:
        return env
    win = "C:/ffmpeg/ffmpeg-8.1.1-essentials_build/bin/ffmpeg.exe"
    return shutil.which("ffmpeg") or (win if os.path.exists(win) else None)
