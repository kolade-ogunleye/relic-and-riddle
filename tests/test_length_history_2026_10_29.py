"""Production, Derinkuyu 2026-10-29 (rc69, run_derinkuyu_underground_city_1793273380): the length instructions
alternated between two failing drafts (100 words read 41.5 s: "extend to 104-108"; 106 words read 51.5 s: "cut to
99-103") because each was worked out from that draft's own pace; and once the writer's three attempts were spent the
unreviewed curated script was tried and the run failed "No verified fact sheet", hiding the real cause."""
import pytest

from src import script_engine
from src.models import ScriptPackage
from src.script_engine import ScriptEngine, spoken_length_problems

PACE = {"A": 41.5 / 100, "B": 51.6 / 106}   # seconds a word: the two drafts' paces (names and numbers differ)
SHEET = {"topic_id": "t1", "facts": [], "sources": [{"title": "X", "url": "https://en.wikipedia.org/wiki/X", "tier": 2}]}


def _draft(curated_payload, kind, words):
    closing = curated_payload["closing_sentence"]
    body = " ".join(f"{kind}{i}" for i in range(words - len(closing.split())))
    return ScriptPackage.model_validate(dict(curated_payload, narration_script=f"{body}. {closing}"))


@pytest.fixture
def measured(monkeypatch):
    """The measurement stubbed: a draft's pace is its kind's (the first letter of its first word); one take pace;
    no faster read (ElevenLabs); nothing written to the pace file."""
    monkeypatch.setattr(script_engine.audio_engine, "measure_spoken_seconds",
                        lambda text, speed=1.0: len(text.split()) * PACE[text[0]])
    monkeypatch.setattr(script_engine, "_pace_window", lambda: (1.0, 1.0))
    monkeypatch.setattr(script_engine, "fit_speed", lambda text, seconds=None: None)
    monkeypatch.setattr(script_engine, "record_word_pace", lambda *a, **k: None)
    script_engine.begin_length_history()
    monkeypatch.setitem(script_engine._length_history, "on", True)
    monkeypatch.setitem(script_engine._length_history, "thread", __import__("threading").get_ident())
    yield
    script_engine.begin_length_history()


def _window(problem):
    import re
    return tuple(int(x) for x in re.search(r"(\d+)-(\d+) words", problem).groups())


def test_the_instructions_converge_on_the_window_both_paces_allow(measured, curated_payload):
    first = spoken_length_problems(_draft(curated_payload, "A", 100))
    assert _window(first[0])[1] > 105                                    # one pace seen: as before, past B's ceiling
    long = spoken_length_problems(_draft(curated_payload, "B", 106))
    short = spoken_length_problems(_draft(curated_payload, "A", 100))
    assert _window(long[0]) == _window(short[0]) == (102, 105)           # both now aim inside 102-105
    for words in range(102, 106):                                        # and a draft there passes at either pace
        assert spoken_length_problems(_draft(curated_payload, "A", words)) == []
        assert spoken_length_problems(_draft(curated_payload, "B", words)) == []


def test_a_lone_check_is_judged_on_its_own_pace(measured, curated_payload, monkeypatch):
    spoken_length_problems(_draft(curated_payload, "A", 100))
    monkeypatch.setitem(script_engine._length_history, "on", False)      # not inside a run's writing
    assert _window(spoken_length_problems(_draft(curated_payload, "B", 106))[0])[0] < 102   # under A's minimum, as before


def test_paces_too_far_apart_aim_at_the_middle_and_say_so(measured, curated_payload, monkeypatch):
    monkeypatch.setitem(PACE, "B", 0.62)
    spoken_length_problems(_draft(curated_payload, "A", 100))
    problem = spoken_length_problems(_draft(curated_payload, "B", 100))[0]
    assert "aimed at the middle one" in problem


def test_a_draft_just_over_the_limit_says_so_to_the_hundredth(measured, curated_payload, monkeypatch):
    monkeypatch.setitem(PACE, "B", 51.52 / 106)
    assert "runs 51.52s when spoken (limit 51.5s)" in spoken_length_problems(_draft(curated_payload, "B", 106))[0]


