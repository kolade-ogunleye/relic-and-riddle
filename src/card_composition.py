"""Bind reviewed design cards to their source fields and actual scene intervals.

Rendering never calls a checker. The explicit pre-voice chapter verification
uses the existing fact gate; stale or unsigned sources refuse locally.
"""
from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import replace
from itertools import pairwise
from pathlib import Path

from . import card_fields, config, record_signing
from .checkpoints import Checkpoints, file_inputs, fingerprint
from .credits import sha256_file
from .lineage import archive_generator, record_graphic, sidecar, source_chain

log = logging.getLogger(__name__)
VERSION = "card-composition-1"
KINDS = {"chapter", "mythfact", "quote", "hook"}


def contract_path(path: Path) -> Path:
    return Path(str(path) + ".card.json")


def _normal(text: str) -> str:
    from .design_cards import _canon
    return _canon(text)


def _same_line(first: str, second: str) -> bool:
    # A question cannot inherit the authority of an assertion (or vice versa).
    return (_normal(first) == _normal(second)
            and str(first).rstrip().endswith("?") == str(second).rstrip().endswith("?"))


def _spoken(text: str) -> list[str]:
    return re.findall(r"\d[\d,]*(?:\.\d+)?|[^\W\d_]+(?:'[^\W\d_]+)?", _normal(text))


def _phrase_times(words, text: str) -> list[tuple[float, float]]:
    tokens = [(token, float(word["start"]), float(word["start"]) + float(word["dur"]))
              for word in words for token in _spoken(word["word"])]
    wanted = _spoken(text)
    if not wanted:
        return []
    return [(tokens[i][1], tokens[i + len(wanted) - 1][2])
            for i in range(len(tokens) - len(wanted) + 1)
            if [token[0] for token in tokens[i:i + len(wanted)]] == wanted]


def _myth_timing(spec, field, sheet, words, width, height):
    """Fit the existing animation to the complete correction's spoken onset.

    The first correction line must be fully visible when its first word is
    spoken. The renderer's myth reading/strike/fact/source hold floors still
    govern; an impossibly fast exchange refuses instead of being sped up.
    """
    from . import design_cards as dc
    if not words:
        raise dc.CardRefused("myth card needs the actual checked narration timings")
    baseline = dc.plan_myth_fact(spec, sheet, width, height)
    first = next(element for element in baseline.elements if element.name == "fact_0")
    for myth_start, myth_end in _phrase_times(words, field["myth"]):
        if myth_start <= 0:
            continue
        for correction_start, correction_end in _phrase_times(words, field["correction"]):
            if correction_start < myth_end:
                continue
            delta = correction_start - myth_start - first.t_full
            candidate = replace(spec, fact_at=baseline.meta["t_s"] + baseline.meta["pen_total"] + delta)
            try:
                planned = dc.plan_myth_fact(candidate, sheet, width, height)
            except dc.CardRefused:
                continue
            actual = next(element.t_full for element in planned.elements if element.name == "fact_0")
            if (abs(myth_start + actual - correction_start) > .5 / config.VIDEO_FPS
                    or correction_end > myth_start + planned.seconds):
                continue
            return candidate, {"at_s": myth_start, "correction_s": correction_start,
                               "first_fact_full_s": actual, "correction_end_s": correction_end}
    raise dc.CardRefused("complete myth and correction cannot match the narration within their reading floors")


