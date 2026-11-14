"""rc72 items 5-11 (production run_derinkuyu_underground_city_1793286295 passed every gate with weak content).

5. the narration never placed the site (Cappadocia, Turkey), so the rendered map stood on no beat;
6. the title and caption told the 1963 discovery, the narration a list of measurements;
7. a 15-word hook was only warned about;
8. "280 feet (85 m)" stood in the narration;
9. one shot was held over two lines (beats 5/6, 10/11) while a graphic and reserve photos were unused;
10. Kling refused "Account balance not enough" in the log only; motion fell back to parallax silently;
11. dark, murky stills with a yellow-green cast: a gentle, capped colour and brightness balance.
No model, network or repository write: the writer and the picture judge are stubs, files go to tmp_path.
"""
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src import colour_balance as cb, graphic_match, motion, provider_status, scene_planner, script_engine
from src.lineage import record_graphic, source_chain
from src.models import ScriptPackage

TOPIC = {"id": "derinkuyu_underground_city", "title": "Derinkuyu Underground City",
         "location": {"name": "Derinkuyu Underground City", "city": "Derinkuyu", "region": "Nevşehir / Cappadocia",
                      "country": "Turkey"}}
SHEET = {"topic_id": "t", "sources": [], "facts": []}


# ---------------------------------------------------------------- 5-7: the story the draft tells

def test_the_place_names_are_the_region_and_country_not_the_sites_own_name():
    assert script_engine.place_names(TOPIC) == ["Nevşehir", "Cappadocia", "Turkey"]
    unplaced = "Derinkuyu reaches deep. Its shafts carry air. Stables fill the upper floors. Who dug it?"
    assert script_engine.place_problems(unplaced, TOPIC)[0].startswith(script_engine.STORY_PROBLEM)
    assert script_engine.place_problems("Beneath Cappadocia, a city hides. It is deep.", TOPIC) == []
    assert script_engine.place_problems("Beneath Nevsehir, a city hides.", TOPIC) == []   # folded: ş = s
    late = "One. Two. Three. In Turkey, a city."
    assert script_engine.place_problems(late, TOPIC)                                 # the 4th sentence is too late
    assert script_engine.place_problems(unplaced, {"id": "x", "title": "X"}) == []     # nothing to name: no rule


def test_a_title_and_caption_story_the_narration_never_tells_is_found(curated_payload):
    pkg = ScriptPackage.model_validate(dict(curated_payload, title="The 1963 basement that hid a city",
                                            caption="An ordinary basement renovation in 1963 uncovered a city.\n\nMore.",
                                            narration_script="A city lies 280 feet down. Its shafts carry air."))
    found = script_engine.told_story_problems(pkg)
    assert found and "1963" in found[0]
    told = pkg.model_copy(update={"narration_script": "In 1963, a man knocked through a wall. A city lay behind it."})
    assert script_engine.told_story_problems(told) == []


def test_story_problems_need_the_topic_being_written(curated_payload):
    pkg = ScriptPackage.model_validate(dict(curated_payload, narration_script="Deep. Dark. Old. Quiet."))
    assert script_engine.story_problems(pkg) == []                                   # no run: no topic
    token = script_engine._draft_topic.set(TOPIC)
    try:
        assert any("places the site" in p for p in script_engine.draft_problems(pkg))
    finally:
        script_engine._draft_topic.reset(token)
    # Kept like a weak hook when the edit cannot fix it: never a rewrite on its own.
    assert script_engine.blocking_problems([script_engine.STORY_PROBLEM + "x"]) == []


def _engine(monkeypatch, curated_payload, narration, revise):
    curated_payload["payoff_sentence"] = "Derinkuyu's upper floors held stables."
    engine = script_engine.ScriptEngine()
    monkeypatch.setattr(script_engine, "_ready", lambda: True)
    monkeypatch.setattr(engine, "_sheet", lambda topic: SHEET)
    monkeypatch.setattr(engine, "_sheet_context", lambda sheet: ("notes", []))
    monkeypatch.setattr(engine, "_with_graphics", lambda core, notes, retrieved, feedback: core)
    monkeypatch.setattr(engine, "_finalize", lambda pkg, topic, **k: {"narration_script": pkg.narration_script,
                                                                       "hook_sentence": pkg.hook_sentence})
    monkeypatch.setattr(script_engine, "validate_structure", lambda pkg: [])
    monkeypatch.setattr(script_engine, "spoken_length_problems", lambda pkg: [])
    monkeypatch.setattr(script_engine.improve, "note_problems", lambda problems, source: None)
    written = []
    monkeypatch.setattr(engine, "_generate_with_claude", lambda topic, feedback: written.append(1) or ScriptPackage.model_validate(
        dict(curated_payload, narration_script=narration, hook_sentence=narration.split(". ")[0] + ".",
             title="Derinkuyu's hidden levels", caption="A city cut into rock.\n\n#Derinkuyu")))
    edits = []

    def surgical(previous, findings, sheet):
        edits.append(findings)
        return revise(previous)
    monkeypatch.setattr(engine, "_revise_surgically", surgical)
    return engine, written, edits


