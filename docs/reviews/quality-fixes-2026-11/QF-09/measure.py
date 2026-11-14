"""
QF-09 measurement: Pillow 13 + lockfile, the byte cap on Commons downloads and the process-wide pixel limit.

    python measure.py <repo root>

Runs against the repository at <repo root> (its own src/ and scripts/), with the interpreter that runs it (the
environment that repository installs). Prints JSON and writes measurements.json next to this file.

What it measures
  dependencies   the installed Pillow, requirements.txt's pins (pillow, scipy, any line that is not an exact pin),
                 the committed uv.lock (packages, sha256 hashes, agreement with requirements.txt), the CI workflow's
                 advisory step
  advisories     pip-audit on requirements.txt exactly as the bot's weekly check runs it (src/maintenance.py), and,
                 where a uv.lock exists, on the hashed lock as CI runs it
  pixel_limit    the value of Image.MAX_IMAGE_PIXELS seen at every open and decode while the pipeline's own functions
                 decode our large rasters (graphics._relief, upscale._upscale_ncnn with the binary's output faked,
                 scripts/fetch_relief.valid_relief) and a Commons download (commons.download_jpeg, the request
                 mocked): any decode under a lifted limit is one where a Commons photo decoded on another thread
                 went unchecked (and a changed limit, a lowered one too, applied to that photo as well)
  byte_cap       commons.download_jpeg given a 120 MB body (a valid JPEG padded with zeros; the request mocked):
                 how much of it was read and whether it was refused

Every input is generated here; no network call except pip-audit's advisory lookups (PyPI/OSV, free).
"""

import io
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import PIL
from PIL import Image, ImageFile

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT))   # the measured repository's src/ and scripts/

HERE = Path(__file__).resolve().parent
DEFAULT_LIMIT = Image.MAX_IMAGE_PIXELS
# A path that fails is recorded in the measurement, not hidden (the start commit's code may fail differently)
RECORDED = (OSError, ValueError, TypeError, AttributeError, SyntaxError, RuntimeError)


def _pins(text: str) -> dict:
    out, unpinned = {}, []
    for raw in text.splitlines():
        line = re.sub(r"\s+#.*$", "", raw).strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)\s*==\s*([^\s;]+)", line)
        if m:
            out[m.group(1).lower()] = m.group(2)
        else:
            unpinned.append(line)
    return {"pins": out, "not_exact_pins": unpinned}


def dependencies() -> dict:
    req = _pins((ROOT / "requirements.txt").read_text("utf-8"))
    lock = ROOT / "uv.lock"
    workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text("utf-8")
    out = {
        "pillow_installed": PIL.__version__,
        "pillow_pinned": req["pins"].get("pillow"),
        "scipy_pinned": req["pins"].get("scipy"),
        "requirements_not_exact_pins": req["not_exact_pins"],
        "uv_lock": lock.exists(),
        "ci_runs_pip_audit": "pip_audit" in workflow or "pip-audit" in workflow,
        "ci_installs_hashed_lock": "--require-hashes" in workflow,
        "ci_checks_requirements_against_lock": "check_lock.py" in workflow,
    }
    if lock.exists():
        text = lock.read_text("utf-8")
        out["uv_lock_packages"] = text.count("[[package]]")
        out["uv_lock_sha256_hashes"] = text.count('hash = "sha256:')
        res = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_lock.py"), str(ROOT)],
                             capture_output=True, text=True, check=False)
        out["requirements_agree_with_lock"] = res.returncode == 0
        out["check_lock_output"] = res.stdout.strip().splitlines()[-1:]
    return out


def _audit(args: list) -> dict:
    res = subprocess.run([sys.executable, "-m", "pip_audit", *args, "--format", "json", "--progress-spinner", "off"],
                         capture_output=True, text=True, timeout=1800, cwd=str(ROOT), check=False)
    if not res.stdout.strip():
        return {"error": res.stderr.strip()[-400:]}
    data = json.loads(res.stdout)
    deps = data["dependencies"] if isinstance(data, dict) else data
    vulns = [(d["name"], d.get("version"), v["id"]) for d in deps for v in (d.get("vulns") or [])]
    return {"packages_audited": len(deps), "advisories": len(vulns),
            "by_package": {f"{n} {ver}": sorted(i for nn, vv, i in vulns if (nn, vv) == (n, ver))
                           for n, ver, _ in vulns},
            "exit_status": res.returncode}


