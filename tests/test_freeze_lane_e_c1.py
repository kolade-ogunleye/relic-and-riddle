"""Freeze lane E, cycle 1 (end card 9:16, loop bridge, 16:9 end screen).

The 9:16 end card's lines read at the reading pace (operator, 2026-11-19: 17 cps stays the target; the share line is not
a caption, its hold is a design choice): the card holds, in whole frames, until the share line and the follow line have
been read once at tok("motion.caption_cps"), bounded by the room the release gate leaves after the narration, where it
takes only a shorter VERIFIED line (the fallback's share half) and otherwise records the pace. One reveal timeline for
frames() and its gates, held equal to the written motion.* tokens until they drive (queue SYS-E-1)."""
import inspect
import math
from types import SimpleNamespace

import pytest

from src import end_card
from src.brand_system import SPEC, tok
from src.script_engine import SHARE_FALLBACK, SHARE_LINE_MAX

FALLBACK = end_card.card_text(SHARE_FALLBACK)
WORDS = ("Send this to the friend who reads every plaque in every museum and still wants to know more about "
         "how ancient builders moved stone").split()


def _line(n_chars: int) -> str:
    """A share half of about n_chars (whole words), ending in a full stop."""
    out = []
    for w in WORDS:
        if len(" ".join(out + [w])) + 1 > n_chars:
            break
        out.append(w)
    return " ".join(out) + "."


def test_reading_is_sequential_and_never_starts_before_a_line_shows():
    cps = tok("motion.caption_cps")
    assert end_card.reading_need([(0.5, "x" * 17)]) == pytest.approx(0.5 + 17 / cps)
    # the second line shows late: it is read from when it shows
    assert end_card.reading_need([(0.0, "x" * 17), (3.0, "y" * 17)]) == pytest.approx(3.0 + 1.0 * 17 / cps)
    # the second line shows early: it is read after the first
    assert end_card.reading_need([(0.0, "x" * 34), (0.5, "y" * 17)]) == pytest.approx(3 * 17 / cps)
    assert end_card.reading_need([(0.4, "  "), (1.0, "")]) == 0.0


@pytest.mark.parametrize("n", [20, 34, 40, 49, 56, 70, SHARE_LINE_MAX])
def test_the_card_holds_until_its_lines_read(n):
    """Every share half the writer's limit allows (real lines: 34-56 characters on 68 dev and production runs), with
    the follow line the card adds: held in whole frames to the reading need, never under SECONDS."""
    share = _line(n)
    text, lay, seconds, rec = end_card.held_layout(share, end_card.WIDTH, end_card.HEIGHT)
    need = end_card.reading_need(end_card.card_lines(lay))
    assert text == share and rec["line"] == "as given"
    assert seconds == pytest.approx(max(end_card.SECONDS, math.ceil(round(need * end_card.FPS, 6)) / end_card.FPS))
    assert round(seconds * end_card.FPS) == pytest.approx(seconds * end_card.FPS, abs=1e-6)   # whole frames
    assert not rec["over_pace"] and rec["shown_cps"] <= tok("motion.caption_cps") + 0.05
    assert lay["follow"] and end_card.card_lines(lay)[1][1] == end_card.brand.FOLLOW_LINE


def test_the_end_screen_keeps_its_length_and_reads():
    """16:9: the hand-off, WATCH NEXT and the credits line read well inside END_SCREEN_SECONDS (YouTube's element
    template), so its length does not move."""
    from src import brand
    for credits in (False, True):
        text, lay, seconds, rec = end_card.held_layout(brand.HANDOFF_LINE, 1920, 1080, credits=credits)
        assert seconds == end_card.END_SCREEN_SECONDS and not rec["over_pace"]
    text, lay, seconds, rec = end_card.held_layout(_line(SHARE_LINE_MAX), 1920, 1080)   # handoff=False's longest
    assert seconds == end_card.END_SCREEN_SECONDS and not rec["over_pace"]


