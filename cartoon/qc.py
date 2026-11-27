"""The cartoon gate: checks a finished cartoon video before the upload gate sees it.

1. Text over the picture: no burned-in text (word captions, hook card, case tag, end
   card) covers a face, a set's key item (the NEWS screen) or a pop-up, and no pop-up
   covers a face. Measured frame by frame (every 0.1 s) from the same layout the engine
   drew, against the same text positions the composer uses (cartoon.layout).
2. Picture matches the narration: the finished video is transcribed again (Whisper,
   independent of the narrator's own timings) and every scene must be on screen while
   its sentence is being spoken, and every pop-up and gesture must land on its word.
3. Content matches the sentence: each pop-up says or shows something from the sentence
   on screen at the time, and nobody the script does not call convicted is drawn as a
   villain.

    result = qc.check(plan, video_path)   # {"ok": bool, "failures": [...], "stats": {...}}
"""
import re

from . import engine, planner
from .layout import active, area, overlap

STEP = 0.1              # seconds between checked frames
COVER_MAX = 0.10        # share of a face / key item / pop-up that text may cover
POPUP_OVER_FACE = 0.25  # share of a face a pop-up may cover
MIN_CLASH_S = 0.25      # shorter clashes are a pop-in overshoot, not a problem
COVERAGE_MIN = 0.5      # share of the script Whisper must find before timing is judged
SCENE_SYNC_MIN = 0.7    # share of a scene's heard words spoken while it is on screen
SYNC_SLACK = 0.35       # a word this close to a scene change may sit either side
START_SLACK = 0.7       # a scene may start this far AFTER its first spoken word...
EARLY_SLACK = 1.2       # ...or this far before it (it shows in the pause before the sentence; a
                        # 0.8 s early start blocked a fine video in run 44585277817)
CUE_SLACK = 0.6         # a pop-up or gesture may land this far from its word
MOVING_ACTIONS = {"walk_in", "walk_out", "point", "shrug", "look_up", "hold", "phone", "slam", "write", "nod",
                  "react"}


def _runs(times):
    """Consecutive sampled times -> [(start, end)]."""
    runs = []
    for t in times:
        if runs and t - runs[-1][1] <= STEP * 1.5:
            runs[-1][1] = t
        else:
            runs.append([t, t])
    return [(a, b + STEP) for a, b in runs]


def check_layout(plan):
    """Failures where text covers something important for MIN_CLASH_S or longer."""
    zones = plan.get("zones") or []
    clashes = {}
    t, end = 0.0, plan["scenes"][-1]["t1"]
    frames = 0
    while t < end:
        frames += 1
        boxes = engine.important_boxes(plan, t)
        for what, b in boxes:
            for z in active(zones, t):
                if overlap(b, z["box"]) > COVER_MAX * max(1.0, area(b)):
                    clashes.setdefault((what, f"the {z['name']}"), []).append(t)
        faces = [(w, b) for w, b in boxes if w.endswith("face")]
        for w, b in boxes:
            if "pop-up" in w:
                for fw, fb in faces:
                    if overlap(b, fb) > POPUP_OVER_FACE * max(1.0, area(fb)):
                        clashes.setdefault((fw, w), []).append(t)
        t = round(t + STEP, 3)
    failures = []
    for (what, by), times in clashes.items():
        for a, b in _runs(times):
            if b - a >= MIN_CLASH_S:
                failures.append(f"{by} covers {what} at {a:.1f}-{b:.1f} s")
    return failures, {"frames_checked": frames}


