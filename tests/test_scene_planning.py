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
