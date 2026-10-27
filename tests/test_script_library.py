"""
tests/test_script_library.py
============================
Step-1 work already paid for is reused, not bought again (operator, 2026-10-27): the script library carries
development's fact sheets and checked scripts to production; it never overrides an environment's own work, a
script is used only against the very research it was checked with and only if it passes today's checks, and a
run can start afresh from any step when a test needs that.
"""

import json
from argparse import Namespace

import pytest

import main
from src import fact_sheet, script_library
from src.credits import sha256_file


@pytest.fixture(autouse=True)
def _library(tmp_path, monkeypatch):
    monkeypatch.setattr(script_library, "LIBRARY_DIR", tmp_path / "library")
    monkeypatch.setattr(script_library, "FRESH_FROM", None)
    monkeypatch.setattr(fact_sheet, "SHEETS_DIR", tmp_path / "sheets")
    (tmp_path / "sheets").mkdir()


def _entry(tmp_path, tid="t1", facts=("a fact",), crlf=True):
    """A development sheet (CRLF, as written on Windows) and a script checked against it, exported."""
    dev = tmp_path / "dev_sheet.json"
    text = json.dumps({"topic_id": tid, "facts": list(facts)}, indent=1)
    dev.write_bytes(text.replace("\n", "\r\n" if crlf else "\n").encode("utf-8"))
    script = {"guardrail_passed": True, "narration_script": "Words.",
              "fact_review": {"sheet_sha256": sha256_file(dev), "sheet_path": str(dev)}}
    script_library.export(tid, dev, script, {"topic_id": tid, "run_id": "run_dev"})
    return dev, script


def test_reuses_follows_the_step_order():
    assert all(script_library.reuses(s) for s in script_library.STEPS)
    script_library.set_fresh_from("voice")
    assert [s for s in script_library.STEPS if script_library.reuses(s)] == ["facts", "script"]
    with pytest.raises(ValueError):
        script_library.set_fresh_from("pictures")


def test_export_keeps_the_sheet_as_the_repository_does_and_refuses_an_unmatched_script(tmp_path):
    _entry(tmp_path)
    out = script_library.LIBRARY_DIR / "t1"
    assert b"\r\n" not in (out / "fact_sheet.json").read_bytes()
    saved = json.loads((out / "verified_script.json").read_text(encoding="utf-8"))
    assert saved["fact_review"]["sheet_sha256"] == sha256_file(out / "fact_sheet.json")
    other = tmp_path / "other.json"
    other.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        script_library.export("t2", other, {"fact_review": {"sheet_sha256": "nope"}}, {})


def test_a_missing_fact_sheet_is_seeded_but_an_existing_one_never_overwritten(tmp_path):
    _entry(tmp_path)
    sheet = fact_sheet.path_for("t1")
    assert script_library.seed_fact_sheet("t1", sheet) and sheet.is_file()
    sheet.write_text('{"own": true}', encoding="utf-8")
    assert not script_library.seed_fact_sheet("t1", sheet)
    assert json.loads(sheet.read_text(encoding="utf-8")) == {"own": True}   # the environment's own work stays


def test_fresh_from_facts_seeds_nothing(tmp_path):
    _entry(tmp_path)
    script_library.set_fresh_from("facts")
    assert not script_library.seed_fact_sheet("t1", fact_sheet.path_for("t1"))


