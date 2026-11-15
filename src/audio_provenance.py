"""Record audio generation and source evidence without inferring licenses from filenames."""

import json
from pathlib import Path

from .credits import license_allowed, sha256_file


def record_voice(path, provider, licensed, script, tier=None, plan_status=None):
    """The voiceover's rights record: who voiced it, on which tier (for ElevenLabs also the subscription's status
    at the take, when one was read), and their recorded licence status (voice_licence, QF-127). `licensed` alone
    never makes a take licensed: the provider and tier must be. With no tier given the record is judged as one
    made before the tier was saved (an Azure take is then not licensed)."""
    from .checkpoints import fingerprint
    from .voice_licence import record_licence
    path = Path(path)
    named = {"provider": provider, **({} if tier is None else {"tier": str(tier).strip().lower()}),
             **({} if tier is None or plan_status is None else {"plan_status": str(plan_status).strip().lower()})}
    ok, basis = record_licence({**named, "commercial_license_recorded": licensed is True})
    record = {"path": str(path.resolve()), "sha256": sha256_file(path),
              **named, "commercial_license_recorded": ok,
              "licence_basis": basis, "script_fingerprint": fingerprint(script)}
    if not record["sha256"]:
        raise ValueError("Cannot record absent narration")
    with open(path.with_suffix(path.suffix + ".rights.json"), "w", encoding="utf-8", newline="\n") as handle:  # LF: the repository refuses CRLF
        handle.write(json.dumps(record, indent=2))
    return record


def tracked_path(path) -> str:
    """A path inside the checkout as it is tracked (repo-relative, POSIX), so a committed record names no machine or
    user (QF-49); a path outside the checkout stays absolute."""
    from .config import BASE_DIR
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(BASE_DIR).as_posix()
    except ValueError:
        return str(resolved)


def record_sfx(path, provider, prompt, plan, commercial):
    """Rights evidence for one sound-effect file, bound to its exact bytes (sfx_rights checks the hash, not the path).
    The record is committed with data/sfx, so its path is the tracked one."""
    path = Path(path)
    record = {"path": tracked_path(path), "sha256": sha256_file(path), "provider": provider, "plan": plan,
              "prompt": prompt, "commercial_license_recorded": commercial is True}
    if not record["sha256"]:
        raise ValueError("Cannot record an absent sound effect")
    with open(path.with_suffix(path.suffix + ".rights.json"), "w", encoding="utf-8", newline="\n") as handle:  # LF: the repository refuses CRLF
        handle.write(json.dumps(record, indent=2))
    return record


def sfx_rights(path):
    """The file's rights record if it is intact and commercially licensed, else None (then the
    synthesized effect is used instead: a sound of unknown origin never enters a mix)."""
    path = Path(path)
    try:
        record = json.loads(path.with_suffix(path.suffix + ".rights.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if record.get("sha256") != sha256_file(path) or record.get("commercial_license_recorded") is not True:
        return None
    return record


def record_bed(path, music, sfx=None):
    """sfx: the effect files mixed into the bed (their rights records), so the bed's evidence
    covers every sound in it, not only the music."""
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
    effects = [{**r, "rights_status": "verified" if sfx_rights(r["path"]) else "unverified"} for r in (sfx or [])]
    record = {"path": str(path.resolve()), "sha256": sha256_file(path),
              "origin": music["source"], "sources": sources, "sound_effects": effects,
              "required_attribution": music.get("credit", "")}
    # The drone is synthesized locally. Provider music requires its own license evidence;
    # a paid text-to-speech account does not prove music clearance.
    record["rights_status"] = "verified" if music["source"] == "none" or (
        music["source"] == "library" and sources and
        all(s["rights_status"] == "verified" and s["sha256"] for s in sources)) else "unverified"
    if any(e["rights_status"] != "verified" for e in effects):
        record["rights_status"] = "unverified"
    if not record["sha256"]:
        raise ValueError("Cannot record absent music bed")
    with open(path.with_suffix(path.suffix + ".rights.json"), "w", encoding="utf-8", newline="\n") as handle:  # LF: the repository refuses CRLF
        handle.write(json.dumps(record, indent=2))
    return record


def rebind_rights(path) -> bool:
    """A carried-over file's rights record names the run folder it was made in. Rebinds the record to this copy
    when the bytes are the same (the hash recorded at generation still matches), keeping every other field and
    noting where it came from. A record whose hash does not match is left as it is, so the release gate still
    refuses it. Every regenerated run on production was sent for review as "blocked: Audio changed after
    rights evidence was recorded" though its voice and bed were byte-identical copies (2026-10-26, audit 28 Oct)."""
    path = Path(path)
    evidence = path.with_suffix(path.suffix + ".rights.json")
    try:
        record = json.loads(evidence.read_text(encoding="utf-8"))
        here = str(path.resolve())
        if not isinstance(record, dict) or record.get("path") == here or record.get("sha256") != sha256_file(path):
            return False
        record["carried_from"] = record["path"]
        record["path"] = here
    except (OSError, ValueError, KeyError, TypeError):
        return False
    with open(evidence, "w", encoding="utf-8", newline="\n") as handle:  # LF: the repository refuses CRLF
        handle.write(json.dumps(record, indent=2))
    return True


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
        if record["sha256"] != sha256_file(path):
            return False, "Audio changed after rights evidence was recorded"
        if Path(record["path"]).resolve() != path.resolve():
            # The same bytes, evidence copied from another run and not rebound: said as it is, since "Audio changed"
            # hid six such refusals on production (audit, 2026-10-28). Still refused.
            return False, f"Rights evidence names another file ({Path(record['path']).parent.name}) and was not rebound to this run"
        if kind == "voice":
            if script is not None:
                from .checkpoints import fingerprint
                if record.get("script_fingerprint") != fingerprint(script):
                    return False, "Narration was generated for a different script"
            # QF-127: the record is read against the per-provider/tier table, so a take marked licensed only
            # because an Azure key was configured does not pass.
            from .voice_licence import NOT_LICENSED, record_licence
            ok, basis = record_licence(record)
            if not ok and record.get("commercial_license_recorded") is True:   # the record claims what the table denies
                return False, f"{NOT_LICENSED.capitalize()}: {basis}"
            return ok, record
        sources_valid = all(s.get("sha256") and sha256_file(Path(s["path"])) == s["sha256"]
                            for s in record.get("sources", []))
        attribution = record.get("required_attribution", "")
        # The caption carries the credit as recorded, or (QF-126) the complete line of each track that asks for one:
        # a bed recorded before QF-126 names the short line, which the complete one replaces in the copy.
        from .music import credit_line
        complete = [line for line in (credit_line(s) for s in record.get("sources", [])
                                      if isinstance(s, dict) and s.get("title")) if line]
        have = {line.strip() for line in (caption or "").splitlines()}
        credited = not attribution or attribution in caption or (bool(complete) and all(c in have for c in complete))
        return bool(record.get("rights_status") == "verified" and sources_valid and credited), record
    except (OSError, ValueError, KeyError, TypeError):
        return False, "Audio rights evidence missing or malformed"
