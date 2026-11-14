"""QF-116: CI renders with the FFmpeg release development and production run (8.1), not the runner's apt ffmpeg
(8.0.1 on the ubuntu-26.04 image at the base commit). The workflow installs a static 8.1 build pinned by URL and
SHA-256, verifies the hash before unpacking, and scripts/check_ffmpeg.py fails the run when the binary on PATH is
another release, lacks a library the captions/encodes need, or lacks a filter, encoder or format src/ uses."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"


def _check_ffmpeg():
    # Import at the assertion so the base commit (no script) fails each test instead of the whole module.
    from scripts import check_ffmpeg
    return check_ffmpeg


def test_ci_installs_the_pinned_release_instead_of_apt_ffmpeg():
    cf = _check_ffmpeg()
    text = WORKFLOW.read_text(encoding="utf-8")
    apt = [line for line in text.splitlines() if "apt-get install" in line]
    assert apt and not any(re.search(r"\bffmpeg\b", line) for line in apt)
    url = re.search(r"^\s*FFMPEG_URL: (\S+)$", text, re.MULTILINE).group(1)
    release = f"{cf.RELEASE[0]}.{cf.RELEASE[1]}"
    # A fixed release tag (never "latest") whose asset names the dev/prod major.minor.
    assert re.fullmatch(r"https://github\.com/BtbN/FFmpeg-Builds/releases/download/autobuild-[\d-]+/"
                        rf"ffmpeg-n{re.escape(release)}(?:-[\w]+)*-linux64-gpl-{re.escape(release)}\.tar\.xz", url), url
    assert re.search(r"^\s*FFMPEG_SHA256: [0-9a-f]{64}$", text, re.MULTILINE)
    verify = text.index('sha256sum --check --strict')
    assert text.index('curl -fsSL') < verify < text.index("tar -xJf")
    assert text.index("python scripts/check_ffmpeg.py") < text.index("python -m pytest")
    assert text.index('"$GITHUB_PATH"') < text.index("python scripts/check_ffmpeg.py")


def test_ffmpeg_on_path_is_the_dev_prod_release_with_what_src_uses():
    cf = _check_ffmpeg()
    assert cf.check(ROOT) == []


def _fake(version_line, config, filters, encoders, formats):
    def run(args):
        if args[-1] == "-version":
            return f"{version_line.replace('ffmpeg', args[0], 1)}\nconfiguration: {config}\n"
        kind = args[-1].lstrip("-")
        names = {"filters": filters, "encoders": encoders, "formats": formats}[kind]
        row = {"filters": " TS {} V->V x", "encoders": " V....D {} x", "formats": " DE  {} x"}[kind]
        return "header\n = legend\n  ------\n" + "\n".join(row.format(n) for n in sorted(names)) + "\n"
    return run


def test_check_fails_loudly_on_another_release_a_missing_library_or_filter(tmp_path):
    cf = _check_ffmpeg()
    full = " ".join(f"--enable-{lib}" for lib in cf.LIBRARIES)
    good = {"config": full, "filters": set(cf.FILTERS), "encoders": set(cf.ENCODERS), "formats": set(cf.FORMATS)}
    (tmp_path / "src").mkdir()
    assert cf.check(tmp_path, _fake("ffmpeg version n8.1-7-gabc Copyright", **good)) == []
    assert cf.check(tmp_path, _fake("ffmpeg version 8.1-full_build-www.gyan.dev Copyright", **good)) == []
    for other in ("ffmpeg version 8.0.1-3ubuntu2 Copyright", "ffmpeg version 6.1.1-3ubuntu5 Copyright",
                  "ffmpeg version 9.0.2 Copyright", "ffmpeg version N-123456-gabc Copyright"):
        problems = cf.check(tmp_path, _fake(other, **good))
        assert any("ffmpeg:" in p and "is not the 8.1 release" in p for p in problems), other
        assert any("ffprobe:" in p and "is not the 8.1 release" in p for p in problems), other
    no_ass = dict(good, config=full.replace("--enable-libass", ""))
    assert cf.check(tmp_path, _fake("ffmpeg version n8.1", **no_ass)) == ["ffmpeg: built without --enable-libass"]
    no_subs = dict(good, filters=set(cf.FILTERS) - {"subtitles"})
    assert cf.check(tmp_path, _fake("ffmpeg version n8.1", **no_subs)) == [
        "ffmpeg: filters missing from this build: subtitles"]
    # src/ starting to use a filter, an encoder or a format the lists do not name fails until it is listed.
    (tmp_path / "src" / "m.py").write_text(
        'CMD = ["ffmpeg", "-f", "webm", "-vf", f"zoompan=z={1}", "-c:v", "libvpx-vp9"]\n'
        "# a comment naming gblur=sigma=2 is not a use\n", encoding="utf-8")
    wider = dict(good, filters=set(cf.FILTERS) | {"zoompan", "gblur"}, encoders=set(cf.ENCODERS) | {"libvpx-vp9"},
                 formats=set(cf.FORMATS) | {"webm"})
    problems = cf.check(tmp_path, _fake("ffmpeg version n8.1", **wider))
    assert len(problems) == 3 and "filters" in problems[0] and problems[0].endswith(": zoompan")
    assert "encoders" in problems[1] and problems[1].endswith(": libvpx-vp9")
    assert "formats" in problems[2] and problems[2].endswith(": webm")


def test_check_reports_a_missing_binary(tmp_path):
    cf = _check_ffmpeg()

    def run(args):
        raise FileNotFoundError(args[0])
    problems = cf.check(tmp_path, run)
    assert [p.split(":")[0] for p in problems] == ["ffmpeg", "ffprobe"] and all("not runnable" in p for p in problems)
