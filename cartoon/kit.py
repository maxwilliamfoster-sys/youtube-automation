"""Cartoon drawing kit (pycairo): helpers, characters, pop-up icons, stamps.

Style: flat round white heads with dot eyes and thick dark outlines, bean
bodies and stick legs, warm key light, cool purple-blue shadows (never black).
Everything is plain vector drawing, so it renders the same on a GitHub runner.
"""
import math
import os

import cairo

W, H = 1080, 1920
INK = "#1d1a2b"

# Fonts: Windows names locally; on the Linux runner the workflow installs
# assets/MontserratBold.ttf and DejaVu (fonts-dejavu-core) for fontconfig.
if os.name == "nt":
    FONT_BOLD, FONT_UI, FONT_SERIF = "Arial Black", "Segoe UI", "Times New Roman"
else:
    FONT_BOLD, FONT_UI, FONT_SERIF = "Montserrat", "DejaVu Sans", "DejaVu Serif"


# ---------- maths ----------
def clamp(x, a=0.0, b=1.0):
    return a if x < a else b if x > b else x


def smooth(x):
    x = clamp(x)
    return x * x * (3 - 2 * x)


def eio(x):
    x = clamp(x)
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def back(x):
    x = clamp(x)
    c1 = 1.9
    c3 = c1 + 1
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


def p(t, t0, d=0.35, f=smooth):
    return f((t - t0) / d) if d > 0 else float(t >= t0)


def lerp(a, b, k):
    return a + (b - a) * k


def hexc(h, a=1.0):
    h = h.lstrip("#")
    return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255, a)


def mix(h1, h2, k):
    a, b = hexc(h1), hexc(h2)
    return "#%02x%02x%02x" % tuple(int(round(255 * lerp(a[i], b[i], k))) for i in range(3))


class SafeCtx(cairo.Context):
    """Pop-in animations start at scale 0, which cairo cannot invert."""

    def scale(self, sx, sy):
        def fix(v):
            return v if abs(v) > 1e-3 else (1e-3 if v >= 0 else -1e-3)
        super().scale(fix(sx), fix(sy))


# ---------- drawing helpers ----------
def src(ctx, h, a=1.0):
    ctx.set_source_rgba(*hexc(h, a))


def stroke(ctx, w=6, c=INK, a=1.0):
    src(ctx, c, a)
    ctx.set_line_width(w)
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    ctx.set_line_join(cairo.LINE_JOIN_ROUND)
    ctx.stroke()


def fill(ctx, c, a=1.0, ow=6, oc=INK):
    src(ctx, c, a)
    if ow:
        ctx.fill_preserve()
        stroke(ctx, ow, oc)
    else:
        ctx.fill()


def rrect(ctx, x, y, w, h, r):
    r = max(0.1, min(r, w / 2, h / 2))
    ctx.new_sub_path()
    ctx.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    ctx.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    ctx.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    ctx.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    ctx.close_path()


def poly(ctx, pts):
    ctx.move_to(*pts[0])
    for q in pts[1:]:
        ctx.line_to(*q)
    ctx.close_path()


def ellipse(ctx, x, y, rx, ry):
    ctx.save()
    ctx.translate(x, y)
    ctx.scale(rx, ry)
    ctx.arc(0, 0, 1, 0, 2 * math.pi)
    ctx.restore()


def lin(ctx, x0, y0, x1, y1, stops):
    g = cairo.LinearGradient(x0, y0, x1, y1)
    for off, h, a in stops:
        g.add_color_stop_rgba(off, *hexc(h, a)[:3], a)
    ctx.set_source(g)


def font(ctx, size, face=None, bold=True):
    face = face or FONT_BOLD
    weight = cairo.FONT_WEIGHT_BOLD if (bold and face != "Arial Black") else cairo.FONT_WEIGHT_NORMAL
    ctx.select_font_face(face, cairo.FONT_SLANT_NORMAL, weight)
    ctx.set_font_size(size)


def text_width(ctx, s, size, face=None):
    font(ctx, size, face)
    return ctx.text_extents(s)[4]


