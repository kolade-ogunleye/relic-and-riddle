"""The subject's share in each drawn Reel cover (above the type) and its profile tile (y 420-1500, above the type),
read from the cover's own recipe (composition), per config. python measure_covers.py <cases.json> cfg1 cfg2 ..."""
import json, sys
from pathlib import Path
H = Path(__file__).resolve().parent
cases = json.load(open(sys.argv[1]))
cfgs = sys.argv[2:]


def rec_of(cfg, name):
    f = H / cfg / "covers" / name / "still_layout.json"
    if not f.is_file():
        return None
    d = json.loads(f.read_text(encoding="utf-8"))
    return d.get("thumbnail_cover.jpg")


def inter(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0]); h = min(a[3], b[3]) - max(a[1], b[1])
    return max(0, w) * max(0, h)


rows = []
for c in cases:
    recs = {cfg: rec_of(cfg, c["name"]) for cfg in cfgs}
    fb = None
    for r in recs.values():
        comp = ((r or {}).get("recipe") or {}).get("composition") or {}
        if comp.get("focus_box"):
            fb = comp["focus_box"]; break
        foc = ((r or {}).get("recipe") or {}).get("focus") or {}
        if foc.get("focus_box"):
            fb = foc["focus_box"]
    row = {"name": c["name"], "reviewed": c.get("reviewed")}
    for cfg, r in recs.items():
        if r is None or fb is None:
            row[cfg] = None; continue
        comp = (r.get("recipe") or {}).get("composition") or {}
        if not comp:
            row[cfg] = {"mode": "?"}; continue
        sw, sh = comp["source_size"]; sb = comp["source_box"]; ib = comp["image_box"]
        k = (ib[2] - ib[0]) / max(1e-6, sb[2] - sb[0])
        s = [fb[0] * sw, fb[1] * sh, fb[2] * sw, fb[3] * sh]
        cv = [ib[0] + (s[0] - sb[0]) * k, ib[1] + (s[1] - sb[1]) * k, ib[0] + (s[2] - sb[0]) * k, ib[1] + (s[3] - sb[1]) * k]
        T = min([e["box"][1] for e in r.get("elements") or [] if e.get("box")] or [1920])
        area = max(1e-9, (cv[2] - cv[0]) * (cv[3] - cv[1]))
        vis = [max(ib[0], 0), max(ib[1], 0), min(ib[2], 1080), min(ib[3], T)]
        reel = inter(cv, vis) / area
        tile = inter(cv, [vis[0], max(vis[1], 420), vis[2], min(vis[3], 1500)]) / area
        row[cfg] = {"mode": comp.get("mode"), "reel": round(reel, 3), "tile": round(tile, 3)}
    rows.append(row)
json.dump(rows, open(H / "covers_measured.json", "w"), indent=1)
E = 1e-3
cnt = {}
for r in rows:
    t = r.get(cfgs[-1]);
    if not t or "reel" not in t:
        continue
    for cfg in cfgs[:-1]:
        b = r.get(cfg)
        if not b or "reel" not in b:
            continue
        key = f"vs {cfg}"
        cnt.setdefault(key, {"less_reel": [], "less_tile": [], "changed": 0, "contain_base": 0, "contain_tip": 0})
        if t["reel"] < b["reel"] - E: cnt[key]["less_reel"].append(r["name"])
        if t["tile"] < b["tile"] - E: cnt[key]["less_tile"].append(r["name"])
        cnt[key]["changed"] += t["mode"] != b["mode"]
        cnt[key]["contain_base"] += b["mode"] == "contain"
        cnt[key]["contain_tip"] += t["mode"] == "contain"
print(json.dumps(cnt, indent=1))
for r in rows:
    modes = [(r.get(c) or {}).get("mode") for c in cfgs]
    if len(set(modes)) > 1 or r.get("reviewed"):
        print(r["name"], " | ".join(f"{c}:{(r.get(c) or {}).get('mode')} reel {(r.get(c) or {}).get('reel')} tile {(r.get(c) or {}).get('tile')}" for c in cfgs))
