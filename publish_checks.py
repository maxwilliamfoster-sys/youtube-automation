"""
Pre-delivery checks: copyright, defamation, TikTok guidelines.

Nothing reaches the phone without passing all three. They are separate on purpose —
each fails in a different way and a single "is this ok?" prompt is a poor judge of
all three at once.

  copyright   Deterministic, not a judgement call. Every image carries the licence it
              was fetched under; anything not on the free list never entered the
              pipeline, and CC-BY/CC-BY-SA require visible attribution, so a missing
              credit is a failure rather than a warning.

  defamation  UK law is claimant-friendly and puts the burden on the publisher. The
              danger is naming a living person in connection with a crime they were
              not convicted of — "alleged", "suspected", "linked to" are the exact
              constructions that create liability. Reporting a court-tested outcome
              is safe; repeating an accusation is not.

  guidelines  Reuses the existing Community Guidelines reviewer, which already
              refuses anything below OK, plus a check on glorification, which TikTok
              treats as a removal offence for crime content specifically.
"""

import re

# Free licences the media layer is allowed to use. Anything else should never have
# been fetched, so its presence here means something upstream regressed.
_ALLOWED_LICENCE = re.compile(r"\b(cc[ -]?by([ -]sa)?|cc0|public domain|ogl)\b", re.I)

# Accusation language about a person who has not been convicted. Each of these is a
# way of saying "this person did a crime" without a court having said so.
_ALLEGATION = re.compile(
    r"\b(alleged(ly)?|suspected|accused of|believed to have|thought to have|"
    r"reportedly (killed|murdered|attacked|abducted)|linked to the (murder|killing)|"
    r"prime suspect|person of interest|rumou?red|widely believed)\b",
    re.I,
)

# Wording that shows a claim IS court-tested, which is what makes naming someone safe.
_COURT_TESTED = re.compile(
    r"\b(convicted|found guilty|pleaded guilty|sentenced|jailed|"
    r"court heard|jury found|inquest (found|concluded)|coroner (found|ruled))\b",
    re.I,
)

# Glorification. TikTok removes crime content that celebrates the perpetrator, and it
# is also simply the wrong register for a victim's story.
_GLORIFY = re.compile(
    r"\b(legend(ary)?|notorious hard man|feared and respected|king of the underworld|"
    r"you have to admire|genius|mastermind who outsmarted|untouchable|"
    r"ran the manor|proper villain|respect(ed)? on the street)\b",
    re.I,
)


def check_copyright(images: list) -> tuple:
    """(ok, problems, attribution_lines) for the images used in a video."""
    problems, credits = [], []
    for img in images or []:
        title = img.get("title", "?")
        licence = img.get("licence", "") or ""
        if not _ALLOWED_LICENCE.search(licence):
            problems.append(f"{title}: licence '{licence or 'unknown'}' is not a free licence")
            continue
        credit = (img.get("credit") or "").strip()
        # CC-BY and CC-BY-SA both require the author be credited.
        if re.search(r"cc[ -]?by", licence, re.I) and not credit:
            problems.append(f"{title}: {licence} requires attribution but no author was recorded")
            continue
        credits.append(f"{title} — {credit} ({licence})")
    return (not problems), problems, credits


def check_defamation(script: str, caption: str = "") -> tuple:
    """
    (ok, problems). Flags accusation language that is not tied to a court outcome.

    Deliberately blunt: this refuses text a careful editor would query, rather than
    trying to decide who is alive or what was proven. A false positive costs one
    regenerated script; a false negative costs a libel claim.
    """
    text = f"{script}\n{caption}"
    problems = []
    for m in _ALLEGATION.finditer(text):
        window = text[max(0, m.start() - 220): m.end() + 220]
        if not _COURT_TESTED.search(window):
            problems.append(
                f"'{m.group(0)}' used without a court outcome nearby — "
                f"reads as an unproven accusation"
            )
    # Dedupe, keep it readable.
    seen, out = set(), []
    for p in problems:
        if p not in seen:
            seen.add(p); out.append(p)
    return (not out), out[:5]


def check_glorification(script: str, caption: str = "") -> tuple:
    """(ok, problems) — TikTok removes crime content that celebrates the offender."""
    text = f"{script}\n{caption}"
    hits = sorted({m.group(0) for m in _GLORIFY.finditer(text)})
    return (not hits), [f"glorifying language: '{h}'" for h in hits]


def run_all(script: str, caption: str, images: list, compliance_verdict: str) -> dict:
    """
    Every gate at once. Returns {ok, blocking, attribution}.

    compliance_verdict is the existing Community Guidelines result; anything other
    than OK is already a refusal and is surfaced here so one report covers everything.
    """
    blocking = []

    ok_c, probs_c, credits = check_copyright(images)
    blocking += [f"COPYRIGHT: {p}" for p in probs_c]

    ok_d, probs_d = check_defamation(script, caption)
    blocking += [f"DEFAMATION: {p}" for p in probs_d]

    ok_g, probs_g = check_glorification(script, caption)
    blocking += [f"GUIDELINES: {p}" for p in probs_g]

    if compliance_verdict != "OK":
        blocking.append(f"GUIDELINES: Community Guidelines verdict is {compliance_verdict}, not OK")

    return {"ok": not blocking, "blocking": blocking, "attribution": credits}