LONG_HOOK = "Derinkuyu reaches far down into the soft volcanic rock where thousands of people once hid together."
BODY = " Its shafts carry air. Derinkuyu's upper floors held stables. Who dug it?"


def test_an_unplaced_site_and_a_long_hook_get_one_opening_edit_not_a_rewrite(monkeypatch, curated_payload):
    fixed = "Beneath Cappadocia, Turkey, Derinkuyu hid thousands."
    engine, written, edits = _engine(monkeypatch, curated_payload, LONG_HOOK + BODY, lambda prev: ScriptPackage.model_validate(
        dict(prev, narration_script=fixed + BODY)))
    out = engine.generate_script(TOPIC)
    assert written == [1] and len(edits) == 1 and edits[0].startswith("OPENING:")      # one edit, no rewrite
    assert "places the site" in edits[0] and "16-word sentence" in edits[0]
    assert out["narration_script"].startswith(fixed) and out["hook_sentence"] == fixed   # the hook follows


def test_an_opening_edit_that_fixes_nothing_cannot_keep_the_draft(monkeypatch, curated_payload):
    engine, written, edits = _engine(monkeypatch, curated_payload, LONG_HOOK + BODY,
                                     lambda prev: ScriptPackage.model_validate(prev))
    monkeypatch.setattr(engine, "_load_curated", lambda topic_id: None)
    with pytest.raises(script_engine.ScriptUnavailableError):
        engine.generate_script(TOPIC)
    assert written == [1, 1, 1] and len(edits) == 1


def test_the_writer_is_told_the_story_rules():
    rules = script_engine.USER_PROMPT_TEMPLATE
    assert "most surprising discovery or human-stakes fact" in rules and "within the first 3 sentences" in rules
    assert "tell the same story" in rules and "in brackets" in rules
    assert "OPENING finding" in script_engine.REVISE_SYSTEM


def test_the_map_gets_the_line_that_names_the_region(tmp_path):
    code = tmp_path / "draw.py"
    code.write_bytes(b"code")
    m = tmp_path / "graphic_map.mp4"
    m.write_bytes(b"map")
    m.with_suffix(".jpg").write_bytes(b"poster")
    record_graphic(m, code, {"builder": "map", "place": {"label": "Derinkuyu Underground City", "country": "Turkey",
                                                         "region": "Nevşehir / Cappadocia"}})
    beats = [{"text": "Thousands once hid underground."},
             {"text": "Beneath the plains of Cappadocia, a city waited."},
             {"text": "Its shafts carried air."}]
    assert scene_planner.lock_narrative_graphics(beats, [m]) == {1: m}
    assert graphic_match.score(m, "Its shafts carried air.") < graphic_match.THRESHOLD


# ---------------------------------------------------------------- 8: bracketed asides

def test_bracketed_asides_leave_the_spoken_narration():
    assert script_engine.spoken_narration("More than 600 entrances descend 280 feet (85 m) underground.") == \
        "More than 600 entrances descend 280 feet underground."
    assert script_engine.spoken_narration("It is old (very old).") == "It is old."
    assert script_engine.spoken_narration("No brackets here.") == "No brackets here."


def test_finalize_strips_the_narration_but_keeps_the_caption(curated_payload):
    narration = curated_payload["narration_script"]
    first = narration.split(". ")[0]
    pkg = ScriptPackage.model_validate(dict(curated_payload, narration_script=first + " (85 m). " + narration.split(". ", 1)[1],
                                            caption="It runs 280 feet (85 m) down.\n\n#Derinkuyu"))
    out = script_engine.ScriptEngine._finalize(pkg, TOPIC, origin="claude")
    assert "(85 m)" not in out["narration_script"] and "(85 m)" in out["caption"]
    assert out["word_count"] == len(out["narration_script"].split())


# ---------------------------------------------------------------- 9: a shot held across two lines

def _photo(tmp_path, name, shade):
    p = tmp_path / name
    Image.new("RGB", (64, 64), (shade, 40, 40)).save(p)
    return p


@pytest.fixture
def judged(monkeypatch):
    """The fit check's light model: a picture fits a line when its file name shares a word with the line."""
    from src import beat_images, config
    monkeypatch.setattr(config, "PICTURE_VISION_CHECK", True)
    monkeypatch.setattr(beat_images, "load_fit_cache", lambda: {})
    monkeypatch.setattr(beat_images, "save_fit_cache", lambda cache: None)

    def fit(path, line, subject, cache, digest=None):
        words = set(Path(path).stem.lower().split("_"))
        return {"fits": any(w in line.lower() for w in words if len(w) > 3), "reason": "judged", "cached": False}
    monkeypatch.setattr(beat_images, "line_fit", fit)


def _beats(*lines):
    return [{"start": 3.0 * i, "end": 3.0 * (i + 1), "text": t} for i, t in enumerate(lines)]


