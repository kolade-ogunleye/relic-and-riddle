"""The photo-fitness rule (design-freeze cycle 5, PHOTO stream; src/photo_fitness.py): one measure of whether a
photograph is fit for the window it is put in, asked by every picker and cropper.

Fixtures are drawn here (no run file, no workstation path): a textured ground with one salient subject, and the
frames a source can carry (a black wedge, an oval surround, a letterbox). Each test holds one class of the panel-6
ledger closed: PHOTO-01 (no pad), PHOTO-02 (no frame in a crop), PHOTO-03 (the subject stays in the window),
PHOTO-04 (nothing foreign under the masthead), PHOTO-06 (an unregistered pair is recorded), PHOTO-07 (no repeated
photograph).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

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
    for name, framed in (("wedge", wedge), ("oval", oval)):
        a = framed(with_subject(ground(1600, 1200), 0.5, 0.5, 0.2))
        im = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
        an = pf.analyse(im)
        assert an.matte_share > 0.02, name
        x0, y0, x1, y1 = an.content_box
        inner = np.asarray(im.crop((x0 + 8, y0 + 8, x1 - 8, y1 - 8)).convert("L"))
        assert (inner <= pf.MATTE_LUMA).mean() < 0.002, name     # no frame inside the content box
        assert (x1 - x0) * (y1 - y0) > 0.35 * 1600 * 1200, name   # and it is the large rectangle, not a sliver
    letter = np.zeros((1200, 1600, 3), np.float32)
    letter[200:1000] = with_subject(ground(1600, 800), 0.5, 0.5, 0.3)
    an = pf.analyse(Image.fromarray(letter.astype(np.uint8)))
    assert abs(an.content_box[1] - 200) <= 8 and abs(an.content_box[3] - 1000) <= 8
    dark = ground(1600, 1200, level=60)   # a photographed dark ground is not a frame
    dark[:300] = np.clip(ground(1600, 300, seed=3, level=30, grain=6), 6, 255)
    assert pf.analyse(Image.fromarray(dark.astype(np.uint8))).matte_share == 0


def test_the_crop_that_clears_a_frame_keeps_the_subject_in_view(tmp_path):
    """PHOTO-03, the regression this stream exists for: lane D cycle 4 moved a slide's window off a black wedge and
    onto bare sand, and nothing measured the subject. The subject sits low and left, beside the wedge."""
    a = wedge(with_subject(ground(1600, 1600), 0.36, 0.70, 0.13))
    p = save(tmp_path, "wedge.png", a)
    window = pf.Window((1440, 1100), name="split")
    an = pf.analysis_of(p)
    asked = pf.place(p, window, fixed=True)
    assert "matte" in asked["problems"]                          # the centred window shows the wedge
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


# ---------------------------------------------------------------- the surfaces ask it
def test_every_cover_crop_stays_inside_the_photographs_content(tmp_path):
    from src.typography import cover_crop
    for name, framed in (("wedge", wedge), ("oval", oval)):
        p = save(tmp_path, f"{name}.png", framed(with_subject(ground(1800, 1400), 0.5, 0.5, 0.2)))
        for size in ((1080, 1920), (1920, 1080), (1440, 1800)):
            out = np.asarray(cover_crop(p, size).convert("L"))
            assert (out <= pf.MATTE_LUMA).mean() <= 0.004, (name, size)


def test_the_carousel_window_records_its_fit_and_never_shows_the_frame(tmp_path):
    from src import multi_format as mf
    p = save(tmp_path, "wedge.png", wedge(with_subject(ground(1600, 1600), 0.36, 0.70, 0.13)))
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
    # an unreviewed tall photograph fills the cover whole
    tall = Image.fromarray(np.clip(with_subject(ground(1400, 2400), 0.5, 0.4, 0.2), 0, 255).astype(np.uint8))
    im, lay = _cover(tall)
    assert lay.meta["composition"]["mode"] == "full_bleed"
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
    tall = save(tmp_path, "02_tall.png", with_subject(ground(1400, 2400), 0.5, 0.4, 0.2))
    assert hero_photo.fills_cover(tall) and not hero_photo.fills_cover(pano)
    assert hero_photo.score(tall) is not None


# ---------------------------------------------------------------- the corpus proof uses this measure
def test_the_corpus_script_judges_with_the_trees_measure(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "corpus_photo_fitness", ROOT / "docs/reviews/freeze-system-2026-11-15/corpus/photo_fitness.py")
    corpus = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(corpus)
    assert corpus.pf is pf
    p = save(tmp_path, "wedge.png", wedge(with_subject(ground(1600, 1600), 0.36, 0.70, 0.13)))
    an = corpus.Photos(None).get(p)
    surfaces = corpus.windows()
    assert {kind for _s, kind, _w in surfaces} == {"carousel", "story", "ground", "hook"} and len(surfaces) >= 13
    base_bad = tip_bad = 0
    for _surface, kind, window in surfaces:
        base_bad += bool(corpus.crop_base(an, window, kind)["problems"])
        tip = corpus.crop_tip(an, window, kind)
        tip_bad += "matte" in tip["problems"]
    assert tip_bad == 0                                            # no window of any surface shows the wedge
    assert base_bad > 0                                            # the base rule's croppers did
    assert corpus.cover_tip(an)["problems"] == []


@pytest.mark.parametrize("name", ["matte", "upscale", "subject", "flat", "protected"])
def test_every_problem_has_a_recorded_reason(name):
    text = pf.reason({"problems": [name]}, "the surface")
    assert pf.REASONS[name] in text and "the surface" in text and "src/photo_fitness.py" in text
