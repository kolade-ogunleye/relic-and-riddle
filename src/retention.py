"""
src/retention.py
================
What may be freed from disk, and the supported clean-up command (QF-110, with QF-100's prepared-image caches).

A run's intermediate files (its scene clips in parallax/ and motion/, QA frames, stage cache, audit frames and loose
scratch) are freed only for a run that is finished: published, rejected or archived, or no longer known to the
pipeline (a folder without a run record cannot be reviewed, booked or posted). A run with a live booking (pending,
waiting, blocked, or one whose outcome is unknown), an open review, or any other status (being made, failed and
resumable, a dry run, scheduled) is never touched by any clean-up. Masters, narration, music bed, captions, covers,
Stories, carousel slides, provenance and every rights record are never freed, for any run: they are what the release
gate checks and what a dispute needs.

Production lost the scene clips of 49 of 51 runs, a booked one among them, to the pre-run purge, which removed them
from every run folder older than 7 days whatever its state, without saying which (2026-11-09).

Prepared-image caches (cache/upscaled, privacy, text_crop, colour_balance) are freed only by the command, never during
a run: an entry no run folder's provenance or lineage names, unchanged for CACHE_RETENTION_DAYS.

Every removal is logged by run and written to <output>/retention_log.jsonl.

    python -m src.retention            # lists what would be freed (nothing is removed)
    python -m src.retention --apply    # frees it; add --caches for the prepared-image caches too
"""

import json
import logging
import shutil
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

FREEABLE_STATUSES = ("published", "rejected", "archived")
LIVE_BOOKINGS = ("pending", "waiting", "blocked", "unknown")
# An explicit allowlist (QF-110 review 1): these folders, and loose files whose name ENDS in ".tmp". Nothing is
# matched by a name pattern: the old pre-run purge's "*temp*" matched every file of a topic named "...temple..."
# (kailasa_temple_monolith: its master, composition record and captions were deleted).
SCRATCH_SUFFIX = ".tmp"
SCRATCH_DIRS = ("motion", "parallax", "qa_frames", "stage_cache", "audit")
# Never freed, whatever their name (checked last, so a future allowlist entry cannot reach them).
KEPT_SUFFIXES = (".mp4", ".composition.json", ".srt", ".vtt", ".ass", ".json", ".jsonl", ".mp3", ".wav", ".jpg",
                 ".png")
CACHE_DIRS = ("upscaled", "privacy", "text_crop", "colour_balance")
LOG_NAME = "retention_log.jsonl"


def protected_runs(db) -> set[str]:
    """The runs no clean-up may touch, from the pipeline's records: every run whose status is not finished, every run
    with a live booking and every run with an open review. None (no database): nothing is known to be pending."""
    if db is None:
        return set()
    with db._conn() as conn:
        protected = {r[0] for r in conn.execute("SELECT run_id, status FROM runs").fetchall()
                     if r[1] not in FREEABLE_STATUSES}
        protected |= {r[0] for r in conn.execute("SELECT run_id FROM approvals WHERE decision = 'pending'").fetchall()}
    protected |= {p["run_id"] for p in db.list_scheduled_posts(LIVE_BOOKINGS)}
    return protected


def _scratch(run_dir: Path) -> Iterable[Path]:
    """The run folder's intermediates, by explicit name: the SCRATCH_DIRS folders, and root files ending ".tmp"."""
    for f in sorted(run_dir.iterdir()):
        if f.is_file() and f.name.endswith(SCRATCH_SUFFIX) and not f.name[:-len(SCRATCH_SUFFIX)].endswith(KEPT_SUFFIXES):
            yield f
    for name in SCRATCH_DIRS:
        d = run_dir / name
        if d.is_dir() and not d.is_symlink():
            yield d


def _size(path: Path) -> tuple:
    if path.is_file():
        return 1, path.stat().st_size
    files = [f for f in path.rglob("*") if f.is_file()]
    return len(files), sum(f.stat().st_size for f in files)


def _log(output_dir: Path, entry: dict[str, Any]) -> None:
    try:
        with (Path(output_dir) / LOG_NAME).open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        log.warning("Retention log not written: %s", e)


