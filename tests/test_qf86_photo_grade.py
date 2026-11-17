"""Actual composer pixels: photo curves never recolour authored graphic layers."""
import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image

from src import brand, graphics
from src import video_composer as vc
from src.delivery_profiles import get_delivery_profile
from src.encoding import BT709_TO_RGB, RGB_TO_BT709

TOKENS = ("INK", "INK2", "BONE", "STONE", "STONE_LT", "GOLD", "GOLD_DIM", "TEAL", "CORAL")


def lab(rgb):
    """CIE Lab D65; delta-E 76 is explicitly used throughout this evidence."""
    x = np.asarray(rgb, dtype=float) / 255
    x = np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
    xyz = x @ np.array([[.4124564, .3575761, .1804375],
                        [.2126729, .7151522, .0721750],
                        [.0193339, .1191920, .9503041]]).T
    xyz /= np.array([.95047, 1, 1.08883])
    f = np.where(xyz > (6 / 29) ** 3, np.cbrt(xyz), xyz / (3 * (6 / 29) ** 2) + 4 / 29)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]),
                     200 * (f[..., 1] - f[..., 2])], axis=-1)


def palette(width, height):
    a = np.empty((height, width, 3), np.uint8)
    for i, token in enumerate(TOKENS):
        a[:, i * width // len(TOKENS):(i + 1) * width // len(TOKENS)] = getattr(brand, token)
    return Image.fromarray(a)


def decoded(path, width, height, t):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path),
                          "-vf", f"{BT709_TO_RGB},format=rgb24", "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True, timeout=120).stdout
    return np.frombuffer(raw, np.uint8).reshape(height, width, 3)


