"""colour_balance VERSION 5, the lamp step, and the covers' hue (design critic round 4, 29 Oct 2026).

The critic found that version 4 leaves a yellow-green lamp cast on Derinkuyu's stone door (the blue gain stops at
GAIN_LIMIT, cast 0.42-0.47), and asked that the covers' still grade never re-saturate a corrected cast. The lamp
step finishes the correction on the green side of the warm range only: warm light and warm materials (hue at or
below 48 deg) come back exactly as they were, and every pixel keeps its luminance.
"""

import numpy as np
from PIL import Image

from src import colour_balance as cb


def _wall(seed=1, size=96):
    rng = np.random.default_rng(seed)
    grey = rng.uniform(40, 140, (size, size, 1))
    return np.repeat(grey, 3, axis=2) + rng.normal(0, 4, (size, size, 3))


def _jpeg(tmp_path, name, a):
    p = tmp_path / name
    Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB").save(p, "JPEG", quality=95)
    return p


def _rgb(p):
    return np.asarray(Image.open(p).convert("RGB"), dtype=np.float64)


def _neutral_cast(a):
    n = cb.scene(a)["neutral"]
    return float(np.max(np.abs(n.mean() / n - 1)))


def _lime_share(a):
    hue, sat = cb._hsv(a)
    return float(((hue >= 52) & (hue <= 90) & (sat > 0.3)).mean())


def test_a_yellow_green_cast_the_gains_cannot_reach_is_finished_by_the_lamp_step(tmp_path):
    """Derinkuyu's door: 70/71/31. With the blue gain capped, version 4 left a cast of about 0.45 and the stone
    read lime; version 5 takes most of what remains out."""
    door = _wall(21) * np.array([1.0, 1.02, 0.45])
    src = _jpeg(tmp_path, "25_commons.jpg", door)
    settings = cb.plan(Image.open(src))
    assert settings["gains"][2] == cb.GAIN_LIMIT[1]                 # the gain is capped: v4's residual
    assert settings["lamp_step"] == {"keep": cb.LAMP_STEP_KEEP, "pull": cb.LAMP_STEP_PULL,
                                     "hue": list(cb.LAMP_STEP_HUE)}
    gains_only = cb.apply(Image.open(src), {k: v for k, v in settings.items() if k != "lamp_step"})
    after = _rgb(cb.balanced(src))
    v4 = np.asarray(gains_only, dtype=np.float64)
    assert _neutral_cast(after) < 0.6 * _neutral_cast(v4)
    assert _lime_share(after) < 0.5 * _lime_share(v4)
    assert abs(cb._luma(after).std() - cb._luma(v4).std()) < 1.0     # contrast kept
    assert cb.VERSION >= 5                                            # the cache is re-keyed


def test_the_lamp_step_leaves_warm_pixels_exactly_and_keeps_luminance():
    """Amber, gold, candle and torch (hue 10-48) are never cooled or turned orange: the critic's 28-88 band took a
    third of their saturation. Each changed pixel keeps its luminance, and no two hues swap order."""
    hues = np.linspace(0, 359, 720)
    import colorsys
    ramp = np.array([colorsys.hsv_to_rgb(h / 360, 0.7, 0.8) for h in hues])[None]      # 1 x 720 x 3, 0-1
    out = cb.lamp_step(ramp)
    warm = hues <= cb.LAMP_STEP_HUE[0]
    assert np.array_equal(out[0, warm], ramp[0, warm])
    far = hues >= cb.LAMP_STEP_HUE[3]                                  # greens, blues, reds: untouched
    assert np.array_equal(out[0, far], ramp[0, far])
    assert np.allclose(cb._luma(out), cb._luma(ramp), atol=1e-6)
    h_out, s_out = cb._hsv(out * 255)
    band = (hues > cb.LAMP_STEP_HUE[0]) & (hues < cb.LAMP_STEP_HUE[3])
    assert np.all(np.diff(h_out[0, band]) > 0)                         # monotonic: no banding
    core = (hues >= cb.LAMP_STEP_HUE[1]) & (hues <= cb.LAMP_STEP_HUE[2])
    h_in, s_in = cb._hsv(ramp * 255)
    assert np.all(s_out[0, core] < s_in[0, core] * (cb.LAMP_STEP_KEEP + 0.15))
    assert np.all(h_out[0, core] >= cb.LAMP_STEP_HUE[0]) and np.all(h_out[0, core] < h_in[0, core])


