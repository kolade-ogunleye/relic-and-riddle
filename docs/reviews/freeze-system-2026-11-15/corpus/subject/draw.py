"""Draw Reel covers (+ grid tile) and recorded slides / Story frames in one tree, optionally with the labelled boxes
fed as subjects (photo_subject.use_oracle; tip tree only).
python draw.py <tree> <dev> <out> <cases.json> <oracle.json|-> [runs...]"""
import json, os, sys, socket, warnings, shutil, traceback
from pathlib import Path
warnings.simplefilter("ignore")
tree, dev, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve()
cases = json.loads(Path(sys.argv[4]).read_text(encoding="utf-8"))
oracle = None if sys.argv[5] == "-" else json.loads(Path(sys.argv[5]).read_text())
runs = sys.argv[6:]
out.mkdir(parents=True, exist_ok=True)
os.environ.update(FONT_SIZE="80", PYTHONIOENCODING="utf-8")
def _off(*a, **k): raise OSError("offline")
socket.create_connection = _off; socket.socket.connect = _off
sys.path.insert(0, str(tree)); os.chdir(tree)
if oracle is not None:
    from src import photo_subject
    photo_subject.use_oracle(oracle)
from src.visual_engine import VisualEngine
from src import multi_format as mf
res = {}
for c in cases:
    d = out / "covers" / c["name"]
    if d.exists(): shutil.rmtree(d)
    d.mkdir(parents=True)
    payload = json.loads((dev / "output" / c["run"] / "script_metadata.json").read_text(encoding="utf-8"))
    try:
        VisualEngine().generate_thumbnails(dev / c["hero"], dict(payload), d, style=c.get("style", "badge"), variants=False)
        lay = json.loads((d / "still_layout.json").read_text(encoding="utf-8"))
        res[c["name"]] = {k: (v.get("meta") or {}).get("photo_fit") if isinstance(v, dict) else None for k, v in lay.items()}
        print("cover ok", c["name"], flush=True)
    except Exception as e:
        res[c["name"]] = {"error": repr(e)[:300]}; traceback.print_exc()
for run in runs:
    for sub in ("carousel", "carousel_fb", "stories"):
        src = dev / "output" / run / sub
        if not (src / "still_layout.json").is_file(): continue
        dst = out / "decks" / run / sub
        if dst.exists(): shutil.rmtree(dst)
        dst.mkdir(parents=True)
        for f in src.iterdir():
            if f.is_file(): shutil.copy2(f, dst / f.name)
        recs = json.loads((dst / "still_layout.json").read_text(encoding="utf-8"))
        for name in sorted(recs):
            recipe = recs[name].get("recipe") or {}
            if not recipe.get("photo"): continue
            try:
                (mf.redraw_story_frame if sub == "stories" else mf.redraw_carousel_slide)(dst / name, recipe)
                print("ok", run, sub, name, flush=True)
            except Exception as e:
                res[f"{run}/{sub}/{name}"] = {"error": repr(e)[:300]}; print("ERR", run, sub, name, repr(e)[:200], flush=True)
(out / "records.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8", newline="\n")
