"""
scripts/check_ffmpeg.py
=======================
The ffmpeg and ffprobe on PATH must be the release development and production run (8.1, docs/deployment.md step
5), built with the libraries the renders rely on (libass captions shaped through HarfBuzz, FriBidi and FreeType with
fontconfig; libx264; libvmaf), and must offer every filter, encoder and format src/ invokes. CI runs it right after
installing the pinned static build (QF-116), so the caption, encode and picture/audio-QA tests exercise the release
that ships instead of the runner's apt ffmpeg (Ubuntu 24.04: 6.1.1). Exit status 1 lists each mismatch.

    python scripts/check_ffmpeg.py [repo root]
"""

import io
import re
import subprocess
import sys
import tokenize
from pathlib import Path

RELEASE = (8, 1)   # major.minor of development and production (docs/deployment.md, "Keep FFmpeg 8.1")

# ./configure switches the renders need: subtitles= (libass) shapes captions with HarfBuzz and FriBidi, rasterises
# with FreeType and finds fonts through fontconfig; libx264 is the CPU encoder; libvmaf is in the dev/prod build.
LIBRARIES = ("libass", "libharfbuzz", "libfribidi", "libfreetype", "fontconfig", "libx264", "libvmaf")

# Every filter src/ names in a filter graph (find_src_usage() keeps this list complete; a few are ordinary words
# that happen to be filter names in src/ strings, e.g. "copy", "null", "format": harmless, every full build has them).
FILTERS = (
    "adelay", "aeval", "afade", "aformat", "alimiter", "alphamerge", "amerge", "amix", "amultiply", "anullsink",
    "apad", "aresample", "asetpts", "asplit", "astats", "atrim", "blend", "boxblur", "color", "colorspace",
    "compand", "concat", "copy", "crop", "cue", "curves", "ebur128", "eq", "equalizer", "exposure", "fade", "field",
    "format", "fps", "geq", "highpass", "hstack", "hue", "identity", "life", "loop", "lowpass", "metadata",
    "null", "overlay", "pad", "pan", "phase", "scale", "scroll", "segment", "select", "setparams", "setpts",
    "setsar", "settb", "sidechaincompress", "signature", "silencedetect", "split", "subtitles", "thumbnail",
    "tile", "tpad", "treble", "trim", "vignette", "volume", "xfade",
)
ENCODERS = ("aac", "ffv1", "h264_nvenc", "libx264", "pcm_s16le", "pcm_s24le", "png", "rawvideo")
# "-f" formats src/ names, plus the containers its outputs use by extension (.mp4, .mkv, .wav, numbered images).
FORMATS = ("f32le", "image2", "image2pipe", "lavfi", "matroska", "mp4", "null", "rawvideo", "wav")

_VERSION = re.compile(r"^ff(?:mpeg|probe) version n?(\d+)\.(\d+)", re.MULTILINE)
_CODEC_FLAGS = {"-c:v", "-c:a", "-vcodec", "-acodec", "-c", "-codec:v", "-codec:a"}
# A filter name inside a graph string: after a graph delimiter or the string's quote, before its arguments, a
# delimiter, an "@label" or the closing quote.
_GRAPH_WORD = re.compile(r"(?:(?<=[\[\],;{}'\"]))([a-z][a-z0-9_]*)(?=[=,;\[\]@'\"])")


def _run(args: list[str]) -> str:
    res = subprocess.run(args, check=False, capture_output=True, text=True, timeout=60)
    return res.stdout + res.stderr


def parse_version(text: str) -> tuple[int, int] | None:
    m = _VERSION.search(text)
    return (int(m.group(1)), int(m.group(2))) if m else None


_ROWS = {"filters": re.compile(r"^ [.A-Z|]{2,3} +(\S+)"),
         "encoders": re.compile(r"^ [VAS][.A-Z]{5} +(\S+)"),
         "formats": re.compile(r"^ [D. ][E. ]d? *(\S+)")}