def test_a_warm_object_in_a_yellow_green_scene_keeps_its_colour(tmp_path):
    """A lamp-lit lime room with an amber lamp shade in it: the room is finished, the shade keeps its hue."""
    a = _wall(22) * np.array([1.0, 1.02, 0.45])
    a[30:60, 30:60] = np.array([200.0, 120.0, 40.0])                  # hue 30: amber
    src = _jpeg(tmp_path, "room.jpg", a)
    settings = cb.plan(Image.open(src))
    assert settings.get("lamp_step")
    out = _rgb(cb.balanced(src))
    gains_only = np.asarray(cb.apply(Image.open(src), {k: v for k, v in settings.items() if k != "lamp_step"}),
                            dtype=np.float64)
    h4, s4 = cb._hsv(gains_only[34:56, 34:56])
    h5, s5 = cb._hsv(out[34:56, 34:56])
    assert abs(float(h5.mean() - h4.mean())) < 1.0 and float(s5.mean()) >= 0.98 * float(s4.mean())


def test_only_yellow_green_casts_take_the_lamp_step(tmp_path):
    """Magenta and blue casts are within the gains' reach (Derinkuyu 21, 23); a warm cast, daylight, sand and
    black-and-white keep version 4's settings, with no lamp step."""
    magenta = _wall(23) * np.array([1.15, 0.85, 1.1])
    blue = _wall(24) * np.array([0.85, 0.95, 1.25])
    warm = _wall(12) * np.array([1.25, 0.95, 0.55])
    sky = _wall(8) + 60
    sky[:48] = np.array([60.0, 120.0, 200.0])
    sand = np.clip(_wall(15) * 1.6 * np.array([1.0, 0.78, 0.45]), 0, 255)
    grey = np.repeat(_wall(3)[..., :1], 3, axis=2)
    for name, a in (("magenta.jpg", magenta), ("blue.jpg", blue), ("warm.jpg", warm), ("sky.jpg", sky),
                    ("sand.jpg", sand), ("bw.jpg", grey)):
        settings = cb.plan(Image.open(_jpeg(tmp_path, name, a)))
        assert settings is None or "lamp_step" not in settings, name


def test_a_cover_keeps_the_balanced_heros_hue_and_saturation(tmp_path):
    """The covers draw the balanced hero with no colour grade of their own (29 Oct): nothing puts a corrected lamp
    cast back or inflates saturation. A grade added later must keep this (the still-side twin of
    video_composer.film_grade_filter's hue-safe print)."""
    from src.visual_engine import VisualEngine
    from src.typography import cover_crop
    hero = cb.balanced(_jpeg(tmp_path, "25_commons.jpg", _wall(25, 480) * np.array([1.0, 1.02, 0.45])))
    copy = {"thumbnail": {"eyebrow": "CAPPADOCIA, TURKEY", "line1": "THE STONE DOOR", "line2": "ROLLED FROM INSIDE",
                          "tagline": "A city underground"}}
    cover, _ = VisualEngine().generate_thumbnails(hero, copy, tmp_path / "out", style="minimal", variants=False)
    ref = np.asarray(cover_crop(hero, Image.open(cover).size).convert("RGB"), dtype=np.float64)
    got = _rgb(cover)
    patch = (slice(40, 200), slice(40, 200))                           # a top corner: photo, no text
    h_ref, s_ref = cb._hsv(ref[patch])
    h_got, s_got = cb._hsv(got[patch])
    k = s_ref > 0.12
    assert abs(float(np.median(h_got[k]) - np.median(h_ref[k]))) < 2.0
    scale = got[patch].reshape(-1, 3).mean(axis=0) / ref[patch].reshape(-1, 3).mean(axis=0)
    assert float(scale.max() / scale.min()) < 1.03     # a neutral dim at most: every channel scaled alike, no tint


def test_a_balanced_hero_whose_cast_was_taken_out_gets_no_warm_bias(tmp_path):
    """After v5 the door no longer measures as cast, so the bias is decided from the copy's own record."""
    import json
    from PIL import Image
    from src import art_direction
    from src.lineage import sidecar
    hero = tmp_path / "hero.jpg"
    Image.new("RGB", (64, 64), (200, 150, 90)).save(hero)          # warm: would get a warm bias
    assert art_direction.grade_bias(hero) > 0
    sidecar(hero).write_text(json.dumps({"operation": "colour_balance", "settings": {"gains": [0.97, 0.96, 1.18]}}),
                             encoding="utf-8")
    assert art_direction.grade_bias(hero) == 0.0
    sidecar(hero).write_text(json.dumps({"operation": "colour_balance", "settings": {"gains": [1.0, 1.0, 1.0],
                                                                                     "gamma": 0.8}}), encoding="utf-8")
    assert art_direction.grade_bias(hero) > 0                       # only lifted: its warmth is its own