def test_a_reserve_photo_is_preferred_over_holding_the_shot(tmp_path, judged):
    hook, shaft, stable = (_photo(tmp_path, n, s) for n, s in (("hook.jpg", 10), ("shaft.jpg", 60), ("stable.jpg", 90)))
    chapel = _photo(tmp_path, "chapel.jpg", 120)
    beats = _beats("A city underground.", "Air came down the shaft.", "A chapel was carved below.")
    out, report = scene_planner.fit_to_lines(beats, [hook, shaft, stable], [hook, shaft, stable], "Derinkuyu",
                                             reserve=[chapel])
    assert out[2] == chapel and report["held"] == [] and report["swapped"][-1]["reserve"] is True


def test_an_unused_graphic_is_preferred_over_holding_the_shot(tmp_path, judged):
    hook, shaft, stable = (_photo(tmp_path, n, s) for n, s in (("hook.jpg", 10), ("shaft.jpg", 60), ("stable.jpg", 90)))
    code = tmp_path / "draw.py"
    code.write_bytes(b"code")
    numbers = tmp_path / "graphic_numbers.mp4"
    numbers.write_bytes(b"n")
    numbers.with_suffix(".jpg").write_bytes(b"poster")
    record_graphic(numbers, code, {"builder": "numbers", "payload": {"stat_cards": [{"value": "18", "label": "levels"}]}})
    beats = _beats("A city underground.", "Air came down the shaft.", "A chapel was carved below.")
    out, report = scene_planner.fit_to_lines(beats, [hook, shaft, stable], [hook, shaft, stable, numbers], "Derinkuyu",
                                             durations=[3.0, 3.0, 3.0, 3.0][:3] + [9.0])
    assert out[2] == numbers and report["held"] == []
    assert report["unshown"][-1]["fallback"] == "graphic" and "graphics is shown instead" in scene_planner.fit_note(report)


def test_with_neither_the_shot_is_held_as_before(tmp_path, judged):
    hook, shaft, stable = (_photo(tmp_path, n, s) for n, s in (("hook.jpg", 10), ("shaft.jpg", 60), ("stable.jpg", 90)))
    beats = _beats("A city underground.", "Air came down the shaft.", "A chapel was carved below.")
    out, report = scene_planner.fit_to_lines(beats, [hook, shaft, stable], [hook, shaft, stable], "Derinkuyu")
    assert out[2] == shaft and report["held"][-1]["beat"] == 3


# ---------------------------------------------------------------- 10: a paid provider out of credit

class _Resp:
    status_code, content = 400, b"x"
    text = '{"code": 1102, "message": "Account balance not enough"}'

    def json(self):
        return {"code": 1102, "message": "Account balance not enough"}


def test_kling_out_of_credit_is_on_the_dashboard_and_the_card(monkeypatch, tmp_path):
    img = tmp_path / "p.jpg"
    Image.new("RGB", (32, 32)).save(img)
    monkeypatch.setattr(motion, "_jwt", lambda: "token", raising=False)
    monkeypatch.setattr(motion.requests, "post", lambda *a, **k: _Resp())
    try:
        with pytest.raises(motion.MotionError):
            motion.animate_kling(img, tmp_path / "out.mp4")
    except TypeError:
        pytest.skip("animate_kling needs credentials in this build")
    assert "kling" in provider_status.capped()
    lines = " ".join(provider_status.dashboard_lines())
    assert "Kling is unavailable" in lines and "parallax" in lines
    assert motion.fallback_note([], tried_paid=True) == "🎞 Opening motion: parallax (Kling out of credit)"
    assert motion.fallback_note(["kling-3.0"], tried_paid=True) == "" and motion.fallback_note([], tried_paid=False) == ""
    provider_status.clear("kling")
    assert motion.fallback_note([], tried_paid=True) == ""


def test_only_a_credit_refusal_counts():
    assert motion.out_of_credit("Account balance not enough") and motion.out_of_credit("Insufficient credit")
    assert not motion.out_of_credit("image too small") and not motion.out_of_credit("")


# ---------------------------------------------------------------- 11: colour and brightness balance

def _img(tmp_path, name, array):
    p = tmp_path / name
    Image.fromarray(np.clip(array, 0, 255).astype(np.uint8), "RGB").save(p, "JPEG", quality=95)
    return p


def _scene(seed=1):
    rng = np.random.default_rng(seed)
    base = rng.uniform(40, 200, (96, 96, 3))
    return base


def _wall(seed=1):
    """A lamp-lit stone wall's own colour: textured, near grey, a little chroma noise."""
    rng = np.random.default_rng(seed)
    grey = rng.uniform(40, 140, (96, 96, 1))
    return np.repeat(grey, 3, axis=2) + rng.normal(0, 4, (96, 96, 3))


def _cast(a):
    m = a.reshape(-1, 3).mean(axis=0)
    return float(np.max(np.abs(m.mean() / m - 1)))


