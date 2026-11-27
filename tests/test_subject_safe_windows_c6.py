"""Subject-safe window rules (design-freeze cycle 6, SUBJECT stream, rounds 2-3). With a subject box:
(a) no crop shows less of the subject, in the view or the hold, than BOTH bases: the crop without the box and the
    cycle-5 crop with the same box (_place(..., legacy=True));
(b) the subject's need spans the zoom ladder the window may use: a subject the window can show whole is shown whole;
    a subject larger than every crop is shown as whole as the best crop shows it, and that crop is fit;
(c) the Reel cover leaves its layout for a full-width one only when the hold keeps the subject whole (SUBJECT_WHOLE)
    and the profile tile COVER_GRID_FOCUS of it; it first tries the layout it takes without the box;
(d) a crop drawn as without the box keeps that record's fitness ("subject_partial").
Without a subject box every placement is base a9de76b3's (fixture written by the base tree)."""
import dataclasses
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from src import cover_design as cd
from src import photo_fitness as pf

FIXTURE = Path(__file__).parent / "fixtures" / "subject_c6_nobox_base.json"


def _picture(w, h, seed=3):
    rng = np.random.default_rng(seed)
    a = (rng.normal(120, 30, (h, w, 3))).clip(0, 255).astype(np.uint8)   # textured ground, no frame
    return Image.fromarray(a)


def _with_focus(img, box):
    a = pf.analyse(img)
    return dataclasses.replace(a, focus=box, _sat={})


def _shown(a, r, window):
    view = pf._sub(tuple(r["box"]), window.size, window.view_box())
    return pf._overlap(a.focus, view)


def test_wide_subject_in_a_narrow_window_is_fit_and_shown_as_whole_as_it_can_be():
    img = _picture(1800, 900)
    d = ImageDraw.Draw(img)
    d.rectangle((200, 300, 1600, 650), fill=(230, 200, 60))          # a subject wider than any 9:16 crop
    a = _with_focus(img, (200.0, 300.0, 1600.0, 650.0))
    rec = pf.place(None, pf.Window((1080, 1920), name="story"), analysis=a)
    assert "subject" not in rec["problems"], rec            # (b): its size alone is no fault
    best = max(pf._overlap(a.focus, pf._sub(b, (1080, 1920), (0, 0, 1080, 1920)))
               for b in pf._candidates(a, pf.Window((1080, 1920)), 1.0))
    assert rec["focus"] >= best - 0.01                      # as whole as the window can hold


def test_no_crop_shows_less_of_the_subject_than_either_base():
    img = _picture(1800, 900, seed=5)
    d = ImageDraw.Draw(img)
    d.rectangle((60, 120, 1740, 860), fill=(30, 30, 30))   # a subject filling most of the picture
    d.rectangle((0, 0, 1800, 14), fill=(0, 0, 0))           # a scan's black edge: the frame rule moves the crop
    window = pf.Window((1080, 1350), name="ig carousel immersive")
    a = _with_focus(img, (60.0, 120.0, 1740.0, 860.0))
    blind = dataclasses.replace(a, focus=None, _sat={})
    got = pf.place(None, window, analysis=a)
    assert _shown(a, got, window) >= _shown(a, pf.place(None, window, analysis=blind), window) - 1e-3
    assert _shown(a, got, window) >= _shown(a, pf._place(None, window, analysis=a, legacy=True), window) - 1e-3


def test_a_subject_the_window_can_show_whole_is_shown_whole_on_the_zoom_ladder():
    # the window's view is its top half (a panel covers the rest): at the plain fit the subject lies under the panel;
    # a closer crop (the zoom ladder) puts it whole in the view - the cycle-5 rules find it, so must these
    img = _picture(2400, 1200, seed=13)
    d = ImageDraw.Draw(img)
    d.rectangle((1100, 560, 1300, 760), fill=(220, 40, 40))
    a = _with_focus(img, (1100.0, 560.0, 1300.0, 760.0))
    window = pf.Window((1080, 1350), view=(0, 0, 1080, 675), name="debate")
    got = pf.place(None, window, analysis=a)
    assert got["focus"] >= pf.FOCUS_KEEP and got["fit"], got
    assert got["focus_need"] > 0.9                          # the need is not lowered to what the plain fit shows


