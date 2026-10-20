from pathlib import Path

import pytest

from src import scene_planner as sp
from src import video_qa
from src.models import QAIssue, QAReport, ScenePlan


def _words(text, start=0.0, step=0.35):
    out, t = [], start
    for w in text.split():
        out.append({"word": w, "start": t, "dur": step - 0.05})
        t += step
    return out


def test_beats_cut_at_pauses_and_cover_the_whole_narration():
    words = _words("In 1963 a man knocked through a wall. Behind it lay a tunnel, dark and cold, "
                   "leading down into an underground city that held thousands of people and their animals.")
    beats = sp.segment_beats(words, total=12.0)
    assert beats[0]["start"] == 0.0 and beats[-1]["end"] == 12.0
    assert all(b["end"] > b["start"] for b in beats)
    assert all(a["end"] == b["start"] for a, b in zip(beats, beats[1:]))  # contiguous
    assert beats[0]["text"].endswith("wall.")                              # cut at the sentence end
    assert all(b["end"] - b["start"] <= sp.MAX_BEAT + 0.5 for b in beats[:-1])


def test_plan_without_claude_keeps_filename_order(monkeypatch):
    monkeypatch.setattr(sp, "claude_available", lambda: False)
    beats = [{"start": 0, "end": 3, "text": "a"}, {"start": 3, "end": 7, "text": "b"}, {"start": 7, "end": 10, "text": "c"}]
    media = [Path("1.jpg"), Path("2.jpg")]
    scenes, durations, notes = sp.plan_scenes(beats, media)
    assert scenes == [Path("1.jpg"), Path("2.jpg"), Path("1.jpg")] and durations == [3, 4, 3]


def test_plan_uses_claude_choice_and_keeps_hook_clip(monkeypatch, tmp_path):
    from PIL import Image
    imgs = []
    for i in range(3):
        p = tmp_path / f"{i}.jpg"
        Image.new("RGB", (40, 40)).save(p)
        imgs.append(p)
    monkeypatch.setattr(sp, "claude_available", lambda: True)
    monkeypatch.setattr(sp, "structured_call", lambda *a, **k: ScenePlan(images=[3, 1], notes="ok"))
    beats = [{"start": 0, "end": 3, "text": "a"}, {"start": 3, "end": 6, "text": "b"}]
    scenes, _, _ = sp.plan_scenes(beats, imgs)
    assert scenes == [imgs[2], imgs[0]]
    hook = tmp_path / "hook_motion.mp4"
    scenes, _, _ = sp.plan_scenes(beats, [hook] + imgs)
    assert scenes[0] == hook


def test_bad_plan_falls_back(monkeypatch, tmp_path):
    from PIL import Image
    p = tmp_path / "a.jpg"
    Image.new("RGB", (40, 40)).save(p)
    monkeypatch.setattr(sp, "claude_available", lambda: True)
    monkeypatch.setattr(sp, "structured_call", lambda *a, **k: ScenePlan(images=[9], notes=""))
    scenes, _, notes = sp.plan_scenes([{"start": 0, "end": 3, "text": "a"}], [p])
    assert scenes == [p] and notes == "filename order"


def test_lock_narrative_graphics():
    beats = [
        {"start": 0.0, "end": 3.0, "text": "An ancient subterranean wonder awaits."},
        {"start": 3.0, "end": 6.5, "text": "In 1963, a man in Cappadocia knocked through his wall."},
        {"start": 6.5, "end": 10.0, "text": "It plunges roughly 85 metres deep into volcanic rock."},
        {"start": 10.0, "end": 14.0, "text": "Massive rolling stone doors sealed each corridor."},
    ]
    graphics = [
        Path("graphic_map.mp4"),
        Path("graphic_section.mp4"),
        Path("graphic_labels_door.mp4"),
    ]
    locked = sp.lock_narrative_graphics(beats, graphics)
    assert locked[1] == Path("graphic_map.mp4")
    assert locked[2] == Path("graphic_section.mp4")
    assert locked[3] == Path("graphic_labels_door.mp4")


