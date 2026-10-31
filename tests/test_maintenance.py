from src.maintenance import Update, UpdatePlan, _write_pins, build_plan, read_pins, report_text


def test_plan_classifies_safe_security_and_major():
    pins = {"numpy": "2.4.4", "pillow": "12.2.0", "pytest": "8.3.5", "soundfile": "0.13.1", "anthropic": "1.13.0"}
    latest = {"numpy": "2.4.6", "pillow": "12.3.0", "pytest": "9.1.1", "soundfile": "0.14.0"}
    vulns = {"pillow": {"ids": ["A", "B"], "fix": "12.3.0"}, "pytest": {"ids": ["C"], "fix": "9.0.3"}}
    plan = {u.name: u for u in build_plan(pins, latest, vulns).updates}
    assert (plan["numpy"].reason, plan["numpy"].target) == ("safe", "2.4.6")
    assert (plan["pillow"].reason, plan["pillow"].target) == ("security", "12.3.0")
    assert plan["pytest"].reason == "major"            # fix needs a new major version
    assert plan["soundfile"].reason == "major"         # 0.x minor bump may break
    assert "anthropic" not in plan                     # already current


def test_security_prefers_newest_in_same_major():
    plan = build_plan({"requests": "2.32.3"}, {"requests": "2.34.2"}, {"requests": {"ids": ["X"], "fix": "2.33.0"}})
    assert plan.updates[0].target == "2.34.2"


def test_report_and_applicable():
    plan = UpdatePlan([Update("pillow", "12.2.0", "12.3.0", "security", ["A"]), Update("pytest", "8", "9", "major")])
    assert [u.name for u in plan.applicable] == ["pillow"] and [u.name for u in plan.manual] == ["pytest"]
    text = report_text(plan)
    assert "🔴 pillow" in text and "1 advisory" in text and "manual review" in text
    assert "all pinned packages are current" in report_text(UpdatePlan([]))


def test_write_pins_only_touches_named_packages(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text("pillow==12.2.0\nrequests==2.32.3\n\n# dev\npytest==8.3.5\n", encoding="utf-8")
    _write_pins([Update("pillow", "12.2.0", "12.3.0", "security")], req)
    assert read_pins(req) == {"pillow": "12.3.0", "requests": "2.32.3", "pytest": "8.3.5"}
    assert "# dev" in req.read_text(encoding="utf-8")


def test_pins_with_environment_markers(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text('onnxruntime-directml==1.24.4; sys_platform == "win32"\nonnxruntime==1.24.4; sys_platform != "win32"\n',
                   encoding="utf-8")
    assert read_pins(req) == {"onnxruntime-directml": "1.24.4", "onnxruntime": "1.24.4"}
    _write_pins([Update("onnxruntime", "1.24.4", "1.24.5", "safe")], req)
    assert 'onnxruntime==1.24.5; sys_platform != "win32"' in req.read_text(encoding="utf-8")


def test_apply_ignores_live_run_data_and_commits_only_the_pins(monkeypatch):
    # 2026-10-24: assets/ and data/ are always modified on a working installation (runs write them), so
    # the weekly apply was blocked forever; and `commit -am` would have swept those files into the update.
    from types import SimpleNamespace
    from src import maintenance
    commands = []

    def run(args, timeout=900):
        commands.append(args)
        return SimpleNamespace(returncode=0, stdout="main" if args[:2] == ["git", "rev-parse"] else "", stderr="")
    monkeypatch.setattr(maintenance, "_run", run)
    monkeypatch.setattr(maintenance, "_write_pins", lambda updates: None)
    plan = UpdatePlan([Update("pillow", "12.2.0", "12.3.0", "security", ["A"])])
    maintenance.apply(plan)
    status = next(c for c in commands if c[:2] == ["git", "status"])
    assert status[-3:] == [".", ":!assets", ":!data"]
    commit = next(c for c in commands if c[:2] == ["git", "commit"])
    assert "-am" not in commit and commit[-2:] == ["--", str(maintenance.REQUIREMENTS)]
