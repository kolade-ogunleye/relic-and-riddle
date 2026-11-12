"""QF-110 measure: what blocks an approved booking at its slot, before and after.

    python docs/reviews/quality-fixes-2026-11/QF-110/measure.py <repo root> [--runs DIR ...] [--db COPY.db]

Prints JSON, writes nothing. Without options it measures the synthetic scenarios only (built in a temp folder from
the tests' recipe). --runs: run folders to measure (a checkout's output/, or a COPY of the production sample's
output/: paths recorded on another machine are re-pointed at the sample's own output/ and assets/, and a file the
sample does not contain is counted apart). --db: a read-only COPY of a pipeline database, for the approval binding.
Counts only. Runs on the base too (a function the base lacks is reported as "absent")."""
import argparse
import json
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path


def _setup(root: Path):
    sys.path.insert(0, str(root))
    from src import config
    config.FACT_RECORD_KEY = "measure-only-key-0123456789abcdef0123456789"   # synthetic records only


def synthetic() -> dict:
    """The production failure modes, built as the tests build them: does each still block or now post?"""
    from src import release, video_qa
    from src.credits import generate_provenance_ledger
    from src.lineage import record_derived
    out = {}
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        photo = t / "assets" / "photo.jpg"
        photo.parent.mkdir()
        photo.write_bytes(b"licensed photo")
        run = t / "run_y"
        (run / "parallax").mkdir(parents=True)
        clip = run / "parallax" / "scene_01.mp4"
        clip.write_bytes(b"clip")
        record_derived(clip, [photo], "parallax")
        master = run / "m.mp4"
        master.write_bytes(b"master")
        ledger = generate_provenance_ledger(run, {"id": "t"}, {}, master, media_items=[clip])
        shutil.rmtree(run / "parallax")
        relaxed = getattr(video_qa, "provenance_problems", None)
        out["missing_parallax_verified_master"] = (
            "posts" if relaxed and not relaxed(ledger)["problems"] else
            "blocks" if not video_qa.verify_provenance_integrity(ledger) else "posts")
        master.write_bytes(b"altered")
        out["altered_master"] = ("blocks" if (relaxed(ledger)["problems"] if relaxed else
                                             not video_qa.verify_provenance_integrity(ledger)) else "posts")
        master.write_bytes(b"master")
        photo.write_bytes(b"swapped")
        out["changed_source_photo"] = ("blocks" if (relaxed(ledger)["problems"] if relaxed else
                                                   not video_qa.verify_provenance_integrity(ledger)) else "posts")
    out["recheck_after_code_update"] = "present" if hasattr(__import__("src.fact_evidence").fact_evidence,
                                                             "run_recheck") else "absent"
    out["post_due_isolates_bookings"] = "present" if hasattr(__import__("main").PipelineCoordinator,
                                                              "_post_one_due") else "absent"
    out["cleanup_protects_pending_runs"] = "present" if Path(release.__file__).with_name("retention.py").is_file() \
        else "absent"
    return out


NOT_IN_SAMPLE = "__not_in_sample__"


def _where(raw: str, run: Path, runs_dir: Path, sample: bool) -> tuple:
    """(the file to read, its place: "run/<folder>", "run/<root>", "assets", "data", "fonts", "tools", "cache")."""
    p = Path(raw)
    if not re.match(r"^([A-Za-z]:|[\\/])", raw):
        p, raw = run / raw, str(run / raw)
    parts = re.split(r"[\\/]", raw)
    if "output" in parts:
        i = len(parts) - 1 - parts[::-1].index("output")
        rest = parts[i + 1:]
        place = "run/" + rest[1] if len(rest) > 2 else "run/<root>"
        return (runs_dir.joinpath(*rest) if sample else p), place
    place = next((x for x in ("assets", "data", "fonts", "tools", "cache") if x in parts), "other")
    if sample:
        if place == "assets":
            i = len(parts) - 1 - parts[::-1].index("assets")
            return runs_dir.parent.joinpath("assets", *parts[i + 1:]), place
        return Path(NOT_IN_SAMPLE) / parts[-1], place
    return p, place


