"""The QF-29 proof fixtures for the golden-image mechanism (scripts/goldens.py): one still and one video sample,
rendered by the real design code from committed inputs. They are NOT frozen designs and are never in the store
(tests/goldens/): tests/test_qf29_golden_images.py and the QF-29 measure take their goldens in a temporary store.

A plant (environment variable QF29_PLANT, passed to the render subprocess) changes the design the way a later edit
would, to show the check catches it:
  shift    the design's text 1 px to the right (thumbnail: the left margin LEFT 96 -> 97; end card: the masthead)
  colour   one brand colour by six levels of blue (thumbnail: GOLD (242, 182, 48) -> (242, 182, 54); end card:
           BONE (243, 237, 226) -> (243, 237, 220))
  threads:N  (video) x264 on N threads (6 on a 4-core CI runner): another machine, the same design
"""
from __future__ import annotations

import itertools
import json
import os
from pathlib import Path

from scripts.goldens import Design

RUN = "tests/fixtures/ui02_review_v2/run_gol_gumbaz_whispering_gallery_1793278143/script_metadata.json"
PHOTO = "docs/reviews/archive-film-frame-2026-10-28/before_fill_crop.jpg"   # a real 9:16 archive-film frame
END_CARD_FRAMES = 15            # the first half second: the masthead fades in, the rule draws, the share line starts
END_CARD_SAMPLES = (7, 14)


def _plant() -> str:
    return os.environ.get("QF29_PLANT", "")


def _thumbnail(inputs: dict[str, Path], work: Path):
    from PIL import Image

    from src import brand
    from src import youtube_thumbnail as yt
    if _plant() == "shift":
        yt.LEFT += 1
    elif _plant() == "colour":
        brand.GOLD = (242, 182, 54)
    meta = json.loads(inputs[RUN].read_text(encoding="utf-8"))
    with Image.open(inputs[PHOTO]) as photo:
        im, _ = yt.render(photo.convert("RGB"), meta["thumbnail"])
    return {"thumbnail": im}


def _end_card(inputs: dict[str, Path], work: Path) -> Path:
    from PIL import Image

    from src import encoding, end_card, graphics
    plant = _plant()
    if plant == "shift":
        layout = end_card.layout

        def shifted(*a, **k):
            lay = layout(*a, **k)
            x, y = lay["masthead_xy"]
            lay["masthead_xy"] = (x + 1, y)
            return lay
        end_card.layout = shifted
    elif plant == "colour":
        end_card.WHITE = (243, 237, 220)   # BONE (243, 237, 226) with blue six levels down
    elif plant.startswith("threads:"):
        encoder, n = encoding.delivery_video_encoder, plant.split(":", 1)[1]
        encoding.delivery_video_encoder = lambda: [*encoder(), "-threads", n]
    meta = json.loads(inputs[RUN].read_text(encoding="utf-8"))
    with Image.open(inputs[PHOTO]) as photo:
        last = photo.convert("RGB")
    frames = itertools.islice(end_card.frames(last, end_card.card_text(meta["share_line"])), END_CARD_FRAMES)
    # graphics._encode: raw RGB through the production encoder (QF-134, delivery_video_encoder), BT.709 tagged
    return graphics._encode(frames, work / "end_card.mp4", END_CARD_FRAMES, end_card.WIDTH, end_card.HEIGHT)


DESIGNS = {
    "proof.youtube-thumbnail.gol-gumbaz.16x9": Design(
        name="proof.youtube-thumbnail.gol-gumbaz.16x9", surface="youtube thumbnail", case="gol gumbaz copy",
        format="16:9", kind="still", inputs=(RUN, PHOTO), render=_thumbnail),
    "proof.end-card.gol-gumbaz.9x16": Design(
        name="proof.end-card.gol-gumbaz.9x16", surface="end card (video)", case="gol gumbaz share line",
        format="9:16", kind="video", inputs=(RUN, PHOTO), render=_end_card, samples=END_CARD_SAMPLES),
}