def load_contract(path: Path) -> dict:
    """Recheck signed asset identity, source lineage and source-field checks."""
    path = Path(path)
    record = json.loads(contract_path(path).read_text(encoding="utf-8"))
    why = record_signing.problem("design_card_asset", record)
    if why:
        raise ValueError("Untrusted design card: " + why)
    if record.get("version") != VERSION or record.get("kind") not in KINDS:
        raise ValueError("Unknown design-card contract")
    if record.get("sha256") != sha256_file(path):
        raise ValueError("Design-card media changed")
    design = Path(str(path) + ".design.json")
    if record.get("design_sha256") != sha256_file(design):
        raise ValueError("Design-card metadata changed")
    if record.get("contrast_sha256") != sha256_file(Path(str(path) + ".contrast.json")):
        raise ValueError("Design-card contrast evidence changed")
    if not source_chain(path):
        raise ValueError("Design card has no source lineage")
    sheet_path = Path(record["sheet_path"])
    if record["sheet_sha256"] != sha256_file(sheet_path):
        raise ValueError("Design-card fact sheet changed")
    sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
    if record.get("gated_payload") is not None:
        from .design_cards import Gated, gated_text
        checked = record["gated_payload"]
        source = Gated("title", checked["fact_review"]["public_fingerprint"], checked)
        if not _same_line(gated_text(source, checked, sheet), record["checked_text"]):
            raise ValueError("Design-card title differs from its checked copy")
    else:
        card_fields.verify_field(sheet, record["field_kind"], record["field_id"])
    if record["kind"] == "quote" and not config.QUOTE_CARDS_ENABLED:
        raise ValueError("Quote cards are held")
    if record["kind"] == "mythfact":
        timing = record["narration_timing"]
        measured = json.loads(design.read_text(encoding="utf-8"))
        first = next(e["t_full"] for e in measured["elements"] if e["name"] == "fact_0")
        if abs(timing["at_s"] + first - timing["correction_s"]) > .5 / config.VIDEO_FPS:
            raise ValueError("Myth correction is not synchronized with its checked narration")
    return record


def is_card(path: Path) -> bool:
    stem = Path(path).stem
    return stem == "hook_frame" or any(stem.startswith("graphic_" + kind + "_")
                                       for kind in ("chapter", "mythfact", "quote"))


def matches(path: Path, beat: dict, words=None) -> bool:
    """Cards need their full reading time and their exact declared narration."""
    try:
        record = load_contract(path)
        duration = float(beat["end"]) - float(beat["start"])
        if duration + 1e-6 < record["seconds"]:
            return False
        if record["kind"] == "chapter":
            return abs(float(beat["start"]) - record["at_s"]) <= .5 / config.VIDEO_FPS
        if record["kind"] == "mythfact":
            timing = record["narration_timing"]
            if abs(float(beat["start"]) - timing["at_s"]) > .5 / config.VIDEO_FPS:
                return False
            if words is not None:
                for text, expected in zip(record["narration_text"], (timing["at_s"], timing["correction_s"])):
                    if not any(abs(start - expected) <= .5 / config.VIDEO_FPS
                               for start, _ in _phrase_times(words, text)):
                        return False
        said = _spoken(beat.get("text", ""))
        phrases = [_spoken(text) for text in record["narration_text"]]
        return record["kind"] in ("mythfact", "quote") and bool(phrases) and all(phrases) and all(
            any(said[i:i + len(phrase)] == phrase for i in range(len(said) - len(phrase) + 1))
            for phrase in phrases)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def cache_inputs(paths) -> dict:
    cards = [Path(p) for p in paths if is_card(Path(p))]
    return {str(p): {"contract": load_contract(p), "files": file_inputs(
        [p, contract_path(p), Path(str(p) + ".design.json"), Path(str(p) + ".contrast.json"),
         sidecar(p)])} for p in cards}


def chapter_cuts(cuts: list[dict], chapters: list[dict], words: list[dict], total: float) -> list[dict]:
    """Give every documentary chapter its complete reading interval at its act.

    Only picture cuts move. Audio and word records remain untouched; each word
    belongs to exactly one new picture interval by its original start time.
    """
    from .design_cards import CHAPTER_SECONDS
    seconds = CHAPTER_SECONDS[True]
    starts = [float(c["start_sec"]) for c in chapters]
    if not starts:
        return cuts
    if any(not math.isfinite(t) or t < 0 or t + seconds > total + 1e-6 for t in starts):
        raise ValueError("Chapter interval does not fit the documentary")
    if any(b - a < seconds for a, b in pairwise(starts)):
        raise ValueError("Chapter cards have overlapping reading intervals")
    edges = {0.0, total, *starts, *(t + seconds for t in starts)}
    for cut in cuts:
        t = float(cut["start"])
        if not any(start < t < start + seconds for start in starts):
            edges.add(t)
    edges = sorted(edges)
    return [{"start": a, "end": b, "text": " ".join(w["word"] for w in words
             if a <= float(w["start"]) < b)} for a, b in pairwise(edges)]


