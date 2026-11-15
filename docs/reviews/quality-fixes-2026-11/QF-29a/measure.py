"""QF-29a measure: does the golden-image mechanism catch a 1-px shift, a colour change and a toolchain change on
the two proof fixtures (a still and a video sample), with no false failure on an unchanged tree, and what does a
golden cost in the repository?

    python docs/reviews/quality-fixes-2026-11/QF-29a/measure.py <repo root>

Prints one JSON object. Renders run in a temporary store (no design is frozen; nothing in the repository is
written). Inputs: the committed proof fixtures (tests/golden_proof_designs.py). Local renders only, no network.
"""
from __future__ import annotations

import copy
import io
import json
import re
import sys
import tempfile
from pathlib import Path

STILL = "proof.youtube-thumbnail.gol-gumbaz.16x9"
VIDEO = "proof.end-card.gol-gumbaz.9x16"


def _bytes(im, fmt: str, **kw) -> int:
    buf = io.BytesIO()
    im.save(buf, fmt, **kw)
    return buf.tell()


def _dhash(im, size: int = 8) -> int:
    """A 64-bit difference hash (the perceptual-hash alternative the brief offered)."""
    g = im.convert("L").resize((size + 1, size))
    px = list(g.getdata())
    bits = 0
    for y in range(size):
        for x in range(size):
            bits = (bits << 1) | (px[y * (size + 1) + x] > px[y * (size + 1) + x + 1])
    return bits


def _storage(images: dict) -> dict:
    """Bytes per golden for each storage choice (the frames of one design)."""
    out = {"full_png": 0, "half_png": 0, "full_webp_lossless": 0, "half_webp_lossless": 0,
           "dhash_plus_8_crops_png": 0}
    for im in images.values():
        half = im.reduce(2)
        out["full_png"] += _bytes(im, "PNG", optimize=True)
        out["half_png"] += _bytes(half, "PNG", optimize=True)
        out["full_webp_lossless"] += _bytes(im, "WEBP", lossless=True, quality=100, method=6, exact=True)
        out["half_webp_lossless"] += _bytes(half, "WEBP", lossless=True, quality=100, method=6, exact=True)
        w, h = im.size
        crops = [im.crop((x, y, x + 128, y + 128)) for x in (w // 8, w // 2) for y in (h // 8, h // 3, h // 2, h * 3 // 4)]
        out["dhash_plus_8_crops_png"] += 8 + sum(_bytes(c, "PNG", optimize=True) for c in crops)
    return out


def main(root: Path) -> dict:
    goldens_py = root / "scripts" / "goldens.py"
    if not goldens_py.is_file():
        return {"mechanism": False, "golden_tests": 0, "planted_changes_caught": "0 of 4",
                "toolchain_changes_caught": "0 of 2", "unchanged_reruns_passing": "n/a (no goldens)"}
    sys.path.insert(0, str(root))
    from scripts import goldens as g
    proof = root / "tests" / "golden_proof_designs.py"
    result: dict = {"mechanism": True, "tolerance": g.TOLERANCE, "reference": {"scale": g.REFERENCE_SCALE,
                                                                                 "format": g.REFERENCE_FORMAT}}
    caught, toolchain = [], []
    with tempfile.TemporaryDirectory(prefix="qf29-measure-") as tmp:
        store = Path(tmp) / "store"
        for name in (STILL, VIDEO):
            rec: dict = {}
            manifest = g.take(name, "2026-11-15", proof, store, root, allow_dirty=True)
            v0 = manifest["variants"][0]
            rec["toolchain"] = v0["fingerprint_id"]
            if "x264" in v0["fingerprint"]:
                rec["x264_threads"] = int(re.search(r"\bthreads=(\d+)", v0["fingerprint"]["x264"]).group(1))
            rec["stored_bytes"] = sum(p.stat().st_size for p in (store / name).iterdir())
            reruns = []
            for _ in range(2):
                r = g.check(name, proof, store, root)
                reruns.append({"status": r.status})   # (render times: README; they vary with machine load)
            rec["unchanged_reruns"] = reruns
            base = r.rendering
            rec["storage_choices_bytes"] = _storage(base.images)
            # Host information, not fingerprint (round 2): NumPy's SIMD dispatch with X86_V3 switched off
            simd = g.render(name, proof, root, extra_env={"NPY_DISABLE_CPU_FEATURES": "X86_V3"})
            rec["numpy_simd_x86_v3_off"] = {
                "dispatch": simd.info.get("numpy_simd"), "same_fingerprint": simd.fingerprint_id == base.fingerprint_id,
                "pixels_identical": all(simd.frames[k]["sha256"] == f["sha256"] for k, f in base.frames.items())}
            plants = ["shift", "colour"]
            if name == VIDEO:
                plants.append("threads:6" if rec["x264_threads"] != 6 else "threads:3")
            for plant in plants:
                res = g.check(name, proof, store, root, extra_env={"QF29_PLANT": plant})
                entry = {"status": res.status, "stats": res.stats}
                if not plant.startswith("threads"):
                    caught.append(res.status == "design-changed")
                    entry["dhash_differs"] = [
                        _dhash(base.images[k]) != _dhash(res.rendering.images[k]) for k in sorted(base.images)]
                    # the same render judged under a toolchain the golden never saw: the tolerance's verdict
                    other = copy.deepcopy(manifest)
                    other["variants"][0]["fingerprint"]["platform"] = "elsewhere"
                    other["variants"][0]["fingerprint_id"] = g.fingerprint_id(other["variants"][0]["fingerprint"])
                    entry["under_new_toolchain"] = g.compare(name, other, res.rendering, store).message.split(". ")[-1]
                else:
                    toolchain.append(res.status == "toolchain-changed")
                    entry["x264_threads"] = int(re.search(r"\bthreads=(\d+)",
                                                          res.variant["fingerprint"]["x264"]).group(1))
                rec[f"plant_{plant.split(':')[0]}"] = entry
            if name == STILL:   # a library version change with identical pixels: still a loud failure
                other = copy.deepcopy(manifest)
                other["variants"][0]["fingerprint"]["pillow_libs"]["raqm"] = "0.10.1"
                other["variants"][0]["fingerprint_id"] = g.fingerprint_id(other["variants"][0]["fingerprint"])
                drift = g.compare(name, other, base, store)
                toolchain.append(drift.status == "toolchain-changed")
                rec["toolchain_raqm_change"] = {"status": drift.status, "message": drift.message}
            result[name] = rec
    result["planted_changes_caught"] = f"{sum(caught)} of {len(caught)}"
    result["toolchain_changes_caught"] = f"{sum(toolchain)} of {len(toolchain)}"
    result["unchanged_reruns_passing"] = (
        f"{sum(x['status'] == 'pass' for n in (STILL, VIDEO) for x in result[n]['unchanged_reruns'])} of 4")
    return result


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: measure.py <repo root>")
    print(json.dumps(main(Path(sys.argv[1]).resolve()), indent=1, sort_keys=True))
