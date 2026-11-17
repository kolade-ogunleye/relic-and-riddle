import json
import pickle
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
from src import config, picture_qa

with open(sys.argv[2], "rb") as fh:
    rec = pickle.load(fh)
rows = [r for r in rec if r["sid"] == 2]
py, px, F = 81, 35, 200
win = (slice(py, py + F), slice(px, px + F))
t = np.array([r["t"] for r in rows])
L = np.stack([r["lum"][win] for r in rows])          # frames x 200 x 200 (0.1-degree boxes)
out = {"field": {"rows": [py, py + F], "cols": [px, px + F], "grid": "599 x 599 fine stage"}}
out["field_mean"] = {f"{tt:.3f}": round(float(m), 3) for tt, m in zip(t, L.mean((1, 2))) if 4.3 <= tt <= 4.9}


def swings_count(series, lo, hi):
    """Transitions per element (WCAG general rule, _Swings) on frames lo <= t <= hi."""
    sw = picture_qa._Swings(config.PICTURE_QA_FLASH_LUMINANCE, config.PICTURE_QA_FLASH_DARK)
    n = np.zeros(series.shape[1:], int)
    for tt, v in zip(t, series):
        up, down = sw.frame(v)
        if lo <= tt <= hi:
            n += up | down
    return n


fm = L.mean((1, 2))[:, None]
out["field_mean_transitions_3.7_4.7"] = int(swings_count(fm, 3.7, 4.7)[0])
for name, k in (("1_degree", 20), ("0.5_degree", 10), ("0.1_degree", 1)):
    B = L.reshape(len(t), F // k, k, F // k, k).mean((2, 4))
    n = swings_count(B, 3.7, 4.7)
    out[f"{name}_boxes_transitions_3.7_4.7"] = {f">={j}": round(float((n >= j).mean()), 3) for j in range(1, 8)}
    out[f"{name}_boxes_max"] = int(n.max())
# per field transition: share of 1-degree boxes changing >= 0.10 up/down vs previous frame
B = L.reshape(len(t), 10, 20, 10, 20).mean((2, 4))
steps = {}
for i in range(1, len(t)):
    if 4.45 <= t[i] <= 4.72:
        d = B[i] - B[i - 1]
        steps[f"{t[i]:.3f}"] = {"boxes_up_0.1": round(float((d >= 0.1).mean()), 2),
                                "boxes_down_0.1": round(float((d <= -0.1).mean()), 2),
                                "mean_change": round(float(d.mean()), 3)}
out["frame_to_frame_1_degree"] = steps
print(json.dumps(out, indent=1))
Path(sys.argv[3]).write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8", newline="\n")
np.save(sys.argv[3].replace(".json", "_curves.npy"), np.concatenate([t[:, None], L.mean((1, 2))[:, None],
        L.reshape(len(t), 10, 20, 10, 20).mean((2, 4)).reshape(len(t), -1)], 1))
