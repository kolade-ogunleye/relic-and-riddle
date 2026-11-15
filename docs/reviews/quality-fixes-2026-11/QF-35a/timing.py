"""Runtime cost of reading the brand at call time: a few default renders timed on one tree.

    python timing.py <tree> [repeats]

Run it on the base tree and on this row's tree in turn (alternating, so a busy machine weighs on both alike) and
compare each surface's best time. Inputs are committed: the Richat still of the freeze-panel fixtures as the ground
of the end card (90 frames at 9:16) and of a chapter card (the card tests' act 5 title, every frame, signed with
the tests' throwaway key in this process only), the cross-section test spec (9:16, frames hashed where they would
be encoded, no encoder) and one real narration built into a 9:16 caption file. Prints JSON: per surface the best
and the median of `repeats` (default 3) runs after one warm-up run, in seconds, and the frames drawn.
"""
import json
import os
import socket
import statistics
import sys
import tempfile
import time
from pathlib import Path

FIX = Path("docs") / "reviews" / "freeze-panel-2026-11-04" / "fixtures"


def _offline(*_a, **_k):
    raise RuntimeError("timing.py is offline")


def main(root: Path, repeats: int = 3) -> None:
    root = root.resolve()
    os.environ.update(FONT_SIZE="80", PYTHONIOENCODING="utf-8")
    socket.create_connection = _offline
    socket.socket.connect = _offline
    sys.path.insert(0, str(root))
    from PIL import Image

    from src import config, end_card, graphics
    from src import design_cards as dc
    from src import video_composer as vc
    config.FACT_RECORD_KEY = "test-only-fact-record-key-0123456789abcdef"
    from tests import test_freeze_cards_2026_11_04 as fc
    from tests.test_diagrams_2026_11_02 import SECTION
    still = root / FIX / "stills" / "richat_structure_sahara_eye_01_commons_960.jpg"
    ground = Image.open(still).convert("RGB").resize((1080, 1920))
    words = json.loads((root / FIX / "runs" / "run_amber_room_heist_1792790550" / "words.json").read_text(
        encoding="utf-8"))
    drawn = {}

    def encode(frames, path, n, **_k):
        drawn["cross_section 916"] = sum(1 for _ in frames)
        return Path(path)
    graphics._encode = encode
    with tempfile.TemporaryDirectory(prefix="qf35a-timing-") as tmp:
        def end():
            drawn["endcard 916"] = sum(1 for _ in end_card.frames(ground, "Send this to someone who loves a mystery.", {}))

        def card():
            plan = fc.chapter_plan(fc.TALL, 5, fc.ACTS, fc.RICHAT_ACTS[4], photo=still)
            drawn["card chapter_act5 916"] = sum(1 for k in range(plan.frames) if dc.frame_at(plan, k))

        def section():
            graphics.render_cross_section(SECTION, Path(tmp) / f"s{time.perf_counter_ns()}.mp4", width=1080, height=1920)

        def captions():
            vc.VideoComposer().build_subtitles(words, Path(tmp) / f"c{time.perf_counter_ns()}.ass")
            drawn["captions 916 (file)"] = 0
        jobs = {"endcard 916": end, "card chapter_act5 916": card, "cross_section 916": section,
                "captions 916 (file)": captions}
        out = {}
        for name, job in jobs.items():
            job()
            times = []
            for _ in range(repeats):
                t0 = time.perf_counter()
                job()
                times.append(time.perf_counter() - t0)
            out[name] = {"best_s": round(min(times), 3), "median_s": round(statistics.median(times), 3),
                         "frames": drawn[name]}
    print(json.dumps(out, indent=1, sort_keys=True))


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit("usage: timing.py <tree> [repeats]")
    main(Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) == 3 else 3)
