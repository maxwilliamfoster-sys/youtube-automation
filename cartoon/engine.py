"""Render a timed cartoon plan (cartoon.planner.plan) to a silent 1080x1920 MP4.

Sets and actors live in world space under a gliding camera; pop-ups (icons,
labels, stamps) are drawn in screen space so they always land inside the TikTok
safe area: between the hook card (top 13%) and the captions (38%), and between
the captions and the actors' heads. Captions, hook/CTA cards, music and the
narration are added afterwards by documentary_composer.add_audio_and_captions.
"""
import math
import os
import shutil
import subprocess
import zlib

import cairo

from .kit import (W, H, SafeCtx, back, card, clamp, draw_icon, eio, lerp, p, person, src, stamp, vignette)
from .sets import SETS

FPS = 30
SPOT_X = {"left": 270, "center": 540, "right": 810}
ACTOR_SCALE = 0.95
# pop-up slots in SCREEN space
STAMP_SLOT = (540, 470)
ICON_SLOTS = [(250, 905), (830, 905), (270, 470), (810, 470)]
LABEL_SLOTS = [(540, 905), (540, 470)]
CAM_Y = 1010  # world y at screen centre: actors' heads land just under the caption line
STAMP_COLOURS = {"MISSING": "#e0413b", "UNSOLVED": "#7b4fd1", "COLD CASE": "#3b5fa8", "REOPENED": "#2f9e5a",
                 "CASE CLOSED": "#2f9e5a", "MATCH": "#2f9e5a", "EVIDENCE": "#c9921f", "NOT GUILTY": "#2f9e5a",
                 "ACQUITTED": "#2f9e5a", "NO CHARGES": "#3b5fa8", "BREAKING NEWS": "#e0413b", "APPEAL": "#c9921f",
                 "TOP SECRET": "#e0413b", "CHARGED": "#c9921f"}


def _ffmpeg():
    return shutil.which("ffmpeg") or "C:/ffmpeg/ffmpeg-8.1.1-essentials_build/bin/ffmpeg.exe"


# ---------- camera ----------
def _camera(sc, t, actors_pos):
    u = clamp((t - sc["t0"]) / max(0.1, sc["t1"] - sc["t0"]))
    k = eio(u)
    kind = sc["camera"]
    cy = CAM_Y if sc["set"] != "map" else 960
    if kind == "push_in":
        z, cx, cyy = lerp(1.03, 1.2, k), 540, lerp(cy, cy + 30, k)
    elif kind == "pull_out":
        z, cx, cyy = lerp(1.24, 1.04, k), 540, cy
    elif kind == "pan_left":
        z, cx, cyy = 1.14, lerp(620, 460, k), cy
    elif kind == "pan_right":
        z, cx, cyy = 1.14, lerp(460, 620, k), cy
    elif kind == "closeup" and actors_pos:
        ax, ay = actors_pos[0]
        kk = eio(clamp((u - 0.35) / 0.4))  # hold the close-up, then ease out
        z, cx, cyy = lerp(1.8, 1.1, kk), lerp(ax, 540, kk), lerp(ay - 470, cy, kk)
    else:
        z, cx, cyy = 1.08, 540, cy
    punch = 1 + 0.05 * (1 - eio(clamp((t - sc["t0"]) / 0.22)))  # small punch-in on every cut
    return z * punch, cx + math.sin(t * 0.7) * 6, cyy + math.cos(t * 0.5) * 5


# ---------- actors ----------
def _actor_kwargs(a, sc, t, floor, tint):
    """Pose for one actor at time t (all motion keyed to its cue time a['t'])."""
    spot = a["spot"]
    tc = a["t"]
    act = a["action"]
    if spot == "bench":
        x, y, s, face = 540, 1260, 0.82, 1
    else:
        x, y, s = SPOT_X.get(spot, 540), floor, ACTOR_SCALE
        face = 1 if spot == "left" else -1 if spot == "right" else (1 if tint % 2 else -1)
    kw = dict(role=a["role"], s=s, face=face, mood=a["mood"], t=t, tint=tint, hold=a.get("hold"))
    react_moods = {"shocked", "happy", "sad", "angry"}
    if act == "react" or (a["mood"] in react_moods and act not in ("talk",)):
        if t < tc:
            kw["mood"] = "neutral"
    if act == "walk_in":
        start_x = -260 if spot != "right" else W + 260
        t_start = min(tc, sc["t0"] + 0.1)
        t_end = max(tc, t_start + 0.9)
        k = clamp((t - t_start) / (t_end - t_start))
        x = lerp(start_x, x, eio(k))
        if 0 < k < 1:
            kw["walk"] = t * 9
        kw["face"] = 1 if start_x < 0 else -1
    elif act == "walk_out":
        k = clamp((t - tc) / 1.3)
        end_x = -300 if spot == "left" else W + 300
        if k > 0:
            kw["face"] = -1 if end_x < 0 else 1
            if k < 1:
                kw["walk"] = t * 9
        x = lerp(x, end_x, eio(k))
    elif act == "point":
        kw["ra"] = lerp(12, 88, p(t, tc, 0.3, back))
    elif act == "shrug":
        k = p(t, tc, 0.3, back)
        kw["ra"] = kw["la"] = lerp(12, 150, k)
    elif act == "talk":
        kw["talk"] = 1.0 if tc <= t < tc + 1.8 else 0.0
        kw["ra"] = 25 + 15 * math.sin(t * 5)
    elif act == "look_up":
        kw["look"] = (0, -lerp(0, 1, p(t, tc, 0.3)))
    elif act == "hold":
        kw["ra"] = lerp(45, 100, p(t, tc, 0.3, back))
    elif act == "phone":
        kw["ra"], kw["hold"] = 165, "phone"
        kw["talk"] = 1.0 if t >= tc else 0.0
    elif act == "slam":
        kw["hold"] = kw.get("hold") or "gavel"
        kw["ra"] = 150 if t < tc - 0.15 else lerp(150, 70, p(t, tc - 0.15, 0.15))
    elif act == "write":
        kw["ra"] = 60 + 12 * math.sin(t * 14) if t >= tc else 40
    elif act == "nod":
        kw["tilt"] = 0.12 * math.sin(t * 8) if tc <= t < tc + 1.2 else 0.0
    return x, y, kw


