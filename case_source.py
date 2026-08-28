"""
Real, verifiable true-crime cases sourced from Wikipedia.

Why this module exists
----------------------
Asking an LLM to name "obscure true crime cases" fails in two ways at once:

  * Left open-ended it mode-collapses onto the same handful of famous cases
    (Tylenol, Dyatlov Pass, Taman Shud) no matter what the temperature is, which
    is what made the channel post the same three videos on a loop.
  * Pushed toward obscurity it simply invents cases. A test run produced
    "Enigma of Anna-Greta Gustafsson" — that is Greta Garbo's birth name attached
    to a Swedish murder that never happened, and the model's own fact-checker
    scored it 8/10 because it was grading its own hallucination.

So case names are never chosen by the model. They come from Wikipedia category
listings (real articles about real cases), and the script is written from the
article's own summary text rather than from the model's memory.

No API key needed. Wikipedia requires a descriptive User-Agent or it returns 403.
"""

import json
import os
import random
import re
import time

import requests

API = "https://en.wikipedia.org/w/api.php"
UA = "BuriedCasefiles/1.0 (https://github.com/maxwilliamfoster-sys/youtube-automation)"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Committed to the repo, and committed back by CI when it expires. Rebuilding costs
# ~60 rate-limited API calls, so a cloud run should read it, not build it.
POOL_FILE = os.path.join(BASE_DIR, "case_pool.json")
POOL_TTL_DAYS = 30

# Roots are expanded through their sub-categories (by country, by decade), which is
# where the breadth comes from — a few roots fan out into thousands of real cases.
ROOT_CATEGORIES = [
    # UK-focused. The audience is British (analytics: UK-skewed, 35+, watching British
    # news), and a case they could imagine happening down the road lands far harder
    # than an unfamiliar foreign one.
    "Category:Murder in London",
    "Category:Murder in England",
    "Category:Murder in Scotland",
    "Category:Murder in Wales",
    "Category:Missing person cases in England",
    "Category:Missing person cases in Scotland",
    "Category:Unsolved murders in the United Kingdom",
    "Category:Crime in London",
    "Category:Crime in Scotland",
    "Category:Kidnappings in the United Kingdom",
    "Category:Robberies in the United Kingdom",
    "Category:Miscarriage of justice cases in the United Kingdom",
]
# Deliberately NOT included: "Category:English gangsters" and the organised-crime
# group categories. Those articles are biographies of named individuals, and a script
# calling a named person a gang member is precisely the defamation exposure to avoid —
# UK defamation law is claimant-friendly and the burden would fall on this channel.
# Reported CASES ("Murder of X", "Disappearance of Y") are court-tested public record;
# accusations about people are not. The _is_case title filter already enforces this,
# since gangster articles are titled with bare personal names.
# Deliberately NOT included: "Category:Unidentified people" — despite the name it is
# mostly anonymous medieval artists ("Master of Cabestany"), not unidentified victims.

# Pages that are not individual cases.
_NOT_A_CASE = re.compile(
    r"^(list|lists|index|outline|timeline|category|template|portal|wikipedia)\b[\s:]",
    re.I,
)

# The category tree also sweeps in war crimes, political violence and antiquity —
# real, but wrong for this channel and often straight into TikTok's moderation
# filters. Cheaper to drop them here than to burn a generation attempt discovering
# the fact-checker won't pass them.
# Wikipedia's first sentence is always definitional ("X is a marble statue by..."), so
# it is the cheapest reliable way to tell a real case from a thing NAMED like one.
# "Abduction of a Sabine Woman" is a Giambologna sculpture; it matched the case-title
# pattern perfectly, and every downstream gate passed it because the script was
# accurate — accurately describing a statue. A whole video shipped about it.
_NOT_AN_EVENT = re.compile(
    r"\bis\s+(a|an|the)\s+[^.]{0,60}?\b("
    r"statue|sculpture|painting|portrait|artwork|fresco|mural|engraving|"
    r"novel|book|short story|poem|play|opera|ballet|musical|"
    r"film|movie|documentary|television|tv series|episode|sitcom|"
    r"song|single|album|band|video game|board game|"
    r"myth|legend|folk tale|fairy tale|deity|god|goddess|"
    r"museum|monument|memorial|building|church|castle|bridge|"
    r"genus|species|plant|mineral|asteroid|crater"
    r")\b",
    re.I,
)

