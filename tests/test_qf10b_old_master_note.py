"""QF-10b: the operator's old-master note ("Grandfather old masters") survives a failed picture measurement, stays as
narrow as ruled, shows on a blocked card, and new renders always carry the key that keeps them out of it."""

import json
import subprocess
from pathlib import Path

import pytest

from src import config, picture_qa, release
from tests.test_qf10_picture_gate import (
    compose_record,
    gate,
    make,
    old_report,
    pre_qf01,
)


def timed_out(monkeypatch):
    monkeypatch.setattr(config, "PICTURE_QA_TIMEOUT_BASE_S", 0.0)
    monkeypatch.setattr(config, "PICTURE_QA_TIMEOUT_PER_S", 0.0)


def normal(monkeypatch):
    monkeypatch.setattr(config, "PICTURE_QA_TIMEOUT_BASE_S", 120.0)
    monkeypatch.setattr(config, "PICTURE_QA_TIMEOUT_PER_S", 5.0)


def test_a_timeout_on_the_first_check_does_not_remove_the_note(tmp_path, monkeypatch):
    master = pre_qf01(tmp_path / "m.mp4")
    compose_record(master)
    old_report(master)
    timed_out(monkeypatch)
    report, checks = gate(master)
    # The failed check itself blocks, and says so.
    measured = checks["picture_measurement"]
    assert measured["status"] == "fail" and "did not finish" in measured["detail"]
    assert "picture_measurement" in {c["name"] for c in release.blocking_checks(report)}
    normal(monkeypatch)
    for _ in range(2):     # the next check measures and notes; the one after reuses it
        report, checks = gate(master)
        assert checks["picture_colour"]["status"] == "pass" and checks["picture_colour"]["grandfathered"]
        assert not [p for p in release.release_problems(report) if p.startswith("picture_")]


def test_a_failed_re_measure_after_a_method_change_keeps_the_note(tmp_path, monkeypatch):
    master = pre_qf01(tmp_path / "m.mp4")
    compose_record(master)
    old_report(master)
    assert gate(master)[1]["picture_colour"]["grandfathered"]
    monkeypatch.setattr(picture_qa, "METHOD_VERSION", picture_qa.METHOD_VERSION + 1)   # as QF-88 will
    timed_out(monkeypatch)
    assert gate(master)[1]["picture_measurement"]["status"] == "fail"
    normal(monkeypatch)
    assert gate(master)[1]["picture_colour"]["grandfathered"]


@pytest.mark.parametrize("case", ["never_gated", "new_render", "first_gated_by_qf10"])
def test_a_failed_check_never_makes_an_ineligible_master_eligible(tmp_path, monkeypatch, case):
    master = pre_qf01(tmp_path / "m.mp4")
    compose_record(master, **({"full_image_spans": []} if case == "new_render" else {}))
    if case == "new_render":
        old_report(master)
    if case == "first_gated_by_qf10":
        gate(master)       # the first check is QF-10's own: no note, and nothing to carry
        monkeypatch.setattr(picture_qa, "METHOD_VERSION", picture_qa.METHOD_VERSION + 1)   # measured again
    timed_out(monkeypatch)
    _, checks = gate(master)
    measured = checks["picture_measurement"]
    assert measured["status"] == "fail" and "gated_before_qf10" not in measured
    normal(monkeypatch)
    report, checks = gate(master)
    assert checks["picture_colour"]["status"] == "fail" and not checks["picture_colour"].get("grandfathered")
    assert "picture_colour" in {c["name"] for c in release.blocking_checks(report)}


def test_the_mark_is_for_these_bytes_only(tmp_path, monkeypatch):
    master = pre_qf01(tmp_path / "m.mp4")
    compose_record(master)
    old_report(master)
    timed_out(monkeypatch)
    gate(master)
    normal(monkeypatch)
    pre_qf01(master, "black")      # other bytes under the same name, with a record bound to them
    compose_record(master)
    _, checks = gate(master)
    assert checks["picture_colour"]["status"] == "fail"


def test_the_card_shows_the_note_when_another_check_blocks(tmp_path):
    from src.telegram_bot import TelegramApprovalBot
    master = pre_qf01(tmp_path / "m.mp4", "black")
    compose_record(master)
    old_report(master)
    report, _ = gate(master)
    card = TelegramApprovalBot.release_lines(report)
    assert "blocked" in card and "picture_black" in card
    assert "picture_colour (note, not blocking): Made before the colour fix" in card


def _narration(path, seconds=2.5):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000",
                    "-af", "volume=0.3", "-ac", "1", "-t", str(seconds), str(path)], check=True, timeout=60)
    return path