KIND = {"master_video": "master", "composition_manifest": "master", "voiceover": "voice_or_music",
        "ambient_bed": "voice_or_music", "license_evidence": "rights_records", "resource_evidence": "fonts",
        "factual_evidence": "fact_files"}


def real_runs(runs_dir: Path) -> dict:
    """Per run folder: what the release gate's provenance and facts checks find, counted (old rule and new)."""
    from src import fact_evidence as fe
    from src import video_qa
    from src.credits import sha256_file
    sample = not (runs_dir.parent / "src").is_dir()      # a checkout has src/ beside output/
    intermediate = tuple(getattr(video_qa, "INTERMEDIATE_DIRS", ("parallax", "motion")))
    valid = {fe.review_rules_fingerprint(), "79415fa9c90d9a5027f19d13e77f998cf14dfdf848dc59042819e993438c455d"}
    c = {"runs": 0, "with_provenance": 0, "lack_parallax_dir": 0, "intermediate_files_missing": 0,
         "provenance_old_rule_pass": 0, "provenance_new_rule_pass": 0, "provenance_new_rule_pass_if_outside_verifies": 0,
         "runs_blocked_by_intermediates_alone": 0, "true_blocks": {}, "runs_with_true_block": 0,
         "runs_missing_files_outside_sample": 0,
         "fact_stamp": 0, "stamp_signed": 0, "stamp_made_before_signing": 0, "drift_rules": 0, "drift_public": 0,
         "drift_any": 0, "sheet_unchanged": 0, "sheet_changed": 0, "sheet_not_in_sample": 0,
         "drift_alone_with_sheet_unchanged": 0, "gate_provenance_pass": None, "gate_problems": {}}
    gate = getattr(video_qa, "provenance_problems", None)
    if gate and not sample:     # the release gate's own provenance check (the sample holds no fonts, data or tools)
        c["gate_provenance_pass"] = 0
    for run in sorted(runs_dir.glob("run_*")):
        if not run.is_dir():
            continue
        c["runs"] += 1
        c["lack_parallax_dir"] += not (run / "parallax").is_dir()
        meta = run / "script_metadata.json"
        if meta.is_file():
            payload = json.loads(meta.read_text(encoding="utf-8"))
            review = payload.get("fact_review")
            if isinstance(review, dict) and review:
                c["fact_stamp"] += 1
                c["stamp_signed" if review.get("signature") else "stamp_made_before_signing"] += 1
                rules = review.get("review_rules_fingerprint") not in valid
                public = review.get("public_fingerprint") != fe.public_fingerprint(payload)
                c["drift_rules"] += rules
                c["drift_public"] += public
                c["drift_any"] += rules or public
                if review.get("signature"):
                    raw = str(review.get("sheet_path") or "")
                else:
                    raw = str(runs_dir.parent / "data" / "fact_sheets" / f"{payload.get('topic_id')}.json")
                sheet, _ = _where(raw, run, runs_dir, sample)
                if str(sheet).startswith(NOT_IN_SAMPLE) or (sample and not sheet.is_file()):
                    c["sheet_not_in_sample"] += 1
                elif review.get("sheet_sha256") in fe._digests(sheet):
                    c["sheet_unchanged"] += 1
                    c["drift_alone_with_sheet_unchanged"] += rules or public
                else:
                    c["sheet_changed"] += 1
        ledger = run / "provenance.json"
        if not ledger.is_file():
            continue
        c["with_provenance"] += 1
        if c["gate_provenance_pass"] is not None:
            found = gate(ledger)["problems"]
            c["gate_provenance_pass"] += not found
            for problem in found:
                key = re.sub(r" \(.*?\)", "", problem)
                c["gate_problems"][key] = c["gate_problems"].get(key, 0) + 1
        data = json.loads(ledger.read_text(encoding="utf-8"))
        groups = {g: [data[g]] for g in ("master_video", "voiceover", "ambient_bed", "composition_manifest")
                  if data.get(g)}
        groups.update({g: data.get(g, []) for g in ("assets", "license_evidence", "derived_sources",
                                                    "factual_evidence", "resource_evidence")})
        missing_inter, absent, blocks = 0, 0, set()
        for group, records in groups.items():
            for rec in records:
                path, place = _where(rec["path"], run, runs_dir, sample)
                inter = place.startswith("run/") and place[4:] in intermediate
                kind = KIND.get(group) or {"assets": "photos_and_sources", "data": "fact_files", "tools": "map_data",
                                           "cache": "prepared_photos", "fonts": "fonts"}.get(place, "run_files")
                if str(path).startswith(NOT_IN_SAMPLE) or (sample and place == "run/graphics"
                                                           and path.suffix == ".py" and not path.is_file()):
                    absent += not path.is_file()     # the sample holds content artefacts only (no code, data, fonts)
                    continue
                if not path.is_file():
                    if inter and group != "master_video":
                        missing_inter += 1
                    else:
                        blocks.add(kind + "_missing")
                elif sha256_file(path) != rec.get("sha256"):
                    blocks.add(kind + "_changed")
        if data.get("status") != "recorded" or data.get("issues"):
            blocks.add("record_incomplete")
        c["intermediate_files_missing"] += missing_inter
        c["provenance_old_rule_pass"] += not (blocks or missing_inter or absent)
        c["provenance_new_rule_pass"] += not (blocks or absent)
        c["provenance_new_rule_pass_if_outside_verifies"] += not blocks
        c["runs_blocked_by_intermediates_alone"] += bool(missing_inter) and not blocks
        c["runs_missing_files_outside_sample"] += bool(absent)
        c["runs_with_true_block"] += bool(blocks)
        for b in blocks:
            c["true_blocks"][b] = c["true_blocks"].get(b, 0) + 1
    c["true_blocks"] = dict(sorted(c["true_blocks"].items()))
    c["gate_problems"] = dict(sorted(c["gate_problems"].items()))
    c["sample"] = sample
    return c


