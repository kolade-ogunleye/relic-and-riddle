"""
scripts/goldens.py
==================
Golden images for frozen designs (QF-29a). When the operator agrees a design is FROZEN (FREEZE-PLAYBOOK.md), the
lander takes its golden: the design is rendered from pinned, committed inputs and its pixels are recorded. From then
on every test run renders it again and compares, so any later change that touches the design fails CI; the change
path is then a light re-validation (critic, the diff board this tool draws, one lens on the changed frames).

The store (tests/goldens/):
    designs.py               the registry: DESIGNS = {name: Design(...)} (a design is added as it freezes)
    <design>/manifest.json   design, surface, case, format, freeze date, commit, pinned inputs (path -> SHA-256),
                             and the accepted toolchain variants: each a toolchain fingerprint with the SHA-256 of
                             every frame's full-resolution pixels
    <design>/<label>.webp    the reference image of each frame: half size, lossless (diff boards and the tolerance
                             comparison; the exact check uses the full-resolution pixel hash)

The check (tests/test_goldens.py, and `check` below):
- the pinned inputs must still hash as recorded (a changed input is reported, never compared);
- the design is rendered in a clean subprocess (a whitelisted environment, the checkout's .env ignored, the design's
  own settings only), so a developer's shell or .env cannot change a golden render;
- when the render's toolchain fingerprint equals an accepted variant's, every frame must match that variant's pixels
  exactly (SHA-256 of the full-resolution pixels): any change, a 1-px shift or one level of one colour, fails;
- when the fingerprint is new (the rendering libraries: Pillow and its FreeType/raqm/FriBiDi/HarfBuzz, NumPy,
  OpenCV, skia, uharfbuzz, fontTools, SciPy versions; for video also the ffmpeg build, which carries libass and
  libx264, and x264's settings and thread count read from the encoded stream) the check FAILS with "toolchain
  changed: re-validate", whatever the pixels: it reports whether the pixels equal an accepted variant (accepting the
  toolchain is then mechanical) or how far they are from the closest one under the documented tolerance (TOLERANCE
  below). Never a silent pass. Host properties measured not to move pixels (Python release, platform, NumPy's SIMD
  dispatch, CPU count) are recorded as information only (host_info), so mixed CI runner CPUs do not fail a golden.
- every render records the commit and the tree's changes (tree_state); a toolchain whose pixels differ is accepted
  only from a candidate rendered on a clean tree at the golden's design (its commit equal to the golden's in every
  file but golden data: tests/goldens/<design>/manifest.json and *.webp; the registry designs.py is design code),
  so a design change can never be re-baselined as a toolchain.

Video goldens are frame samples of the design's clip encoded by the production encoder (QF-134,
encoding.delivery_video_encoder); the samples are decoded as yuv420p (H.264 decoding is bit-exact) and hashed as
decoded; their RGB (BT.709 limited range) is only for the reference images and boards.

Commands (repository root, the project's virtual environment):
    goldens.py fingerprint                         print this machine's toolchain fingerprint (still and video)
    goldens.py list                                the registry's designs and their goldens
    goldens.py take <design> --frozen-on DATE      take a golden (the lander, only after the operator's FROZEN)
    goldens.py check [<design> ...] [--out DIR]    render and compare; a failing design gets a diff board in DIR
    goldens.py board <design> --out DIR            render and draw the diff board against the golden
    goldens.py accept-toolchain <design> [--from DIR] [--revalidated NOTE]
                                                   record a new toolchain variant: pixels identical to an accepted
                                                   variant are accepted as they are; different pixels need the diff
                                                   board looked at, --revalidated "<who/what was checked>" and a
                                                   candidate rendered clean from the golden's design
`--registry FILE` and `--store DIR` choose another registry/store (the QF-29 proof uses temporary ones).
Local renders only: no network, no API; writes only the store (take, accept-toolchain) or the --out folder.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as _dt
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "tests" / "goldens"
REGISTRY = STORE / "designs.py"
SCHEMA = 1
REFERENCE_SCALE = 2          # reference images are stored at 1/2 size (Image.reduce(2): exact box average)
REFERENCE_FORMAT = "webp"    # lossless WebP: decodes to exactly the pixels written (README: sizes measured vs PNG)
RENDER_TIMEOUT = 900         # seconds for one design's render subprocess

# The tolerance, used only when the toolchain fingerprint is new (it never makes a check pass). Measured on the QF-29
# proof fixtures (docs/reviews/quality-fixes-2026-11/QF-29a/README.md), at reference size: a frame is "within
# tolerance" of a reference when at most SHARE_MAX of its pixels differ by more than LEVEL in any channel, none by
# more than PEAK, and no 16x16 block's mean signed difference (any channel) exceeds BLOCK_BIAS levels (a colour
# change moves whole glyphs one way; encoder noise averages out). Measured: x264 on another thread count (another
# machine's cores) peak 24, share 0, block bias <= 2.18; a 1-px text shift share 0.18-1.1%, peak 65-139; a six-level
# colour change block bias 5.9-6.0 (peak 6-43).
TOLERANCE = {"level": 24, "share_max": 0.0005, "peak": 96, "block": 16, "block_bias": 3.0}

# The environment a golden render sees: what the OS, Python and ffmpeg need to run, nothing a setting could read.
ENV_KEEP = ("PATH", "SYSTEMROOT", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR", "HOME",
            "USERPROFILE", "LOCALAPPDATA", "APPDATA", "PROGRAMDATA", "LANG", "LC_ALL", "XDG_RUNTIME_DIR",
            "FONTCONFIG_FILE", "FONTCONFIG_PATH")

_PILLOW_LIBS = ("freetype2", "raqm", "fribidi", "harfbuzz", "libjpeg_turbo", "zlib", "littlecms2", "webp")
_PACKAGES = ("pillow", "numpy", "opencv-python-headless", "skia-python", "uharfbuzz", "fonttools", "scipy")


class GoldenError(Exception):
    """A golden cannot be taken or checked as asked (bad input, missing golden, refused environment)."""


@dataclasses.dataclass(frozen=True)
class Design:
    """One frozen design: surface x case x format, rendered from pinned committed inputs.

    render(inputs, work) gets {repo-relative path: absolute Path} for every pinned input and a scratch folder; a
    still returns {label: PIL image}; a video returns the Path of its clip encoded by the production encoder, and
    `samples` names the frame numbers compared. `settings` are environment variables the render runs with (the
    only configuration a golden render sees besides the code's defaults)."""
    name: str
    surface: str
    case: str
    format: str
    kind: str                                   # "still" | "video"
    inputs: tuple[str, ...]
    render: Callable[[dict[str, Path], Path], Any]
    samples: tuple[int, ...] = ()
    settings: Mapping[str, str] = dataclasses.field(default_factory=dict)

    def __post_init__(self):
        if self.kind not in ("still", "video"):
            raise GoldenError(f"{self.name}: kind must be still or video, not {self.kind!r}")
        if self.kind == "video" and not self.samples:
            raise GoldenError(f"{self.name}: a video golden names the frames it samples")
        if not self.name or "/" in self.name or "\\" in self.name or self.name.startswith("."):
            raise GoldenError(f"bad design name {self.name!r}")


# --------------------------------------------------------------------------------------------- helpers
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _json(data: Any) -> str:
    return json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json(data).encode("utf-8"))