def test_the_room_bounds_the_card_and_only_a_verified_line_replaces_the_writers():
    long_line = _line(SHARE_LINE_MAX)
    _, lay, free, _ = end_card.held_layout(long_line, end_card.WIDTH, end_card.HEIGHT)
    _, f_lay, f_need, _ = end_card.held_layout(FALLBACK, end_card.WIDTH, end_card.HEIGHT)
    assert f_need < free
    # room for the fallback, not for the writer's longer line: the fallback's share half, never new wording
    text, _, seconds, rec = end_card.held_layout(long_line, end_card.WIDTH, end_card.HEIGHT, room=f_need)
    assert text == FALLBACK and seconds == f_need and not rec["over_pace"] and "fallback" in rec["line"]
    # room for neither: the writer's line stays, the card lasts the room, and the pace is recorded (not failed)
    text, _, seconds, rec = end_card.held_layout(long_line, end_card.WIDTH, end_card.HEIGHT, room=3.5)
    assert text == long_line and seconds <= 3.5 and rec["over_pace"] and rec["shown_cps"] > tok("motion.caption_cps")
    # a line shorter than the fallback is never swapped for it
    short = _line(30)
    text, _, _, rec = end_card.held_layout(short, end_card.WIDTH, end_card.HEIGHT, room=end_card.SECONDS)
    assert text == short and rec["line"] == "as given"
    # room enough: nothing changes
    assert end_card.held_layout(long_line, end_card.WIDTH, end_card.HEIGHT, room=60.0)[2] == free


def test_the_room_is_what_the_release_gate_leaves():
    from src import config
    assert end_card.card_room(45.0) == pytest.approx(config.RELEASE_DURATION_MAX - 45.0)
    assert end_card.card_room(config.RELEASE_DURATION_MAX + 1) == 0.0


def test_one_reveal_timeline_read_from_the_motion_tokens():
    """Since SYS-E-1 the end card's reveal names are the system's motion tokens, read now (a brand that sets one moves
    the name: tests/test_freeze_sys_2.py measures the frames), and frames() and _gate_lines() read no inline
    literal."""
    from src.brand_system import tok
    assert end_card.MAST_IN == tok("motion.reveal")
    assert end_card.WORD_STAGGER == tok("motion.word_stagger")
    assert end_card.SHARE_RISE == tok("motion.rise_l")
    assert end_card.FOLLOW_RISE == end_card.LOOP_FOLLOW_RISE == tok("motion.rise_m")
    assert end_card.LOOP_CROSSFADE == (tok("motion.loop_crossfade"), tok("motion.loop_crossfade_visual"))
    assert end_card.LOOP_PUSH == tok("motion.push_per_frame") and end_card.LOOP_PUSH_CAP == tok("motion.push_cap")
    assert not {k for k in SPEC if k in ("motion.reveal", "motion.word_stagger", "motion.rise_l", "motion.rise_m")}
    for fn in (end_card.frames, end_card._gate_lines):
        src = inspect.getsource(fn)
        for literal in ("0.055", "0.45 ", "(t - 0.15)", "BELL_AT - 0.35", "round(14 *", "round(10 *"):
            assert literal not in src, (fn.__name__, literal)
    assert end_card.share_done(9) == pytest.approx(end_card.word_at(8) + end_card.WORD_IN)


def test_the_bridge_follow_reads_at_the_pace():
    total = round(end_card.LOOP_SECONDS * end_card.FPS)
    up = [i for i in range(total) if end_card.follow_alpha(i, total) >= 1.0]
    assert end_card.reading_need([(0.0, end_card.LOOP_FOLLOW)]) <= (up[-1] + 1) / end_card.FPS


def test_the_line_gate_draws_each_ground_once_and_refuses_by_line(monkeypatch):
    from src import masthead
    calls = []

    def ground(t):
        calls.append(round(t * end_card.FPS))
        return t

    def plate(text, ratio, box=(300, 900, 500, 960)):
        return SimpleNamespace(text=text, ratio=ratio, box=box, empty=False)
    # from frame 100 the word "b" and the follow line read 1:1 (under the gate); "a" holds throughout
    monkeypatch.setattr(masthead, "judge",
                        lambda g, p: p.ratio if round(g * end_card.FPS) < 100 or p.text == "a" else 1.0)
    total = 120
    words = [plate("a", 9.0), plate("b", 9.0)]
    follow = plate("FOLLOW", 9.0)
    refused = end_card._gate_lines(ground, total, end_card.WIDTH, end_card.HEIGHT, False, words, follow, None)
    assert list(refused) == ["share", "follow"]          # "b" and the follow line fail from frame 100: both refused
    assert "'b'" in refused["share"] and "frame 100" in refused["share"]
    assert len(calls) == len(set(calls))                # one ground per frame, never one per plate
    first = min(int(-(-(end_card.word_at(0) + end_card.WORD_IN) * end_card.FPS // 1)), total)
    assert calls[0] == first and calls[-1] == 100       # nothing left to judge once both are refused
