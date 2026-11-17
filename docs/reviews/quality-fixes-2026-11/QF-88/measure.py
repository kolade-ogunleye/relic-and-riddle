"""QF-88 acceptance: a 10-Hz strobe is blocked with a named reason; a map passes the CVD gate; a 20-cps burst fails
the build. Plus the row's other promises, each through the real code path.

    python measure.py <repo root>

Runs on the base too (each part reports false where the code is missing). Generated fixtures go to a temporary
folder; the real map renders are committed evidence images (docs/reviews/brand-overhaul-2026-10-29/map_*.jpg); the
real-data block reads this folder's committed real_data*.json (counts only for the production sample). Prints JSON
(LF); writes nothing outside the temporary folder.
"""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
from src import picture_qa, release

here = Path(__file__).resolve().parent
# the fixtures are this commit's regression test's own (on the base its imports fail: reported as errors)
spec = importlib.util.spec_from_file_location("qf88_tests", here.parents[3] / "tests/test_qf88_accessibility_gates.py")
result = {}


def attempt(name, job):
    try:
        result[name] = job()
    except Exception as exc:  # noqa: BLE001 - the base lacks the code: reported, not raised
        result[name] = {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}


def fixtures():
    fx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fx)
    return fx


with tempfile.TemporaryDirectory(prefix="qf88-") as temp:
    temp = Path(temp)

    def strobe_cases():
        fx = fixtures()
        out = {}
        report, checks = fx.gate(fx.clip(temp / "strobe_10hz.mp4", fx.strobe(10)))
        flash = checks.get("picture_flash") or {}
        problems = [p for p in release.release_problems(report, dry_run=True) if p.startswith("picture_flash:")]
        out["strobe_10hz_blocked_with_named_reason"] = bool(problems) and "10 times in a second" in problems[0]
        cases = {"red_on_black_10hz": (fx.strobe(10, (255, 0, 0)), "fail"), "white_4hz": (fx.strobe(4), "fail"),
                 "patch_6pct_10hz": (fx.strobe(10, area=0.06), "fail"), "white_3hz": (fx.strobe(3), "pass"),
                 "dim_10hz": (fx.strobe(10, (40, 40, 40), (25, 25, 25)), "pass"),
                 "patch_2pct_10hz": (fx.strobe(10, area=0.02), "pass")}
        for name, (frame, want) in cases.items():
            got = fx.flash_check(fx.clip(temp / f"{name}.mp4", frame))["status"]
            out[name] = {"status": got, "as_wcag": got == want}
        # review 1: counter-phase patterns, fields at any position, the 0.1-degree pattern limit
        round2 = {"checker_12_across": (fx.checker(12), "fail"), "checker_4_across": (fx.checker(4), "fail"),
                  "halves": (fx.halves, "fail"),
                  "strip32_off_grid": (fx.rect(fx.FW * 0.25, fx.FH * 0.1, fx.FW, fx.FH * 0.32), "fail"),
                  "strip22_on_grid": (fx.rect(0, fx.FH * 0.1, fx.FW, fx.FH * 0.22), "pass")}
        for name, (frame, want) in round2.items():
            got = fx.flash_check(fx.clip(temp / f"{name}.mp4", frame))["status"]
            out[name] = {"status": got, "as_wcag": got == want}
        for deg, phase, want in ((0.1, 0.5, "fail"), (0.04, 0.0, "pass")):
            path = fx.clip(temp / f"deg{deg}.mp4", fx.degree_checker(deg, phase), seconds=1.2, w=1080, h=1920)
            got = fx.flash_check(path)["status"]
            out[f"checker_{deg}_degree_phase_{phase}"] = {"status": got, "as_wcag": got == want}
        # review 1, round 2: counter-phase and direction counting, parts of a field changing in turn
        round3 = {"checker_12_across_3hz": (fx.checker(12, 3), "pass"), "halves_2hz": (fx.halves_at(2), "pass"),
                  "late_fall_after_3_flashes": (fx.late_fall, "pass", 1.4),
                  "in_turn_9_frames": (fx.in_turn(9), "pass", 1.5), "in_turn_1_s": (fx.in_turn(30), "fail", 1.5),
                  "drifting_band_10hz": (fx.drifting_band, "fail")}
        for name, (frame, want, *seconds) in round3.items():
            got = fx.flash_check(fx.clip(temp / f"{name}.mp4", frame, *seconds))["status"]
            out[name] = {"status": got, "as_wcag": got == want}
        # review 1, round 3: parts of a field flashing in turn, and a strobe moving across fields
        round4 = {"rolling_bands_white": fx.rolling(fx.bands), "rolling_bands_red": fx.rolling(fx.bands, on=(255, 0, 0)),
                  "rolling_blocks": fx.rolling(fx.blocks), "rolling_columns4": fx.rolling(fx.columns4),
                  "rolling_bands_2_each": fx.rolling(fx.bands, per=2), "moving_band_10px": fx.moving_band(10)}
        for name, frame in round4.items():
            got = fx.flash_check(fx.clip(temp / f"{name}.mp4", frame))["status"]
            out[name] = {"status": got, "as_wcag": got == "fail"}
        # review 1, round 4: a counter-phase frame is excused (the whip) only when neither its rise nor its fall is
        # coarse; fine counter-phase flashing in place beside flashes that always count is counted
        round5 = {"masked_rolling": (fx.masked_rolling(), "fail"),
                  "masked_rolling_red": (fx.masked_rolling(on=(255, 0, 0)), "fail"),
                  "masked_rolling_fine": (fx.masked_rolling_fine, "fail"),
                  "cuts_8_per_s": (fx.cuts(8), "fail"), "cuts_9_per_s": (fx.cuts(9), "fail"),
                  "cuts_8_band40": (fx.cuts(8, band=40), "fail"), "cuts_8_red": (fx.cuts(8, on=(255, 0, 0)), "fail"),
                  "cuts_8_masked": (fx.masked_cuts, "fail"),
                  "cuts_12_four_bands_of_26pct": (fx.cuts(12, band=42, classes=4), "fail"),
                  "in_turn_then_a_flash": (fx.in_turn_then_flash, "fail"), "cuts_6_per_s": (fx.cuts(6), "pass")}
        for name, (frame, want) in round5.items():
            got = fx.flash_check(fx.clip(temp / f"{name}.mp4", frame))["status"]
            out[name] = {"status": got, "as_wcag": got == want}
        # review 1, round 5: a fine pattern flashing in place on the same frames as a coarse change, each under 25%
        # of the field (fail); a count only a motion test could settle (held: never a pass, never auto-rejected)
        round6 = {"sub_band_checker_20": (fx.sub_band_checker, "fail"), "sub_band_vstrip": (fx.sub_band_vstrip, "fail"),
                  "sub_band_burst": (fx.sub_band_burst, "fail"),
                  "coarse3_plus_fine_reversal": (fx.coarse_then_fine_reversal, "needs_review")}
        for name, (frame, want) in round6.items():
            got = fx.flash_check(fx.clip(temp / f"{name}.mp4", frame))["status"]
            out[name] = {"status": got, "as_wcag": got == want}
        # review 1, round 6: 0.44-degree checkers over 52-60% of a field reversing 8 a second (the boxes at the
        # squares' edges read as unchanged), a band beside such a checker, and fails with a held stretch beside them
        round7 = {"chk52_8": (fx.checker_over(0.52), "fail"), "chk56_8": (fx.checker_over(0.56), "fail"),
                  "chk60_8": (fx.checker_over(0.60), "fail"), "chk52_red": (fx.checker_over(0.52, red=True), "fail"),
                  "sb_b18_c16": (fx._band_and_checker(0.18, 0.16), "fail"),
                  "sb_b22_c08": (fx._band_and_checker(0.22, 0.08), "fail"),
                  "ip_b3_k6": (fx._reversing_until(6), "fail"),
                  "strobe_then_held": (fx.full_4hz_then_held, "fail", 5.0)}
        for name, (frame, want, *seconds) in round7.items():
            got = fx.flash_check(fx.clip(temp / f"{name}.mp4", frame, *seconds))["status"]
            out[name] = {"status": got, "as_wcag": got == want}
        out["detail"] = flash.get("detail")
        return out

    def hold_case():
        fx = fixtures()
        (temp / "held").mkdir()
        report, checks = fx.gate(fx.clip(temp / "held" / "m.mp4", fx.coarse_then_fine_reversal))
        flash = checks["picture_flash"]
        sheet = temp / "held" / flash["contact_sheets"][0]["path"]
        return {"status": flash["status"], "says_why": "Possible flash; needs your look" in flash["detail"],
                "blocks_a_live_post": "picture_flash" in {c["name"] for c in release.blocking_checks(report)},
                "contact_sheet_jpeg_within_300_kb": sheet.suffix == ".jpg" and sheet.stat().st_size <= 300 * 1024}

    def cps_case():
        fx = fixtures()
        from src import video_composer as vc
        phrase = fx._words([("ARCHAEOLOGISTS", 0.0, 0.5), ("FOUND", 0.55, 0.45)])
        narration = vc.reading_rates([phrase], [(0.0, 1.0, [0.0, 0.55])])
        defect = vc.reading_rates([phrase], [(0.0, 0.8, [0.0, 0.55])])
        words = fx._words(fx.FAST)
        real, phrases = vc.burst_timings, vc.caption_phrases
        vc.caption_phrases = lambda w, *a, **k: [w[:2], w[2:]]
        vc.burst_timings = lambda p, **kw: [(s, (h - 0.25 if i == 0 else h), r) for i, (s, h, r) in enumerate(real(p, **kw))]
        try:
            vc.VideoComposer().build_subtitles(words, temp / "fast.ass")
            fails = False
        except vc.CaptionReadingRateError:
            fails = True
        finally:
            vc.burst_timings = real
        try:
            vc.VideoComposer().build_subtitles(words, temp / "right.ass")
            right_passes = True
        except vc.CaptionReadingRateError:
            right_passes = False
        finally:
            vc.caption_phrases = phrases
        note = release._reading_rate_checks({"reading_rate": narration})
        return {"burst_20cps_cut_short_fails_the_build": fails, "same_words_timed_right_build": right_passes,
                "narration_20cps_is_a_defect": bool(narration["timing_defects"]),
                "shown_25_spoken_20_is_a_defect": bool(defect["timing_defects"]),
                "narration_pace_noted_not_blocking": bool(note) and note[0]["status"] == "pass" and note[0].get("note"),
                "cut_before_a_fast_boundary_makes_no_defect": snap_ok()}

    def snap_ok():
        from src import video_composer as vc
        b0 = fixtures()._words([("ROMAN", 0.50, 0.3), ("CONCRETE", 0.85, 0.45), ("HEALS", 1.35, 0.3), ("ITSELF", 1.70, 0.35)])
        b1 = fixtures()._words([("WITH", 2.10, 0.2), ("LIME", 2.35, 0.3), ("CLASTS.", 2.7, 0.5)])
        timings = vc.burst_timings([b0, b1], dwell=True)
        return all(not vc.reading_rates([b0, b1], vc.snap_to_cuts([b0, b1], timings, [timings[0][1] - k / 30]))[
            "timing_defects"] for k in range(1, 7))

    def cvd_case():
        fx = fixtures()
        from src import art_direction, colour_vision
        maps = {}
        for p in sorted((root / "docs/reviews/brand-overhaul-2026-10-29").glob("map_*.jpg")):
            r = colour_vision.check(p, (0x78, 0xBE, 0xFF), kind="map")
            maps[p.stem] = {"pass": r["pass"], "roles": r["roles"],
                            "worst_pair_de00": min((q["de00"][q["worst_vision"]] for q in r["pairs"]), default=None)}
        run = temp / "green_run"
        fx._gold_and_accent(run / "graphics" / "graphic_route.jpg", fx.GREEN)
        (run / "palette.json").write_text(json.dumps({"accent": list(fx.GREEN)}), encoding="utf-8")
        green = release._colour_vision_checks(run)
        from PIL import Image
        hero = temp / "green_hero.jpg"
        Image.new("RGB", (96, 96), fx.GREEN).save(hero)
        picked = art_direction.palette_from(hero)["accent"]
        return {"real_maps_pass": bool(maps) and all(m["pass"] and m["worst_pair_de00"] for m in maps.values()),
                "maps": maps,
                "green_accent_fails_the_gate": bool(green) and green[0]["status"] == "fail",
                "art_direction_rejects_the_green_accent": colour_vision.accent_problem(tuple(picked)) is None}

    def grandfather_case():
        fx = fixtures()
        (temp / "old").mkdir()
        master = fx.pre_qf01(temp / "old" / "m.mp4")
        fx.compose_record(master)
        fx.old_report(master)
        keep = picture_qa.METHOD_VERSION
        picture_qa.METHOD_VERSION = 1
        try:
            _, first = fx.gate(master)
        finally:
            picture_qa.METHOD_VERSION = keep
        _, second = fx.gate(master)
        return {"method_version": keep, "noted_before": bool(first["picture_colour"].get("grandfathered")),
                "noted_after_re_measure": bool(second["picture_colour"].get("grandfathered")),
                "flash_measured": "picture_flash" in second}

    attempt("photosensitivity", strobe_cases)
    def fail_precedence_case():
        # a 4 Hz full-frame strobe, then a held second: blocked as a fail, never held, never clearable
        fx = fixtures()
        (temp / "failheld").mkdir()
        report, checks = fx.gate(fx.clip(temp / "failheld" / "m.mp4", fx.full_4hz_then_held, seconds=5.0))
        flash = checks["picture_flash"]
        try:
            release.clear_flash_hold(temp / "failheld", report, by="measure")
            refused = False
        except ValueError:
            refused = True
        return {"status": flash["status"], "held": bool(release.held_flash(report)),
                "blocks": "picture_flash" in {c["name"] for c in release.blocking_checks(report, dry_run=True)},
                "clear_flash_refused": refused}

    attempt("hold", hold_case)
    attempt("fail_precedence", fail_precedence_case)
    attempt("cps", cps_case)
    attempt("cvd", cvd_case)
    attempt("grandfather", grandfather_case)

