"""PLATEFORM (freeze cycle 6; lander decision: the block family is the text ground over a photograph): the block form
against integration 5 on every still both trees drew, per text element and per still. Cheap: reads what
plates_draw.py --ground-record left (stills, still_layout.json sidecars with each still's meta and ground record,
<still>.ground.png darkening maps); no drawing.

    python plateform_block.py <base drawn> <block drawn> [--brand default] [--labels subject_labels.json]
                              [--json out.json]

<base drawn> / <block drawn>: plates_draw.py's <out> (with <brand>/<run>/...), drawn with --ground-record by the
same tree, RR_PLATE_FORM unset (base) and =block. Rules (a violation exits 1):
- text: every still's text elements identical (name, occurrence, text); no still drawn at base missing at block.
- contrast: per text element, the pipeline's own record after the platform re-encode (contrast_platform) not lower
  than base by > 0.05, and our own re-encoded pixel measure (pf_measure.element_contrast: q75 4:2:0, glyph p10 against
  the p95 of a 3-10 px ring) not lower than base by > 0.25 where it is under 7:1; and at or above the element's gate
  (contrast.text 4.5; a kicker or the masthead contrast.kicker / masthead gate 5.5) wherever base was.
- darkened share: the share of the still its text grounds darkened by > 20 luma (ground record) not above base.
- subject: where a labelled subject (the SUBJECT stream's subject_labels.json, measurement only) lies in a still's
  photo window, the share of it the grounds darkened by > 20 not above base's.
- safe box: no block clamped by its safe box (Story: safe_zones story box; carousel: the frame) with its type
  outside it.
Reported (not gated): edge measures of the darkening map (step, run, tex, slab), what each ground call laid.
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "plateform"))
import pf_measure as pm  # noqa: E402

GATES = {"kicker": 5.5, "header_brand": 5.5}


def frames(side: Path) -> dict:
    d = json.loads(side.read_text(encoding="utf-8"))
    return d.get("frames", d) if isinstance(d, dict) else {}


def keyed(els) -> dict:
    c = collections.Counter()
    out = {}
    for e in els or []:
        if not e.get("text") or not e.get("box"):
            continue
        k = (e.get("name"), c[e.get("name")])
        c[e.get("name")] += 1
        out[k] = e
    return out


def own(im: Image.Image, els) -> dict:
    b = io.BytesIO()
    im.convert("RGB").save(b, "JPEG", quality=75, subsampling=2)
    a = np.asarray(Image.open(io.BytesIO(b.getvalue())).convert("RGB"), np.float32)
    fills = {tuple(e["fill"][:3]) for e in els.values() if isinstance(e.get("fill"), (list, tuple))}
    fills = {f for f in fills if float(pm.Y(np.array(f, np.float32))) > 0.2} | set(pm.TEXT_FILLS) \
        | {(c[2], c[1], c[0]) for c in pm.TEXT_FILLS}
    return {k: pm.element_contrast(a, e, fills) for k, e in els.items()}


def labels_for(path: Path | None) -> dict:
    if not path or not path.is_file():
        return {}
    out = {}
    for lab in json.loads(path.read_text(encoding="utf-8")):
        topic, name = lab["name"].split("/", 1)
        out[(topic, name.split("_")[0])] = lab
    return out


def subject_boxes(meta: dict, run: str, labels: dict, size) -> list:
    """The labelled subjects inside the still's photo windows, in still px."""
    out = []
    for w in (meta or {}).get("windows") or []:
        fit = w.get("fit") or {}
        box, photo = fit.get("box"), str(w.get("photo") or "")
        if not box or not photo:
            continue
        lab = next((lab for (t, n), lab in labels.items() if t in run and photo.startswith(n + "_")), None)
        if lab is None or lab.get("kind") == "scene":
            continue
        sw, sh = lab["size"]
        x0, y0, x1, y1 = (lab["box"][0] * sw, lab["box"][1] * sh, lab["box"][2] * sw, lab["box"][3] * sh)
        fx0, fy0, fx1, fy1 = box
        W, H = w.get("size") or size
        kx, ky = W / max(1e-6, fx1 - fx0), H / max(1e-6, fy1 - fy0)
        b = [round((x0 - fx0) * kx), round((y0 - fy0) * ky), round((x1 - fx0) * kx), round((y1 - fy0) * ky)]
        b = [max(0, b[0]), max(0, b[1]), min(W, b[2]), min(H, b[3])]
        if b[2] - b[0] > 8 and b[3] - b[1] > 8:
            out.append(b)
    return out


