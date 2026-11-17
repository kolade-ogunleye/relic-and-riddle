"""QF-13 measurement: lossless prepared photo layers. Prints JSON and writes nothing outside a temporary folder.

    python docs/reviews/quality-fixes-2026-11/QF-13/measure.py <repo root> [<data root> [<runs> [<stills per run>]]] [--counts-only]

<repo root> is the code measured (the parent or this row). Without <data root> only part A runs.

A. Fixture: seven production serialization paths on generated pixels (download, colour balance, text-band crop,
   upscale serialization, privacy blur, illustration composite, detail crop): the saved file against the pixels
   the stage computed.
B. Real runs (<data root> holds assets/<topic>/ and output/run_*/: the dev checkout, or a COPY of the production
   sample). For each run, the stills its provenance.json lists (pool and beat photos) go through the measured
   commit's own preparation chain, as main.prepared_still and privacy.anonymize_all run it: download
   (commons.download_jpeg, transport mocked, the pool file's bytes served), upscale.ensure_resolution,
   colour_balance.balanced and, where the run's provenance shows a privacy copy, privacy.anonymize_media (face
   detector mocked to one fixed box: the serialization loss is measured, not the detector). Then-and-now uses the
   run's own downloaded then/now candidates (graphics/then_k with now_k_0): download, ensure_resolution, a
   registration when the pair matches by geometry (then_now.geometric_match, no vision call), else the
   downloaded pair as it is.
   The chain runs twice with the same code: as committed, and as a lossless reference in which every JPEG save
   (Pillow and OpenCV) writes PNG pixels instead. Every surface that uses the layer is rendered by the measured
   commit's own function from both layers and compared:
     reel_still      parallax.render_clip (the Reel's still shot: rgb24 -> RGB_TO_BT709 -> libx264), frame 0
                     decoded back with BT709_TO_RGB; also its colour tags (ffprobe)
     photo_ground    typography.cover_crop at 1080x1920: the 9:16 stat card's, the callout's and the hook frame's
                     photo ground before their scrims and type
     stat_ground_wide graphics.stat_backdrop_wide at 1920x1080 (the 16:9 single-number card's ground)
     then_now        then_now.frame_pair + then_now.compose at 1080x1920, the divider at the middle
   On this row the committed chain IS the lossless one, so every difference is 0. On the parent the difference
   is what the JPEG generations cost each surface. Surface hashes are printed so the parent's reference can be
   matched against this row's committed output.
C. Cost on real pool photos: bytes and save time of each stage's output and the disk each stage leaves (B), and,
   for a sample of pool photos, JPEG q92 (the parent's download) against PNG at Pillow's default level and at
   level 1, and (this row) what the download stores: the source's own bytes, metadata stripped, when it is an
   upright RGB/greyscale JPEG, else the PNG.
"""

import contextlib
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, JpegImagePlugin

# --counts-only: for the production sample (decisions/PROD-SAMPLE-counts-only.md), print counts and aggregates only:
# no run names, file names or image hashes.
COUNTS_ONLY = "--counts-only" in sys.argv
sys.argv = [a for a in sys.argv if a != "--counts-only"]
root = Path(sys.argv[1]).resolve()
data = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else None
MAX_RUNS = int(sys.argv[3]) if len(sys.argv) > 3 else 6
PER_RUN = int(sys.argv[4]) if len(sys.argv) > 4 else 3
POOL_SAMPLE = 24
sys.path.insert(0, str(root))
from src import (
    beat_images,
    colour_balance,
    commons,
    picture_coverage,
    privacy,
    substrate_compositor,
    upscale,
    url_guard,
)

cv2 = privacy.cv2


def pixels(path):
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))


def score(path, expected):
    with Image.open(path) as image:
        encoding = image.format
        sampling = JpegImagePlugin.get_sampling(image) if encoding == "JPEG" else None
        actual = np.asarray(image.convert("RGB"))
    difference = np.abs(actual.astype(np.int16) - expected.astype(np.int16))
    return {"format": encoding, "jpeg_sampling": sampling,
            "maximum_channel_error": int(difference.max()),
            "mean_channel_error": round(float(difference.mean()), 6),
            "changed_pixels": int(np.any(difference != 0, axis=2).sum()),
            "pixels": int(actual.shape[0] * actual.shape[1])}