def _engine(monkeypatch, curated_payload):
    engine = ScriptEngine()
    monkeypatch.setattr(script_engine, "_ready", lambda: True)
    monkeypatch.setattr(engine, "_sheet", lambda topic: SHEET)
    monkeypatch.setattr(engine, "_sheet_context", lambda sheet: ("notes", []))
    monkeypatch.setattr(engine, "_with_graphics", lambda core, notes, retrieved, feedback: core)
    monkeypatch.setattr(engine, "_finalize", lambda pkg, topic, **k: {"narration_script": pkg.narration_script, **k})
    monkeypatch.setattr(script_engine, "validate_structure", lambda pkg: [])
    monkeypatch.setattr(script_engine, "hook_problems", lambda narration: [])
    monkeypatch.setattr(script_engine.improve, "note_problems", lambda problems, source: None)
    monkeypatch.setattr(script_engine, "spoken_length_problems", lambda pkg: [] if 105 <= len(pkg.narration_script.split()) <= 110 else [
        f"Narration runs {len(pkg.narration_script.split()) * 0.4:.1f}s when spoken (minimum 42s). This draft is "
        f"{len(pkg.narration_script.split())} words; extend the narration to 105-110 words with more verified facts "
        "from the research notes, keeping the closing sentence."])
    return engine


def test_a_length_edit_a_few_words_short_gets_one_more_edit_not_a_rewrite(monkeypatch, curated_payload):
    engine = _engine(monkeypatch, curated_payload)
    written, edits = [], []
    monkeypatch.setattr(engine, "_generate_with_claude", lambda topic, feedback: written.append(1) or
                        _draft(curated_payload, "A", 90))
    monkeypatch.setattr(engine, "_revise_surgically", lambda previous, findings, sheet: edits.append(findings) or
                        _draft(curated_payload, "A", 103 if len(edits) == 1 else 107))
    out = engine.generate_script({"id": "t1", "title": "T"})
    assert len(written) == 1 and len(edits) == 2 and len(out["narration_script"].split()) == 107


def test_a_length_edit_far_off_still_falls_back_to_the_rewrite(monkeypatch, curated_payload):
    engine = _engine(monkeypatch, curated_payload)
    written, edits = [], []
    monkeypatch.setattr(engine, "_generate_with_claude", lambda topic, feedback: written.append(1) or
                        _draft(curated_payload, "A", 90 if len(written) == 1 else 107))
    monkeypatch.setattr(engine, "_revise_surgically", lambda previous, findings, sheet: edits.append(1) or
                        _draft(curated_payload, "A", 95))
    engine.generate_script({"id": "t1", "title": "T"})
    assert len(written) == 2 and len(edits) == 1


def test_a_surgical_revision_off_only_in_length_is_edited_to_it_not_rewritten(monkeypatch, curated_payload):
    engine = _engine(monkeypatch, curated_payload)
    written, edits = [], []
    monkeypatch.setattr(engine, "_generate_with_claude", lambda topic, feedback: written.append(1))
    monkeypatch.setattr(engine, "_revise_surgically", lambda previous, findings, sheet: edits.append(findings) or
                        _draft(curated_payload, "A", 112 if len(edits) == 1 else 108))
    out = engine.generate_script({"id": "t1", "title": "T"}, revision_notes="'a claim here' (overstated)",
                                 previous=dict(curated_payload, fact_sheet=True))
    assert not written and len(edits) == 2 and edits[1].startswith("LENGTH:") and out["fact_sheet_used"] is True