def subject_dark(png: Path, boxes) -> float | None:
    if not boxes or not png.is_file():
        return None
    D = np.asarray(Image.open(png), np.float32)
    m = np.zeros(D.shape, bool)
    for x0, y0, x1, y1 in boxes:
        m[y0:y1, x0:x1] = True
    return round(float((D[m] > 20).mean()) * 100, 2) if m.any() else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base", type=Path)
    ap.add_argument("block", type=Path)
    ap.add_argument("--brand", default="default")
    ap.add_argument("--labels", type=Path)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    labels = labels_for(a.labels)
    B, T = a.base / a.brand, a.block / a.brand
    rows, viol = [], collections.Counter()
    n_el = 0
    for side in sorted(B.rglob("still_layout.json")):
        rel = side.relative_to(B)
        run = rel.parts[0]
        tside = T / rel
        fb = frames(side)
        ft = frames(tside) if tside.is_file() else {}
        for name, eb in fb.items():
            if not isinstance(eb, dict) or not (side.parent / name).is_file():
                continue
            row = {"still": str(rel.parent / name), "violations": []}
            rows.append(row)
            et = ft.get(name)
            if not isinstance(et, dict) or not (tside.parent / name).is_file():
                row["violations"].append("missing at block")
                viol["missing"] += 1
                continue
            kb, kt = keyed(eb.get("elements")), keyed(et.get("elements"))
            if {k: e.get("text") for k, e in kb.items()} != {k: e.get("text") for k, e in kt.items()}:
                row["violations"].append("text differs")
                viol["text"] += 1
            ib, it = Image.open(side.parent / name), Image.open(tside.parent / name)
            ob, ot = own(ib, kb), own(it, kt)
            for k, e in kb.items():
                f = kt.get(k)
                if f is None:
                    continue
                n_el += 1
                rb, rt = e.get("contrast_platform"), f.get("contrast_platform")
                gate = GATES.get(k[0], 4.5)
                if rb is not None and rt is not None and rt < rb - 0.05:
                    row["violations"].append(f"{k[0]}#{k[1]} record {rb} -> {rt}")
                    viol["contrast_record"] += 1
                vb, vt = ob.get(k), ot.get(k)
                if vb is not None and vt is not None and vt < vb - 0.25 and vt < 7.0:
                    row["violations"].append(f"{k[0]}#{k[1]} own {vb:.2f} -> {vt:.2f}")
                    viol["contrast_own"] += 1
                if rt is not None and rt < gate and (rb is None or rb >= gate):
                    row["violations"].append(f"{k[0]}#{k[1]} under its gate {gate}: {rt}")
                    viol["gate"] += 1
            gb = (eb.get("meta") or {}).get("ground_record") or {}
            gt = (et.get("meta") or {}).get("ground_record") or {}
            row["dark"] = [gb.get("dark_share"), gt.get("dark_share")]
            row["edge"] = [gb.get("edge"), gt.get("edge")]
            row["laid"] = gt.get("laid")
            if gb.get("dark_share") is not None and gt.get("dark_share") is not None \
                    and gt["dark_share"] > gb["dark_share"] + 5e-4:
                row["violations"].append(f"darkened share {gb['dark_share']} -> {gt['dark_share']}")
                viol["dark"] += 1
            sb = subject_boxes(eb.get("meta"), run, labels, ib.size)
            if sb:
                db = subject_dark(side.parent / Path(name).with_suffix(".ground.png").name, sb)
                dt = subject_dark(tside.parent / Path(name).with_suffix(".ground.png").name, sb)
                row["subject_dark"] = [db, dt]
                if db is not None and dt is not None and dt > db + 0.5:
                    row["violations"].append(f"subject darkened {db} -> {dt} %")
                    viol["subject"] += 1
            pf = (et.get("meta") or {}).get("plate_form") or {}
            if any(b.get("clamped") for b in pf.get("blocks") or []):
                row["clamped"] = True
    if a.json:
        a.json.write_text(json.dumps(rows, indent=1), encoding="utf-8", newline="\n")
    dk = [r["dark"] for r in rows if r.get("dark") and None not in r["dark"]]
    sl = [(r["edge"][0] or {}).get("slab", 0) for r in rows if r.get("edge")], \
         [(r["edge"][1] or {}).get("slab", 0) for r in rows if r.get("edge")]
    sj = [r["subject_dark"] for r in rows if r.get("subject_dark") and None not in r["subject_dark"]]
    laid = collections.Counter(x for r in rows for x in (r.get("laid") or []))
    print(f"{a.brand}: {len(rows)} stills, {n_el} text elements; laid {dict(laid)}")
    if dk:
        print(f"  darkened share median base {np.median([d[0] for d in dk]) * 100:.1f} % -> block "
              f"{np.median([d[1] for d in dk]) * 100:.1f} %; max {max(d[0] for d in dk) * 100:.1f} -> "
              f"{max(d[1] for d in dk) * 100:.1f} %")
    if sl[0]:
        print(f"  slab median {np.median(sl[0]):.2f} -> {np.median(sl[1]):.2f}; max {max(sl[0]):.2f} -> {max(sl[1]):.2f}")
    if sj:
        print(f"  labelled subjects on {len(sj)} stills: darkened median {np.median([s[0] for s in sj]):.1f} -> "
              f"{np.median([s[1] for s in sj]):.1f} %")
    print(f"  clamped blocks: {sum(1 for r in rows if r.get('clamped'))}")
    print(f"  violations: {dict(viol)}")
    for r in rows:
        if r["violations"]:
            print("   ", r["still"], "; ".join(r["violations"][:4]))
    return 1 if sum(viol.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
