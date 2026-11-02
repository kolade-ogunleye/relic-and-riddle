"""Panel round 4 (2026-11-02), end card and loop bridge: the 16:9 end screen has a second video element slot in
the lower right (clear of the player's timestamp, the text column and the credits line), its layout guard covers
every slot, and it says a fixed YouTube hand-off line instead of the Reel's share ask; the loop bridge records how
far its last frame is from frame 0 and a render-time gate (check_loop_bridge) fails a hitching seam closed, with a
QA note, so the composer can ship the plain card instead."""
import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import brand, end_card
from src.script_engine import EMOJI_RE, SHARE_LINE_MAX, engagement_bait
from src.typography import drawable

SHARE = "Send this to a space enthusiast you know."
LONG = ("Send this to the friend who still believes the old maps hid a whole continent beneath the ice of the "
        "southern ocean.")


@pytest.fixture
def short_end_screen(monkeypatch):
    monkeypatch.setattr(end_card, "END_SCREEN_SECONDS", 3.0)


def _plate():
    im = Image.new("RGB", (1920, 1080), (205, 190, 165))
    d = ImageDraw.Draw(im)
    for x in range(0, 1920, 90):
        d.line([(x, 0), (x + 300, 1080)], fill=(120, 105, 90), width=6)
    return im


def _overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


# ---------------------------------------------------------------- the second video slot
def test_the_second_video_slot_fills_the_lower_right_and_keeps_clear():
    slots = end_card.END_SCREEN_SLOTS
    assert set(end_card.END_SCREEN_VIDEO_SLOTS) == {"video", "video2"} <= set(slots)
    x0, y0, x1, y1 = slots["video2"]
    assert abs((x1 - x0) / (y1 - y0) - 16 / 9) < 0.02                       # a 16:9 element
    assert x0 >= 960 and y0 >= 540 and x1 <= 1920 and y1 <= 1080            # the lower right quadrant
    frames = end_card.end_screen_extras()["frames"]
    assert set(frames) == {"video", "video2", "subscribe"}
    f2 = frames["video2"]
    assert f2 == (x0 - 12, y0 - 12, x1 + 12, y1 + 12)
    assert not _overlap(f2, end_card.YOUTUBE_TIMESTAMP_ZONE)               # the player's timestamp stays clear
    assert not _overlap(f2, frames["video"]) and not _overlap(f2, frames["subscribe"])
    c = end_card.end_screen_extras()["credits"]
    right = c["xy"][0] + c["font"].getlength(c["text"]) + c["track"] * (len(c["text"]) - 1)
    assert right < f2[0]                                                    # right of the credits line


@pytest.mark.parametrize("share", [end_card.HANDOFF, SHARE, LONG], ids=["handoff", "share", "long"])
def test_the_text_column_clears_every_slot(share):
    lay = end_card.layout(share, 1920, 1080)
    assert end_card.end_screen_problems(lay) == []
    assert end_card.end_screen_problems(lay, credits=True) == []
    right = lay["align_x"] + max(lay["share_widths"])
    bottom = lay["top"] + len(lay["lines"]) * lay["line_h"]
    for k in end_card.END_SCREEN_VIDEO_SLOTS:
        assert right <= end_card.END_SCREEN_SLOTS[k][0] - end_card.END_SCREEN_GAP
    assert bottom <= end_card.END_SCREEN_SLOTS["subscribe"][1] - end_card.END_SCREEN_GAP


def test_the_layout_guard_sees_a_slot_moved_into_the_text(monkeypatch):
    lay = end_card.layout(SHARE, 1920, 1080)
    monkeypatch.setitem(end_card.END_SCREEN_SLOTS, "video2", (300, 300, 780, 570))
    assert any("video2" in p for p in end_card.end_screen_problems(lay))
    monkeypatch.setitem(end_card.END_SCREEN_SLOTS, "video2", (1100, 700, 1580, 970))
    monkeypatch.setattr(end_card, "YOUTUBE_TIMESTAMP_ZONE", (100, 100, 900, 400))
    assert any("timestamp" in p for p in end_card.end_screen_problems(lay))


def test_a_long_line_steps_down_rather_than_reach_the_subscribe_slot(monkeypatch):
    free = end_card.layout(LONG, 1920, 1080)
    monkeypatch.setitem(end_card.END_SCREEN_SLOTS, "subscribe", (140, 560, 420, 840))
    lay = end_card.layout(LONG, 1920, 1080)
    floor = 560 - end_card.END_SCREEN_GAP
    assert lay["top"] + len(lay["lines"]) * lay["line_h"] <= floor
    assert (lay["share_font"].size, len(lay["lines"])) != (free["share_font"].size, len(free["lines"]))


