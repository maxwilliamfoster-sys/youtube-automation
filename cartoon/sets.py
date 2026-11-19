"""Backgrounds ("sets"). Each draws in world space (1080x1920, camera applied by
the engine) and leaves the floor free at y~1300-1700 for the actors.

SETS[name] = {"bg": fn(ctx, rng, t), "fg": fn or None, "floor": y for feet}
"""
import math
import random

import cairo

from .kit import (W, H, INK, FONT_SERIF, ellipse, fill, lin, mix, poly, rrect, src, star, stroke, text)


# ---------- building blocks ----------
def room(ctx, wall="#3f8fa3", side=None, floor="#9aa7b8", ceil="#2b2f55",
         bk=(150, 420, 930, 1270), bricks=True, tiles=True, window=None, panel=False):
    """One-point-perspective room that fills the frame (and beyond, for the camera)."""
    side = side or mix(wall, "#2d2560", 0.25)
    x0, y0, x1, y1 = bk
    vx, vy = (x0 + x1) / 2, (y0 + y1) / 2 + 60
    K = 7

    def pr(px, py):
        return (vx + (px - vx) * K, vy + (py - vy) * K)

    poly(ctx, [(x0, y0), (x1, y0), pr(x1, y0), pr(x0, y0)]); fill(ctx, ceil, ow=0)
    poly(ctx, [(x0, y1), (x1, y1), pr(x1, y1), pr(x0, y1)])
    lin(ctx, 0, y1, 0, H + 200, [(0, floor, 1), (1, mix(floor, "#3a3170", 0.45), 1)]); ctx.fill()
    for xa, sgn in ((x0, -1), (x1, 1)):
        poly(ctx, [(xa, y0), (xa, y1), pr(xa, y1), pr(xa, y0)])
        lin(ctx, xa, 0, xa + sgn * 700, 0, [(0, side, 1), (1, mix(side, "#2a1f5c", 0.5), 1)]); ctx.fill()
    ctx.rectangle(x0, y0, x1 - x0, y1 - y0)
    lin(ctx, 0, y0, 0, y1, [(0, mix(wall, "#ffe2b8", 0.12), 1), (1, mix(wall, "#3a2f6b", 0.18), 1)]); ctx.fill()
    line_c = mix(wall, INK, 0.45)
    if bricks:
        bh, y, row = 48, y0 + 48, 0
        while y < y1:
            ctx.move_to(x0, y); ctx.line_to(x1, y)
            off = 0 if row % 2 else 55
            for bx in range(int(x0 + off), int(x1), 110):
                ctx.move_to(bx, y - bh); ctx.line_to(bx, y)
            y += bh; row += 1
        stroke(ctx, 3, line_c, 0.35)
        for xa in (x0, x1):
            yy = y0 + bh
            while yy < y1:
                ctx.move_to(xa, yy); ctx.line_to(*pr(xa, yy)); yy += bh
            stroke(ctx, 3, mix(side, INK, 0.45), 0.3)
    if panel:
        for bx in range(int(x0), int(x1), 90):
            ctx.move_to(bx, y0); ctx.line_to(bx, y1)
        ctx.move_to(x0, y0 + (y1 - y0) * 0.55); ctx.line_to(x1, y0 + (y1 - y0) * 0.55)
        stroke(ctx, 4, line_c, 0.4)
    if tiles:
        for i in range(9):
            fx = x0 + (x1 - x0) * i / 8
            ctx.move_to(fx, y1); ctx.line_to(*pr(fx, y1))
        for k in (1.15, 1.38, 1.7, 2.2, 3.0, 4.3):
            yy = vy + (y1 - vy) * k
            ctx.move_to(-800, yy); ctx.line_to(W + 800, yy)
        stroke(ctx, 3, mix(floor, INK, 0.5), 0.22)
    ctx.rectangle(x0, y0, x1 - x0, y1 - y0)
    for a in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
        ctx.move_to(*a); ctx.line_to(*pr(*a))
    stroke(ctx, 5, INK, 0.45)
    ctx.rectangle(x0, y1 - 18, x1 - x0, 18); src(ctx, "#2a2350", 0.25); ctx.fill()
    if window:
        wx, wy, ww, wh = window
        poly(ctx, [(wx, wy + wh), (wx + ww, wy + wh), (wx + ww - 160, y1 + 520), (wx - 330, y1 + 520)])
        lin(ctx, 0, wy, 0, y1 + 520, [(0, "#ffd79a", 0.32), (1, "#ffd79a", 0.0)]); ctx.fill()
        poly(ctx, [(wx - 60, y1 + 60), (wx + ww - 40, y1 + 60), (wx + ww - 150, y1 + 420), (wx - 300, y1 + 420)])
        src(ctx, "#ffcf8a", 0.16); ctx.fill()
        rrect(ctx, wx - 10, wy - 10, ww + 20, wh + 20, 8); fill(ctx, mix(wall, INK, 0.35), ow=5)
        ctx.rectangle(wx, wy, ww, wh)
        lin(ctx, wx, wy, wx + ww, wy + wh, [(0, "#fff1c9", 1), (1, "#ffc477", 1)]); ctx.fill()
        for i in range(1, 4):
            ctx.move_to(wx + ww * i / 4, wy); ctx.line_to(wx + ww * i / 4, wy + wh)
        ctx.move_to(wx, wy + wh / 2); ctx.line_to(wx + ww, wy + wh / 2)
        stroke(ctx, 7, "#3b3552")


