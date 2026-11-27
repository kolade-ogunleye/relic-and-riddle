import sys, time, json, shutil, tempfile
from pathlib import Path
sys.path.insert(0, r"C:\tmp\qf-w-subject-c6")
from src import photo_subject as ps
pool = json.load(open(r"C:\tmp\qf-scratch-c6\subject\gt.json"))
paths = {o["sha256"]: o["path"] for o in json.load(open(r"C:\tmp\qf-scratch-c6\subject\photos_all.json"))}
tmp = Path(tempfile.mkdtemp(dir=r"C:\tmp\qf-scratch-c6\subject"))
ts = []
for g in pool[:: max(1, len(pool) // 8)][:8]:
    src = Path(paths[g["sha256"]]); dst = tmp / src.name
    shutil.copy(src, dst)
    t = time.perf_counter(); r = ps.record(dst); ts.append(time.perf_counter() - t)
    print(g["id"], round(ts[-1], 2), r["box"], r["confidence"], flush=True)
print("mean s/photo (incl. decode, first includes model load):", sum(ts) / len(ts), "median", sorted(ts)[len(ts)//2])