def test_a_yellow_green_cast_is_reduced_within_its_cap(tmp_path):
    a = _wall() * np.array([1.15, 1.2, 0.7])         # Derinkuyu's yellow-green lamp on grey stone
    src = _img(tmp_path, "03_commons.jpg", a)
    out = cb.balanced(src)
    assert out != src and out.name == src.with_suffix(".png").name              # its own folder, lossless copy
    before, after = (np.asarray(Image.open(p).convert("RGB"), dtype=float) for p in (src, out))
    assert _cast(after) < _cast(before) * 0.75                                   # reduced, toward grey
    record = source_chain(out)
    assert record and json.loads((out.parent / (out.name + ".lineage.json")).read_text(encoding="utf-8")
                                 if (out.parent / (out.name + ".lineage.json")).is_file() else "{}") is not None
    gains = cb.plan(Image.open(src))["gains"]
    assert all(cb.GAIN_LIMIT[0] - 0.05 <= g <= cb.GAIN_LIMIT[1] + 0.05 for g in gains)


def test_a_dark_exposure_is_lifted_within_its_cap_and_keeps_its_contrast(tmp_path):
    a = _scene(2) * 0.35                                 # mean luminance about 0.17
    src = _img(tmp_path, "dark.jpg", a)
    out = cb.balanced(src)
    before, after = (np.asarray(Image.open(p).convert("RGB"), dtype=float) / 255 for p in (src, out))
    lb, la = cb._luma(before), cb._luma(after)
    assert la.mean() > lb.mean() and la.mean() <= lb.mean() + cb.MAX_LIFT * (cb.LIFT_TARGET - lb.mean()) + 0.02
    assert la.std() >= lb.std()                           # not flattened
    assert cb.plan(Image.open(src))["gamma"] >= cb.MIN_GAMMA


def test_monochrome_and_sepia_are_never_touched(tmp_path):
    grey = np.repeat(_scene(3)[..., :1], 3, axis=2) * 0.5
    sepia = np.repeat(_scene(4)[..., :1], 3, axis=2) * np.array([1.0, 0.85, 0.6]) * 0.5
    for name, a in (("bw.jpg", grey), ("sepia.jpg", sepia)):
        src = _img(tmp_path, name, a)
        assert cb.balanced(src) == src


def test_a_balanced_photo_our_graphics_and_a_balanced_copy_are_unchanged(tmp_path):
    src = _img(tmp_path, "fine.jpg", _scene(5) * np.array([1.0, 1.0, 1.0]) + 30)
    assert cb.plan(Image.open(src)) is None and cb.balanced(src) == src
    graphic = _img(tmp_path, "graphic_map.jpg", _scene() * np.array([1.3, 1.0, 0.5]))
    assert cb.balanced(graphic) == graphic
    cast = _img(tmp_path, "cast.jpg", _wall(6) * np.array([1.2, 1.15, 0.7]))
    once = cb.balanced(cast)
    assert once != cast and cb.balanced(once) == once                           # idempotent
    mtime = once.stat().st_mtime_ns
    assert cb.balanced(cast) == once and once.stat().st_mtime_ns == mtime       # cached: not made again
    assert cb.CACHE_DIR in once.parents and "repo_writes" in str(cb.CACHE_DIR)  # the sandbox, never the repo cache


def _sat(a):
    mx, mn = a.max(axis=-1), a.min(axis=-1)
    return np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)


def test_a_daylight_blue_sky_over_a_grey_wall_keeps_its_colour(tmp_path):
    """2026-10-29 review, Derinkuyu photo 5: grey-world over the whole frame had read a clear blue sky as a cast and
    turned it grey-teal. The cast is measured on neutral candidates only, and a sky is daylight."""
    rng = np.random.default_rng(7)
    a = _wall(8) + 60                                              # a sunlit grey wall
    a[:48] = np.array([60.0, 120.0, 200.0]) + rng.normal(0, 4, (48, 96, 3))   # a clear blue sky above it
    src = _img(tmp_path, "street.jpg", a)
    out = cb.balanced(src)
    before, after = (np.asarray(Image.open(p).convert("RGB"), dtype=float) for p in (src, out))
    assert _sat(after[:48]).mean() >= _sat(before[:48]).mean() * 0.97   # the sky keeps its saturation
    assert out == src                                                   # nothing to correct at all
    sc = cb.scene(np.asarray(Image.open(src).convert("RGB"), dtype=float))
    assert sc["daylight_sky"] and not sc["lamp_lit"]


def test_a_saturated_natural_scene_and_vegetation_are_never_neutralised(tmp_path):
    rng = np.random.default_rng(9)
    desert = np.array([200.0, 140.0, 80.0]) * rng.uniform(0.85, 1.05, (96, 96, 1)) + rng.normal(0, 3, (96, 96, 3))
    src = _img(tmp_path, "desert.jpg", desert)                         # bright sunlit sand, orange all over
    assert cb.balanced(src) == src
    dim = _wall(10) * np.array([1.15, 1.2, 0.7])
    dim[60:] = np.array([60.0, 120.0, 40.0]) + rng.normal(0, 4, (36, 96, 3))   # grass along the bottom
    src2 = _img(tmp_path, "garden.jpg", dim)
    assert not cb.scene(np.asarray(Image.open(src2).convert("RGB"), dtype=float))["lamp_lit"]


