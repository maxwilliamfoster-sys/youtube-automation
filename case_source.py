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
    "Category:Unsolved deaths",
    "Category:Unsolved murders by country",
    "Category:Missing person cases by country",
    "Category:Kidnappings by country",
    "Category:Murders by country",
    "Category:Unidentified murder victims",
    "Category:Cold cases",
    "Category:Overturned convictions",
]
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


def pick_case(is_duplicate, tries: int = 8) -> dict:
    """
    Return {title, extract, url} for a real case that isn't a repeat.

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
    for title in candidates[:tries]:
        summary = get_summary(title)
        if summary:
            return summary
    return {}


if __name__ == "__main__":
    pool = load_pool(force_refresh="--refresh" in os.sys.argv)
    print(f"{len(pool)} cases in pool")
    for t in random.sample(pool, min(15, len(pool))):
        print("  -", t)
