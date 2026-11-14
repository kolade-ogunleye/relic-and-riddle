"""QF-117: the patch bumps leave every rendered pixel unchanged.

    python docs/reviews/quality-fixes-2026-11/QF-117/measure.py <repo root>

Renders a fixed set of real frames with the interpreter that runs this script (its installed fontTools, pydantic,
soundfile...) from the committed inputs/ (three real development runs: make_inputs.py), and prints one JSON object:
the installed versions, a SHA-256 of the decoded pixels of each item, and the comparison with before.json (the same
script run in a venv synced from the parent's uv.lock). Equal digests of the raw decoded RGB(A) buffers mean equal
pixels: `differing_items` 0 is 0 differing pixels.

Items (the four the row asks for, on both runs where the run has them):
- graphics: the stat card, the locator map (country, site and water labels), the timeline and the scale graphic,
  each its still (.jpg) and every frame of its clip (decoded to rgb24), drawn as graphics.make_all draws them (the
  run's palette and photo grade bias), at 1080x1920;
- covers: the delivered cover and its profile-grid tile in every renderable style (visual_engine);
- captions: the ASS file VideoComposer.build_subtitles writes from the run's words (its plates are measured
  through font_engine), and three distinct frames of it burnt by libass (ffmpeg's subtitles filter, the bundled fonts) over
  the run's hero photo, at 9:16 and at 16:9;
- fonts: the in-memory face font_engine._glyph_font_data compiles with fontTools for every bundled font (the one
  place the project writes font tables; Pillow rasterises tracked glyphs from it), and inspect_font's record;
- audio: soundfile's WAV (PCM_16, PCM_24, FLOAT) and FLAC bytes of a fixed signal (a float WAV's PEAK time stamp
  zeroed: audio_bytes), and the samples read back.

The video encoder is pinned to libx264 at the project's preset and CRF (encoding.get_video_encoder's own
fallback), so a hardware encoder cannot vary the clips.
"""
from __future__ import annotations

import hashlib
import importlib.metadata as md
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
INPUTS = HERE / "inputs"
PACKAGES = ("pydantic", "pydantic-core", "fonttools", "soundfile", "pytest", "ruff", "pillow", "uharfbuzz",
            "skia-python", "numpy", "opencv-python-headless", "anthropic")
CAPTION_FRAMES = 3


def pixels_digest(im) -> str:
    h = hashlib.sha256(f"{im.mode} {im.size}".encode())
    h.update(im.tobytes())
    return h.hexdigest()


def image_digest(path: Path) -> str:
    from PIL import Image
    with Image.open(path) as im:
        im.load()
        return pixels_digest(im)


def clip_digest(path: Path) -> tuple[str, int]:
    """SHA-256 over every decoded frame (rgb24) and the frame count."""
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                            "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
    w, h = (int(v) for v in probe.stdout.strip().split(",")[:2])
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                            stdout=subprocess.PIPE)
    digest, frames, size = hashlib.sha256(), 0, w * h * 3
    while True:
        buf = proc.stdout.read(size)
        if not buf:
            break
        if len(buf) != size:
            raise RuntimeError(f"{path.name}: a short frame")
        digest.update(buf)
        frames += 1
    if proc.wait():
        raise RuntimeError(f"ffmpeg could not decode {path.name}")
    return digest.hexdigest(), frames


def ffmpeg_path(p: Path) -> str:
    return str(p.resolve()).replace("\\", "/").replace(":", "\\:")


def caption_times(ass: Path) -> list[float]:
    """The distinct midpoints of the first CAPTION_FRAMES dialogue events."""
    out = []
    for line in ass.read_text(encoding="utf-8").splitlines():
        m = re.match(r"Dialogue: [^,]*,(\d+):(\d+):(\d+\.\d+),(\d+):(\d+):(\d+\.\d+),", line)
        if m:
            a = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
            b = int(m[4]) * 3600 + int(m[5]) * 60 + float(m[6])
            out.append(round((a + b) / 2, 3))
    return sorted(set(out))[:CAPTION_FRAMES]   # a burst's plate and text share their times