def test_a_lamp_tinted_grey_wall_is_neutralised(tmp_path):
    a = _wall(11) * np.array([1.15, 1.12, 0.7])    # a warm lamp on grey stone (hue ~56: a sepia-hued one is a toned print)
    src = _img(tmp_path, "tunnel.jpg", a)
    out = cb.balanced(src)
    assert out != src
    before, after = (np.asarray(Image.open(p).convert("RGB"), dtype=float) for p in (src, out))
    assert _cast(after) < _cast(before) * 0.6
    assert after[..., 2].mean() > before[..., 2].mean()              # the blue a lamp drowns is given back
    assert cb.plan(Image.open(src))["cast"] >= cb.CAST_MIN


def test_a_warm_cast_is_left_warm(tmp_path):
    """Final rc72 review: the Amber Room's panels were cooled toward grey. Candle, torch and tungsten light share
    their hue with amber, gold and ochre, and nothing in the pixels tells them apart, so a warm cast stays."""
    a = _wall(12) * np.array([1.25, 0.95, 0.55])    # orange: hue about 30
    src = _img(tmp_path, "amber.jpg", a)
    settings = cb.plan(Image.open(src))
    assert settings is None or settings["gains"] == [1.0, 1.0, 1.0]
    assert cb.scene(np.asarray(Image.open(src).convert("RGB"), dtype=float))["lamp_lit"]


def test_a_bright_frame_lit_yellow_green_is_balanced_but_bright_warm_sand_is_not(tmp_path):
    """Production, 29 Oct: Derinkuyu's stone door was bright (95th percentile 0.79) and lit yellow-green, so it
    passed as daylight and reached the video neon lime. Bright warm sand (no sky, tinted highlights) stays as it is."""
    lime = np.clip(_wall(14) * 1.6 * np.array([1.0, 1.02, 0.35]), 0, 255)
    sand = np.clip(_wall(15) * 1.6 * np.array([1.0, 0.78, 0.45]), 0, 255)
    lime_plan = cb.plan(Image.open(_img(tmp_path, "lime.jpg", lime)))
    assert lime_plan and lime_plan["gains"][2] > 1.05
    sand_plan = cb.plan(Image.open(_img(tmp_path, "sand.jpg", sand)))
    assert sand_plan is None or sand_plan["gains"] == [1.0, 1.0, 1.0]


def test_dark_green_noise_is_not_vegetation(tmp_path):
    a = _wall(13) * np.array([1.15, 1.12, 0.7])
    a[:40] = np.random.default_rng(13).normal([10, 22, 8], 3, (40, 96, 3))   # deep shadow, green JPEG noise
    sc = cb.scene(np.asarray(Image.open(_img(tmp_path, "shadow.jpg", a)).convert("RGB"), dtype=float))
    assert float(sc["foliage"].mean()) < cb.FOLIAGE_MIN


def test_a_broken_photo_is_used_as_it_is(tmp_path):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not a jpeg")
    assert cb.balanced(bad) == bad


def test_every_prepared_still_is_balanced():
    import inspect
    import main
    assert "colour_balance.balanced(ensure_resolution(path))" in inspect.getsource(main.prepared_still)
    src = inspect.getsource(main.PipelineCoordinator._prepare_images)
    assert "colour_balance.balance_all(scene_media)" in src


# ---------------------------------------------------------------- 12: the word budget by role

def test_a_draft_that_keeps_the_budget_lands_inside_the_window_with_no_rule_adding_a_sentence(monkeypatch, curated_payload):
    narration = ("In 1963, a man in Cappadocia, Turkey, broke through his basement wall. Behind it lay a city. "
                 "Its shafts carried air down many levels. Stables and chapels were cut from the rock. "
                 "Rolling stone doors sealed each floor. Who were they hiding from?")
    pkg = ScriptPackage.model_validate(dict(curated_payload, narration_script=narration,
                                            hook_sentence=narration.split(". ")[0] + ".",
                                            title="The 1963 wall that hid a city",
                                            caption="A basement wall, 1963, and a city behind it.\n\n#Derinkuyu"))
    monkeypatch.setattr(script_engine.audio_engine, "measure_spoken_seconds", lambda text, speed=1.0: 45.0)
    monkeypatch.setattr(script_engine, "_pace_window", lambda: (1.0, 1.0))
    assert script_engine.spoken_length_problems(pkg) == []                        # inside the measured window
    assert script_engine.story_problems(pkg, TOPIC) == [] and script_engine.hook_problems(narration) == []
    assert script_engine.spoken_narration(narration) == narration
    rules = script_engine.USER_PROMPT_TEMPLATE + script_engine.REVISE_SYSTEM
    assert "never as a sentence added for it" in rules and "replaces measurement sentences" in rules
    assert "Never add a sentence for it" in script_engine.REVISE_SYSTEM and "WORD BUDGET" in script_engine.REVISE_SYSTEM


