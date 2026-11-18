"""Board composition speed (cycle 0 round 8, lane BOARD): exact memoisation only, never a different picture.

Profile (round 7, C:\\tmp\\board-c0r7\\*-compose.log and surfaces.json "seconds"; cProfile of one 16:9 end screen in
round 8): the board spent ~2.5 h per palette, and about 80 % of it in one place. end_card.frames gates every share
word, the follow line and the credits line on EVERY opaque frame (end_card._gate_lines), and it rebuilds the card's
ground (two Lanczos camera pushes and a float blend of the whole frame, 0.34 s) once per line per frame: 61 ground
builds for 60 judgements (0.047 s each). Row 2 drew each 16:9 end screen for 710 s to keep 24 frames; the endcard job
of render_surfaces.py took 820 s per 9:16 case and 230 s per 16:9 case.

install() wraps end_card._gate_lines so that, within one call, the ground of a frame is built once and handed to every
line's judgement (a dict keyed by the frame's time). ground(t) is a pure function of t inside one frames() call and
masthead.judge never writes into the image it is given (TextPlate.draw composites onto a copy), so every judgement,
every refusal and every pixel is the same as without it (board_speed.check() proves it on one card, frame by frame).
The memo lives for one _gate_lines call: at most one card's opaque frames (<= 300 x 6.2 MB on 16:9), freed after.
"""
from __future__ import annotations

import hashlib
from typing import Any


def install() -> None:
    """Memoise the card ground inside end_card._gate_lines (idempotent)."""
    from src import end_card
    real = end_card._gate_lines
    if getattr(real, "board_memo", False):
        return

    def gate_lines(ground, *a: Any, **k: Any):
        memo: dict[float, Any] = {}

        def once(t: float):
            im = memo.get(t)
            if im is None:
                im = memo[t] = ground(t)
            return im
        try:
            return real(once, *a, **k)
        finally:
            memo.clear()
    gate_lines.board_memo = True
    gate_lines.__wrapped__ = real
    end_card._gate_lines = gate_lines


def uninstall() -> None:
    from src import end_card
    real = getattr(end_card._gate_lines, "__wrapped__", None)
    if real is not None:
        end_card._gate_lines = real


def check(last, share: str) -> dict[str, Any]:
    """The same card drawn with and without the memo: each frame's sha256 and the stats, equal or not."""
    from src import end_card

    def run():
        stats: dict[str, Any] = {}
        h = [hashlib.sha256(f.tobytes()).hexdigest() for f in end_card.frames(last, share, stats)]
        return h, stats
    uninstall()
    plain, s0 = run()
    install()
    fast, s1 = run()
    return {"frames": len(plain), "frames_equal": plain == fast,
            "stats_equal": repr(sorted(s0.items())) == repr(sorted(s1.items()))}