def advisories() -> dict:
    out = {"weekly_check_requirements": _audit(["-r", str(ROOT / "requirements.txt")])}
    if (ROOT / "uv.lock").exists():
        with tempfile.TemporaryDirectory() as tmp:
            locked = Path(tmp) / "locked.txt"
            exp = subprocess.run([sys.executable, "-m", "uv", "export", "--locked", "--all-groups", "--no-emit-project",
                                  "--format", "requirements-txt", "-q", "-o", str(locked)],
                                 capture_output=True, text=True, cwd=str(ROOT), check=False)
            out["ci_hashed_lock"] = (_audit(["--strict", "--require-hashes", "--disable-pip", "-r", str(locked)])
                                     if exp.returncode == 0 else {"error": exp.stderr.strip()[-400:]})
    return out


class _Spy:
    """Records Image.MAX_IMAGE_PIXELS at every Image.open and ImageFile.load."""

    def __enter__(self):
        self.seen = []
        self._open, self._load = Image.open, ImageFile.ImageFile.load
        spy = self

        def spy_open(*a, **k):
            spy.seen.append(Image.MAX_IMAGE_PIXELS)
            return spy._open(*a, **k)

        def spy_load(im, *a, **k):
            spy.seen.append(Image.MAX_IMAGE_PIXELS)
            return spy._load(im, *a, **k)
        Image.open, ImageFile.ImageFile.load = spy_open, spy_load
        return self

    def __exit__(self, *exc):
        Image.open, ImageFile.ImageFile.load = self._open, self._load

    def summary(self, error=None) -> dict:
        lifted = [v for v in self.seen if v is None or v > DEFAULT_LIMIT]
        changed = [v for v in self.seen if v != DEFAULT_LIMIT]
        return {"decodes": len(self.seen), "decodes_under_lifted_limit": len(lifted),
                "decodes_under_changed_limit": len(changed),
                "limits_seen": sorted({str(v) for v in self.seen}),
                "global_limit_after": str(Image.MAX_IMAGE_PIXELS), **({"error": error} if error else {})}


def _relief_tif(path: Path, size) -> Path:
    w, h = size
    ramp = (np.arange(w, dtype=np.uint16)[None, :] * 255 // (w - 1)).astype(np.uint8)
    Image.fromarray(np.repeat(ramp, h, axis=0), "L").save(path)
    return path


def _jpeg(size=(60, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (120, 80, 40)).save(buf, "JPEG")
    return buf.getvalue()


class _Response:
    """A requests response (both the read-at-once and the streamed API), counting the body bytes read."""

    def __init__(self, body: bytes):
        self.body, self.headers, self.status_code, self.read = body, {}, 200, 0

    def raise_for_status(self):
        pass

    def json(self):
        return {}

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self.body), chunk_size):
            self.read += len(self.body[i:i + chunk_size])
            yield self.body[i:i + chunk_size]

    @property
    def content(self):   # read once and kept, as requests does
        if not hasattr(self, "_content"):
            self._content = b"".join(self.iter_content(1 << 20))
        return self._content

    def close(self):
        pass


def _mock_get(commons, resp, calls):
    from src import url_guard

    url_guard._addresses = lambda host: ["1.1.1.1"]
    def get(url, params=None, headers=None, timeout=None, **k):
        calls.append(dict(k))
        return resp
    commons.requests.get = get
    commons.time.sleep = lambda s: None


