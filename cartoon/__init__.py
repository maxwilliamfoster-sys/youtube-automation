"""Cartoon visuals for BuriedCasefiles: every sentence of the narration is acted out
by round-headed stick characters in simple sets, timed to the spoken words.

    from cartoon import make_background
    bg = make_background(story, tts, out_path)   # silent MP4, narration length + 0.6 s
"""
from . import planner, engine


def make_background(script, word_timings, duration, out_path, title="", use_llm=True, log=print):
    """Plan + render. Returns (bg_path, plan)."""
    pl = planner.plan(script, word_timings, duration, title=title, use_llm=use_llm, log=log)
    engine.render(pl, out_path, seed_text=title or script[:40], log=log)
    return out_path, pl
