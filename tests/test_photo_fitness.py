"""The photo-fitness rule (design-freeze cycle 5, PHOTO stream; src/photo_fitness.py): one measure of whether a
photograph is fit for the window it is put in, asked by every picker and cropper.

Fixtures are drawn here (no run file, no workstation path): a textured ground with one salient subject, the frames
a source can carry (a scan border, a letterbox, a mount's corners) and the scene black that is not one (space above a
limb, a black ground, a window's wedge, an oval). Each test holds one class of the panel-6 ledger closed: PHOTO-01
(no pad), PHOTO-02 (no frame in a crop), PHOTO-03 (the subject stays in the window), PHOTO-04 (nothing foreign under
the masthead), PHOTO-06 (an unregistered pair is recorded), PHOTO-07 (no repeated photograph).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageOps

from src import brand
from src import photo_fitness as pf

ROOT = Path(__file__).resolve().parents[1]


def ground(w: int, h: int, seed: int = 0, level: int = 150, grain: int = 5) -> np.ndarray:
    """A ground with no subject: evenly textured (rock, scrub: a coarse mottle the eye and the block measure both
    see), or with `grain` 1 a flat field (sand, sky)."""
    rng = np.random.default_rng(seed)
    a = np.full((h, w, 3), (level, level - 20, level - 50), np.float32)
    a += rng.normal(0, grain, (h, w, 1)).astype(np.float32)
    if grain > 1:
        coarse = rng.normal(0, 4 * grain, (h // 12 + 1, w // 12 + 1)).astype(np.float32)
        mottle = np.asarray(Image.fromarray(coarse, "F").resize((w, h), Image.BILINEAR), np.float32)
        a += mottle[..., None]
    return a


def with_subject(a: np.ndarray, cx: float, cy: float, r: float, seed: int = 1) -> np.ndarray:
    """`a` with a detailed, differently coloured disc (concentric rings) at (cx, cy) of the picture, radius r of its
    height: the one thing in the picture."""
    h, w = a.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    d = np.hypot(xx - cx * w, yy - cy * h)
    inside = d < r * h
    rings = (np.sin(d / max(2.0, r * h / 14)) > 0)
    out = a.copy()
    out[inside & rings] = (40, 90, 130)
    out[inside & ~rings] = (210, 120, 100)
    return out


def save(tmp: Path, name: str, a: np.ndarray) -> Path:
    p = tmp / name
    Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).save(p, "PNG")
    return p


def wedge(a: np.ndarray) -> np.ndarray:
    """A spacecraft window's black wedge down the left edge and across the lower-left corner (digital black)."""
    h, w = a.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    out = a.copy()
    out[xx < 0.04 * w + 0.16 * w * np.clip((yy - 0.55 * h) / (0.45 * h), 0, 1)] = 0
    return out


def border(a: np.ndarray, left: float = 0.075, bottom: float = 0.10) -> np.ndarray:
    """A scan's or plate's border: straight digital-black bands down the left side and across the foot, the picture's
    content stopping at a hard straight edge (the source's FRAME)."""
    h, w = a.shape[:2]
    out = a.copy()
    out[:, :int(left * w)] = 0
    out[int((1 - bottom) * h):] = 0
    return out


def limb(a: np.ndarray) -> np.ndarray:
    """Black space above a planet's curved limb (an orbital photograph): digital black along the top whose inner edge
    is a curve the picture's content meets: the photograph's SCENE, not its frame."""
    h, w = a.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    out = a.copy()
    out[yy < 0.22 * h + 0.25 * h * ((xx - w / 2) / (w / 2)) ** 2] = 0
    return out


def oval(a: np.ndarray) -> np.ndarray:
    """A black oval surround: the picture inside an ellipse that cuts every corner."""
    h, w = a.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    out = a.copy()
    out[((xx - w / 2) / (0.56 * w)) ** 2 + ((yy - h / 2) / (0.60 * h)) ** 2 > 1] = 0
    return out


# ---------------------------------------------------------------- the measures
def test_a_plain_photograph_is_fit_and_its_crop_is_the_one_asked(tmp_path):
    p = save(tmp_path, "plain.png", with_subject(ground(1600, 1200), 0.5, 0.5, 0.25))
    rec = pf.place(p, pf.Window((1080, 1350)))
    assert rec["fit"] and not rec["moved"] and rec["problems"] == []
    assert rec["matte"] == 0 and rec["box"] == [round(v, 1) for v in pf.fit_box((1600, 1200), (1080, 1350))]
    image, got = pf.fill(p, pf.Window((1080, 1350)))
    from src.typography import cover_crop
    assert np.array_equal(np.asarray(image), np.asarray(cover_crop(p, (1080, 1350)).convert("RGB")))   # same pixels


def test_the_frame_is_found_and_the_content_box_is_a_whole_rectangle_of_picture(tmp_path):
    a = border(with_subject(ground(1600, 1200), 0.5, 0.5, 0.2))
    im = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    an = pf.analyse(im)
    assert an.matte_share > 0.1
    x0, y0, x1, y1 = an.content_box
    inner = np.asarray(im.crop((x0 + 8, y0 + 8, x1 - 8, y1 - 8)).convert("L"))
    assert (inner <= pf.MATTE_LUMA).mean() < 0.002                 # no frame inside the content box
    assert abs(x0 - 120) <= 8 and abs(y1 - 1080) <= 8 and y0 == 0 and x1 == 1600
    letter = np.zeros((1200, 1600, 3), np.float32)
    letter[200:1000] = with_subject(ground(1600, 800), 0.5, 0.5, 0.3)
    an = pf.analyse(Image.fromarray(letter.astype(np.uint8)))
    assert abs(an.content_box[1] - 200) <= 8 and abs(an.content_box[3] - 1000) <= 8
    dark = ground(1600, 1200, level=60)   # a photographed dark ground is not a frame
    dark[:300] = np.clip(ground(1600, 300, seed=3, level=30, grain=6), 6, 255)
    assert pf.analyse(Image.fromarray(dark.astype(np.uint8))).matte_share == 0


def test_scene_black_is_not_a_frame():
    """Review of cycle 5, B1: digital black the picture's content runs into, or whose inner edge is not a straight
    full-length band, is the photograph's SCENE (space above the Earth's limb, a specimen on a black ground, a
    spacecraft window's curved wedge, an oval surround, the black round an arched window): no window moves for it."""
    subject = with_subject(ground(1600, 1200), 0.5, 0.6, 0.2)
    h, w = 1200, 1600
    yy, xx = np.mgrid[0:h, 0:w]
    studio = subject.copy()
    studio[np.hypot((xx - w / 2) / (0.42 * w), (yy - h / 2) / (0.40 * h)) > 1] = 0   # a specimen on a black ground
    arch = subject.copy()
    arch[(yy < 0.3 * h) & (np.hypot(xx - w / 2, yy - 0.3 * h) > w / 2)] = 0          # an arched window's spandrels
    for name, a in (("limb", limb(subject)), ("wedge", wedge(subject)), ("oval", oval(subject)),
                    ("studio", studio), ("arch", arch)):
        an = pf.analyse(Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)))
        assert an.matte_share == 0, name
    # a mount's rounded corners are a frame
    corners = subject.copy()
    r = 60
    for cx, cy in ((r, r), (w - r, r), (r, h - r), (w - r, h - r)):
        q = ((xx < r) if cx == r else (xx >= w - r)) & ((yy < r) if cy == r else (yy >= h - r))
        corners[q & (np.hypot(xx - cx, yy - cy) > r)] = 0
    assert pf.analyse(Image.fromarray(np.clip(corners, 0, 255).astype(np.uint8))).matte_share > 0