# A genuine case intro says what happened and that someone looked into it.
_CRIME_WORDS = re.compile(
    r"\b(murder|murdered|killed|killing|homicide|manslaughter|"
    r"disappear|disappeared|disappearance|missing|vanished|abducted|abduction|"
    r"kidnap|kidnapped|kidnapping|body|bodies|remains|corpse|"
    r"police|detective|investigat|inquest|coroner|suspect|convicted|conviction|"
    r"trial|court|sentenced|arrested|charged|crime|unsolved|cold case)\b",
    re.I,
)

# Split into two tiers, because a single blunt keyword list over the whole intro was
# throwing away prime material. "Vizconde murders" — a family murdered in their home,
# exactly this channel's content — was rejected for containing the word "massacre",
# and "Murder of Wendy Albano" for mentioning a senator who pushed the investigation.
#
# Tier 1: unambiguous. If these appear anywhere, it is not a true-crime case.
_OFF_TOPIC_ANYWHERE = re.compile(
    r"\b(genocide|pogrom|war crimes?|holocaust|ethnic cleansing|"
    r"nazi|gestapo|wehrmacht|apartheid|terrorist attack|suicide bombing|"
    r"extrajudicial|enforced disappearance|forced disappearance|"
    r"crimes against humanity|state-sponsored)\b",
    re.I,
)

# Country/institution-level topics dressed up as cases, e.g. "Extrajudicial killings
# and enforced disappearances in Bangladesh". These are systemic human-rights articles,
# not a single crime with a victim and an investigation — impossible to tell as one
# 60-second documentary and squarely into TikTok's political-content territory.
_SYSTEMIC_TITLE = re.compile(
    r"\b(killings|disappearances|murders|massacres)\s+(and\s+\w+\s+)?(in|of|during|under|by)\s+"
    r"(the\s+)?[A-Z]",
)

# Tier 2: only disqualifying when they describe what the article IS. Wikipedia's first
# sentence is definitional, so checking only there keeps passing mentions harmless.
# "massacre" is deliberately absent: it describes family murders (Vizconde, Villisca)
# as often as war atrocities, and the former is exactly this channel's material. War
# atrocities are caught by tier 1 and by the Community Guidelines gate downstream.
_OFF_TOPIC_SUBJECT = re.compile(
    r"\b(bombing|airstrike|air raid|insurgen|guerrilla|militia|paramilitary|"
    r"regiment|battalion|mass shooting|school shooting|coup|junta|dictator|"
    r"president|senator|politician|prime minister|ambassador|general|admiral|"
    r"ancient|classical|roman|byzantine|medieval)\b",
    re.I,
)

# Dates that place a case outside living memory. "Death of Cleopatra" became a
# true-crime documentary because the word filter never saw "ancient" — the article
# says "30 BC". Audiences connect to cases with photographs, police files and
# relatives still alive to wonder about them.
_TOO_OLD = re.compile(
    r"\b\d{1,4}\s?(BC|BCE|B\.C\.)\b"
    r"|\b1[0-7]\d{2}\b"
    r"|\b\d{1,2}(st|nd|rd|th)\s+century\b",
    re.I,
)

# State violence and public-figure killings. Real crimes, but they play as politics,
# not true crime: no relatable victim and nothing for a viewer to solve. The pool
# served a Russian activist, a Philippine official and a US execution — every one
# scored badly on intrigue and every one still got posted.
_POLITICAL_CASE = re.compile(
    r"\b(activist|dissident|opposition (leader|figure|politician)"
    r"|executed by|death (row|penalty)|capital punishment|lethal injection"
    r"|government official|party official|board secretary|mayor|councillor"
    r"|cartel|militant|rebel|regime|state security|secret police)\b",
    re.I,
)


