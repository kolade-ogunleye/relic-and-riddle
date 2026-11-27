"""Subject-safe window rules (design-freeze cycle 6, SUBJECT stream, round 2). With a subject box:
(a) no crop shows less of the subject than the crop the photograph gets without one;
(b) a subject larger than the window is shown as whole as the window can hold, and that crop is fit (its size alone
    is no fault: no photograph is swapped for it);
(c) the Reel cover takes the full-width photograph when the hold keeps the subject whole or as whole as
    SUBJECT_CUT_MAX, and stands contained (the subject whole) only when every full-width crop cuts it more.
Without a subject box every placement is what it was (place() == _place())."""
import dataclasses

import numpy as np
from PIL import Image, ImageDraw

from src import cover_design as cd
from src import photo_fitness as pf


def _picture(w, h, seed=3):
    rng = np.random.default_rng(seed)
    a = (rng.normal(120, 30, (h, w, 3))).clip(0, 255).astype(np.uint8)   # textured ground, no frame
    return Image.fromarray(a)


def _with_focus(img, box):
    a = pf.analyse(img)
    return dataclasses.replace(a, focus=box, _sat={})


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


def test_no_crop_shows_less_of_the_subject_than_without_it():
    img = _picture(1800, 900, seed=5)
    d = ImageDraw.Draw(img)
    d.rectangle((60, 120, 1740, 860), fill=(30, 30, 30))   # a subject filling most of the picture
    d.rectangle((0, 0, 1800, 14), fill=(0, 0, 0))           # a scan's black edge: the frame rule moves the crop
    window = pf.Window((1080, 1350), name="ig carousel immersive")
    a = _with_focus(img, (60.0, 120.0, 1740.0, 860.0))
    blind = dataclasses.replace(a, focus=None, _sat={})
    without = pf.place(None, window, analysis=blind)
    got = pf.place(None, window, analysis=a)
    view = window.view_box()
    shown = lambda r: pf._overlap(a.focus, pf._sub(tuple(r["box"]), window.size, view))
    assert shown(got) >= shown(without) - 1e-6              # (a)


def test_cover_takes_the_full_width_photograph_when_the_subject_fits_its_hold():
    img = _picture(1200, 1800, seed=7)                      # a portrait photograph
    d = ImageDraw.Draw(img)
    d.rectangle((420, 300, 780, 950), fill=(200, 60, 40))   # a subject the full-width crop can hold whole
    a = _with_focus(img, (420.0, 300.0, 780.0, 950.0))
    fit = [(mode, pf.place(None, w, analysis=a)) for mode, w in cd.cover_windows()]
    mode, rec = next(((m, r) for m, r in fit if r["fit"]), (None, None))
    assert mode in ("full_bleed", "photo_over_ink"), [r["problems"] for _, r in fit]
    assert rec["held"] >= pf.FOCUS_KEEP


def test_cover_stands_contained_when_every_full_width_crop_cuts_the_subject():
    img = _picture(2400, 800, seed=9)                       # a panorama
    d = ImageDraw.Draw(img)
    d.rectangle((100, 200, 2300, 700), fill=(40, 160, 200))  # a subject spanning it: any 1080-wide crop cuts it
    a = _with_focus(img, (100.0, 200.0, 2300.0, 700.0))
    assert not any(pf.place(None, w, analysis=a)["fit"] for _m, w in cd.cover_windows())


def test_without_a_subject_nothing_changes():
    img = _picture(1600, 1000, seed=11)
    a = pf.analyse(img)
    for _m, w in cd.cover_windows():
        assert pf.place(None, w, analysis=a) == pf._place(None, w, analysis=a)
    w = pf.Window((1080, 1920), name="story")
    assert pf.place(None, w, (0.3, 0.5), analysis=a) == pf._place(None, w, (0.3, 0.5), analysis=a)


def test_cover_takes_the_full_width_photograph_when_the_subject_is_as_whole_as_the_hold_allows():
    img = _picture(1200, 1800, seed=7)
    d = ImageDraw.Draw(img)
    d.rectangle((420, 300, 780, 1100), fill=(200, 60, 40))  # 4 % of it below any full-width hold: not a cut
    a = _with_focus(img, (420.0, 300.0, 780.0, 1100.0))
    fit = [(m, pf.place(None, w, analysis=a)) for m, w in cd.cover_windows()]
    mode, rec = next(((m, r) for m, r in fit if r["fit"]), (None, None))
    assert mode in ("full_bleed", "photo_over_ink"), [r["problems"] for _, r in fit]
    assert pf.SUBJECT_CUT_MAX <= rec["held"] < pf.FOCUS_KEEP


def test_the_cover_hold_asks_for_subject_cut_max():
    assert all(w.hold_min == pf.SUBJECT_CUT_MAX for _m, w in cd.cover_windows())


def test_the_cover_keeps_its_layout_without_the_subject_when_that_is_still_fit():
    img = _picture(1200, 1800, seed=7)
    d = ImageDraw.Draw(img)
    d.rectangle((420, 300, 780, 950), fill=(200, 60, 40))
    a = _with_focus(img, (420.0, 300.0, 780.0, 950.0))
    blind = dataclasses.replace(a, focus=None, _sat={})
    first = next((m for m, w in cd.cover_windows() if pf.place(None, w, analysis=blind)["fit"]), None)
    order = [m for m, _w in cd.cover_order(None, a)]
    if first is not None:
        assert order[0] == first
    assert sorted(order) == sorted(m for m, _w in cd.cover_windows())


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
