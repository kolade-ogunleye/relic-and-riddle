"""QF-116 acceptance: which ffmpeg the CI workflow tests with, and how it is verified.

    python measure.py <repo root>

Reads `.github/workflows/tests.yml` at <repo root> and, when the commit has it, runs `scripts/check_ffmpeg.py`'s
check() against the ffmpeg on this machine's PATH (development: 8.1). Prints one JSON object.
"""
import importlib.util
import json
import re
import sys
from pathlib import Path


def main(root: Path) -> dict:
    text = (root / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    apt = [line for line in text.splitlines() if "apt-get install" in line]
    url = re.search(r"^\s*FFMPEG_URL: (\S+)$", text, re.MULTILINE)
    sha = re.search(r"^\s*FFMPEG_SHA256: ([0-9a-f]{64})$", text, re.MULTILINE)
    release = re.search(r"/ffmpeg-n(\d+\.\d+)", url.group(1)) if url else None
    out = {
        "ci_apt_installs_ffmpeg": any(re.search(r"\bffmpeg\b", line) for line in apt),
        "ci_pinned_url": url.group(1) if url else None,
        "ci_pinned_sha256": sha.group(1) if sha else None,
        "ci_pinned_release": release.group(1) if release else None,
        "ci_url_is_fixed_release_tag": bool(url and "/releases/download/autobuild-" in url.group(1)),
        "ci_hash_verified_before_unpack": ("sha256sum --check --strict" in text and "tar -xJf" in text
                                           and text.index("sha256sum --check --strict") < text.index("tar -xJf")),
        "ci_runs_version_and_filter_check_before_pytest": ("python scripts/check_ffmpeg.py" in text and
                                                           text.index("python scripts/check_ffmpeg.py")
                                                           < text.index("python -m pytest")),
    }
    script = root / "scripts" / "check_ffmpeg.py"
    if script.exists():
        spec = importlib.util.spec_from_file_location("check_ffmpeg_measured", script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        out["required_release"] = f"{mod.RELEASE[0]}.{mod.RELEASE[1]}"
        out["required_libraries"] = list(mod.LIBRARIES)
        out["required_filters"] = len(mod.FILTERS)
        out["required_encoders"] = list(mod.ENCODERS)
        out["required_formats"] = list(mod.FORMATS)
        out["local_check_problems"] = mod.check(root)
    return out


if __name__ == "__main__":
    print(json.dumps(main(Path(sys.argv[1]).resolve()), indent=2))
