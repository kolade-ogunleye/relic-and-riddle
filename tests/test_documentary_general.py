"""
tests/test_documentary_general.py
=================================
The 16:9 documentary works for any topic: no topic-specific copy, graphics or shot rules.
"""


def _words(text, start=0.0, step=0.4):
    out, t = [], start
    for w in text.split():
        out.append({"word": w, "start": round(t, 3), "dur": step * 0.9})
        t += step
    return out


def test_documentary_cuts_follow_sentences_and_cover_the_narration():
    from src.scene_planner import documentary_cuts, MIN_CUT, MAX_CUT
    text = ("Short one. " * 6) + ("This sentence is a good deal longer and carries on for quite a while before it ends. " * 4)
    words = _words(text)
    total = words[-1]["start"] + words[-1]["dur"] + 0.3
    cuts = documentary_cuts(words, total)
    assert cuts[0]["start"] == 0.0 and abs(cuts[-1]["end"] - total) < 1e-6
    assert all(a["end"] == b["start"] for a, b in zip(cuts, cuts[1:]))  # contiguous: no gaps, no overlaps
    for c in cuts[:-1]:
        length = c["end"] - c["start"]
        assert length >= MIN_CUT or length > MAX_CUT - MIN_CUT  # short sentences were merged
    assert all(c["text"].strip().endswith(".") for c in cuts[:-1])  # cuts land on sentence ends


def test_cover_lines_come_from_the_checked_title_only():
    from main import documentary_cover
    cover = documentary_cover("The Tunguska Event: Siberia's 1908 Blast")
    assert cover["line1"] and cover["line2"] and "METROPOLIS" not in " ".join(cover.values())
    assert all(len(cover[k]) <= 22 for k in ("line1", "line2"))
    joined = (cover["line1"] + " " + cover["line2"]).replace("…", "")
    assert joined.split()[0] in "THE TUNGUSKA EVENT: SIBERIA'S 1908 BLAST"


def test_no_topic_specific_documentary_code():
    import inspect
    import main
    src = inspect.getsource(main.PipelineCoordinator.produce_deep_dive)
    for marker in ("derinkuyu", "ANCIENT METROPOLIS", "04_commons", "cappadocia", "ventilation shaft"):
        assert marker not in src.lower() if marker.islower() else marker not in src, marker


def test_documentary_planning_ignores_a_reel_storyboard(tmp_path, monkeypatch):
    import json
    from PIL import Image
    from src import scene_planner
    monkeypatch.setattr(scene_planner, "claude_available", lambda: False)
    monkeypatch.setattr(scene_planner.free_llm, "enabled", lambda: False)
    stills = []
    for i in range(3):
        p = tmp_path / f"0{i}_commons.jpg"
        Image.new("RGB", (64, 64)).save(p)
        stills.append(p)
    beats = [{"start": i * 6.0, "end": (i + 1) * 6.0, "text": f"Sentence {i}."} for i in range(4)]
    (tmp_path / "storyboard.json").write_text(json.dumps(
        [{"beat": i + 1, "file": stills[i % 3].name, "text": b["text"]} for i, b in enumerate(beats)]), encoding="utf-8")
    _, _, notes = scene_planner.plan_scenes(beats, stills, use_storyboard=False)
    assert notes != scene_planner.STORYBOARD_NOTES
    _, _, notes = scene_planner.plan_scenes(beats, stills)
    assert notes == scene_planner.STORYBOARD_NOTES  # Reels still honour it


def test_a_rejected_documentary_draft_is_rewritten_with_the_findings(db, tmp_path, monkeypatch):
    import main

    class Stop(Exception):
        pass

    class Visuals:
        def get_scene_sequence(self, topic):
            return []

        def hero_image(self, media):
            return None

    feedbacks, verdicts = [], iter([(False, ["'In Mauritania.' (Act 1; untraced: no place fact)"]), (True, [])])

    class Writer:
        def generate_deep_dive(self, topic, feedback=""):
            feedbacks.append(feedback)
            return type("Pkg", (), {"full_narration": "x", "acts": [], "title": "T",
                                    "model_dump_json": lambda self, **k: "{}"})()

        def verify_deep_dive_act_by_act(self, pkg, topic):
            return next(verdicts)

    def stop(title):
        raise Stop
    coordinator = object.__new__(main.PipelineCoordinator)
    coordinator.db = db
    coordinator.visual_engine, coordinator.script_engine = Visuals(), Writer()
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(main, "documentary_cover", stop)  # the first step after the script passes
    result = coordinator.produce_deep_dive({"id": "01_topic", "title": "Topic 1"}, dry_run=True)
    assert "fact verification failed" not in str(result)  # the second draft passed; it went on to the cover
    assert feedbacks[0] == "" and "In Mauritania." in feedbacks[1] and len(feedbacks) == 2


def test_the_documentary_shows_a_progress_card_that_becomes_its_failure_card(db, tmp_path, monkeypatch):
    import main

    class Visuals:
        def get_scene_sequence(self, topic):
            return []

        def hero_image(self, media):
            return None

    class Writer:
        def generate_deep_dive(self, topic, feedback=""):
            return type("Pkg", (), {"full_narration": "x", "acts": [], "title": "T"})()

        def verify_deep_dive_act_by_act(self, pkg, topic):
            return False, ["'In Mauritania.' (Act 1; untraced: no place fact)"]

    calls = []

    class Bot:
        def send_progress_card(self, run_id, title):
            calls.append(("start", title))
            return 77

        def update_progress_card(self, *a, **k):
            calls.append(("step", a[1]))

        def finish_progress_card(self, mid, title, run_id, success=True, error=""):
            calls.append(("finish", mid, success, "Mauritania" in error))
            return True

        def notify(self, *a, **k):
            calls.append(("alert",))
    coordinator = object.__new__(main.PipelineCoordinator)
    coordinator.db, coordinator.telegram_bot = db, Bot()
    coordinator.visual_engine, coordinator.script_engine = Visuals(), Writer()
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)
    result = coordinator.produce_deep_dive({"id": "01_topic", "title": "Topic 1"}, dry_run=True)
    assert result["status"] == "failed"
    assert calls[0] == ("start", "Topic 1 · 16:9 documentary")
    assert calls[-1] == ("finish", 77, False, True) and ("alert",) not in calls  # one failure card, no extra alert