def fixture():
    results = {}
    with tempfile.TemporaryDirectory(prefix="qf13-") as temporary:
        folder = Path(temporary)
        rgb = np.random.default_rng(13).integers(20, 220, (192, 192, 3), dtype=np.uint8)
        original = folder / "original.png"
        Image.fromarray(rgb).save(original)
        # Mock the transport boundary; all parsing and serialization is production code.
        response = type("Response", (), {"content": original.read_bytes()})()
        with patch.object(url_guard, "follow", return_value=response):
            downloaded = folder / "download.png"
            commons.download_jpeg("https://upload.wikimedia.org/generated.png", downloaded)
        results["download"] = score(downloaded, rgb)
        source = folder / "source.jpg"
        Image.fromarray(rgb).save(source, quality=95)
        decoded = pixels(source)
        plan = {"gains": [1.05, 1.0, 0.95], "gamma": 1, "cast": 0.1}
        with patch.object(colour_balance, "CACHE_DIR", folder / "balance"), \
                patch.object(colour_balance, "plan", return_value=plan):
            with Image.open(source) as image:
                expected = np.asarray(colour_balance.apply(image, plan))
            results["balance"] = score(colour_balance.balanced(source), expected)
        with patch.object(beat_images, "TEXT_CROP_DIR", folder / "crop"):
            cropped = beat_images.crop_text_band(source, "bottom", [900, 0, 1000, 1000])
            results["crop"] = score(cropped, decoded[:pixels(cropped).shape[0]])
        with patch.object(upscale, "CACHE_DIR", folder / "upscale"), \
                patch.object(upscale, "_upscale_cpu_polish", return_value=Image.fromarray(decoded)):
            output = upscale._cache_path(source, "cpu_lanczos_polish")
            results["upscale_serialization"] = score(upscale._try_backend("cpu_lanczos_polish", source, output),
                                                     decoded)

        def detect(image, **kwargs):
            kwargs["status"]["complete"] = True
            return [(50, 50, 30, 30)]

        with patch.object(privacy, "CACHE_DIR", folder / "privacy"), \
                patch.object(privacy, "FACE_PRIVACY_ENABLED", True), \
                patch.object(privacy, "is_artwork", return_value=False), \
                patch.object(privacy, "looks_like_art", return_value=False), \
                patch.object(privacy, "detect_human_faces", side_effect=detect):
            expected = privacy.blur_faces_on_image(cv2.imread(str(source)), [(50, 50, 30, 30)])
            expected = cv2.cvtColor(expected, cv2.COLOR_BGR2RGB)
            results["privacy"] = score(privacy.anonymize_media(source), expected)
        scene = folder / "engraving.png"
        expected = substrate_compositor.composite_engraving_on_ink(original, scene, (192, 192))
        results["illustration"] = score(scene, np.asarray(expected))
        with Image.open(source) as image:
            detail = picture_coverage._save_crop(source, image, (0.25, 0.25, 0.75, 0.75), folder / "detail", {})
        results["detail_crop"] = score(detail, decoded[48:144, 48:144])
    return {"stages": results,
            "jpeg_420_stages": sum(value["jpeg_sampling"] == 2 for value in results.values()),
            "lossless_stages": sum(value["changed_pixels"] == 0 for value in results.values())}


# ----------------------------------------------------------------------------------------------- B: real runs
JPEG_NAMES = (".jpg", ".jpeg")
_pil_save = Image.Image.save
_cv2_imwrite = cv2.imwrite
SAVES = []          # (kind, format, bytes, seconds) of every image file the chain writes


def _is_jpeg(fp, format_):
    if format_:
        return str(format_).upper() in ("JPEG", "JPG")
    return isinstance(fp, (str, Path)) and Path(fp).suffix.lower() in JPEG_NAMES


def _recording_save(lossless):
    def save(self, fp, format=None, **params):
        jpeg = _is_jpeg(fp, format)
        if lossless and jpeg:
            format, params = "PNG", {k: v for k, v in params.items() if k in ("exif", "icc_profile")}
        t0 = time.perf_counter()
        out = _pil_save(self, fp, format, **params)
        if isinstance(fp, (str, Path)):
            SAVES.append(((format or Path(fp).suffix).upper().strip("."), Path(fp).stat().st_size,
                          time.perf_counter() - t0))
        return out
    return save