def sky(ctx, top="#2a2363", mid="#7b4f9e", low="#ff9e6b", horizon=1250):
    ctx.rectangle(-900, -900, W + 1800, horizon + 900)
    lin(ctx, 0, -200, 0, horizon, [(0, top, 1), (0.6, mid, 1), (1, low, 1)]); ctx.fill()


def ground(ctx, y=1250, c="#4b4a7a", walk="#8d8fb5"):
    ctx.rectangle(-900, y, W + 1800, H + 900)
    lin(ctx, 0, y, 0, H + 200, [(0, c, 1), (1, mix(c, "#1e1840", 0.5), 1)]); ctx.fill()
    ctx.rectangle(-900, y, W + 1800, 150)
    lin(ctx, 0, y, 0, y + 150, [(0, walk, 1), (1, mix(walk, c, 0.5), 1)]); ctx.fill()
    for x in range(-900, W + 900, 170):
        ctx.move_to(x, y); ctx.line_to(x - 60, y + 150)
    stroke(ctx, 3, INK, 0.25)
    ctx.move_to(-900, y); ctx.line_to(W + 900, y); stroke(ctx, 5, INK, 0.5)


def skyline(ctx, rng_seed, base=1250, night=True):
    rnd = random.Random(rng_seed)
    x = -700
    while x < W + 700:
        w = rnd.randint(140, 260); h = rnd.randint(260, 620)
        ctx.rectangle(x, base - h, w, h)
        if night:
            fill(ctx, mix("#3a3478", "#251f52", rnd.random()), ow=4, oc="#1b1638")
        else:
            fill(ctx, mix("#9aa6d6", "#7d86c0", rnd.random()), ow=4, oc="#4a4f80")
        for wy in range(base - h + 40, base - 40, 70):
            for wx in range(x + 25, x + w - 30, 55):
                if rnd.random() < (0.35 if night else 0.6):
                    ctx.rectangle(wx, wy, 26, 34)
                    src(ctx, "#ffd486" if night else "#d9ecff", 0.85); ctx.fill()
        x += w + rnd.randint(10, 40)


def tree(ctx, x, y, s=1.0, c="#3f6b5a"):
    ctx.save(); ctx.translate(x, y); ctx.scale(s, s)
    ctx.rectangle(-18, -160, 36, 160); fill(ctx, "#5b3b27", ow=5)
    for cx, cy, r in ((0, -260, 110), (-80, -190, 80), (80, -190, 80)):
        ctx.arc(cx, cy, r, 0, 6.3); fill(ctx, c, ow=6)
    ctx.restore()


def desk(ctx, x, y, w=460, h=170, c="#8a5a3b"):
    poly(ctx, [(x - w / 2, y - h), (x + w / 2, y - h), (x + w / 2 - 34, y - h - 70), (x - w / 2 + 34, y - h - 70)])
    fill(ctx, mix(c, "#ffd9a8", 0.25))
    ctx.rectangle(x - w / 2, y - h, w, h)
    lin(ctx, 0, y - h, 0, y, [(0, c, 1), (1, mix(c, "#2a2058", 0.35), 1)]); ctx.fill_preserve(); stroke(ctx, 6)


