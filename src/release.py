"""Measured, evidence-backed release checks shared by production and publishing."""

import json
import math
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .credits import (SHARE_ALIKE_NOTICE, asset_attribution, credit_line_problems, credit_present, is_share_alike,
                      sha256_file)
from .fact_evidence import fact_review_problem, fact_review_shown, human_review_shown, human_review_text
from .delivery_profiles import get_delivery_profile
from .video_qa import is_intermediate, measure_program_loudness, provenance_problems, verify_provenance_integrity


def write_release_report(run, dry_run=False):
    payload = run.get("script_payload") or {}
    master = Path(run["master_video_path"])
    profile = get_delivery_profile(payload.get("delivery_profile") or
                                   ("deep_dive" if run.get("content_format") == "deep_dive" else "reel"))
    checks = []

    def add(name, passed, detail, review=False):
        checks.append({"name": name, "status": "pass" if passed else "needs_review" if review else "fail",
                       "detail": detail})

    add("caption_length", len(payload.get("caption", "")) <= 2200,
        "Caption and attribution must fit the supported Instagram publication limit.")
    # the review stamp must also carry a valid FACT_RECORD_KEY signature (operator decision A, 2026-11-03): an
    # unsigned, edited or other-key record fails here, and so does every record when the key is unset
    # ... and carry the automated checkers' own signed record of their passing runs on this copy (operator decision
    # 2026-11-04). A person's review of a curated script is shown beside it and never decides it.
    facts_problem = fact_review_problem(payload)
    shown = facts_problem or fact_review_shown(payload)
    if facts_problem:
        facts_problem, shown = _rechecked_facts(run, payload, facts_problem)
    human = human_review_shown(payload)
    add("facts", not facts_problem, " ".join(t for t in (shown, human_review_text(human)) if t))
    if human:
        checks[-1]["human_review"] = human
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                                 "-of", "json", str(master)], capture_output=True, text=True,
                                check=True, timeout=30)
        media = json.loads(result.stdout)
        video = next(s for s in media["streams"] if s["codec_type"] == "video")
        audio = next(s for s in media["streams"] if s["codec_type"] == "audio")
        duration = float(media["format"]["duration"])
        add("dimensions", (video["width"], video["height"]) == (profile.width, profile.height),
            f"Measured {video['width']}x{video['height']}; expected {profile.width}x{profile.height}.")
        add("duration", math.isfinite(duration) and profile.duration_min <= duration <= profile.duration_max,
            f"Measured {duration:.3f}s; expected {profile.duration_min}-{profile.duration_max}s.")
        add("channels", audio.get("channels") == profile.audio_channels, f"Measured {audio.get('channels')} channels.")
        add("sample_rate", int(audio["sample_rate"]) == profile.audio_sample_rate, f"Measured {audio['sample_rate']} Hz.")
        measured = measure_program_loudness(master)
        add("loudness", abs(measured["integrated_lufs"] - profile.target_lufs) <= 1.0, measured)
        add("true_peak", measured["true_peak_dbtp"] <= profile.target_true_peak, measured)
        v_dur = float(video.get("duration", duration))
        a_dur = float(audio.get("duration", duration))
        sync_drift_ms = round(abs(v_dur - a_dur) * 1000, 1)
        add("sync_drift", sync_drift_ms <= 80.0,
            f"Measured {sync_drift_ms:.1f}ms audio/video duration drift (must be <= 80ms for sync).")
    except (OSError, ValueError, KeyError, StopIteration, subprocess.SubprocessError) as exc:
        add("media_measurement", False, str(exc))

    # The picture itself, every frame decoded (QF-10): black, frozen, barred or out-of-range pictures and wrong colour
    # tags fail. A master measured before keeps its result (the review card re-runs this gate each time it is shown).
    from . import picture_qa
    deep_dive = profile.name == "deep_dive_16x9"
    report_path = master.parent / "release_report.json"
    eligible = None   # the sha256 this master is "gated before QF-10" for, decided before any measurement (QF-10b)
    try:
        composition_record = _bound_composition(master)
        eligible = _gated_before_qf10(composition_record, report_path)
        exempt = (composition_record or {}).get("full_image_spans") or []
        picture = (picture_qa.recorded(report_path, master, deep_dive=deep_dive, exempt_bars=exempt)
                   or picture_qa.measure(master, deep_dive=deep_dive, exempt_bars=exempt))
        _grandfather_colour(picture, eligible)
        _flash_hold(picture, master)
        checks.extend(picture["checks"])
        checks.append({"name": "picture_measurement", "status": "pass",
                       "detail": f"Decoded all {picture['decoded_frames']} frames ({picture['duration']:.1f} s).",
                       "measurement": {k: v for k, v in picture.items() if k != "checks"}})
    except Exception as exc:  # noqa: BLE001 - any failure to measure is recorded as a failed check, never a pass
        add("picture_measurement", False, f"The picture could not be checked: {exc}")
    if eligible:
        # Kept on every outcome, a failed measurement included: the next check that does measure still finds the
        # master gated before QF-10 (a timeout used to remove the operator's note for good).
        next(c for c in checks if c["name"] == "picture_measurement")["gated_before_qf10"] = eligible
    checks.extend(_reading_rate_checks(_bound_composition(master)))
    checks.extend(_colour_vision_checks(master.parent))
    checks.extend(_ingest_notes(master.parent))

    # The sound, every sample decoded once (QF-11): a dropout to silence blocks; a phone-speaker loss is a note. A
    # master measured before keeps its result. A master gated before QF-11 has no exemption: no real master fails
    # these checks (dev 0 of 30, production sample 0 of 33), so nothing grandfathered under QF-10 changes status.
    from . import audio_qa
    try:
        sound = (audio_qa.recorded(report_path, master, deep_dive=deep_dive)
                 or audio_qa.measure(master, deep_dive=deep_dive))
        checks.extend(sound["checks"])
        checks.append({"name": "audio_measurement", "status": "pass",
                       "detail": (f"Decoded all {sound['decoded_samples_per_channel']:,} samples per channel "
                                  f"({sound['duration']:.1f} s): loudness range {sound['loudness_range_lu']} LU, "
                                  f"loudest 3 s {sound['max_short_term_lufs']} LUFS."),
                       "measurement": {k: v for k, v in sound.items() if k != "checks"}})
    except Exception as exc:  # noqa: BLE001 - any failure to measure is recorded as a failed check, never a pass
        add("audio_measurement", False, f"The sound could not be checked: {exc}")

    ledger = master.parent / "provenance.json"
    # QF-110: the approved master is the unit of trust; a scene clip freed after it was made is counted, not refused
    # (the strict check passes only where this one does; it is asked second so a measure that stubs it still can)
    provenance = provenance_problems(ledger)
    integrity = not provenance["problems"] or verify_provenance_integrity(ledger)
    kept_out = provenance["missing_intermediates"]
    add("provenance", integrity, (None if integrity else provenance["problems"]) or (
        "All recorded files must exist and match their checksums." + (
            f" {kept_out} intermediate scene clip file(s) are no longer kept; the master and every source and "
            "rights record verify." if kept_out else "")))
    if integrity:
        data = json.loads(ledger.read_text(encoding="utf-8"))
        unresolved = [a["path"] for a in data["assets"] if a.get("rights_status") != "verified"]
        add("asset_rights", not unresolved, unresolved, review=True)
        # A line written before QF-42 that rewrites to the asset's line counts (credit_present): a run waiting for
        # approval at deploy keeps Approve; credit_lines below still fails one with a link, address or handle.
        missing_credits = [asset_attribution(a) for a in data["assets"]
                           if not credit_present(a, payload.get("caption", ""))
                           and not credit_present(a, payload.get("description", ""))]
        add("image_attribution", not missing_credits, missing_credits)
        # The credit lines are checked as public text (QF-42): the fact and phrase checks skip them as the pipeline's
        # own, so a link, address, handle or hashtag in one (an edit, a line written before the sanitising) fails
        # here, and a banned phrase in a quoted file title is the operator's call.
        problems = [p for line in dict.fromkeys(ln.strip() for f in ("caption", "description", "carousel_caption",
                                                                    "first_comment")
                                                for ln in str(payload.get(f) or "").splitlines())
                    for p in credit_line_problems(line)]
        unsafe = [p for p in problems if not p.startswith("banned phrase")]
        add("credit_lines", not unsafe, (unsafe + ["Edit the text or Regenerate: either rewrites the credit lines."]
                                         if unsafe else "No link, address, handle or hashtag in the image credits."))
        if len(unsafe) < len(problems):
            add("credit_phrases", False, [p for p in problems if p.startswith("banned phrase")], review=True)
        if any(is_share_alike(a.get("license", "")) for a in data["assets"]):
            # ShareAlike: the adaptation (this video) must be offered under CC BY-SA 4.0, in the post itself.
            add("share_alike_licence", SHARE_ALIKE_NOTICE in payload.get("caption", "") and
                (not profile.requires_youtube_thumbnail or SHARE_ALIKE_NOTICE in payload.get("description", "")),
                "A CC BY-SA image is used, so the post must state: " + SHARE_ALIKE_NOTICE)
        if profile.requires_youtube_thumbnail:
            missing_description = [asset_attribution(a) for a in data["assets"]
                if not credit_present(a, payload.get("description", ""))]
            add("description_attribution", not missing_description, missing_description)
        from .audio_provenance import inspect_audio_rights, rebind_rights
        for key, kind in (("voiceover", "voice"), ("ambient_bed", "music")):
            audio = data.get(key)
            if audio:  # a copy carried before rc66 still names its first run's folder: rebound when the bytes
                rebind_rights(Path(audio["path"]))  # match (production Amber Room, reopened 2026-10-28)
            ok, evidence = inspect_audio_rights(Path(audio["path"]), kind, payload.get("caption", ""),
                                                      script=payload.get("narration_script") if kind == "voice" else None) if audio else (
                False, "Required audio is absent from provenance")
            add(key + "_rights", ok, evidence, review=True)
    try:
        composition = json.loads(master.with_suffix(".composition.json").read_text(encoding="utf-8"))
        valid_composition = composition["master_sha256"] == sha256_file(master)
        for category in ("scenes", "voiceover", "ambient_bed", "fonts", "captions"):
            files = composition[category]
            valid_composition = valid_composition and bool(files) and all(
                digest and (sha256_file(Path(path)) == digest or _freed_clip(category, Path(path), master.parent))
                for path, digest in files.items())
        from .checkpoints import fingerprint
        words = json.loads((master.parent / "words.json").read_text(encoding="utf-8"))
        valid_composition = valid_composition and composition["words_fingerprint"] == fingerprint(words)
        from .font_evidence import font_rights
        font_results = [font_rights(Path(path)) for path in composition["fonts"]]
        add("font_rights", bool(font_results) and all(result[0] for result in font_results),
            [result[1] for result in font_results])
        if integrity:
            recorded_manifest = data.get("composition_manifest") or {}
            valid_composition = valid_composition and recorded_manifest.get("sha256") == sha256_file(master.with_suffix(".composition.json"))
            expected_scenes = {a["path"]: a["sha256"] for a in data["assets"]}
            valid_composition = valid_composition and composition["scenes"] == expected_scenes
            for category in ("voiceover", "ambient_bed"):
                expected = data.get(category) or {}
                valid_composition = valid_composition and composition[category] == {expected.get("path"): expected.get("sha256")}
        add("composition_inputs", valid_composition, "The checked master must use these exact recorded sources.")
    except (OSError, ValueError, KeyError, TypeError):
        add("composition_inputs", False, "Complete composition evidence is missing or malformed.")
    quality = payload.get("self_healing") or {}
    audited_master = bool(quality.get("master_sha256")) and quality["master_sha256"] == sha256_file(master)
    issues = [i for i in (quality.get("issues") or []) if isinstance(i, dict)]
    # The automated look at the frames (the card's "Automated video check") gates with the audit: a major
    # picture finding (a modern car under "ancient surveyors") sat beside "all checks pass" (Nazca, 2026-10-25).
    look = [{"type": "video_check", **i} for i in ((payload.get("video_check") or {}).get("issues") or []) if isinstance(i, dict)]
    found = [i for i in issues + look if i.get("severity") != "info"]  # info: shown on the card, never a block
    from . import config as _config
    # The verdict is the findings' severities, not the audit's "certified" flag: certified needs a score of
    # exactly 100, and every minor finding costs 5 points, so one minor timing finding (score 95) blocked the
    # release with MINOR_FINDINGS_BLOCK_RELEASE=false (audit of production and development, 2026-10-28).
    # Critical and major always block; minor blocks when the operator's setting says so; info never does.
    blocking = [i for i in found if i.get("severity") != "minor" or _config.MINOR_FINDINGS_BLOCK_RELEASE]
    if audited_master and found and not blocking:
        # Shown, not blocking (MINOR_FINDINGS_BLOCK_RELEASE=false): the reviewer sees them on the card.
        checks.append({"name": "visual_checks", "status": "pass", "detail": found, "minor": True})
    else:
        # The reasons are what blocks; a minor finding that does not block is kept apart as a note. Passing every
        # finding listed a minor turnstile-photo note among the blocked reasons (production, rc72, 2026-10-29).
        notes = [i for i in found if not any(i is b for b in blocking)]
        add("visual_checks", audited_master and not found,
            blocking or ("Visual audit of this master found no issues." if audited_master
                         else "No completed visual audit recorded."))  # a passed audit used to read as none recorded
        if notes:
            checks[-1]["notes"] = notes
    for key in ("thumbnail_cover_path", "thumbnail_grid_path"):
        add(key, bool(run.get(key)) and Path(run[key]).is_file(), run.get(key))
    if profile.requires_youtube_thumbnail:
        thumbnail = payload.get("thumbnail_youtube")
        add("youtube_thumbnail", bool(thumbnail) and Path(thumbnail).is_file(), thumbnail)
    # Stories go out with no caption, so image_attribution (caption and description) never saw their photos'
    # credits; each frame must carry its own (2026-10-29). A run with no Story frames has nothing to check.
    stories = story_credit_problems(master.parent)
    if stories is not None:
        add("story_credits", not stories, stories or "Every Story frame credits its photo as its licence asks.")
    refused_frames = story_refusals(master.parent)
    if refused_frames:   # QF-94: shown and decided by the operator, never a silent short Story
        add("story_frames", False, refused_frames, review=True)
    status ="fail" if any(c["status"] == "fail" for c in checks) else (
        "needs_review" if any(c["status"] == "needs_review" for c in checks) else "pass")
    # 1.1: the report carries the picture measurement (QF-10); a 1.0 report was written by the gate before it.
    report = {"schema_version": "1.1", "checked_at": datetime.now(timezone.utc).isoformat(),
              "profile": profile.model_dump(), "status": status, "dry_run": dry_run, "checks": checks}
    _save_report(master.parent / "release_report.json", report)
    return report


