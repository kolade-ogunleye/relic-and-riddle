"""QF-79: the evaluation harness base (scripts/eval_harness.py) on eval_prompts.py, mocked: no engine, no network."""
import json
import socket

import pytest

from scripts import eval_harness as eh
from scripts import eval_prompts as ep
from scripts.prompt_variants import VARIANTS
from src import script_engine


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("the harness opened a network connection")
    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def stub_draft(seen):
    def draft(topic):
        seen.append((topic["id"], script_engine.SYSTEM_PROMPT))
        good = script_engine.SYSTEM_PROMPT == VARIANTS["general"][0]
        return {"structure": 0 if good else 1, "traced": 0.9 if good else 0.6, "passed": good,
                "length_ok": True, "tropes": 0, "words": 120}
    return draft


TOPICS = [{"id": "topic_a"}, {"id": "topic_b"}]


def test_each_draft_runs_under_its_variants_prompts_and_restores_production():
    seen, before = [], script_engine.SYSTEM_PROMPT
    rows = eh.run(TOPICS, ["current", "general"], stub_draft(seen))
    assert [(r["topic"], r["variant"], r["run"]) for r in rows] == [
        ("topic_a", "current", 1), ("topic_a", "general", 1), ("topic_b", "current", 1), ("topic_b", "general", 1)]
    assert [s for _, s in seen] == [VARIANTS["current"][0], VARIANTS["general"][0]] * 2
    assert script_engine.SYSTEM_PROMPT == before


def test_an_interrupted_run_resumes_without_redrafting(tmp_path):
    results, seen = tmp_path / "evals" / "results.jsonl", []
    first = eh.run(TOPICS, ["current", "general"], stub_draft(seen), results=results, max_drafts=3)
    assert len(first) == 3 and len(results.read_text(encoding="utf-8").splitlines()) == 3
    rows = eh.run(TOPICS, ["current", "general"], stub_draft(seen), results=results)
    assert len(seen) == 4 and len(rows) == 4
    assert [json.loads(line)["variant"] for line in results.read_text(encoding="utf-8").splitlines()] == \
        ["current", "general", "current", "general"]


def test_the_paired_comparison_agrees_with_eval_prompts_summary():
    rows = eh.run(TOPICS + [{"id": "topic_c"}], ["current", "general"], stub_draft([]), runs=2)
    rows = [r for r in rows if not (r["topic"] == "topic_c" and r["variant"] == "general")]
    wins = eh.compare(rows, "current", "general")
    assert wins == {"candidate": 2, "baseline": 0, "tie": 0}
    assert (f"general better {wins['candidate']}, current better {wins['baseline']}, tie {wins['tie']}"
            in ep.summarize(rows))


def test_a_margin_inside_the_noise_is_a_tie():
    rows = [{"topic": "t", "variant": v, "run": 1, "structure": 0, "traced": t, "passed": True,
             "length_ok": True, "tropes": 0} for v, t in (("current", 0.80), ("general", 0.81))]
    assert eh.compare(rows, "current", "general") == {"candidate": 0, "baseline": 0, "tie": 1}


def test_the_cli_only_reads_a_results_file(tmp_path, capsys):
    results = tmp_path / "r.jsonl"
    eh.run(TOPICS, ["current", "general"], stub_draft([]), results=results)
    assert eh.main([str(results)]) == 0
    out = capsys.readouterr().out
    assert "paired by topic: general better 2" in out and '"candidate": 2' in out


def test_the_cli_prints_eval_prompts_paired_line_only_for_the_pair_it_describes(tmp_path, capsys):
    results = tmp_path / "r.jsonl"
    eh.run(TOPICS, ["general", "current"], stub_draft([]), results=results)
    assert eh.main([str(results), "--baseline", "general", "--candidate", "current"]) == 0
    out = capsys.readouterr().out
    assert "paired by topic" not in out
    assert json.loads(out.strip().splitlines()[-1]) == {"pair": ["general", "current"], "candidate": 0,
                                                       "baseline": 2, "tie": 0}