def _recording_imwrite(lossless):
    def imwrite(path, img, params=None):
        t0 = time.perf_counter()
        if lossless and Path(path).suffix.lower() in JPEG_NAMES:
            ok, buf = cv2.imencode(".png", img)
            Path(path).write_bytes(buf.tobytes())
            fmt = "PNG"
        else:
            ok = _cv2_imwrite(path, img, params) if params is not None else _cv2_imwrite(path, img)
            fmt = Path(path).suffix.upper().strip(".")
        SAVES.append((fmt, Path(path).stat().st_size, time.perf_counter() - t0))
        return ok
    return imwrite


def _download_name():
    """The pool file name the measured commit's fetcher writes (NN_commons.jpg before this row, .png after)."""
    text = (root / "scripts" / "fetch_commons_images.py").read_text(encoding="utf-8")
    return ".png" if '_commons.png"' in text else ".jpg"


@contextlib.contextmanager
def _sandbox(work, lossless, served):
    """The measured code's chain, its caches in `work`, the transport and the detectors mocked."""
    response = type("Response", (), {"content": b""})()
    stack = contextlib.ExitStack()

    def follow(url, *a, **k):
        response.content = served["bytes"]
        return response

    def detect(image, **kwargs):
        h, w = image.shape[:2]
        kwargs["status"]["complete"] = True
        return [(int(w * 0.45), int(h * 0.30), max(8, int(w * 0.06)), max(8, int(w * 0.06)))]

    from src import parallax
    for target, name, value in (
            (url_guard, "follow", follow),
            (upscale, "CACHE_DIR", work / "upscaled"), (upscale, "is_gpu_accelerated", lambda: False),
            (upscale, "backend", lambda: "measure"),
            # The model's merit is outside this metric: every backend computes with the CPU polish and is saved by
            # the measured _try_backend, so the serialization is the production one.
            (upscale, "_backend_files", lambda name: [root / "src" / "upscale.py"]),
            (upscale, "_upscale_onnx", lambda path: upscale._upscale_cpu_polish(path)),
            (upscale, "_upscale_ncnn", lambda path, tmp: upscale._upscale_cpu_polish(path)),
            (colour_balance, "CACHE_DIR", work / "colour_balance"),
            (privacy, "CACHE_DIR", work / "privacy"), (privacy, "FACE_PRIVACY_ENABLED", True),
            (privacy, "is_artwork", lambda p: False), (privacy, "looks_like_art", lambda p: False),
            (privacy, "detect_human_faces", detect),
            (parallax, "depth_map", lambda rgb: None),     # computed and unused by render_clip's Ken Burns
            (Image.Image, "save", _recording_save(lossless)), (cv2, "imwrite", _recording_imwrite(lossless))):
        stack.enter_context(patch.object(target, name, value))
    with stack:
        yield


def _stored(dest):
    """The file the measured commit's download wrote (this row keeps an upright JPEG source as .jpg)."""
    return commons.stored(dest) if hasattr(commons, "stored") else dest


def _disk(work):
    """Bytes of the image files the chain left in `work`, by stage folder (the downloads at the top: "download")."""
    out = {}
    for f in Path(work).rglob("*"):
        if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png"):
            rel = f.relative_to(work).parts
            stage = "download" if len(rel) == 1 or rel[0] == "pool" else rel[0]
            out[stage] = out.get(stage, 0) + f.stat().st_size
    return out


def _prepare(src, work, lossless, faces):
    """The still's layer as the measured commit prepares it; (path, [(kind, bytes, seconds)])."""
    served = {"bytes": src.read_bytes()}
    start = len(SAVES)
    with _sandbox(work, lossless, served):
        dest = work / "pool" / (src.stem + _download_name())
        dest.parent.mkdir(parents=True, exist_ok=True)
        commons.download_jpeg("https://upload.wikimedia.org/measure" + src.suffix, dest)
        dest = _stored(dest)
        layer = colour_balance.balanced(upscale.ensure_resolution(dest))
        if faces:
            layer = privacy.anonymize_media(layer)
    return Path(layer), SAVES[start:], _disk(work)


