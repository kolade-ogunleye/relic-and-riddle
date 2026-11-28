"""Freeze c6 capgate (2026-11-28; audit of dev run 1795857682, items 12-14), ruling 4: what the run reports around the
caption gate. (a) A blocked run's console line counted no Story frames although two were on disk; (b) the log's
"Quality audit 100/100" covered only the timeline audit and was printed before the contrast audit failed; (c) a
visual-check finding that contradicts itself ("'DEDICATED' appears as 'DEDICATED'") was carried into the block
reason. Also: the caption gate's fade dips are a note in the release report (ruling 2)."""
import logging

from src import config, release, video_qa
from src.credits import sha256_file


def test_the_outputs_line_reports_what_is_on_disk_for_a_blocked_run(tmp_path):
    import main
    (tmp_path / "stories").mkdir()
    for name in ("story_01_hook.jpg", "story_02_reveal.jpg"):
        (tmp_path / "stories" / name).write_bytes(b"jpg")
    (tmp_path / "carousel_plan.json").write_text("{}", encoding="utf-8")
    res = {"status": "blocked", "error": "Release blocked", "master_video": str(tmp_path / "x_master.mp4")}
    line = main.run_outputs_line(res)
    assert "Story frames on disk: 2" in line and "carousel not drawn" in line and "posting slot" in line
    assert "Stories: 0" not in line
    (tmp_path / "carousel").mkdir()
    for k in (1, 2, 3):
        (tmp_path / "carousel" / f"carousel_slide_{k}.jpg").write_bytes(b"jpg")
    assert main.run_outputs_line(res) == "Story frames on disk: 2, carousel slides: 3"


def test_the_timeline_audit_line_is_named_for_what_it_covers(tmp_path, monkeypatch, caplog):
    auditor = video_qa.QualityAuditor()
    monkeypatch.setattr(auditor, "audit_rendered_timeline",
                        lambda *a, **k: {"score": 100, "certified": True, "issues": []})
    monkeypatch.setattr(video_qa, "text_contrast_audit", lambda *a, **k: {
        "issues": [{"type": "text_contrast", "severity": "major", "issue": "captions: 2.00:1"}],
        "captions": None, "score": 90, "certified": False})
    master = tmp_path / "m_master.mp4"
    master.write_bytes(b"video")
    with caplog.at_level(logging.INFO, logger=video_qa.log.name):
        out = auditor.certify(master, [], [], tmp_path)
    assert out["certified"] is False
    lines = [r.getMessage() for r in caplog.records]
    assert not any(m.startswith("Quality audit: score=100") for m in lines)
    assert any(m.startswith("Timeline audit (the master's timeline only") and "score=100/100" in m for m in lines)
    assert any(m.startswith("Quality audit (every surface") and "certified=False" in m for m in lines)


DEDICATED = ("The caption says 'DEDICATED IN AD 128', but the text 'DEDICATED' appears as 'DEDICATED' while the "
             "narration reads 'dedicated in AD 128' (the caption card text has 'DEDICATED' spelled correctly).")


def test_a_finding_the_checker_contradicts_is_dropped_and_counted():
    assert release.checker_false_positive({"severity": "minor", "issue": DEDICATED})
    assert not release.checker_false_positive({"issue": "the text 'DEDICATED' appears as 'DEDICTED' in beat 2"})
    assert not release.checker_false_positive({"issue": "a modern car under 'ancient surveyors'"})
    assert release.checker_false_positive({"issue": "the label ‘ROME’ is shown as ‘ROME’"})


def _run(master, payload):
    return {"run_id": "run_c", "topic_id": "t", "master_video_path": str(master), "script_payload": payload,
            "thumbnail_cover_path": "", "thumbnail_grid_path": "", "status": "pending_approval"}


