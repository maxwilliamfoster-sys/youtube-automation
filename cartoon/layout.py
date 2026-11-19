"""Where burned-in text sits on screen, and over which seconds.

Mirrors video_composer.build_filter_script (same constants, same wrapping), so the
engine can keep faces and pop-ups out of these areas and cartoon.qc can check that
it did. Boxes are (x0, y0, x1, y1) in 1080x1920 screen pixels; every text area spans
the full width (the text is centred and its width changes word by word).
"""
W, H = 1080, 1920
PAD = 14            # stroke + shadow around the drawn letters


def caption_band():
    """The word captions: one line of CAPTION_FONT_SIZE type at CAPTION_POSITION."""
    from config import CAPTION_FONT_SIZE, CAPTION_POSITION
    y = int(H * CAPTION_POSITION)
    return (0, y - PAD, W, y + CAPTION_FONT_SIZE + PAD)


def text_zones(duration, hook_text="", cta_text="", case_tag="", captions=True):
    """List of {"name", "t0", "t1", "box"} for every text overlay the composer adds."""
    from video_composer import _wrap_ffmpeg_text, escape_ffmpeg_text
    zones = []
    if captions:
        zones.append({"name": "captions", "t0": 0.0, "t1": float(duration) + 1.0, "box": caption_band()})
    if hook_text:
        hook = _wrap_ffmpeg_text(escape_ffmpeg_text(hook_text.upper()), 18)
        size, step = 76, 92
        if hook.count("\n") >= 4:
            hook = _wrap_ffmpeg_text(escape_ffmpeg_text(hook_text.upper()), 21)
            size, step = 64, 78
        n = len([ln for ln in hook.split("\n") if ln.strip()])
        top = int(H * 0.13)
        zones.append({"name": "hook card", "t0": 0.0, "t1": 1.8,
                      "box": (0, top - PAD, W, top + (n - 1) * step + size + PAD)})
    if case_tag:
        top = int(H * 0.10)
        zones.append({"name": "case tag", "t0": 0.0, "t1": 3.0, "box": (0, top - PAD, W, top + 44 + PAD)})
    if cta_text and duration:
        cta = _wrap_ffmpeg_text(escape_ffmpeg_text(cta_text.upper()), 14)
        n = len([ln for ln in cta.split("\n") if ln.strip()])
        top = int(H * 0.62)
        zones.append({"name": "end card", "t0": max(0.0, float(duration) - 2.6), "t1": float(duration),
                      "box": (0, top - PAD, W, top + (n - 1) * 86 + 72 + PAD)})
    return zones


def active(zones, t0, t1=None):
    """Zones shown at any moment of [t0, t1] (or at the instant t0)."""
    t1 = t0 if t1 is None else t1
    return [z for z in zones if z["t0"] <= t1 and z["t1"] >= t0]


def overlap(a, b):
    """Intersection area of two boxes."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def area(a):
    return max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