def test_an_unreviewed_curated_fallback_names_the_writers_real_failure(monkeypatch, curated_payload):
    engine = _engine(monkeypatch, curated_payload)
    monkeypatch.setattr(engine, "_generate_with_claude", lambda topic, feedback: _draft(curated_payload, "A", 90))
    monkeypatch.setattr(engine, "_revise_surgically", lambda previous, findings, sheet: None)
    curated = ScriptPackage.model_validate(curated_payload)
    monkeypatch.setattr(engine, "_load_curated", lambda topic_id: (curated, False))
    monkeypatch.setattr(engine, "_finalize", lambda pkg, topic, **k: dict(pkg.model_dump(), topic_id="t1", **k))
    payload = engine.generate_script({"id": "t1", "title": "T"})
    assert payload["origin"] == "curated" and "made 3 attempt(s)" in payload["writer_failure"]
    monkeypatch.setattr(script_engine, "spoken_length_problems", lambda pkg: [])
    monkeypatch.setattr(script_engine.free_llm, "enabled", lambda: True)
    passed, _, notes = engine.verify_claims(payload, topic={"id": "t1"})
    assert not passed and "No verified fact sheet" not in notes
    assert "not human-reviewed" in notes and "extend the narration to 105-110 words" in notes


def test_a_writers_script_is_checked_against_the_sheet_only_when_written_from_it(monkeypatch, curated_payload):
    engine = ScriptEngine()
    monkeypatch.setattr(script_engine, "validate_structure", lambda pkg: [])
    monkeypatch.setattr(script_engine, "spoken_length_problems", lambda pkg: [])
    monkeypatch.setattr(script_engine.free_llm, "enabled", lambda: True)
    monkeypatch.setattr(script_engine, "SECOND_CHECK_ENABLED", False)
    loaded, traced = [], []
    monkeypatch.setattr(script_engine.fact_sheet, "load", lambda topic_id, max_age_days=0: loaded.append(topic_id) or SHEET)
    monkeypatch.setattr(script_engine.trace_check, "check", lambda p, s: traced.append(s) or (True, 100.0, [], "all traced"))
    passed, _, _ = engine.verify_claims(dict(curated_payload, topic_id="t1", origin="gemini-free", fact_sheet=True))
    assert passed and traced == [SHEET]
    # written from research notes (no mark): never judged against a sheet it did not see, and none is loaded (or seeded)
    loaded.clear()
    passed, _, notes = engine.verify_claims(dict(curated_payload, topic_id="t1", origin="gemini-free"))
    assert not passed and loaded == [] and "fails closed" in notes


def _snapshot_engine(monkeypatch, curated_payload):
    """generate_script around a stand-in that measures one draft of the topic's kind and returns the run's paces."""
    engine = ScriptEngine()

    def write(topic, max_attempts, revision_notes, previous):
        spoken_length_problems(_draft(curated_payload, topic["kind"], 100))
        return dict(script_engine._draft_paces)
    monkeypatch.setattr(engine, "_generate_script", write)
    return engine


def test_another_topic_or_run_starts_its_length_history_afresh(measured, curated_payload, monkeypatch):
    engine = _snapshot_engine(monkeypatch, curated_payload)
    engine.length_run = "run_1"
    engine.generate_script({"id": "a", "kind": "A"})
    # the next topic of a cycle, its first call carrying a payload (a saved script, a regeneration)
    paces = engine.generate_script({"id": "b", "kind": "B"}, revision_notes="x", previous={"fact_sheet": True})
    assert list(paces.values()) == [pytest.approx(PACE["B"])]
    engine.length_run = "run_2"                                          # the same topic, another run
    paces = engine.generate_script({"id": "b", "kind": "A"}, revision_notes="x", previous={"fact_sheet": True})
    assert list(paces.values()) == [pytest.approx(PACE["A"])]
    paces = engine.generate_script({"id": "b", "kind": "B"}, revision_notes="x", previous={"fact_sheet": True})
    assert len(paces) == 2                                               # its own revision round carries on
    assert script_engine._length_history["on"] is False                  # and only while writing