def _first_sentence(text: str) -> str:
    m = re.search(r"^.{0,400}?[.!?](?=\s|$)", text.strip(), re.S)
    return m.group(0) if m else text[:400]


def _is_off_topic(text: str) -> str:
    """Return the offending term, or '' if the text looks like a true-crime case."""
    m = _OFF_TOPIC_ANYWHERE.search(text)
    if m:
        return m.group(0)
    m = _OFF_TOPIC_SUBJECT.search(_first_sentence(text))
    return m.group(0) if m else ""

_session = None
_last_call = 0.0

# Wikimedia throttles hard from shared cloud IPs, and GitHub Actions runners are very
# much shared. Firing requests back-to-back got every one of them 429'd from CI while
# working fine from a home connection, so requests are serialised with a gap.
MIN_INTERVAL = 0.5
MAX_RETRIES = 4


def _get(params: dict) -> dict:
    global _session, _last_call
    if _session is None:
        _session = requests.Session()
        _session.headers["User-Agent"] = UA
    params = {**params, "format": "json", "maxlag": "5"}

    for attempt in range(MAX_RETRIES):
        gap = time.time() - _last_call
        if gap < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - gap)

        r = _session.get(API, params=params, timeout=25)
        _last_call = time.time()

        if r.status_code == 429:
            wait = float(r.headers.get("Retry-After", 2 ** attempt))
            print(f"[CaseSource] Rate-limited by Wikipedia — waiting {wait:.0f}s...")
            time.sleep(min(wait, 30))
            continue
        r.raise_for_status()
        data = r.json()
        # maxlag returns 200 with an error body when the replicas are behind.
        if isinstance(data, dict) and data.get("error", {}).get("code") == "maxlag":
            time.sleep(2 ** attempt)
            continue
        return data

    raise RuntimeError(f"Wikipedia rate-limited after {MAX_RETRIES} attempts")


def _members(category: str, kind: str) -> list:
    """Category members of a given type ('page' or 'subcat'). Empty list on failure."""
    try:
        data = _get({
            "action": "query", "list": "categorymembers",
            "cmtitle": category, "cmlimit": "500", "cmtype": kind,
        })
        return [m["title"] for m in data.get("query", {}).get("categorymembers", [])]
    except Exception:
        return []


# Wikipedia titles case articles predictably: "Murder of X", "Disappearance of Y",
# "<Place> murders", "<Place> Mystery". Requiring that shape drops ~75% of the raw
# category haul and nearly all of the noise — the junk is overwhelmingly bare names
# ("Bindy Johal", "Microman (wrestler)") swept in by broad parent categories.
_CASE_TITLE = re.compile(
    r"^(the\s+)?(murders?|killings?|deaths?|disappearances?|kidnapping|abduction|"
    r"assassination|shooting|poisoning|homicide|lynching|execution)\b.*\bof\b"
    r"|^unidentified\b"
    r"|\b(murders|killings|case|cases|incident|mystery|affair|slayings)\b",
    re.I,
)


def _is_case(title: str) -> bool:
    if not title or _NOT_A_CASE.match(title):
        return False
    if _is_off_topic(title):
        return False
    if _SYSTEMIC_TITLE.search(title):
        return False
    return bool(_CASE_TITLE.search(title))


def build_pool(depth: int = 2) -> list:
    """
    Walk the category roots and collect real case-article titles.
    Costs roughly 40-80 API calls, so the result is cached to disk.
    """
    seen_cats, titles = set(), set()
    frontier = list(ROOT_CATEGORIES)

    for _ in range(depth + 1):
        if not frontier:
            break
        next_frontier = []
        for cat in frontier:
            if cat in seen_cats:
                continue
            seen_cats.add(cat)
            titles.update(t for t in _members(cat, "page") if _is_case(t))
            next_frontier.extend(_members(cat, "subcat"))
        frontier = next_frontier

    return sorted(titles)


