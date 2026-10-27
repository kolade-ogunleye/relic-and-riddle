"""Measured, evidence-backed release checks shared by production and publishing."""

import json
import math
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .credits import SHARE_ALIKE_NOTICE, asset_attribution, is_share_alike, sha256_file
from .fact_evidence import current_fact_review
from .delivery_profiles import get_delivery_profile
from .video_qa import measure_program_loudness, verify_provenance_integrity


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
    add("facts", current_fact_review(payload),
        "Factual review must match the public copy and current research file.")
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

    ledger = master.parent / "provenance.json"
    integrity = verify_provenance_integrity(ledger)
    add("provenance", integrity, "All recorded files must exist and match their checksums.")
    if integrity:
        data = json.loads(ledger.read_text(encoding="utf-8"))
        unresolved = [a["path"] for a in data["assets"] if a.get("rights_status") != "verified"]
        add("asset_rights", not unresolved, unresolved, review=True)
        missing_credits = [asset_attribution(a) for a in data["assets"]
                           if asset_attribution(a) and asset_attribution(a) not in payload.get("caption", "")
                           and asset_attribution(a) not in payload.get("description", "")]
        add("image_attribution", not missing_credits, missing_credits)
        if any(is_share_alike(a.get("license", "")) for a in data["assets"]):
            # ShareAlike: the adaptation (this video) must be offered under CC BY-SA 4.0, in the post itself.
            add("share_alike_licence", SHARE_ALIKE_NOTICE in payload.get("caption", "") and
                (not profile.requires_youtube_thumbnail or SHARE_ALIKE_NOTICE in payload.get("description", "")),
                "A CC BY-SA image is used, so the post must state: " + SHARE_ALIKE_NOTICE)
        if profile.requires_youtube_thumbnail:
            missing_description = [asset_attribution(a) for a in data["assets"]
                if asset_attribution(a) and asset_attribution(a) not in payload.get("description", "")]
            add("description_attribution", not missing_description, missing_description)
        from .audio_provenance import inspect_audio_rights
        for key, kind in (("voiceover", "voice"), ("ambient_bed", "music")):
            audio = data.get(key)
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
                digest and sha256_file(Path(path)) == digest for path, digest in files.items())
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
    minor_only = bool(found) and all(i.get("severity") == "minor" for i in found)
    clean = quality.get("certified") is True and not any(i.get("severity") in ("critical", "major") for i in look)
    from . import config as _config
    if audited_master and clean and minor_only and not _config.MINOR_FINDINGS_BLOCK_RELEASE:
        # Shown, not blocking (MINOR_FINDINGS_BLOCK_RELEASE=false): the reviewer sees them on the card.
        checks.append({"name": "visual_checks", "status": "pass", "detail": found, "minor": True})
    else:
        add("visual_checks", clean and audited_master and not found,
            found or ("Visual audit certified this master; no issues." if audited_master and clean
                      else "No completed visual audit recorded."))  # a passed audit used to read as none recorded
    for key in ("thumbnail_cover_path", "thumbnail_grid_path"):
        add(key, bool(run.get(key)) and Path(run[key]).is_file(), run.get(key))
    if profile.requires_youtube_thumbnail:
        thumbnail = payload.get("thumbnail_youtube")
        add("youtube_thumbnail", bool(thumbnail) and Path(thumbnail).is_file(), thumbnail)
    status = "fail" if any(c["status"] == "fail" for c in checks) else (
        "needs_review" if any(c["status"] == "needs_review" for c in checks) else "pass")
    report = {"schema_version": "1.0", "checked_at": datetime.now(timezone.utc).isoformat(),
              "profile": profile.model_dump(), "status": status, "dry_run": dry_run, "checks": checks}
    master.parent.mkdir(parents=True, exist_ok=True)
    (master.parent / "release_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def blocking_checks(report, dry_run=False):
    blocking_statuses = ("fail",) if dry_run else ("fail", "needs_review")
    return [c for c in (report or {}).get("checks", []) if c.get("status") in blocking_statuses]


def check_problems(check) -> list:
    """A check's findings in words: an audit issue's own sentence, not the dict it is stored as."""
    detail = check.get("detail")
    items = detail if isinstance(detail, list) else [detail]
    # A video-check finding says where it is and how serious ("beat 6, 0:21-0:26 (minor): ...").
    found = [(f"{i['where']}" + (f" ({i['severity']})" if i.get("severity") else "") + ": " if isinstance(i, dict) and i.get("where") else "")
             + (str(i.get("issue") or i.get("detail") or "") if isinstance(i, dict) else str(i or "")) for i in items]
    # A file is named, not its whole path (asset_rights lists the files still awaiting review).
    found = [Path(f).name if " " not in f and ("/" in f or "\\" in f) and Path(f).suffix else f for f in found]
    return [f for f in found if f] or ["no detail recorded"]


def release_problems(report, dry_run=False) -> list:
    """One line per blocking finding, "check: what was found" (the operator saw only "visual_checks")."""
    return [f"{c['name']}: {p}" for c in blocking_checks(report, dry_run) for p in check_problems(c)]


def require_release(run, dry_run=False):
    report = write_release_report(run, dry_run=dry_run)
    problems = release_problems(report, dry_run)
    if problems:
        raise ValueError("Release blocked: " + "; ".join(problems))
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