def token_errors(frame):
    height, width = frame.shape[:2]
    measured = []
    for i in range(len(TOKENS)):
        x = int((i + .5) * width / len(TOKENS))
        measured.append(np.median(frame[height // 2 - 12:height // 2 + 12, x - 12:x + 12], axis=(0, 1)))
    reference = [getattr(brand, name) for name in TOKENS]
    return dict(zip(TOKENS, np.linalg.norm(lab(measured) - lab(reference), axis=1).tolist()))


def render_palette(work, profile_name, bias=0.0):
    work.mkdir(parents=True, exist_ok=True)
    profile = get_delivery_profile(profile_name)
    w, h = profile.width, profile.height
    source = work / "graphic_tokens.mp4"
    graphics._encode((palette(w, h) for _ in range(30)), source, 30, w, h)
    voice = work / "voice.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=220:sample_rate=48000", "-t", "1.5", str(voice)], check=True, timeout=60)
    master = work / "master.mp4"
    vc.VideoComposer().compose_video([source], voice, voice,
        [{"word": "Colour.", "start": .1, "dur": .3}], 1, master,
        durations=[1], grade_bias=bias, delivery_profile=profile)
    frame = decoded(master, w, h, .6)
    return {"delta_e76": token_errors(frame), "maximum_delta_e76": max(token_errors(frame).values())}, frame


def curve_ramp(bias=0.0):
    # 256 exact grey inputs spanning native 16-bit endpoints; capture
    # before its first colour conversion so YUV delivery quantization is separate.
    a = np.tile((np.arange(256, dtype=np.uint16) * 257)[None, None, :], (3, 16, 1))
    result = subprocess.run(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gbrp16le",
        "-s", "256x16", "-i", "-", "-vf", f"format=gbrp16le,{vc.film_grade_filter(bias)},format=gbrp16le",
        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gbrp16le", "-"],
        input=a.tobytes(), capture_output=True, check=True, timeout=60)
    planes = np.frombuffer(result.stdout, dtype="<u2").reshape(3, 16, 256)
    return {"levels": min(len(np.unique(p[8])) for p in planes),
            "black": planes[:, 8, 0].tolist(), "white": planes[:, 8, -1].tolist(),
            "monotone": bool(np.all(np.diff(planes[:, 8].astype(int), axis=1) > 0))}


@pytest.mark.parametrize("profile,bias", [("reel", 0.0), ("deep_dive", .05), ("reel", -.05)])
def test_native_graphic_tokens_survive_the_actual_master(tmp_path, profile, bias):
    result, _ = render_palette(tmp_path, profile, bias)
    assert result["maximum_delta_e76"] <= 2, result


@pytest.mark.parametrize("bias", [0.0, -.05, .05])
def test_real_curve_preserves_250_levels_and_black_zero(bias):
    result = curve_ramp(bias)
    assert result["levels"] >= 250, result
    assert result["black"] == [0, 0, 0], result
    assert result["white"] == [65535, 65535, 65535], result
    assert result["monotone"], result


def test_graphic_contrast_measurement_uses_the_ungraded_path(tmp_path):
    measured = graphics.graded_frame(palette(900, 400), tmp_path)
    assert measured is not None
    assert max(token_errors(np.asarray(measured)).values()) <= 2


def test_no_formula_lut_or_cache_is_used(tmp_path):
    root = Path(vc.__file__).resolve().parents[1]
    assert not (root / "assets/luts/kodak_2383.cube").exists()
    assert not (root / "scripts/generate_kodak_lut.py").exists()
    with patch.object(Path, "read_bytes", side_effect=AssertionError("No LUT reads")):
        curve = vc.film_grade_filter(.05, source=tmp_path / "old.cube", out_dir=tmp_path)
    assert "lut3d" not in curve and curve.startswith("curves=")
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("tamper", ["none", "marketing", "metadata", "domain_comment"])
def test_name_exception_is_exact_not_a_marketing_allowlist(tamper):
    root = Path(__file__).resolve().parents[1]
    script = root / "docs/reviews/quality-fixes-2026-11/QF-86/measure.py"
    spec = importlib.util.spec_from_file_location("qf86_policy", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    matches = subprocess.check_output(["git", "grep", "-n", "-i", "-E",
        r"kodak|national geographic|\bBBC\b|economist", "--", "src", "scripts", "assets", "main.py"],
        cwd=root, text=True, encoding="utf-8").splitlines()
    if tamper == "marketing":
        matches.append("main.py:1:# BBC quality grade")
    elif tamper == "metadata":
        matches = [line.replace("Kodak Ektachrome", "Kodak authentic emulation") for line in matches]
    elif tamper == "domain_comment":
        matches = [line + " # BBC grade" if line.startswith("src/design_cards.py:") else line for line in matches]
    result = module.audit_names(root, matches)
    assert result["passed_with_approved_exact_exception"] == (tamper == "none"), result


@pytest.mark.parametrize("profile,size,kind", [
    ("reel", (1200, 1600), "photo"),
    ("deep_dive", (2400, 1600), "photo"),
    ("deep_dive", (1200, 1600), "photo"),
    ("reel", (800, 1200), "essential"),
    ("reel", (1200, 1600), "illustration"),
])
def test_actual_still_branches_grade_only_photographs(tmp_path, profile, size, kind):
    source = tmp_path / ("gap_gen_fixture.png" if kind == "illustration" else "photo.png")
    colour = (85, 84, 45)
    Image.new("RGB", size, colour).save(source)
    if kind == "essential":
        from src.image_presentation import designate
        designate(source, "Explicit historical image fixture")
    voice = tmp_path / "voice.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=220:sample_rate=48000", "-t", "1.5", str(voice)], check=True, timeout=60)
    delivery = get_delivery_profile(profile)
    master = tmp_path / "photo-master.mp4"
    with patch.object(vc, "_run_ffmpeg", wraps=vc._run_ffmpeg) as run,             patch.object(vc, "render_cameras", wraps=vc.render_cameras) as cameras:
        vc.VideoComposer().compose_video([source], voice, voice,
            [{"word": "Colour.", "start": .1, "dur": .3}], 1, master,
            durations=[1], grade_bias=.05, delivery_profile=delivery)
    graph, graded = _graded_layers(run, cameras, vc.film_grade_filter(.05))
    assert graded == (0 if kind == "illustration" else 1)
    assert "curves=" not in graph.split("[video_raw]", 1)[1]
    frame = decoded(master, delivery.width, delivery.height, .6)
    h, w = frame.shape[:2]
    got = np.median(frame[h // 2 - 10:h // 2 + 10, w // 2 - 10:w // 2 + 10], axis=(0, 1))
    curve = "null" if kind == "illustration" else vc.film_grade_filter(.05)
    reference = subprocess.run(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", "32x32", "-i", "-", "-vf", f"format=gbrp16le,{curve},{RGB_TO_BT709},{BT709_TO_RGB},format=rgb24",
        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        input=bytes(colour) * 1024, capture_output=True, check=True, timeout=60).stdout
    want = np.frombuffer(reference, np.uint8).reshape(32, 32, 3)[16, 16]
    assert np.max(np.abs(got - want)) <= 3, (kind, profile, got, want)




def _graded_layers(run, cameras, grade):
    """(graph, how many layers take `grade`): in the master's graph (a parallax clip, an essential full image) or in
    the shared camera's clip (QF-22: a moved still or the AI hook clip, graded in RGB before its conversion)."""
    graphs = [call.args[0][call.args[0].index("-filter_complex") + 1]
              for call in run.call_args_list if "-filter_complex" in call.args[0]]
    graph = next(g for g in graphs if "[video_raw]" in g)
    in_camera = [opts.get("grade") for call in cameras.call_args_list for *_, opts in call.args[0]]
    assert all(g in (None, grade) for g in in_camera), in_camera
    in_graph = f"format=gbrp16le,{grade},{RGB_TO_BT709}[v0]" in graph
    assert graph.count("curves=") == int(in_graph)
    return graph, int(in_graph) + sum(1 for g in in_camera if g)


def _clip_of(path, colour=(85, 84, 45), size=(1080, 1920)):
    path.parent.mkdir(parents=True, exist_ok=True)
    return graphics._encode((Image.new("RGB", size, colour) for _ in range(30)), path, 30, *size)


def _still(path, colour=(85, 84, 45), size=(1080, 1920)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, colour).save(path)
    return path


def _lineage_case(tmp_path, kind):
    """(clip, expected is_photo_clip) for one lineage shape."""
    from src.lineage import record_derived
    photo = _still(tmp_path / "src" / "04_commons.jpg")
    settings = {"preset": "hook", "duration": 1.0, "fps": 30, "variant": 0}
    if kind == "parallax_of_photo":
        clip = _clip_of(tmp_path / "parallax" / "scene_01.mp4")
        record_derived(clip, [photo], "parallax", settings)
        return clip, True
    if kind == "parallax_of_upscaled_photo":
        up = _still(tmp_path / "upscaled" / "04_commons_ab.jpg")
        record_derived(up, [photo], "upscale", {})
        clip = _clip_of(tmp_path / "parallax" / "scene_02.mp4")
        record_derived(clip, [up], "parallax", settings)
        return clip, True
    if kind == "ai_motion_of_photo":
        anchor = _still(tmp_path / "motion" / "motion_01_anchor.jpg")
        record_derived(anchor, [photo], "motion_anchor_frame", {})
        clip = _clip_of(tmp_path / "motion" / "motion_01.mp4")
        record_derived(clip, [anchor], "ai_motion", {"provider": "fixture", "ai_generated": True})
        return clip, True
    if kind == "parallax_of_illustration":
        art = _still(tmp_path / "src" / "gap_gen_03.png")
        clip = _clip_of(tmp_path / "parallax" / "scene_03.mp4")
        record_derived(clip, [art], "parallax", settings)
        return clip, False
    if kind == "parallax_of_upscaled_illustration":
        art = _still(tmp_path / "src" / "gap_gen_04.png")
        up = _still(tmp_path / "upscaled" / "up_04.png")
        record_derived(up, [art], "upscale", {})
        clip = _clip_of(tmp_path / "parallax" / "scene_04.mp4")
        record_derived(clip, [up], "parallax", settings)
        return clip, False
    if kind == "parallax_chain_broken":
        # an upscaled illustration whose source has gone: the chain cannot be checked, so not shown to be a photo
        art = _still(tmp_path / "src" / "gap_gen_05.png")
        up = _still(tmp_path / "upscaled" / "up_05.png")
        record_derived(up, [art], "upscale", {})
        clip = _clip_of(tmp_path / "parallax" / "scene_05.mp4")
        record_derived(clip, [up], "parallax", settings)
        art.unlink()
        return clip, False
    if kind == "parallax_stale_clip":
        clip = _clip_of(tmp_path / "parallax" / "scene_06.mp4")
        record_derived(clip, [photo], "parallax", settings)
        _clip_of(clip, colour=(10, 10, 10))   # the clip changed after its record
        return clip, False
    if kind == "parallax_record_of_graphic_still":
        g = _still(tmp_path / "graphics" / "graphic_numbers.jpg")
        clip = _clip_of(tmp_path / "parallax" / "scene_07.mp4")
        record_derived(clip, [g], "parallax", settings)
        return clip, False
    if kind == "ai_motion_of_illustration":
        art = _still(tmp_path / "src" / "gen_08.png")
        anchor = _still(tmp_path / "motion" / "motion_08_anchor.jpg")
        record_derived(anchor, [art], "motion_anchor_frame", {})
        clip = _clip_of(tmp_path / "motion" / "motion_08.mp4")
        record_derived(clip, [anchor], "ai_motion", {"provider": "fixture", "ai_generated": True})
        return clip, False
    if kind == "hook_frame_with_parallax_record":
        clip = _clip_of(tmp_path / "cards" / "hook_frame.mp4")
        record_derived(clip, [photo], "parallax", settings)
        return clip, False
    if kind == "graphic_clip":
        return _clip_of(tmp_path / "graphics" / "graphic_numbers.mp4"), False
    if kind == "footage_clip":
        return _clip_of(tmp_path / "footage" / "footage_01.mp4"), False
    if kind == "no_lineage":
        return _clip_of(tmp_path / "parallax" / "scene_09.mp4"), False
    raise AssertionError(kind)


LINEAGE_KINDS = ["parallax_of_photo", "parallax_of_upscaled_photo", "ai_motion_of_photo", "parallax_of_illustration",
                 "parallax_of_upscaled_illustration", "parallax_chain_broken", "parallax_stale_clip",
                 "parallax_record_of_graphic_still", "ai_motion_of_illustration", "hook_frame_with_parallax_record",
                 "graphic_clip", "footage_clip", "no_lineage"]


@pytest.mark.parametrize("kind", LINEAGE_KINDS)
def test_is_photo_clip_fails_closed(tmp_path, kind):
    """Only a clip whose own record names a photo operation and whose whole chain verifies as photographs is a
    photo clip; an illustration anywhere in the chain, a broken or stale chain, a graphic, a hook frame or footage
    is not (review QF-86 r1, non-blocking 1)."""
    clip, expected = _lineage_case(tmp_path, kind)
    assert vc.is_photo_clip(clip) is expected, kind


STILL_KINDS = ["downloaded_photo", "upscaled_photo", "upscaled_illustration", "upscale_illustration_source_deleted",
               "upscale_photo_source_deleted", "stale_upscale", "upscale_of_graphic_still", "graphic_still",
               "hook_frame_still", "illustration_still", "footage_still"]


@pytest.mark.parametrize("kind", STILL_KINDS)
def test_is_photograph_fails_closed_like_the_clip_rule(tmp_path, kind):
    """A still is graded only when it is shown to be a photograph: the clips' stem rule over its name and its whole
    lineage chain, and a chain that verifies. A broken or stale chain, or a graphic, hook frame, illustration or
    footage anywhere in it, keeps its own colour (review QF-86 r2, non-blocking 1). The master's still branch and
    the photo layers inside graphics use the one rule."""
    from src import photo_grade
    from src.lineage import record_derived
    photo = _still(tmp_path / "src" / "04_commons.jpg")
    expected = kind in ("downloaded_photo", "upscaled_photo")
    if kind == "downloaded_photo":
        still = photo
    elif kind.startswith(("upscale", "stale")):
        source = {"upscaled_photo": photo, "upscale_photo_source_deleted": photo, "stale_upscale": photo,
                  "upscale_of_graphic_still": _still(tmp_path / "graphics" / "graphic_numbers.jpg")}.get(kind)
        source = source or _still(tmp_path / "src" / "gap_gen_03.png")
        still = _still(tmp_path / "upscaled" / "up_01.png")
        record_derived(still, [source], "upscale", {})
        if kind.endswith("source_deleted"):
            source.unlink()
        if kind == "stale_upscale":
            _still(still, colour=(10, 10, 10))   # the upscale changed after its record
    else:
        still = _still(tmp_path / {"graphic_still": "graphics/graphic_map.jpg", "hook_frame_still": "cards/hook_frame.jpg",
                                   "illustration_still": "src/gen_02.png", "footage_still": "footage/footage_01.jpg"}[kind])
    assert vc.is_photo_still(still) is expected, kind
    assert photo_grade.is_photograph(still) is expected, kind


@pytest.mark.parametrize("kind", ["parallax_of_photo", "ai_motion_of_photo", "graphic_clip", "footage_clip",
                                  "parallax_of_upscaled_illustration", "hook_frame_with_parallax_record"])
def test_a_photo_clip_takes_the_photo_curve_and_other_clips_do_not(tmp_path, kind):
    """Real Reels carry their photos as parallax clips (src/parallax.py) and open on an AI motion clip animated
    from a photo (src/motion.py): both take the photo curve, or a photo's grade would depend on how it moves.
    Graphic, hook-frame, illustration and footage clips keep their supplied colour."""
    colour = (85, 84, 45)
    clip, photo_clip = _lineage_case(tmp_path, kind)
    voice = tmp_path / "voice.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=220:sample_rate=48000", "-t", "1.5", str(voice)], check=True, timeout=60)
    master = tmp_path / "clip-master.mp4"
    with patch.object(vc, "_run_ffmpeg", wraps=vc._run_ffmpeg) as run,             patch.object(vc, "render_cameras", wraps=vc.render_cameras) as cameras:
        vc.VideoComposer().compose_video([clip], voice, voice, [{"word": "Colour.", "start": .1, "dur": .3}], 1,
                                         master, durations=[1], grade_bias=.05,
                                         delivery_profile=get_delivery_profile("reel"))
    _, graded = _graded_layers(run, cameras, vc.film_grade_filter(.05))
    assert graded == (1 if photo_clip else 0)
    if kind == "ai_motion_of_photo":   # the hook clip moves through the shared camera (QF-22), graded there
        assert [opts.get("preset") for call in cameras.call_args_list for *_, opts in call.args[0]] == ["hook"]
    frame = decoded(master, 1080, 1920, .6)
    got = np.median(frame[950:970, 530:550], axis=(0, 1))
    if photo_clip:
        assert got[0] - got[2] > colour[0] - colour[2] + 4, got   # the warm bias reached the photo
    else:
        assert np.max(np.abs(got - np.array(colour))) <= 3, got