def card_cuts(beats: list[dict], cards, words=None) -> list[dict]:
    """Join adjacent picture beats only to show a complete matching card.

    Every spoken word and timestamp stays unchanged. This precedes image query,
    planning and photo-coverage gates, so cards never bypass those gates.
    """
    from .design_cards import MAX_SECONDS
    from .image_review import required_photos
    from .scene_planner import MAX_GRAPHICS
    out = [dict(b) for b in beats]
    for card in cards:
        record = load_contract(card)
        if record["kind"] not in ("mythfact", "quote"):
            continue
        found = False
        for start in range(1, len(out)):
            for end in range(start + 1, len(out) + 1):
                block = out[start:end]
                joined = {"start": block[0]["start"], "end": block[-1]["end"],
                          "text": " ".join(b["text"] for b in block)}
                if joined["end"] - joined["start"] > MAX_SECONDS:
                    break
                if end - start > 1 and len(out) - (end - start) + 1 - MAX_GRAPHICS < required_photos():
                    break  # a card cannot spend the distinct-photo slots needed by the Reel
                if matches(card, joined, words):
                    out[start:end] = [joined]
                    found = True
                    break
            if found:
                break
    return out


def require_chapters(scenes, chapters) -> None:
    records = [load_contract(p) for p in scenes if is_card(p)]
    shown = sorted(r["number"] for r in records if r["kind"] == "chapter")
    expected = [c["act_number"] for c in chapters]
    if shown != expected:
        raise ValueError(f"Documentary chapter cards incomplete: {shown}; expected {expected}")


def opening_card(work_dir: Path) -> Path | None:
    """Only this generation's successful hook is eligible, never an old file."""
    try:
        selected = json.loads((Path(work_dir) / "card-selection.json").read_text(encoding="utf-8"))
        if selected.get("hook") != "hook_frame.mp4":
            return None
        path = Path(work_dir) / "hook_frame.mp4"
        record = load_contract(path)
        return path if record["kind"] == "hook" else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def composed_words(manifest: dict, original: list[dict]) -> list[dict]:
    """Release verifies the master clock against original, cached narration words."""
    offset = float(manifest.get("opening_offset_s", 0.0))
    opening = manifest.get("opening_card")
    if not opening and offset == 0.0:
        return original
    if not opening or offset <= 0:
        raise ValueError("Composition offset requires its verified opening")
    record = load_contract(Path(opening["asset"]))
    expected = math.ceil(record["caption_clear_until_s"] * config.VIDEO_FPS) / config.VIDEO_FPS
    if (record["kind"] != "hook" or offset != expected or offset > record["seconds"]
            or opening["start"] != 0.0 or opening["end"] != offset or opening["voice_start"] != offset
            or manifest.get("scenes", {}).get(opening["asset"]) != record["sha256"]):
        raise ValueError("Composition clock or opening source differs from the checked hook")
    return [{**word, "start": float(word["start"]) + offset} for word in original]


def _opening_voice_rights(source: Path, dest: Path, offset: float) -> Path:
    """Carry hash-bound source evidence through silence insertion; never infer a license."""
    source_rights = source.with_suffix(source.suffix + ".rights.json")
    try:
        record = json.loads(source_rights.read_text(encoding="utf-8"))
        if record["sha256"] != sha256_file(source) or Path(record["path"]).resolve() != source.resolve():
            raise ValueError("Source rights do not match narration")
    except (OSError, ValueError, KeyError, TypeError):
        record = {"commercial_license_recorded": False, "provider": "unverified source"}
    derived = {**record, "path": str(dest.resolve()), "sha256": sha256_file(dest),
               "operation": "insert_silence_before_unchanged_samples", "offset_s": offset,
               "sources": [*record.get("sources", []),
                           {"path": str(source.resolve()), "sha256": sha256_file(source)}]}
    if source_rights.is_file():
        derived["sources"].append({"path": str(source_rights.resolve()), "sha256": sha256_file(source_rights)})
    evidence = dest.with_suffix(dest.suffix + ".rights.json")
    evidence.write_text(json.dumps(derived, indent=2) + "\n", encoding="utf-8")
    return evidence