def test_qa_summary_orders_by_severity_and_escapes():
    report = QAReport(summary="", issues=[QAIssue(beat=2, severity="minor", issue="<tiny>"),
                                          QAIssue(beat=0, severity="Critical", issue="watermark")])
    text = video_qa.qa_summary_html(report)
    assert text.index("cover") < text.index("beat 2") and "<tiny>" not in text and "🛑" in text
    assert "no issues" in video_qa.qa_summary_html(QAReport(issues=[], summary=""))
    assert "not run" in video_qa.qa_summary_html(None)


def test_card_includes_qa_summary():
    from src.telegram_bot import TelegramApprovalBot
    card = TelegramApprovalBot.format_card_text({"title": "T", "qa_summary": "🔎 Automated video check: no issues found"}, "r1")
    assert "Automated video check" in card


def test_storyboard_lock_overrides_llm(tmp_path, monkeypatch):
    import json
    imgs = [tmp_path / f"img_{i}.jpg" for i in range(1, 4)]
    for p in imgs:
        p.write_bytes(b"data")

    # Create approved storyboard locking beat 1 -> img_3, beat 2 -> img_1, beat 3 -> img_2
    sb_data = [
        {"beat": 1, "file": "img_3.jpg", "text": "beat 1"},
        {"beat": 2, "file": "img_1.jpg", "text": "beat 2"},
        {"beat": 3, "file": "img_2.jpg", "text": "beat 3"},
    ]
    (tmp_path / "storyboard.json").write_text(json.dumps(sb_data), encoding="utf-8")

    # Ensure LLM would have picked something else if called
    monkeypatch.setattr(sp, "claude_available", lambda: True)
    monkeypatch.setattr(sp, "structured_call", lambda *a, **k: pytest.fail("LLM should not be called when storyboard exists"))

    beats = [
        {"start": 0, "end": 3, "text": "beat 1"},
        {"start": 3, "end": 6, "text": "beat 2"},
        {"start": 6, "end": 9, "text": "beat 3"},
    ]
    scenes, durations, notes = sp.plan_scenes(beats, imgs)
    assert scenes == [imgs[2], imgs[0], imgs[1]]
    assert notes == "approved storyboard contract lock"


def test_storyboard_lock_preserves_motion_hook_and_animated_graphics(tmp_path):
    import json
    imgs = [tmp_path / f"img_{i}.jpg" for i in range(1, 4)]
    for p in imgs:
        p.write_bytes(b"data")

    sb_data = [
        {"beat": 1, "file": "img_1.jpg", "text": "opening hook"},
        {"beat": 2, "file": "img_2.jpg", "text": "deep inside Cappadocia Turkey"},
        {"beat": 3, "file": "img_3.jpg", "text": "final conclusion"},
    ]
    (tmp_path / "storyboard.json").write_text(json.dumps(sb_data), encoding="utf-8")

    hook_vid = tmp_path / "hook_motion.mp4"
    hook_vid.write_bytes(b"video")

    map_vid = tmp_path / "graphic_map.mp4"
    map_vid.write_bytes(b"video")
    (tmp_path / "graphic_map.jpg").write_bytes(b"poster")

    media = [hook_vid, map_vid] + imgs
    beats = [
        {"start": 0, "end": 3, "text": "opening hook"},
        {"start": 3, "end": 6, "text": "deep inside Cappadocia Turkey"},
        {"start": 6, "end": 9, "text": "final conclusion"},
    ]
    scenes, durations, notes = sp.plan_scenes(beats, media)
    # Beat 0 gets motion hook video
    assert scenes[0] == hook_vid
    # Beat 1 gets animated map graphic locked to Cappadocia Turkey
    assert scenes[1] == map_vid
    # Beat 2 gets locked storyboard image img_3
    assert scenes[2] == imgs[2]