real = {}
if (here / "cps_cuts.json").is_file():   # real narration, real build_subtitles, 20 random cut sets a run
    real["cps_cuts.json"] = json.loads((here / "cps_cuts.json").read_text(encoding="utf-8"))
for name in ("real_data_dev.json", "real_data_prod.json"):
    path = here / name
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        real[name] = {label: s["summary"] for label, s in data["sets"].items()}
        if "operator_footage" in data:
            real[name]["operator_footage"] = {k: v for k, v in data["operator_footage"].items() if k != "rows"}
result["real_data"] = real


def ok(path, value=True):
    node = result
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return False
        node = node[key]
    return node == value


flash = result.get("photosensitivity", {})
result["acceptance_met"] = bool(
    ok(("photosensitivity", "strobe_10hz_blocked_with_named_reason"))
    and all(v.get("as_wcag") for k, v in flash.items() if isinstance(v, dict) and "as_wcag" in v)
    and ok(("cps", "burst_20cps_cut_short_fails_the_build")) and ok(("cps", "same_words_timed_right_build"))
    and ok(("cps", "narration_pace_noted_not_blocking")) and ok(("cps", "narration_20cps_is_a_defect"), False)
    and ok(("cps", "cut_before_a_fast_boundary_makes_no_defect"))
    and ok(("cvd", "real_maps_pass")) and ok(("cvd", "green_accent_fails_the_gate"))
    and ok(("cvd", "art_direction_rejects_the_green_accent"))
    and ok(("grandfather", "noted_before")) and ok(("grandfather", "noted_after_re_measure"))
    and ok(("grandfather", "flash_measured"))
    and ok(("hold", "status"), "needs_review") and ok(("hold", "says_why")) and ok(("hold", "blocks_a_live_post"))
    and ok(("hold", "contact_sheet_jpeg_within_300_kb"))
    and ok(("fail_precedence", "status"), "fail") and ok(("fail_precedence", "held"), False)
    and ok(("fail_precedence", "blocks")) and ok(("fail_precedence", "clear_flash_refused")))
sys.stdout.reconfigure(newline="\n")
print(json.dumps(result, indent=1, default=str))
