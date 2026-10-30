from pathlib import Path

from src import video_qa

ASS = """[Script Info]
PlayResX: 1080
PlayResY: 1920

[Events]
Dialogue: 1,0:00:40.00,0:00:41.00,CapText,,0,0,0,,first
Dialogue: 1,0:00:44.00,0:00:45.00,CapText,,0,0,0,,before the card
Dialogue: 1,0:00:45.76,0:00:46.18,CapText,,0,0,0,,under the card
"""


def test_captions_under_the_end_card_are_not_judged(tmp_path, monkeypatch):
    """Production, 30 Oct: the last caption's midpoint fell inside the end card, where no caption is drawn; the
    bare stone read 2.08:1 and blocked the review. Only captions shown before the card are sampled."""
    ass = tmp_path / "c.ass"
    ass.write_text(ASS, encoding="utf-8")
    seen = []

    def fake_run(cmd, **kw):
        if "-ss" in cmd:
            seen.append(float(cmd[cmd.index("-ss") + 1]))
        raise RuntimeError("stop after recording the sample time")

    monkeypatch.setattr(video_qa.subprocess, "run", fake_run)
    try:
        video_qa.caption_contrast(tmp_path / "m.mp4", ass, tmp_path / "out", until=45.5)
    except RuntimeError:
        pass
    assert seen and all(t < 45.5 for t in seen)
    assert video_qa.caption_contrast(tmp_path / "m.mp4", ass, tmp_path / "out2", until=39.0) == {"samples": [],
                                                                                                    "min": None}