def opening_schedule(hook: Path | None, words: list[dict], durations: list[float], beats: list[dict],
                     voice: Path, bed: Path, total: float, work_dir: Path) -> dict:
    """Add a caption-clear voice pre-roll; preserve every original voice sample.

    Only the voice gets silence. The caller mixes the bed against this schedule
    through the normal music/ducking/SFX path; an existing mixed stem is not padded.
    The first picture's spoken interval and every subsequent beat remain intact;
    the verified hook covers only the added prefix of that first picture.
    """
    out = {"hook": hook, "offset": 0.0, "words": words, "durations": durations,
           "beats": beats, "voice": voice, "bed": bed, "total": total}
    if hook is None:
        return out
    record = load_contract(hook)
    if record["kind"] != "hook" or not durations:
        raise ValueError("An opening schedule requires a verified hook and a picture timeline")
    offset = math.ceil(record["caption_clear_until_s"] * config.VIDEO_FPS) / config.VIDEO_FPS
    if not 0 < offset <= record["seconds"]:
        raise ValueError("Hook clear interval falls outside its clip")
    import numpy as np
    import soundfile as sf

    from .lineage import record_derived
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    shifted_audio = []
    for label, path in (("voice", voice),):
        identity = fingerprint({"source": sha256_file(path), "offset": offset, "version": VERSION,
                                "rules": sha256_file(Path(__file__)),
                                "rights": sha256_file(path.with_suffix(path.suffix + ".rights.json"))})
        dest = work_dir / f"{label}-{identity[:16]}.wav"
        checkpoint = Checkpoints(work_dir / (label + "-cache"))
        if not checkpoint.reusable("audio", identity):
            samples, rate = sf.read(path, dtype="float64", always_2d=True)
            silence = np.zeros((round(offset * rate), samples.shape[1]), dtype=np.float64)
            # IEEE float64 retains the decoded original samples exactly.
            sf.write(dest, np.concatenate((silence, samples)), rate, subtype="DOUBLE")
            record_derived(dest, [path], "design_card_preroll", {"offset_s": offset, "silent_prefix": True})
            rights = _opening_voice_rights(path, dest, offset)
            checkpoint.complete("audio", identity, [dest, sidecar(dest), rights])
        shifted_audio.append(dest)
    out.update(offset=offset, words=[{**w, "start": float(w["start"]) + offset} for w in words],
               durations=[durations[0] + offset, *durations[1:]], total=total + offset,
               beats=[{**b, "start": b["start"] + offset,
                       "end": b["end"] + offset} for b in beats],
               voice=shifted_audio[0])
    return out


def mix_opening_bed(engine, music: dict, base_bed: Path, words: list[dict], sound_plan: dict | None,
                    bed_seconds: float, voice_seconds: float, durations: list[float], offset: float,
                    *, sample_rate: int, loop_seamless: bool, end_card: bool) -> Path:
    """Re-mix the selected licensed score once on the verified opening timeline."""
    from .audio_provenance import audio_source_inputs, record_bed
    from .video_composer import caption_cut_times
    shifted_words = [{**w, "start": float(w["start"]) + offset} for w in words]
    shifted_plan = ({**sound_plan, "moments": [{**m, "t": float(m["t"]) + offset}
                    for m in sound_plan.get("moments", [])]} if sound_plan else None)
    out = Path(base_bed).with_name(Path(base_bed).stem + "_opening.wav")
    inputs = {"offset": offset, "words": shifted_words, "plan": shifted_plan,
              "duration": bed_seconds + offset, "voice_end": voice_seconds + offset,
              "cuts": caption_cut_times(durations), "music": music,
              "sources": file_inputs([Path(__file__), Path(config.__file__),
                  Path(__file__).with_name("audio_engine.py"),
                  *([Path(music["path"])] if music.get("path") else []),
                  *(Path(p) for p in music.get("paths") or [])]),
              "selected_sources": audio_source_inputs(out),
              "sample_rate": sample_rate, "loop_seamless": loop_seamless, "end_card": end_card}
    checkpoint = Checkpoints(out.parent / "stage_cache" / "opening-bed")
    if not checkpoint.reusable("bed", inputs):
        engine.generate_ambient_bed(bed_seconds + offset, out, words=shifted_words,
            music_path=music.get("path"), music_paths=music.get("paths"), sr=sample_rate,
            loop_seamless=loop_seamless, sound_plan=shifted_plan,
            end_card_at=voice_seconds + offset if end_card else None, scene_cuts=inputs["cuts"])
        record_bed(out, music, sfx=engine.last_sfx_sources)
        inputs["selected_sources"] = audio_source_inputs(out)
        checkpoint.complete("bed", inputs, [out, out.with_suffix(".wav.rights.json")])
    return out