def test_a_story_window_over_black_space_keeps_its_subject(tmp_path):
    """B1 on a Story frame (the Richat Eye under black space and a spacecraft): the window asked is drawn, and no
    move without a reviewed subject box leaves the asked window's centre."""
    a = with_subject(ground(2150, 1856, grain=3), 0.36, 0.72, 0.12)      # the crater, low and left of centre
    h, w = a.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    space = yy < 0.28 * h + 0.2 * h * ((xx - w / 2) / (w / 2)) ** 2
    lit = a.copy()
    a[space] = 0                                                             # space above the limb
    a[(xx < 0.35 * w) & (yy < 0.30 * h)] = (200, 200, 210)                  # a spacecraft in it, bright and hard
    p = save(tmp_path, "orbit.png", a)
    rec = pf.place(p, pf.Window((1080, 1920), name="story"), (0.5, 0.5))
    asked = pf.fit_box((w, h), (1080, 1920))
    assert not rec["moved"] and rec["box"] == [round(v, 1) for v in asked] and rec["matte"] == 0
    # with a real frame (a straight border down the right), the move is taken and holds the asked centre
    lit[:, int(0.95 * w):] = 0
    framed = pf.analyse(Image.fromarray(np.clip(lit, 0, 255).astype(np.uint8)))   # (a file's clean region would
    got = pf.place(None, pf.Window((1080, 1920), name="story"), (1.0, 0.5), analysis=framed)   # trim the band)
    ask = pf.fit_box((w, h), (1080, 1920), (1.0, 0.5))
    cx, cy = (ask[0] + ask[2]) / 2, (ask[1] + ask[3]) / 2
    hw, hh = (ask[2] - ask[0]) * pf.MOVE_HOLD / 2, (ask[3] - ask[1]) * pf.MOVE_HOLD / 2
    assert got["moved"] and got["matte"] <= pf.MATTE_MAX
    assert got["basis"] in ("minimal frame move", "asked centre held")
    b = got["box"]
    assert b[0] <= cx - hw + 1 and b[2] >= min(cx + hw, int(0.95 * w)) - 2 and b[1] <= cy - hh + 1
    assert b[3] >= cy + hh - 1
    if got["fit"]:
        assert got["kept_asked"] >= pf.MOVE_KEEP


@pytest.mark.parametrize("case", json.loads((ROOT / "tests/fixtures/photo_fitness_c5/cases.json").read_text(
    encoding="utf-8")), ids=lambda c: c["name"])
def test_the_reviews_story_frames_keep_their_subject(case):
    """Review of cycle 5, B1, on the real photographs (kept at the measure's own 256 px; the stained glass as the
    silhouette of its digital black only): the two Richat Story 03 frames moved off black space, the Eye left the
    picture and the record said fit; the stained-glass window's crop moved down and cut the figure's head. The Richat
    photographs hold no frame and are drawn as asked; the window's straight border is a frame, and the crop that
    clears it holds the asked window's centre."""
    rgb = np.asarray(Image.open(ROOT / "tests/fixtures/photo_fitness_c5" / f"{case['name']}.png").convert("RGB"))
    an = pf.analyse_maps(rgb, tuple(case["size"]))
    window = pf.Window((1080, 1920), name="story")
    rec = pf.place(None, window, tuple(case["centering"]), analysis=an)
    asked = pf.fit_box(an.size, window.size, tuple(case["centering"]))
    if case["name"].startswith("richat"):
        assert an.matte_share == 0 and not rec["moved"] and rec["box"] == [round(v, 1) for v in asked]
        return
    assert an.matte_share > 0
    if not rec["moved"]:     # no crop clears the border and keeps the asked view: the asked one, recorded unfit
        assert rec["box"] == [round(v, 1) for v in asked] and not rec["fit"] and rec["problems"] == ["matte"]
        return
    assert rec["basis"] in ("minimal frame move", "asked centre held") and rec["fit"]
    cx, cy = (asked[0] + asked[2]) / 2, (asked[1] + asked[3]) / 2
    hw, hh = (asked[2] - asked[0]) * pf.MOVE_HOLD / 2, (asked[3] - asked[1]) * pf.MOVE_HOLD / 2
    x0, y0, x1, y1 = an.content_box
    b = rec["box"]
    assert b[0] <= max(cx - hw, x0) + 1 and b[2] >= min(cx + hw, x1) - 1
    assert b[1] <= max(cy - hh, y0) + 1 and b[3] >= min(cy + hh, y1) - 1


def test_the_crop_that_clears_a_frame_keeps_the_subject_in_view(tmp_path):
    """PHOTO-03, the regression this stream exists for: lane D cycle 4 moved a slide's window off a black wedge and
    onto bare sand, and nothing measured the subject. The subject sits low and left, beside the wedge."""
    a = border(with_subject(ground(1600, 1600), 0.36, 0.70, 0.13))
    p = save(tmp_path, "border.png", a)
    window = pf.Window((1440, 1100), name="split")
    an = pf.analysis_of(p)
    asked = pf.place(p, window, fixed=True)
    assert "matte" in asked["problems"]                          # the centred window shows the border
    rec = pf.place(p, window)
    assert rec["fit"] and rec["moved"] and rec["matte"] <= pf.MATTE_MAX
    assert rec["keep"] >= pf.SUBJECT_KEEP and rec["density"] >= pf.DENSITY_MIN
    x0, y0, x1, y1 = rec["box"]
    assert x0 < 0.36 * 1600 - 150 and x1 > 0.36 * 1600 + 150 and y0 < 0.70 * 1600 - 150 and y1 > 0.70 * 1600 + 150
    # the window lane D's rule took (the frame cleared, the picture's centre held, the subject not asked about):
    # top-right, on the bare ground. The measure refuses it.
    lost = (1600 - 1440 * 0.5, 0.0, 1600.0, 1100 * 0.5)
    bad = pf.measure(an, lost, window, an.total("salience", tuple(rec["box"])))
    assert "subject" in pf.problems(bad, window) or "flat" in pf.problems(bad, window)