def free_run_intermediates(output_dir: Path, db=None, retention_days: float = 7, apply: bool = True) -> dict[str, Any]:
    """Frees the intermediate files of finished runs whose folder is older than `retention_days`; a protected run
    (protected_runs) is skipped whatever its age. Returns counts and, per run, what was (or would be) removed."""
    output_dir = Path(output_dir)
    protected = protected_runs(db)
    cutoff = time.time() - retention_days * 86400
    runs, files, size, kept = [], 0, 0, 0
    for run_dir in sorted(output_dir.iterdir()) if output_dir.is_dir() else []:
        if not (run_dir.is_dir() and run_dir.name.startswith("run_")):
            continue
        if run_dir.name in protected:
            kept += 1
            continue
        if run_dir.stat().st_mtime >= cutoff:
            continue
        removed, run_files, run_bytes = [], 0, 0
        for target in list(_scratch(run_dir)):
            try:
                n, b = _size(target)
                folder = target.is_dir()
                if apply:
                    shutil.rmtree(target) if folder else target.unlink()
                removed.append(target.name + ("/" if folder else ""))
                run_files, run_bytes = run_files + n, run_bytes + b
            except OSError as e:
                log.warning("Could not free %s: %s", target, e)
        if removed:
            runs.append({"run": run_dir.name, "removed": removed, "files": run_files, "bytes": run_bytes})
            files, size = files + run_files, size + run_bytes
            if apply:
                log.info("Freed %s of %s: %s (%d files, %.1f MB).", "intermediates", run_dir.name, ", ".join(removed),
                         run_files, run_bytes / 2 ** 20)
                _log(output_dir, {"at": datetime.now(UTC).isoformat(timespec="seconds"), "kind": "run",
                                  "run": run_dir.name, "removed": removed, "files": run_files, "bytes": run_bytes})
    mb = size / (1024 * 1024)
    log.info("%s %d intermediate files (%.2f MB) of %d finished run(s); %d pending run(s) left untouched.",
             "Purged" if apply else "Would free", files, mb, len(runs), kept)
    return {"purged_count": files, "mb_reclaimed": round(mb, 2), "runs": runs, "protected": kept, "applied": apply}


def _paths_in(value, found: set[str]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            if k in ("path", "file") and isinstance(v, str):
                found.add(v)
            _paths_in(v, found)
    elif isinstance(value, list):
        for v in value:
            _paths_in(v, found)


def referenced_files(output_dir: Path) -> set[str]:
    """Every file a run folder's provenance or lineage names (resolved, case-folded), the lineage records' own
    sources included: a cache entry named here belongs to a kept run and is never freed."""
    found: set[str] = set()
    for ledger in Path(output_dir).glob("run_*/provenance.json"):
        try:
            _paths_in(json.loads(ledger.read_text(encoding="utf-8")), found)
        except (OSError, ValueError):
            continue
    for record in [Path(p) for p in list(found) if p.endswith(".json")]:
        try:
            _paths_in(json.loads(record.read_text(encoding="utf-8")), found)
        except (OSError, ValueError):
            continue
    found |= {p + ".lineage.json" for p in list(found)}   # a kept entry keeps its own lineage record
    out = set()
    for p in found:
        try:
            out.add(str(Path(p).resolve()).casefold())
        except (OSError, ValueError):
            continue
    return out


def free_caches(cache_dir: Path, output_dir: Path, days: float, apply: bool = True) -> dict[str, Any]:
    """Frees prepared-image cache entries (CACHE_DIRS) that no run folder references and that have not changed for
    `days`. Returns counts per folder."""
    referenced = referenced_files(output_dir)
    cutoff = time.time() - days * 86400
    out: dict[str, Any] = {"applied": apply, "folders": {}}
    for name in CACHE_DIRS:
        folder = Path(cache_dir) / name
        n = size = kept = 0
        for f in sorted(folder.rglob("*")) if folder.is_dir() else []:
            if not f.is_file():
                continue
            if str(f.resolve()).casefold() in referenced or f.stat().st_mtime >= cutoff:
                kept += 1
                continue
            b = f.stat().st_size
            try:
                if apply:
                    f.unlink()
                n, size = n + 1, size + b
            except OSError as e:
                log.warning("Could not free %s: %s", f, e)
        out["folders"][name] = {"freed": n, "bytes": size, "kept": kept}
        if apply and n:
            log.info("Freed %d prepared-image cache entries from %s (%.1f MB).", n, name, size / 2 ** 20)
            _log(output_dir, {"at": datetime.now(UTC).isoformat(timespec="seconds"), "kind": "cache",
                              "folder": name, "files": n, "bytes": size})
    return out


def main(argv: list | None = None) -> int:
    import argparse

    from . import config
    from .db import PipelineDatabase
    parser = argparse.ArgumentParser(description="Free intermediate files of finished runs (never a pending one)")
    parser.add_argument("--apply", action="store_true", help="remove the files (default: only list them)")
    parser.add_argument("--days", type=float, default=7, help="only runs whose folder is older than this (days)")
    parser.add_argument("--caches", action="store_true", help="also prepared-image cache entries no run references")
    args = parser.parse_args(argv)
    db = PipelineDatabase()
    result = free_run_intermediates(config.OUTPUT_DIR, db, args.days, apply=args.apply)
    for run in result["runs"]:
        print(f"{run['run']}: {', '.join(run['removed'])} ({run['files']} files, {run['bytes'] / 2 ** 20:.1f} MB)")
    print(f"{'Freed' if args.apply else 'Would free'} {result['purged_count']} files ({result['mb_reclaimed']} MB) "
          f"of {len(result['runs'])} finished run(s); {result['protected']} pending run(s) untouched.")
    if args.caches:
        caches = free_caches(config.BASE_DIR / "cache", config.OUTPUT_DIR, config.CACHE_RETENTION_DAYS, apply=args.apply)
        for name, c in caches["folders"].items():
            print(f"cache/{name}: {'freed' if args.apply else 'would free'} {c['freed']} ({c['bytes'] / 2 ** 20:.1f} MB), "
                  f"kept {c['kept']}")
    if not args.apply:
        print("Nothing was removed. Run again with --apply to free these files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