def test_load_takes_the_library_sheet_when_the_topic_has_none(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    dev = tmp_path / "dev.json"
    dev.write_text(json.dumps({"topic_id": "t1", "rules_version": fact_sheet.RULES_VERSION, "facts": [],
                               "built_at": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
    script_library.export("t1", dev, None, {"topic_id": "t1"})
    assert fact_sheet.load("t1") is not None and fact_sheet.path_for("t1").is_file()
    assert fact_sheet.load("t_unknown") is None


def test_the_library_script_is_used_against_the_same_research_with_other_line_endings(tmp_path):
    dev, _ = _entry(tmp_path)
    local = fact_sheet.path_for("t1")
    local.write_bytes(dev.read_bytes().replace(b"\r\n", b"\n"))   # what a checkout of the release gives
    kept = script_library.kept_script("t1", local)
    assert kept is not None
    assert kept["fact_review"]["sheet_path"] == str(local.resolve())
    assert kept["fact_review"]["sheet_sha256"] == sha256_file(local)
    assert kept["from_library"]["run_id"] == "run_dev"


def test_the_library_script_is_not_used_against_other_research(tmp_path):
    _entry(tmp_path)
    local = fact_sheet.path_for("t1")
    local.write_text(json.dumps({"topic_id": "t1", "facts": ["rebuilt, different"]}), encoding="utf-8")
    assert script_library.kept_script("t1", local) is None


def test_fresh_from_script_uses_no_library_script(tmp_path):
    dev, _ = _entry(tmp_path)
    local = fact_sheet.path_for("t1")
    local.write_bytes(dev.read_bytes())
    script_library.set_fresh_from("script")
    assert script_library.kept_script("t1", local) is None


def _coordinator(db):
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.db = db
    c.reuse_from = None
    return c


def test_reuse_from_keeps_the_runs_passed_script_and_fresh_from_overrides_it(db, tmp_path, monkeypatch):
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(main.PipelineCoordinator, "_passes_today", staticmethod(lambda saved, origin: True))
    (tmp_path / "run_old").mkdir()
    (tmp_path / "run_old" / "verified_script.json").write_text(json.dumps(
        {"guardrail_passed": True, "narration_script": "Words."}), encoding="utf-8")
    c = _coordinator(db)
    assert c._script_from_previous({}) is None            # an ordinary run reuses no other run's script
    c.reuse_from = "run_old"
    assert c._script_from_previous({})["narration_script"] == "Words."
    script_library.set_fresh_from("script")
    assert c._script_from_previous({}) is None
    assert c._previous_motion_dirs() == []


def test_a_regeneration_about_the_words_still_rewrites_with_reuse_from_set(db, tmp_path, monkeypatch):
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(main.PipelineCoordinator, "_passes_today", staticmethod(lambda saved, origin: True))
    (tmp_path / "run_old").mkdir()
    (tmp_path / "run_old" / "verified_script.json").write_text(json.dumps(
        {"guardrail_passed": True, "narration_script": "Words."}), encoding="utf-8")
    c = _coordinator(db)
    c.reuse_from = "run_old"
    assert c._script_from_previous({"reason": "facts", "from_run": "run_old"}) is None


def test_reuse_from_takes_the_runs_topic_and_refuses_another(db, tmp_path, monkeypatch):
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)
    topic = db.list_all_topics()[0]["id"]
    db.create_run("run_old", topic)
    (tmp_path / "run_old").mkdir()
    c = _coordinator(db)
    args = Namespace(reuse_from="run_old", fresh_from="voice", topic_id=None)
    assert main.apply_reuse_options(c, args) is None
    assert (args.topic_id, c.reuse_from, script_library.FRESH_FROM) == (topic, "run_old", "voice")
    assert "not other_topic" in main.apply_reuse_options(c, Namespace(reuse_from="run_old", fresh_from=None,
                                                                         topic_id="other_topic"))
    assert "not recorded" in main.apply_reuse_options(c, Namespace(reuse_from="run_gone", fresh_from=None, topic_id=None))


@pytest.mark.parametrize("command", ["run", "test"])
def test_the_command_line_takes_both_options(command, capsys):
    with pytest.raises(SystemExit) as stop:
        main.main([command, "--fresh-from", "pictures"])
    assert stop.value.code == 2 and "invalid choice" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main.main([command, "--help"])
    shown = capsys.readouterr().out
    assert "--reuse-from" in shown and "--fresh-from" in shown


def test_every_shipped_entry_still_matches_its_research_and_passes_todays_checks(monkeypatch):
    """A release never ships a library script that would be refused (or one checked against other research)."""
    monkeypatch.setattr(script_library, "LIBRARY_DIR", main.config.DATA_DIR / "script_library")
    entries = sorted(p for p in script_library.LIBRARY_DIR.glob("*") if p.is_dir())
    for entry in entries:
        source = json.loads((entry / "source.json").read_text(encoding="utf-8"))
        assert source["topic_id"] == entry.name
        assert b"\r\n" not in (entry / "fact_sheet.json").read_bytes()
        if (entry / "verified_script.json").is_file():
            kept = script_library.kept_script(entry.name, entry / "fact_sheet.json")
            assert kept is not None, entry.name
            assert main.PipelineCoordinator._passes_today(kept, entry.name), entry.name
            from src.fact_evidence import current_fact_review
            assert current_fact_review(kept), entry.name   # no new fact-check spend where the rules are unchanged


def test_render_fresh_and_reuse_start_a_run_with_those_options(db):
    from src.control import make_command_handler
    from src.telegram_commands import normalise_command as route
    db.create_run("run_old", db.list_all_topics()[0]["id"])
    started = []
    handle = make_command_handler(db, lambda: None, lambda content_format="reel", extra=None: started.append(extra) or "🎬 Starting")
    key = str(db.run_key("run_old"))
    assert handle(*route("render", f"fresh voice reuse {key}")) == "🎬 Starting"
    assert started == [["--fresh-from", "voice", "--reuse-from", "run_old"]]
    for wrong in ("fresh pictures", "reuse 99999", "fresh"):
        reply = handle(*route("render", wrong))
        assert reply.startswith("⚠️") and started == [["--fresh-from", "voice", "--reuse-from", "run_old"]]


def test_a_review_is_current_whatever_line_endings_the_checkout_gave_its_files(tmp_path, monkeypatch):
    """Production's reference_facts.json still had CRLF (written before the repository kept LF), so Wieliczka's
    library review never counted as current and rc57 and rc58 were rolled back (2026-10-27)."""
    from src import fact_evidence
    from src.fact_evidence import current_fact_review, record_fact_review
    sheet, reference = tmp_path / "sheet.json", tmp_path / "reference.json"
    sheet.write_bytes(b'{\n "facts": []\n}\n')
    reference.write_bytes(b'{\n "a": 1\n}\n')
    monkeypatch.setattr(fact_evidence, "REFERENCE_FILE", reference)
    monkeypatch.setattr(fact_evidence, "selected_reference", lambda scale: True)
    payload = json.loads((main.config.DATA_DIR / "script_library" / "wieliczka_salt_mine" / "verified_script.json")
                         .read_text(encoding="utf-8"))
    record_fact_review(payload, sheet)
    assert current_fact_review(payload)
    for f in (sheet, reference):            # the same content with Windows line endings
        f.write_bytes(f.read_bytes().replace(b"\n", b"\r\n"))
    assert current_fact_review(payload)
    record_fact_review(payload, sheet)      # recorded on the CRLF copies, checked on LF ones
    for f in (sheet, reference):
        f.write_bytes(f.read_bytes().replace(b"\r\n", b"\n"))
    assert current_fact_review(payload)
    reference.write_bytes(b'{\n "a": 2\n}\n')   # other content is still not current
    assert not current_fact_review(payload)


def test_every_shipped_entry_passes_with_a_crlf_checkout(tmp_path, monkeypatch):
    """The shipped-entry check as production ran it: its reference file with Windows line endings."""
    from src import fact_evidence
    crlf = tmp_path / "reference_facts.json"
    crlf.write_bytes(fact_evidence.REFERENCE_FILE.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    monkeypatch.setattr(fact_evidence, "REFERENCE_FILE", crlf)
    test_every_shipped_entry_still_matches_its_research_and_passes_todays_checks(monkeypatch)