def _prepare_pair(then_src, now_src, work, lossless):
    from src import then_now
    start = len(SAVES)
    layers = []
    for src in (then_src, now_src):
        served = {"bytes": src.read_bytes()}
        with _sandbox(work, lossless, served):
            dest = work / (src.stem + _download_name())
            commons.download_jpeg("https://upload.wikimedia.org/measure" + src.suffix, dest)
            layers.append(_stored(dest))
    with _sandbox(work, lossless, {"bytes": b""}):
        old = upscale.ensure_resolution(layers[0])
        geo = then_now.geometric_match(old, layers[1])
        pair = then_now.register(old, layers[1], geo["matrix"], work / "registered", geo) if geo else None
    registered = bool(pair)
    return (tuple(pair) if pair else (old, layers[1])), registered, SAVES[start:], _disk(work)


def _surfaces(layer, work):
    from src import graphics, parallax
    from src.encoding import BT709_TO_RGB
    from src.typography import cover_crop
    out = {}
    clip = work / "still.mp4"
    with patch.object(parallax, "depth_map", lambda rgb: None):
        parallax.render_clip(layer, clip, 2 / 30, preset="push", fps=30, size=(1080, 1920))
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(clip), "-frames:v", "1", "-vf",
                          f"{BT709_TO_RGB},format=rgb24", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    out["reel_still"] = np.frombuffer(raw, np.uint8).reshape(1920, 1080, 3)
    tags = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                           "stream=pix_fmt,color_space,color_transfer,color_primaries,color_range", "-of", "json",
                           str(clip)], capture_output=True, text=True, check=True).stdout
    out["_reel_tags"] = json.loads(tags)["streams"][0]
    out["photo_ground"] = np.asarray(cover_crop(layer, (1080, 1920)).convert("RGB"))
    out["stat_ground_wide"] = np.asarray(graphics.stat_backdrop_wide(layer, 1920, 1080).convert("RGB"))
    return out


def _then_now_surface(old, new):
    from src import then_now
    a, b, _ = then_now.frame_pair(old, new, 1080, 1920)
    return {"then_now": np.asarray(then_now.compose(a, b, 0.5).convert("RGB"))}


def _bt709_chroma(rgb):
    x = rgb.astype(np.float64) / 255
    y = 0.2126 * x[..., 0] + 0.7152 * x[..., 1] + 0.0722 * x[..., 2]
    return (x[..., 2] - y) / 1.8556, (x[..., 0] - y) / 1.5748


def compare(got, ref):
    d = np.abs(got.astype(np.int16) - ref.astype(np.int16))
    mse = float((d.astype(np.float64) ** 2).mean())
    cb_g, cr_g = _bt709_chroma(got)
    cb_r, cr_r = _bt709_chroma(ref)
    return {"mean_channel_error": round(float(d.mean()), 4), "p99_channel_error": int(np.percentile(d, 99)),
            "maximum_channel_error": int(d.max()), "psnr_db": round(10 * np.log10(255 ** 2 / mse), 2) if mse else None,
            "changed_pixel_share": round(float(np.any(d > 0, axis=2).mean()), 4),
            "pixels_off_by_3_or_more_share": round(float(np.any(d >= 3, axis=2).mean()), 4),
            "mean_chroma_error_8bit": round(float((np.abs(cb_g - cb_r) + np.abs(cr_g - cr_r)).mean() / 2 * 255), 4)}


def _digest(arrays):
    h = hashlib.sha256()
    for a in arrays:
        h.update(hashlib.sha256(np.ascontiguousarray(a).tobytes()).digest())
    return h.hexdigest()[:16]


def _relative(recorded, data_root):
    """A path recorded on any host, re-rooted at data_root (assets/... or output/run_.../...)."""
    parts = re.split(r"[\\/]", recorded)
    for anchor in ("assets", "output"):
        if anchor in parts:
            return data_root.joinpath(*parts[parts.index(anchor):])
    return None


def _run_stills(run, data_root):
    try:
        text = (run / "provenance.json").read_text(encoding="utf-8")
    except OSError:
        return []
    recorded = sorted(set(re.findall(r'"([A-Za-z]:[^"]+?\.(?:jpg|jpeg|png))"', text)))
    privacy_stems = {re.split(r"[\\/]", p)[-1] for p in recorded if re.search(r"[\\/]cache[\\/]privacy[\\/]", p)}
    stills = []
    for p in recorded:
        p = p.replace("\\\\", "\\")
        if re.search(r"[\\/]cache[\\/]", p):
            continue
        local = _relative(p, data_root)
        if local is None or not local.is_file() or not (("assets" in local.parts) or ("beat_images" in local.parts)):
            continue
        faces = any(name.startswith(local.stem + "_") for name in privacy_stems)
        stills.append((local, faces))
    return stills


