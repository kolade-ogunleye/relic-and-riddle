"""Engagement asks (2026-11-01): the share line's and the caption question's forms, measured on what viewers did
and steered towards the forms that work."""

from collections import Counter

from src import audience
from src import engagement_learning as el


def test_share_lines_are_classified() -> None:
    assert el.classify_share("Send this to the codebreaker you know, and follow for the next relic.") == "identity"
    assert el.classify_share("Send this to a friend who loves Roman engineering, and follow for more.") == "interest"
    assert el.classify_share("") is None


def test_the_caption_question_is_found_and_classified() -> None:
    caption = ("A city under the fields.\nWho dug it is still debated.\n"
               "Would you explore it on foot, or only from above? 🛰️\n\n#Derinkuyu #History")
    q = el.caption_question(caption)
    assert q.startswith("Would you explore") and el.classify_question(q) == "either_or"
    assert el.classify_question("Would you have stayed below?") == "would_you"
    assert el.classify_question("Which wonder looks most magnificent from orbit?") == "opinion"
    assert el.classify_question("What did they fear?") == "open"
    assert el.caption_question("No question here.\n\n#Tag?") == ""


def test_rewards_use_rates_per_account_reached() -> None:
    assert el.share_reward({"share_rate": 0.02, "save_rate": 0.01}) == 1.0
    assert el.share_reward({}) is None
    assert el.question_reward({"comments": 5.0, "reach": 1000.0}) == 0.5
    assert el.question_reward({"comments": 5.0, "reach": 0}) is None


def test_the_stronger_question_form_is_asked_for_more_often() -> None:
    rows = [{"platform": "instagram", "run_id": f"e{i}", "question_form": "either_or", "share_form": "identity",
             "comments": 20.0, "reach": 1000.0, "share_rate": 0.01} for i in range(10)]
    for form in ("would_you", "opinion", "open"):
        rows += [{"platform": "instagram", "run_id": f"{form}{i}", "question_form": form, "share_form": "interest",
                  "comments": 1.0, "reach": 1000.0, "share_rate": 0.01} for i in range(10)]
    picks = Counter(el.choose(rows, seed=f"r{i}")[0][1] for i in range(200))
    assert picks["either_or"] > 150


def test_the_steer_names_both_asks() -> None:
    note = el.steer_note(("identity", "either_or"))
    assert note.startswith("\n\nENGAGEMENT FORM") and "share_line" in note and "choice between two" in note
    assert el.steer_note(None) == ""


def test_opening_fields_carry_the_ask_forms() -> None:
    run = {"script_payload": {"share_line": "Send this to the codebreaker you know, and follow.",
                              "caption": "Line.\nWhich code is hardest?\n\n#x"}}
    fields = audience.opening_fields(run, {}, None)
    assert fields["share_form"] == "identity" and fields["question_form"] == "opinion"


def test_the_digest_compares_the_asks() -> None:
    rows = [{"platform": "instagram", "run_id": "a", "share_form": "identity", "question_form": "either_or",
             "share_rate": 0.02, "save_rate": 0.01, "comments": 12.0, "reach": 1000.0},
            {"platform": "instagram", "run_id": "b", "share_form": "interest", "question_form": "open",
             "share_rate": 0.005, "save_rate": 0.0, "comments": 2.0, "reach": 1000.0}]
    lines = audience.asks_comparison(rows)
    assert [ln.split(" (")[0] for ln in lines] == ["• Share asks · Instagram", "• Caption questions · Instagram"]
    assert lines[0].index("identity (1)") < lines[0].index("interest (1)") and "1.20% commented" in lines[1]


def test_the_writer_and_the_run_carry_the_asks() -> None:
    import inspect

    import main
    from src.script_engine import ScriptEngine
    assert 'engagement_steer(topic.get("engagement_forms"))' in inspect.getsource(ScriptEngine._generate_with_claude)
    run = inspect.getsource(main.PipelineCoordinator._write_and_check_script)
    assert "engagement_learning.for_run(" in run and '"engagement_plan"' in run
