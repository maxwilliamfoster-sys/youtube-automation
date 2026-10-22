"""
Cheap pre-screen of a case BEFORE any script is written, plus a memory of every
case that has already been rejected so it is never tried again.

Why this exists
---------------
In the 30 days to 2026-10-12 only 26 of 56 generate runs produced a video. The
logs showed the same story every time: the pipeline picked a case, spent ~8 LLM
calls researching, writing, fact-checking and softening it, and only THEN did the
TikTok compliance gate or the legal gate reject it - for reasons that were visible
in the Wikipedia intro all along (a murdered nine-year-old, a named suspect who was
acquitted). And because rejections were only remembered for the current run, the
same doomed cases came back day after day: "Murder of Linda Smith", "Murder of
Janet Brown" and "Thames Torso Murders" were each tried four times.

Two layers, both cheaper than a script:
  1. screen_text() - regexes over the article intro, zero API cost. They encode the
     same criteria the final gates apply (story_generator.compliance_review and
     _legal_review): a killed child, sexual violence, gore, a named person who was
     never convicted, live court proceedings.
  2. story_generator._prescreen_case() - one short LLM call on the chosen case
     with the same criteria, for what a regex cannot see.

Neither replaces the final compliance and legal gates; those still run on every
script exactly as before. This only stops the pipeline wasting a slot on cases
those gates were always going to refuse.

Rejected cases are persisted in rejected_cases.json (committed back by CI, like
recent_cases.json). Delete an entry - or the file - to let a case be tried again.
"""

import datetime as _dt
import json
import os
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REJECTED_FILE = os.path.join(BASE_DIR, "rejected_cases.json")
MAX_REMEMBERED = 800

# A rejection at any of these stages is about the CASE itself, so one strike is
# enough. Anything else (an empty script from a flaky model, a low accuracy score)
# may well succeed on a second try, so those need two strikes.
HARD_STAGES = {"prefilter", "prescreen", "compliance", "legal"}
SOFT_STRIKES = 2


# ─── Regex pre-filter (mirrors the compliance + legal gate criteria) ──────────

# Sexual violence: compliance BANs any sexual detail involving a minor and
# age-restricts it otherwise, and the softener cannot remove what the case IS.
# Only checked in the title and the opening (~350 chars), i.e. where Wikipedia says
# what the case IS. Checked across the whole intro it flagged 158 of 881 cases,
# mostly for a SUSPECT's record ("a convicted sex offender, was identified...") in
# cases that have cleared the gates and been posted (Suzy Lamplugh, Danielle Jones).
_SEXUAL = re.compile(
    r"\b(rap(e|ed|es|ing|ist|ists)|sexual(ly)?\s+(assault\w*|abus\w*|attack\w*|motivated|violen\w*)|"
    r"sex\s+(offen[cs]e\w*|crime\w*|attack\w*)|indecent(ly)?\s+assault\w*|"
    r"sex[- ]trafficking|necrophil\w*)\b",
    re.I,
)
_SEXUAL_LEAD_CHARS = 350
# Sexual abuse of children: the BANNED tier, so anywhere in the intro.
_CHILD_SEXUAL = re.compile(
    r"\b(child\s+sexual\s+(abuse|exploitation)|child\s+abuse|paedophil\w*|pedophil\w*|"
    r"molest\w*|sexual(ly)?\s+(abus\w*|assault\w*)\s+(of\s+)?(a\s+|the\s+)?"
    r"(child|children|girls?|boys?|minors?|pupils?))\b",
    re.I,
)

# Gore: a case defined by the state of the body is "dwelling on a real tragedy for
# shock value" (FYF_INELIGIBLE) however carefully it is told.
_GORE = re.compile(
    r"\b(dismember\w*|decapitat\w*|behead\w*|mutilat\w*|cannibal\w*|tortur\w*|"
    r"disembowel\w*|eviscerat\w*|burn(ed|t)\s+alive|severed\s+(head|limbs?|hands?)|"
    r"torsos?|body\s+parts|flay\w*|scalp(ed|ing))\b",
    re.I,
)

# Ages. "a 9-year-old", "aged 12", "12 years old".
_AGE = re.compile(
    r"\b(\d{1,2})[- ]years?[- ]old\b|\baged\s+(\d{1,2})\b|\b(\d{1,2})\s+years\s+of\s+age\b",
    re.I,
)
_CHILD_WORDS = re.compile(
    r"\b(schoolgirl|schoolboy|toddler|infant|newborn|baby|babies|"
    r"(young|little)\s+(girl|boy|child|children)|child\s+(victim|murder\w*|killing))\b",
    re.I,
)
# A killing (as opposed to a disappearance with no confirmed death).
_KILLED = re.compile(
    r"\b(murder\w*|killed|killing|slain|slaying|homicide|manslaughter|strangl\w*|"
    r"stabbed|beaten to death|body was found|bodies were found|remains were found)\b",
    re.I,
)