def check_sync(plan, heard_words):
    """Failures where a scene, pop-up or gesture is out of step with the spoken words."""
    toks = plan["tokens"]
    scenes = plan["scenes"]
    heard = planner.match_times(toks, heard_words)
    content = [i for i, tok in enumerate(toks) if planner.norm(tok)]
    found = [i for i in content if heard[i] is not None]
    coverage = len(found) / max(1, len(content))
    stats = {"coverage": round(coverage, 2)}
    if coverage < COVERAGE_MIN:
        return [f"only {coverage:.0%} of the script was heard in the finished video, so the picture "
                "could not be checked against the narration"], stats

    def scene_index(t):
        return next((k for k, s in enumerate(scenes) if s["t0"] <= t < s["t1"]), len(scenes) - 1)

    failures, worst = [], 1.0
    for k, sc in enumerate(scenes):
        a, b = sc["span"]
        said = [heard[i] for i in range(a, b) if heard[i] is not None]
        quote = " ".join(toks[a:min(b, a + 6)]) + ("..." if b - a > 6 else "")
        if said:
            ok = sum(1 for w in said if scene_index(w) == k
                     or sc["t0"] - SYNC_SLACK <= w < sc["t1"] + SYNC_SLACK)
            share = ok / len(said)
            worst = min(worst, share)
            if len(said) >= 3 and share < SCENE_SYNC_MIN:
                failures.append(f"scene {k + 1} ('{quote}') is on screen at {sc['t0']:.1f}-{sc['t1']:.1f} s "
                                f"but the narrator says it at {min(said):.1f}-{max(said):.1f} s")
        gap = sc["t0"] - heard[a] if heard[a] is not None else 0.0
        if k and (gap > START_SLACK or -gap > EARLY_SLACK):
            failures.append(f"scene {k + 1} ('{quote}') starts {abs(gap):.1f} s "
                            f"{'after' if gap > 0 else 'before'} its first word")
        for item in sc["props"] + sc["actors"]:
            cue = item.get("cue")
            if cue is None or cue >= len(heard) or heard[cue] is None:
                continue
            if "kind" in item:
                if item.get("slot") is None and "slot" in item:
                    continue                # dropped pop-up: never drawn
                what = f"the '{item['value']}' pop-up"
            elif item.get("action") in MOVING_ACTIONS:
                what = f"the {item['role'].replace('_', ' ')}'s '{item['action']}'"
            else:
                continue
            gap = item["t"] - heard[cue]
            if abs(gap) > CUE_SLACK:
                failures.append(f"{what} comes {abs(gap):.1f} s {'after' if gap > 0 else 'before'} "
                                f"'{toks[cue].strip('.,!?;:')}' is said")
    stats["worst_scene_sync"] = round(worst, 2)
    return failures, stats


def check_content(plan):
    """Failures where a pop-up has nothing to do with the sentence it appears on, or
    someone not convicted is drawn as a villain."""
    toks = plan["tokens"]
    script = " ".join(toks)
    convicted = plan.get("convicted", bool(planner.CONVICTED_RE.search(script)))
    failures = []
    for k, sc in enumerate(plan["scenes"]):
        sent = sc["text"]
        flat = f" {planner._flat(sent)} "
        named = planner.strong_set(sent)
        if named and sc["set"] in planner.GENERIC_SETS:
            failures.append(f"scene {k + 1} is set in the {sc['set'].replace('_', ' ')} but the narrator is "
                            f"talking about a {named.replace('_', ' ')} scene ('{sent[:60]}')")
        for pr in sc["props"]:
            if pr.get("slot") is None and "slot" in pr:
                continue
            v = pr["value"]
            if pr["kind"] == "label" and f" {planner._flat(v)} " not in flat:
                failures.append(f"scene {k + 1}: the label '{v}' is not in the sentence it appears on")
            elif pr["kind"] == "icon" and not planner.icon_fits(v, sent):
                failures.append(f"scene {k + 1}: the {v} icon has nothing to do with '{sent[:60]}'")
            elif pr["kind"] == "stamp" and not (v in planner.STAMPS and re.search(planner.STAMPS[v], sent, re.I)):
                failures.append(f"scene {k + 1}: the {v} stamp is not backed by '{sent[:60]}'")
        if planner.risky_sentence(sent, script, convicted):
            for a in sc["actors"]:
                if a["role"] in ("shadow", "convict"):
                    failures.append(f"scene {k + 1}: a {a['role']} figure on a sentence about someone not convicted")
    return failures


def transcribe(video_path, model="small"):
    """Words heard in the finished video, with start times (faster-whisper).
    "small", the model tts_generator times the plan with (2026-11-27). With "tiny" the gate's clock drifted up
    to ~1 s from the plan's, so in-step videos failed ('pop-up comes 0.7 s before', 'only 22% heard')."""
    from faster_whisper import WhisperModel
    m = WhisperModel(model, device="cpu", compute_type="int8")
    segments, _ = m.transcribe(video_path, language="en", word_timestamps=True, vad_filter=False)
    return [{"word": w.word.strip(), "start": float(w.start), "end": float(w.end)}
            for seg in segments for w in (seg.words or [])]


def check(plan, video_path=None, heard_words=None, log=print):
    """All three checks. Fails closed: a check that cannot run is a failure."""
    failures, stats = [], {}
    try:
        f, s = check_layout(plan)
        failures += f
        stats.update(s)
    except Exception as e:
        failures.append(f"text-over-picture check could not run ({str(e)[:120]})")
    try:
        if heard_words is None:
            heard_words = transcribe(video_path)
        f, s = check_sync(plan, heard_words)
        failures += f
        stats.update(s)
    except Exception as e:
        failures.append(f"picture-to-narration timing check could not run ({str(e)[:120]})")
    try:
        failures += check_content(plan)
    except Exception as e:
        failures.append(f"pop-up content check could not run ({str(e)[:120]})")
    ok = not failures
    log(f"[CartoonQC] {'PASS' if ok else 'FAIL'}: {stats}" + "".join(f"\n    ! {x}" for x in failures[:8]))
    return {"ok": ok, "failures": failures, "stats": stats}