def load_pool(force_refresh: bool = False) -> list:
    """Cached case pool. Rebuilds if missing, stale, or suspiciously small."""
    if not force_refresh and os.path.exists(POOL_FILE):
        try:
            with open(POOL_FILE, encoding="utf-8") as f:
                blob = json.load(f)
            fresh = time.time() - blob.get("built_at", 0) < POOL_TTL_DAYS * 86400
            cases = blob.get("cases", [])
            if fresh and len(cases) > 200:
                return cases
        except Exception:
            pass

    print("[CaseSource] Building case pool from Wikipedia...")
    cases = build_pool()
    print(f"[CaseSource] Pool: {len(cases)} real cases.")
    if cases:
        try:
            with open(POOL_FILE, "w", encoding="utf-8") as f:
                json.dump({"built_at": int(time.time()), "cases": cases}, f, indent=1)
        except Exception:
            pass
    return cases


def get_summary(title: str) -> dict:
    """
    The article's own intro text — the factual ground the script is written from.
    Returns {} if the article has no usable extract.
    """
    try:
        data = _get({
            "action": "query", "prop": "extracts|info",
            "exintro": 1, "explaintext": 1, "inprop": "url",
            "redirects": 1, "titles": title,
        })
        pages = data.get("query", {}).get("pages", {})
        for _, page in pages.items():
            extract = (page.get("extract") or "").strip()
            if len(extract) < 200:      # too thin to build a documentary on
                return {}
            # Titles that are just a person's name reveal nothing; the intro does.
            # This is what catches e.g. a Wehrmacht admiral filed under "unsolved deaths".
            off = _is_off_topic(extract)
            if off:
                print(f"[CaseSource] Skipping {title!r} — off-topic ({off}).")
                return {}
            # Outside living memory — no photographs, no files, no one left to wonder.
            if _TOO_OLD.search(extract[:500]):
                print(f"[CaseSource] Skipping {title!r} — outside living memory.")
                return {}
            # Political killing rather than true crime.
            if _POLITICAL_CASE.search(extract[:700]):
                print(f"[CaseSource] Skipping {title!r} — political, not true crime.")
                return {}
            # An artwork or film whose title reads like a case.
            if _NOT_AN_EVENT.search(extract[:400]):
                print(f"[CaseSource] Skipping {title!r} — not a real case (artwork/media).")
                return {}
            # Positive check: a real case names a crime or an investigation somewhere in
            # its intro. Requiring this rejects the long tail of odd articles the
            # category tree drags in without needing a rule for each one.
            if not _CRIME_WORDS.search(extract):
                print(f"[CaseSource] Skipping {title!r} — intro describes no crime.")
                return {}
            return {
                "title":   page.get("title", title),
                "extract": extract,
                "url":     page.get("fullurl", ""),
            }
    except Exception as e:
        print(f"[CaseSource] Summary fetch failed for {title!r}: {e}")
    return {}


# ─── Intrigue scoring ─────────────────────────────────────────────────────────
# Cases were previously chosen at random, which is why quality swung so wildly. The
# channel's own analytics are unambiguous about what works: the best post (1.9K views,
# ~2.6x the next) was bodies at the base of cliffs "with no signs of" a fall, while
# "teen dies in brawl" managed 218. An unexplained mystery massively outperforms a
# sad-but-ordinary crime, because only the mystery leaves a question the viewer needs
# answered — which is also what drives the comments the whole strategy depends on.

# The hook: something that should be impossible, or was never explained.
_INTRIGUE_STRONG = re.compile(
    r"\b(never (been )?(found|identified|solved|explained|recovered|seen again)|"
    r"no (trace|sign|signs|evidence|explanation|witnesses)|"
    r"remains? (a )?(mystery|unidentified|unsolved)|"
    r"vanished|disappeared without|unexplained|inexplicable|baffl\w+|"
    r"still unidentified|unidentified (man|woman|body|remains)|"
    r"mysterious circumstances|locked from the inside|no one (has )?ever)\b",
    re.I,
)