def test_a_flat_view_and_an_empty_view_are_unfit(tmp_path):
    a = with_subject(ground(1200, 2000, grain=1), 0.5, 0.88, 0.07)     # the subject at the foot, under the text
    p = save(tmp_path, "low.png", a)
    full = pf.Window((1080, 1350), view=(0, 0, 1080, 560), name="full bleed, type at the foot")
    asked = pf.place(p, full, fixed=True)
    assert {"flat", "subject"} & set(asked["problems"])
    # salience is no subject detector: what it suggests is recorded and chooses between photographs (rank, the deck's
    # photo guard); alone it moves no window (a move made for it took a Story frame to an aircraft's wing)
    rec = pf.place(p, full)
    assert not rec["fit"] and not rec["moved"] and rec["box"] == asked["box"]
    assert set(rec["problems"]) <= set(pf.SOFT) and "rule: a photograph is fit" in pf.reason(rec, "the slide")
    other = save(tmp_path, "mid.png", with_subject(ground(1200, 2000, seed=3), 0.5, 0.3, 0.12))
    assert [q.name for q, _r in pf.rank([p, other], full)] == ["mid.png", "low.png"]   # the picker takes the fit one


def test_salience_alone_moves_no_window():
    """Decision D1 (review of cycle 5, D4): the soft measures of an unreviewed photograph are recorded and never alone
    the cause of a move, even where a nearer crop would hold more of what salience reads as the subject (a move made
    for it took a Story frame to the aircraft's wing at the photograph's edge)."""
    im = Image.fromarray(np.clip(with_subject(ground(3000, 1500, grain=2), 0.25, 0.5, 0.15), 0, 255).astype(np.uint8))
    an = pf.analyse(im)
    window = pf.Window((1080, 1350), name="slide")
    rec = pf.place(None, window, analysis=an)
    assert rec["problems"] == ["subject"]                      # unfit on salience only (no frame, not flat)
    assert not rec["moved"] and rec["box"] == [round(v, 1) for v in pf.fit_box(an.size, window.size)]
    # the move it did not take exists and is FIT: a crop holding the asked centre with the subject in view (the
    # rule reverted, `due` = every problem, takes it)
    alt = pf.place(None, window, (0.375, 0.5), analysis=an, fixed=True)
    assert alt["fit"] and alt["box"][0] >= rec["box"][0] - (rec["box"][2] - rec["box"][0]) * pf.MOVE_HOLD / 2 - 1


def _drawn(a: np.ndarray) -> np.ndarray:
    return np.clip(a, 0, 255).astype(np.uint8)


