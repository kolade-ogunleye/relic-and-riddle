"""Hostile photo grounds, synthesised (no download): every kind is a pure function of (kind, seed, size).

Kinds (GROUND_KINDS):
- flat: pure white, pure black, mid-grey;
- noise: uniform RGB noise; texture: a high-frequency checker / stripe weave; edge: a hard black/white edge through
  the middle third (where text sits), both orientations;
- text_band: burned-in text bands (a caption strip, a sign) drawn with a bundled face;
- tiny: a tiny-resolution input (24..96 px on the long side) upscaled by the renderer;
- aspect: an extreme aspect ratio (8:1 panorama, 1:8 strip);
- alpha: a transparent PNG (RGBA, half the canvas transparent);
- hero_crop: a real hero from the dev checkout's assets (read-only), cropped pathologically (a 6 % sliver, a
  corner, a letterboxed band); falls back to `noise` when no hero is found.

`make(spec, out_dir)` writes the ground to out_dir/<name>.<ext> once (atomic rename) and returns the path. A spec
is a dict {"kind", "seed", "variant"}; ground_specs(count, seed) draws `count` of them, every kind in turn first.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

GROUND_KINDS = ("white", "black", "grey", "noise", "texture", "edge_v", "edge_h", "text_band", "tiny", "aspect_wide",
                "aspect_tall", "alpha", "hero_crop")


def _main_checkout(here: Path) -> Path:
    """The main checkout of this repository (git's common directory's parent): the dev checkout that holds the
    gitignored data, found from any worktree without naming a machine path."""
    import subprocess
    try:
        common = subprocess.run(["git", "-C", str(here), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                capture_output=True, text=True, check=True, timeout=30).stdout.strip()
        return Path(common).parent
    except (OSError, subprocess.SubprocessError):
        return here


DEV_ASSETS = Path(os.environ.get("RR_STRESS_ASSETS") or _main_checkout(Path(__file__).resolve().parent) / "assets")
BASE = (1400, 1800)     # the cards' test photo size; covers and slides fit it to their own canvas


def ground_specs(count: int, seed: int) -> list[dict]:
    rnd = random.Random(seed * 7919 + 3)
    out = []
    for i in range(count):
        kind = GROUND_KINDS[i % len(GROUND_KINDS)] if i < len(GROUND_KINDS) else rnd.choice(GROUND_KINDS)
        out.append({"kind": kind, "seed": rnd.randrange(1 << 30), "variant": rnd.randrange(4)})
    return out


def name_of(spec: dict) -> str:
    return f"g_{spec['kind']}_{spec['variant']}_{spec['seed'] % 100000:05d}"


def _heroes() -> list[Path]:
    if not DEV_ASSETS.is_dir():
        return []
    return sorted(p for p in DEV_ASSETS.glob("*/0[1-3]_commons.jpg"))


def _font(px: int):
    here = Path(__file__).resolve().parents[2] / "fonts"
    for cand in sorted(here.glob("**/*.ttf")):
        try:
            return ImageFont.truetype(str(cand), px)
        except OSError:
            continue
    return ImageFont.load_default(size=px)


def render(spec: dict) -> Image.Image:
    kind, v = spec["kind"], int(spec.get("variant", 0))
    rng = np.random.default_rng(int(spec["seed"]))
    w, h = BASE
    if kind == "white":
        return Image.new("RGB", (w, h), (255, 255, 255))
    if kind == "black":
        return Image.new("RGB", (w, h), (0, 0, 0))
    if kind == "grey":
        return Image.new("RGB", (w, h), (118, 118, 118) if v % 2 else (128, 128, 128))
    if kind == "noise":
        return Image.fromarray(rng.integers(0, 256, (h, w, 3), dtype=np.uint8))
    if kind == "texture":
        y, x = np.mgrid[0:h, 0:w]
        p = 2 + v                       # 2..5 px period: the halo and the ring read a weave, never a flat ground
        a = (((x // p) + (y // p)) % 2 * 255).astype(np.uint8) if v % 2 == 0 else ((x // p) % 2 * 255).astype(np.uint8)
        return Image.fromarray(np.stack([a, a, a], axis=2))
    if kind in ("edge_v", "edge_h"):
        arr = np.zeros((h, w, 3), np.uint8)
        f = 0.35 + 0.1 * v               # through the middle third, where every surface sets its text
        if kind == "edge_v":
            arr[:, int(w * f):] = 255
        else:
            arr[int(h * f):] = 255
        return Image.fromarray(arr)
    if kind == "text_band":
        im = Image.fromarray((rng.integers(40, 90, (h, w, 3))).astype(np.uint8))
        d = ImageDraw.Draw(im)
        f = _font(64 + 16 * v)
        for k in range(3 + v):
            y0 = int(h * (0.25 + 0.15 * k)) % (h - 120)
            d.rectangle((0, y0, w, y0 + 110), fill=(250, 250, 250))
            d.text((30, y0 + 15), "BURNED IN CAPTION TEXT 1999 ARCHIVE", font=f, fill=(10, 10, 10))
        return im
    if kind == "tiny":
        s = (24, 40, 64, 96)[v]
        small = Image.fromarray(rng.integers(0, 256, (max(8, int(s * 1.3)), s, 3), dtype=np.uint8))
        return small
    if kind == "aspect_wide":
        return Image.fromarray(rng.integers(60, 200, (240, 1920, 3), dtype=np.uint8))
    if kind == "aspect_tall":
        return Image.fromarray(rng.integers(60, 200, (1920, 240, 3), dtype=np.uint8))
    if kind == "alpha":
        arr = np.zeros((h, w, 4), np.uint8)
        arr[..., :3] = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
        arr[: h // 2, :, 3] = 255        # top half opaque, bottom half fully transparent
        return Image.fromarray(arr, "RGBA")
    if kind == "hero_crop":
        heroes = _heroes()
        if not heroes:
            return render({**spec, "kind": "noise"})
        p = heroes[int(spec["seed"]) % len(heroes)]
        with Image.open(p) as src:
            im = src.convert("RGB")
        W, H = im.size
        if v == 0:      # a 6 % vertical sliver, stretched by the renderer's fit
            return im.crop((int(W * 0.47), 0, int(W * 0.53), H))
        if v == 1:      # the top-left corner, 12 %
            return im.crop((0, 0, max(16, int(W * 0.12)), max(16, int(H * 0.12))))
        if v == 2:      # letterboxed: the photo in a black band, 25 % of the height
            out = Image.new("RGB", (W, H), (0, 0, 0))
            band = im.resize((W, max(8, H // 4)))
            out.paste(band, (0, (H - band.height) // 2))
            return out
        return im.crop((0, int(H * 0.9), W, H))   # the bottom 10 % strip
    raise ValueError(f"unknown ground kind {kind!r}")


def make(spec: dict, out_dir: Path) -> Path:
    """The ground written once (shared across workers; atomic rename) at out_dir/<name>.png (alpha) or .jpg."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if spec["kind"] == "real":       # a real run's own photo (realistic / mutation modes): a copy, never the source
        src = Path(spec["path"])     # (a renderer may write a sidecar beside the photo it reads)
        dst = out_dir / f"real_{hashlib.sha256(str(src).encode()).hexdigest()[:12]}{src.suffix.lower()}"
        if not dst.exists():
            tmp = dst.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_bytes(src.read_bytes())
            os.replace(tmp, dst)
        return dst
    ext = ".png" if spec["kind"] == "alpha" else ".jpg"
    path = out_dir / (name_of(spec) + ext)
    if not path.exists():
        im = render(spec)
        tmp = path.with_suffix(f".{os.getpid()}.tmp{ext}")
        if ext == ".png":
            im.save(tmp)
        else:
            im.save(tmp, quality=92)
        os.replace(tmp, path)
    return path


def credited(paths: list[Path], folder: Path, artist: str = "Stress Test", licence: str = "CC BY 4.0") -> list[Path]:
    """Copies of `paths` in `folder` with a credits.json beside them (the carousel and Story frames read a photo's
    credit there; a ground with no credit would be refused for the credit, never stressed for its ground)."""
    folder.mkdir(parents=True, exist_ok=True)
    out, images = [], []
    for i, p in enumerate(paths):
        dst = folder / f"{i + 1:02d}_commons{p.suffix}"
        if not dst.exists():
            dst.write_bytes(Path(p).read_bytes())
        out.append(dst)
        digest = hashlib.sha256(dst.read_bytes()).hexdigest()[:8]
        images.append({"file": dst.name, "title": f"File:Stress ground {digest}{p.suffix}",
                       "source_url": f"https://commons.wikimedia.org/wiki/File:Stress_ground_{digest}{p.suffix}",
                       "artist": artist, "license": licence,
                       "license_url": "https://creativecommons.org/licenses/by/4.0",
                       "attribution_required": True, "year": 2024, "description": "stress ground"})
    (folder / "credits.json").write_text(json.dumps({"reviewed": True, "images": images}, ensure_ascii=False),
                                         encoding="utf-8", newline="\n")
    return out