def text(ctx, s, x, y, size, color="#ffffff", face=None, align="c", ow=0, oc=INK, alpha=1.0, rot=0.0):
    """Text centred vertically on y."""
    ctx.save()
    ctx.translate(x, y)
    if rot:
        ctx.rotate(rot)
    font(ctx, size, face)
    ext = ctx.text_extents(s)
    xa = ext[4]
    asc = ctx.font_extents()[0]
    ox = {"c": -xa / 2, "l": 0, "r": -xa}[align]
    ctx.move_to(ox, asc * 0.36)
    ctx.text_path(s)
    if ow:
        src(ctx, oc, alpha)
        ctx.set_line_width(ow)
        ctx.set_line_join(cairo.LINE_JOIN_ROUND)
        ctx.stroke_preserve()
    src(ctx, color, alpha)
    ctx.fill()
    ctx.restore()
    return xa


def fit_size(ctx, s, max_w, size, face=None, min_size=26):
    while size > min_size and text_width(ctx, s, size, face) > max_w:
        size -= 2
    return size


def star(ctx, x, y, r, c, ow=3):
    pts = []
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.45
        pts.append((x + math.cos(a) * rr, y + math.sin(a) * rr))
    poly(ctx, pts)
    fill(ctx, c, ow=ow)


def vignette(ctx, strength=0.5):
    g = cairo.RadialGradient(W / 2, H * 0.48, H * 0.3, W / 2, H * 0.48, H * 0.78)
    g.add_color_stop_rgba(0, 0.08, 0.06, 0.16, 0)
    g.add_color_stop_rgba(1, 0.08, 0.06, 0.16, strength)
    ctx.set_source(g)
    ctx.paint()


# ---------- characters ----------
CIVILIAN_SHIRTS = ["#3f7fd1", "#d1584a", "#4aa36b", "#c98a2e", "#8a5ac9", "#2f9da3", "#c85a8f", "#6b7a99"]
HAIR = ["#3b2a20", "#6b4226", "#1f1a1a", "#a8642e", "#d8b25c"]

ROLES = {
    "detective": dict(body="#c08f57", tie="#b03a3a", hat="fedora", coat=True),
    "police": dict(body="#24346a", hat="cap", badge=True),
    "shadow": dict(body="#1c1a28", hat="dark", coat=True, shadow=True),
    "convict": dict(body="#e9792b", shadow=True),
    "judge": dict(body="#1f1d2b", robe=True, wig=True),
    "reporter": dict(body="#d9a63a", tie="#3b5fa8", hair="short"),
    "lawyer": dict(body="#3c4255", tie="#8a2b3b", hair="short"),
    "forensic": dict(body="#eef1f6", glasses=True, hair="short"),
    "man": dict(hair="short"),
    "woman": dict(hair="long"),
    "elder_man": dict(hair="grey_sides", glasses=True),
    "elder_woman": dict(hair="grey_bun", glasses=True),
}

# mood -> (brows, mouth)
MOODS = {
    "neutral": ("n", "flat"), "happy": ("n", "smile"), "worried": ("worry", "flat"),
    "shocked": ("up", "o"), "sad": ("sad", "frown"), "angry": ("angry", "frown"),
    "smug": ("smug", "grin"), "thinking": ("smug", "flat"), "determined": ("angry", "flat"),
}