def frame_pic(ctx, x, y, w, h, c="#6b4630"):
    rrect(ctx, x - w / 2, y - h / 2, w, h, 6); fill(ctx, c, ow=6)
    ctx.rectangle(x - w / 2 + 16, y - h / 2 + 16, w - 32, h - 32); fill(ctx, "#bfe3ff", ow=3)
    poly(ctx, [(x - w / 2 + 16, y + h / 2 - 16), (x - 10, y), (x + w / 2 - 16, y + h / 2 - 16)])
    fill(ctx, "#7fb27a", ow=0)


# ---------- sets ----------
def s_office(ctx, rng, t):
    room(ctx, wall="#3f8fa3", floor="#a3adc0", ceil="#283058", window=(620, 520, 220, 190))
    # filing cabinet + plant against the back wall
    ctx.rectangle(200, 900, 170, 370); fill(ctx, "#8d96ad")
    for i in range(3):
        ctx.rectangle(215, 920 + i * 115, 140, 95); fill(ctx, "#a7b0c6", ow=4)
        ctx.rectangle(265, 960 + i * 115, 40, 10); src(ctx, INK); ctx.fill()
    rrect(ctx, 400, 1180, 90, 90, 12); fill(ctx, "#c26a3a")
    for a in (-0.6, -0.2, 0.2, 0.6):
        ellipse(ctx, 445 + math.sin(a) * 50, 1130 - math.cos(a) * 40, 22, 50); fill(ctx, "#4aa36b", ow=4)


def s_incident_room(ctx, rng, t):
    room(ctx, wall="#4d7fb0", floor="#a7aec4", ceil="#26305a", bricks=False)
    rrect(ctx, 230, 560, 620, 440, 14); fill(ctx, "#7a4e2c", ow=7)
    ctx.rectangle(255, 585, 570, 390); fill(ctx, "#cf9f62", ow=4)
    pts = [(330, 650, -0.1), (720, 660, 0.08), (360, 880, 0.05), (700, 890, -0.06), (540, 760, 0.02)]
    for px, py, r in pts:
        ctx.save(); ctx.translate(px, py); ctx.rotate(r)
        ctx.rectangle(-58, -42, 116, 84); fill(ctx, "#fffaf0", ow=4)
        ctx.restore()
    for (ax, ay, _), (bx, by, _) in zip(pts, pts[1:] + pts[:1]):
        ctx.move_to(ax, ay); ctx.line_to(bx, by)
    stroke(ctx, 4, "#d23b3b")
    for px, py, _ in pts:
        ctx.arc(px, py - 36, 8, 0, 6.3); fill(ctx, "#e0413b", ow=3)


def s_interview_room(ctx, rng, t):
    room(ctx, wall="#7d8a9e", floor="#8e94a8", ceil="#2b2f4f", bricks=False)
    ctx.rectangle(230, 620, 620, 300); fill(ctx, "#4c5674", ow=7)  # one-way mirror
    poly(ctx, [(260, 650), (400, 650), (300, 890), (260, 890)]); src(ctx, "#ffffff", 0.12); ctx.fill()
    ctx.move_to(540, 0); ctx.line_to(540, 420); stroke(ctx, 6)
    poly(ctx, [(480, 420), (600, 420), (630, 470), (450, 470)]); fill(ctx, "#3a3552")
    poly(ctx, [(450, 470), (630, 470), (900, 1500), (180, 1500)]); src(ctx, "#fff1c9", 0.12); ctx.fill()
    # table in the middle of the floor
    poly(ctx, [(330, 1330), (750, 1330), (800, 1400), (280, 1400)]); fill(ctx, "#9aa0b5")
    ctx.rectangle(280, 1400, 520, 30); fill(ctx, "#7a8098", ow=5)


def s_courtroom(ctx, rng, t):
    room(ctx, wall="#8a5a3b", floor="#b49a86", ceil="#3a2830", bricks=False, panel=True, tiles=False)
    ctx.arc(540, 620, 90, 0, 6.3); fill(ctx, "#e8c25a", ow=6)
    star(ctx, 540, 620, 44, "#fff3c4")