def _add(total, part):
    for k, v in part.items():
        total[k] = total.get(k, 0) + v


def summarise(rows):
    keys = rows[0].keys() if rows else []
    out = {}
    for k in keys:
        vals = [r[k] for r in rows if r[k] is not None]
        if vals:
            out[k] = {"median": round(float(np.median(vals)), 4), "max": max(vals), "min": min(vals)}
    return out


def real_runs(data_root):
    runs = sorted(p for p in (data_root / "output").glob("run_*") if (p / "provenance.json").is_file())
    chosen = [r for r in runs if _run_stills(r, data_root)][-MAX_RUNS:]
    per_surface = {k: [] for k in ("reel_still", "photo_ground", "stat_ground_wide", "then_now")}
    digests = {k: {"committed": [], "lossless_reference": []} for k in per_surface}
    stage_cost = {"committed": [], "lossless_reference": []}
    stage_disk = {"committed": {}, "lossless_reference": {}}
    tags, layers, runs_out = set(), [], []
    with tempfile.TemporaryDirectory(prefix="qf13-real-") as temporary:
        tmp = Path(temporary)
        for run in chosen:
            stills = _run_stills(run, data_root)[:PER_RUN]
            runs_out.append({"run": run.name, "stills": len(stills), "with_privacy_copy": sum(f for _, f in stills)})
            for k, (src, faces) in enumerate(stills):
                surf = {}
                for mode, lossless in (("committed", False), ("lossless_reference", True)):
                    work = tmp / run.name / str(k) / mode
                    layer, saves, disk = _prepare(src, work, lossless, faces)
                    stage_cost[mode] += saves
                    _add(stage_disk[mode], disk)
                    s = _surfaces(layer, work)
                    tags.add(json.dumps(s.pop("_reel_tags"), sort_keys=True))
                    surf[mode] = s
                    if mode == "committed":
                        with Image.open(layer) as im:
                            layers.append({"format": im.format, "sampling": JpegImagePlugin.get_sampling(im)
                                           if im.format == "JPEG" else None})
                for name in surf["committed"]:
                    per_surface[name].append(compare(surf["committed"][name], surf["lossless_reference"][name]))
                    for mode in digests[name]:
                        digests[name][mode].append(surf[mode][name])
                shutil.rmtree(tmp / run.name / str(k), ignore_errors=True)
        pairs = []
        for run in sorted((data_root / "output").glob("run_*")):
            for then in sorted((run / "graphics").glob("then_*.jpg")):
                now = run / "graphics" / f"now_{then.stem.split('_')[1]}_0.jpg"
                if now.is_file():
                    pairs.append((run, then, now))
                    break
        registered = 0
        for i, (run, then, now) in enumerate(pairs[:MAX_RUNS]):
            surf = {}
            for mode, lossless in (("committed", False), ("lossless_reference", True)):
                work = tmp / "pairs" / str(i) / mode
                work.mkdir(parents=True, exist_ok=True)
                (old, new), was_registered, saves, disk = _prepare_pair(then, now, work, lossless)
                stage_cost[mode] += saves
                _add(stage_disk[mode], {"then_now_" + k: v for k, v in disk.items()})
                surf[mode] = _then_now_surface(old, new)
            registered += was_registered
            per_surface["then_now"].append(compare(surf["committed"]["then_now"],
                                                   surf["lossless_reference"]["then_now"]))
            for mode in digests["then_now"]:
                digests["then_now"][mode].append(surf[mode]["then_now"])
            shutil.rmtree(tmp / "pairs" / str(i), ignore_errors=True)
    cost = {}
    for mode, saves in stage_cost.items():
        cost[mode] = {"files_written": len(saves),
                      "formats": {f: sum(1 for s in saves if s[0] == f) for f in sorted({s[0] for s in saves})},
                      "megabytes": round(sum(s[1] for s in saves) / 1e6, 2),
                      "save_seconds": round(sum(s[2] for s in saves), 2),
                      "disk_megabytes_by_stage": {k: round(v / 1e6, 2) for k, v in sorted(stage_disk[mode].items())},
                      "disk_megabytes": round(sum(stage_disk[mode].values()) / 1e6, 2)}
    return {"runs": runs_out, "stills": len(layers),
            "then_now_pairs": len(pairs[:MAX_RUNS]), "then_now_pairs_registered": registered,
            "layer_formats": dict(Counter(json.dumps(x, sort_keys=True) for x in layers)),
            "reel_clip_colour_tags": [json.loads(t) for t in sorted(tags)],
            "surfaces": {k: {"n": len(v), **summarise(v)} for k, v in per_surface.items()},
            "surfaces_changed": {k: sum(r["changed_pixel_share"] > 0 for r in v) for k, v in per_surface.items()},
            "surface_digests": {k: {m: _digest(a) for m, a in v.items()} for k, v in digests.items()},
            "prepared_files": cost}