# Supporting texture: cold cases, strange details, reopened investigations.
_INTRIGUE_MEDIUM = re.compile(
    r"\b(cold case|unsolved|reopened|new evidence|decades later|years later|"
    r"conflicting|contradict\w*|theory|theories|speculation|rumou?r|"
    r"last seen|final sighting|anonymous|cryptic|strange|bizarre|puzzl\w+|"
    r"never charged|acquitted|overturned|wrongful|exonerat\w+)\b",
    re.I,
)

# Resolved and ordinary: a known killer, a confession, a routine conviction. Real
# crime, but there is no open question left for a viewer to argue about.
_MUNDANE = re.compile(
    r"\b(pleaded guilty|pled guilty|confessed|admitted|convicted of|found guilty|"
    r"sentenced to|arrested (at|and charged)|domestic (dispute|violence)|"
    r"brawl|bar fight|drunken|robbery gone|drug deal|gang (dispute|feud))\b",
    re.I,
)


# Below this, a case is not worth a video. Tuned against cases with known
# performance: the duds scored under 2.5, the strong ones 7+.
MIN_INTRIGUE = 6.0


def intrigue_score(summary: dict) -> float:
    """
    Rate how much of an open question a case leaves. Higher = better video.

    Scored from the article's own text, so it measures the actual story rather than
    the title. Normalised by nothing — the value is only ever compared against other
    candidates in the same batch.
    """
    text = summary.get("extract", "")
    if not text:
        return 0.0

    # Score DENSITY, not raw counts. Counting hits alone just rewards long articles:
    # a rambling 4,000-word piece on an ordinary murder outscored the Great Mull Air
    # Mystery, which is genuinely gripping but briefly written. What matters is how
    # much of the story is unexplained, not how many words it took to say so.
    per_1k = max(len(text), 400) / 1000.0
    raw = (3.0 * len(_INTRIGUE_STRONG.findall(text))
           + 1.0 * len(_INTRIGUE_MEDIUM.findall(text))
           - 2.0 * len(_MUNDANE.findall(text)))
    score = raw / per_1k

    low = text.lower()
    # Audience relatability. The analytics show a UK-skewed, 35+ audience whose other
    # viewing is British news. A case they could imagine happening near them lands;
    # one in an unfamiliar country with no cultural foothold does not, however
    # mysterious it is on paper.
    if re.search(r"(england|scotland|wales|britain|british|uk|london|ireland|irish)", low):
        score += 3.5
    elif re.search(r"(united states|american|australia|australian|canada|canadian|new zealand)", low):
        score += 2.0

    # An ordinary victim is the whole appeal of the genre — it could have been anyone.
    if re.search(r"(student|nurse|teacher|schoolgirl|schoolboy|mother|father|"
                 r"housewife|waitress|barmaid|shop assistant|teenager|child|"
                 r"young woman|young man|girl|boy)", low):
        score += 2.0

    # A concrete, filmable discovery is what a hook is built from — the research is
    # explicit that a viral case needs "a specific, shocking detail that can open the
    # video". Without one there is nothing to put in the first three seconds.
    if re.search(r"(body was found|remains were found|found (dead|buried|floating|"
                 r"in a|at the)|last seen|abandoned car|locked|footprints|"
                 r"never arrived|failed to return|walked out)", low):
        score += 2.5

    title = summary.get("title", "").lower()
    # Disappearances are the channel's strongest format: the mystery is the premise,
    # and they consistently clear the Community Guidelines gate that graphic murders
    # trip on, so they are far likelier to actually reach the For You feed.
    if title.startswith(("disappearance", "vanishing")):
        score += 4.0
    elif "unidentified" in title or "mystery" in title:
        score += 3.0

    # Needs enough substance to carry 60 seconds, but epics earn nothing extra.
    score += min(len(text) / 2000.0, 1.0)
    return score