def person(ctx, x, y, s=1.0, role="man", face=1, mood="neutral", talk=0.0, look=(0, 0), la=10, ra=10,
           walk=None, hold=None, t=0.0, tilt=0.0, sweat=False, wink=False, tint=0, lean=0.0):
    """Draw a character with its feet at (x, y). face=1 faces right, -1 left."""
    spec = ROLES.get(role, ROLES["man"])
    shadow = spec.get("shadow", False)
    rim = "#8b7de6"
    oc = rim if shadow else INK
    body_c = spec.get("body") or CIVILIAN_SHIRTS[tint % len(CIVILIAN_SHIRTS)]
    hair_c = HAIR[tint % len(HAIR)]
    brow, mouth = MOODS.get(mood, MOODS["neutral"])

    ctx.save()
    ctx.translate(x, y)
    ctx.scale(s * face, s)
    LEG = 200
    bob = abs(math.sin(walk)) * 8 if walk is not None else math.sin(t * 2.2 + tint) * 1.5
    hip = -LEG - bob
    sw = math.sin(walk) * 0.42 if walk is not None else 0.0
    for side, a in ((-1, sw), (1, -sw)):
        hx = side * 17
        fx, fy = hx + math.sin(a) * LEG, hip + math.cos(a) * LEG
        ctx.move_to(hx, hip)
        ctx.line_to(fx, fy)
        stroke(ctx, 11, oc)
        ellipse(ctx, fx + 12, fy - 6, 22, 11)
        fill(ctx, "#121019" if shadow else "#2a2638", ow=4, oc=oc)
    ctx.translate(0, -bob)
    ctx.rotate(lean)
    top = -LEG - 150

    # torso
    if spec.get("robe"):
        poly(ctx, [(-50, top), (50, top), (85, -20), (-85, -20)])
        fill(ctx, body_c, ow=6, oc=oc)
    else:
        tall = 190 if spec.get("coat") else 165
        wide = 54 if spec.get("coat") else 50
        rrect(ctx, -wide, top, 2 * wide, tall, 38)
        lin(ctx, -wide, 0, wide, 0, [(0, mix(body_c, "#ffe6c4", 0.12), 1), (1, mix(body_c, "#2a2260", 0.3), 1)])
        ctx.fill_preserve()
        stroke(ctx, 6, oc)
        if spec.get("coat"):
            ctx.move_to(-6, top + 6); ctx.line_to(-20, top + 70)
            ctx.move_to(6, top + 6); ctx.line_to(22, top + 70)
            stroke(ctx, 4, oc, 0.6)
        if spec.get("badge"):
            ctx.move_to(-48, top + 120); ctx.line_to(48, top + 120)
            stroke(ctx, 8, "#14182b")
            star(ctx, -22, top + 45, 13, "#f2c14e")
    if spec.get("tie"):
        poly(ctx, [(0, top + 8), (10, top + 22), (6, top + 80), (0, top + 92), (-6, top + 80), (-10, top + 22)])
        fill(ctx, spec["tie"], ow=3, oc=oc)

    # arms
    shy = top + 30
    hands = {}
    for side, ang in ((-1, la), (1, ra)):
        a = math.radians(ang)
        sx = side * 42
        hx, hy = sx + side * math.sin(a) * 135, shy + math.cos(a) * 135
        ctx.move_to(sx, shy)
        ctx.line_to(hx, hy)
        stroke(ctx, 10, oc)
        ctx.arc(hx, hy, 14, 0, 2 * math.pi)
        fill(ctx, "#2b2838" if shadow else "#fbfaf7", ow=5, oc=oc)
        hands[side] = (hx, hy)

    # head
    cy = top - 72
    R = 88
    ctx.save()
    ctx.translate(0, cy)
    ctx.rotate(tilt)
    hair = spec.get("hair")
    if hair == "long":
        rrect(ctx, -R - 8, -R + 10, 2 * R + 16, R * 2.05, 50)
        fill(ctx, hair_c, ow=6)
    if spec.get("wig"):
        for sx in (-1, 1):
            for k in range(3):
                ctx.arc(sx * 78, -10 + k * 34, 26, 0, 2 * math.pi)
                fill(ctx, "#e7e4ee", ow=4)
    ctx.arc(0, 0, R, 0, 2 * math.pi)
    g = cairo.RadialGradient(-28, -32, 8, 0, 0, R)
    if shadow:
        g.add_color_stop_rgba(0, *hexc("#3a3550")[:3], 1)
        g.add_color_stop_rgba(1, *hexc("#15131e")[:3], 1)
    else:
        g.add_color_stop_rgba(0, 1, 1, 1, 1)
        g.add_color_stop_rgba(1, *hexc("#dcd8e8")[:3], 1)
    ctx.set_source(g)
    ctx.fill_preserve()
    stroke(ctx, 6, oc)
    if hair in ("short", "long"):
        ctx.arc(0, 0, R + 2, math.radians(198), math.radians(342))
        ctx.curve_to(60, -60, 20, -48, -10, -58)
        ctx.curve_to(-40, -50, -70, -40, -84, -28)
        ctx.close_path()
        fill(ctx, hair_c, ow=5)
    elif hair == "grey_sides":
        for sx in (-1, 1):
            ellipse(ctx, sx * 80, -6, 16, 30)
            fill(ctx, "#cfcad8", ow=4)
    elif hair == "grey_bun":
        ctx.arc(0, -R - 18, 30, 0, 2 * math.pi)
        fill(ctx, "#cfcad8", ow=5)
        ctx.arc(0, 0, R + 2, math.radians(195), math.radians(345))
        ctx.curve_to(40, -66, -40, -66, -84, -26)
        ctx.close_path()
        fill(ctx, "#cfcad8", ow=5)

    lx, ly = look
    ex = [-14 + lx * 10, 30 + lx * 10]
    ey = -6 + ly * 8
    for i, e in enumerate(ex):
        if wink and i == 1:
            ctx.move_to(e - 11, ey)
            ctx.curve_to(e - 5, ey + 7, e + 5, ey + 7, e + 11, ey)
            stroke(ctx, 5, INK)
        elif shadow:
            ellipse(ctx, e, ey, 14, 8)
            fill(ctx, "#f4f1ff", ow=0)
            ctx.arc(e + 4, ey, 4.5, 0, 2 * math.pi)
            src(ctx, "#15131e")
            ctx.fill()
        else:
            ctx.arc(e, ey, 9, 0, 2 * math.pi)
            src(ctx, INK)
            ctx.fill()
    if spec.get("glasses"):
        for e in ex:
            ctx.new_sub_path()
            ctx.arc(e, ey, 20, 0, 2 * math.pi)
        ctx.move_to(ex[0] + 20, ey)
        ctx.line_to(ex[1] - 20, ey)
        stroke(ctx, 4, INK)
    # A shadow figure (an unidentified culprit, or a convicted person) is FACELESS: only its glowing eyes.
    # 27 Nov 2026: shadows had brows and a mouth (even a smile), so the AI review blocked videos for
    # 'showing the face of the convicted killer', against the channel's own faceless-shadow rule.
    if not shadow:
        by = -40
        bc = "#d9d3ff" if shadow else INK
        for i, e in enumerate(ex):
            inner = 1 if i == 0 else -1
            d_in, d_out = {"n": (0, 0), "angry": (9, -7), "worry": (-9, 5), "up": (-12, -12),
                           "smug": ((0, 0) if i == 0 else (-14, -4)), "sad": (-6, 6)}[brow]
            ctx.move_to(e - inner * 13, by + d_out)
            ctx.line_to(e + inner * 13, by + d_in)
        stroke(ctx, 7, bc)
        mx, my = 10 + lx * 8, 38
        mc = "#f4f1ff" if shadow else INK
        if talk > 0:
            o = 0.25 + 0.75 * abs(math.sin(t * 19))
            ellipse(ctx, mx, my, 13, 4 + 14 * o * talk)
            fill(ctx, "#3a1e2a", ow=4, oc=oc)
        elif mouth == "smile":
            ctx.arc(mx, my - 14, 22, math.radians(30), math.radians(150))
            stroke(ctx, 6, mc)
        elif mouth == "grin":
            ctx.move_to(mx - 34, my - 6)
            ctx.curve_to(mx - 20, my + 26, mx + 20, my + 26, mx + 34, my - 6)
            ctx.close_path()
            fill(ctx, "#f4f1ff", ow=4, oc=oc)
        elif mouth == "frown":
            ctx.arc(mx, my + 18, 18, math.radians(210), math.radians(330))
            stroke(ctx, 6, mc)
        elif mouth == "o":
            ellipse(ctx, mx, my + 4, 12, 16)
            fill(ctx, "#3a1e2a", ow=4, oc=oc)
        else:
            ctx.move_to(mx - 16, my)
            ctx.line_to(mx + 16, my)
            stroke(ctx, 6, mc)

    hat = spec.get("hat")
    if hat == "cap":
        ellipse(ctx, 0, -62, 92, 40); fill(ctx, "#24346a", ow=5)
        ctx.rectangle(-80, -64, 160, 26); fill(ctx, "#1a2350", ow=5)
        ellipse(ctx, 40, -36, 66, 13); fill(ctx, "#111522", ow=4)
        star(ctx, 0, -60, 12, "#f2c14e")
    elif hat in ("fedora", "dark"):
        c1, c2 = ("#5b3b27", "#6b4630") if hat == "fedora" else ("#121019", "#121019")
        ellipse(ctx, 0, -66, 122, 22); fill(ctx, c1, ow=5, oc=oc)
        rrect(ctx, -64, -128, 128, 66, 22); fill(ctx, c2, ow=5, oc=oc)
        if hat == "fedora":
            ctx.rectangle(-63, -84, 126, 16); src(ctx, "#2a1b14"); ctx.fill()
    if sweat:
        ctx.move_to(-70, -40)
        ctx.curve_to(-86, -10, -84, 4, -70, 6)
        ctx.curve_to(-56, 4, -54, -10, -70, -40)
        fill(ctx, "#8fd3ff", ow=3)
    ctx.restore()

    if hold:
        hx, hy = hands[1]
        ctx.save()
        ctx.translate(hx, hy)
        ctx.scale(face, 1)  # icons never mirror (text stays readable)
        draw_icon(ctx, hold, 30, -40, 0.55, badge=False)
        ctx.restore()
    ctx.restore()