def test_frames_are_judged_by_shape_and_content():
    """Second review, B5 / B6: a frame is a hard cut band along the sides, bounded in share, paired or adjacent when
    deep; the reviewer's constructed cases (drawn here with the same geometry) and three real frames the cycle's
    first rule missed (kept as the silhouettes of their dark pixels)."""
    w, h = 3000, 2000
    yy, xx = np.mgrid[0:h, 0:w]
    land = ground(w, h, seed=7)
    night = land.copy()                                       # C1: black sky over a straight horizon, a disc in it
    night[:1100] = 0
    night = with_subject(night, 0.53, 0.25, 0.07)
    shelf = np.zeros((h, w, 3), np.float32)                    # C5b: an object on a shelf against a black ground
    shelf[1500:] = ground(w, 500, seed=8)
    shelf[:1500, 1330:1670] = ground(340, 1500, seed=9, level=180)
    table = land.copy()                                       # C4: a black table across the foot
    table[1450:] = 0
    letter = np.zeros((int(w * 9 / 16), w, 3), np.float32)    # C3: a 2.39:1 still letterboxed in 16:9
    sh = int(w / 2.39)
    letter[(letter.shape[0] - sh) // 2:(letter.shape[0] - sh) // 2 + sh] = ground(w, sh, seed=10)
    pillar = np.zeros((int(w * 9 / 16), w, 3), np.float32)    # C3b: a portrait pillarboxed in 16:9
    pillar[:, 985:2015] = with_subject(ground(1030, pillar.shape[0], seed=11), 0.5, 0.4, 0.15)
    for name, a, framed in (("night sky", night, False), ("shelf on black", shelf, False), ("black table", table, False),
                            ("letterbox", letter, True), ("pillarbox", pillar, True)):
        an = pf.analyse(Image.fromarray(_drawn(a)))
        assert (an.matte_share > 0) == framed, (name, an.matte_share)
    an = pf.analyse(Image.fromarray(_drawn(pillar)))
    assert abs(an.content_box[0] - 985) <= 16 and abs(an.content_box[2] - 2015) <= 16
    # the ground under a card (frame-only window) keeps the night sky's disc: no frame, no move
    ground_rec = pf.place(None, pf.Window((1920, 1080), checks=("matte",)), analysis=pf.analyse(Image.fromarray(
        _drawn(night))))
    assert not ground_rec["moved"] and ground_rec["box"][1] < 0.25 * h
    import json as _json
    fx = ROOT / "tests/fixtures/photo_fitness_c5"
    for case in _json.loads((fx / "frames.json").read_text(encoding="utf-8")):
        an = pf.analyse(Image.open(fx / case["file"]).convert("RGB"))
        _, sides = pf.frame_bands(np.asarray(an.thumb.convert("L"), np.float32))
        assert an.matte_share > 0 and set(case["sides"]) <= set(sides), (case["name"], sides)
        story = pf.place(None, pf.Window((1080, 1920), name="story"), analysis=an)
        assert not (story["fit"] and story["matte"] > pf.MATTE_MAX), case["name"]   # never fit over a frame


@pytest.mark.parametrize("case", json.loads((ROOT / "tests/fixtures/photo_fitness_c5/frame_moves.json").read_text(
    encoding="utf-8")), ids=lambda c: c["name"])
def test_a_move_off_a_frame_is_the_minimal_one(case):
    """Third review, B7: a window moved only to clear the source's frame is the nearest crop that clears it (shifted
    along the frame's axis just far enough, or made closer about the asked centre), never the most salient one: the
    salient choice slid Richat 14's Story 230 px sideways off a 15 px scan edge and cut the Eye's ring, halved a
    Memnon colossus and cut the Voynich plant."""
    thumb = Image.open(ROOT / "tests/fixtures/photo_fitness_c5" / case["file"]).convert("RGB")
    w0, h0 = case["size"]                                  # the photograph's own size (kept at THUMB px here)
    k = pf.ANALYSIS / max(w0, h0)
    rgb = np.asarray(thumb.resize((max(1, round(w0 * k)), max(1, round(h0 * k))), Image.Resampling.BOX))
    an = pf.analyse_maps(rgb, (w0, h0), thumb=thumb)
    assert an.matte_share > 0
    x0, y0, x1, y1 = an.content_box
    moved = 0
    for window in (pf.Window((1080, 1920), name="story"),
                   pf.Window((1080, 1920), checks=("matte", "subject", "flat"), name="hook ground"),
                   pf.Window((1080, 1920), checks=("matte",), name="9:16 ground")):
        rec = pf.place(None, window, analysis=an)
        asked = pf.fit_box(an.size, window.size)
        if not rec["moved"]:
            assert "matte" in rec["problems"], (case["name"], window.name)
            continue
        moved += 1
        assert rec["basis"] == "minimal frame move" and rec["matte"] <= pf.MATTE_MAX
        b = rec["box"]
        w, h = asked[2] - asked[0], asked[3] - asked[1]
        need_x = max(0.0, x0 - asked[0]) + max(0.0, asked[2] - x1)
        need_y = max(0.0, y0 - asked[1]) + max(0.0, asked[3] - y1)
        dx = abs((b[0] + b[2]) / 2 - (asked[0] + asked[2]) / 2)
        dy = abs((b[1] + b[3]) / 2 - (asked[1] + asked[3]) / 2)
        assert dx <= need_x + 0.01 * w and dy <= need_y + 0.01 * h, (case["name"], window.name, b, asked)
    assert moved                                            # each photograph's frame is cleared on some window


def test_every_move_keeps_most_of_what_was_asked_for():
    """Second review, B5: a frame-only window (typography.cover_crop's, under cards and graphics) moved off a frame
    with no keep check and lost the subject; every window keeps MOVE_KEEP of the asked view's salience or stays."""
    a = ground(3000, 1500)
    a[:250] = 0                                               # a letterbox: straight black bands, head and foot
    a[-250:] = 0
    a = with_subject(with_subject(a, 1100 / 3000, 0.5, 0.03), 1900 / 3000, 0.5, 0.03, 2)   # at the asked edges
    an = pf.analyse(Image.fromarray(_drawn(a)))
    assert an.matte_share > 0.3
    window = pf.Window((1080, 1920), checks=("matte",), name="9:16 ground")
    rec = pf.place(None, window, analysis=an)
    # the crop that clears the bands (within the enlargement limit, holding the asked centre) keeps 0.64 of the
    # asked view: under MOVE_KEEP (0.70), so the asked window stays and is recorded with its frame
    assert not rec["moved"] and rec["problems"] == ["matte"]


def test_a_blurred_ground_records_its_fit(tmp_path):
    """Second review follow-up: the ground under a card or a graphic (typography.cover_crop) writes its fit record
    like every other window: recorded unfit, with the reason, when no crop clears the frame and keeps MOVE_KEEP; the
    clip's contrast record (graphics.record_contrast) carries it."""
    from src import graphics
    from src.typography import cover_crop
    a = ground(3000, 1500)
    a[:250] = 0
    a[-250:] = 0
    a = with_subject(with_subject(a, 1100 / 3000, 0.5, 0.03), 1900 / 3000, 0.5, 0.03, 2)
    p = save(tmp_path, "letterboxed.png", a)
    pf.take_grounds()
    got: dict = {}
    cover_crop(p, (1080, 1920), record=got)
    assert got["fit"] is False and got["problems"] == ["matte"] and "rule: a photograph is fit" in got["reason"]
    plain = save(tmp_path, "plain.png", with_subject(ground(3000, 1500), 0.5, 0.5, 0.2))
    cover_crop(plain, (1080, 1920))
    out = tmp_path / "graphic_test.mp4"
    rec = graphics.record_contrast(out, Image.new("RGB", (1080, 1920), (20, 20, 20)), [])
    grounds = {g["photo"]: g for g in rec["grounds"]}
    assert grounds["letterboxed.png"]["fit"] is False and grounds["letterboxed.png"]["problems"] == ["matte"]
    assert grounds["plain.png"]["fit"] is True
    assert json.loads(Path(str(out) + graphics.CONTRAST_SIDECAR).read_text(encoding="utf-8"))["grounds"]
    assert pf.take_grounds() == []                          # taken by the clip's record


def test_a_cover_crop_must_hold_the_photographs_central_half():
    """The cover's central-half condition (lander ruling, second review), alone: a crop that shows the subject and
    passes the Reel's and the tile's salience shares but leaves part of the photograph's central half out is unfit."""
    wide = Image.fromarray(_drawn(with_subject(ground(3000, 1600, grain=1), 0.5, 0.6, 0.12)))
    an = pf.analyse(wide)
    window = pf.Window((1080, 1920), view=(0, 420, 1080, 1440), hold=(0, 0, 1080, 1440), shown_min=0.5,
                       tile=(0, 420, 1080, 1440), centre_half=True)
    rec = pf.place(None, window, analysis=an, fixed=True)
    assert rec["shown"] >= 0.5 and rec["shown_tile"] >= pf.TILE_SHARE * rec["shown"]
    assert rec["centre_half"] is False and "subject" in rec["problems"]


def test_a_window_that_crops_hard_must_still_show_most_of_an_unreviewed_photograph():
    """The no-regression guard of the cover's full-width crop: "keep" compares crops of one shape, so a 2:1 picture
    cut to 9:16 passed it showing a quarter of itself. "shown" is the share of the photograph's whole salience where
    it shows unobscured; a reviewed subject is held to its own box instead."""
    wide = Image.fromarray(np.clip(with_subject(with_subject(ground(3000, 1500), 0.15, 0.5, 0.3), 0.85, 0.5, 0.3, 2),
                                   0, 255).astype(np.uint8))        # two subjects, at the ends of a 2:1 picture
    an = pf.analyse(wide)
    tall = pf.Window((1080, 1920), view=(0, 420, 1080, 1140), hold=(0, 0, 1080, 1140), shown_min=0.5)
    rec = pf.place(None, tall, analysis=an)
    assert rec["shown"] < 0.5 and "subject" in rec["problems"] and not rec["moved"]
    assert "subject" not in pf.place(None, pf.Window((1080, 1920), view=(0, 420, 1080, 1140)), analysis=an,
                                     fixed=True)["problems"] or rec["keep"] < pf.SUBJECT_KEEP   # "keep" alone passed it
    whole = pf.Window((1080, 540), shown_min=0.5)                  # a window of the photograph's own shape shows it
    assert pf.place(None, whole, analysis=an)["shown"] > 0.95
    reviewed = pf.analyse(wide, (0.15 * 3000 - 300, 450, 0.15 * 3000 + 300, 1050))
    assert "shown" not in pf.place(None, tall, analysis=reviewed, fixed=True)


def test_the_enlargement_limit_is_the_hook_cards(tmp_path):
    from src import design_cards
    assert design_cards.UPSCALE_MAX == pf.UPSCALE_MAX == 2.0
    p = save(tmp_path, "small.png", with_subject(ground(400, 300), 0.5, 0.5, 0.25))
    rec = pf.place(p, pf.Window((1080, 1920)))
    assert not rec["fit"] and "upscale" in rec["problems"] and rec["upscale"] > pf.UPSCALE_MAX


def test_a_reviewed_subject_must_be_whole_in_view():
    im = Image.fromarray(np.clip(with_subject(ground(2400, 1200), 0.2, 0.5, 0.3), 0, 255).astype(np.uint8))
    box = (0.2 * 2400 - 380, 600 - 380, 0.2 * 2400 + 380, 600 + 380)
    an = pf.analyse(im, box)
    window = pf.Window((1080, 1350))
    asked = pf.place(None, window, image=im, analysis=an, fixed=True)
    assert asked["focus"] < pf.FOCUS_KEEP and "subject" in asked["problems"]   # the centred crop cuts it
    rec = pf.place(None, window, image=im, analysis=an)
    assert rec["fit"] and rec["focus"] >= pf.FOCUS_KEEP


def test_a_foreign_structure_under_the_masthead_moves_the_window(tmp_path):
    a = with_subject(ground(2400, 1500), 0.5, 0.6, 0.22)
    im = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(im)
    d.polygon([(560, 0), (1500, 0), (1500, 120), (560, 60)], fill=(235, 235, 235))   # a wing's skin and red trim
    d.line([(560, 60), (1500, 120)], fill=(200, 20, 20), width=14)
    p = tmp_path / "wing.png"
    im.save(p)
    window = pf.Window((1440, 1800), view=(0, 0, 1440, 990), protected=((90, 0, 620, 150),), name="cover")
    asked = pf.place(p, window, fixed=True)
    assert "protected" in asked["problems"]
    rec = pf.place(p, window)
    if rec["fit"]:
        assert rec["moved"] and not rec["protected"][0]["foreign"] and rec["protected"][0]["line"] < pf.LINE_MAX
        assert rec["keep"] >= pf.SUBJECT_KEEP                      # and the subject is still in the view
    plain = save(tmp_path, "nowing.png", a)
    assert "protected" not in pf.place(plain, window, fixed=True)["problems"]


def test_a_move_does_not_set_the_type_on_a_busier_ground(tmp_path):
    """No-regression guard of a move (a slide moved off black space set its masthead on a spacecraft's module): the
    window that clears the frame also keeps the ground under the type no busier than BUSY_MOVE_MAX."""
    rng = np.random.default_rng(3)
    a = ground(2400, 1600, grain=2)
    a[:, :1300] = np.where(rng.random((1600, 1300, 1)) > 0.5, 230.0, 30.0)      # the left of the picture: hard clutter
    a[:, :1300] = np.repeat(np.repeat(a[::8, :1300:8], 8, axis=0), 8, axis=1)[:1600, :1300]
    a[:90] = 0                                                                   # digital black across the top
    p = save(tmp_path, "clutter.png", a)
    window = pf.Window((1440, 1100), protected=((60, 0, 520, 150),), name="split")
    asked = pf.place(p, window, fixed=True)
    assert "matte" in asked["problems"]
    rec = pf.place(p, window)
    if rec["moved"]:
        assert rec["matte"] <= pf.MATTE_MAX
        if rec["fit"]:
            assert rec["protected"][0]["busy"] <= max(pf.BUSY_MOVE_MAX, asked["protected"][0]["busy"])
    else:   # no crop that clears the frame keeps the asked centre and a calm ground: the asked one, recorded
        assert rec["box"] == asked["box"] and not rec["fit"] and "matte" in rec["problems"]
    # a move is never taken that trades the asked crop's defect for another one (review of cycle 5, B1)
    assert rec["fit"] or not rec["moved"] or set(rec["problems"]) < set(asked["problems"])


# ---------------------------------------------------------------- the surfaces ask it
def test_every_cover_crop_stays_inside_the_photographs_content(tmp_path):
    from src.typography import cover_crop
    p = save(tmp_path, "border.png", border(with_subject(ground(1800, 1400), 0.5, 0.5, 0.2), 0.05, 0.06))
    for size in ((1080, 1920), (1920, 1080), (1440, 1800)):
        out = np.asarray(cover_crop(p, size).convert("L"))
        assert (out <= pf.MATTE_LUMA).mean() <= 0.004, size
    scene = save(tmp_path, "limb.png", limb(with_subject(ground(1800, 1400), 0.5, 0.6, 0.2)))
    with Image.open(scene) as im:                                  # scene black: cropped exactly as asked
        plain = ImageOps.fit(im.convert("RGBA"), (1080, 1920), Image.LANCZOS)
    assert np.array_equal(np.asarray(cover_crop(scene, (1080, 1920))), np.asarray(plain))


def test_the_carousel_window_records_its_fit_and_never_shows_the_frame(tmp_path):
    from src import multi_format as mf
    p = save(tmp_path, "border.png", border(with_subject(ground(1600, 1600), 0.36, 0.70, 0.13)))
    mf._render_state.window_k, mf._render_state.masthead_box, mf._render_state.window_rec = 0, None, []
    try:
        im = mf.MultiFormatGenerator._fill(p, (1440, 1100), view=(0, 0, 1440, 900))
        rec = mf._render_state.window_rec[-1]["fit"]
    finally:
        mf._render_state.window_rec = None
    assert rec["fit"] and rec["moved"] and rec["keep"] >= pf.SUBJECT_KEEP
    lum = np.asarray(im.convert("L"))
    assert (lum <= pf.MATTE_LUMA).mean() <= 0.004
    ring = np.asarray(im.convert("RGB"), np.int16)
    assert ((ring[..., 2] - ring[..., 0]) > 50).mean() > 0.01      # the subject's blue rings are in the window


def test_the_photo_guard_takes_a_fit_photograph_the_run_already_has(tmp_path, monkeypatch):
    from src import multi_format as mf
    bad = save(tmp_path, "01_flat.png", with_subject(ground(1200, 2000, grain=1), 0.5, 0.93, 0.05))
    good = save(tmp_path, "02_subject.png", with_subject(ground(1600, 1200, seed=4), 0.5, 0.35, 0.25))
    cards = [{"heading": "THE RINGS", "body": "A formation seen from orbit."},
             {"heading": "THE AGE", "body": "It cooled long ago."}]
    monkeypatch.setattr(mf, "modern_presence", lambda photo: "")
    assert mf._slide_unfit(cards, 0, bad) and not mf._slide_unfit(cards, 0, good)
    spare = save(tmp_path, "03_other.png", with_subject(ground(1600, 1200, seed=6), 0.5, 0.4, 0.25))
    out, choice = mf.deck_photo_guard(cards, [bad, good], [bad, good, spare])
    assert out[0] == spare and out[1] == good
    assert choice[0]["reason"].startswith("fit:") and choice[0]["replaced"] == bad.name
    out, choice = mf.deck_photo_guard(cards, [bad, good], [bad, good])       # no spare: kept, and said
    assert out[0] == bad and choice[0]["reason"] == "matched" and choice[0]["unfit"]


def _cover(source: Image.Image, focus=None, style: str = "badge"):
    from src import still_layout
    from src.cover_design import render_cover
    lay = still_layout.Layout("cover", (1080, 1920), (60, 440, 1020, 1480))
    copy = {"eyebrow": "DESERT ANOMALY", "line1": "SAHARA", "line2": "EYE", "tagline": "CARVED BY NATURE"}
    base = Image.new("RGBA", (1080, 1920), (0, 0, 0, 255))
    src = source.convert("RGBA")
    return render_cover(base, copy, style, lay=lay, source=src, focus=focus), lay


def test_the_reel_cover_is_filled_with_the_photograph_or_stands_on_ink():
    """PHOTO-01 and PHOTO-07: no pad, no blurred continuation, no darkened copy of the photograph around its inset."""
    ink = np.array(brand.INK, np.int16)
    # a wide photograph with a reviewed feature: full width down to the type, INK under it
    wide = Image.fromarray(np.clip(with_subject(ground(3000, 1900), 0.55, 0.5, 0.3), 0, 255).astype(np.uint8))
    focus = {"focus_box": [0.55 - 0.2, 0.3, 0.55 + 0.2, 0.7], "sha256": "x", "reviewed": "test"}
    im, lay = _cover(wide, focus)
    comp = lay.meta["composition"]
    assert comp["mode"] in ("full_bleed", "photo_over_ink") and comp["source_coverage"] == 1.0
    assert comp["upscale"] <= pf.UPSCALE_MAX and lay.meta["photo_fit"]["fit"]
    a = np.asarray(im.convert("RGB"), np.int16)
    top_rows = a[:300]
    assert np.abs(np.diff(top_rows, axis=1)).mean() > 0.8          # real photograph up to the top edge, no smear
    px0, py0, px1, py1 = comp["protected_box"]
    assert 0 <= px0 < px1 <= 1080 and 0 <= py0 < py1 <= comp["image_box"][3]   # the feature whole, in the photo
    if comp["mode"] == "photo_over_ink":
        assert np.abs(a[1880:] - ink).max() <= 2                      # INK under the photograph, to the foot
    # a panorama whose feature cannot fit a full-width crop: the contained window on a clean INK field
    pano = Image.fromarray(np.clip(with_subject(ground(4000, 800), 0.5, 0.5, 0.45), 0, 255).astype(np.uint8))
    focus = {"focus_box": [0.05, 0.05, 0.95, 0.95], "sha256": "x", "reviewed": "test"}
    im, lay = _cover(pano, focus)
    assert lay.meta["composition"]["mode"] == "contain" and lay.meta["photo_fit"]["fit"] is False
    assert "rule: a photograph is fit" in lay.meta["photo_fit"]["reason"]
    a = np.asarray(im.convert("RGB"), np.int16)
    x0, y0, x1, y1 = lay.meta["composition"]["image_box"]
    assert np.abs(a[:y0 - 2] - ink).max() <= 2                     # nothing of the photograph above its window
    # an unreviewed LANDSCAPE photograph whose subject shows in the Reel's view and the profile tile's fills the
    # cover at full width (lander ruling, second review); one whose subject sits above the tile stands contained
    wide = Image.fromarray(_drawn(with_subject(ground(2400, 1600), 0.5, 0.62, 0.22)))
    im, lay = _cover(wide)
    fit = lay.meta["photo_fit"]
    assert lay.meta["composition"]["mode"] == "photo_over_ink" and fit["fit"]
    kept = [t for t in fit["tried"] if not t["problems"]][0]
    assert kept["shown"] >= 0.5 and kept["shown_tile"] >= pf.TILE_SHARE * kept["shown"] and kept["centre_half"]
    high = Image.fromarray(_drawn(with_subject(ground(2400, 1600, grain=2), 0.5, 0.16, 0.14)))
    im, lay = _cover(high)
    assert lay.meta["composition"]["mode"] == "contain"
    assert any(t.get("shown_tile", 1.0) < pf.TILE_SHARE * t.get("shown", 0.0) for t in lay.meta["photo_fit"]["tried"])
    # an unreviewed PORTRAIT photograph stays whole (review of cycle 5, B2: a full-width band of a portrait cut the
    # statue's head out of the profile tile): contained, once, on INK, with the reason recorded
    tall = Image.fromarray(np.clip(with_subject(ground(1400, 2400), 0.5, 0.4, 0.2), 0, 255).astype(np.uint8))
    im, lay = _cover(tall)
    assert lay.meta["composition"]["mode"] == "contain" and lay.meta["composition"]["source_box"] == [0, 0, 1400, 2400]
    assert lay.meta["photo_fit"]["fit"] is False and "portrait" in lay.meta["photo_fit"]["reason"]
    # an unreviewed wide one is not cut to a quarter of itself: it stands whole, once, on INK (the cycle-5 crop of a
    # 2:1 miniature showed its parchment and no ship)
    ends = Image.fromarray(np.clip(with_subject(with_subject(ground(3000, 1500), 0.15, 0.5, 0.3), 0.85, 0.5, 0.3, 2),
                                   0, 255).astype(np.uint8))
    im, lay = _cover(ends)
    comp = lay.meta["composition"]
    assert comp["mode"] == "contain" and comp["source_box"] == [0, 0, 3000, 1500]
    assert lay.meta["photo_fit"]["fit"] is False and all("subject" in t["problems"] for t in lay.meta["photo_fit"]["tried"])
    assert all(t["shown"] < 0.5 and not t["moved"] for t in lay.meta["photo_fit"]["tried"])
    a = np.asarray(im.convert("RGB"), np.int16)
    x0, y0, x1, y1 = comp["image_box"]
    assert np.abs(a[:y0 - 2] - ink).max() <= 2 and x1 - x0 >= 900
    blue = (a[..., 2] - a[..., 0]) > 50
    assert blue[y0:y1, x0:(x0 + x1) // 2].mean() > 0.02 and blue[y0:y1, (x0 + x1) // 2:x1].mean() > 0.02   # both ends


def test_the_cover_record_keeps_the_ladder_and_the_reason(tmp_path):
    """Truth: what the photo-fitness ladder tried and why the photograph stands contained is on the cover's kept
    record (still_layout.json recipe.photo_fit), not only in memory."""
    import json

    from src import still_layout
    from src.visual_engine import VisualEngine
    ends = with_subject(with_subject(ground(3000, 1500), 0.15, 0.5, 0.3), 0.85, 0.5, 0.3, 2)
    hero = tmp_path / "hero.jpg"
    Image.fromarray(np.clip(ends, 0, 255).astype(np.uint8)).save(hero, quality=95)
    copy = {"eyebrow": "DESERT ANOMALY", "line1": "SAHARA", "line2": "EYE", "tagline": "CARVED BY NATURE"}
    VisualEngine().generate_thumbnails(hero, {"thumbnail": copy}, tmp_path / "out", style="badge", variants=False)
    rec = json.loads((tmp_path / "out" / still_layout.SIDECAR).read_text(encoding="utf-8"))["thumbnail_cover.jpg"]
    fit = rec["recipe"]["photo_fit"]
    assert rec["recipe"]["composition"]["mode"] == "contain" and fit["fit"] is False
    assert "rule: a photograph is fit" in fit["reason"] and [t["mode"] for t in fit["tried"]] == ["full_bleed",
                                                                                                "photo_over_ink"]
    assert all(t["shown"] < 0.5 for t in fit["tried"])


def test_the_youtube_side_field_is_ink():
    from src import youtube_thumbnail as yt
    field = np.asarray(yt._side_field(Image.new("RGB", (600, 720), (200, 180, 90)), 680, 0), np.int16)
    assert field.shape[:2] == (yt.H, yt.W) and np.abs(field - np.array(brand.INK, np.int16)).max() == 0


def test_the_end_screen_takes_the_field_only_ground_over_a_framed_last_frame(monkeypatch):
    """PHOTO-02: a last frame inside its own black oval is no ground; the whole frame eases to INK (no soft
    ellipse left at rest), and a plain last frame keeps its picture."""
    from src import end_card
    monkeypatch.setattr(end_card, "END_SCREEN_SECONDS", 2.0)
    a = with_subject(ground(1920, 1080, level=170), 0.5, 0.5, 0.3)
    framed = Image.fromarray(np.clip(oval(a), 0, 255).astype(np.uint8))
    stats: dict = {}
    frames = list(end_card.frames(framed, "Share this with someone who loves a good mystery.", stats))
    assert stats["matte"] and stats["matte"]["ground"] == "field only" and "rule:" in stats["matte"]["reason"]
    rest = np.asarray(frames[-1].convert("RGB"), np.int16)
    right = rest[:, 1500:]                                         # the video slot's side: lightest dim before
    assert np.abs(right - np.array(brand.INK, np.int16)).max() <= 3
    plain = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    stats = {}
    frames = list(end_card.frames(plain, "Share this with someone who loves a good mystery.", stats))
    assert stats["matte"] is None
    assert np.asarray(frames[-1].convert("L"))[:, 1500:].std() > 3   # the picture is still the ground


def test_an_unregistered_then_and_now_pair_is_recorded_and_keeps_the_shared_window(tmp_path):
    """PHOTO-06: only a registered pair (one size: then_now.register) compares through one window. A pair that is
    not is drawn as it was (no crop by salience stands in for a registration) and recorded with the reason."""
    from PIL import ImageOps

    from src import then_now
    from src.photo_grade import grade
    old = save(tmp_path, "then.png", with_subject(ground(1800, 1400, level=190), 0.3, 0.45, 0.12))
    new = save(tmp_path, "now.png", with_subject(ground(2200, 1500, seed=5), 0.7, 0.5, 0.15))
    w, h = 1920, 1080
    a, b, info = then_now.frame_pair(old, new, w, h)
    assert info["registered"] is False and "not registered" in info["unregistered"]
    assert "rule:" in info["unregistered"] and "panes" not in info
    big = (round(w * (1 + then_now.PUSH)), round(h * (1 + then_now.PUSH)))
    for got, path in ((a, old), (b, new)):                         # the shared window: the centred fit of each
        want = grade(ImageOps.fit(Image.open(path).convert("RGB"), big, Image.LANCZOS))
        assert np.abs(np.asarray(got.convert("RGB"), np.int16) - np.asarray(want, np.int16)).max() <= 1
    same = save(tmp_path, "now_reg.png", with_subject(ground(1800, 1400, seed=7), 0.3, 0.7, 0.18))
    _a, _b, info = then_now.frame_pair(old, same, w, h)            # one size: a registered pair
    assert info["registered"] is True and "unregistered" not in info


def test_a_hero_that_cannot_fill_the_cover_gives_way(tmp_path, monkeypatch):
    from src import hero_photo
    pano = save(tmp_path, "01_pano.png", np.concatenate([np.zeros((300, 3000, 3), np.float32),
                                                         with_subject(ground(3000, 500), 0.5, 0.5, 0.4),
                                                         np.zeros((300, 3000, 3), np.float32)]))
    wide = save(tmp_path, "02_wide.png", with_subject(ground(2400, 1600), 0.5, 0.62, 0.22))
    tall = save(tmp_path, "03_tall.png", with_subject(ground(1400, 2400), 0.5, 0.4, 0.2))
    assert hero_photo.fills_cover(wide) and not hero_photo.fills_cover(pano)
    assert not hero_photo.fills_cover(tall)          # an unreviewed portrait stands contained (cover_design._filled)
    assert hero_photo.score(wide) is not None


def test_the_cover_penalty_never_promotes_a_less_relevant_photograph(tmp_path):
    """Lander ruling, second review (Göbekli Tepe: a landscape without the site replaced the excavated enclosure):
    the cover-fitness penalty orders photographs of equal relevance only."""
    import json as _json

    from src import hero_photo
    first = save(tmp_path, "01_commons.png", with_subject(ground(1400, 2400, grain=4), 0.5, 0.4, 0.2))  # portrait
    other = save(tmp_path, "02_commons.png", with_subject(ground(2400, 1600, grain=8), 0.5, 0.62, 0.22))  # fills

    def credits(second_shows):
        imgs = [{"file": "01_commons.png", "license": "CC BY-SA 4.0", "relevance": 9,
                 "vision_review": {"shows_topic": "yes", "usable": True, "safe": True}},
                {"file": "02_commons.png", "license": "CC BY-SA 4.0", "relevance": 9,
                 "vision_review": {"shows_topic": second_shows, "usable": True, "safe": True}}]
        (tmp_path / "credits.json").write_text(_json.dumps({"images": imgs}), encoding="utf-8")

    assert not hero_photo.fills_cover(first) and hero_photo.fills_cover(other)
    raw = hero_photo.score(other, fitness=False) - hero_photo.score(first, fitness=False)
    fit = hero_photo.score(other) - hero_photo.score(first)
    assert raw < hero_photo.SWITCH_MARGIN <= fit                # the penalty alone decides
    credits("partly")                                          # the other photograph is context: lower relevance
    assert hero_photo.pick([first, other]) == first
    credits("yes")                                             # equal relevance: the penalty orders them
    assert hero_photo.pick([first, other]) == other


# ---------------------------------------------------------------- the corpus proof uses this measure
def test_the_corpus_script_judges_with_the_trees_measure(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "corpus_photo_fitness", ROOT / "docs/reviews/freeze-system-2026-11-15/corpus/photo_fitness.py")
    corpus = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(corpus)
    assert corpus.pf is pf
    p = save(tmp_path, "border.png", border(with_subject(ground(1600, 1600), 0.36, 0.70, 0.13)))
    an = corpus.Photos(None).get(p)
    surfaces = corpus.windows()
    assert {kind for _s, kind, _w in surfaces} == {"carousel", "story", "ground", "hook"} and len(surfaces) >= 13
    base_bad = tip_bad = 0
    for _surface, kind, window in surfaces:
        base_bad += bool(corpus.crop_base(an, window, kind)["problems"])
        tip = corpus.crop_tip(an, window, kind)
        tip_bad += "matte" in tip["problems"]
    assert tip_bad == 0                                            # no window of any surface shows the border
    assert base_bad > 0                                            # the base rule's croppers did
    assert corpus.cover_tip(an)["problems"] == []


@pytest.mark.parametrize("name", ["matte", "upscale", "subject", "flat", "protected"])
def test_every_problem_has_a_recorded_reason(name):
    text = pf.reason({"problems": [name]}, "the surface")
    assert pf.REASONS[name] in text and "the surface" in text and "src/photo_fitness.py" in text


def test_a_moved_window_never_costs_the_text_its_contrast():
    """Review of cycle 5, B3: a slide or Story frame whose window was moved is drawn again as asked; the moved drawing
    stands only when no text element is lower after the platform re-encode and none falls under the floor."""
    from types import SimpleNamespace

    from src import multi_format as mf

    class Plate:
        empty = False

        def measure(self, im):   # a stand-in ratio read from the delivered pixels: brighter ground, higher ratio
            return float(np.asarray(im.convert("L")).mean()) / 25.0

    def drawing(level, moved):
        lay = SimpleNamespace(meta={"windows": [{"fit": {"moved": moved, "fit": True, "problems": []}}]},
                              plates={"body": [Plate()]})
        return Image.new("RGB", (64, 80), (level,) * 3), lay

    asked_calls = []

    def again(level):
        def draw():
            asked_calls.append(getattr(mf._render_state, "asked_only", False))
            return drawing(level, False)
        return draw

    im, lay = drawing(100, True)                              # moved: 4.0, asked: 6.0 -> the asked drawing
    got_im, got_lay = mf.keep_text_contrast(again(150), im, lay)
    assert asked_calls == [True] and not getattr(mf._render_state, "asked_only", False)
    assert np.asarray(got_im).mean() == pytest.approx(150, abs=1)
    rec = got_lay.meta["windows"][0]["fit"]
    assert "legibility" in rec["move_refused"] and rec["text_contrast"]["asked"]["body"] > 5.5
    im, lay = drawing(150, True)                              # moved: 6.0, asked: 4.0 -> the moved one stands
    got_im, got_lay = mf.keep_text_contrast(again(100), im, lay)
    assert got_lay is lay and "move_refused" not in lay.meta["windows"][0]["fit"]
    im, lay = drawing(150, False)                             # nothing moved: no second drawing
    asked_calls.clear()
    assert mf.keep_text_contrast(again(100), im, lay)[1] is lay and asked_calls == []


def test_a_move_stands_unless_text_falls_under_its_floor_or_the_lowest_ratio_falls():
    """Lander ruling on the second review (B4): one element losing a little while every element stays over the floor
    and the slide's lowest ratio holds is the move's price, not a refusal (a masthead from 5.21 to 5.00 with the body
    and heading rising)."""
    from types import SimpleNamespace

    from src import multi_format as mf

    class Plate:
        empty = False

        def __init__(self, box):
            self.box = box

        def measure(self, im):   # this element's ratio: the mean of its own box of the delivered picture / 25
            x0, y0, x1, y1 = self.box
            return float(np.asarray(im.convert("L"))[y0:y1, x0:x1].mean()) / 25.0

    def drawing(masthead, body, moved):
        a = np.zeros((80, 64, 3), np.uint8)
        a[:40] = masthead
        a[40:] = body
        lay = SimpleNamespace(meta={"windows": [{"fit": {"moved": moved, "fit": True, "problems": []}}]},
                              plates={"masthead": [Plate((0, 4, 64, 36))], "body": [Plate((0, 44, 64, 76))]})
        return Image.fromarray(a), lay

    im, lay = drawing(130, 140, True)                        # masthead 5.2 -> 5.0 (moved), body 4.8 -> 5.6
    got = mf.keep_text_contrast(lambda: drawing(135, 120, False), im, lay)
    assert got[1] is lay and "move_refused" not in lay.meta["windows"][0]["fit"]
    im, lay = drawing(135, 115, True)                        # the lowest falls (4.8 -> 4.6): refused
    got = mf.keep_text_contrast(lambda: drawing(135, 120, False), im, lay)
    assert got[1] is not lay and "lowest" in got[1].meta["windows"][0]["fit"]["move_refused"]


def test_a_move_that_leaves_text_under_its_floor_and_lower_is_refused_one_that_raises_it_stands():
    """The floor half of the guard (third review): an element that ends under 4.5:1 AND lower than as asked refuses
    the move even when the slide's lowest ratio rises; an element already under its floor as asked that rises (with
    every other) lets the move stand."""
    from types import SimpleNamespace

    from src import multi_format as mf

    class Plate:
        empty = False

        def __init__(self, box):
            self.box = box

        def measure(self, im):
            x0, y0, x1, y1 = self.box
            return float(np.asarray(im.convert("L"))[y0:y1, x0:x1].mean()) / 25.0

    def drawing(masthead, body, moved):
        a = np.zeros((80, 64, 3), np.uint8)
        a[:40] = masthead
        a[40:] = body
        lay = SimpleNamespace(meta={"windows": [{"fit": {"moved": moved, "fit": True, "problems": []}}]},
                              plates={"masthead": [Plate((0, 4, 64, 36))], "body": [Plate((0, 44, 64, 76))]})
        return Image.fromarray(a), lay

    # masthead 4.60 -> 4.40 (under 4.5 and lower), body 4.20 -> 4.48: the lowest rises, the masthead refuses it
    im, lay = drawing(110, 112, True)
    got = mf.keep_text_contrast(lambda: drawing(115, 105, False), im, lay)
    assert got[1] is not lay and "masthead" in got[1].meta["windows"][0]["fit"]["move_refused"]
    # body 4.00 -> 4.30 (under 4.5 as asked, and higher), masthead 6.0 -> 6.1: every element rises, the move stands
    im, lay = drawing(152, 107, True)
    got = mf.keep_text_contrast(lambda: drawing(150, 100, False), im, lay)
    assert got[1] is lay


def test_the_slide_and_story_renderers_are_held_to_the_text_guard():
    """The guard is wired, not only written: both renderers are _holds_text_contrast wrappers of their drawing."""
    from src import multi_format as mf
    probe = mf._holds_text_contrast(lambda self: None)
    for fn in (mf.MultiFormatGenerator._render_carousel_slide, mf.MultiFormatGenerator._render_story_frame):
        assert fn.__code__ is probe.__code__ and fn.__wrapped__.__name__ == fn.__name__