def burn_caption(photo: Path, ass: Path, fonts: Path, t: float, size: tuple[int, int], work: Path):
    from PIL import Image
    w, h = size
    raw = work / f"{ass.stem}_{t:.3f}.rgb"
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},format=rgb24,"
          f"subtitles='{ffmpeg_path(ass)}':fontsdir='{ffmpeg_path(fonts)}'")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", "30", "-i", str(photo), "-vf", vf,
                    "-ss", f"{t:.3f}", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", str(raw)],
                   check=True)
    return Image.frombytes("RGB", size, raw.read_bytes())


def draw(graphics, name: str, run: dict, clip: Path, photo: Path, narration: str):
    """One graphic as graphics.make_all calls its renderer."""
    if name == "numbers":
        return graphics.render_stat_cards(run["stat_cards"], clip, photo, narration=narration)
    if name == "map":
        return graphics.render_map(run["place"], clip, narration=narration)
    if name == "timeline":
        return graphics.render_timeline(run["timeline"], clip, photo=photo)
    return graphics.render_scale(run["scale_fact"], clip)


def audio_bytes(path: Path) -> bytes:
    """The file's bytes, with the time stamp libsndfile writes into a float WAV's PEAK chunk (the second it was
    written, so two writes a second apart differ with the same library) set to zero."""
    data = bytearray(path.read_bytes())
    at = data.find(b"PEAK")
    if at >= 0 and path.suffix == ".wav":
        data[at + 12:at + 16] = bytes(4)    # PEAK: id, size, version, time stamp, then the peaks
    return bytes(data)