def fingerprint_id(fp: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(fp, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)


def load_registry(path: Path | None = None) -> dict[str, Design]:
    """DESIGNS of a registry file (loaded by path: the store's designs.py or a proof registry)."""
    path = Path(path or REGISTRY).resolve()
    if not path.is_file():
        raise GoldenError(f"no golden registry at {path}")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    name = "_golden_registry_" + hashlib.sha256(str(path).encode()).hexdigest()[:12]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    designs = getattr(module, "DESIGNS", None)
    if not isinstance(designs, dict) or not all(isinstance(d, Design) and d.name == k for k, d in designs.items()):
        raise GoldenError(f"{path}: DESIGNS must map each design's name to its Design")
    return designs


def input_problems(root: Path, paths: tuple[str, ...] | list[str]) -> list[str]:
    """Why each pinned input cannot be one: missing, outside the repository, gitignored or not committed."""
    problems = []
    for rel in paths:
        p = (root / rel).resolve()
        if Path(rel).is_absolute() or ".." in Path(rel).parts:
            problems.append(f"{rel}: inputs are repository-relative paths")
            continue
        if not p.is_file():
            problems.append(f"{rel}: missing")
            continue
        if _git(root, "check-ignore", "-q", "--no-index", "--", rel).returncode == 0:
            problems.append(f"{rel}: gitignored (inputs are committed fixtures only)")
        elif _git(root, "ls-files", "--error-unmatch", "--", rel).returncode != 0:
            problems.append(f"{rel}: not committed")
    return problems


# ----------------------------------------------------------------------------------------- fingerprint
def _numpy_simd() -> list[str]:
    try:
        from numpy._core import _multiarray_umath as um
    except ImportError:   # NumPy 1.x layout
        from numpy.core import _multiarray_umath as um
    features = getattr(um, "__cpu_features__", {})
    baseline = list(getattr(um, "__cpu_baseline__", []))
    return baseline + [d for d in getattr(um, "__cpu_dispatch__", []) if features.get(d) and d not in baseline]


def ffmpeg_identity() -> dict[str, str]:
    """The ffmpeg build: its version line and the executable's SHA-256 (libass, libx264, FreeType, HarfBuzz and
    FriBidi are linked into the build, so the binary's hash pins them; ffmpeg prints no libass version)."""
    exe = shutil.which("ffmpeg")
    if not exe:
        raise GoldenError("ffmpeg is not on PATH (video goldens are encoded by the production encoder)")
    out = subprocess.run([exe, "-hide_banner", "-version"], capture_output=True, text=True, timeout=60,
                         check=False).stdout
    return {"version": (out.splitlines() or [""])[0].strip(), "sha256": sha256_file(Path(exe))}


def toolchain(kind: str = "still") -> dict[str, Any]:
    """The hard fingerprint: the rendering libraries a render's pixels depend on besides the code and the inputs
    (a new value fails the check as "toolchain changed"). Host properties measured not to move pixels (host_info)
    are recorded beside it, never compared."""
    from importlib import metadata

    from PIL import features
    fp: dict[str, Any] = {"pillow_libs": {k: features.version(k) for k in _PILLOW_LIBS}}
    packages = {}
    for name in _PACKAGES:
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    fp["packages"] = packages
    if kind == "video":
        fp["ffmpeg"] = ffmpeg_identity()
    return fp


def host_info() -> dict[str, Any]:
    """Information only, never part of the fingerprint: the Python release, the platform and NumPy's active SIMD
    dispatch. Measured not to change the proof fixtures' pixels (README: NumPy with X86_V3 disabled and Windows vs
    Linux both pixel-identical), so mixed CI runner CPUs or a Python patch release do not fail a golden; a pixel
    change they did cause would still fail, as a design change."""
    return {"python": f"{platform.python_implementation()} {platform.python_version()}",
            "platform": f"{sys.platform} {platform.machine()}", "numpy_simd": _numpy_simd(),
            "cpus": os.cpu_count()}


# Golden DATA, the only files a commit may change and still render the golden's design: a taken golden's manifest
# and its reference images, tests/goldens/<design>/manifest.json and tests/goldens/<design>/*.webp. Everything else
# counts as design code, the registry tests/goldens/designs.py included (its render functions and settings decide
# what a golden draws), and so does any other file under tests/goldens/ (test_store_holds_together refuses those).
_GOLDEN_DATA = re.compile(r"^tests/goldens/[^/]+/(manifest\.json|[^/]+\.webp)$")


def is_golden_data(rel: str) -> bool:
    """A repository-relative path that is a taken golden's manifest or reference image (not design code)."""
    return bool(_GOLDEN_DATA.match(rel.replace("\\", "/")))


def _porcelain_paths(stdout: str) -> list[str]:
    """Paths of `git status --porcelain` lines (both sides of a rename)."""
    paths = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        for part in line[3:].split(" -> "):
            paths.append(part.strip().strip('"'))
    return paths


def tree_state(root: Path) -> dict[str, Any]:
    """The commit a render came from and the tree's changes other than golden data (tracked or untracked,
    gitignored files excluded; tests/goldens/designs.py counts): a candidate variant carries it, so a toolchain is
    never accepted from a changed design."""
    head = _git(root, "rev-parse", "HEAD").stdout.strip() or None
    status = _git(root, "status", "--porcelain")
    changed = sorted({q for q in _porcelain_paths(status.stdout) if not is_golden_data(q)})
    return {"commit": head, "dirty": bool(changed) or status.returncode != 0, "changed": changed[:20],
            "changed_count": len(changed)}


def design_tree_changes(root: Path, a: str, b: str) -> list[str]:
    """Files other than golden data that differ between commits a and b (tests/goldens/designs.py counts): a
    candidate from b renders the golden's design only when this is empty."""
    out = _git(root, "diff", "--name-only", a, b)
    if out.returncode != 0:
        raise GoldenError(f"cannot compare commits {a[:10]} and {b[:10]} here (fetch them): "
                          f"{out.stderr.strip()[-300:]}")
    return [q for q in out.stdout.splitlines() if q.strip() and not is_golden_data(q.strip())]


def x264_settings(clip: Path) -> str:
    """x264's version and settings as written into the stream (SEI user data): the core version, every encoder
    option and the thread count, which the encoder takes from the machine's cores and which changes the bits."""
    data = clip.read_bytes()
    start = data.find(b"x264 - core")
    if start < 0:
        raise GoldenError(f"{clip.name}: not an x264 stream (video goldens use the production encoder, libx264)")
    end = data.find(b"\x00", start)
    return data[start:end if end > 0 else start + 4096].decode("ascii", "replace")


# ------------------------------------------------------------------------------------------- rendering
def _clean_env(settings: Mapping[str, str], extra: Mapping[str, str] | None) -> dict[str, str]:
    env = {k: os.environ[k] for k in ENV_KEEP if k in os.environ}
    env.update({"PYTHONHASHSEED": "0", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"})
    env.update({str(k): str(v) for k, v in settings.items()})
    env.update({str(k): str(v) for k, v in (extra or {}).items()})
    return env


@dataclasses.dataclass
class Rendering:
    design: str
    fingerprint: dict[str, Any]
    frames: dict[str, dict[str, Any]]         # label -> {"sha256", "pixel_format", "size"}
    images: dict[str, Any]                    # label -> full-resolution RGB PIL image
    inputs: dict[str, str]
    seconds: float
    info: dict[str, Any] = dataclasses.field(default_factory=dict)       # host_info(): never compared
    tree: dict[str, Any] = dataclasses.field(default_factory=dict)       # tree_state() at render time

    @property
    def fingerprint_id(self) -> str:
        return fingerprint_id(self.fingerprint)


def render(name: str, registry: Path | None = None, root: Path = ROOT, extra_env: Mapping[str, str] | None = None,
           ) -> Rendering:
    """Render one design in a clean subprocess and load its frames."""
    from PIL import Image
    registry = Path(registry or REGISTRY).resolve()
    design = load_registry(registry).get(name)
    if design is None:
        raise GoldenError(f"{name}: not in the registry {registry}")
    with tempfile.TemporaryDirectory(prefix="golden-") as tmp:
        out = Path(tmp) / "out"
        cmd = [sys.executable, str(Path(__file__).resolve()), "--registry", str(registry), "_render", "--design",
               name, "--root", str(root), "--out", str(out)]
        proc = subprocess.run(cmd, cwd=str(root), env=_clean_env(design.settings, extra_env), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=RENDER_TIMEOUT,
                              check=False)
        if proc.returncode != 0:
            raise GoldenError(f"{name}: the render failed (exit {proc.returncode}):\n{proc.stderr[-3000:]}")
        result = json.loads((out / "result.json").read_text(encoding="utf-8"))
        images = {}
        for label in result["frames"]:
            with Image.open(out / "frames" / f"{label}.png") as im:
                images[label] = im.convert("RGB")
                images[label].load()
    return Rendering(name, result["fingerprint"], result["frames"], images, result["inputs"], result["seconds"],
                     result.get("info", {}), result.get("tree", {}))


def _ignore_env_file(root: Path) -> None:
    """Import src.config as if the checkout had no .env: a golden renders with the code's defaults and the design's
    settings only (the development .env must not change a golden; production has no checkout-local goldens)."""
    env_file = (root / ".env").resolve()
    real_exists = Path.exists

    def exists(self, *a, **k):
        try:
            if self.resolve() == env_file:
                return False
        except OSError:
            pass
        return real_exists(self, *a, **k)
    Path.exists = exists
    try:
        from src import config
    finally:
        Path.exists = real_exists
    if config.ENV_FILE_KEYS:
        raise GoldenError(f"a .env was read ({len(config.ENV_FILE_KEYS)} keys): goldens render with defaults only")


def yuv420p_to_rgb(raw: bytes, width: int, height: int):
    """RGB of one decoded yuv420p frame, BT.709 limited range (for reference images and boards only)."""
    import numpy as np
    from PIL import Image
    n = width * height
    y = np.frombuffer(raw, np.uint8, n).reshape(height, width).astype(np.float64)
    cw, ch = (width + 1) // 2, (height + 1) // 2
    u = np.frombuffer(raw, np.uint8, cw * ch, n).reshape(ch, cw).astype(np.float64)
    v = np.frombuffer(raw, np.uint8, cw * ch, n + cw * ch).reshape(ch, cw).astype(np.float64)
    u = u.repeat(2, 0).repeat(2, 1)[:height, :width] - 128.0
    v = v.repeat(2, 0).repeat(2, 1)[:height, :width] - 128.0
    yy = (y - 16.0) * (255.0 / 219.0)
    k = 255.0 / 224.0
    r = yy + 1.5748 * k * v
    g = yy - 0.1873 * k * u - 0.4681 * k * v
    b = yy + 1.8556 * k * u
    rgb = np.rint(np.stack([r, g, b], -1)).clip(0, 255).astype(np.uint8)
    return Image.fromarray(rgb, "RGB")


def decode_samples(clip: Path, samples: tuple[int, ...]) -> dict[int, tuple[bytes, int, int]]:
    """The sampled frames of an encoded clip as decoded yuv420p bytes (bit-exact H.264 decoding)."""
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height,pix_fmt", "-of", "json", str(clip)],
                           capture_output=True, text=True, timeout=120, check=False)
    stream = json.loads(probe.stdout or "{}").get("streams", [{}])[0]
    w, h = int(stream.get("width", 0)), int(stream.get("height", 0))
    if stream.get("pix_fmt") != "yuv420p" or not w or not h:
        raise GoldenError(f"{clip.name}: expected a yuv420p H.264 clip, got {stream}")
    select = "+".join(f"eq(n\\,{i})" for i in samples)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-threads", "1", "-i", str(clip), "-map", "0:v:0",
                          "-vf", f"select={select}", "-fps_mode", "passthrough", "-f", "rawvideo",
                          "-pix_fmt", "yuv420p", "-"], capture_output=True, timeout=300, check=False)
    if raw.returncode != 0:
        raise GoldenError(f"{clip.name}: decoding failed: {raw.stderr.decode(errors='replace')[-500:]}")
    size = w * h + 2 * ((w + 1) // 2) * ((h + 1) // 2)
    data = raw.stdout
    if len(data) != size * len(samples):
        raise GoldenError(f"{clip.name}: {len(data) // size} of the {len(samples)} sampled frames exist")
    return {i: (data[k * size:(k + 1) * size], w, h) for k, i in enumerate(sorted(samples))}


def _render_here(registry: Path, name: str, root: Path, out: Path) -> None:
    """The subprocess side of render(): import, render, hash, write frames and result.json."""
    import time
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    _ignore_env_file(root)
    design = load_registry(registry)[name]
    problems = input_problems(root, design.inputs)
    if problems:
        raise GoldenError("pinned inputs: " + "; ".join(problems))
    inputs = {rel: (root / rel).resolve() for rel in design.inputs}
    hashes = {rel: sha256_file(p) for rel, p in inputs.items()}
    (out / "frames").mkdir(parents=True, exist_ok=True)
    work = out / "work"
    work.mkdir()
    t0 = time.perf_counter()
    result = design.render(inputs, work)
    frames: dict[str, dict[str, Any]] = {}
    fp = toolchain(design.kind)
    if design.kind == "still":
        if not isinstance(result, dict) or not result:
            raise GoldenError(f"{name}: a still design returns {{label: image}}")
        for label, im in sorted(result.items()):
            im = im.convert("RGB")
            frames[label] = {"sha256": hashlib.sha256(im.tobytes()).hexdigest(), "pixel_format": "rgb24",
                             "size": list(im.size)}
            im.save(out / "frames" / f"{label}.png", compress_level=1)
    else:
        clip = Path(result)
        fp["x264"] = x264_settings(clip)
        for i, (raw, w, h) in decode_samples(clip, design.samples).items():
            label = f"f{i:05d}"
            frames[label] = {"sha256": hashlib.sha256(raw).hexdigest(), "pixel_format": "yuv420p", "size": [w, h]}
            yuv420p_to_rgb(raw, w, h).save(out / "frames" / f"{label}.png", compress_level=1)
    _write_json(out / "result.json", {"fingerprint": fp, "frames": frames, "inputs": hashes,
                                      "seconds": round(time.perf_counter() - t0, 2), "info": host_info(),
                                      "tree": tree_state(root)})


# ---------------------------------------------------------------------------------------- the store
def manifest_path(store: Path, name: str) -> Path:
    return Path(store) / name / "manifest.json"


def load_manifest(store: Path, name: str) -> dict[str, Any]:
    path = manifest_path(store, name)
    if not path.is_file():
        raise GoldenError(f"{name}: no golden in {store}")
    return json.loads(path.read_text(encoding="utf-8"))


def reference_of(im):
    """The stored reference: half size by an exact box average."""
    return im.reduce(REFERENCE_SCALE) if REFERENCE_SCALE > 1 else im


def _save_reference(im, path: Path) -> None:
    im.save(path, "WEBP", lossless=True, quality=100, method=6, exact=True)


def _ref_name(label: str, variant: int) -> str:
    return f"{label}.{REFERENCE_FORMAT}" if variant == 0 else f"{label}.v{variant}.{REFERENCE_FORMAT}"


def manifest_problems(store: Path, name: str, manifest: Mapping[str, Any], design: Design | None) -> list[str]:
    """A manifest that does not hold together: schema, design fields, references present, variants consistent."""
    problems = []
    if manifest.get("schema") != SCHEMA:
        problems.append(f"schema {manifest.get('schema')!r} (expected {SCHEMA})")
    if manifest.get("design") != name:
        problems.append(f"names design {manifest.get('design')!r}")
    for key in ("surface", "case", "format", "kind", "frozen_on", "commit", "inputs", "variants"):
        if not manifest.get(key):
            problems.append(f"no {key}")
    if design is None:
        problems.append("not in the registry")
    else:
        for key in ("surface", "case", "format", "kind"):
            if manifest.get(key) != getattr(design, key):
                problems.append(f"{key} {manifest.get(key)!r} != the registry's {getattr(design, key)!r}")
        if sorted(manifest.get("inputs") or {}) != sorted(design.inputs):
            problems.append("pinned inputs differ from the registry's")
        if dict(manifest.get("settings") or {}) != dict(design.settings):
            problems.append("settings differ from the registry's")
    labels = None
    for k, variant in enumerate(manifest.get("variants") or []):
        frames = variant.get("frames") or {}
        if labels is None:
            labels = sorted(frames)
        elif sorted(frames) != labels:
            problems.append(f"variant {k} samples other frames")
        if fingerprint_id(variant.get("fingerprint") or {}) != variant.get("fingerprint_id"):
            problems.append(f"variant {k}: fingerprint_id does not match its fingerprint")
        for label, frame in frames.items():
            ref = frame.get("reference")
            if not ref or not (Path(store) / name / ref).is_file():
                problems.append(f"variant {k} {label}: reference image {ref!r} missing")
    return problems


def _check_clean_tree(root: Path, store: Path) -> str:
    """HEAD's sha; refuses a tree with any change but golden data (a golden records a commit, so its design must be
    committed: an uncommitted registry entry in tests/goldens/designs.py is refused too). A store outside the
    repository (the proofs' temporary stores) is not in the tree at all."""
    head = _git(root, "rev-parse", "HEAD").stdout.strip()
    if not head:
        raise GoldenError("not a git checkout")
    dirty = [q for q in _porcelain_paths(_git(root, "status", "--porcelain").stdout) if not is_golden_data(q)]
    if dirty:
        raise GoldenError("the tree has uncommitted changes besides golden data (" + ", ".join(dirty[:5]) +
                          "): a golden is taken from a commit")
    return head


def take(name: str, frozen_on: str, registry: Path | None = None, store: Path = STORE, root: Path = ROOT, *,
         retake_reason: str | None = None, allow_dirty: bool = False, extra_env: Mapping[str, str] | None = None,
         ) -> dict[str, Any]:
    """Take a design's golden (after the operator agreed it is FROZEN): render, store references, write the
    manifest. Refuses an existing golden unless retake_reason says why (the old manifest is kept in history)."""
    _dt.date.fromisoformat(frozen_on)
    registry = Path(registry or REGISTRY).resolve()
    design = load_registry(registry).get(name)
    if design is None:
        raise GoldenError(f"{name}: not in the registry {registry} (add the design there first)")
    problems = input_problems(root, design.inputs)
    if problems:
        raise GoldenError("pinned inputs: " + "; ".join(problems))
    path = manifest_path(store, name)
    if path.exists() and not retake_reason:
        raise GoldenError(f"{name}: a golden exists; a retake needs --retake-reason")
    commit = _git(root, "rev-parse", "HEAD").stdout.strip() if allow_dirty else _check_clean_tree(root, store)
    r = render(name, registry, root, extra_env)
    folder = Path(store) / name
    if folder.exists():
        for old in folder.glob(f"*.{REFERENCE_FORMAT}"):
            old.unlink()
    folder.mkdir(parents=True, exist_ok=True)
    frames = {}
    for label, frame in r.frames.items():
        ref = _ref_name(label, 0)
        _save_reference(reference_of(r.images[label]), folder / ref)
        frames[label] = {**frame, "reference": ref}
    manifest = {
        "schema": SCHEMA, "design": name, "surface": design.surface, "case": design.case, "format": design.format,
        "kind": design.kind, "frozen_on": frozen_on, "commit": commit,
        "taken_at": _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "inputs": r.inputs, "settings": dict(design.settings), "samples": list(design.samples),
        "reference": {"scale": REFERENCE_SCALE, "format": f"{REFERENCE_FORMAT} lossless"},
        "variants": [{"fingerprint_id": r.fingerprint_id, "fingerprint": r.fingerprint, "how": "taken",
                      "accepted_on": _dt.date.today().isoformat(), "info": r.info, "frames": frames}],
    }
    if retake_reason:
        manifest["retake_reason"] = retake_reason
    _write_json(path, manifest)
    return manifest


# ----------------------------------------------------------------------------------------- comparing
def frame_stats(a, b) -> dict[str, Any]:
    """Pixel differences of two same-size RGB images: the share of pixels changed and over the tolerance level, the
    peak channel delta, the largest block bias (TOLERANCE) and the changed box."""
    import numpy as np
    x = np.asarray(a.convert("RGB"), np.int16)
    y = np.asarray(b.convert("RGB"), np.int16)
    if x.shape != y.shape:
        return {"size_differs": [list(a.size), list(b.size)], "within_tolerance": False}
    signed = (y - x).astype(np.float64)
    d = np.abs(signed).max(-1)
    over = d > TOLERANCE["level"]
    changed = d > 0
    ys, xs = np.nonzero(changed)
    box = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1] if len(xs) else None
    k = TOLERANCE["block"]
    h, w = (signed.shape[0] // k) * k, (signed.shape[1] // k) * k
    blocks = signed[:h, :w].reshape(h // k, k, w // k, k, 3).mean((1, 3)) if h and w else np.zeros((1, 1, 3))
    bias = float(np.abs(blocks).max())
    share_over = float(over.mean())
    peak = int(d.max())
    return {"changed_share": round(float(changed.mean()), 6), "over_level_share": round(share_over, 6),
            "peak": peak, "mean": round(float(d.mean()), 4), "block_bias": round(bias, 2), "box": box,
            "within_tolerance": (share_over <= TOLERANCE["share_max"] and peak <= TOLERANCE["peak"]
                                 and bias <= TOLERANCE["block_bias"])}


@dataclasses.dataclass
class CheckResult:
    design: str
    status: str             # "pass" | "design-changed" | "toolchain-changed" | "input-changed" | "error"
    message: str
    variant: dict[str, Any] | None = None   # the render as a variant record (fingerprint, frames)
    stats: dict[str, Any] | None = None     # label -> frame_stats against the closest variant's references
    rendering: Rendering | None = None

    @property
    def ok(self) -> bool:
        return self.status == "pass"


def _references(store: Path, name: str, variant: Mapping[str, Any]):
    from PIL import Image
    refs = {}
    for label, frame in variant["frames"].items():
        with Image.open(Path(store) / name / frame["reference"]) as im:
            refs[label] = im.convert("RGB")
    return refs


def _diff_line(old: Mapping[str, Any], new: Mapping[str, Any], prefix: str = "") -> list[str]:
    lines = []
    for k in sorted(set(old) | set(new)):
        a, b = old.get(k), new.get(k)
        if isinstance(a, dict) and isinstance(b, dict):
            lines += _diff_line(a, b, f"{prefix}{k}.")
        elif k == "x264" and isinstance(a, str) and isinstance(b, str) and a != b:
            # name the differing x264 options (threads=48 -> 6), not the whole settings string
            oa = dict(t.split("=", 1) for t in a.split() if "=" in t)
            ob = dict(t.split("=", 1) for t in b.split() if "=" in t)
            diff = [f"{o}={oa.get(o)} -> {ob.get(o)}" for o in sorted(set(oa) | set(ob)) if oa.get(o) != ob.get(o)]
            head_a, head_b = a.split(" - options:")[0], b.split(" - options:")[0]
            if head_a != head_b:
                diff.insert(0, f"{head_a!r} -> {head_b!r}")
            lines.append(f"{prefix}x264: " + ", ".join(diff))
        elif a != b:
            lines.append(f"{prefix}{k}: {a!r} -> {b!r}")
    return lines


def compare(name: str, manifest: Mapping[str, Any], r: Rendering, store: Path) -> CheckResult:
    """Compare a rendering with a golden's manifest (no rendering here)."""
    variant_rec = {"fingerprint_id": r.fingerprint_id, "fingerprint": r.fingerprint, "frames": r.frames,
                   "info": r.info, "tree": r.tree}
    changed_inputs = [p for p, h in manifest["inputs"].items() if r.inputs.get(p) != h]
    if changed_inputs:
        return CheckResult(name, "input-changed", f"{name}: pinned input changed since the golden: "
                           + ", ".join(changed_inputs) + " (a golden's inputs are fixed; retake after re-validation)",
                           variant_rec, None, r)
    variants = manifest["variants"]
    if sorted(r.frames) != sorted(variants[0]["frames"]):
        return CheckResult(name, "design-changed", f"{name}: frames {sorted(r.frames)} != the golden's "
                           f"{sorted(variants[0]['frames'])}", variant_rec, None, r)
    hashes = {label: f["sha256"] for label, f in r.frames.items()}
    same = [v for v in variants if all(v["frames"][k]["sha256"] == h for k, h in hashes.items())]
    known = next((v for v in variants if v["fingerprint_id"] == r.fingerprint_id), None)
    if known is not None and known in same:
        return CheckResult(name, "pass", f"{name}: identical to the golden ({len(hashes)} frame(s), toolchain "
                           f"{r.fingerprint_id})", variant_rec, None, r)
    # A difference: measure it against the closest accepted variant (references at reference size).
    best, best_stats = None, None
    for v in ([known] if known is not None else variants):
        refs = _references(store, name, v)
        stats = {k: frame_stats(refs[k], reference_of(r.images[k])) for k in hashes}
        score = sum(s.get("over_level_share", 1.0) for s in stats.values())
        if best is None or score < best[0]:
            best, best_stats = (score, v), stats
    v = best[1]
    worst = "; ".join(f"{k}: {s.get('changed_share', 1.0):.4%} of pixels changed, peak {s.get('peak')}"
                      for k, s in best_stats.items())
    within = all(s.get("within_tolerance") for s in best_stats.values())
    if known is not None:
        changed = [k for k, h in hashes.items() if known["frames"][k]["sha256"] != h]
        return CheckResult(name, "design-changed",
                           f"{name}: the design's pixels changed (same toolchain {r.fingerprint_id}): frames "
                           f"{', '.join(changed)} differ from the golden ({worst}). A frozen design changed: "
                           "re-validate (critic, diff board, one lens on the changed frames), then retake.",
                           variant_rec, best_stats, r)
    diff = _diff_line(v["fingerprint"], r.fingerprint)
    if same:
        verdict = (f"the pixels are identical to accepted variant {same[0]['fingerprint_id']}: accept this toolchain "
                   f"with `scripts/goldens.py accept-toolchain {name}`")
    elif within:
        verdict = (f"the pixels differ within the documented tolerance ({worst}): look at the diff board, then "
                   f"`accept-toolchain {name} --revalidated \"...\"`")
    else:
        verdict = (f"the pixels differ BEYOND the tolerance ({worst}): the design looks changed as well; re-validate "
                   "the design before accepting anything")
    return CheckResult(name, "toolchain-changed",
                       f"{name}: toolchain changed: re-validate. This render's toolchain {r.fingerprint_id} is not "
                       f"one the golden accepted ({', '.join(x['fingerprint_id'] for x in variants)}); against "
                       f"{v['fingerprint_id']}: " + "; ".join(diff[:12]) + f". {verdict}.", variant_rec, best_stats, r)


def check(name: str, registry: Path | None = None, store: Path = STORE, root: Path = ROOT,
          extra_env: Mapping[str, str] | None = None) -> CheckResult:
    """Render the design and compare it with its golden."""
    manifest = load_manifest(store, name)
    design = load_registry(registry).get(name)
    problems = manifest_problems(store, name, manifest, design)
    if problems:
        return CheckResult(name, "error", f"{name}: the golden's manifest: " + "; ".join(problems))
    r = render(name, registry, root, extra_env)
    return compare(name, manifest, r, store)


# ------------------------------------------------------------------------------------- actuals, boards
def default_actuals_dir() -> Path | None:
    """Where a failing check leaves its render: GOLDEN_ACTUALS_DIR, else on GitHub Actions the shard's artifact
    folder (QF-135 uploads $RUNNER_TEMP/shard/ from every shard, pass or fail), else nowhere."""
    if os.environ.get("GOLDEN_ACTUALS_DIR"):
        return Path(os.environ["GOLDEN_ACTUALS_DIR"])
    if os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("RUNNER_TEMP"):
        return Path(os.environ["RUNNER_TEMP"]) / "shard" / "golden-actuals"
    return None


def save_actuals(result: CheckResult, out: Path, store: Path = STORE) -> Path:
    """The failing render as a variant candidate: variant.json and the frames' reference-size images, plus the
    diff board. `accept-toolchain --from` reads this folder."""
    folder = Path(out) / result.design
    folder.mkdir(parents=True, exist_ok=True)
    r = result.rendering
    frames = {}
    for label, frame in r.frames.items():
        ref = f"{label}.{REFERENCE_FORMAT}"
        _save_reference(reference_of(r.images[label]), folder / ref)
        frames[label] = {**frame, "reference": ref}
    _write_json(folder / "variant.json", {"design": result.design, "status": result.status,
                                          "message": result.message, "inputs": r.inputs,
                                          "fingerprint_id": r.fingerprint_id, "fingerprint": r.fingerprint,
                                          "info": r.info, "tree": r.tree, "frames": frames,
                                          "stats": result.stats})
    try:
        board(result.design, load_manifest(store, result.design), r, folder / "board.png", store, result)
    except (OSError, ValueError, KeyError) as e:   # the board is a convenience; the variant record is the evidence
        (folder / "board-error.txt").write_text(f"{type(e).__name__}: {e}\n", encoding="utf-8")
    return folder


def board(name: str, manifest: Mapping[str, Any], r: Rendering, out: Path, store: Path = STORE,
          result: CheckResult | None = None) -> Path:
    """A diff board per sampled frame: golden | this render | difference x8 | changed pixels in red | 4x zoom on the
    largest change (golden beside or above the render). Lossless PNG (a 1-px shift must stay visible)."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    variants = manifest["variants"]
    v = next((x for x in variants if x["fingerprint_id"] == r.fingerprint_id), variants[0])
    refs = _references(store, name, v)
    cell_w = 360
    rows = []
    font = ImageFont.load_default(18)
    for label in sorted(r.frames):
        g = refs[label]
        a = reference_of(r.images[label])
        if a.size != g.size:
            a = a.resize(g.size)
        x, y = np.asarray(g, np.int16), np.asarray(a, np.int16)
        d = np.abs(x - y).max(-1)
        amp = Image.fromarray((np.clip(d * 8, 0, 255)).astype(np.uint8), "L").convert("RGB")
        mask = np.asarray(g, np.uint8).copy() // 3
        mask[d > 0] = (255, 40, 40)
        mask[d > TOLERANCE["level"]] = (255, 255, 0)
        marked = Image.fromarray(mask, "RGB")
        if d.any():   # the zoom sits on the largest change (a moved glyph edge, a recoloured stroke)
            cy, cx = (int(v) for v in np.unravel_index(int(d.argmax()), d.shape))
        else:
            cx, cy = g.width // 2, g.height // 2
        half = 45
        box = (max(0, cx - half), max(0, cy - half), min(g.width, cx + half), min(g.height, cy + half))
        zoom_g = g.crop(box).resize(((box[2] - box[0]) * 4, (box[3] - box[1]) * 4), Image.NEAREST)
        zoom_a = a.crop(box).resize(zoom_g.size, Image.NEAREST)
        side = g.width > g.height   # landscape frames: golden beside render; portrait: golden above render
        zoom = Image.new("RGB", (zoom_g.width * (2 if side else 1) + (4 if side else 0),
                                 zoom_g.height * (1 if side else 2) + (0 if side else 4)), (255, 255, 255))
        zoom.paste(zoom_g, (0, 0))
        zoom.paste(zoom_a, (zoom_g.width + 4, 0) if side else (0, zoom_g.height + 4))
        cells = []
        for im in (g, a, amp, marked):
            s = cell_w / im.width
            cells.append(im.resize((cell_w, round(im.height * s)), Image.LANCZOS))
        s = cells[0].height / zoom.height
        cells.append(zoom.resize((max(1, round(zoom.width * s)), cells[0].height), Image.NEAREST))
        h = cells[0].height + 30
        row = Image.new("RGB", (sum(c.width for c in cells) + 10 * len(cells), h), (24, 24, 24))
        xo = 0
        for c in cells:
            row.paste(c, (xo, 30))
            xo += c.width + 10
        st = frame_stats(g, a)
        ImageDraw.Draw(row).text((6, 5), f"{label}: golden | render | diff x8 | changed (red; yellow over level "
                                 f"{TOLERANCE['level']}) | 4x zoom   changed {st.get('changed_share', 0):.4%} "
                                 f"peak {st.get('peak')}", fill=(240, 240, 240), font=font)
        rows.append(row)
    head = result.message if result is not None else f"{name}: board against {v['fingerprint_id']}"
    words, lines, line = head.split(), [], ""
    width = max(r_.width for r_ in rows)
    for w in words:
        if len(line) + len(w) + 1 > width // 10:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    lines.append(line)
    header = 12 + 24 * len(lines)
    sheet = Image.new("RGB", (width, header + sum(r_.height + 8 for r_ in rows)), (16, 16, 16))
    d = ImageDraw.Draw(sheet)
    for k, text in enumerate(lines):
        d.text((8, 6 + 24 * k), text, fill=(255, 220, 120), font=font)
    yo = header
    for r_ in rows:
        sheet.paste(r_, (0, yo))
        yo += r_.height + 8
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, "PNG", optimize=True)
    return Path(out)


# --------------------------------------------------------------------------------- accepting a toolchain
def accept_toolchain(name: str, registry: Path | None = None, store: Path = STORE, root: Path = ROOT, *,
                     source: Path | None = None, revalidated: str | None = None,
                     extra_env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Add a toolchain variant to a golden. The candidate is a fresh local render, or a failing check's actuals
    folder (`source`, e.g. a CI shard's golden-actuals/<design>). Pixels identical to an accepted variant are
    recorded with no new image, from any commit. Different pixels need `revalidated` (who looked at which diff
    board) AND a candidate rendered from the golden's own design: a clean tree at a commit whose files equal the
    golden's commit except golden data (is_golden_data: tests/goldens/<design>/manifest.json and *.webp; the
    registry tests/goldens/designs.py is design code). Anything else is a design change: re-validate and retake.
    The accepted variant records the candidate's commit, tree state and frame stats. Refuses a candidate whose inputs
    differ from the golden's or whose frames do not match."""
    manifest = load_manifest(store, name)
    problems = manifest_problems(store, name, manifest, load_registry(registry).get(name))
    if problems:
        raise GoldenError(f"{name}: the golden's manifest: " + "; ".join(problems))
    from PIL import Image
    if source is not None:
        record = Path(source) / "variant.json"
        if not record.is_file():
            raise GoldenError(f"{source}: no variant.json (point --from at a failing check's "
                              "golden-actuals/<design> folder)")
        try:
            cand = json.loads(record.read_text(encoding="utf-8"))
            if cand.get("design") != name:
                raise GoldenError(f"{source}: a candidate for {cand.get('design')!r}, not {name}")
            if fingerprint_id(cand["fingerprint"]) != cand["fingerprint_id"]:
                raise GoldenError(f"{source}: fingerprint_id does not match its fingerprint")
            images = {}
            for label, f in cand["frames"].items():
                with Image.open(Path(source) / f["reference"]) as im:
                    images[label] = im.convert("RGB")
            inputs, fp, frames = cand["inputs"], cand["fingerprint"], cand["frames"]
            info, tree = cand.get("info") or {}, cand.get("tree") or {}
        except (KeyError, ValueError, OSError) as e:
            raise GoldenError(f"{source}: not a complete candidate ({type(e).__name__}: {e})") from e
    else:
        r = render(name, registry, root, extra_env)
        inputs, fp, frames, info, tree = r.inputs, r.fingerprint, r.frames, r.info, r.tree
        images = {k: reference_of(im) for k, im in r.images.items()}
    if inputs != manifest["inputs"]:
        raise GoldenError(f"{name}: the candidate was rendered from other inputs than the golden's")
    if sorted(frames) != sorted(manifest["variants"][0]["frames"]):
        raise GoldenError(f"{name}: the candidate samples other frames")
    fid = fingerprint_id(fp)
    if any(v["fingerprint_id"] == fid for v in manifest["variants"]):
        raise GoldenError(f"{name}: toolchain {fid} is already accepted")
    same = next((v for v in manifest["variants"]
                 if all(v["frames"][k]["sha256"] == f["sha256"] for k, f in frames.items())), None)
    candidate = {"commit": tree.get("commit"), "dirty": tree.get("dirty"), "changed": tree.get("changed", [])}
    if same is not None:
        new_frames = {k: {**f, "reference": same["frames"][k]["reference"]} for k, f in frames.items()}
        how, note, stats = "identical", f"pixels identical to {same['fingerprint_id']}", None
    else:
        if not revalidated:
            raise GoldenError(f"{name}: the pixels differ from every accepted variant: look at the diff board and "
                              "pass --revalidated '<who checked what>'")
        golden_commit = manifest["commit"]
        if not candidate["commit"]:
            raise GoldenError(f"{name}: the candidate does not record the commit it was rendered from: render it "
                              "again with this tool")
        if candidate["dirty"] is not False:
            raise GoldenError(f"{name}: the candidate was rendered from a tree with changes ("
                              + ", ".join(candidate["changed"][:5] or ["unknown"]) + "): a toolchain is accepted "
                              "only from the golden's design on a clean tree")
        moved = design_tree_changes(root, golden_commit, candidate["commit"])
        if moved:
            raise GoldenError(f"{name}: the candidate's commit {candidate['commit'][:10]} differs from the golden's "
                              f"{golden_commit[:10]} in design files ({', '.join(moved[:5])}"
                              f"{', ...' if len(moved) > 5 else ''}): that is a design change, not a toolchain; "
                              "re-validate the design and retake, or render the candidate from the golden's commit")
        refs = _references(store, name, manifest["variants"][0])
        stats = {k: frame_stats(refs[k], images[k]) for k in frames}
        k = len(manifest["variants"])
        new_frames = {}
        for label, f in frames.items():
            ref = _ref_name(label, k)
            _save_reference(images[label], Path(store) / name / ref)
            new_frames[label] = {"sha256": f["sha256"], "pixel_format": f["pixel_format"], "size": f["size"],
                                 "reference": ref}
        how, note = "revalidated", revalidated
    manifest["variants"].append({"fingerprint_id": fid, "fingerprint": fp, "how": how, "note": note,
                                 "accepted_on": _dt.date.today().isoformat(), "candidate": candidate,
                                 "stats_vs_taken": stats, "info": info, "frames": new_frames})
    _write_json(manifest_path(store, name), manifest)
    return manifest


# ------------------------------------------------------------------------------------------------ CLI
def _cli(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[1], formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--registry", type=Path, default=None)
    p.add_argument("--store", type=Path, default=STORE)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fingerprint")
    sub.add_parser("list")
    t = sub.add_parser("take")
    t.add_argument("design")
    t.add_argument("--frozen-on", required=True, help="the date the operator agreed the design is FROZEN")
    t.add_argument("--retake-reason")
    c = sub.add_parser("check")
    c.add_argument("designs", nargs="*")
    c.add_argument("--out", type=Path)
    b = sub.add_parser("board")
    b.add_argument("design")
    b.add_argument("--out", type=Path, required=True)
    a = sub.add_parser("accept-toolchain")
    a.add_argument("design")
    a.add_argument("--from", dest="source", type=Path)
    a.add_argument("--revalidated")
    r = sub.add_parser("_render")
    r.add_argument("--design", required=True)
    r.add_argument("--root", type=Path, required=True)
    r.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    try:
        if args.cmd == "_render":
            _render_here(Path(args.registry), args.design, args.root.resolve(), args.out)
        elif args.cmd == "fingerprint":
            print(_json({"still": toolchain("still"), "video": toolchain("video")}), end="")
        elif args.cmd == "list":
            for name, d in sorted(load_registry(args.registry).items()):
                has = manifest_path(args.store, name).is_file()
                print(f"{name}\t{d.kind}\t{d.surface} / {d.case} / {d.format}\t{'golden' if has else 'NO GOLDEN'}")
        elif args.cmd == "take":
            m = take(args.design, args.frozen_on, args.registry, args.store, retake_reason=args.retake_reason)
            print(f"{args.design}: golden taken at {m['commit'][:10]}, toolchain {m['variants'][0]['fingerprint_id']}")
        elif args.cmd == "check":
            names = args.designs or sorted(load_registry(args.registry))
            failed = 0
            for name in names:
                res = check(name, args.registry, args.store)
                print(("PASS " if res.ok else "FAIL ") + res.message)
                if not res.ok:
                    failed += 1
                    out = args.out or default_actuals_dir()
                    if out is not None and res.rendering is not None:
                        print(f"  actuals and diff board: {save_actuals(res, out, args.store)}")
            return 1 if failed else 0
        elif args.cmd == "board":
            manifest = load_manifest(args.store, args.design)
            rend = render(args.design, args.registry)
            res = compare(args.design, manifest, rend, args.store)
            print(board(args.design, manifest, rend, args.out / f"{args.design}.board.png", args.store, res))
        elif args.cmd == "accept-toolchain":
            m = accept_toolchain(args.design, args.registry, args.store, source=args.source,
                                 revalidated=args.revalidated)
            v = m["variants"][-1]
            print(f"{args.design}: toolchain {v['fingerprint_id']} accepted ({v['how']}: {v['note']})")
    except GoldenError as e:
        print(f"goldens: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    # Run as the module scripts.goldens, so a registry's `from scripts.goldens import Design` is this module's class.
    sys.path.insert(0, str(ROOT))
    sys.exit(importlib.import_module("scripts.goldens")._cli())