@pytest.mark.parametrize("bias", [0.0, .036, .05, -.05])
def test_a_photo_drawn_inside_a_graphic_matches_the_same_photo_bare(tmp_path, bias):
    """review QF-86 r1, blocking 1: a graphic draws its photo through photo_grade (the master's own curve and the
    run's bias) before anything is drawn on it, and the master leaves the graphic alone, so the photo delivered
    inside a graphic matches the same photo delivered bare (lens off in both: the grade alone)."""
    from src import photo_grade
    from src.lineage import record_derived
    rng = np.random.default_rng(7)
    arr = (rng.random((96, 54, 3)) * np.array([230, 190, 150]) + 15).astype(np.uint8)
    photo = tmp_path / "src" / "04_commons.png"
    photo.parent.mkdir()
    Image.fromarray(arr).resize((1080, 1920), Image.BICUBIC).save(photo)
    raw = Image.open(photo).convert("RGB")
    (tmp_path / "parallax").mkdir()
    (tmp_path / "graphics").mkdir()
    bare = graphics._encode((raw for _ in range(30)), tmp_path / "parallax" / "scene_01.mp4", 30, 1080, 1920)
    record_derived(bare, [photo], "parallax", {"preset": "hook", "duration": 1.0, "fps": 30, "variant": 0})
    layer = photo_grade.cover(photo, (1080, 1920), bias)
    inside = graphics._encode((layer for _ in range(30)), tmp_path / "graphics" / "graphic_photo.mp4", 30, 1080, 1920)
    (tmp_path / "ungraded").mkdir()
    ungraded = graphics._encode((raw for _ in range(30)), tmp_path / "ungraded" / "graphic_photo.mp4", 30, 1080, 1920)
    voice = tmp_path / "voice.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=220:sample_rate=48000", "-t", "1.5", str(voice)], check=True, timeout=60)
    frames = []
    with patch.object(vc, "picture_vignette", lambda width, height: "null"):
        for k, clip in enumerate((bare, inside, ungraded)):
            out = tmp_path / f"m{k}.mp4"
            vc.VideoComposer().compose_video([clip], voice, voice, [], 1, out, durations=[1], grade_bias=bias,
                                             delivery_profile=get_delivery_profile("reel"))
            frames.append(decoded(out, 1080, 1920, .6))
    gap = [np.linalg.norm(lab(frames[0][::4, ::4]) - lab(f[::4, ::4]), axis=-1) for f in frames[1:]]
    graded, plain = (float(np.median(g)) for g in gap)
    # The floor does not depend on the bias: median 1.61-1.66 on this noise field at all four biases (p95 3.4-3.8
    # with the encoder this machine picks), about 1.1-1.3 on the real photos (measure.py cut_gap). It is not the
    # 8-bit table nor the graphic's second encode (QF-86/floor.py: a 16-bit dithered layer or a lossless graphic
    # encode leave it within 0.08). It is grading before rather than after 4:2:0 chroma subsampling, plus the
    # parallax-clip and graphic-clip paths inside compose_video differing even with the curve off (the parallax
    # path is 0.5-0.7 code darker), plus the delivery encode's noise on two differently processed pictures.
    assert graded <= 2.0 and float(np.percentile(gap[0], 95)) <= 4.5, (bias, graded)
    if abs(bias) >= .036:   # an ungraded photo layer shows the run's bias as a cut (review r1: CIE76 2.2-4.0)
        assert plain >= graded + 1.0, (bias, graded, plain)


