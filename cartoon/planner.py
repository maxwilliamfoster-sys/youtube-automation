"""Turn a narrated script into a timed cartoon scene plan.

1. split the script into sentences (one scene each; tiny ones merge)
2. line the script's words up with the narrator's word timings
3. ask the free LLM chain for a scene per sentence (set, actors, pop-ups, cue words)
4. validate everything against fixed vocabularies and the legal rules; any sentence
   the LLM got wrong (or every sentence, if no LLM answers) gets a rule-based scene

Legal rules (BuriedCasefiles): on-screen text may only repeat words from the script;
nobody who is only suspected/arrested/charged/cleared is drawn as a villain; the
faceless "shadow" is reserved for an unidentified culprit or someone convicted;
"convict" (orange) only when the script states a conviction. No violence, weapons,
injuries or bodies exist in the vocabulary at all, and children are never drawn.
"""
import json
import re

from .kit import ICONS, MOODS, ROLES
from .sets import SETS

ACTIONS = {"stand", "walk_in", "walk_out", "point", "shrug", "talk", "look_up", "hold", "phone",
           "slam", "write", "nod", "react"}
SPOTS = ("left", "right", "center")
CAMERAS = {"static", "push_in", "pull_out", "pan_left", "pan_right", "closeup"}

CONVICTED_RE = re.compile(r"\b(convicted|found guilty|pleaded guilty|pled guilty|admitted (?:killing|murder|the)|"
                          r"sentenced|jailed|imprisoned|life sentence|life in prison|confessed)\b", re.I)
SUSPECT_RE = re.compile(r"\b(suspect\w*|arrest\w*|charged|question\w*|accused|released|cleared|acquitted|"
                        r"denied|denies|trial|alleged\w*|person of interest|interviewed)\b", re.I)

# Stamps that name a person's guilt are never drawn: the AI review blocked a cartoon on
# 2026-11-19 (run 44544915448) for a GUILTY stamp even though the script stated the plea.
# The court outcome shows as a neutral CASE CLOSED instead; CHARGED (not convicted) is dropped.
STAMP_SWAP = {"GUILTY": "CASE CLOSED", "GUILTY PLEA": "CASE CLOSED", "CONVICTED": "CASE CLOSED",
              "LIFE SENTENCE": "CASE CLOSED", "JAILED": "CASE CLOSED"}

# stamp text -> the script must match this before the stamp may appear
STAMPS = {
    "MISSING": r"\b(missing|disappear\w*|vanish\w*)\b",
    "UNSOLVED": r"\b(unsolved|never (?:been )?solved|never (?:been )?found|no one (?:has ever been|was ever)|"
                r"nobody (?:has ever been|was ever)|remains? a mystery|cold case)\b",
    "COLD CASE": r"\b(cold case|unsolved|years later|decades later|reopened)\b",
    "REOPENED": r"\b(reopen\w*|re-open\w*|new investigation|fresh appeal)\b",
    "CASE CLOSED": r"\b(convicted|sentenced|solved|case closed|jailed|imprisoned|found guilty|pleaded guilty|"
                   r"pled guilty|guilty verdict|life in prison)\b",
    "NOT GUILTY": r"\b(not guilty|acquitted)\b",
    "ACQUITTED": r"\b(acquitted)\b",
    "NO CHARGES": r"\b(never charged|no charges|not charged|released without charge)\b",
    "MATCH": r"\b(match\w*|identified)\b",
    "EVIDENCE": r"\b(evidence|clue\w*|found|discovered)\b",
    "BREAKING NEWS": r"\b(news|headline\w*|reported|press|appeal)\b",
    "APPEAL": r"\b(appeal\w*)\b",
    "TOP SECRET": r"\b(secret\w*|classified|hidden)\b",
}
CLEARED_RE = re.compile(r"\b(released|cleared|acquitted|not guilty|no charges|never charged|without charge|"
                        r"innocent|exonerat\w*|wrongly|overturned|quashed)\b", re.I)
VILLAIN_MOODS = {"smug", "angry"}


