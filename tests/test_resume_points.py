"""When a failed run is made again in place (operator, 2026-10-25): from a fact-checked script onward, saying
what it keeps; before that, the topic is rendered again. The Claude standby tries Opus twice, then Sonnet once."""
import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from src import llm
from src.resume_points import kept_phrase, reached, worth_resuming


def _run_dir(tmp_path, stages, files=()):
    d = tmp_path / "run_x"
    d.mkdir()
    (d / "checkpoints.json").write_text(json.dumps({s: {"inputs": "x", "outputs": {}} for s in stages}), encoding="utf-8")
    for f in files:
        (d / f).parent.mkdir(parents=True, exist_ok=True)
        (d / f).write_bytes(b"x")
    return d


def test_before_the_script_passed_nothing_is_worth_keeping(tmp_path):
    d = _run_dir(tmp_path, [])
    assert reached(d) == [] and not worth_resuming(d) and kept_phrase(d) == ""
    assert not worth_resuming(tmp_path / "gone")


def test_each_point_reached_is_kept_and_named(tmp_path):
    d = _run_dir(tmp_path, ["script", "voice"], ["verified_script.json", "voiceover.mp3"])
    assert reached(d) == ["script", "voiceover"] and worth_resuming(d)
    assert kept_phrase(d) == "the fact-checked script and the voiceover"            # the 2026-10-25 Antikythera run
    (d / "motion").mkdir(); (d / "motion" / "clip_01.mp4").write_bytes(b"x")
    master = d / "m.mp4"; master.write_bytes(b"x")
    assert reached(d, {"master_video_path": str(master)})[-2:] == ["pictures", "video"]


def test_a_failed_run_can_be_rerendered_from_its_script(db, tmp_path, monkeypatch):
    import main
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)
    topic = db.list_all_topics()[0]["id"]
    db.create_run("run_x", topic)
    db.update_run("run_x", "failed", error="Voiceover is 53.7s; hard ceiling is 52s.")
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.db = db
    (tmp_path / "run_x").mkdir()
    assert "before its script passed the fact check" in c.rerender_refusal("run_x")
    (tmp_path / "run_x" / "verified_script.json").write_text("{}", encoding="utf-8")
    (tmp_path / "run_x" / "checkpoints.json").write_text(json.dumps({"script": {}}), encoding="utf-8")
    assert c.rerender_refusal("run_x") is None


def _api_error(cls):
    request = httpx.Request("POST", "https://api.anthropic.com")
    if cls is anthropic.APIConnectionError:
        return cls(request=request)
    return cls("down", response=httpx.Response(529 if cls is anthropic.InternalServerError else 404, request=request), body=None)


def test_the_standby_tries_opus_twice_then_sonnet_once(monkeypatch):
    monkeypatch.setattr(llm, "model_chain", lambda models=None: ["claude-opus-5-5", "claude-sonnet-5-5"])
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    tried = []

    def always_down(model):
        tried.append(model)
        raise _api_error(anthropic.InternalServerError)
    with pytest.raises(llm.LLMError):
        llm._first_request(always_down)
    assert tried == ["claude-opus-5-5", "claude-opus-5-5", "claude-sonnet-5-5"]
    tried.clear()

    def opus_back(model):
        tried.append(model)
        if len(tried) == 1:
            raise _api_error(anthropic.APIConnectionError)
        return "ok"
    assert llm._first_request(opus_back) == ("ok", "claude-opus-5-5") and tried == ["claude-opus-5-5"] * 2
    tried.clear()

    def opus_missing(model):
        tried.append(model)
        if model == "claude-opus-5-5":
            raise _api_error(anthropic.NotFoundError)
        return "ok"
    assert llm._first_request(opus_missing) == ("ok", "claude-sonnet-5-5") and tried == ["claude-opus-5-5", "claude-sonnet-5-5"]


def test_a_resumed_dry_run_continues_live_in_production_only(db, monkeypatch):
    # Operator, 2026-10-25: "there's nothing like dry run on production"; the re-render card still said Dry run.
    import main
    from src import config
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.db = db
    db.set_setting("run_mode:run_x", "dry_run")
    monkeypatch.setattr(config, "BACKUP_ENVIRONMENT", "development")
    assert c.resume_mode("run_x", False) is True                                    # development: as it was made
    monkeypatch.setattr(config, "BACKUP_ENVIRONMENT", "production")
    assert c.resume_mode("run_x", False) is False and db.get_setting("run_mode:run_x") == "live"
    assert c.resume_mode("run_x", True) is True                                     # an explicit dry run stays one


def test_other_with_words_about_the_script_revises_it():
    # Operator, 2026-10-25: "Incomplete text on a Story image and repeated phrases in the voiceover" under
    # Other kept the script word for word, repeated hedge included.
    import main
    assert main.revises_script({"reason": "other", "text": "Incomplete text on a Story image and repeated phrases in the voiceover"})
    assert not main.revises_script({"reason": "other", "text": "the map is too dark"})
    assert not main.revises_script({"reason": "pictures", "text": "wrong temple, repeated photo"})
    assert not main.revises_script({"reason": "other"})


def test_a_kept_script_must_pass_todays_checks(db, tmp_path, monkeypatch, curated_payload):
    import main
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.db = db
    prev = tmp_path / "run_prev"
    prev.mkdir()
    saved = dict(curated_payload, guardrail_passed=True)
    (prev / "verified_script.json").write_text(json.dumps(saved), encoding="utf-8")
    monkeypatch.setattr(c, "_previous_run_dir", lambda: prev, raising=False)
    from src.script_engine import validate_structure
    from src.models import ScriptPackage
    assert not validate_structure(ScriptPackage.model_validate(saved))              # the fixture passes today
    assert c._script_from_previous({"reason": "pictures", "from_run": "run_prev"}) == saved
    words = saved["narration_script"].split(". ")
    saved["narration_script"] = ". ".join(["By one estimate, " + w for w in words[:2]] + words[2:])
    (prev / "verified_script.json").write_text(json.dumps(saved), encoding="utf-8")
    assert c._script_from_previous({"reason": "pictures", "from_run": "run_prev"}) is None   # repeats now: rewritten
