import json

from scripts import runtime_probe


def test_acceptance_probe_rejects_desktop_session_before_loading_app_or_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime_probe, "process_identity", lambda: ("LAB\\rr-pipeline$", 2))
    report = tmp_path / "evidence" / "probe.json"
    assert runtime_probe.main(["--app-root", str(tmp_path / "missing-app"), "--report", str(report)]) == 2
    data = json.loads(report.read_text())
    assert data["passed"] is False and data["session_id"] == 2
    assert data["checks"] == {}
    assert not (tmp_path / "missing-app").exists()


def test_acceptance_probe_refuses_to_replace_prior_evidence(tmp_path):
    import pytest
    report = tmp_path / "report.json"
    report.write_text("preserve evidence")
    with pytest.raises(SystemExit):
        runtime_probe.main(["--app-root", str(tmp_path), "--report", str(report)])
    assert report.read_text() == "preserve evidence"