def test_an_opening_edit_that_adds_a_sentence_is_refused(monkeypatch, curated_payload):
    unplaced = "Derinkuyu hid thousands." + BODY
    engine, written, edits = _engine(monkeypatch, curated_payload, unplaced, lambda prev: ScriptPackage.model_validate(
        dict(prev, narration_script="It lies in Cappadocia, Turkey. " + unplaced)))
    out = engine.generate_script(TOPIC)
    assert len(edits) == 1 and out["narration_script"] == unplaced                 # kept as it was


# ---------------------------------------------------------------- 13: the voice window on the middle half

PROD_TAKES = [0.949, 0.962, 0.975, 0.99, 1.0, 1.004, 1.01, 1.018, 1.022, 1.03, 1.035, 1.04, 1.045, 1.05, 1.055,
              1.06, 1.065, 1.07, 1.075, 1.079]


def test_the_word_window_is_planned_on_p15_p85_of_the_takes(monkeypatch, tmp_path):
    from src import audio_engine, config
    pace = tmp_path / "voice_pace.json"
    monkeypatch.setattr(audio_engine, "PACE_FILE", pace)
    pace.write_text(json.dumps({"elevenlabs:v:m": PROD_TAKES}), encoding="utf-8")
    fast, slow = audio_engine.pace_range("elevenlabs:v:m")
    assert 0.949 < fast < slow < 1.079
    old, new = script_engine.length_window(0.45, 0.949, 1.079), script_engine.length_window(0.45, fast, slow)
    assert new.low <= old.low and new.high >= old.high and (new.high - new.low) > (old.high - old.low)
    # The expected re-read rate (review rc72): a script at the window's low edge reads short on at most ~15% of
    # the takes seen; one at its high edge, on the slowest take, still makes a master inside the release limit.
    low_edge = new.low * 0.45
    short = sum(1 for r in PROD_TAKES if low_edge * r < script_engine.TARGET_DURATION_MIN) / len(PROD_TAKES)
    assert short <= 0.15
    from src import end_card
    assert new.high * 0.45 * max(PROD_TAKES) + end_card.SECONDS <= config.RELEASE_DURATION_MAX
    assert audio_engine.pace_ratio("elevenlabs:v:m") == max(PROD_TAKES[-10:])   # the voice stage's net: unchanged
    pace.write_text(json.dumps({"elevenlabs:v:m": PROD_TAKES[:7]}), encoding="utf-8")
    assert audio_engine.pace_range("elevenlabs:v:m") == (min(PROD_TAKES[:7]), max(PROD_TAKES[:7]))   # few: extremes
    for i in range(25):
        audio_engine.record_pace("elevenlabs:v:m", 10.0, 10.0 + i * 0.01)
    assert len(json.loads(pace.read_text(encoding="utf-8"))["elevenlabs:v:m"]) == audio_engine.PACE_KEPT


def test_a_take_outside_the_band_is_still_read_again_or_kept_and_the_metric_recorded(tmp_path, monkeypatch):
    import main
    from src import config, improve
    found = []
    monkeypatch.setattr(improve, "note", lambda klass, detail, source="pipeline": found.append((klass, detail)))
    assert [script_engine.take_band(s) for s in (40.0, 47.0, 53.0, 56.0)] == ["short", "inside", "long", "over"]
    for seconds, retakes_expected, band in ((41.1, 1, "short"), (47.0, 0, "inside"), (54.0, 0, "long")):
        c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
        c.audio_engine = type("A", (), {"last_provider": "elevenlabs", "get_duration": staticmethod(lambda p: seconds)})()
        retakes = []
        c._fit_take(tmp_path / "vo.mp3", [], 1.0, lambda s: retakes.append(s) or [], {})
        assert len(retakes) == retakes_expected and c.last_take_band == {"seconds": seconds, "band": band}
    assert [k for k, _ in found] == ["voice.take_band", "voice.take_band"]          # short and long are filed

# ---------------------------------------------------------------- review fixes (rc72)

def test_an_opening_edit_that_pushes_a_fitted_draft_out_of_its_window_is_refused(monkeypatch, curated_payload):
    """The review's repro: "Beneath one house in Cappadocia, Turkey," inserted took a fitted 108-word draft to 114,
    past the window, and cost a full rewrite. The fitted draft is kept instead (the story finding is advisory)."""
    unplaced = "Derinkuyu hid thousands." + BODY
    engine, written, edits = _engine(monkeypatch, curated_payload, unplaced, lambda prev: ScriptPackage.model_validate(
        dict(prev, narration_script="Beneath one house in Cappadocia, Turkey, Derinkuyu hid thousands." + BODY)))
    monkeypatch.setattr(script_engine, "spoken_length_problems", lambda pkg: [
        "Narration runs 53.0s when spoken (limit 51.5s). This draft is 114 words; rewrite the narration in 103-108 "
        "words, keeping the closing sentence."] if "Beneath" in pkg.narration_script else [])
    out = engine.generate_script(TOPIC)
    assert written == [1] and len(edits) == 1 and out["narration_script"] == unplaced