def test_cover_takes_the_full_width_photograph_when_the_subject_fits_its_hold():
    img = _picture(1200, 1800, seed=7)                      # a portrait photograph
    d = ImageDraw.Draw(img)
    d.rectangle((420, 300, 780, 950), fill=(200, 60, 40))   # a subject the full-width crop can hold whole
    a = _with_focus(img, (420.0, 300.0, 780.0, 950.0))
    fit = [(mode, pf.place(None, w, analysis=a)) for mode, w in cd.cover_windows()]
    mode, rec = next(((m, r) for m, r in fit if r["fit"]), (None, None))
    assert mode in ("full_bleed", "photo_over_ink"), [r["problems"] for _, r in fit]
    assert rec["held"] >= pf.SUBJECT_WHOLE and rec["focus"] >= cd.COVER_GRID_FOCUS


def test_cover_stands_contained_when_every_full_width_crop_cuts_the_subject():
    img = _picture(2400, 800, seed=9)                       # a panorama
    d = ImageDraw.Draw(img)
    d.rectangle((100, 200, 2300, 700), fill=(40, 160, 200))  # a subject spanning it: any 1080-wide crop cuts it
    a = _with_focus(img, (100.0, 200.0, 2300.0, 700.0))
    assert not any(pf.place(None, w, analysis=a)["fit"] for _m, w in cd.cover_windows())


def test_a_cover_never_draws_a_subject_cut_in_its_hold():
    # 4 % of the subject below every full-width hold: a viewer reads a cut (round 3, B3 / B4): not full-width
    img = _picture(1200, 1800, seed=7)
    d = ImageDraw.Draw(img)
    d.rectangle((420, 300, 780, 1100), fill=(200, 60, 40))
    a = _with_focus(img, (420.0, 300.0, 780.0, 1100.0))
    for _m, w in cd.cover_windows():
        rec = pf.place(None, w, analysis=a)
        assert not rec["fit"] or rec["held"] >= 0.98, rec    # the lander's ruling, round 3 (not 0.85)


def test_the_cover_asks_the_subject_whole_and_the_tile_its_share_never_lowered():
    img = _picture(1200, 1800, seed=7)
    d = ImageDraw.Draw(img)
    d.rectangle((420, 300, 780, 950), fill=(200, 60, 40))
    a = _with_focus(img, (420.0, 300.0, 780.0, 950.0))
    for _m, w in cd.cover_windows():
        assert w.hold_min == pf.SUBJECT_WHOLE == 0.98
        needs = pf._subject_needs(a, w, pf.fit_box(a.size, w.size))
        assert needs["focus_need"] == cd.COVER_GRID_FOCUS and needs["held_need"] == pf.SUBJECT_WHOLE


def test_the_cover_tries_first_the_layout_it_takes_without_the_box(monkeypatch):
    img = _picture(1200, 1800, seed=7)
    a = _with_focus(img, (420.0, 300.0, 780.0, 950.0))
    real = pf.place

    def place(photo, window, *args, analysis=None, **kw):
        if analysis is not None and analysis.focus is None:   # without the box only photo_over_ink is fit
            return {"fit": window.name.endswith("photo_over_ink")}
        return real(photo, window, *args, analysis=analysis, **kw)
    monkeypatch.setattr(pf, "place", place)
    assert [m for m, _w in cd.cover_order(None, a)] == ["photo_over_ink", "full_bleed"]


def test_a_crop_drawn_as_without_the_box_keeps_its_fitness():
    # a fixed ground (the hook card's) cannot move to the subject: the box records it, it does not turn it unfit
    rng = np.random.default_rng(17)                         # a blocky texture: the centred crop is fit without a box
    img = Image.fromarray(rng.integers(40, 220, (40, 80, 3)).astype(np.uint8)).resize((2400, 1200), Image.NEAREST)
    a = _with_focus(img, (60.0, 300.0, 500.0, 900.0))       # a subject at the left edge, outside the fixed crop
    window = pf.Window((1080, 1920), checks=("matte", "subject", "flat"), name="hook ground")
    blind = pf.place(None, window, analysis=dataclasses.replace(a, focus=None, _sat={}), fixed=True)
    got = pf.place(None, window, analysis=a, fixed=True)
    assert blind["fit"], blind
    assert got["box"] == blind["box"] and got["focus"] < 0.5
    assert got["fit"] and got.get("subject_partial")


