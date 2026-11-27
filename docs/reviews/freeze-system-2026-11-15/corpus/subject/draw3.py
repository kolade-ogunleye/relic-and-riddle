"""Round 3: draw Reel covers (+ profile tile) through the production path (VisualEngine.generate_thumbnails) in one
tree, with oracle boxes fed as subjects or none. In a tree without photo_subject (the base), the oracle is fed through
cover_design.reviewed_focus (the one interface the base reads), so the base draws the cycle-5 rules WITH the box.
python draw3.py <tree> <dev> <out> <cases.json> <oracle.json|-> [k n]   (k-th of n chunks)"""
import json, os, sys, socket, warnings, shutil, traceback, hashlib
from pathlib import Path
warnings.simplefilter("ignore")
tree, dev, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve()
cases = json.loads(Path(sys.argv[4]).read_text(encoding="utf-8"))
oracle = None if sys.argv[5] == "-" else json.loads(Path(sys.argv[5]).read_text())
if len(sys.argv) > 7:
    k, n = int(sys.argv[6]), int(sys.argv[7])
    cases = cases[k::n]
out.mkdir(parents=True, exist_ok=True)
os.environ.update(FONT_SIZE="80", PYTHONIOENCODING="utf-8")
def _off(*a, **kw): raise OSError("offline")
socket.create_connection = _off; socket.socket.connect = _off
sys.path.insert(0, str(tree)); os.chdir(tree)
if oracle is not None:
    try:
        from src import photo_subject
        photo_subject.use_oracle(oracle)
    except ImportError:
        from src import cover_design
        real = cover_design.reviewed_focus

        def fed(path, kept=None):
            got = real(path, kept)
            if got is not None:
                return got
            try:
                d = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            except OSError:
                return None
            return {"focus_box": list(oracle[d]), "sha256": d, "reviewed": "oracle"} if d in oracle else None
        cover_design.reviewed_focus = fed
from src.visual_engine import VisualEngine
for c in cases:
    d = out / "covers" / c["name"]
    if (d / "thumbnail_cover.jpg").is_file():
        continue
    if d.exists(): shutil.rmtree(d)
    d.mkdir(parents=True)
    payload = json.loads((dev / "output" / c["run"] / "script_metadata.json").read_text(encoding="utf-8"))
    try:
        VisualEngine().generate_thumbnails(dev / c["hero"], dict(payload), d, style=c.get("style", "badge"), variants=False)
        print("cover ok", c["name"], flush=True)
    except Exception as e:
        (d / "error.txt").write_text(repr(e)[:500], encoding="utf-8"); print("ERR", c["name"], repr(e)[:200], flush=True)
