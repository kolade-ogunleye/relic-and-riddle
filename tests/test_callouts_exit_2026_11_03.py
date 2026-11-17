"""Callout exit (review 2026-11-03, frame t=5.65 of the reference pair): the label text faded out before its plate
closed, so empty black plates sat on screen. Text and plate now leave together: on every frame through the exit, at
both 9:16 and 16:9, a plate is never visible without its text, measured from the alphas the frame drew and from the
delivered pixels in the plate and text regions. The leader and the marker retract only after the plate has gone."""
import numpy as np
import pytest
from PIL import Image, ImageFilter

from src import callouts
from src.config import VIDEO_FPS


def _photo(path, size, seed=1, lo=60, hi=240):
    rng = np.random.default_rng(seed)
    arr = rng.integers(lo, hi, (size[1] // 12, size[0] // 12, 3), dtype=np.uint8)
    Image.fromarray(arr).resize(size, Image.BICUBIC).save(path)
    return path


def _pt(label, x, y, fw, fh, kind="point", size=(1080, 1920)):
    box = (x - fw / 2, y - fh / 2, x + fw / 2, y + fh / 2)
    return (label, x, y, fw * fh / (size[0] * size[1]), fw, fh, kind, box)


# The reference pair (the door recess and the carved tuff wall) in both cuts.
PAIRS = {
    "916": ((1080, 1920), (1200, 2100), [_pt("stone door recess", 600, 900, 260, 300),
                                         _pt("carved tuff wall", 300, 1010, 300, 380, "extent")]),
    "169": ((1920, 1080), (2100, 1200), [_pt("stone door recess", 1100, 500, 260, 300, size=(1920, 1080)),
                                         _pt("carved tuff wall", 600, 620, 300, 380, "extent", size=(1920, 1080))]),
}


def _render(tmp_path, monkeypatch, fmt):
    size, photo_size, pts = PAIRS[fmt]
    w, h = size
    kept = callouts.declutter(callouts.clear_of_captions(pts, w, h), w, h)
    assert kept is not None and len(kept) == 2
    t_exit = callouts.exit_start()
    first = int(np.ceil(t_exit * VIDEO_FPS - 1e-6)) - 1
    frames = {}

    def encode(it, out, n, **k):
        for i, im in enumerate(it):
            if i >= first:
                frames[i] = np.asarray(im.convert("L"), dtype=np.float64)
        return out
    monkeypatch.setattr(callouts, "_encode", encode)
    trace: dict = {}
    photo = _photo(tmp_path / f"p{fmt}.jpg", photo_size)
    callouts.render(photo, kept, tmp_path / f"x{fmt}.mp4", width=w, height=h, reveal=[0.6, 2.2], record=trace)
    return trace, frames, first


def _regions(lab, shift, text_at, size):
    """(text glyph pixels, plate pixels clear of the text, a ring just outside the plate) as boolean frame masks."""
    w, h = size
    glyph = Image.new("L", size, 0)
    glyph.paste(lab["text"], (round(text_at[0]), round(text_at[1])))
    core = np.asarray(glyph.filter(ImageFilter.MinFilter(3))) >= 200
    near = np.asarray(glyph.filter(ImageFilter.MaxFilter(7))) > 0
    x0, y0, x1, y1 = (lab["box"][0] + shift[0], lab["box"][1] + shift[1], lab["box"][2] + shift[0],
                      lab["box"][3] + shift[1])
    yy, xx = np.mgrid[0:h, 0:w]
    ins = lab["radius"] + 2
    inner = (xx >= x0 + ins) & (xx < x1 - ins) & (yy >= y0 + ins) & (yy < y1 - ins)
    grown = (xx >= x0 - 12) & (xx < x1 + 12) & (yy >= y0 - 12) & (yy < y1 + 12)
    outer = (xx >= x0 - 4) & (xx < x1 + 4) & (yy >= y0 - 4) & (yy < y1 + 4)
    return core & inner, inner & ~near, grown & ~outer


@pytest.mark.parametrize("fmt", ["916", "169"])
def test_a_plate_is_never_visible_without_its_text_through_the_exit(tmp_path, monkeypatch, fmt):
    trace, frames, first = _render(tmp_path, monkeypatch, fmt)
    size = PAIRS[fmt][0]
    rows = [f for f in trace["frames"] if f["i"] >= first]
    n = len(trace["frames"])
    assert rows and rows[-1]["i"] == n - 1 and len(frames) == len(rows)
    assert len(trace["labels"]) == 2
    seen_partial = False
    for row in rows:
        i = row["i"]
        lum = frames[i]
        assert set(row["labels"]) == {0, 1}, (i, row["labels"].keys())
        for k, st in row["labels"].items():
            where = (fmt, i, round(row["t"], 3), k)
            # the drawn alphas: the plate never more opaque than its text, never there with the text gone
            assert st["plate_a"] <= st["text_a"] + 1e-9, (where, st["plate_a"], st["text_a"])
            if st["plate_a"] > 0:
                assert st["text_a"] > 0, where
                # the leader and marker stay whole until the plate has gone
                assert st["lead"] == pytest.approx(1.0) and st["g"] > 0.99, (where, st["lead"], st["g"])
            if 0 < st["plate_a"] < 1:
                seen_partial = True
            # the delivered pixels: where the plate darkens the photo, its text stands out of it
            glyph, plate, ring = _regions(trace["labels"][k], st["shift"], st["text_at"], size)
            assert glyph.sum() > 50 and plate.sum() > 200 and ring.sum() > 200, where
            dark = float(np.median(lum[ring]) - np.median(lum[plate]))
            lift = float(np.median(lum[glyph]) - np.median(lum[plate]))
            if dark > 20:
                assert lift >= 0.5 * dark, (where, dark, lift, st["plate_a"], st["text_a"])
    assert seen_partial, "the plates leave over several frames, not in one cut"
    # the last frame before the exit has every label whole; the last delivered frame has none
    assert all(st["plate_a"] == pytest.approx(1.0) and st["text_a"] == pytest.approx(1.0)
               for st in rows[0]["labels"].values())
    assert all(st["plate_a"] == 0 and st["text_a"] == 0 and st["lead"] == 0 and st["g"] == 0
               for st in rows[-1]["labels"].values())