def test_spelled_numbers_ordinals_and_conversions_tell_the_same_story(curated_payload):
    cases = [("A 2,000-year-old secret", "It has stood for two thousand years."),
             ("280 feet down", "The city reaches 85 meters below the plain."),
             ("An 8th-century refuge", "Christians expanded it in the eighth century."),
             ("Top 15 levels", "Fifteen levels were cut.")]
    for title, narration in cases:
        pkg = ScriptPackage.model_validate(dict(curated_payload, title=title, caption="A city.\n\n#X",
                                                narration_script=narration))
        assert script_engine.told_story_problems(pkg) == [], title
    assert script_engine.spelled_numbers("two thousand and twenty-five, the eighth") == [2025.0, 8.0]


def test_nested_graphic_paths_are_listed_and_applied():
    draft = {"cross_section": {"subject": "X", "total_depth_ft": 280, "approximate": True,
                               "levels": [{"depth_ft": 20, "label": "Stables"}, {"depth_ft": 200, "label": "Chapel"},
                                          {"depth_ft": 260, "label": "Well"}]}}
    paths = script_engine.graphic_paths(draft)
    assert "cross_section.levels[2].label: Chapel" in paths
    assert script_engine.graphic_edit(draft, "cross_section.levels[2].label", "Chapel", "Church")
    assert draft["cross_section"]["levels"][1]["label"] == "Church"
    assert script_engine.graphic_edit(draft, "cross_section.levels[1]", "", "")
    assert script_engine.graphic_edit(draft, "cross_section.levels[1]", "", "") and draft["cross_section"] is None


def test_bracket_stripping_matches_the_voice_for_nested_asides():
    from src import audio_engine
    text = "A city (one of many (at least 36) here) lay 280 feet (85 m) down."
    assert script_engine.spoken_narration(text) == "A city lay 280 feet down."
    assert audio_engine.strip_asides(text) == "A city lay 280 feet down."
    spoken = audio_engine.normalize_speech_text(text)
    assert "(" not in spoken and ")" not in spoken


def test_the_filler_graphic_is_not_placed_once_the_checks_stopped(tmp_path, judged):
    hook, shaft, stable = (_photo(tmp_path, n, s) for n, s in (("hook.jpg", 10), ("shaft.jpg", 60), ("stable.jpg", 90)))
    code = tmp_path / "draw.py"
    code.write_bytes(b"code")
    numbers = tmp_path / "graphic_numbers.mp4"
    numbers.write_bytes(b"n")
    numbers.with_suffix(".jpg").write_bytes(b"poster")
    record_graphic(numbers, code, {"builder": "numbers", "payload": {"stat_cards": [{"value": "18", "label": "levels"}]}})
    spare = _photo(tmp_path, "spare.jpg", 150)
    beats = _beats("A city underground.", "Air came down the shaft.", "A chapel was carved below.")
    # Three new looks: beats 2 and 3, then the spare photo; the next look hits the budget and the checks stop.
    out, report = scene_planner.fit_to_lines(beats, [hook, shaft, stable], [hook, shaft, stable, spare, spare, numbers],
                                             "Derinkuyu", durations=[3.0, 3.0, 9.0], max_calls=3)
    assert report["stopped"] and numbers not in out


def test_the_picture_side_search_has_a_time_budget(monkeypatch, tmp_path):
    import inspect
    import time
    import main
    from src import beat_images, prevoice_lines
    assert prevoice_lines.SEARCH_SECONDS <= 180
    assert "deadline" in inspect.signature(beat_images.source_gaps).parameters
    assert "deadline=started + pl.SEARCH_SECONDS" in inspect.getsource(main.PipelineCoordinator._picture_side)
    monkeypatch.setattr(beat_images.commons, "search_widened", lambda *a, **k: pytest.fail("searched after the budget"))
    item = type("I", (), {"beat": 1, "query": "q", "keywords": ["q"], "place": None})()
    assert beat_images._download([item], {"id": "t"}, tmp_path, set(), "line", deadline=time.monotonic() - 1) == ([], [])


# ---------------------------------------------------------------- 15: headline figures

OBELISK = {"facts": [
    {"id": "f1", "statement": "The obelisk weighs between 1,090 and 1,168 tonnes.", "disputed": True,
     "hedge": "estimated at 1,090 to 1,168 tonnes", "evidence": []},
    {"id": "f2", "statement": "It was carved in 1479 BC.", "disputed": False, "hedge": "", "evidence": []},
    {"id": "f3", "statement": "It is 42 m long.", "disputed": True, "hedge": "by one estimate, it is 42 m long",
     "evidence": []}]}


def _headline(curated_payload, title, line1="UNFINISHED", caption="A giant left in the rock."):
    thumb = {"eyebrow": "LOST ENGINEERING", "line1": line1, "line2": "IN THE ROCK", "tagline": "WHY STOP?"}
    return ScriptPackage.model_validate(dict(curated_payload, title=title, thumbnail=thumb,
                                             caption=caption + "\n\nMore.\n\n#X"))