def _nobox_cases():
    out = []
    for k, (w, h, seed) in enumerate(((1600, 1000, 11), (1000, 1600, 12), (2400, 800, 14), (1200, 1200, 15))):
        img = _picture(w, h, seed)
        d = ImageDraw.Draw(img)
        d.rectangle((w // 5, h // 4, w // 2, h // 2), fill=(230, 120, 40))
        d.rectangle((0, 0, w, 10), fill=(0, 0, 0))
        out.append((f"p{k}", img))
    windows = [(m, w) for m, w in cd.cover_windows()] + [
        ("story", pf.Window((1080, 1920), name="story")),
        ("slide", pf.Window((1440, 1800), view=(0, 0, 1440, 1000), protected=((100, 60, 700, 160),), name="slide")),
        ("ground", pf.Window((1920, 1080), checks=("matte",), name="wide ground"))]
    return out, windows


def _rounded(rec):
    keep = ("box", "fit", "problems", "moved", "zoom", "upscale", "matte", "flat", "subject", "shown", "basis")
    return {k: (round(v, 2) if isinstance(v, float) else [round(x, 1) for x in v] if k == "box" else v)
            for k, v in rec.items() if k in keep}


def test_without_a_subject_every_placement_is_the_bases():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    photos, windows = _nobox_cases()
    for name, img in photos:
        a = pf.analyse(img)
        for wn, w in windows:
            for cen in ((0.5, 0.5), (0.2, 0.5)):
                got = _rounded(pf.place(None, w, cen, analysis=a))
                assert got == fixture[f"{name}|{wn}|{cen[0]}"], (name, wn, cen)


def test_a_window_moved_for_a_subject_costs_no_text_element_its_contrast():
    """The masthead losing a little while the lowest ratio holds stands for a cycle-5 move; for a window moved with a
    subject box (its record carries "focus") it is refused: per element, no lower than as asked."""
    from types import SimpleNamespace

    from src import multi_format as mf

    class Plate:
        empty = False

        def __init__(self, box):
            self.box = box

        def measure(self, im):
            x0, y0, x1, y1 = self.box
            return float(np.asarray(im.convert("L"))[y0:y1, x0:x1].mean()) / 25.0

    def drawing(masthead, body, moved, focus):
        a = np.zeros((80, 64, 3), np.uint8)
        a[:40] = masthead
        a[40:] = body
        fit = {"moved": moved, "fit": True, "problems": [], **({"focus": 1.0} if focus else {})}
        lay = SimpleNamespace(meta={"windows": [{"fit": fit}]},
                              plates={"masthead": [Plate((0, 4, 64, 36))], "body": [Plate((0, 44, 64, 76))]})
        return Image.fromarray(a), lay

    im, lay = drawing(130, 140, True, focus=False)           # masthead 5.4 -> 5.2, body up: stands (cycle 5)
    assert mf.keep_text_contrast(lambda: drawing(135, 120, False, False), im, lay)[1] is lay
    im, lay = drawing(130, 140, True, focus=True)            # the same move made for a subject: refused
    got = mf.keep_text_contrast(lambda: drawing(135, 120, False, True), im, lay)
    assert got[1] is not lay and "move_refused" in got[1].meta["windows"][0]["fit"]


def test_the_subject_safe_crop_is_drawn_only_when_it_shows_no_less_than_the_cycle5_crop(monkeypatch):
    img = _picture(1800, 1200, seed=19)
    a = _with_focus(img, (700.0, 300.0, 1100.0, 900.0))
    window = pf.Window((1080, 1350), name="ig carousel immersive")
    real = pf._place
    legacy = real(None, window, analysis=a, legacy=True)

    def worse(photo, w, *args, legacy=False, floor=None, analysis=None, **kw):
        rec = real(photo, w, *args, legacy=legacy, floor=floor, analysis=analysis, **kw)
        if analysis is not None and analysis.focus is not None and not legacy:   # the new rules lose the subject
            rec = {**rec, "box": [0.0, 0.0, 960.0, 1200.0], "problems": []}
        return rec
    monkeypatch.setattr(pf, "_place", worse)
    got = pf.place(None, window, analysis=a)
    assert got["box"] == legacy["box"] and "cycle-5" in got.get("basis", "")
