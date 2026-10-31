"""A run leaves enough evidence to be followed from start to end: its own DEBUG log and a record of
every model call (task, model, seconds, tokens, outcome) in its folder. pipeline.log keeps INFO for
every process at once, and following one run through it meant guessing (2026-10-24)."""

import json
import logging
from types import SimpleNamespace

from pydantic import BaseModel

from src import free_llm, gemini, llm, ops
from src.ops import RunLog, record_llm_call


def test_a_run_gets_its_own_debug_log_while_the_shared_handlers_keep_info(tmp_path, caplog):
    root = logging.getLogger()
    shared = logging.StreamHandler()
    root.addHandler(shared)
    before_root, before_shared = root.level, shared.level
    try:
        sink = RunLog(tmp_path / "run_x").start()
        logging.getLogger("src.something").debug("a debug line for the run")
        logging.getLogger("src.something").info("an info line")
        assert shared.level == logging.INFO and root.level == logging.DEBUG
        sink.stop()
        logging.getLogger("src.something").debug("after the run")
    finally:
        root.removeHandler(shared)
    text = (tmp_path / "run_x" / "run.log").read_text(encoding="utf-8")
    assert "a debug line for the run" in text and "an info line" in text and "after the run" not in text
    assert root.level == before_root and shared.level == before_shared


