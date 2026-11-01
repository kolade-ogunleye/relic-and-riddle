"""The hook learning loop (2026-11-01): how a Reel opened, how its first three seconds did, and the writer
steered towards the openings that keep viewers."""

from collections import Counter
from pathlib import Path

from src import audience, hook_learning, learning


def test_hook_forms_are_read_from_the_sentence() -> None:
    assert hook_learning.classify("How did a city of 20,000 stay hidden for centuries?") == "question"
    assert hook_learning.classify("280 feet below a Turkish village, a city lay sealed.") == "figure"
    assert hook_learning.classify("It has no foundations, yet it has stood for 900 years.") == "contradiction"
    assert hook_learning.classify("Beneath a house in Cappadocia, a city lay hidden.") == "discovery"
    assert hook_learning.classify("") is None


def test_figure_is_offered_only_when_the_topic_holds_a_number() -> None:
    assert "figure" in hook_learning.fitting({"key_facts": ["Up to 20,000 people sheltered here"]})
    assert "figure" not in hook_learning.fitting({"hook": "A city hidden underground", "key_facts": ["Carved tuff"]})


def test_the_reward_reads_only_the_first_three_seconds() -> None:
    assert hook_learning.reward({"skip_rate": 40.0}) == 0.6
    assert hook_learning.reward({"hold_3s": 0.7}) == 0.7
    assert abs(hook_learning.reward({"skip_rate": 40.0, "hold_3s": 0.8}) - 0.7) < 1e-9
    assert hook_learning.reward({"watch_ratio": 0.9, "share_rate": 0.1}) is None   # retention is not the hook


def test_after_exploration_the_stronger_opening_is_asked_for_more_often() -> None:
    topic = {"key_facts": ["280 feet deep"]}
    rows = [{"platform": "instagram", "run_id": f"q{i}", "hook_form": "question", "skip_rate": 25.0} for i in range(10)]
    for form in ("discovery", "figure", "contradiction"):
        rows += [{"platform": "instagram", "run_id": f"{form}{i}", "hook_form": form, "skip_rate": 65.0}
                 for i in range(10)]
    picks = Counter(hook_learning.choose(topic, rows, seed=f"r{i}")[0] for i in range(200))
    assert picks["question"] > 150


def test_the_steer_is_one_instruction_inside_the_existing_rules() -> None:
    note = hook_learning.steer_note("question")
    assert note.startswith("\n\nHOOK FORM") and "most surprising verified" in note
    assert hook_learning.steer_note(None) == "" and hook_learning.steer_note("unknown") == ""


def test_the_opening_shot_is_named(tmp_path: Path) -> None:
    motion = tmp_path / "motion"
    motion.mkdir()
    assert hook_learning.opening_shot([motion / "clip.mp4", tmp_path / "b.jpg"], motion) == "ai_motion"
    assert hook_learning.opening_shot([tmp_path / "footage.mp4"], motion) == "footage"
    assert hook_learning.opening_shot([tmp_path / "a.jpg"], motion) == "still"
    assert hook_learning.opening_shot([], motion) is None


def test_hold_at_three_seconds_comes_from_the_retention_graph() -> None:
    graph = {str(k): 1.0 - k * 0.01 for k in range(41)}          # 40 fortieths of a 40 s video
    assert audience.hold_at({"post_video_retention_graph": graph}, 40.0) == 0.97
    assert audience.hold_at({}, 40.0) is None and audience.hold_at({"post_video_retention_graph": graph}, None) is None


def test_opening_fields_measure_earlier_runs_too() -> None:
    run = {"script_payload": {"hook_sentence": "How did it stay hidden?"}}
    assert audience.opening_fields(run, {}, None) == {"hook_form": "question", "opening_shot": None, "hold_3s": None,
                                                       "worst_beat": None}


def test_the_digest_compares_openings_per_platform() -> None:
    rows = [{"platform": "instagram", "run_id": "a", "hook_form": "question", "opening_shot": "ai_motion",
             "skip_rate": 30.0, "hold_3s": None},
            {"platform": "instagram", "run_id": "b", "hook_form": "discovery", "opening_shot": "still",
             "skip_rate": 50.0, "hold_3s": None},
            {"platform": "facebook", "run_id": "a", "hook_form": "question", "opening_shot": "ai_motion",
             "skip_rate": None, "hold_3s": 0.8},
            {"platform": "facebook", "run_id": "b", "hook_form": "discovery", "opening_shot": "still",
             "skip_rate": None, "hold_3s": 0.6}]
    lines = audience.opening_comparison(rows)
    assert [ln.split(" (")[0] for ln in lines] == ["• Hook forms · Instagram", "• Hook forms · Facebook",
                                                   "• Opening shots · Instagram", "• Opening shots · Facebook"]
    assert lines[0].index("question (1)") < lines[0].index("discovery (1)")
    assert "80% still watching at 3 s" in lines[1]


def test_the_shared_sampler_explores_least_tried_first() -> None:
    option, why = learning.choose(["a", "b"], {"a": [0.5] * 3}, seed="s", label="t")
    assert option == "b" and why.startswith("exploring")


def test_the_writer_and_the_run_carry_the_steer() -> None:
    import inspect

    import main
    from src.script_engine import ScriptEngine
    writer = inspect.getsource(ScriptEngine._generate_with_claude)
    assert 'steer_note(topic.get("hook_form"))' in writer
    run = inspect.getsource(main.PipelineCoordinator._write_and_check_script) if hasattr(main, "PipelineCoordinator") \
        else inspect.getsource(main)
    assert "hook_learning.for_run(" in run and '"hook_plan"' in run
