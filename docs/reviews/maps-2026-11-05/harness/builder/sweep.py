import sys, json, time, logging
sys.path.insert(0, "/home/user/relic-and-riddle")
from pathlib import Path
from src import graphics
from tests import geo_truth
from tests.test_round15_review_2026_11_05 import SWEEP, P
graphics.VIDEO_FPS = 1
graphics.graded_frame = lambda frame, work, vignette=False: None
graphics._encode = lambda frames, path, n, width=1080, height=1920: [None for _ in frames] and path
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
lo, hi = int(sys.argv[2]), int(sys.argv[3])
sizes = [(1080, 1920), (1920, 1080)] if len(sys.argv) < 5 else [tuple(map(int, sys.argv[4].split("x")))]
res = open(out / f"res_{lo}_{hi}.jsonl", "a")
for k in range(lo, min(hi, len(SWEEP))):
    lat, lon, c = SWEEP[k]
    for w, h in sizes:
        lg = logging.getLogger(); [lg.removeHandler(x) for x in list(lg.handlers)]
        fh = logging.FileHandler(str(out / f"log_{k}_{w}.txt"), mode="w"); lg.addHandler(fh); lg.setLevel(logging.INFO)
        t = time.time()
        try:
            graphics.render_map(P(lat, lon, c, f"Site {k + 1}"), out / f"s{k}_{w}.mp4", width=w, height=h)
            lay = json.loads((out / f"s{k}_{w}.mp4.layout.json").read_text())
            f = geo_truth.faults(lay)
            r = {"k": k, "w": w, "site": [lat, lon, c], "home": (lay["country"] or {}).get("text"), "drop": lay.get("home_drop"), "faults": f, "t": round(time.time() - t, 1)}
        except Exception as e:
            import traceback
            r = {"k": k, "w": w, "site": [lat, lon, c], "error": repr(e)[:300], "tb": traceback.format_exc()[-800:]}
        res.write(json.dumps(r) + "\n"); res.flush()
        print(json.dumps(r)[:300], flush=True)