def test_slot_frames_frame_the_second_slot_and_leave_it_clear(short_end_screen):
    plain = list(end_card.frames(_plate(), SHARE))[-1]
    framed = list(end_card.frames(_plate(), SHARE, slot_frames=True))[-1]
    a, b = np.asarray(plain, np.int16), np.asarray(framed, np.int16)
    x0, y0, x1, y1 = end_card.END_SCREEN_SLOTS["video2"]
    assert np.abs(a[y0 + 4:y1 - 4, x0 + 4:x1 - 4] - b[y0 + 4:y1 - 4, x0 + 4:x1 - 4]).max() <= 1
    o = end_card.SLOT_FRAME_OFFSET
    edge = b[y1 + o, x0:x1]
    assert np.abs(edge - np.array(brand.GOLD_DIM)).sum(axis=1).mean() < 40        # a GOLD_DIM hairline
    assert np.abs(a[y1 + o + 3, x0:x1] - b[y1 + o + 3, x0:x1]).max() <= 1          # one pixel wide


# ---------------------------------------------------------------- the YouTube hand-off line
def test_the_handoff_line_is_a_plain_brand_line():
    line = end_card.HANDOFF
    assert len(line) <= SHARE_LINE_MAX and "#" not in line and not EMOJI_RE.findall(line)
    assert not any(ch.isdigit() for ch in line) and engagement_bait(line) == []
    assert drawable(line, "Montserrat") == line                             # every glyph is in the font
    assert not end_card.says_follow(line) and "share" not in line.lower()   # neither the Reel's asks
    lay = end_card.layout(line, 1920, 1080)
    assert len(lay["lines"]) == 1 and lay["share_font"].size >= 56          # one line at the 16:9 sizes


def test_the_16x9_screen_says_the_handoff_not_the_share_ask(short_end_screen):
    stats = {}
    last = list(end_card.frames(_plate(), SHARE, stats))[-1]
    assert stats["text"] == end_card.HANDOFF and stats["handoff"] is True
    assert stats["share_lines"] == [end_card.HANDOFF]
    assert stats["layout_problems"] == []
    assert all(v is not None and v >= brand.TEXT_CONTRAST for v in stats["contrast"].values())
    kept = {}
    other = list(end_card.frames(_plate(), SHARE, kept, handoff=False))[-1]
    assert kept["text"] == SHARE and kept["handoff"] is False
    assert np.abs(np.asarray(last, np.int16) - np.asarray(other, np.int16)).max() > 50   # a different line drawn
    assert last.size == (1920, 1080)


def test_the_9x16_card_keeps_the_share_ask():
    stats = {}
    for _ in end_card.frames(Image.new("RGB", (1080, 1920), (120, 110, 100)), SHARE, stats):
        pass
    assert stats["text"] == SHARE and "handoff" not in stats and stats["follow"] is True


# ---------------------------------------------------------------- the seam gate
def _dark_last():
    im = Image.new("RGB", (1080, 1920), (18, 16, 14))
    ImageDraw.Draw(im).rectangle([300, 700, 780, 1200], fill=(60, 50, 40))
    return im


def _bright_first():
    im = Image.new("RGB", (1080, 1920), (235, 228, 210))
    ImageDraw.Draw(im).rectangle([200, 600, 880, 1300], fill=(250, 210, 90))
    return im


def test_the_bridge_records_its_distance_from_frame_zero():
    stats = {}
    frames = list(end_card.loop_bridge(_dark_last(), _bright_first(), SHARE, stats, line=False))
    mad = np.abs(np.asarray(frames[-1], np.float32) - np.asarray(_bright_first(), np.float32)).mean()
    assert abs(stats["mad_last"] - mad) < 0.01
    assert 0 < stats["mad_last"] < end_card.LOOP_MAX_MAD


@pytest.mark.parametrize("line", [True, False], ids=["with_line", "visual"])
def test_a_clean_seam_passes_the_gate(line):
    out = end_card.check_loop_bridge(_dark_last(), _bright_first(), SHARE, line=line)
    assert out["ok"] is True and out["problems"] == [] and out["note"] is None
    assert out["stats"]["max_step_L"] <= end_card.LOOP_MAX_STEP_L
    assert out["stats"]["text"] == ("Send this to a space enthusiast you know." if line else None)


def test_a_hitching_seam_fails_closed_with_a_note(monkeypatch):
    monkeypatch.setattr(end_card, "LOOP_MAX_STEP_L", 0.5)
    monkeypatch.setattr(end_card, "LOOP_MAX_MAD", 0.5)
    out = end_card.check_loop_bridge(_dark_last(), _bright_first(), SHARE)
    assert out["ok"] is False and len(out["problems"]) == 2
    note = out["note"]
    assert note["type"] == "end_card" and note["severity"] == "warning" and "plain end card" in note["issue"]


def test_a_wrong_sized_frame_zero_fails_the_gate_rather_than_raise():
    out = end_card.check_loop_bridge(_dark_last(), _bright_first().resize((1200, 2100)), SHARE)
    assert out["ok"] is False and "1080x1920" in out["problems"][0] and out["note"] is not None


def test_seam_problems_reads_missing_or_bad_stats_as_problems():
    assert len(end_card.seam_problems({})) == 3
    good = {"max_step_L": 10.0, "mad_last": 3.0, "mix_last": round(29 / 30, 4)}
    assert end_card.seam_problems(good) == []
    assert end_card.seam_problems({**good, "mix_last": 1.0})                 # a duplicated frame 0
    assert end_card.seam_problems({**good, "mad_last": end_card.LOOP_MAX_MAD})