def _freed_clip(category: str, path: Path, run_dir: Path) -> bool:
    """A scene clip that is gone from an intermediate folder (QF-110): its recorded sha256 is still compared with the
    provenance record's below, and the master's own sha256 binds the rest."""
    return category == "scenes" and not path.exists() and is_intermediate(path, run_dir)


def _approved_now(run) -> str:
    """The run's review fingerprint as it is now (telegram_state.review_fingerprint over its payload and files), or
    "" when it cannot be computed (a file missing)."""
    from .telegram_state import review_fingerprint

    class _One:
        @staticmethod
        def get_run(_run_id):
            return run
    try:
        return review_fingerprint(_One(), run.get("run_id"))
    except (OSError, ValueError, TypeError, KeyError):
        return ""


def _rechecked_facts(run, payload, problem: str):
    """(problem, detail) for the facts check when its stamp is not current (QF-110). A stamp that is stale only
    because a code update changed the checkers' rules or the copy fingerprint passes on a signed, passing re-check of
    this very copy under the current rules (fact_evidence.RECHECK_FILE beside the master), made for the content the
    operator approved and still matching it now; any other problem is kept as it is."""
    from . import fact_evidence as fe
    payload = {**payload, "topic_id": payload.get("topic_id") or run.get("topic_id")}   # a documentary's has none
    if not fe.drift_only(payload):
        return problem, problem
    try:
        rec = json.loads((Path(run["master_video_path"]).parent / fe.RECHECK_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, TypeError):
        rec = None
    why = fe.recheck_problem(payload, rec, _approved_now(run))
    if why:
        text = f"The fact check was made under earlier rules (a code update since); {why}."
        return text, text
    models = (rec.get("check") or {})
    return "", (f"Checked again under the current rules on {rec.get('checked_at')} (the fact check was made before a "
                f"code update): trace check ({(models.get('trace') or {}).get('model')}) and evidence check "
                f"({(models.get('evidence') or {}).get('model')}) passed on this copy, which is the one you approved.")


class ReleaseBlocked(ValueError):
    """The release checks refused this version (QF-110): no retry can change it, only a new version or putting its
    cause right. A ValueError, so every caller that refuses a blocked release still does; post_due tells it apart from
    a state that may pass (an upload in progress, an outcome unknown, a lock held)."""


class ReleaseReportLocked(ValueError):
    """The release report could not be replaced because another program holds it open. A ValueError, so every
    caller that refuses a blocked release ("Not posted", "Refusing") refuses this one in the same words."""


REPORT_REPLACE_TRIES = 7        # 6 waits doubling from 20 ms: 1.26 s in all before giving up
REPORT_REPLACE_FIRST_WAIT_S = 0.02


def _save_report(path: Path, report: dict) -> None:
    """Write the report atomically (a check never reads half of one, which would lose the old-master mark). On
    Windows the final rename fails while another handle has the file open without delete sharing (an editor, a
    reader, a virus scanner); that is retried for about 1.3 s, then refused in plain words, never a raw OSError."""
    import time

    from .atomic_files import write_json
    wait = REPORT_REPLACE_FIRST_WAIT_S
    for attempt in range(REPORT_REPLACE_TRIES):
        try:
            write_json(path, report)
            return
        except PermissionError:
            if attempt == REPORT_REPLACE_TRIES - 1:
                break
            time.sleep(wait)
            wait *= 2
    raise ReleaseReportLocked(f"the release report ({Path(path).name}) is open in another program, so this check "
                              "could not be saved, and nothing was approved or posted. Close it, then try again.")


def locked_report(exc: Exception) -> dict:
    """A report that holds the release when its file could not be saved (for callers that go on to the card)."""
    return {"schema_version": "1.1", "status": "fail", "dry_run": False,
            "checks": [{"name": "release_report", "status": "fail", "detail": str(exc)}]}


def _bound_composition(master: Path) -> dict | None:
    """The master's composition record when it is bound to these exact bytes, else None."""
    try:
        record = json.loads(Path(master).with_suffix(".composition.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) and record.get("master_sha256") == sha256_file(master) else None


GRANDFATHERED_COLOUR = ("Made before the colour fix: the video's colour tags are incomplete (its picture is BT.709). "
                        "It can still be posted; re-render it to tag it.")


def _gated_before_qf10(composition: dict | None, report_path: Path) -> str | None:
    """The master's sha256 when it was gated before QF-10, else None. Read from stored records, never guessed: its
    own composition record (bound to its sha256) was written before QF-10, which always adds `full_image_spans`;
    and the run's release report was written by the earlier gate (no picture measurement), or by this gate, which
    recorded the same finding for these same bytes (`gated_before_qf10` on its picture_measurement, kept whether the
    measurement passed or failed; or a round-5 report's `grandfathered` measurement)."""
    if composition is None or "full_image_spans" in composition or not composition.get("master_sha256"):
        return None
    sha = composition["master_sha256"]
    try:
        previous = json.loads(Path(report_path).read_text(encoding="utf-8"))
        earlier = {c.get("name"): c for c in previous.get("checks", [])}
    except (OSError, ValueError, AttributeError, TypeError):
        return None
    if "picture_measurement" not in earlier:
        return sha
    found = earlier["picture_measurement"] or {}
    measured = found.get("measurement") or {}
    round5 = measured.get("grandfathered") and measured.get("master_sha256") == sha
    return sha if found.get("gated_before_qf10") == sha or round5 else None


def _grandfather_colour(picture: dict, eligible: str | None) -> None:
    """Operator ruling (2026-11-10, "Grandfather old masters", decisions QF-10-ssim): a master gated before QF-10
    (`eligible`: _gated_before_qf10) whose colour tags are exactly the pre-QF-01 set gets a note, not a block. Every
    other picture check still blocks, and a new render gets no exemption."""
    from .picture_qa import PRE_QF01_TAGS
    colour = next((c for c in picture["checks"] if c["name"] == "picture_colour"), None)
    if colour is None or colour["status"] != "fail" or colour.get("tags") != PRE_QF01_TAGS:
        return
    if not eligible or eligible != picture.get("master_sha256"):
        return
    colour.update(status="pass", minor=True, grandfathered=True, detail=GRANDFATHERED_COLOUR)
    picture["grandfathered"] = True


FLASH_REVIEW = "flash_review.json"     # the operator's clearance of a held flash stretch, beside the master


def _flash_hold(picture: dict, master: Path) -> None:
    """QF-88, operator decision A after round 5: a picture_flash count only a motion test could confirm is held
    ("needs_review"), never passed silently and never rejected as a hazard. The report carries a contact sheet of
    each held stretch from the master's own frames; the hold blocks every live post until the operator clears it for
    these exact bytes and stretches (`review clear-flash`, clear_flash_hold), and then reads as a note.

    Only a pure hold: a check with any failing interval is a fail and stays one (round 7). A recorded report that
    holds a fail with a held stretch beside it (as round 6 measured) is put back to the fail it is, and an earlier
    clearance is never applied to it."""
    from . import picture_qa
    flash = next((c for c in picture.get("checks", []) if c.get("name") == "picture_flash"), None)
    if flash and flash.get("intervals"):
        if flash.get("hold") or flash.get("held") or flash.get("cleared"):
            for key in ("hold", "held", "cleared", "contact_sheets", "minor", "note"):
                flash.pop(key, None)
            flash["status"] = "fail"
            if flash.get("held_detail"):
                flash["detail"] = flash.pop("held_detail")
        return
    if not flash or not flash.get("hold") or not flash.get("held"):
        return
    # A reused report may hold the cleared form: the hold is always judged again from the record on disk.
    flash.update(status="needs_review", minor=False, note=False)
    flash.pop("cleared", None)
    if flash.get("held_detail"):
        flash["detail"] = flash["held_detail"]
    flash["held_detail"] = flash["detail"]
    sheets, missing = [], []
    for k, span in enumerate(flash["held"][:4]):
        out = Path(master).with_name(f"{Path(master).stem}.flash_hold_{k + 1}.jpg")
        known = next((s for s in flash.get("contact_sheets") or [] if s.get("path") == out.name
                      and s.get("start") == span["start"] and s.get("end") == span["end"]), None)
        try:
            if known and out.is_file() and known.get("master_sha256") == picture.get("master_sha256"):
                sheets.append(known)
                continue
            sheets.append({**picture_qa.hold_sheet(master, span["start"], span["end"], out),
                           "master_sha256": picture.get("master_sha256")})
        except Exception as exc:  # noqa: BLE001 - a missing sheet never lifts the hold; it is said instead
            missing.append(f"{_clock(span['start'])}-{_clock(span['end'])} ({exc})")
    flash["contact_sheets"] = sheets
    if sheets:
        flash["detail"] += " Contact sheet: " + ", ".join(s["path"] for s in sheets) + " (beside the master)."
    if missing:
        flash["detail"] += " The contact sheet could not be made for " + "; ".join(missing) + "."
    try:
        cleared = json.loads((Path(master).parent / FLASH_REVIEW).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if (isinstance(cleared, dict) and cleared.get("master_sha256") == picture.get("master_sha256")
            and cleared.get("held") == flash["held"] and cleared.get("cleared_at")):
        flash.update(status="pass", minor=True, note=True, cleared=cleared,
                     detail=f"Possible flash at {', '.join(_clock(s['start']) + '-' + _clock(s['end']) for s in flash['held'])}: "
                            f"you looked at it and cleared it ({str(cleared['cleared_at'])[:10]}).")


def held_flash(report) -> list:
    """The held picture_flash stretches of a report ([] when none is held, and always when the check has a failing
    interval: a fail is never a hold)."""
    flash = next((c for c in (report or {}).get("checks", []) if c.get("name") == "picture_flash"), None)
    return (list(flash.get("held") or []) if flash and flash.get("status") == "needs_review"
            and not flash.get("intervals") else [])


def flash_failure(report) -> str | None:
    """Why a report's picture_flash check fails (its sentence), or None when it has no failing interval."""
    flash = next((c for c in (report or {}).get("checks", []) if c.get("name") == "picture_flash"), None)
    return flash.get("detail") if flash and (flash.get("intervals") or flash.get("status") == "fail") else None


def clear_flash_hold(run_dir: Path, report: dict, by: str) -> dict:
    """The operator's clearance of a held flash stretch for these exact bytes and stretches, written beside the
    master; a re-render or a different hold needs a new look. Raises when the report holds nothing, and when the
    flash check fails anywhere: a failing flash is a hazard, never the operator's to clear (QF-88 round 7)."""
    flash = next((c for c in (report or {}).get("checks", []) if c.get("name") == "picture_flash"), None)
    if flash and (flash.get("intervals") or flash.get("status") == "fail"):
        raise ValueError("The flash check fails on this master (it flashes more than 3 times in a second somewhere), "
                         "so it cannot be cleared: re-render it without that flashing.")
    held = held_flash(report)
    measured = next((c for c in report.get("checks", []) if c.get("name") == "picture_measurement"), {})
    sha = (measured.get("measurement") or {}).get("master_sha256")
    if not flash or not held or not sha:
        raise ValueError("The release report holds no flash stretch for this master.")
    from datetime import UTC
    record = {"master_sha256": sha, "held": held, "cleared_at": datetime.now(UTC).isoformat(), "by": by,
              "contact_sheets": [s.get("path") for s in flash.get("contact_sheets") or []]}
    from .atomic_files import write_json
    write_json(Path(run_dir) / FLASH_REVIEW, record)
    return record


def _clock(seconds: float) -> str:
    return f"{int(seconds // 60)}:{seconds % 60:04.1f}"


def _reading_rate_checks(composition: dict | None) -> list:
    """QF-88, CPS (operator decision, option B): the captions' reading rate the composer recorded for these bytes.
    A timing defect (a burst shown faster than 17 cps and than its words are spoken) fails; the narration's own
    pace over 17 cps is a note, never a block. A master rendered before QF-88 has no record: nothing is claimed."""
    rate = (composition or {}).get("reading_rate")
    if not isinstance(rate, dict):
        return []
    limit = rate.get("max_cps", 17.0)
    defects = rate.get("timing_defects") or []
    if defects:
        shown = "; ".join(f"{_clock(d['at'])} \"{d['text']}\" at {d['shown_cps']} cps (spoken at {d['spoken_cps']})"
                          for d in defects[:3])
        return [{"name": "caption_rate", "status": "fail", "detail":
                 f"Captions shown faster than {limit:g} characters a second and faster than they are spoken: "
                 f"{shown}. Re-render: the caption timing must hold them longer."}]
    worst = rate.get("worst") or {}
    if rate.get("over_max"):
        return [{"name": "caption_rate", "status": "pass", "minor": True, "note": True, "rate": rate, "detail":
                 f"{rate['over_max']} of {rate['bursts']} caption bursts go by faster than {limit:g} characters a "
                 f"second because the narration is that fast (worst {worst.get('shown_cps')} cps at "
                 f"{_clock(worst.get('at') or 0)}, \"{worst.get('text')}\"). The captions keep pace with the voice; "
                 "a shorter script line there would slow them."}]
    return [{"name": "caption_rate", "status": "pass", "rate": rate,
             "detail": f"Every caption burst reads at {limit:g} characters a second or slower."}]


def _ingest_notes(run_dir: Path) -> list:
    """QF-88: footage and AI clips the ingest check could not measure (their records say "not measured"). A note,
    not a block: the master's own picture check, which fails closed, measured every frame they are in."""
    unmeasured = []
    for record in sorted([*Path(run_dir).glob("footage/footage_*.json"), *Path(run_dir).glob("motion/*.qa.json")]):
        try:
            state = str(json.loads(record.read_text(encoding="utf-8")).get("picture_check") or "")
        except (OSError, ValueError, AttributeError):
            continue
        if state.startswith("not measured"):
            unmeasured.append(f"{record.name.split('.')[0]} ({state})")
    if not unmeasured:
        return []
    return [{"name": "ingest_picture", "status": "pass", "minor": True, "note": True, "detail":
             f"Not picture-checked when taken in: {'; '.join(unmeasured[:4])}. The master's own check (black, "
             "freeze, flashing) measured every frame they appear in."}]


def _colour_vision_checks(run_dir: Path) -> list:
    """QF-88, CVD (operator decision, option C): every rendered graphic's meaning colours stay apart for
    protanopia, deuteranopia and tritanopia (colour_vision.check, Machado 2009); a pair under 10 dE00 fails. WCAG
    1.4.11 non-text contrast is measured and noted, never a block. A run without graphics gets no check."""
    from . import colour_vision
    try:
        palette = json.loads((Path(run_dir) / "palette.json").read_text(encoding="utf-8"))
        accent = palette.get("accent")
    except (OSError, ValueError, AttributeError):
        accent = None
    try:
        results = colour_vision.run_check(run_dir, accent)
    except Exception as exc:  # noqa: BLE001 - an unmeasured graphic is a failed check, never a pass
        return [{"name": "graphic_colour_vision", "status": "fail",
                 "detail": f"The graphics' colours could not be checked for colour-blind viewers: {exc}"}]
    if not results:
        return []
    names = {"gold": "the gold figures", "accent": "the accent lines", "teal": "the teal series",
             "coral": "the coral series", "home_fill": "the highlighted country", "land": "its neighbours"}
    bad = [(r["graphic"], p) for r in results for p in r["pairs"] if not p["pass"]]
    if bad:
        shown = "; ".join(f"{g}: {names[p['pair'][0]]} and {names[p['pair'][1]]} are {p['de00'][p['worst_vision']]} "
                          f"dE00 apart under {p['worst_vision']}" for g, p in bad[:3])
        return [{"name": "graphic_colour_vision", "status": "fail", "graphics": results, "detail":
                 f"A colour-blind viewer would see two meaning colours as one ({shown}; at least "
                 f"{colour_vision.MIN_DE00:g} is needed): re-render with an accent clear of them."}]
    skipped = "" if accent is not None else (
        " The run has no palette record (made before the art direction), so its accent is unknown and the gold "
        "against accent pair was not judged.")
    out = [{"name": "graphic_colour_vision", "status": "pass", "graphics": results, "detail":
            f"Every graphic's meaning colours stay apart for colour-blind viewers ({len(results)} checked; at least "
            f"{colour_vision.MIN_DE00:g} dE00 under protanopia, deuteranopia and tritanopia).{skipped}"}]
    short = [(r["graphic"], n) for r in results for n in r["nontext"] if not n["meets"]]
    if short:
        shown = "; ".join(f"{g}: {names[n['mark']]} {n['contrast'][n['worst_vision']]}:1"
                          + ("" if n["worst_vision"] == "normal" else f" under {n['worst_vision']}")
                          for g, n in short[:3])
        out.append({"name": "graphic_contrast", "status": "pass", "minor": True, "note": True, "detail":
                    f"Marks under the 3:1 contrast WCAG 1.4.11 asks against their ground: {shown}. A design "
                    "criterion for the freeze loop, not a block."})
    return out


def story_frames(run_dir: Path) -> list:
    """The run's Story frames, as main.py books and posts them (run_dir/stories/story_*.jpg): the sequence's
    frames only, never a retired poll frame an older run left (multi_format.story_posting_frames, QF-80)."""
    from .multi_format import story_posting_frames
    return [Path(p) for _, p in story_posting_frames(sorted((Path(run_dir) / "stories").glob("story_*.jpg")))]


def _plain_story_problem(text: str) -> str:
    """multi_format.story_credit_report's sentence in the operator's words (2026-10-29): "Story frame 2", not
    "story_02_reveal.jpg", and credit lines quoted, not a Python list."""
    import ast
    text = re.sub(r"^story_0*(\d+)\w*\.jpg:", lambda m: f"Story frame {int(m.group(1))}:", str(text))
    text = text.replace("no layout record of its photo and credit (drawn before 2026-10-29?)",
                        "no record of its photo and credit (made before Story credits were drawn: redraw the Stories)")

    def lines(m):
        try:
            found = ast.literal_eval(m.group(0))
        except (ValueError, SyntaxError):
            return m.group(0)
        return " / ".join(f'"{x}"' for x in found) if isinstance(found, list) and found else "nothing"
    return re.sub(r"\[[^\[\]]*\]|\bNone\b", lines, text)


def story_credit_problems(run_dir: Path):
    """None when the run has no Story frames; otherwise its frames' credit problems in plain words ([] when
    every frame credits its photo whole, at 4.5:1 contrast or more, as its licence asks)."""
    frames = story_frames(run_dir)
    if not frames:
        return None
    from .multi_format import story_credit_report
    return [_plain_story_problem(p) for p in story_credit_report(frames)["problems"]]


def story_refusals(run_dir: Path) -> list:
    """The Story frames refused because a field on them (their copy, or their photo's credit) has characters no
    bundled face draws (QF-94), in the operator's words: which frame, what it could not draw, and that the Story
    would post short. [] when none was refused."""
    try:
        layout = json.loads((Path(run_dir) / "stories" / "layout.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = [e for e in layout if isinstance(e, dict)] if isinstance(layout, list) else []
    refused = [e for e in entries if e.get("refused")]
    drawn = len(story_frames(run_dir))
    out = []
    for e in refused:
        frame = re.sub(r"^story_0*(\d+)\w*\.jpg$", lambda m: f"Story frame {int(m.group(1))}", str(e.get("frame")))
        chars = " ".join(str(e.get("chars") or ""))
        scripts = ", ".join(e.get("scripts") or [])
        text = " / ".join(" ".join(line.split()) for line in str(e.get("text") or "").splitlines() if line.strip())
        out.append(f"{frame} not drawn: no bundled face draws {chars} ({scripts}) in \u201c{text}\u201d; the Story "
                   f"would post {drawn} of {len(entries)} frames")
    return out


def blocking_checks(report, dry_run=False):
    blocking_statuses = ("fail",) if dry_run else ("fail", "needs_review")
    return [c for c in (report or {}).get("checks", []) if c.get("status") in blocking_statuses]


def plain_finding(issue) -> str:
    """An audit finding as the operator reads it. The graphic check's own sentence ("Scene 1: Graphic
    'graphic_scale.mp4' missing narration keywords ['280', '85', 'city', 'depth']") counted scenes from 0
    and read as code on the review card (production, rc72, 2026-10-29); beats count from 1 there."""
    if not isinstance(issue, dict):
        return str(issue or "")
    text = str(issue.get("issue") or issue.get("detail") or "")
    if issue.get("type") == "semantic_desync" and isinstance(issue.get("scene_index"), int) and issue["scene_index"] >= 0:
        from .graphic_match import kind_of
        kind = kind_of(Path(str(issue.get("asset") or ""))) or "original"
        shown = re.search(r"\[([^\]]*)\]", text)
        words = ", ".join(w.strip(" '\"") for w in shown.group(1).split(",") if w.strip(" '\"")) if shown else ""
        return (f"Beat {issue['scene_index'] + 1}: the {kind} graphic appears without the words it shows nearby"
                + (f" ({words})" if words else ""))
    return text


def check_problems(check, key: str = "detail") -> list:
    """A check's findings in words: an audit issue's own sentence, not the dict it is stored as. `key`: "notes"
    for the minor findings kept beside a blocked check."""
    detail = check.get(key)
    if key != "detail" and not detail:
        return []
    items = detail if isinstance(detail, list) else [detail]
    # A video-check finding says where it is and how serious ("beat 6, 0:21-0:26 (minor): ...").
    found = [(f"{i['where']}" + (f" ({i['severity']})" if i.get("severity") else "") + ": " if isinstance(i, dict) and i.get("where") else "")
             + plain_finding(i) for i in items]
    # A file is named, not its whole path (asset_rights lists the files still awaiting review).
    found = [Path(f).name if " " not in f and ("/" in f or "\\" in f) and Path(f).suffix else f for f in found]
    return [f for f in found if f] or ["no detail recorded"]


def release_problems(report, dry_run=False) -> list:
    """One line per blocking finding, "check: what was found" (the operator saw only "visual_checks")."""
    return [f"{c['name']}: {p}" for c in blocking_checks(report, dry_run) for p in check_problems(c)]


# What a blocking check is about, as the operator reads it on a blocked booking (QF-110); others by their name.
PLAIN_CHECKS = {"facts": "Fact check", "provenance": "Source files", "composition_inputs": "Recorded sources",
                "asset_rights": "Photo rights", "voiceover_rights": "Voice rights", "ambient_bed_rights": "Music rights",
                "font_rights": "Font rights", "image_attribution": "Photo credits", "description_attribution": "Photo credits",
                "credit_lines": "Credit lines", "credit_phrases": "Credit lines", "share_alike_licence": "Licence notice",
                "story_credits": "Story credits", "story_frames": "Story frames", "visual_checks": "Video check",
                "media_measurement": "Video file", "picture_measurement": "Picture", "audio_measurement": "Sound",
                "release_report": "Release report"}


def plain_block_reasons(report, limit: int = 4) -> list:
    """The release report's blocking checks in plain words, one line each ("Source files: its narration
    (voiceover.mp3) is missing"), at most `limit` (the rest counted)."""
    lines = []
    for c in blocking_checks(report):
        what = PLAIN_CHECKS.get(c.get("name"), str(c.get("name") or "check").replace("_", " ").capitalize())
        found = check_problems(c)
        lines.append(f"{what}: " + "; ".join(found[:3]) + (f" (and {len(found) - 3} more)" if len(found) > 3 else ""))
    if len(lines) > limit:
        lines = lines[:limit] + [f"and {len(lines) - limit} more check(s)"]
    return lines


def require_release(run, dry_run=False):
    report = write_release_report(run, dry_run=dry_run)
    problems = release_problems(report, dry_run)
    if problems:
        raise ReleaseBlocked("Release blocked: " + "; ".join(problems))
    return report


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Inspect a finished master without publishing it")
    parser.add_argument("master", type=Path)
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--cover", type=Path)
    parser.add_argument("--grid", type=Path)
    parser.add_argument("--profile", help="Explicit delivery profile for legacy metadata")
    args = parser.parse_args()
    root = args.master.parent
    metadata = args.metadata or root / "script_metadata.json"
    try:
        payload = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        parser.error(f"Cannot read package metadata: {exc}")
    if args.profile:
        try:
            payload["delivery_profile"] = get_delivery_profile(args.profile).name
        except KeyError as exc:
            parser.error(str(exc))
    report = write_release_report({"master_video_path": str(args.master), "script_payload": payload,
        "thumbnail_cover_path": str(args.cover or root / "thumbnail_cover.jpg"),
        "thumbnail_grid_path": str(args.grid or root / "thumbnail_profile_grid.jpg")})
    print(f"Release: {report['status']} ({report['profile']['name']})")
    for check in report["checks"]:
        print(f"  {check['status']}: {check['name']}")
    print(f"Evidence: {root / 'release_report.json'}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