def make_all(payload: dict, work_dir: Path, *, photo: Path | None = None,
             width: int = 1080, height: int = 1920, words=None) -> list[Path]:
    """Render only eligible signed fields; the hook is separately scheduled.

    Each chapter heading must match a checked statement or its own gate-passed
    title. Reviewing the overall payload never approves an unchecked act title.
    """
    from . import design_cards as dc
    work_dir = Path(work_dir)
    review = payload.get("fact_review") or {}
    sheet_path = Path(payload.get("design_card_sheet") or review.get("sheet_path")
                      or work_dir / "missing-sheet.json")
    refused = []
    try:
        sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    fields = sheet.get("cards") or {}
    requests = []
    chapters = payload.get("chapters") or []
    if chapters and [c.get("act_number") for c in chapters] != list(range(1, len(chapters) + 1)):
        refused.append({"kind": "chapter", "reason": "chapter numbers must be complete and sequential"})
        chapters = []
    for chapter in chapters:
        field = next((f for f in fields.get("statements", [])
                      if _same_line(f.get("text", ""), chapter.get("title", ""))), None)
        number = chapter["act_number"]
        gate = next((g for g in sheet.get("chapter_gates", []) if g["number"] == number
                     and _same_line(g["payload"].get("title", ""), chapter.get("title", ""))), None)
        if field is None and gate is None:
            refused.append({"kind": "chapter", "number": chapter["act_number"],
                            "reason": "chapter title has no checked statement or title gate"})
            continue
        if field is None:
            checked = gate["payload"]
            field = {"id": f"chapter_{number}", "text": checked["title"], "gated_payload": checked,
                     "fact_ids": gate["fact_ids"]}
            title_src = dc.Gated("title", checked["fact_review"]["public_fingerprint"], checked)
        else:
            title_src = dc.CardRef("statements", field["id"])
        spec = dc.ChapterCard(number, len(chapters), field["text"], photo=photo,
                              title_src=title_src, fact_ids=tuple(field.get("fact_ids", [])))
        requests.append(("chapter", number, "statements", field, spec, float(chapter["start_sec"]), []))
    for kind, plural, spec_type in (("mythfact", "myths", dc.MythFact), ("quote", "quotes", dc.QuoteCard)):
        for number, field in enumerate(fields.get(plural, []), 1):
            ref = dc.CardRef(plural, field["id"])
            spec = spec_type(**({"myth_src": ref, "photo": photo} if kind == "mythfact" else {"quote_src": ref}))
            texts = [field.get("myth", ""), field.get("correction", "")] if kind == "mythfact" else [
                field.get("quotation", ""), (field.get("speaker") or {}).get("name", "")]
            at = None
            if kind == "mythfact":
                try:
                    spec, timing = _myth_timing(spec, field, sheet, words, width, height)
                except dc.CardRefused as exc:
                    refused.append({"kind": kind, "field_id": field["id"], "reason": str(exc)})
                    continue
                field = {**field, "narration_timing": timing}
                at = timing["at_s"]
            requests.append((kind, number, plural, field, spec, at, texts))
    from .scene_planner import strong_shot
    if words and photo and height > width and strong_shot(photo):
        from .video_composer import caption_phrases
        first = " ".join(w["word"] for w in caption_phrases(words)[0])
        for field in fields.get("statements", []):
            text = field.get("text", "")
            if len(text) <= 30 and len(text.split()) <= 6:
                spec = dc.HookFrame(text, (field["fact_id"],), photo, first,
                                    claim_src=dc.CardRef("statements", field["id"]),
                                    run_myths=tuple(f.get("myth", "") for f in fields.get("myths", [])))
                requests.append(("hook", 1, "statements", field, spec, 0.0, []))
    made, hook_made = [], False
    work_dir.mkdir(parents=True, exist_ok=True)
    for kind, number, plural, field, spec, at, texts in requests:
        if kind == "hook" and hook_made:
            continue
        out = work_dir / ("hook_frame.mp4" if kind == "hook" else f"graphic_{kind}_{number:02d}.mp4")
        try:
            if field.get("gated_payload") is not None:
                dc.gated_text(spec.title_src, field["gated_payload"], sheet)
            else:
                card_fields.verify_field(sheet, plural, field["id"])
            if kind == "quote" and not config.QUOTE_CARDS_ENABLED:
                raise dc.CardRefused("quote cards are held", code="held")
            if at is not None and (not math.isfinite(at) or at < 0):
                raise dc.CardRefused("invalid chapter time")
            source_files = [Path(__file__), Path(dc.__file__), sheet_path, Path(config.__file__),
                            *(Path(__file__).with_name(name + ".py") for name in
                              ("graphics", "typography", "brand", "safe_zones", "card_fields", "fact_evidence"))]
            from .typography import FONTS_DIR, bundled_font_files
            source_files += [FONTS_DIR / name for name in bundled_font_files()]
            if photo:
                source_files.append(photo)
            identity = {"sources": file_inputs(source_files), "field": field, "at_s": at,
                        "kind": kind, "number": number, "chapters": chapters,
                        "width": width, "height": height, "words": words if kind in ("hook", "mythfact") else None}
            checkpoint = Checkpoints(work_dir / "stage_cache" / out.stem)
            if checkpoint.reusable("card", identity):
                load_contract(out)
            else:
                renderer = {"chapter": dc.render_chapter_card, "mythfact": dc.render_myth_fact,
                            "quote": dc.render_quote_card, "hook": dc.render_hook_frame}[kind]
                result = (renderer(spec, sheet, work_dir, payload=payload) if kind == "hook" else
                          renderer(spec, sheet, out, width, height, payload=payload))
                if result is None:
                    raise dc.CardRefused(json.loads(Path(str(out) + ".design.json").read_text(encoding="utf-8"))["refused"])
                design_path = Path(str(out) + ".design.json")
                design = json.loads(design_path.read_text(encoding="utf-8"))
                record_graphic(out, Path(dc.__file__), {"builder": kind, "card_field": field,
                               "width": width, "height": height}, [photo] if photo else [],
                               dependencies=[sheet_path, archive_generator(Path(__file__), work_dir)])
                # The existing encoder emits the measured last-frame poster.
                record = {"version": VERSION, "kind": kind, "sha256": sha256_file(out),
                          "design_sha256": sha256_file(design_path), "field_kind": plural,
                          "contrast_sha256": sha256_file(Path(str(out) + ".contrast.json")),
                          "field_id": field["id"], "sheet_path": str(sheet_path.resolve()),
                          "sheet_sha256": sha256_file(sheet_path), "seconds": design["seconds"],
                          "at_s": at, "narration_text": texts, "identity": fingerprint(identity),
                          "width": width, "height": height, "number": number,
                          "caption_clear_until_s": design.get("caption_clear_until_s", 0.0)}
                if kind in ("chapter", "hook"):
                    record["checked_text"] = field["text"]
                if kind == "mythfact":
                    record["narration_timing"] = field["narration_timing"]
                if field.get("gated_payload") is not None:
                    record.update(gated_payload=field["gated_payload"], checked_text=field["text"])
                record_signing.signed("design_card_asset", record)
                contract_path(out).write_text(json.dumps(record, indent=2), encoding="utf-8")
                load_contract(out)
                checkpoint.complete("card", identity, [out, out.with_suffix(".jpg"), design_path,
                                                        Path(str(out) + ".contrast.json"), sidecar(out), contract_path(out)])
            if kind == "hook":
                hook_made = True
            else:
                made.append(out)
        except (dc.CardRefused, card_fields.FieldRefused, OSError, ValueError, KeyError, TypeError) as exc:
            refused.append({"kind": kind, "field_id": field.get("id"), "reason": str(exc)})
            log.warning("Design card %s refused: %s", kind, exc)
    (work_dir / "card-selection.json").write_text(json.dumps({"version": VERSION,
        "made": [p.name for p in made], "hook": "hook_frame.mp4" if hook_made else None,
        "refused": refused}, indent=2), encoding="utf-8")
    return made


