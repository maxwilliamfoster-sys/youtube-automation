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

from .kit import (W, H, SafeCtx, back, card, clamp, draw_icon, eio, fit_size, lerp, p, person, src, stamp,
                  text_width, vignette)
from .layout import active, overlap
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
STAMP_SLOTS = [STAMP_SLOT, (540, 905)]
_MEASURE = None


def _measure():
    global _MEASURE
    if _MEASURE is None:
        _MEASURE = SafeCtx(cairo.ImageSurface(cairo.FORMAT_ARGB32, 4, 4))
    return _MEASURE


def prop_box(pr, slot):
    """Screen box a pop-up covers once fully in (its drop shadow and bob included)."""
    x, y = slot
    if pr["kind"] == "icon":
        r = 118 * (0.8 if y < 700 else 0.64) + 6
        return (x - r, y - r - 6, x + r + 6, y + r + 16)
    ctx = _measure()
    if pr["kind"] == "label":
        size = fit_size(ctx, pr["value"], 860 - 70, 66)
        w, h = text_width(ctx, pr["value"], size) + 70, size * 1.55
        return (x - w / 2 - 4, y - h / 2 - 10, x + w / 2 + 10, y + h / 2 + 16)
    size = fit_size(ctx, pr["value"], 900 - 80, 96)
    w, h = text_width(ctx, pr["value"], size) + 74, size * 1.44 + 14
    bw, bh = w * math.cos(0.14) + h * math.sin(0.14), w * math.sin(0.14) + h * math.cos(0.14)
    return (x - bw / 2, y - bh / 2 - 6, x + bw / 2, y + bh / 2 + 6)


def _layout_props(props):
    """Slots chosen by prepare(); a plan rendered without it gets the first free slots."""
    if all("slot" in pr for pr in props):
        return [(pr, tuple(pr["slot"])) for pr in props if pr["slot"]]
    return [(pr, s) for pr, s in _assign_slots(props, [], 1e9)]


def _assign_slots(props, zones, t_end):
    """First slot for each pop-up that keeps it off the text shown while it is up and off
    the pop-ups already placed. A pop-up with no clear slot takes the first slot not
    covered by another pop-up (cartoon.qc then reports the clash); one with neither is dropped."""
    out, boxes, stamped = [], [], False
    for pr in props:
        if pr["kind"] == "stamp":
            if stamped:
                continue
            stamped, cands = True, STAMP_SLOTS
        else:
            cands = LABEL_SLOTS if pr["kind"] == "label" else ICON_SLOTS
        text = [z["box"] for z in active(zones, pr["t"], t_end)]
        free = [c for c in cands if not any(overlap(prop_box(pr, c), b) for b in boxes)]
        clear = [c for c in free if not any(overlap(prop_box(pr, c), b) for b in text)]
        pick = (clear or free or [None])[0]
        if pick:
            out.append((pr, pick))
            boxes.append(prop_box(pr, pick))
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


# ---------- keeping faces and pop-ups clear of the burned-in text ----------
# Head box around the head centre in units of the actor's scale: the face (R=88) plus
# hats, wigs and hair, so the camera keeps a margin. cartoon.qc checks the face alone.
HEAD_SAFE = (-112, -140, 112, 100)
FACE_R = 88
FACE_MARGIN = (-96, -100, 96, 96)
SAFE_Y = (150, 1560)        # heads stay below the status bar and above TikTok's bottom UI
# Parts of a set that carry meaning (world boxes), kept clear like faces.
SET_KEYS = {"newsroom": [("the NEWS screen", (300, 600, 780, 780))]}
CAM_SHIFTS = sorted(range(-360, 361, 8), key=abs)
ZOOM_CAPS = (None, 1.15, 1.08, 1.02)


def _scene_at(plan, t):
    scenes = plan["scenes"]
    sc = next((s for s in scenes if s["t0"] <= t < s["t1"]), scenes[-1])
    return sc, scenes.index(sc)


def _poses(sc, t, seed):
    floor = SETS[sc["set"]]["floor"]
    return [_actor_kwargs(a, sc, t, floor, (seed + zlib.crc32(f"{a['role']}{a['spot']}".encode())) % 97)
            for a in sc["actors"]]


def _head_world(x, y, s, box=HEAD_SAFE):
    cy = y - 422 * s                      # person(): feet at y, head centre LEG + 150 + 72 above
    return (x + box[0] * s, cy + box[1] * s, x + box[2] * s, cy + box[3] * s)


def _to_screen(b, cam):
    z, cx, cy = cam
    return ((b[0] - cx) * z + W / 2, (b[1] - cy) * z + H / 2, (b[2] - cx) * z + W / 2, (b[3] - cy) * z + H / 2)


def _fixed(cam, fix):
    z, cx, cy = cam
    d, zc = fix or (0, None)
    if zc and z > zc:
        z = zc
    return z, cx, cy - d / z


