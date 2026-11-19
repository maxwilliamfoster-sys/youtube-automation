"""Local check: plan a script and write a contact sheet of one frame per scene.

  py -m cartoon.preview path/to/timing.json [--llm] [--video]
timing.json: {"script": "...", "words": [{"word","start","end"}], "duration": s}
"""
import json
import os
import sys

from . import engine, planner


def main():
    src = sys.argv[1]
    d = json.load(open(src, encoding="utf-8"))
    pl = planner.plan(d["script"], d["words"], d["duration"], title=d.get("title", ""), use_llm="--llm" in sys.argv)
    out = os.path.join(os.path.dirname(os.path.abspath(src)), "preview")
    os.makedirs(out, exist_ok=True)
    json.dump(pl, open(os.path.join(out, "plan.json"), "w"), indent=1)
    from PIL import Image
    tiles = []
    for i, sc in enumerate(pl["scenes"]):
        t = min(sc["t1"] - 0.05, max([sc["t0"] + 0.9] + [x["t"] + 0.5 for x in sc["props"]]))
        path = os.path.join(out, f"s{i:02d}.png")
        engine.still(pl, t, path, seed_text=d.get("title", ""))
        tiles.append(Image.open(path).resize((270, 480)))
        print(i, sc["set"], sc["source"], [a["role"] + ":" + a["action"] for a in sc["actors"]],
              [(x["kind"], x["value"]) for x in sc["props"]], "|", sc["text"][:60])
    cols = 6
    sheet = Image.new("RGB", (270 * cols, 480 * ((len(tiles) + cols - 1) // cols)))
    for i, im in enumerate(tiles):
        sheet.paste(im, ((i % cols) * 270, (i // cols) * 480))
    sheet.save(os.path.join(out, "sheet.png"))
    if "--video" in sys.argv:
        engine.render(pl, os.path.join(out, "bg.mp4"), seed_text=d.get("title", ""))


if __name__ == "__main__":
    main()