# ---------- icons (pop-ups and held props) ----------
def _phone(ctx):
    rrect(ctx, -45, -80, 90, 160, 18); fill(ctx, "#2b2f45")
    rrect(ctx, -35, -62, 70, 112, 8); fill(ctx, "#7fd0ff", ow=0)
    ctx.arc(0, 64, 7, 0, 6.3); src(ctx, "#9aa0b8"); ctx.fill()


def _letter(ctx):
    rrect(ctx, -80, -52, 160, 104, 8); fill(ctx, "#fbf6ea")
    ctx.move_to(-76, -48); ctx.line_to(0, 8); ctx.line_to(76, -48); stroke(ctx, 5)


def _car(ctx):
    ctx.move_to(-95, 20); ctx.line_to(-90, -15); ctx.line_to(-50, -25); ctx.line_to(-25, -60)
    ctx.line_to(40, -60); ctx.line_to(65, -25); ctx.line_to(95, -18); ctx.line_to(98, 20); ctx.close_path()
    fill(ctx, "#d1584a")
    poly(ctx, [(-18, -50), (14, -50), (14, -25), (-38, -25)]); fill(ctx, "#bfe3ff", ow=4)
    poly(ctx, [(22, -50), (38, -50), (55, -25), (22, -25)]); fill(ctx, "#bfe3ff", ow=4)
    for wx in (-55, 58):
        ctx.arc(wx, 22, 22, 0, 6.3); fill(ctx, "#2b2838")
        ctx.arc(wx, 22, 9, 0, 6.3); fill(ctx, "#c9ccd8", ow=3)