def fg_courtroom(ctx, rng, t):
    # the judge's bench sits in front of the judge spot (540, 1260)
    ctx.rectangle(260, 1110, 560, 300); fill(ctx, "#6b4026")
    ctx.rectangle(240, 1100, 600, 40); fill(ctx, "#8a5a3b")


def s_cell(ctx, rng, t):
    room(ctx, wall="#7d8fa8", floor="#9aa0b5", ceil="#2b2f4f", window=(640, 560, 160, 140))
    rrect(ctx, 180, 1150, 330, 110, 14); fill(ctx, "#5c6680")  # bunk


def fg_cell(ctx, rng, t):
    c = "#6c7393"
    n, x0, x1, y0, y1 = 8, -120, 1200, 300, 1900
    for i in range(n):
        bx = x0 + (x1 - x0) * (i + 0.5) / n
        rrect(ctx, bx - 14, y0, 28, y1 - y0, 12)
        lin(ctx, bx - 14, 0, bx + 14, 0, [(0, mix(c, "#ffffff", 0.35), 1), (1, mix(c, INK, 0.3), 1)])
        ctx.fill_preserve(); stroke(ctx, 5)
    for yy in (y0 + 40, y1 - 60):
        rrect(ctx, x0, yy, x1 - x0, 30, 8); fill(ctx, mix(c, INK, 0.15), ow=5)


def s_lab(ctx, rng, t):
    room(ctx, wall="#cfe3ee", floor="#b8c4d6", ceil="#5a6f92", bricks=False)
    ctx.rectangle(150, 1030, 780, 40); fill(ctx, "#8d96ad")
    for i, c in enumerate(("#ff7aa8", "#7ad3ff", "#9be37a", "#ffd34d")):
        rrect(ctx, 230 + i * 50, 930, 30, 100, 12); fill(ctx, "#fffaf0", ow=4)
        rrect(ctx, 233 + i * 50, 975, 24, 52, 10); fill(ctx, c, ow=0)
    # microscope
    ctx.rectangle(700, 1000, 120, 30); fill(ctx, "#3a3552")
    ctx.move_to(780, 1000); ctx.line_to(780, 860); ctx.line_to(720, 820); stroke(ctx, 18, "#3a3552")
    rrect(ctx, 690, 800, 50, 70, 10); fill(ctx, "#c9ccd8", ow=5)
    # DNA poster
    rrect(ctx, 420, 560, 240, 300, 10); fill(ctx, "#fffaf0", ow=6)
    for i in range(9):
        yy = 600 + i * 28
        a = math.sin(i * 0.7 + t * 2) * 60
        ctx.move_to(540 - a, yy); ctx.line_to(540 + a, yy)
    stroke(ctx, 5, "#3b5fa8")


def s_newsroom(ctx, rng, t):
    room(ctx, wall="#3c2f7a", floor="#5e568f", ceil="#1d1640", bricks=False, tiles=True)
    rrect(ctx, 250, 520, 580, 360, 18); fill(ctx, "#1b1638", ow=8)
    rrect(ctx, 270, 540, 540, 320, 12)
    lin(ctx, 270, 540, 810, 860, [(0, "#3a7bd5", 1), (1, "#7b4fd1", 1)]); ctx.fill()
    text(ctx, "NEWS", 540, 690, 120, "#ffffff", ow=10)
    ctx.rectangle(270, 900, 540, 60); fill(ctx, "#e0413b", ow=5)
    text(ctx, "BREAKING", 540, 930, 40, "#ffffff")


def s_street_night(ctx, rng, t):
    sky(ctx, top="#1f1a4e", mid="#4b3a86", low="#c7709a")
    ctx.arc(860, 300, 60, 0, 6.3); fill(ctx, "#fff3c4", ow=0)
    skyline(ctx, rng, 1250, night=True)
    ground(ctx, 1250, c="#3e3b6e", walk="#7c7ea6")
    ctx.move_to(760, 1330); ctx.line_to(760, 760); ctx.line_to(860, 760); stroke(ctx, 14, "#2d2a46")
    poly(ctx, [(820, 770), (900, 770), (1020, 1480), (620, 1480)]); src(ctx, "#ffd486", 0.14); ctx.fill()
    ellipse(ctx, 860, 775, 42, 16); fill(ctx, "#ffe9a8")