def test_main_names_the_run_whose_drafts_these_are(db, tmp_path):
    import main
    from types import SimpleNamespace

    class Engine:
        progress = None

        def generate_script(self, topic, revision_notes="", previous=None):
            seen.append(self.length_run)
            return {"narration_script": "x", "origin": "curated"}

        def verify_claims(self, payload, topic=None, allow_unreviewed_curated=False):
            return True, payload, "PASSED"
    seen = []
    db.create_run("run_x", db.list_all_topics()[0]["id"])
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.db, c.script_engine = db, Engine()
    c._check_cancel = lambda run_id: None
    c.telegram_bot = SimpleNamespace(update_progress_card=lambda *a, **k: None)
    c._write_and_check_script({"id": "t", "title": "T"}, "run_x", tmp_path, dry_run=True)
    assert seen == ["run_x"]


def test_one_far_off_draft_does_not_set_the_window(measured, curated_payload, monkeypatch):
    monkeypatch.setitem(PACE, "C", 0.425)
    monkeypatch.setitem(PACE, "D", 0.62)                                 # a name-heavy draft, 45% slower
    for kind in "ACD":
        problems = spoken_length_problems(_draft(curated_payload, kind, 100))
    assert "aimed at the middle one" not in problems[0]
    assert script_engine._run_paces() == sorted([round(PACE["A"], 5), round(PACE["C"], 5)])


def test_the_prompts_length_note_names_the_runs_range(measured, curated_payload):
    spoken_length_problems(_draft(curated_payload, "A", 100))
    band = _window(spoken_length_problems(_draft(curated_payload, "B", 106))[0])
    assert f"write the narration in {band[0]}-{band[1]} words" in script_engine.length_target_note()


def test_with_a_real_take_spread_the_instructions_agree_and_their_range_passes(measured, curated_payload, monkeypatch):
    monkeypatch.setattr(script_engine, "_pace_window", lambda: (0.997, 1.10))   # ElevenLabs' takes vary
    monkeypatch.setitem(PACE, "B", 0.43)
    long = spoken_length_problems(_draft(curated_payload, "A", 90))
    first = _window(spoken_length_problems(_draft(curated_payload, "B", 120))[0])
    again = _window(spoken_length_problems(_draft(curated_payload, "A", 90))[0])
    assert long and first == again
    for words in range(first[0], first[1] + 1):
        assert spoken_length_problems(_draft(curated_payload, "A", words)) == []
        assert spoken_length_problems(_draft(curated_payload, "B", words)) == []


def test_the_draft_judged_always_counts_and_an_instruction_never_points_the_wrong_way(measured, curated_payload, monkeypatch):
    monkeypatch.setitem(PACE, "C", 0.425)
    monkeypatch.setitem(PACE, "D", 0.62)
    for kind in "AC":
        spoken_length_problems(_draft(curated_payload, kind, 100))
    problem = spoken_length_problems(_draft(curated_payload, "D", 100))[0]  # reads 62 s: too long
    assert "limit" in problem and _window(problem)[1] < 100                # told to cut, never to grow
    for kind, words in (("A", 60), ("A", 98), ("D", 70), ("D", 110), ("C", 95), ("A", 125)):
        for p in spoken_length_problems(_draft(curated_payload, kind, words)):
            low, high = _window(p)
            assert (high < words) if "limit" in p else (low > words), (kind, words, p)


def test_a_check_on_another_thread_never_enters_the_runs_history(measured, curated_payload):
    import threading
    spoken_length_problems(_draft(curated_payload, "A", 100))
    other = threading.Thread(target=lambda: spoken_length_problems(_draft(curated_payload, "B", 106)))
    other.start()
    other.join(5)
    assert list(script_engine._draft_paces.values()) == [pytest.approx(PACE["A"])]


def test_paces_all_far_apart_are_said_and_the_draft_is_judged_on_its_own(measured, curated_payload, monkeypatch, caplog):
    for kind, pace in zip("ABCD", (0.30, 0.31, 0.60, 0.61)):           # two clusters: none near the median
        monkeypatch.setitem(PACE, kind, pace)
    with caplog.at_level("INFO", logger="src.script_engine"):
        for kind in "ABCD":
            spoken_length_problems(_draft(curated_payload, kind, 100))
    assert "too far apart to set a window" in caplog.text