def pick_case(is_duplicate, tries: int = 8, shortlist: int = 12,
              _retries: int = 2) -> dict:
    """
    Return {title, extract, url} for the most intriguing unused case available.

    Fetches a shortlist of candidates and returns the highest-scoring one rather than
    the first that loads. Random selection was producing a lot of ordinary
    "man convicted of murder" stories that had nothing for a viewer to wonder about.

    `is_duplicate(title) -> bool` is supplied by the caller so this module stays
    unaware of how recency is tracked.
    """
    pool = load_pool()
    if not pool:
        return {}

    candidates = [t for t in pool if not is_duplicate(t)]
    if not candidates:
        print("[CaseSource] Every case in the pool has been used — refreshing pool.")
        pool = load_pool(force_refresh=True)
        candidates = [t for t in pool if not is_duplicate(t)] or pool

    random.shuffle(candidates)

    scored = []
    for title in candidates[:shortlist]:
        summary = get_summary(title)
        if not summary:
            continue
        summary["intrigue"] = intrigue_score(summary)
        scored.append(summary)
        # A standout is not worth spending more API calls on.
        if summary["intrigue"] >= 12:
            break

    if not scored:
        # Shortlist all rejected — fall back to the old behaviour so a run still ships.
        for title in candidates[shortlist:shortlist + tries]:
            summary = get_summary(title)
            if summary:
                return summary
        return {}

    scored.sort(key=lambda s: s["intrigue"], reverse=True)

    # A minimum bar, not just "best available". This was the real defect: the picker
    # returned the top of the shortlist even when every candidate was weak, so on a
    # bad draw it confidently shipped a dud. Death of Cleopatra (1.0), Dustin Higgs
    # (-1.5) and a Russian activist (0.2) were all scored correctly as poor and all
    # got posted anyway. Below the bar it re-rolls a fresh shortlist instead.
    if scored[0]["intrigue"] < MIN_INTRIGUE and _retries > 0:
        print(f"[CaseSource] Best candidate only scored {scored[0]['intrigue']:.1f} "
              f"(bar is {MIN_INTRIGUE}) — drawing a new shortlist.")
        fresh = pick_case(is_duplicate, tries=tries, shortlist=shortlist,
                          _retries=_retries - 1)
        if fresh:
            return fresh

    best = scored[0]
    runners_up = "; ".join(f"{s['title'][:28]} ({s['intrigue']:.0f})" for s in scored[1:4])
    print(f"[CaseSource] Picked '{best['title']}' — intrigue {best['intrigue']:.1f}, "
          f"best of {len(scored)}")
    if runners_up:
        print(f"[CaseSource]   beat: {runners_up}")
    return best


if __name__ == "__main__":
    pool = load_pool(force_refresh="--refresh" in os.sys.argv)
    print(f"{len(pool)} cases in pool")
    for t in random.sample(pool, min(15, len(pool))):
        print("  -", t)


def get_full_text(title: str, cap: int = 7000) -> str:
    """
    The complete article text, not just the intro.

    Filtering and scoring run on the intro because they happen across a whole
    shortlist and the intro is cheap. Script writing is different: it happens once,
    for one chosen case, and it needs material. Intros run 250-350 characters, while
    the full articles run 4,000-7,000 — 12x to 21x more real detail.

    That gap was the cause of the fabrication problem. Asked to build a 170-word
    script from 40 words of source, the model invented the difference: relatives'
    names, autopsy findings, the weather. Fact-check accuracy sat at 3-4/10 and no
    amount of prompt discipline fixed it, because the instruction to write a full
    script and the instruction to invent nothing were impossible together. Given the
    real article there is enough to say without inventing anything.
    """
    try:
        data = _get({
            "action": "query", "prop": "extracts",
            "explaintext": 1, "redirects": 1, "titles": title,
        })
        for _, page in data.get("query", {}).get("pages", {}).items():
            text = (page.get("extract") or "").strip()
            if text:
                return text[:cap]
    except Exception as e:
        print(f"[CaseSource] Full text unavailable for {title!r}: {e}")
    return ""