def s_street_day(ctx, rng, t):
    sky(ctx, top="#5d8fe0", mid="#9cc2f0", low="#ffd9a8")
    ctx.arc(820, 320, 70, 0, 6.3); fill(ctx, "#ffe27a", ow=0)
    skyline(ctx, rng, 1250, night=False)
    ground(ctx, 1250, c="#6f7aa3", walk="#c1c6dd")
    tree(ctx, 160, 1250, 0.9)


def s_house(ctx, rng, t):
    sky(ctx, top="#33408f", mid="#9a74c4", low="#ffb889")
    ground(ctx, 1300, c="#58628a", walk="#a3a8c6")
    ctx.rectangle(260, 820, 560, 480); fill(ctx, "#f3eee4")
    poly(ctx, [(220, 830), (540, 560), (860, 830)]); fill(ctx, "#b24a3f")
    rrect(ctx, 490, 1080, 110, 220, 10); fill(ctx, "#7a4a2a")
    for wx in (350, 700):
        ctx.rectangle(wx - 60, 920, 120, 110); fill(ctx, "#ffd486", ow=5)
        ctx.move_to(wx, 920); ctx.line_to(wx, 1030); ctx.move_to(wx - 60, 975); ctx.line_to(wx + 60, 975)
        stroke(ctx, 5)
    for fx in range(-200, 1300, 60):
        ctx.rectangle(fx, 1330, 22, 90); fill(ctx, "#fffaf0", ow=3)
    tree(ctx, 960, 1300, 0.85)


def s_woods(ctx, rng, t):
    sky(ctx, top="#232a5c", mid="#4f4a8a", low="#a97aa8", horizon=1280)
    rnd = random.Random(rng + 1)
    for layer, (c, s, y) in enumerate((("#3b3f73", 0.8, 1200), ("#2f3a5e", 1.0, 1260))):
        for i in range(9):
            tree(ctx, -200 + i * 190 + rnd.randint(-40, 40), y, s + rnd.random() * 0.2, c=c)
    ground(ctx, 1280, c="#3d4a5e", walk="#6b6a8e")
    lin(ctx, 0, 1000, 0, 1500, [(0, "#d8d4f0", 0.0), (0.6, "#d8d4f0", 0.25), (1, "#d8d4f0", 0.0)])
    ctx.rectangle(-900, 1000, W + 1800, 500); ctx.fill()


def s_road_night(ctx, rng, t):
    sky(ctx, top="#1d1946", mid="#3c3480", low="#8a5aa0", horizon=1150)
    ctx.arc(250, 330, 55, 0, 6.3); fill(ctx, "#fff3c4", ow=0)
    ctx.rectangle(-900, 1150, W + 1800, 900); fill(ctx, "#2f3354", ow=0)
    poly(ctx, [(500, 1150), (580, 1150), (1400, 2000), (-320, 2000)]); fill(ctx, "#4a4d6b", ow=0)
    for k in range(6):
        y0 = 1180 + k * k * 30
        poly(ctx, [(536, y0), (544, y0), (548 + k * 6, y0 + 40 + k * 10), (532 - k * 6, y0 + 40 + k * 10)])
        src(ctx, "#ffe27a"); ctx.fill()
    # parked car with headlights
    ctx.save(); ctx.translate(820, 1420); ctx.scale(1.6, 1.6)
    poly(ctx, [(-70, 10), (70, 10), (90, 40), (-90, 40)]); src(ctx, "#ffe9a8", 0.0); ctx.fill()
    from .kit import ICONS
    ICONS["car"](ctx)
    ctx.restore()
    poly(ctx, [(970, 1415), (1400, 1330), (1400, 1560)]); src(ctx, "#ffe9a8", 0.25); ctx.fill()