def pixel_limit(tmp: Path) -> dict:
    from scripts import fetch_relief
    from src import commons, geo, graphics, upscale
    out = {}

    geo.RELIEF_FILE = _relief_tif(tmp / "SR_HR.tif", (200, 120))
    graphics._relief.cache_clear()
    with _Spy() as spy:
        try:
            relief = graphics._relief()
            err = None if relief is not None and relief.size == (200, 120) else f"unexpected relief {relief}"
        except RECORDED as e:   # recorded, not hidden
            err = repr(e)
    out["graphics._relief"] = spy.summary(err)
    graphics._relief.cache_clear()

    src = tmp / "still.png"
    Image.new("RGB", (40, 50), (10, 20, 30)).save(src)
    binary = tmp / "realesrgan-ncnn-vulkan.exe"
    binary.write_bytes(b"")
    upscale.REALESRGAN_BIN = str(binary)
    real_run = upscale.subprocess.run

    def fake_run(args, **k):   # the binary's 4x output, made here
        with Image.open(args[args.index("-i") + 1]) as im:
            im.resize((im.width * 4, im.height * 4)).save(args[args.index("-o") + 1])
    upscale.subprocess.run = fake_run
    with _Spy() as spy:
        try:
            up = upscale._upscale_ncnn(src, tmp / "up.png")
            err = None if up.size == (160, 200) else f"unexpected size {up.size}"
        except RECORDED as e:
            err = repr(e)
    upscale.subprocess.run = real_run
    out["upscale._upscale_ncnn"] = spy.summary(err)

    tif = _relief_tif(tmp / "SR_50M.tif", (150, 90))
    with _Spy() as spy:
        try:
            err = None if fetch_relief.valid_relief(tif, (150, 90)) else "valid_relief refused it"
        except RECORDED as e:
            err = repr(e)
    out["fetch_relief.valid_relief"] = spy.summary(err)

    calls = []
    _mock_get(commons, _Response(_jpeg()), calls)
    with _Spy() as spy:
        try:
            commons.download_jpeg("https://upload.wikimedia.org/a.jpg", tmp / "a.jpg")
            err = None
        except RECORDED as e:
            err = repr(e)
    out["commons.download_jpeg"] = spy.summary(err)
    paths = [v for v in out.values() if isinstance(v, dict)]
    out["decodes_under_lifted_limit_total"] = sum(v["decodes_under_lifted_limit"] for v in paths)
    out["decodes_under_changed_limit_total"] = sum(v["decodes_under_changed_limit"] for v in paths)
    return out


def byte_cap(tmp: Path) -> dict:
    from src import commons, config
    body = _jpeg()
    body += b"\0" * (120 * 1024 * 1024 - len(body))
    resp, calls = _Response(body), []
    _mock_get(commons, resp, calls)
    dest = tmp / "big.jpg"
    try:
        commons.download_jpeg("https://upload.wikimedia.org/big.jpg", dest)
        outcome = "kept"
    except ValueError as e:
        outcome = f"refused: {e}"
    except RECORDED as e:
        outcome = f"error: {e!r}"
    return {"body_bytes": len(body), "bytes_read": resp.read, "outcome": outcome, "saved": dest.exists(),
            "streamed": bool(calls) and all(call.get("stream") is True for call in calls),
            "redirects_disabled": bool(calls) and all(call.get("allow_redirects") is False for call in calls),
            "cap_mb_setting": getattr(config, "COMMONS_DOWNLOAD_MAX_MB", None)}


def _commit() -> str:
    """The measured tree's commit (the evidence names no absolute path), "+changes" when its tree differs from it."""
    git = ["git", "-C", str(ROOT)]
    sha = subprocess.run([*git, "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False)
    dirty = subprocess.run([*git, "status", "--porcelain"], capture_output=True, text=True, check=False)
    return (sha.stdout.strip() or "unknown") + ("+changes" if dirty.stdout.strip() else "")


def main() -> dict:
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        result = {"commit": _commit(), "python": sys.version.split()[0], "dependencies": dependencies(),
                  "pixel_limit": pixel_limit(tmp), "byte_cap": byte_cap(tmp), "advisories": advisories()}
    print(json.dumps(result, indent=2))
    (HERE / "measurements.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    return result


if __name__ == "__main__":
    main()