# Live proceedings: UK contempt of court applies from arrest/charge until verdict.
_ACTIVE_PROCEEDINGS = re.compile(
    r"\b(awaiting\s+(trial|sentenc\w*|extradition)|"
    r"trial\s+(is\s+)?(due|scheduled|set|expected)\s+to\s+(begin|start|open)|"
    # Not "no one has been charged" - that is every cold case, and harmless.
    r"(?<!one\s)(?<!nobody\s)(?<!never\s)(has|have)\s+(since\s+)?been\s+charged|"
    r"(is|are)\s+(currently\s+)?(on\s+trial|being\s+tried)|"
    r"remanded\s+in\s+custody|appeal\s+is\s+(pending|ongoing)|"
    r"ongoing\s+(trial|prosecution|court case)|proceedings\s+are\s+(ongoing|active))\b",
    re.I,
)

# A named / identifiable person connected to the crime who was NOT convicted.
# This is the single biggest cause of wasted runs (25 legal blocks in 26 runs).
_UNCONVICTED = re.compile(
    r"\b(acquitted|found\s+not\s+guilty|(conviction|convictions)\s+(was\s+|were\s+)?"
    r"(quashed|overturned)|quashed|exonerat\w*|cleared\s+of|"
    r"person\s+of\s+interest|(prime|main|chief|principal|key)\s+suspect|"
    r"(named|identified)\s+as\s+a\s+suspect|"
    r"(was|were|had\s+been)\s+(arrested|questioned|interviewed)\b[^.]{0,120}?"
    r"\b(released|never\s+charged|without\s+charge|no\s+further\s+action|not\s+charged)|"
    r"charges\s+(were\s+|was\s+)?(dropped|withdrawn|dismissed)|"
    r"released\s+without\s+charge|no\s+further\s+action)\b",
    re.I,
)
# Someone WAS finally convicted. Then an earlier acquittal / quashed conviction is
# usually the "wrong man" half of a resolved story, which the legal gate passes when
# the exoneration is stated - leave those to the LLM pre-screen.
_CONVICTED = re.compile(
    r"\b(was|were)\s+(later\s+|eventually\s+|subsequently\s+|finally\s+)?"
    r"(convicted|found\s+guilty|jailed|sentenced)\b|\bpleaded\s+guilty\b",
    re.I,
)


def _minor_killed(title: str, text: str) -> bool:
    """A killing whose victim was under 18. Disappearances alone are allowed: those
    have repeatedly cleared the gates (Andrew Gosden, Etan Patz, Genette Tate)."""
    head = text[:700]
    if title.lower().startswith(("disappearance", "vanishing")) and not re.search(
            r"\b(body|remains)\s+(was|were)\s+(found|discovered|recovered)|\bmurdered\b", head, re.I):
        return False
    if not _KILLED.search(f"{title} {head}"):
        return False
    for m in _AGE.finditer(head):
        age = int(next(g for g in m.groups() if g))
        if age < 18:
            return True
    return bool(_CHILD_WORDS.search(head))


def _too_recent(text: str, years: int = 2) -> bool:
    """Events in the last couple of years: proceedings are probably still live."""
    this_year = _dt.date.today().year
    found = [int(y) for y in re.findall(r"\b(19\d{2}|20\d{2})\b", text[:600])]
    if not found:
        return False
    # The EARLIEST year in the intro dates the crime; later years are usually the
    # trial, an appeal or an anniversary.
    return min(found) >= this_year - years + 1


def screen_text(title: str, text: str) -> str:
    """
    Return a short reason code if this case would fail the final gates, else "".
    `text` is the article intro (what case_source scores on). Kept deliberately
    to the first ~1,500 characters: long articles mention every related crime.
    """
    title = title or ""
    body = (text or "")[:1500]
    both = f"{title}\n{body}"
    if _SEXUAL.search(f"{title}\n{body[:_SEXUAL_LEAD_CHARS]}") or _CHILD_SEXUAL.search(both):
        return "sexual_violence"
    if _GORE.search(both):
        return "gore"
    if _minor_killed(title, body):
        return "minor_victim"
    if _ACTIVE_PROCEEDINGS.search(body) or _too_recent(body):
        return "active_proceedings"
    if _UNCONVICTED.search(body) and not _CONVICTED.search(body):
        return "unconvicted_suspect"
    return ""


# ─── Rejected-case memory ─────────────────────────────────────────────────────

