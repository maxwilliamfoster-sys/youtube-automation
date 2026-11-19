"""Cartoon visuals for BuriedCasefiles: every sentence of the narration is acted out
by round-headed stick characters in simple sets, timed to the spoken words.

    from cartoon import make_background, qc
    bg, plan = make_background(script, word_timings, duration, out_path,
                               hook_text=..., cta_text=..., case_tag=...)
    ... compose captions, cards and audio over bg ...
    verdict = qc.check(plan, final_video)     # the cartoon gate
"""
from . import planner, engine, layout, qc  # noqa: F401


def make_background(script, word_timings, duration, out_path, title="", use_llm=True, log=print,
                    hook_text="", cta_text="", case_tag=""):
    """Plan + render, keeping faces and pop-ups off the text the composer will add.
    Returns (bg_path, plan)."""
    pl = planner.plan(script, word_timings, duration, title=title, use_llm=use_llm, log=log)
    zones = layout.text_zones(duration, hook_text=hook_text, cta_text=cta_text, case_tag=case_tag)
    engine.render(pl, out_path, seed_text=title or script[:40], log=log, zones=zones)
    return out_path, pl