def test_model_calls_are_recorded_only_while_a_run_is_open(tmp_path):
    record_llm_call("scene_plan", "gemini-3.8-flash", 1.5)        # no run open: nothing written anywhere
    assert not list(tmp_path.glob("**/llm_calls.jsonl"))
    sink = RunLog(tmp_path / "run_x").start()
    record_llm_call("script", "gemini-3.8-flash", 12.34, 1000, 200, role="writer")
    record_llm_call("trace_check", "gemini-3.7-flash", 3.0, outcome="LLMError", role="checker")
    sink.stop()
    rows = [json.loads(ln) for ln in (tmp_path / "run_x" / "llm_calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(r["task"], r["model"], r["seconds"], r["tokens_in"], r["outcome"]) for r in rows] == [
        ("script", "gemini-3.8-flash", 12.34, 1000, "ok"), ("trace_check", "gemini-3.7-flash", 3.0, 0, "LLMError")]


class _Out(BaseModel):
    text: str


def test_a_claude_call_is_recorded_with_its_task_tokens_and_time(tmp_path, monkeypatch):
    response = SimpleNamespace(stop_reason="end_turn", parsed_output=_Out(text="ok"), content=[], model="claude-opus-5-5", _request_id="req",
                               usage=SimpleNamespace(input_tokens=321, output_tokens=45))
    monkeypatch.setattr(llm, "_client", lambda: SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=lambda **kw: response))))
    monkeypatch.setattr(llm, "model_chain", lambda models=None: ["claude-opus-5-5"])
    monkeypatch.setattr(llm, "_record", lambda r: None)
    sink = RunLog(tmp_path / "run_x").start()
    with llm.task("scene_plan"):
        assert llm.structured_call("s", "p", _Out).text == "ok"
    sink.stop()
    row = json.loads((tmp_path / "run_x" / "llm_calls.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert (row["task"], row["model"], row["tokens_in"], row["tokens_out"], row["outcome"]) == ("scene_plan", "claude-opus-5-5", 321, 45, "ok")


def test_a_web_searching_call_is_recorded_once_with_all_its_continuations(tmp_path, monkeypatch):
    # Writer trial 2026-10-24: the second opinion's search request (180-300k input tokens, the run's
    # largest cost) was absent from llm_calls.jsonl; only the structured parse after it was there.
    def usage(i, o, searches):
        return SimpleNamespace(input_tokens=i, output_tokens=o, server_tool_use=SimpleNamespace(web_search_requests=searches))
    def reply(stop, content, use):
        return SimpleNamespace(stop_reason=stop, content=content, usage=use, model="claude-fable-5-1", _request_id="req")
    paused = reply("pause_turn", [], usage(120000, 300, 4))
    final = reply("end_turn", [SimpleNamespace(type="text", text="the answer", citations=[])], usage(90000, 900, 2))
    answers = iter([paused, final])
    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: next(answers))))
    monkeypatch.setattr(llm, "_client", lambda: client)
    monkeypatch.setattr(llm, "_first_request", lambda send, models: (send("claude-fable-5-1"), "claude-fable-5-1"))
    monkeypatch.setattr(llm, "_record", lambda r: None)
    sink = RunLog(tmp_path / "run_x").start()
    with llm.task("second_check"):
        assert llm.research("s", "p", web_search_uses=5)[0] == "the answer"
    answers = iter([reply("end_turn", [], usage(10, 0, 0))])                                     # no text: an error
    try:
        llm.research_with_evidence("s", "p", web_search_uses=5)
    except llm.LLMError:
        pass
    sink.stop()
    rows = [json.loads(ln) for ln in (tmp_path / "run_x" / "llm_calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(r["task"], r["model"], r["tokens_in"], r["tokens_out"], r.get("searches"), r["outcome"]) for r in rows] == [
        ("second_check", "claude-fable-5-1", 210000, 1200, 6, "ok"), ("other", "claude-fable-5-1", 10, 0, None, "LLMError")]


def test_a_free_model_call_is_recorded_with_its_role_and_failure(tmp_path, monkeypatch):
    from src import config
    monkeypatch.setattr(config, "GEMINI_FREE_API_KEY", "free-key-123")
    monkeypatch.setattr(config, "FREE_TIER_HEAVY", True)
    monkeypatch.setattr(gemini, "structured", lambda *a, **k: _Out(text="free"))
    monkeypatch.setattr(gemini, "last_usage", lambda: (50, 7))
    sink = RunLog(tmp_path / "run_x").start()
    with llm.task("trace_check"):
        assert free_llm.structured("s", "p", _Out, role="checker").text == "free"
    monkeypatch.setattr(gemini, "structured", lambda *a, **k: (_ for _ in ()).throw(gemini.GeminiError("bad request")))
    try:
        free_llm.structured("s", "p", _Out)
    except llm.LLMError:
        pass
    sink.stop()
    rows = [json.loads(ln) for ln in (tmp_path / "run_x" / "llm_calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert (rows[0]["task"], rows[0]["role"], rows[0]["tokens_in"], rows[0]["tokens_out"], rows[0]["outcome"]) == ("trace_check", "checker", 50, 7, "ok")
    assert rows[1]["outcome"] == "LLMError" and rows[1]["role"] == "writer"


def test_a_run_opens_and_closes_its_evidence_sink(db, tmp_path, monkeypatch):
    import main
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)
    coordinator = SimpleNamespace(db=db, topic_engine=SimpleNamespace(get_topic_by_id=lambda t: {"id": t, "title": "T", "times_published": 0}, record_status=lambda *a, **k: None),
                                  progress_msg_id=None, telegram_bot=SimpleNamespace(), _alert=lambda *a, **k: None)
    filed = []
    coordinator._finish_progress = lambda *a, **k: None
    coordinator._collect_findings = lambda *a, **k: filed.append(a)
    topic = db.list_all_topics()[0]["id"]
    db.create_run("run_r", topic)
    (tmp_path / "run_r").mkdir()
    db.update_run("run_r", "failed", error="x", master_video_path=tmp_path / "run_r" / "m.mp4")

    def produce(topic, run_id, run_dir, auto_approve, dry_run, previous_status):
        logging.getLogger("src.stage").debug("inside the run")
        assert ops._run_dir_for_evidence == run_dir
        return {"status": "simulated", "run_id": run_id}
    coordinator._produce_and_publish = produce
    assert main.PipelineCoordinator.resume_run(coordinator, "run_r")["status"] == "simulated"
    assert filed and filed[0][0] == "run_r"                    # a resumed run files its findings (the self-audit) too
    assert "inside the run" in (tmp_path / "run_r" / "run.log").read_text(encoding="utf-8")
    assert ops._run_dir_for_evidence is None


def test_library_debug_noise_stays_out_of_the_run_log(tmp_path):
    pil = logging.getLogger("PIL.TiffImagePlugin")
    before = pil.level
    sink = RunLog(tmp_path / "run_x").start()
    pil.debug("tag: Orientation (274)")
    logging.getLogger("src.stage").debug("ours")
    sink.stop()
    text = (tmp_path / "run_x" / "run.log").read_text(encoding="utf-8")
    assert "ours" in text and "Orientation" not in text and pil.level == before