def _listed(text: str, kind: str) -> set[str]:
    """Names from `ffmpeg -filters/-encoders/-formats`: the rows below the '---' rule (a format row may name several,
    e.g. "mov,mp4,m4a")."""
    names, lines = set(), text.splitlines()
    rule = next((i for i, line in enumerate(lines) if re.fullmatch(r"\s*-{2,}\s*", line)), len(lines))
    for line in lines[rule + 1:]:
        m = _ROWS[kind].match(line)
        if m:
            names.update(m.group(1).split(","))
    return names


def binary_offers(run=_run) -> dict[str, set[str]]:
    return {kind: _listed(run(["ffmpeg", "-hide_banner", f"-{kind}"]), kind) for kind in _ROWS}


def find_src_usage(root: Path, filters: set[str], encoders: set[str], formats: set[str]) -> dict[str, set[str]]:
    """Filters named in src/ graph strings, encoders after a codec flag and formats after "-f" (string literals only,
    so comments never count). The universes are what the binary offers."""
    used = {"filters": set(), "encoders": set(), "formats": set()}
    for path in sorted((root / "src").rglob("*.py")):
        prev = None
        for tok in tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline):
            if tok.type != tokenize.STRING:
                if tok.type not in (tokenize.OP, tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT):
                    prev = None
                continue
            used["filters"].update(w for w in _GRAPH_WORD.findall(tok.string) if w in filters)
            m = re.fullmatch(r"""[rbfRBF]*(['"])(.*)\1""", tok.string, re.DOTALL)
            value = m.group(2) if m else None
            if prev in _CODEC_FLAGS and value in encoders:
                used["encoders"].add(value)
            if prev == "-f" and value in formats:
                used["formats"].add(value)
            prev = value
    return used


def check(root: Path, run=_run) -> list[str]:
    problems = []
    for tool in ("ffmpeg", "ffprobe"):
        try:
            text = run([tool, "-hide_banner", "-version"])
        except (OSError, subprocess.SubprocessError) as exc:
            problems.append(f"{tool}: not runnable ({exc})")
            continue
        version = parse_version(text)
        if version != RELEASE:
            first = text.strip().splitlines()[0] if text.strip() else "no output"
            problems.append(f"{tool}: {first!r} is not the {RELEASE[0]}.{RELEASE[1]} release dev and prod run")
        if tool == "ffmpeg":
            config = re.search(r"^configuration:(.*)$", text, re.MULTILINE)
            for lib in LIBRARIES:
                if not config or f"--enable-{lib}" not in config.group(1).split():
                    problems.append(f"ffmpeg: built without --enable-{lib}")
    if problems and any("not runnable" in p for p in problems):
        return problems
    offers = binary_offers(run)
    for kind, wanted in (("filters", FILTERS), ("encoders", ENCODERS), ("formats", FORMATS)):
        missing = sorted(set(wanted) - offers[kind])
        if missing:
            problems.append(f"ffmpeg: {kind} missing from this build: {', '.join(missing)}")
    used = find_src_usage(root, offers["filters"], offers["encoders"], offers["formats"])
    for kind, wanted in (("filters", FILTERS), ("encoders", ENCODERS), ("formats", FORMATS)):
        unlisted = sorted(used[kind] - set(wanted))
        if unlisted:
            problems.append(f"src/ uses {kind} this check does not require yet (add them to {kind.upper()}): "
                            f"{', '.join(unlisted)}")
    return problems


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent.parent
    problems = check(root)
    print(_run(["ffmpeg", "-hide_banner", "-version"]).splitlines()[0] if not any("not runnable" in p for p in problems)
          else "ffmpeg: not runnable")
    for p in problems:
        print(p, file=sys.stderr)
    if not problems:
        print(f"ffmpeg/ffprobe {RELEASE[0]}.{RELEASE[1]} with {', '.join(LIBRARIES)}; {len(FILTERS)} filters, "
              f"{len(ENCODERS)} encoders, {len(FORMATS)} formats present; src/ uses nothing else")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
