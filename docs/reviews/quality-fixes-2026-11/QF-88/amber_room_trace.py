"""argv: <repo root> <master> <out.npz> [t0 t1]. Runs the repo's picture_qa.measure on the master (read-only) and
records, for every _FlashStage instance (the screen stage and the fine stage), each frame's general and red
per-sample up/down masks and the field counts/single arrays after the step, for frames with t0 <= t <= t1."""
import pickle
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
from src import picture_qa

master, out = Path(sys.argv[2]), Path(sys.argv[3])
t0, t1 = (float(sys.argv[4]), float(sys.argv[5])) if len(sys.argv) > 5 else (2.5, 6.5)
rec = []
Base = picture_qa._FlashStage


class Rec(Base):
    serial = 0

    def __init__(self):
        super().__init__()
        Rec.serial += 1
        self.sid = Rec.serial
        self.captured = {}
        for kind, tr in self.trackers.items():
            orig = tr.frame

            def wrapped(*a, _o=orig, _k=kind):
                up, down = _o(*a)
                self.captured[_k] = (up.copy(), down.copy())
                return up, down
            tr.frame = wrapped

    def step(self, time_s, r, g, b):
        res = Base.step(self, time_s, r, g, b)
        if t0 <= time_s <= t1:
            lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
            row = {"sid": self.sid, "t": time_s, "lum": lum.astype(np.float32), "res": res}
            for k in ("general", "red"):
                up, down = self.captured[k]
                row[k] = {"up": up, "down": down, "counts": self.counts[k].copy(), "single": self.single[k].copy(),
                          "last": self.last[k].copy(), "own": self.own[k].copy()}
            rec.append(row)
        return res


picture_qa._FlashStage = Rec
m = picture_qa.measure(master)
c = next(c for c in m["checks"] if c["name"] == "picture_flash")
print(c["detail"])
with open(out, "wb") as fh:
    pickle.dump(rec, fh, protocol=4)
print(len(rec), "records", sorted({r["sid"] for r in rec}))
