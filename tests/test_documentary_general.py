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
    (tmp_path / "storyboard.json").write_text(json.dumps([{"beat": 1, "file": "02_commons.jpg"}]), encoding="utf-8")
    beats = [{"start": i * 6.0, "end": (i + 1) * 6.0, "text": f"Sentence {i}."} for i in range(4)]
    _, _, notes = scene_planner.plan_scenes(beats, stills, use_storyboard=False)
    assert notes != "approved storyboard contract lock"
    _, _, notes = scene_planner.plan_scenes(beats, stills)
    assert notes == "approved storyboard contract lock"  # Reels still honour it
