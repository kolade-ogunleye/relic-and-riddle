"""Art direction (quality step 2, operator 2026-10-24): an accent derived from the topic's hero photograph
with gold kept for figures, one type scale that stays readable on a phone, one motion table under which
every count-up rests early, and a bounded grade bias; the renderers read the palette made active for
the run, including inside the graphics worker thread."""

from PIL import Image

from src import art_direction as ad


def _photo(tmp_path, colour, name="hero.jpg"):
    p = tmp_path / name
    Image.new("RGB", (64, 64), colour).save(p, "JPEG")
    return p


def test_a_cool_hue_becomes_the_accent_and_a_gold_like_hue_its_complement(tmp_path):
    cool = ad.palette_from(_photo(tmp_path, (30, 90, 200), "sea.jpg"))
    assert cool["source"].startswith("sampled") and cool["contrast"] >= ad.MIN_CONTRAST
    r, g, b = cool["accent"]
    assert b > r                                               # the sampled blue, lifted for legibility
    sand = ad.palette_from(_photo(tmp_path, (235, 175, 100), "sand.jpg"))
    assert "complement" in sand["source"]                      # orange sits with gold: the structure goes cool
    r, g, b = sand["accent"]
    assert b > r and ad.contrast(sand["accent"]) >= ad.MIN_CONTRAST
    grey = ad.palette_from(_photo(tmp_path, (120, 120, 122), "bronze.jpg"))
    assert grey["accent"] == ad.DEFAULT_PALETTE["accent"] and grey["source"].startswith("default")
    assert ad.palette_from(tmp_path / "missing.jpg") == ad.DEFAULT_PALETTE


def test_the_type_scale_keeps_every_size_readable_on_a_phone():
    for w, h in ((1080, 1920), (1920, 1080)):
        ts = ad.type_scale(w, h)
        assert ts["figure"] > ts["title"] > ts["label"] >= ts["caption"] >= ts["eyebrow"]
        assert ts["eyebrow"] >= int(h * ad.READABLE_FRACTION)


def test_every_count_up_rests_before_half_the_clip_and_the_push_in_is_slight():
    clip = 6.0
    for k in (0, 1):
        assert ad.count_start(k) + ad.MOTION["count"] <= clip * ad.MOTION["count_done_by"]
    assert ad.push_in(0.0, clip) == 1.0 and abs(ad.push_in(clip, clip) - (1 + ad.MOTION["push_in"])) < 1e-9


def test_the_grade_bias_is_warm_for_a_warm_photo_cool_for_a_cool_one_and_bounded(tmp_path):
    assert ad.grade_bias(_photo(tmp_path, (240, 160, 60), "warm.jpg")) == 0.05
    assert ad.grade_bias(_photo(tmp_path, (40, 90, 230), "cool.jpg")) == -0.05
    assert abs(ad.grade_bias(_photo(tmp_path, (128, 128, 128), "grey.jpg"))) < 1e-9
    assert ad.grade_bias(tmp_path / "missing.jpg") == 0.0


def test_the_palette_is_recorded_for_the_run_and_read_by_the_renderers(tmp_path):
    pal = ad.record(tmp_path, _photo(tmp_path, (30, 90, 200)))
    assert (tmp_path / "palette.json").exists() and ad.accent() == tuple(pal["accent"])
    ad.use_palette(None)
    assert ad.accent() == ad.DEFAULT_PALETTE["accent"]
    from src import graphics
    import threading
    seen = []
    threading.Thread(target=lambda: (graphics.make_all({}, {}, tmp_path, palette=pal), seen.append(ad.accent()))).start()
    import time
    time.sleep(0.5)
    assert seen == [tuple(pal["accent"])]                      # made active inside the worker thread