def _key(ctx):
    ctx.arc(-45, 0, 32, 0, 6.3); fill(ctx, "#f2c14e")
    ctx.arc(-45, 0, 12, 0, 6.3); fill(ctx, "#fffaf0", ow=4)
    poly(ctx, [(-14, -10), (80, -10), (80, 10), (-14, 10)]); fill(ctx, "#f2c14e")
    for kx in (50, 70):
        ctx.rectangle(kx - 6, 10, 12, 22); fill(ctx, "#f2c14e", ow=4)


def _dna(ctx, t=0.0):
    for i in range(9):
        yy = -80 + i * 20
        a = math.sin(i * 0.7 + t * 4) * 45
        ctx.move_to(-a, yy); ctx.line_to(a, yy); stroke(ctx, 5, "#8fd3ff")
        ctx.arc(-a, yy, 8, 0, 6.3); fill(ctx, "#ff7aa8", ow=3)
        ctx.arc(a, yy, 8, 0, 6.3); fill(ctx, "#7ad3ff", ow=3)


def _fingerprint(ctx):
    for r in range(14, 80, 13):
        ctx.new_sub_path()
        ctx.arc(0, 8, r, math.radians(200), math.radians(340 + (r % 3) * 10))
    stroke(ctx, 7, "#3b5fa8")


