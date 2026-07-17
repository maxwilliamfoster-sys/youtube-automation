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
POOL_FILE = os.path.join(BASE_DIR, "case_pool.json")
POOL_TTL_DAYS = 14

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
_OFF_TOPIC = re.compile(
    r"\b(massacre|genocide|pogrom|war crimes?|wartime|terroris[tm]|bombing|airstrike|"
    r"air raid|insurgen|guerrilla|militia|paramilitary|regiment|batallion|battalion|"
    r"mass shooting|school shooting|hostage diplomacy|coup|junta|dictator|"
    r"president|senator|politician|minister|ambassador|general|admiral|"
    r"nazi|ss[- ]|gestapo|wehrmacht|holocaust|apartheid|"
    r"ancient|classical|bc\b|b\.c\.|roman|byzantine|medieval)\b",
    re.I,
)

_session = None


def _get(params: dict) -> dict:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers["User-Agent"] = UA
    params = {**params, "format": "json"}
    r = _session.get(API, params=params, timeout=25)
    r.raise_for_status()
    return r.json()


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
    if _OFF_TOPIC.search(title):
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
            if _OFF_TOPIC.search(extract[:600]):
                return {}
            return {
                "title":   page.get("title", title),
                "extract": extract,
                "url":     page.get("fullurl", ""),
            }
    except Exception as e:
        print(f"[CaseSource] Summary fetch failed for {title!r}: {e}")
    return {}


def pick_case(is_duplicate, tries: int = 25) -> dict:
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