def _red_minus_blue(a) -> float:
    a = np.asarray(a, np.float64)
    return float((a[..., 0] - a[..., 2]).mean())


def test_the_renderers_draw_their_photo_through_the_photo_grade(tmp_path):
    """Each photo-carrying renderer's photo layer is photo_grade's (stat card backdrops, timeline, then-and-now pair,
    callout photo), at the bias the run sets with photo_grade.using; an illustration stays ungraded."""
    import inspect

    from src import callouts, design_cards, photo_grade, then_now
    photo = _still(tmp_path / "04_commons.jpg", colour=(150, 120, 90), size=(1200, 2100))
    shown = {}
    for bias in (.05, 0.0):
        with photo_grade.using(bias):
            shown[bias] = (graphics.timeline_backdrop(photo, 540, 960), graphics.stat_backdrop_wide(photo, 960, 540),
                           then_now.frame_pair(photo, photo, 540, 960)[0], callouts._full_res_view(photo, 540, 960)[0])
    for warm, neutral in zip(shown[.05], shown[0.0]):
        assert _red_minus_blue(warm) > _red_minus_blue(neutral)
    expected = np.asarray(photo_grade.grade(Image.new("RGB", (4, 4), (150, 120, 90)), .05))[0, 0].astype(int)
    assert np.max(np.abs(np.asarray(shown[.05][3])[400, 200].astype(int) - expected)) <= 1
    assert "photo_grade import cover" in inspect.getsource(callouts.render)
    assert "is_photograph" in inspect.getsource(design_cards.plan_hook_frame)
    art = _still(tmp_path / "gap_gen_01.png", colour=(150, 120, 90))
    assert np.array_equal(np.asarray(photo_grade.cover(art, (64, 64), .05)),
                          np.asarray(Image.new("RGB", (64, 64), (150, 120, 90))))


def test_the_layer_grade_is_the_masters_curve():
    """photo_grade's table is the master's film_grade_filter run in gbrp16le: equal to within one 8-bit code."""
    from src import photo_grade
    for bias in (0.0, .041, -.05):
        a = np.tile(np.arange(256, dtype=np.uint8)[None, :, None], (4, 1, 3))
        ours = np.asarray(photo_grade.grade(Image.fromarray(a), bias)).astype(int)
        raw = subprocess.run(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "256x4", "-i", "-",
                              "-vf", f"format=gbrp16le,{vc.film_grade_filter(bias)},format=rgb24", "-frames:v", "1",
                              "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             input=a.tobytes(), capture_output=True, check=True, timeout=60).stdout
        theirs = np.frombuffer(raw, np.uint8).reshape(4, 256, 3).astype(int)
        assert np.abs(ours - theirs).max() <= 1, bias
