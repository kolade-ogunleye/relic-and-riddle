import sys, json, hashlib
from pathlib import Path
import os
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[5]))
import photo_fitness as cp
dev = Path(os.environ["RR_DEV"])  # the dev checkout
pool = {}
for run in sorted((dev/"output").glob("run_*")):
    if not run.is_dir(): continue
    for p in cp.run_pool(dev, run):
        pool.setdefault(str(p.resolve()), []).append(run.name)
out = []
for p, runs in sorted(pool.items()):
    out.append({"path": p, "runs": runs, "sha256": hashlib.sha256(Path(p).read_bytes()).hexdigest()})
json.dump(out, open("pool.json","w"), indent=1)
print(len(out), len({o['sha256'] for o in out}))
from collections import Counter
print(Counter(Path(o['path']).parent.name for o in out))