def _camera_at(sc, t, poses):
    return _fixed(_camera(sc, t, [(x, y) for x, y, _ in poses]), sc.get("cam_fix"))


def _clash(cam, heads, avoid):
    """Area of heads covered by the avoid boxes, plus any head pushed off the safe height."""
    cost = 0.0
    for hb in heads:
        sb = _to_screen(hb, cam)
        if sb[2] < 0 or sb[0] > W:        # walked off screen
            continue
        if sb[1] < SAFE_Y[0] or sb[3] > SAFE_Y[1]:
            cost += 1e4
        for a in avoid:
            cost += overlap(sb, a)
    return cost


def _plan_camera(sc, zones, seed):
    """One vertical offset (and, if that is not enough, a zoom cap) for the whole scene,
    the smallest that keeps every head clear of the text and the pop-ups at every moment.
    Tries the roomy head box (hats, hair) first, then the face with a small margin."""
    dur = sc["t1"] - sc["t0"]
    n = max(2, min(60, int(dur / 0.1)))
    cands = sorted(((d, zc) for zc in ZOOM_CAPS for d in CAM_SHIFTS),
                   key=lambda c: abs(c[0]) + 260 * ZOOM_CAPS.index(c[1]))
    best = (0, None)
    for head_box in (HEAD_SAFE, FACE_MARGIN):
        samples = []
        for i in range(n + 1):
            t = sc["t0"] + dur * i / n
            poses = _poses(sc, t, seed)
            heads = [_head_world(x, y, kw["s"], head_box) for x, y, kw in poses]
            heads += [b for _, b in SET_KEYS.get(sc["set"], [])]
            avoid = [z["box"] for z in active(zones, t)]
            avoid += [prop_box(pr, pr["slot"]) for pr in sc["props"] if pr.get("slot") and t >= pr["t"] - 0.05]
            samples.append((_camera(sc, t, [(x, y) for x, y, _ in poses]), heads, avoid))
        best_cost = None
        for fix in cands:
            cost = 0.0
            for cam, heads, avoid in samples:
                cost += _clash(_fixed(cam, fix), heads, avoid)
                if best_cost is not None and cost >= best_cost:
                    break
            if cost == 0:
                return list(fix)
            if best_cost is None or cost < best_cost:
                best, best_cost = fix, cost
    return list(best)


def prepare(plan, zones, seed):
    """Fix each scene's pop-up slots and camera offset against the text zones. Stored on
    the plan (pr["slot"], sc["cam_fix"]), so draw_frame and cartoon.qc see the same layout."""
    plan["zones"], plan["seed"] = zones, seed
    for sc in plan["scenes"]:
        placed = dict((id(pr), s) for pr, s in _assign_slots(sc["props"], zones, sc["t1"]))
        for pr in sc["props"]:
            pr["slot"] = list(placed[id(pr)]) if id(pr) in placed else None
        sc["cam_fix"] = _plan_camera(sc, zones, seed)
    return plan


def important_boxes(plan, t, seed=None):
    """What a viewer must be able to see at time t, as (what, screen box): every face on
    screen, the set's key items and every pop-up that is fully in."""
    seed = plan.get("seed", 0) if seed is None else seed
    sc, _ = _scene_at(plan, t)
    poses = _poses(sc, t, seed)
    cam = _camera_at(sc, t, poses)
    out = []
    for (x, y, kw), a in zip(poses, sc["actors"]):
        fb = _to_screen(_head_world(x, y, kw["s"], (-FACE_R, -FACE_R, FACE_R, FACE_R)), cam)
        if fb[2] > 0 and fb[0] < W:
            out.append((f"the {a['role'].replace('_', ' ')}'s face", fb))
    out += [(name, _to_screen(b, cam)) for name, b in SET_KEYS.get(sc["set"], [])]
    for pr, slot in _layout_props(sc["props"]):
        if t >= pr["t"] + 0.3:
            out.append((f"the '{pr['value']}' pop-up", prop_box(pr, slot)))
    return out


# ---------- frames ----------
def draw_frame(ctx, plan, t, seed):
    sc, idx = _scene_at(plan, t)
    spec = SETS[sc["set"]]
    src(ctx, "#1c1838")
    ctx.paint()
    poses = _poses(sc, t, seed)
    z, cx, cy = _camera_at(sc, t, poses)
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


def render(plan, out_path, seed_text="", fps=FPS, log=print, zones=None):
    """Silent background MP4 covering the narration + 0.6 s. zones (cartoon.layout.text_zones)
    are the captions and cards the composer will burn in; faces and pop-ups are kept off them."""
    seed = zlib.crc32(seed_text.encode()) % 10000
    prepare(plan, zones or [], seed)
    moved = sum(1 for sc in plan["scenes"] if sc["cam_fix"] != [0, None])
    if moved:
        log(f"[Cartoon] camera moved in {moved} scene(s) to keep faces clear of the text")
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