def load_rejected() -> dict:
    try:
        with open(REJECTED_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


_cache = None


def _store() -> dict:
    global _cache
    if _cache is None:
        _cache = load_rejected()
    return _cache


def is_rejected(title: str) -> bool:
    """True if this case should never be tried again."""
    entry = _store().get(title or "")
    if not entry:
        return False
    if entry.get("stage") in HARD_STAGES:
        return True
    return int(entry.get("strikes", 1)) >= SOFT_STRIKES


# rejected_cases.json is committed to a PUBLIC repo. A reviewer's free-text reason
# ("The script identifies Terri Moulton ... implicates her in alleged wrongdoing")
# restates the very allegation the gate refused to publish, about people who were
# acquitted or never charged (audit 2026-10-21 F12). Only the stage and a short code
# are stored; the full reason goes to the run log at most.
_CODE_RE = re.compile(r"^[a-z][a-z_]{1,39}$")
_LEGAL_SPECULATION = re.compile(r"speculat|invit\w* viewers|guess|theor(y|ies)|ask\w* (the )?viewers", re.I)
_LEGAL_PERSON = re.compile(
    r"convict|acquit|charged|suspect|implicat|person\w* of interest|identif|names?\b|named|"
    r"accus|guilt|blame|attribut|hitm[ae]n|perpetrator|killer|wrongdoing", re.I)
_LEGAL_PROCEEDINGS = re.compile(r"contempt|active proceedings|ongoing|awaiting trial|sub judice", re.I)


# Codes the pre-filter, pre-screen and compliance review already speak in.
KNOWN_CODES = {"sexual_violence", "gore", "minor_victim", "active_proceedings",
               "unconvicted_suspect", "other", "none", "fyf_ineligible", "age_restricted", "banned"}


def _known(code: str) -> bool:
    return bool(_CODE_RE.match(code)) and (code in KNOWN_CODES or "_" in code)


def reason_code(stage: str, reason: str = "") -> str:
    """A short lowercase code for why a case failed - never names or allegations."""
    text = str(reason or "").strip()
    head = re.split(r"[:;\s]", text, maxsplit=1)[0].strip().lower() if text else ""
    if text == text.lower() and _known(text):
        return text                                   # already a code ("gore", "minor_victim")
    if stage in ("prescreen", "compliance") and _known(head):
        return head                                   # "unconvicted_suspect: ...", "AGE_RESTRICTED: ..."
    if stage == "legal":
        if _LEGAL_PROCEEDINGS.search(text):
            return "active_proceedings"
        if _LEGAL_PERSON.search(text):
            return "unconvicted_suspect"
        if _LEGAL_SPECULATION.search(text):
            return "invites_speculation"
        return "legal_risk"
    return {"quality": "low_accuracy", "empty_script": "script_unusable",
            "review_error": "review_error", "factcheck_unsafe": "factcheck_unsafe",
            "prefilter": "prefilter", "prescreen": "prescreen_other",
            "compliance": "compliance_other"}.get(stage, "other")


def _public_entry(entry: dict) -> dict:
    """Stage, code, date and strike count only. Old entries' free text is converted."""
    stage = str(entry.get("stage") or "other")
    code = entry.get("code")
    if not (isinstance(code, str) and _CODE_RE.match(code)):
        code = reason_code(stage, entry.get("reason", ""))
    out = {"stage": stage, "code": code}
    if entry.get("date"):
        out["date"] = str(entry["date"])[:10]
    out["strikes"] = int(entry.get("strikes", 1) or 1)
    return out


def scrub_store(store: dict) -> dict:
    """Rewrite every entry to the public shape (used on save and by the one-off rewrite)."""
    return {title: _public_entry(e if isinstance(e, dict) else {}) for title, e in store.items()}


def save_rejected(store: dict) -> None:
    try:
        with open(REJECTED_FILE, "w", encoding="utf-8") as f:
            json.dump(scrub_store(store), f, indent=1, ensure_ascii=False)
    except Exception as e:
        print(f"[Screen] Could not save {REJECTED_FILE}: {e}")


def record_rejection(title: str, stage: str, reason: str = "") -> None:
    """Remember why a case failed. Hard stages exclude it at once; others after 2 strikes.
    `reason` is reduced to a short code before anything is stored."""
    if not title:
        return
    store = _store()
    prev = store.get(title, {})
    keep_prev = stage not in HARD_STAGES and prev.get("stage") in HARD_STAGES
    entry = {
        "stage":   prev["stage"] if keep_prev else stage,
        "code":    _public_entry(prev)["code"] if keep_prev else reason_code(stage, reason),
        "date":    _dt.date.today().isoformat(),
        "strikes": int(prev.get("strikes", 0)) + 1,
    }
    store.pop(title, None)
    store[title] = entry                      # re-insert so the newest is last
    while len(store) > MAX_REMEMBERED:        # drop the oldest
        store.pop(next(iter(store)))
    save_rejected(store)