def risky_sentence(sentence, script, convicted):
    """True when a sentence is about someone who must not look guilty: a suspect in a case
    with no conviction (or where someone was cleared), or anyone released/cleared/acquitted."""
    if CLEARED_RE.search(sentence):
        return True
    about_suspect = SUSPECT_RE.search(sentence) and not CONVICTED_RE.search(sentence)
    return bool(about_suspect and (not convicted or CLEARED_RE.search(script)))
CIVILIANS = ("man", "woman", "elder_man", "elder_woman")


def norm(w):
    return re.sub(r"[^a-z0-9£]", "", w.lower())


def tokens_of(script):
    return [w for w in script.split() if norm(w)]


# ---------- 1. sentences ----------
def split_sentences(script, min_words=5, max_scenes=18):
    """Sentences as (first_token, last_token_exclusive) over tokens_of(script)."""
    toks = tokens_of(script)
    spans, start = [], 0
    for i, w in enumerate(toks):
        if re.search(r"[.!?…]['\"”’)]*$", w) or i == len(toks) - 1:
            spans.append([start, i + 1])
            start = i + 1
    merged = []
    for s in spans:
        if merged and (s[1] - s[0] < min_words or merged[-1][1] - merged[-1][0] < min_words):
            merged[-1][1] = s[1]
        else:
            merged.append(s)
    while len(merged) > max_scenes:
        k = min(range(len(merged) - 1), key=lambda i: merged[i + 1][1] - merged[i][0])
        merged[k][1] = merged[k + 1][1]
        del merged[k + 1]
    return toks, [tuple(s) for s in merged]


# ---------- 2. timing ----------
def match_times(toks, word_timings):
    """Heard start time for each script token, or None where the transcript has no match
    (greedy, in order, looking at most 6 heard words ahead)."""
    times = [None] * len(toks)
    heard = [(norm(w.get("word", "")), float(w.get("start", 0))) for w in (word_timings or []) if norm(w.get("word", ""))]
    j = 0
    for i, tok in enumerate(toks):
        a = norm(tok)
        if not a:
            continue
        for k in range(j, min(j + 6, len(heard))):
            b = heard[k][0]
            if a == b or (len(a) > 2 and len(b) > 2 and (a.startswith(b[:4]) or b.startswith(a[:4]))):
                times[i] = heard[k][1]
                j = k + 1
                break
    return times


def align_times(toks, word_timings, duration):
    """Start time for every script token. Greedy match against the narrator's timed
    words; anything unmatched is interpolated, so a bad transcript never breaks it."""
    n = len(toks)
    times = match_times(toks, word_timings)
    # interpolate the gaps (and the ends) linearly
    known = [(i, t) for i, t in enumerate(times) if t is not None]
    if not known:
        return [duration * i / max(1, n) for i in range(n)]
    if known[0][0] != 0:
        known.insert(0, (0, min(0.1, known[0][1])))
    if known[-1][0] != n - 1:
        known.append((n - 1, max(known[-1][1], duration - 0.4)))
    for (i0, t0), (i1, t1) in zip(known, known[1:]):
        for i in range(i0, i1 + 1):
            if times[i] is None or i in (i0, i1):
                times[i] = t0 + (t1 - t0) * (i - i0) / max(1, i1 - i0)
    # monotonic
    for i in range(1, n):
        times[i] = max(times[i], times[i - 1])
    return times