def pool_cost(data_root):
    photos = sorted((data_root / "assets").glob("*/*_commons.jpg"))
    step = max(1, len(photos) // POOL_SAMPLE)
    sample = photos[::step][:POOL_SAMPLE]
    rows = []
    for p in sample:
        source = p.stat().st_size
        with Image.open(p) as im:
            rgb = im.convert("RGB")
            rgb.load()
        row = {"source_mb": source / 1e6, "megapixels": rgb.width * rgb.height / 1e6}
        if hasattr(commons, "kept_jpeg"):      # this row: what the download stores for this source
            data = p.read_bytes()
            with Image.open(io.BytesIO(data)) as raw:
                kept = commons.kept_jpeg(data, raw)
            row["stored_kept_source"] = int(kept is not None)
            row["stored_mb"] = len(kept) / 1e6 if kept is not None else None
        for name, kw in (("jpeg_q92", {"format": "JPEG", "quality": 92}), ("png_default", {"format": "PNG"}),
                         ("png_level1", {"format": "PNG", "compress_level": 1})):
            buf = io.BytesIO()
            t0 = time.perf_counter()
            _pil_save(rgb, buf, **kw)
            row[name + "_save_s"] = time.perf_counter() - t0
            row[name + "_mb"] = buf.tell() / 1e6
            t0 = time.perf_counter()
            with Image.open(io.BytesIO(buf.getvalue())) as back:
                back.load()
            row[name + "_read_s"] = time.perf_counter() - t0
        rows.append(row)
    if rows and "stored_mb" in rows[0]:
        for r in rows:       # a source that must be decoded is stored as the PNG measured beside it
            r["stored_mb"] = r["stored_mb"] if r["stored_mb"] is not None else r["png_default_mb"]
    total = {k: round(sum(r[k] for r in rows), 2) for k in rows[0]} if rows else {}
    return {"pool_photos": len(photos), "pool_megabytes": round(sum(p.stat().st_size for p in photos) / 1e6, 1),
            "sample": len(sample), "sample_totals": total,
            "per_photo": {k: {"median": round(float(np.median([r[k] for r in rows])), 3),
                              "max": round(max(r[k] for r in rows), 3)} for k in (rows[0] if rows else [])}}


def main():
    files = ("src/commons.py", "src/colour_balance.py", "src/beat_images.py", "src/upscale.py", "src/privacy.py",
             "src/substrate_compositor.py", "src/picture_coverage.py", "src/then_now.py", "src/parallax.py",
             "src/visual_engine.py", "scripts/fetch_commons_images.py")
    result = {"scope": "8-bit RGB serialization of prepared photo layers; no recovery of source JPEG detail and no "
                       "model-quality claim",
              "source_commit": subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip(),
              "source_hashes": {name: hashlib.sha256((root / name).read_bytes()).hexdigest()[:16] for name in files},
              "fixture": fixture()}
    result["fixture"]["acceptance_met"] = (result["fixture"]["jpeg_420_stages"] == 0
                                           and result["fixture"]["lossless_stages"] == len(result["fixture"]["stages"]))
    if data is not None:
        result["real_runs"] = real_runs(data)
        result["pool_cost"] = pool_cost(data)
        result["real_runs"]["acceptance_met"] = all(v == 0 for v in result["real_runs"]["surfaces_changed"].values())
        if COUNTS_ONLY:
            real = result["real_runs"]
            runs = real.pop("runs")
            real["runs"] = {"runs": len(runs), "stills": sum(r["stills"] for r in runs),
                            "with_privacy_copy": sum(r["with_privacy_copy"] for r in runs)}
            real.pop("surface_digests")
            real["counts_only"] = True
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