@pytest.mark.parametrize("profile", ["reel", "deep_dive"])
def test_every_render_writes_full_image_spans(tmp_path, profile):
    """The key is what keeps a new render out of the old-master note: written on every render, empty or not."""
    from PIL import Image

    from src.delivery_profiles import get_delivery_profile
    from src.image_presentation import designate
    from src.video_composer import VideoComposer
    plain, essential = tmp_path / "plain.png", tmp_path / "essential.png"
    Image.new("RGB", (1200, 2100), (150, 130, 100)).save(plain)
    Image.new("RGB", (800, 600), (90, 110, 140)).save(essential)
    voice = _narration(tmp_path / "vo.wav")
    words = [{"word": "Old.", "start": 0.2, "dur": 0.4}]
    spans = {}
    for name, scenes in (("plain", [plain, plain]), ("essential", [plain, essential])):
        if name == "essential":
            designate(essential, "operator: shown whole")
        out = tmp_path / f"{name}_master.mp4"
        VideoComposer().compose_video(scenes, voice, voice, words, 2.0, out, durations=[1.0, 1.0],
                                      delivery_profile=get_delivery_profile(profile))
        record = json.loads(Path(out).with_suffix(".composition.json").read_text(encoding="utf-8"))
        assert isinstance(record["full_image_spans"], list)
        spans[name] = record["full_image_spans"]
    assert spans["plain"] == []
    assert len(spans["essential"]) == 1 and spans["essential"][0][0] == pytest.approx(1.0, abs=0.2)


def test_other_wrong_tags_stay_blocked_after_a_failed_check(tmp_path, monkeypatch):
    master = make(tmp_path / "m.mp4", "colour")
    compose_record(master)
    old_report(master)
    timed_out(monkeypatch)
    gate(master)
    normal(monkeypatch)
    _, checks = gate(master)
    assert checks["picture_colour"]["status"] == "fail"     # other tags than the pre-QF-01 set: never a note


# --- Round 2: the atomic report write while another program holds the report open (Windows) ----------------------

class _Held:
    """Hold `path` open the way another program would. On Windows a Python file handle has no delete sharing, so the
    atomic rename really fails (PermissionError, WinError 5). Elsewhere a rename succeeds under an open handle,
    so the same failure is produced at os.replace while the handle is held."""

    def __init__(self, path, monkeypatch):
        import os
        import sys

        from src import atomic_files
        self.handle = open(path, "rb")   # noqa: SIM115 - held across the test on purpose
        if sys.platform != "win32":
            real = os.replace

            def replace(src, dst):
                if self.handle and not self.handle.closed and Path(dst) == Path(path):
                    raise PermissionError(5, "Access is denied", str(dst))
                return real(src, dst)
            monkeypatch.setattr(atomic_files.os, "replace", replace)

    def close(self):
        self.handle.close()


def test_a_briefly_held_report_is_saved_after_a_retry(tmp_path, monkeypatch):
    import threading
    import time
    path = tmp_path / "release_report.json"
    path.write_text('{"old": true}', encoding="utf-8")
    held = _Held(path, monkeypatch)
    threading.Timer(0.15, held.close).start()      # the other program lets go after 150 ms
    started = time.monotonic()
    release._save_report(path, {"new": True})
    assert time.monotonic() - started >= 0.1        # it waited, then saved
    assert json.loads(path.read_text(encoding="utf-8")) == {"new": True}
    assert not list(tmp_path.glob("*.tmp"))


def test_a_report_held_throughout_is_refused_in_plain_words(tmp_path, monkeypatch):
    path = tmp_path / "release_report.json"
    path.write_text('{"old": true}', encoding="utf-8")
    monkeypatch.setattr(release, "REPORT_REPLACE_TRIES", 3)
    held = _Held(path, monkeypatch)
    try:
        with pytest.raises(release.ReleaseReportLocked, match="open in another program") as caught:
            release._save_report(path, {"new": True})
        assert isinstance(caught.value, ValueError)  # post_now, the Reel retry, the CLI: "Not posted" / "Refusing"
    finally:
        held.close()
    assert json.loads(path.read_text(encoding="utf-8")) == {"old": True}   # the old report is intact
    assert not list(tmp_path.glob("*.tmp"))


def test_require_release_refuses_a_held_report_as_a_blocked_release(tmp_path, monkeypatch):
    master = make(tmp_path / "m.mp4", "good")
    gate(master)                                   # a report exists
    monkeypatch.setattr(release, "REPORT_REPLACE_TRIES", 2)
    held = _Held(tmp_path / "release_report.json", monkeypatch)
    try:
        with pytest.raises(ValueError, match="nothing was approved or posted"):
            release.require_release({"master_video_path": str(master), "script_payload": {}})
    finally:
        held.close()
    # A caller that goes on to the card holds the run with the reason (the pipeline and the copy edit).
    problems = release.release_problems(release.locked_report(release.ReleaseReportLocked("x is open")))
    assert problems == ["release_report: x is open"]


def test_the_report_retry_waits_about_1_3_s_in_all():
    # The comment, docstring, README and ledger say "about 1.3 s"; keep the constants and the text together.
    waits = sum(release.REPORT_REPLACE_FIRST_WAIT_S * 2 ** n for n in range(release.REPORT_REPLACE_TRIES - 1))
    assert 1.2 <= waits <= 1.4