def _magnifier(ctx):
    ctx.move_to(30, 30); ctx.line_to(80, 80); stroke(ctx, 18, "#6b4630")
    ctx.arc(-10, -10, 55, 0, 6.3); fill(ctx, "#bfe3ff", a=0.85, ow=10)


def _cctv(ctx):
    ctx.move_to(-70, -60); ctx.line_to(-70, 40); stroke(ctx, 10)
    ctx.save(); ctx.rotate(0.25)
    rrect(ctx, -60, -50, 130, 60, 12); fill(ctx, "#e7e2d4")
    ctx.arc(60, -20, 18, 0, 6.3); fill(ctx, "#2b2838")
    ctx.arc(60, -20, 6, 0, 6.3); src(ctx, "#ff5a5a"); ctx.fill()
    ctx.restore()


def _clock(ctx, t=0.0):
    ctx.arc(0, 0, 80, 0, 6.3); fill(ctx, "#fffaf0", ow=8)
    for i in range(12):
        a = i * math.pi / 6
        ctx.move_to(math.cos(a) * 64, math.sin(a) * 64); ctx.line_to(math.cos(a) * 72, math.sin(a) * 72)
    stroke(ctx, 5)
    a = t * 1.5
    ctx.move_to(0, 0); ctx.line_to(math.sin(a) * 55, -math.cos(a) * 55); stroke(ctx, 6)
    ctx.move_to(0, 0); ctx.line_to(math.sin(a / 12) * 36, -math.cos(a / 12) * 36); stroke(ctx, 8)


def _pin(ctx):
    ctx.move_to(0, 85)
    ctx.curve_to(-30, 30, -60, 0, -60, -30)
    ctx.arc(0, -30, 60, math.pi, 0)
    ctx.curve_to(60, 0, 30, 30, 0, 85)
    fill(ctx, "#e0413b")
    ctx.arc(0, -30, 22, 0, 6.3); fill(ctx, "#fffaf0", ow=4)


def _question(ctx):
    text(ctx, "?", 0, 0, 170, "#ffffff", ow=16)


def _bulb(ctx):
    ctx.arc(0, -20, 52, math.radians(140), math.radians(40))
    ctx.line_to(20, 44); ctx.line_to(-20, 44); ctx.close_path()
    fill(ctx, "#ffe27a")
    ctx.rectangle(-22, 44, 44, 28); fill(ctx, "#b5b8c8", ow=5)


def _document(ctx):
    poly(ctx, [(-60, -80), (35, -80), (60, -55), (60, 80), (-60, 80)]); fill(ctx, "#fffaf0")
    for i in range(5):
        ctx.move_to(-40, -40 + i * 26); ctx.line_to(40 - (i == 4) * 30, -40 + i * 26)
    stroke(ctx, 5, "#9a9ab8")


def _bag(ctx):
    ctx.arc(0, -30, 38, math.pi, 0); stroke(ctx, 9)
    rrect(ctx, -70, -30, 140, 100, 22); fill(ctx, "#a0522d")
    ctx.rectangle(-12, -8, 24, 18); fill(ctx, "#f2c14e", ow=3)


def _footprints(ctx):
    for i, (fx, fy, r) in enumerate(((-35, 40, -0.2), (35, -40, 0.2))):
        ctx.save(); ctx.translate(fx, fy); ctx.rotate(r)
        ellipse(ctx, 0, 0, 22, 40); fill(ctx, "#5b4a9a", ow=4)
        ellipse(ctx, 0, 52, 16, 16); fill(ctx, "#5b4a9a", ow=4)
        ctx.restore()


def _money(ctx):
    for k in (1, 0):
        ctx.save(); ctx.translate(k * 14, -k * 14)
        rrect(ctx, -85, -48, 170, 96, 10); fill(ctx, "#6dbf7b")
        ctx.arc(0, 0, 26, 0, 6.3); fill(ctx, "#4a9a58", ow=4)
        text(ctx, "£", 0, 2, 34, "#fffaf0")
        ctx.restore()