# ---------- props ----------
def _layout_props(props):
    out, icons, labels, stamped = [], list(ICON_SLOTS), list(LABEL_SLOTS), False
    for pr in props:
        if pr["kind"] == "stamp" and not stamped:
            stamped = True
            out.append((pr, STAMP_SLOT))
            if (540, 470) in labels:
                labels.remove((540, 470))
            icons = [s for s in icons if s[1] != 470]
        elif pr["kind"] == "label" and labels:
            out.append((pr, labels.pop(0)))
        elif pr["kind"] == "icon" and icons:
            out.append((pr, icons.pop(0)))
    return out


def _draw_prop(ctx, pr, slot, t):
    k = p(t, pr["t"] - 0.05, 0.32)
    if k <= 0:
        return
    x, y = slot
    y += math.sin(t * 2.4 + x) * 6
    if pr["kind"] == "stamp":
        stamp(ctx, pr["value"], x, y, k, color=STAMP_COLOURS.get(pr["value"], "#e0413b"), size=96)
    elif pr["kind"] == "label":
        card(ctx, pr["value"], x, y, k, size=66)
    else:
        s = 0.8 if y < 700 else 0.64
        draw_icon(ctx, pr["value"], x, y, s * back(k), t=t, rot=math.sin(t * 1.7 + x) * 0.05)


# ---------- frames ----------
def draw_frame(ctx, plan, t, seed):
    scenes = plan["scenes"]
    sc = next((s for s in scenes if s["t0"] <= t < s["t1"]), scenes[-1])
    idx = scenes.index(sc)
    spec = SETS[sc["set"]]
    floor = spec["floor"]
    src(ctx, "#1c1838")
    ctx.paint()
    poses = []
    for j, a in enumerate(sc["actors"]):
        tint = (seed + zlib.crc32(f"{a['role']}{a['spot']}".encode())) % 97
        poses.append(_actor_kwargs(a, sc, t, floor, tint))
    z, cx, cy = _camera(sc, t, [(x, y) for x, y, _ in poses])
    ctx.save()
    ctx.translate(W / 2, H / 2)
    ctx.scale(z, z)
    ctx.translate(-cx, -cy)
    spec["bg"](ctx, seed + idx, t)
    # back-to-front: the bench judge first, then by spot
    # the bench judge, then the set's middle layer (the bench), then everyone else, then the front layer (bars)
    order = sorted(range(len(poses)), key=lambda j: (sc["actors"][j]["spot"] != "bench", poses[j][1]))
    mid_done = False
    for j in order:
        if not mid_done and sc["actors"][j]["spot"] != "bench":
            if spec.get("mid"):
                spec["mid"](ctx, seed + idx, t)
            mid_done = True
        x, y, kw = poses[j]
        role = kw.pop("role")
        person(ctx, x, y, role=role, **kw)
    if not mid_done and spec.get("mid"):
        spec["mid"](ctx, seed + idx, t)
    if spec["fg"]:
        spec["fg"](ctx, seed + idx, t)
    ctx.restore()
    vignette(ctx, 0.45)
    for pr, slot in _layout_props(sc["props"]):
        _draw_prop(ctx, pr, slot, t)


def render(plan, out_path, seed_text="", fps=FPS, log=print):
    """Silent background MP4 covering the narration + 0.6 s."""
    seed = zlib.crc32(seed_text.encode()) % 10000
    n = int(math.ceil(plan["scenes"][-1]["t1"] * fps))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    cmd = [_ffmpeg(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{W}x{H}",
           "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
           "-pix_fmt", "yuv420p", os.path.abspath(out_path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    try:
        for i in range(n):
            ctx = SafeCtx(surf)
            draw_frame(ctx, plan, i / fps, seed)
            surf.flush()
            proc.stdin.write(bytes(surf.get_data()))
            if i % (fps * 15) == 0:
                log(f"[Cartoon] frame {i}/{n}")
    finally:
        proc.stdin.close()
        proc.wait()
    if proc.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError("cartoon render: ffmpeg failed")
    log(f"[Cartoon] rendered {n} frames -> {out_path}")
    return out_path


def still(plan, t, png_path, seed_text=""):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    draw_frame(SafeCtx(surf), plan, t, zlib.crc32(seed_text.encode()) % 10000)
    surf.write_to_png(png_path)
    return png_path