def approval_binding(db_copy: Path, output_dir: Path) -> dict:
    """For approved runs: is the review fingerprint now the one on the approved card (the content binding)?"""
    from src.telegram_state import review_fingerprint
    conn = sqlite3.connect(f"file:{db_copy}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    out = {"approved": 0, "card_recorded": 0, "binding_holds": 0, "files_missing": 0}
    for a in conn.execute("SELECT run_id, telegram_message_id FROM approvals WHERE decision='approved'").fetchall():
        out["approved"] += 1
        row = conn.execute("SELECT value FROM telegram_transport WHERE namespace='review-card' AND key=?",
                           (f"{a['run_id']}:{a['telegram_message_id']}",)).fetchone()
        r = conn.execute("SELECT * FROM runs WHERE run_id=?", (a["run_id"],)).fetchone()
        if not row or not r:
            continue
        out["card_recorded"] += 1
        run = dict(r)
        run["script_payload"] = json.loads(run.pop("script_payload_json") or "{}")
        shim = type("Db", (), {"get_run": staticmethod(lambda _id, run=run: run)})
        try:
            now = review_fingerprint(shim, a["run_id"])
        except (OSError, ValueError):
            out["files_missing"] += 1
            continue
        out["binding_holds"] += json.loads(row[0]) == now
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--runs", type=Path, action="append", default=[])
    ap.add_argument("--db", type=Path)
    args = ap.parse_args()
    _setup(args.root.resolve())
    result = {"synthetic": synthetic(), "runs": {}}
    for n, d in enumerate(args.runs):
        result["runs"][f"set_{n + 1}"] = real_runs(d.resolve())
    if args.db:
        result["approval_binding"] = approval_binding(args.db.resolve(), args.runs[0] if args.runs else Path("."))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