def _newspaper(ctx):
    rrect(ctx, -75, -90, 150, 180, 8); fill(ctx, "#f7f1df")
    ctx.rectangle(-60, -75, 120, 24); fill(ctx, "#3a3552", ow=0)
    for i in range(5):
        ctx.move_to(-60, -30 + i * 22); ctx.line_to(60, -30 + i * 22)
    stroke(ctx, 6, "#9a9ab8")


def _tv(ctx):
    rrect(ctx, -90, -65, 180, 130, 18); fill(ctx, "#8c6a4a")
    rrect(ctx, -72, -50, 120, 100, 12); fill(ctx, "#bfe3ff")
    ctx.move_to(-20, -65); ctx.line_to(-50, -110); ctx.move_to(20, -65); ctx.line_to(50, -110); stroke(ctx, 6)


def _computer(ctx):
    rrect(ctx, -90, -75, 180, 120, 12); fill(ctx, "#e7e2d4")
    rrect(ctx, -76, -62, 152, 94, 8); fill(ctx, "#3a7bd5", ow=4)
    poly(ctx, [(-30, 45), (30, 45), (45, 75), (-45, 75)]); fill(ctx, "#cfc8b5")


def _floppy(ctx):
    poly(ctx, [(-70, -70), (58, -70), (70, -58), (70, 70), (-70, 70)]); fill(ctx, "#7c4ad1")
    ctx.rectangle(-38, -70, 76, 46); fill(ctx, "#c9ccd8", ow=4)
    rrect(ctx, -52, 0, 104, 64, 6); fill(ctx, "#fbfaf4", ow=4)


def _gavel(ctx):
    ctx.save(); ctx.rotate(-0.6)
    ctx.move_to(0, -10); ctx.line_to(0, 95); stroke(ctx, 14, "#6b3f22")
    rrect(ctx, -55, -50, 110, 50, 12); fill(ctx, "#8a5432")
    ctx.restore()


def _calendar(ctx):
    rrect(ctx, -75, -70, 150, 150, 12); fill(ctx, "#fffaf0")
    ctx.rectangle(-75, -70, 150, 38); fill(ctx, "#e0413b", ow=4)
    for r in range(3):
        for c in range(4):
            ctx.rectangle(-58 + c * 30, -18 + r * 30, 20, 18); src(ctx, "#9a9ab8"); ctx.fill()


def _camera(ctx):
    rrect(ctx, -85, -50, 170, 110, 16); fill(ctx, "#3a3552")
    ctx.rectangle(-35, -68, 50, 22); fill(ctx, "#3a3552")
    ctx.arc(0, 5, 38, 0, 6.3); fill(ctx, "#8fd3ff", ow=7)


def _ring(ctx):
    ctx.arc(0, 25, 50, 0, 6.3); stroke(ctx, 14, "#f2c14e")
    poly(ctx, [(-26, -40), (26, -40), (38, -22), (0, 12), (-38, -22)]); fill(ctx, "#bfe9ff")


def _house(ctx):
    poly(ctx, [(-80, -10), (0, -85), (80, -10)]); fill(ctx, "#b24a3f")
    ctx.rectangle(-62, -10, 124, 90); fill(ctx, "#f3eee4")
    ctx.rectangle(-15, 30, 30, 50); fill(ctx, "#7a4a2a", ow=4)


def _pills(ctx):
    rrect(ctx, -45, -60, 90, 120, 14); fill(ctx, "#f29a3a")
    ctx.rectangle(-50, -80, 100, 30); fill(ctx, "#fffaf0", ow=5)


def _bike(ctx):
    for wx in (-55, 55):
        ctx.arc(wx, 25, 38, 0, 6.3); stroke(ctx, 8)
    ctx.move_to(-55, 25); ctx.line_to(-10, -30); ctx.line_to(40, -30); ctx.line_to(55, 25)
    ctx.move_to(-10, -30); ctx.line_to(10, 25); ctx.line_to(-55, 25)
    stroke(ctx, 8, "#3f7fd1")