def use_geo_data(root: Path, geo) -> None:
    """The map data (tools/geo, fetched, gitignored) of `root`, else, read only, of the repository's main checkout
    (the parent of git's common directory), so the check also runs in a fresh worktree without its own copy."""
    from src.config import BASE_DIR
    mine = BASE_DIR / "tools" / "geo"
    if geo.available():
        return
    common = subprocess.run(["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True, check=True).stdout.strip()
    other = Path(common).parent / "tools" / "geo"
    if not (other / geo.COUNTRIES_FILE.relative_to(mine)).is_file():
        return
    for name, value in vars(geo).items():
        if isinstance(value, Path) and value.is_relative_to(mine):
            setattr(geo, name, other / value.relative_to(mine))
    geo.RELIEF_FILE = geo._relief_file()
    print(f"map data: the main checkout's tools/geo (none in {root.name})", file=sys.stderr)


def render(root: Path, work: Path) -> dict[str, dict]:
    import numpy as np
    import soundfile as sf

    from src import encoding, font_engine, graphics
    from src.art_direction import use_palette
    from src.config import CAPTION_FIRST_FRAME_WHOLE_S
    from src.font_engine import surface
    from src.photo_grade import using
    from src.typography import FONTS_DIR, bundled_font_files
    from src.video_composer import VideoComposer
    from src.visual_engine import RENDERABLE_STYLES, VisualEngine

    encoding._DETECTED_ENCODER = ["-c:v", "libx264", "-preset", encoding.VIDEO_PRESET, "-crf", str(encoding.VIDEO_CRF)]
    runs = json.loads((INPUTS / "inputs.json").read_text(encoding="utf-8"))["runs"]
    items: dict[str, dict] = {}
    use_geo_data(root, graphics.geo)
    if not graphics.geo.available():
        raise SystemExit("map data missing: tools/geo (scripts/fetch_geodata.py --10m, fetch_geodata.py, "
                         "fetch_marine.py, fetch_relief.py)")
    for key, run in runs.items():
        photo = INPUTS / run["hero"]
        out = work / key
        out.mkdir()
        use_palette(run["palette"])
        narration = run.get("narration_script") or ""
        wanted = {"numbers": run.get("stat_cards"), "map": run.get("place"), "timeline": run.get("timeline"),
                  "scale": run.get("scale_fact")}
        for name in ("numbers", "map", "timeline", "scale"):
            if not wanted[name]:
                continue
            with using(run["palette"]["grade_bias"]), surface(f"graphic {name}"):
                clip = draw(graphics, name, run, out / f"graphic_{name}.mp4", photo, narration)
            if clip is None:   # the renderer declines (e.g. no landmark near the subject's size): same both trees
                items[f"{key}/graphic_{name}/clip"] = {"drawn": False}
                continue
            digest, frames = clip_digest(clip)
            items[f"{key}/graphic_{name}/clip"] = {"sha256": digest, "frames": frames}
            items[f"{key}/graphic_{name}/still"] = {"sha256": image_digest(Path(clip).with_suffix(".jpg"))}
        for style in RENDERABLE_STYLES:
            cover, grid = VisualEngine().generate_thumbnails(photo, {"thumbnail": run["thumbnail"]}, out / style,
                                                             style=style)
            items[f"{key}/cover_{style}"] = {"sha256": image_digest(cover)}
            items[f"{key}/cover_{style}_grid"] = {"sha256": image_digest(grid)}
        for w, h in ((1080, 1920), (1920, 1080)):
            ass = out / f"captions_{w}x{h}.ass"
            VideoComposer().build_subtitles(run["words"], ass, emphasis=run.get("emphasis_words"), width=w, height=h,
                                            first_frame_whole=CAPTION_FIRST_FRAME_WHOLE_S if h > w else None)
            text = ass.read_bytes()
            items[f"{key}/captions_{w}x{h}/ass"] = {"sha256": hashlib.sha256(text).hexdigest()}
            for t in caption_times(ass):
                items[f"{key}/captions_{w}x{h}/frame_{t:.3f}"] = {
                    "sha256": pixels_digest(burn_caption(photo, ass, FONTS_DIR, t, (w, h), work))}
    for name in bundled_font_files():
        path = str(FONTS_DIR / name)
        items[f"fonts/{name}/glyph_font_data"] = {"sha256": hashlib.sha256(font_engine._glyph_font_data(path)).hexdigest()}
        info = font_engine.inspect_font(path)
        record = json.dumps({k: sorted(v) if isinstance(v, frozenset) else v for k, v in vars(info).items()},
                            sort_keys=True, default=str)
        items[f"fonts/{name}/inspect_font"] = {"sha256": hashlib.sha256(record.encode()).hexdigest()}
    rng = np.random.default_rng(117)
    signal = (0.5 * np.sin(np.linspace(0, 2 * np.pi * 440, 48000)) + 0.05 * rng.standard_normal(48000)).astype(np.float32)
    signal = np.stack([signal, signal[::-1]], axis=1)
    for fmt, subtype in (("WAV", "PCM_16"), ("WAV", "PCM_24"), ("WAV", "FLOAT"), ("FLAC", "PCM_16")):
        f = work / f"audio_{subtype}.{fmt.lower()}"
        sf.write(f, signal, 48000, format=fmt, subtype=subtype)
        data, rate = sf.read(f, dtype="float32")
        items[f"audio/{fmt}_{subtype}/file"] = {"sha256": hashlib.sha256(audio_bytes(f)).hexdigest()}
        items[f"audio/{fmt}_{subtype}/read"] = {"sha256": hashlib.sha256(data.tobytes() + str(rate).encode()).hexdigest()}
    return items


def main(root: Path) -> None:
    sys.path.insert(0, str(root))
    versions = {}
    for name in PACKAGES:
        try:
            versions[name] = md.version(name)
        except md.PackageNotFoundError:
            versions[name] = None
    import soundfile
    versions["libsndfile"] = soundfile.__libsndfile_version__
    with tempfile.TemporaryDirectory(prefix="qf117-") as d:
        items = render(root, Path(d))
    result = {"versions": versions, "items": len(items),
              "clip_frames": sum(v.get("frames", 0) for v in items.values()), "digests": items}
    before = HERE / "before.json"
    if before.is_file():
        ref = json.loads(before.read_text(encoding="utf-8"))["digests"]
        differing = sorted(k for k in set(ref) | set(items) if ref.get(k) != items.get(k))
        result["compared_with_before"] = len(set(ref) & set(items))
        result["differing_items"] = len(differing)
        result["differing"] = differing
    print(json.dumps(result, indent=1, sort_keys=True))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(Path(sys.argv[1]).resolve())