def test_the_release_report_drops_the_contradicted_finding_and_notes_the_fade_dips(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MINOR_FINDINGS_BLOCK_RELEASE", True)
    master = tmp_path / "x_master.mp4"
    master.write_bytes(b"video")
    dips = [{"t": 48.2, "contrast": 2.22, "frames": 1, "from": 48.2, "burst": 33, "text": "a timeless / question"}]
    quality = {"master_sha256": sha256_file(master), "issues": [],
               "atomic_breakdown": {"text_contrast": {"captions": {"dips": dips, "allowance_frames": 2, "min": 7.29}}}}
    look = {"run": True, "issues": [{"beat": 2, "severity": "minor", "issue": DEDICATED, "where": "beat 2, 0:04-0:09"}]}
    report = release.write_release_report(_run(master, {"self_healing": quality, "video_check": look}), dry_run=True)
    visual = next(c for c in report["checks"] if c["name"] == "visual_checks")
    assert visual["status"] == "pass"
    assert visual["checker_false_positives"]["count"] == 1
    assert not any("DEDICATED" in p for p in release.release_problems(report, dry_run=True))
    fades = next(c for c in report["checks"] if c["name"] == "caption_fades")
    assert fades["status"] == "pass" and fades["note"] and "2.22:1 at 48.20s" in fades["detail"]


def test_the_test_command_prints_what_a_blocked_run_left_on_disk(tmp_path, monkeypatch, capsys):
    import main
    (tmp_path / "stories").mkdir()
    for name in ("story_01_hook.jpg", "story_02_reveal.jpg"):
        (tmp_path / "stories" / name).write_bytes(b"jpg")
    (tmp_path / "carousel_plan.json").write_text("{}", encoding="utf-8")

    class Blocked:   # run_cycle's result on a gate block: no "stories" or "carousel" keys
        def run_cycle(self, *a, **k):
            return {"run_id": "r", "topic_id": "t", "decision": None, "status": "blocked", "error": "Release blocked",
                    "master_video": str(tmp_path / "x_master.mp4")}
    monkeypatch.setattr(main, "PipelineCoordinator", Blocked)
    assert main.main(["test"]) == 1
    out = capsys.readouterr().out
    assert "Story frames on disk: 2" in out and "carousel not drawn" in out and "Stories: 0" not in out


# ---------------------------------------------------------------- lander ruling 2 (follow-up): fail closed
CAPS = ("[Script Info]\nPlayResX: 1080\nPlayResY: 1920\n\n[Events]\n"
        "Dialogue: 0,0:00:01.00,0:00:02.00,CapBox,,0,0,0,,{\\pos(300,1100)\\p1}m 0 0 l 400 0{\\p0}\n"
        "Dialogue: 1,0:00:01.00,0:00:02.00,CapText,,0,0,0,,{\\pos(540,1150)\\q2}STONE\n")


def _captioned(tmp_path, video=True):
    ass = tmp_path / "x_master_captions.ass"
    ass.write_text(CAPS, encoding="utf-8")
    master = tmp_path / "x_master.mp4"
    if video:
        master.write_bytes(b"video")
    return master, ass


def _blocking(audit):
    return [i["issue"] for i in audit["issues"] if i["severity"] == "major"]


def test_a_caption_measure_that_errors_fails_closed(tmp_path, monkeypatch):
    master, ass = _captioned(tmp_path)

    def broken(*a, **k):
        raise RuntimeError("ffmpeg exited 1")
    monkeypatch.setattr(video_qa, "caption_contrast", broken)
    audit = video_qa.text_contrast_audit(tmp_path, master, ass)
    assert audit["certified"] is False
    assert _blocking(audit) == ["caption contrast could not be measured (RuntimeError: ffmpeg exited 1)"]


def test_a_caption_measure_that_returns_nothing_fails_closed(tmp_path, monkeypatch):
    master, ass = _captioned(tmp_path)
    for result, why in (({"samples": [], "min": None, "bursts": 0, "under": [], "dips": []}, "no caption burst"),
                        ({"samples": [{"t": 1.5, "contrast": None}], "min": None, "bursts": 1, "under": [],
                          "dips": []}, "no measurable glyphs"),
                        ({"samples": [], "min": None, "bursts": 1, "under": [], "dips": []}, "no frame on the master")):
        monkeypatch.setattr(video_qa, "caption_contrast", lambda *a, _r=result, **k: _r)
        audit = video_qa.text_contrast_audit(tmp_path, master, ass)
        assert audit["certified"] is False and why in _blocking(audit)[0], (why, audit["issues"])
    master.unlink()   # captions with no master to read them on
    audit = video_qa.text_contrast_audit(tmp_path, master, ass)
    assert audit["certified"] is False and "could not be measured" in _blocking(audit)[0]


def test_a_master_without_captions_is_not_failed(tmp_path):
    master, ass = _captioned(tmp_path)
    ass.write_text("[Events]\n", encoding="utf-8")
    assert video_qa.text_contrast_audit(tmp_path, master, ass)["certified"] is True
    ass.unlink()
    assert video_qa.text_contrast_audit(tmp_path, master, ass)["certified"] is True


def test_a_contrast_audit_that_crashes_fails_the_quality_audit(tmp_path, monkeypatch):
    auditor = video_qa.QualityAuditor()
    monkeypatch.setattr(auditor, "audit_rendered_timeline",
                        lambda *a, **k: {"score": 100, "certified": True, "issues": []})

    def crash(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(video_qa, "text_contrast_audit", crash)
    master = tmp_path / "m_master.mp4"
    master.write_bytes(b"video")
    out = auditor.certify(master, [], [], tmp_path)
    assert out["certified"] is False
    assert [i["severity"] for i in out["issues"]] == ["major"]
    assert out["issues"][0]["issue"] == "caption contrast could not be measured (OSError: disk full)"


def test_the_release_check_fails_when_the_captions_could_not_be_measured(tmp_path, monkeypatch):
    master, ass = _captioned(tmp_path)

    def broken(*a, **k):
        raise RuntimeError("no frames decoded")
    monkeypatch.setattr(video_qa, "caption_contrast", broken)
    audit = video_qa.text_contrast_audit(tmp_path, master, ass)
    quality = {"master_sha256": sha256_file(master), "issues": audit["issues"]}
    report = release.write_release_report(_run(master, {"self_healing": quality, "video_check": {"issues": []}}),
                                          dry_run=True)
    visual = next(c for c in report["checks"] if c["name"] == "visual_checks")
    assert visual["status"] == "fail"
    assert any("caption contrast could not be measured (RuntimeError: no frames decoded)" in p
               for p in release.release_problems(report, dry_run=True))