# ---------- 3. LLM ----------
SYSTEM = """You storyboard a cartoon true-crime short. Each numbered sentence of narration becomes one scene that acts out EXACTLY what that sentence says, at the moment it is said.

Reply ONLY with JSON: {"scenes":[{"s":1,"set":"...","camera":"...","actors":[{"role":"...","spot":"left|right|center","mood":"...","action":"...","cue":"word","hold":"icon or empty"}],"props":[{"icon":"name","cue":"word"},{"label":"exact words","cue":"word"},{"stamp":"TEXT","cue":"word"}]}]}

Vocabulary (use nothing else):
- set: %(sets)s
- role: %(roles)s
- mood: %(moods)s
- action: %(actions)s  (react = mood changes on the cue word; walk_in/walk_out = enters/leaves on the cue)
- camera: %(cameras)s
- icon: %(icons)s
- stamp: %(stamps)s

Rules:
- One scene per sentence, same order, "s" = sentence number. 1-3 actors, 0-2 props.
- "cue" is ONE word copied from THAT sentence: the word on which the action or pop-up happens.
- label = 1-4 words copied EXACTLY from the sentence (a name, place, date, time, age or number). Never invent text.
- Use stamps only when the sentence says it (MISSING when someone goes missing, CASE CLOSED only after a guilty verdict or plea). Never stamp guilt on a person.
- Pick the set where the sentence happens; change set when the story moves; vary the camera.
- NEVER show violence, weapons, injuries, blood or bodies. Victims appear only alive in everyday moments, never harmed. Never draw children: use an icon instead.
- "shadow" = an unidentified culprit, or a person a court has convicted. "convict" = someone already convicted, after the script says so.
- Anyone only suspected, questioned, arrested, charged, tried, released, cleared or acquitted is drawn as man/woman/elder_man/elder_woman with a neutral or worried mood. Never shadow, convict, smug or angry.
- Police and detectives investigate in office, incident_room, interview_room, lab, map. Court results go in courtroom."""


