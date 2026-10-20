"""Record audio generation and source evidence without inferring licenses from filenames."""

import json
from pathlib import Path

from .credits import license_allowed, sha256_file


def record_voice(path, provider, licensed, script):
    from .checkpoints import fingerprint
    path = Path(path)
    record = {"path": str(path.resolve()), "sha256": sha256_file(path),
              "provider": provider, "commercial_license_recorded": licensed is True,
              "script_fingerprint": fingerprint(script)}
    if not record["sha256"]:
        raise ValueError("Cannot record absent narration")
    path.with_suffix(path.suffix + ".rights.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def record_bed(path, music):
    path = Path(path)
    sources = []
    for track in music.get("tracks", []):
        source = Path(track["path"])
        license_name = track.get("license", "")
        if license_name.lower() == "by":
            license_name = "CC BY " + track.get("license_version", "")
        verified = bool(track.get("reviewed") and license_allowed(license_name)
                        and track.get("source_url") and track.get("license_url"))
        sources.append({**track, "path": str(source.resolve()), "sha256": sha256_file(source),
                        "license": license_name, "rights_status": "verified" if verified else "unverified"})
    if not sources and music.get("source") != "none":
        for item in music.get("paths") or ([music["path"]] if music.get("path") else []):
            source = Path(item)
            sources.append({"path": str(source.resolve()), "sha256": sha256_file(source),
                            "rights_status": "unverified", "provider": music["source"]})
    record = {"path": str(path.resolve()), "sha256": sha256_file(path),
              "origin": music["source"], "sources": sources,
              "required_attribution": music.get("credit", "")}
    # The drone is synthesized locally. Provider music requires its own license evidence;
    # a paid text-to-speech account does not prove music clearance.
    record["rights_status"] = "verified" if music["source"] == "none" or (
        music["source"] == "library" and sources and
        all(s["rights_status"] == "verified" and s["sha256"] for s in sources)) else "unverified"
    if not record["sha256"]:
        raise ValueError("Cannot record absent music bed")
    path.with_suffix(path.suffix + ".rights.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def audio_source_inputs(path):
    from .checkpoints import file_inputs
    path = Path(path)
    try:
        record = json.loads(path.with_suffix(path.suffix + ".rights.json").read_text(encoding="utf-8"))
        return file_inputs([source["path"] for source in record.get("sources", [])])
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def inspect_audio_rights(path, kind, caption, script=None):
    path = Path(path)
    evidence = path.with_suffix(path.suffix + ".rights.json")
    try:
        record = json.loads(evidence.read_text(encoding="utf-8"))
        if record["sha256"] != sha256_file(path) or Path(record["path"]).resolve() != path.resolve():
            return False, "Audio changed after rights evidence was recorded"
        if kind == "voice":
            if script is not None:
                from .checkpoints import fingerprint
                if record.get("script_fingerprint") != fingerprint(script):
                    return False, "Narration was generated for a different script"
            return record.get("commercial_license_recorded") is True, record
        sources_valid = all(s.get("sha256") and sha256_file(Path(s["path"])) == s["sha256"]
                            for s in record.get("sources", []))
        attribution = record.get("required_attribution", "")
        return bool(record.get("rights_status") == "verified" and sources_valid and
                    (not attribution or attribution in caption)), record
    except (OSError, ValueError, KeyError, TypeError):
        return False, "Audio rights evidence missing or malformed"
