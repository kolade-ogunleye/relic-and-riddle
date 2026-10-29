"""Old vs candidate film grades through ffmpeg on real stills, raw and colour-balanced.

usage: harness.py <repo> <workdir> <variants.json or ''> [--per-topic N]
"""
import json, subprocess, sys, os, colorsys
from pathlib import Path
import numpy as np
from PIL import Image

REPO = Path(sys.argv[1]).resolve()
WORK = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(REPO))
from src import colour_balance as cb  # noqa: E402

cb.CACHE_DIR = WORK / "cb_cache"
FF = "ffmpeg"
TOPICS = ["derinkuyu_underground_city", "richat_structure_sahara_eye", "nazca_lines_runway_paradox",
          "roman_self_healing_concrete", "amber_room_heist", "odessa_catacombs", "gol_gumbaz_whispering_gallery",
          "sunken_city_of_heracleion", "dyatlov_pass_incident", "camp_century_nuclear_ice_city"]
PER = int(sys.argv[sys.argv.index("--per-topic") + 1]) if "--per-topic" in sys.argv else 4
PRE = "curves=master='0/0.02 0.5/0.50 1/0.98'"


def esc(p):
    return str(Path(p).resolve()).replace("\\", "/").replace(":", "\\:")


def pick(topic):
    files = sorted((REPO / "assets" / topic).glob("*_commons.jpg"))
    uniq, seen = [], set()
    for f in files:
        h = f.stat().st_size
        if h not in seen:
            seen.add(h); uniq.append(f)
    if len(uniq) <= PER:
        sel = uniq
    else:
        idx = np.linspace(0, len(uniq) - 1, PER).round().astype(int)
        sel = [uniq[i] for i in idx]
    extra = REPO / "assets" / topic / "25_commons.jpg"
    if topic.startswith("derinkuyu") and extra not in sel:
        sel.append(extra)
    return sel


def small(src, dst, side=720):
    with Image.open(src) as im:
        im = im.convert("RGB"); im.thumbnail((side, side), Image.LANCZOS)
        w, h = im.size
        im = im.crop((0, 0, w // 2 * 2, h // 2 * 2))
        im.save(dst, "PNG")


def grade(src, chain, dst):
    vf = f"scale=out_range=tv,format=yuv420p,format=yuv420p,{chain},format=yuv420p"
    subprocess.run([FF, "-v", "error", "-y", "-i", str(src), "-vf", vf, "-frames:v", "1", str(dst)], check=True)
    return np.asarray(Image.open(dst).convert("RGB"), dtype=np.float64)


def hsv(a):
    return cb._hsv(a)


def cast_of(a):
    n = cb.scene(a)["neutral"]
    if n is None:
        return None, None
    full = n.mean() / np.maximum(n, 1e-6)
    return float(np.max(np.abs(full - 1))), [round(float(v), 1) for v in n]


def metrics(ref, out):
    h0, s0 = hsv(ref); h1, s1 = hsv(out)
    m = (s0 > 0.12) & (s1 > 0.12) & (ref.max(-1) > 25)
    dh = (h1 - h0 + 180) % 360 - 180
    hue_shift = float(np.average(np.abs(dh[m]), weights=np.minimum(s0, s1)[m])) if m.sum() > 50 else 0.0
    # signed mean hue drift of the image's mean colour
    def mhue(a):
        return colorsys.rgb_to_hsv(*(a.reshape(-1, 3).mean(0) / 255))
    hh0, ss0, _ = mhue(ref); hh1, ss1, _ = mhue(out)
    y0, y1 = cb._luma(ref), cb._luma(out)
    c0, n0 = cast_of(ref); c1, n1 = cast_of(out)
    return {
        "hue_shift_deg": round(hue_shift, 2),
        "sat_ratio": round(float(s1.mean() / max(s0.mean(), 1e-6)), 3),
        "mean_rgb_before": [round(float(v), 1) for v in ref.reshape(-1, 3).mean(0)],
        "mean_rgb_after": [round(float(v), 1) for v in out.reshape(-1, 3).mean(0)],
        "mean_colour_sat_before": round(ss0, 3), "mean_colour_sat_after": round(ss1, 3),
        "blue_ratio": round(float(out[..., 2].mean() / max(ref[..., 2].mean(), 1e-6)), 3),
        "b_over_g_ratio": round(float((out[..., 2].mean() / max(out[..., 1].mean(), 1e-6)) /
                                      max(ref[..., 2].mean() / max(ref[..., 1].mean(), 1e-6), 1e-6)), 3),
        "cast_before": None if c0 is None else round(c0, 4), "cast_after": None if c1 is None else round(c1, 4),
        "neutral_before": n0, "neutral_after": n1,
        "luma_std_ratio": round(float(y1.std() / max(y0.std(), 1e-6)), 3),
        "luma_mean_ratio": round(float(y1.mean() / max(y0.mean(), 1e-6)), 3),
    }


def main():
    variants = json.loads(Path(sys.argv[3]).read_text()) if len(sys.argv) > 3 and sys.argv[3] else {}
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "in").mkdir(exist_ok=True); (WORK / "out").mkdir(exist_ok=True)
    rows = []
    for t in TOPICS:
        if not (REPO / "assets" / t).is_dir():
            continue
        for f in pick(t):
            for state in ("raw", "balanced"):
                src = f if state == "raw" else cb.balanced(f)
                tag = f"{t}__{f.stem}__{state}"
                sp = WORK / "in" / f"{tag}.png"
                if not sp.exists():
                    small(src, sp)
                ref = grade(sp, "null", WORK / "out" / f"{tag}__ref.png")
                row = {"topic": t, "still": f.name, "state": state, "balanced_changed": src != f}
                for name, chain in variants.items():
                    out = grade(sp, chain, WORK / "out" / f"{tag}__{name}.png")
                    row[name] = metrics(ref, out)
                rows.append(row)
                print(tag, {k: (v["cast_before"], v["cast_after"], v["sat_ratio"], v["hue_shift_deg"], v["luma_std_ratio"])
                            for k, v in row.items() if isinstance(v, dict)}, flush=True)
    (WORK / "rows.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