def _boat(ctx):
    poly(ctx, [(-95, 0), (95, 0), (65, 50), (-65, 50)]); fill(ctx, "#d1584a")
    ctx.move_to(0, 0); ctx.line_to(0, -90); stroke(ctx, 8)
    poly(ctx, [(5, -85), (60, -10), (5, -10)]); fill(ctx, "#fffaf0")


def _plane(ctx):
    rrect(ctx, -95, -16, 190, 32, 16); fill(ctx, "#e7e8f0")
    poly(ctx, [(-10, -10), (30, -10), (-20, -75), (-40, -75)]); fill(ctx, "#bfc4d8")
    poly(ctx, [(-10, 10), (30, 10), (-20, 75), (-40, 75)]); fill(ctx, "#bfc4d8")


def _handprint_q(ctx):
    _question(ctx)


ICONS = {
    "phone": _phone, "letter": _letter, "car": _car, "key": _key, "dna": _dna, "fingerprint": _fingerprint,
    "magnifier": _magnifier, "cctv": _cctv, "clock": _clock, "map_pin": _pin, "question": _question,
    "bulb": _bulb, "document": _document, "bag": _bag, "footprints": _footprints, "money": _money,
    "newspaper": _newspaper, "tv": _tv, "computer": _computer, "floppy": _floppy, "gavel": _gavel,
    "calendar": _calendar, "camera": _camera, "ring": _ring, "house": _house, "pills": _pills,
    "bike": _bike, "boat": _boat, "plane": _plane,
}
ANIMATED = {"dna", "clock"}


def draw_icon(ctx, name, x, y, s=1.0, t=0.0, badge=True, rot=0.0):
    fn = ICONS.get(name)
    if not fn:
        return
    ctx.save()
    ctx.translate(x, y)
    ctx.rotate(rot)
    ctx.scale(s, s)
    if badge and name != "question":
        ctx.arc(6, 10, 118, 0, 6.3); src(ctx, "#0e0b1f", 0.25); ctx.fill()
        ctx.arc(0, 0, 118, 0, 6.3); fill(ctx, "#fffaf0", ow=8)
    if name in ANIMATED:
        fn(ctx, t)
    else:
        fn(ctx)
    ctx.restore()


def card(ctx, s, x, y, k, size=64, bg="#fffaf0", fg=INK, rot=0.0, max_w=860):
    """Rounded label card that pops in as k goes 0 -> 1."""
    if k <= 0:
        return
    size = fit_size(ctx, s, max_w - 70, size)
    w = text_width(ctx, s, size) + 70
    h = size * 1.55
    ctx.save()
    ctx.translate(x, y)
    ctx.rotate(rot)
    sc = back(k)
    ctx.scale(sc, sc)
    rrect(ctx, -w / 2 + 6, -h / 2 + 10, w, h, 22); src(ctx, "#0e0b1f", 0.3); ctx.fill()
    rrect(ctx, -w / 2, -h / 2, w, h, 22); fill(ctx, bg, ow=7)
    text(ctx, s, 0, 0, size, fg)
    ctx.restore()


def stamp(ctx, s, x, y, k, color="#e0413b", size=100, rot=-0.14, max_w=900):
    """Rubber-stamp text that slams in as k goes 0 -> 1."""
    if k <= 0:
        return
    size = fit_size(ctx, s, max_w - 80, size)
    sc = lerp(2.2, 1.0, back(k))
    a = min(1.0, k * 2)
    ctx.save()
    ctx.translate(x, y)
    ctx.rotate(rot)
    ctx.scale(sc, sc)
    xa = text_width(ctx, s, size)
    rrect(ctx, -xa / 2 - 30, -size * 0.72, xa + 60, size * 1.44, 18)
    src(ctx, "#fffaf0", 0.9 * a)
    ctx.fill_preserve()
    src(ctx, color, a)
    ctx.set_line_width(14)
    ctx.stroke()
    text(ctx, s, 0, 0, size, color, alpha=a)
    ctx.restore()
