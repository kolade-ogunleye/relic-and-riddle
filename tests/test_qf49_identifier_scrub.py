"""QF-49 regression: operator and infrastructure identifiers are kept out of the tracked tree, and the code that
writes tracked records or names the project to third parties no longer puts them back.

Fails on a9089ea (identifiers tracked in 163 files; SFX rights records and library exports written with absolute
paths; the archive.org User-Agent carried the GitHub handle; .env.example carried the live forum topic ids).
"""
import hashlib
import importlib.util
import json
import re
from pathlib import Path

from src import archive_film, audio_provenance, config, script_library

ROOT = Path(__file__).resolve().parent.parent


def _scan():
    spec = importlib.util.spec_from_file_location("_qf49_scan", ROOT / "tests" / "test_no_operator_identifiers.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_rows_grep_finds_nothing_outside_the_attribution_allowlist():
    assert _scan().offending(ROOT) == []


def test_archive_user_agent_takes_the_contact_from_settings_and_names_no_handle(monkeypatch):
    sent = {}

    class Reply:
        status_code = 200

        @staticmethod
        def json():
            return {"response": {"docs": []}}

    def fake_get(url, params=None, headers=None, timeout=None):
        sent.update(url=url, headers=headers)
        return Reply()

    monkeypatch.setattr(archive_film.requests, "get", fake_get)
    monkeypatch.setattr(archive_film, "PAUSE", 0)
    assert archive_film._get(archive_film.SEARCH, {"q": "x"}) == {"response": {"docs": []}}
    ua = sent["headers"]["User-Agent"]
    assert sent["url"] == archive_film.SEARCH and ua == archive_film.user_agent(config.BRAND_CONTACT)
    assert "github.com/" not in ua and ua.startswith("RelicAndRiddle/1.0 (archive film for history videos")
    assert archive_film.user_agent("press@example.org").endswith("; press@example.org)")
    assert archive_film.user_agent("") == "RelicAndRiddle/1.0 (archive film for history videos)"


def test_a_recorded_sound_effect_names_its_tracked_path(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    wav = tmp_path / "data" / "sfx" / "sfx_test.wav"
    wav.parent.mkdir(parents=True)
    wav.write_bytes(b"RIFF0000WAVE")
    record = audio_provenance.record_sfx(wav, "elevenlabs-sound-effects", "a test", "creator", True)
    assert record["path"] == "data/sfx/sfx_test.wav"
    assert audio_provenance.sfx_rights(wav) == record          # the gate checks the bytes, not the path


def test_the_committed_sound_effect_records_are_repo_relative_and_still_verify():
    records = sorted((ROOT / "data" / "sfx").glob("*.wav.rights.json"))
    assert len(records) == 25
    for rec in records:
        data = json.loads(rec.read_text(encoding="utf-8"))
        wav = rec.with_name(rec.name[: -len(".rights.json")])
        assert data["path"] == f"data/sfx/{wav.name}", rec.name
        assert data["sha256"] == hashlib.sha256(wav.read_bytes()).hexdigest(), rec.name


def test_a_library_export_records_the_reference_file_repo_relative(tmp_path):
    sheet = tmp_path / "sheet.json"
    sheet.write_text('{"facts": []}\n', encoding="utf-8", newline="\n")
    reference = config.BASE_DIR / "data" / "reference_facts.json"
    script = {"guardrail_passed": True, "narration_script": "x",
              "fact_review": {"sheet_sha256": hashlib.sha256(sheet.read_bytes()).hexdigest(),
                              "sheet_path": str(sheet), "reference_path": str(reference.resolve())}}
    script_library.export("topic_x", sheet, script, {"topic_id": "topic_x"}, library=tmp_path / "lib")
    saved = json.loads((tmp_path / "lib" / "topic_x" / "verified_script.json").read_text(encoding="utf-8"))
    assert saved["fact_review"]["reference_path"] == "data/reference_facts.json"
    committed = json.loads((ROOT / "data/script_library/wieliczka_salt_mine/verified_script.json").read_text("utf-8"))
    assert committed["fact_review"]["reference_path"] == "data/reference_facts.json"


def test_the_env_example_carries_placeholders_for_the_forum_topics():
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert re.findall(r"^\s*#?\s*TELEGRAM_TOPIC_[A-Z]+=\d", example, re.MULTILINE) == []
    for name in ("STUDIO", "NEEDS", "REVIEWS", "RUNS", "INFO"):
        assert re.search(rf"^# TELEGRAM_TOPIC_{name}=your_{name.lower()}_topic_id$", example, re.MULTILINE)
    assert re.search(r"^BRAND_CONTACT=$", example, re.MULTILINE)
