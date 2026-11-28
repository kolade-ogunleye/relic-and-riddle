from src import video_qa

ASS = """[Script Info]
PlayResX: 1080
PlayResY: 1920

[Events]
Dialogue: 1,0:00:40.00,0:00:41.00,CapText,,0,0,0,,first
Dialogue: 1,0:00:44.00,0:00:45.00,CapText,,0,0,0,,before the card
Dialogue: 1,0:00:45.76,0:00:46.18,CapText,,0,0,0,,under the card
"""


def test_captions_under_the_end_card_are_not_judged(tmp_path):
    """Production, 30 Oct: the last caption's midpoint fell inside the end card, where no caption is drawn; the
    bare stone read 2.08:1 and blocked the review. Only captions shown before the card are judged.
    Re-stated 2026-11-28 (freeze c6 capgate): this pinned the 6 sampled midpoints' ffmpeg seeks (-ss); the gate now
    measures every caption frame of the master (video_qa.caption_frame_plan), so the rule is held on the frames it
    plans: none at or after the card's start, and no caption starting under the card."""
    ass = tmp_path / "c.ass"
    ass.write_text(ASS, encoding="utf-8")
    bursts = video_qa.caption_bursts(ass.read_text(encoding="utf-8"), until=45.5)
    assert [b["text"] for b in bursts] == ["first", "before the card"]
    rows, shown = video_qa.caption_frame_plan(bursts, 30.0)
    assert rows and all(n / 30.0 < 45.5 for n, _, _ in rows) and len(shown) == 2
    assert video_qa.caption_contrast(tmp_path / "m.mp4", ass, tmp_path / "out2", until=39.0) == {
        "samples": [], "min": None, "bursts": 0, "under": [], "dips": [], "frames": 0, "badge": None}