def test_the_obelisk_headline_figure_is_refused_and_a_floor_accepted(curated_payload):
    bad = script_engine.headline_problems(_headline(curated_payload, "The 1,200-ton obelisk", line1="1,200-TON"), OBELISK)
    assert {p.split(":")[0] for p in bad} == {"Headline figure in the title", "Headline figure in the thumbnail"}
    assert all(script_engine.copy_problem_fields(p, _headline(curated_payload, "x")) for p in bad)   # the copy edit's
    assert script_engine.headline_problems(_headline(curated_payload, "The 1,000+ tonne obelisk",
                                                     line1="OVER 1,000 TONNES"), OBELISK) == []
    assert script_engine.headline_problems(_headline(curated_payload, "The 1,100+ tonne obelisk"), OBELISK)  # above min
    assert script_engine.headline_problems(_headline(curated_payload, "Carved in 1479 BC"), OBELISK) == []    # undisputed
    single = script_engine.headline_problems(_headline(curated_payload, "A 42 m giant"), OBELISK)
    assert single and "only one estimate" in single[0]
    caption = script_engine.headline_problems(_headline(curated_payload, "A giant", caption="All 1,168 tonnes of it."),
                                              OBELISK)
    assert caption and caption[0].startswith("Headline figure in the caption")
    assert script_engine.headline_problems(_headline(curated_payload, "The 1,200-ton obelisk"), {"facts": []}) == []


def test_a_headline_figure_miss_goes_to_the_copy_edit_not_a_rewrite(monkeypatch, curated_payload):
    engine = script_engine.ScriptEngine()
    monkeypatch.setattr(script_engine, "validate_structure", lambda pkg: [])
    monkeypatch.setattr(script_engine, "spoken_length_problems", lambda pkg: [])
    monkeypatch.setattr(script_engine, "hook_problems", lambda n: [])
    noted = []
    monkeypatch.setattr(script_engine.improve, "note", lambda klass, detail, source="": noted.append(klass))
    pkg = _headline(curated_payload, "The 1,200-ton obelisk", line1="1,200-TON")
    monkeypatch.setattr(script_engine, "structured_call", lambda system, prompt, schema, **kw: schema(
        title="The 1,000+ tonne obelisk", thumbnail={"eyebrow": "LOST ENGINEERING", "line1": "1,000+ TONNES",
                                                    "line2": "IN THE ROCK", "tagline": "WHY STOP?"}))
    monkeypatch.setattr(script_engine.free_llm, "enabled", lambda: False)
    token = script_engine._draft_sheet.set(OBELISK)
    try:
        fixed, problems = engine._narrow_edits(pkg, script_engine.draft_problems(pkg), OBELISK)
    finally:
        script_engine._draft_sheet.reset(token)
    assert fixed.title == "The 1,000+ tonne obelisk" and fixed.thumbnail.line1 == "1,000+ TONNES"
    assert not [p for p in problems if p.startswith(script_engine.HEADLINE_PROBLEM)]
    assert "script.headline_figure" in noted and "floor at or below" in script_engine.COPY_EDIT_SYSTEM


ANTIKYTHERA = {"facts": [
    {"statement": "Divers found the mechanism in 1901 off Antikythera.", "hedge": "", "disputed": False},
    {"statement": "It was built around 100 BC.", "hedge": "", "disputed": False},
    {"statement": "The wreck lay 45 metres down.", "hedge": "", "disputed": False},
    {"statement": "The blast flattened 2,150 square kilometres of forest.", "hedge": "", "disputed": False},
    {"statement": "The obelisk would have weighed 1,090 to 1,168 tonnes.",
     "hedge": "Estimates range from 1,090 to 1,168 tonnes.", "disputed": True},
    {"statement": "Over 1.2 million visitors come each year.", "hedge": "by one estimate 1.2 million visitors",
     "disputed": True},
    {"statement": "The 13th-century chronicle mentions it in 1922.", "hedge": "", "disputed": False}]}


@pytest.mark.parametrize("title", ["Found in 1901", "Top 10 Ancient Machines", "The Apollo 11 of Antiquity",
                                   "Route 66 Relic", "A 13th-Century Clue", "The 2,000-Year-Old Computer",
                                   "Built 2,100 Years Ago", "148 Feet Down", "830 Square Miles Flattened",
                                   "1,000+ Tonnes"])
def test_a_supported_headline_figure_passes(curated_payload, title):
    assert script_engine.headline_problems(_headline(curated_payload, title), ANTIKYTHERA) == []


@pytest.mark.parametrize("title", ["The 1,200-Ton Obelisk", "1.2K Tons", "1.2 Million Visitors",
                                   "Built 3,000 Years Ago", "Over 1,100 Tonnes", "500 Feet Down"])
def test_an_unsupported_headline_figure_is_refused(curated_payload, title):
    # 1,200 tons once passed as the year 1922 in miles; a unit now converts only within its kind, and K/million count
    assert script_engine.headline_problems(_headline(curated_payload, title), ANTIKYTHERA)