def check_chapter_fields(payload: dict, source: Path, destination: Path, *, trace=None, evidence=None) -> Path:
    """Verify each authored chapter title through the existing two-checker gate.

    Each heading is checked as a standalone title, the existing Gated source
    accepted by design_cards. Claim IDs are grounding context, never signing
    authority. A separate immutable research snapshot keeps every gate current
    while the run-local sheet receives the completed checker records.
    """
    from . import design_cards as dc
    from . import trace_check
    from .atomic_files import write_json
    from .fact_evidence import stamp_checked_review
    from .llm import watch_served
    if why := record_signing.key_problem():
        raise ValueError("Chapter verification cannot create usable signed records: " + why)
    sheet = json.loads(Path(source).read_text(encoding="utf-8"))
    acts = payload.get("acts") or []
    identity = fingerprint({"facts": sheet.get("facts"), "acts": acts, "version": VERSION,
                            "rules": file_inputs([Path(__file__), Path(trace_check.__file__)])})
    if Path(destination).is_file():
        cached = json.loads(Path(destination).read_text(encoding="utf-8"))
        if cached.get("chapter_identity") == identity:
            try:
                for entry in cached.get("chapter_gates", []):
                    checked = entry["payload"]
                    dc.gated_text(dc.Gated("title", checked["fact_review"]["public_fingerprint"], checked),
                                  checked, cached)
                if len(cached.get("chapter_gates", [])) == len(acts):
                    return Path(destination)
            except (dc.CardRefused, KeyError, TypeError, ValueError):
                pass
    facts = {f["id"]: f for f in sheet.get("facts", [])}
    research = Path(destination).with_name("chapter-research-" + identity[:16] + ".json")
    write_json(research, {"topic": sheet.get("topic"), "facts": sheet.get("facts", [])})
    trace = trace or trace_check.check
    evidence = evidence or trace_check.evidence_check
    gates = []
    for number, act in enumerate(acts, 1):
        if act.get("act_number") != number:
            raise ValueError("Chapter acts must be sequential")
        title = str(act.get("act_title") or "")
        candidates = [fid for fid in act.get("claim_fact_ids", []) if fid in facts]
        if not title.strip() or not candidates:
            raise ValueError(f"Chapter {number} needs its complete title and cited facts")
        checked = {"title": title}
        with watch_served() as t_served:
            trace_result = trace(checked, sheet)
        if trace_result[0] is not True:
            raise ValueError(f"Chapter {number} heading failed trace verification: {trace_result[3]}")
        with watch_served() as e_served:
            evidence_result = evidence(checked, sheet)
        if evidence_result[0] != "pass":
            raise ValueError(f"Chapter {number} heading failed evidence verification: {evidence_result[3]}")
        checked["guardrail_passed"] = True
        served = {"trace_check": t_served, "evidence_check": e_served}
        models = {k: " + ".join(dict.fromkeys(served[k])) if served[k] else model
                  for k, model in trace_check.checker_models().items()}
        try:
            stamp_checked_review(checked, research, trace_result, evidence_result, models)
            dc.gated_text(dc.Gated("title", checked["fact_review"]["public_fingerprint"], checked), checked, sheet)
        except (ValueError, dc.CardRefused) as exc:
            raise ValueError(f"Chapter {number} heading failed its signed fact gate: {exc}") from exc
        gates.append({"number": number, "fact_ids": candidates, "payload": checked})
    sheet.update(chapter_identity=identity, chapter_gates=gates)
    write_json(Path(destination), sheet)
    return Path(destination)