def s_home_interior(ctx, rng, t):
    room(ctx, wall="#c99a8a", floor="#9b7a6a", ceil="#4a3040", bricks=False, tiles=False,
         window=(600, 560, 230, 200))
    for x in (580, 850):
        rrect(ctx, x - 20, 540, 40, 260, 10); fill(ctx, "#8a3b4a", ow=4)
    frame_pic(ctx, 330, 700, 170, 140)
    rrect(ctx, 180, 1100, 330, 150, 30); fill(ctx, "#5b6fae")
    rrect(ctx, 160, 1060, 60, 190, 26); fill(ctx, "#4c5e98")
    rrect(ctx, 470, 1060, 60, 190, 26); fill(ctx, "#4c5e98")
    ellipse(ctx, 540, 1560, 360, 70); fill(ctx, "#b5525e", a=0.7, ow=0)
    ctx.move_to(880, 1270); ctx.line_to(880, 980); stroke(ctx, 8)
    poly(ctx, [(840, 980), (920, 980), (900, 930), (860, 930)]); fill(ctx, "#ffe27a")


def s_shop(ctx, rng, t):
    room(ctx, wall="#e0c58a", floor="#b7a28a", ceil="#4a3a40", bricks=False)
    for sy in (600, 760, 920):
        ctx.rectangle(180, sy, 720, 18); fill(ctx, "#8a5a3b", ow=4)
        rnd = random.Random(rng + sy)
        for i in range(10):
            c = ["#d1584a", "#3f7fd1", "#4aa36b", "#f2c14e", "#8a5ac9"][rnd.randint(0, 4)]
            rrect(ctx, 200 + i * 70, sy - 80, 50, 80, 6); fill(ctx, c, ow=3)
    ctx.rectangle(150, 1120, 780, 150); fill(ctx, "#8a5a3b")
    rrect(ctx, 760, 1050, 120, 70, 8); fill(ctx, "#3a3552")


def s_map(ctx, rng, t):
    ctx.rectangle(-900, -900, W + 1800, H + 1800); fill(ctx, "#3c4a7a", ow=0)
    rrect(ctx, 60, 330, 960, 1060, 20); fill(ctx, "#f2e6c4", ow=8)
    rnd = random.Random(rng)
    ctx.move_to(60, 900)
    for x in range(60, 1021, 60):
        ctx.line_to(x, 900 + math.sin(x / 90 + rng) * 60)
    stroke(ctx, 26, "#8fc3ff")
    for i in range(6):
        ctx.move_to(60 + rnd.randint(0, 300), 330 + rnd.randint(0, 1060))
        ctx.curve_to(rnd.randint(200, 900), rnd.randint(330, 1390), rnd.randint(200, 900),
                     rnd.randint(330, 1390), 1020 - rnd.randint(0, 300), 330 + rnd.randint(0, 1060))
    stroke(ctx, 10, "#ffffff")
    for i in range(5):
        ellipse(ctx, rnd.randint(150, 930), rnd.randint(420, 1300), rnd.randint(40, 90), rnd.randint(30, 70))
        fill(ctx, "#a9d39a", ow=0)
    ground(ctx, 1450, c="#3c4a7a", walk="#59679a")


SETS = {
    "office":         {"bg": s_office, "fg": None, "floor": 1620},
    "incident_room":  {"bg": s_incident_room, "fg": None, "floor": 1640},
    "interview_room": {"bg": s_interview_room, "fg": None, "floor": 1640},
    "courtroom":      {"bg": s_courtroom, "fg": None, "mid": fg_courtroom, "floor": 1680},
    "cell":           {"bg": s_cell, "fg": fg_cell, "floor": 1600},
    "lab":            {"bg": s_lab, "fg": None, "floor": 1640},
    "newsroom":       {"bg": s_newsroom, "fg": None, "floor": 1640},
    "street_night":   {"bg": s_street_night, "fg": None, "floor": 1600},
    "street_day":     {"bg": s_street_day, "fg": None, "floor": 1600},
    "house":          {"bg": s_house, "fg": None, "floor": 1620},
    "woods":          {"bg": s_woods, "fg": None, "floor": 1620},
    "road_night":     {"bg": s_road_night, "fg": None, "floor": 1640},
    "home_interior":  {"bg": s_home_interior, "fg": None, "floor": 1640},
    "shop":           {"bg": s_shop, "fg": None, "floor": 1640},
    "map":            {"bg": s_map, "fg": None, "floor": 1700},
}