def _llm_plan(title, sentences):
    """Ask the bot's own free LLM chain (Groq primary -> Groq siblings -> Gemini)."""
    from story_generator import _groq_call  # lazy: keeps the renderer importable on its own
    from config import GROQ_API_KEY, GROQ_MODEL
    from groq import Groq
    system = SYSTEM % {
        "sets": ", ".join(s for s in SETS if s not in RETIRED_SETS), "roles": ", ".join(ROLES), "moods": ", ".join(MOODS),
        "actions": ", ".join(sorted(ACTIONS)), "cameras": ", ".join(sorted(CAMERAS)),
        "icons": ", ".join(ICONS), "stamps": ", ".join(STAMPS),
    }
    user = f"Case: {title}\n\n" + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(sentences))
    resp = _groq_call(Groq(api_key=GROQ_API_KEY), model=GROQ_MODEL, temperature=0.4, max_tokens=3500,
                      messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    raw = resp.choices[0].message.content or ""
    m = re.search(r"\{.*\}", raw, re.S)
    data = json.loads(m.group(0)) if m else {}
    return data.get("scenes") or []


# ---------- 4. rules + validation ----------
SET_RULES = [
    (r"\b(court|trial|judge|jury|verdict|sentenc\w*|guilty|acquit\w*|plead\w*)\b", "courtroom"),
    (r"\b(prison|jail\w*|cell|behind bars|inmate)\b", "cell"),
    (r"\b(dna|forensic\w*|lab|sample\w*|post-mortem|autopsy|test\w*)\b", "lab"),
    (r"\b(news\w*|tv|television|reporter\w*|headline\w*|press|journalist\w*)\b", "newsroom"),
    (r"\b(interview\w*|question\w*|interrogat\w*)\b", "interview_room"),
    (r"\b(woods?|forest|field\w*|park|moor\w*|countryside|river|lake|canal|beach|hills?)\b", "woods"),
    (r"\b(car|road|drove|driv\w*|motorway|van|lorry|lay-by)\b", "road_night"),
    (r"\b(shop|store|pub|bar|cafe|restaurant|petrol station|supermarket)\b", "shop"),
    (r"\b(bedroom|kitchen|living room|indoors|inside|sofa)\b", "home_interior"),
    (r"\b(home|house|flat|front door|doorstep|neighbou?r\w*)\b", "street_day"),
    (r"\b(night|street|walk\w*|alley|town centre|bus stop|evening)\b", "street_night"),
    (r"\b(map|miles|village|county|border|town|city)\b", "map"),
    (r"\b(evidence|clue\w*|case|theor\w*|investigat\w*)\b", "incident_room"),
    (r"\b(police|detective\w*|officer\w*|appeal|search\w*)\b", "office"),
]
GENERIC_SETS = ("office", "incident_room")
# Sets never used, and what replaces them. The AI review blocked a cartoon on 2026-11-19
# (run 44527156471) because the drawn house's front door and driveway read as "a real
# private home" under the upload gate's privacy rule.
RETIRED_SETS = {"house": "street_day"}


def strong_set(sentence):
    """The set a sentence clearly names (court, TV station, lab, road...), or None. The
    last two SET_RULES (evidence -> incident_room, police -> office) are only defaults."""
    return _first(SET_RULES[:-2], sentence, None)


ICON_RULES = [
    (r"\b(phone\w*|calls?|rang|texted|text message|voicemail|999)\b", "phone"),
    (r"\b(letter\w*|note\w*|wrote|written|diary)\b", "letter"),
    (r"\b(car|van|drove|driv\w*|lorry|taxi)\b", "car"),
    (r"\b(keys?)\b", "key"),
    (r"\b(dna)\b", "dna"),
    (r"\b(fingerprint\w*)\b", "fingerprint"),
    (r"\b(cctv|camera footage|footage|security camera)\b", "cctv"),
    (r"\b(clock|hours?|minutes?|o'clock|midnight|am|pm)\b", "clock"),
    (r"\b(money|cash|£\S*|pounds|debt\w*|insurance)\b", "money"),
    (r"\b(newspaper\w*|headline\w*)\b", "newspaper"),
    (r"\b(tv|television)\b", "tv"),
    (r"\b(computer|laptop|email\w*|online|internet|facebook|messages?)\b", "computer"),
    (r"\b(bag|handbag|rucksack|backpack|purse)\b", "bag"),
    (r"\b(footprints?|shoe\w*|trainers?)\b", "footprints"),
    (r"\b(ring|wedding|engaged|married)\b", "ring"),
    (r"\b(photo\w*|picture\w*|camera)\b", "camera"),
    (r"\b(bike|bicycle|cycl\w*)\b", "bike"),
    (r"\b(boat|ferry|yacht)\b", "boat"),
    (r"\b(flight|plane|airport|flew)\b", "plane"),
    (r"\b(mystery|unknown|unanswered|why|what happened|never found)\b", "question"),
    (r"\b(clue\w*|evidence|search\w*)\b", "magnifier"),
    (r"\b(date|day|days|weeks?|months?|years? later|anniversary)\b", "calendar"),
    (r"\b(home|house|flat)\b", "house"),
]
# Wider word lists for "does this icon belong to this sentence?" (validator and qc). An icon
# fits when its ICON_RULES pattern or the pattern here matches the sentence.
ICON_EXTRA = {
    "question": r"\?|\b(ask\w*|advice|wonder\w*|puzzl\w*|strange|odd|secret|nobody|no one|unsolved|unexplained)\b",
    "document": r"\b(document\w*|files?|reports?|records?|papers?|disk|forms?|statements?|ad|adverts?|wrote|"
                r"letters?|warrant|notes?|said|says|listed|name)\b",
    "map_pin": r"\b(town|city|village|county|street|road|area|state|country|miles?|where|place)\b",
    "newspaper": r"\b(papers?|ad|adverts?|press|media|reporters?|journalists?|news)\b",
    "letter": r"\b(mail\w*|posted|sent|send|envelope|message)\b",
    "floppy": r"\b(floppy|disks?|discs?)\b",
    "gavel": r"\b(court\w*|trial|judge|jury|guilty|sentenc\w*|pleaded|convicted|acquitted|verdict|charged|prison|"
             r"jail\w*|life)\b",
    "magnifier": r"\b(investigat\w*|detectives?|police|inquir\w*|look\w*|found|discover\w*|trac\w*|clues?|checked)\b",
    "cctv": r"\b(seen|spotted|filmed|recorded|cameras?)\b",
    "clock": r"\b(time|night|morning|evening|late|later|minutes?|hours?|seconds?)\b",
    "calendar": r"\b((?:19|20)\d\d|january|february|march|april|may|june|july|august|september|october|"
                r"november|december|weeks?|months?|years?|days?|later|date|birthday)\b",
    "house": r"\b(home|house|flat|lived|neighbou?r\w*|door|bedroom|kitchen|garden)\b",
    "car": r"\b(vehicle|road|parked|motorway|boot)\b",
    "money": r"\b(paid|pay|stole|stolen|robbery|robbed|bank|wealth\w*|rich)\b",
    "pills": r"\b(pills?|drugs?|overdose|poison\w*|tablets?|medicin\w*|prescri\w*|dose)\b",
    "fingerprint": r"\b(prints?)\b",
    "dna": r"\b(forensic\w*|samples?|blood|hair|genetic\w*|genealog\w*)\b",
    "computer": r"\b(files?|disks?|data|metadata|hard drive|records|saved|software)\b",
    "tv": r"\b(broadcast\w*|news|station\w*|appeal\w*)\b",
    "key": r"\b(lock\w*|door)\b",
    "bulb": r"\b(idea|realis\w*|clues?|breakthrough|discover\w*|twist|noticed|worked out|figured|mistake)\b",
    "camera": r"\b(images?|snapshots?|selfies?)\b",
}


PLACE_NAME_RE = re.compile(r"\b(?:in|near|across|from|at|outside)\s+[A-Z][a-z]+")  # case-sensitive: "in Kansas"


def icon_fits(icon, sentence):
    """True when the sentence says something this icon shows."""
    if icon == "map_pin" and PLACE_NAME_RE.search(sentence):
        return True
    pats = [rx for rx, ic in ICON_RULES if ic == icon] + ([ICON_EXTRA[icon]] if icon in ICON_EXTRA else [])
    return any(re.search(rx, sentence, re.I) for rx in pats)


LABEL_RE = re.compile(r"(£[\d,]+(?:\.\d+)?|\b\d{1,2}(?::\d{2})?\s?(?:am|pm)\b|\b(?:1[89]|20)\d{2}\b|\b\d+\s(?:years?|miles|days|hours)\b)", re.I)
MOOD_RULES = [
    (r"\b(shock\w*|stunn\w*|discover\w*|realis\w*|twist|suddenly)\b", "shocked"),
    (r"\b(died|death|killed|murder\w*|grief|griev\w*|mourn\w*|funeral|tragic)\b", "sad"),
    (r"\b(missing|vanish\w*|disappear\w*|worried|feared|search\w*)\b", "worried"),
    (r"\b(solved|caught|celebrat\w*|finally|breakthrough)\b", "happy"),
    (r"\b(hunt\w*|investigat\w*|determined)\b", "determined"),
]
DEFAULT_CAST = {
    "office": [("detective", "left"), ("police", "right")],
    "incident_room": [("detective", "left"), ("police", "right")],
    "interview_room": [("detective", "left"), ("man", "right")],
    "courtroom": [("judge", "bench"), ("lawyer", "left")],
    "cell": [("police", "center")],
    "lab": [("forensic", "center")],
    "newsroom": [("reporter", "center")],
    "street_night": [("man", "center")],
    "street_day": [("woman", "center")],
    "house": [("police", "left"), ("detective", "right")],
    "woods": [("detective", "center")],
    "road_night": [("police", "left")],
    "home_interior": [("woman", "center")],
    "shop": [("man", "center")],
    "map": [("detective", "left")],
}
CAMERA_CYCLE = ["push_in", "pan_right", "pull_out", "static", "pan_left", "push_in"]


def _first(rules, text, default=None):
    for pat, val in rules:
        if re.search(pat, text, re.I):
            return val
    return default


def _hit(pat, toks, a, b):
    """First token in the span that matches pat (None if the match spans words)."""
    return next((w for w in toks[a:b] if re.search(pat, w.strip(".,;:!?\"'()"), re.I)), None)


def _cue_index(cue, toks, span, default):
    a, b = span
    c = norm(str(cue or ""))
    if c:
        for i in range(a, b):
            if norm(toks[i]) == c:
                return i
        for i in range(a, b):
            n = norm(toks[i])
            if len(c) > 2 and (n.startswith(c[:4]) or c.startswith(n[:4]) and len(n) > 2):
                return i
    return default


def _flat(s):
    return " ".join(norm(w) for w in str(s).split() if norm(w))


def rule_scene(i, sentence, toks, span, convicted, prev_set=None):
    """Keyword storyboard for one sentence (no LLM needed)."""
    a, b = span
    st = _first(SET_RULES, sentence, None) or ["office", "street_day", "incident_room", "home_interior"][i % 4]
    if st == prev_set and st in ("office", "incident_room"):
        st = "incident_room" if st == "office" else "office"
    mood = _first(MOOD_RULES, sentence, "neutral")
    mid = (a + b) // 2
    actors = []
    for role, spot in DEFAULT_CAST[st]:
        if role in ("man", "woman") and re.search(r"\b(she|her|woman|girl|mother|wife|daughter|sister)\b", sentence, re.I):
            role = "woman"
        actors.append({"role": role, "spot": spot, "mood": mood, "action": "react" if mood != "neutral" else "stand",
                       "cue": a + 1 if b - a > 2 else a, "hold": None})
    if st == "courtroom" and convicted and CONVICTED_RE.search(sentence) and not CLEARED_RE.search(sentence):
        actors.append({"role": "convict", "spot": "right", "mood": "sad", "action": "stand", "cue": a, "hold": None})
    if st in ("street_night", "street_day") and actors:
        actors[0]["action"], actors[0]["cue"] = "walk_in", a + 2
    if st == "map" and actors:
        actors[0]["action"], actors[0]["cue"] = "point", mid
    if st == "newsroom" and actors:
        actors[0]["action"], actors[0]["cue"] = "talk", a
    props = []
    m = LABEL_RE.search(sentence)
    if m:
        lab = m.group(1)
        props.append({"kind": "label", "value": lab, "cue": _cue_index(lab.split()[0], toks, span, mid)})
    icon = _first(ICON_RULES, sentence)
    if icon:
        hit = _hit(next(p for p, v in ICON_RULES if v == icon), toks, a, b)
        props.append({"kind": "icon", "value": icon, "cue": _cue_index(hit, toks, span, mid)})
    for stamp, pat in STAMPS.items():
        usable = stamp in ("MISSING", "NOT GUILTY", "UNSOLVED") or (stamp == "CASE CLOSED" and convicted)
        if usable and re.search(pat, sentence, re.I):
            hit = _hit(pat, toks, a, b)
            props.append({"kind": "stamp", "value": stamp, "cue": _cue_index(hit, toks, span, mid)})
            break
    return {"set": st, "camera": CAMERA_CYCLE[i % len(CAMERA_CYCLE)], "actors": actors, "props": props[:3],
            "source": "rules"}


def validate_scene(raw, i, sentence, toks, span, script, convicted, prev_set=None):
    """Coerce one LLM scene into the vocabulary and the legal rules. Returns None if unusable."""
    if not isinstance(raw, dict):
        return None
    a, b = span
    mid = (a + b) // 2
    st = raw.get("set") if isinstance(raw.get("set"), str) else None
    st = RETIRED_SETS.get(st, st)
    if st not in SETS:
        return None
    # A sentence that names a place or setting is acted out there, not in a generic office
    # (one LLM storyboard put 15 of 18 BTK scenes in the office, TV station and lab lines included).
    named = strong_set(sentence)
    swapped = bool(named and st in GENERIC_SETS and named != st)
    if swapped:
        st = named
    elif st in GENERIC_SETS and st == prev_set:
        st = "incident_room" if st == "office" else "office"
    risky = risky_sentence(sentence, script, convicted)
    actors, used = [], set()
    for ad in (raw.get("actors") or [])[:3]:
        if not isinstance(ad, dict):
            continue
        role = ad.get("role") if ad.get("role") in ROLES else "man"
        mood = ad.get("mood") if ad.get("mood") in MOODS else "neutral"
        if (role == "shadow" and risky) or (role == "convict" and (not convicted or CLEARED_RE.search(sentence))):
            role, mood = "man", "neutral"
        if role in CIVILIANS and risky and mood in VILLAIN_MOODS:
            mood = "neutral"
        if role == "judge":
            spot = "bench" if st == "courtroom" else "center"
        else:
            spot = ad.get("spot") if ad.get("spot") in SPOTS else None
        if spot in used or spot is None:
            spot = next((s for s in SPOTS if s not in used), None)
            if spot is None:
                continue
        used.add(spot)
        action = ad.get("action") if ad.get("action") in ACTIONS else "stand"
        hold = ad.get("hold") if ad.get("hold") in ICONS else None
        if hold and not (icon_fits(hold, sentence) or (hold == "gavel" and (role == "judge" or st == "courtroom"))):
            hold = None                        # nothing in the hands that the narrator isn't talking about
        actors.append({"role": role, "spot": spot, "mood": mood, "action": action,
                       "cue": _cue_index(ad.get("cue"), toks, span, a + 1 if b - a > 2 else a), "hold": hold})
    if not actors:
        return None
    if swapped and not any(a["role"] in {r for r, _ in DEFAULT_CAST[st]} for a in actors):
        role, spot = DEFAULT_CAST[st][0]       # the set's own lead: a judge on the bench, a reporter...
        if spot not in {a["spot"] for a in actors[1:]}:
            actors[0].update(role=role, spot=spot)
        else:
            actors[0]["role"] = role
    props = []
    # Every pop-up must belong to THIS sentence, so it appears while the narrator says it:
    # labels are words of the sentence, icons and stamps need a matching word in it.
    flat_sent = _flat(sentence)
    for pd in (raw.get("props") or [])[:3]:
        if not isinstance(pd, dict):
            continue
        cue = _cue_index(pd.get("cue"), toks, span, mid)
        if pd.get("icon") in ICONS:
            if icon_fits(pd["icon"], sentence):
                props.append({"kind": "icon", "value": pd["icon"], "cue": cue})
        elif pd.get("label"):
            lab = str(pd["label"]).strip().strip(".,;:!?\"'")
            if 0 < len(lab.split()) <= 4 and len(lab) <= 28 and _flat(lab) and f" {_flat(lab)} " in f" {flat_sent} ":
                props.append({"kind": "label", "value": lab, "cue": cue})
        elif pd.get("stamp"):
            stp = str(pd["stamp"]).upper().strip()
            stp = STAMP_SWAP.get(stp, stp)
            if stp in STAMPS and re.search(STAMPS[stp], sentence, re.I):
                if stp == "CASE CLOSED" and not convicted:
                    continue
                props.append({"kind": "stamp", "value": stp, "cue": cue})
    cam = raw.get("camera") if raw.get("camera") in CAMERAS else CAMERA_CYCLE[i % len(CAMERA_CYCLE)]
    return {"set": st, "camera": cam, "actors": actors, "props": props, "source": "llm"}


def plan(script, word_timings, duration, title="", use_llm=True, log=print):
    """Full timed plan: list of scenes with t0/t1 and token cue times."""
    toks, spans = split_sentences(script)
    times = align_times(toks, word_timings, duration)
    sentences = [" ".join(toks[a:b]) for a, b in spans]
    convicted = bool(CONVICTED_RE.search(script))
    raw = []
    if use_llm:
        try:
            raw = _llm_plan(title, sentences)
            log(f"[Cartoon] LLM storyboard: {len(raw)} scenes for {len(sentences)} sentences")
        except Exception as e:  # any LLM failure -> the rule storyboard, never a lost video
            log(f"[Cartoon] LLM storyboard failed ({str(e)[:140]}) - using the rule storyboard")
            raw = []
    by_num = {}
    for k, r in enumerate(raw):
        if isinstance(r, dict):
            try:
                by_num[int(r.get("s", k + 1)) - 1] = r
            except (TypeError, ValueError):
                by_num[k] = r
    scenes, prev, n_llm = [], None, 0
    for i, (span, sent) in enumerate(zip(spans, sentences)):
        sc = validate_scene(by_num.get(i), i, sent, toks, span, script, convicted, prev)
        if sc is None:
            sc = rule_scene(i, sent, toks, span, convicted, prev)
        else:
            n_llm += 1
        a, b = span
        sc["t0"] = 0.0 if i == 0 else max(0.0, times[a] - 0.1)
        sc["text"] = sent
        sc["span"] = [a, b]
        for item in sc["actors"] + sc["props"]:
            item["t"] = times[min(item["cue"], len(times) - 1)]
        scenes.append(sc)
        prev = sc["set"]
    for s, nxt in zip(scenes, scenes[1:]):
        s["t1"] = nxt["t0"]
    scenes[-1]["t1"] = duration + 0.6
    log(f"[Cartoon] plan: {len(scenes)} scenes ({n_llm} from the LLM, {len(scenes) - n_llm} from rules)")
    return {"scenes": scenes, "duration": duration, "convicted": convicted, "tokens": toks}
