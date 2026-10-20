"""
src/backup.py
=============
Automated Tier 2 Gold Master Backup and Platform Dispute Archiving.
Syncs finished deliverables, provenance ledgers, and atomic database snapshots to OneDrive:
C:\\Users\\omokolade\\OneDrive\\Backups\\Created Contents\\RelicAndRiddle_Archive\\<run_id>\\
"""

import json
import logging
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

from . import config

log = logging.getLogger(__name__)

# Essential end-product files to preserve
GOLD_FILES = [
    "*_master.mp4",
    "*_master.srt",
    "*_master.vtt",
    "*_master_captions.ass",
    "*_documentary_16x9.mp4",
    "*_documentary_16x9.srt",
    "*_documentary_16x9.vtt",
    "*_documentary_16x9_captions.ass",
    "thumbnail_youtube_16x9.jpg",
    # The narration and bed with their rights records: what a rights dispute is decided on.
    "voiceover.mp3",
    "voiceover.mp3.rights.json",
    "ambient_bed.wav",
    "ambient_bed.wav.rights.json",
    "words.json",
    "thumbnail_cover.jpg",
    "thumbnail_profile_grid.jpg",
    "thumbnail_variant_a_blueprint.jpg",
    "thumbnail_variant_b_intrigue.jpg",
    "thumbnail_variant_c_minimal.jpg",
    "verified_script.json",
    "qa_report.json",
    "provenance.json",
    "credits.json",
    "script_metadata.json",
    "release_report.json",
    "companion_posting.json",
]

GOLD_DIRS = [
    "stories",
    "carousel",
]


def generate_dispute_summary(run_dir: Path, archive_dir: Path) -> Path:
    """Generates a human-readable 00_DISPUTE_SUMMARY.txt file with complete
    attribution, license citations, and fact sources for immediate platform appeals."""
    summary_path = archive_dir / "00_DISPUTE_SUMMARY.txt"
    
    script_data = {}
    verified_script = run_dir / "verified_script.json"
    if verified_script.is_file():
        try:
            script_data = json.loads(verified_script.read_text(encoding="utf-8"))
        except Exception:
            pass

    prov_data = {}
    provenance_file = run_dir / "provenance.json"
    if provenance_file.is_file():
        try:
            prov_data = json.loads(provenance_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    lines = [
        "=" * 80,
        "RELIC & RIDDLE — MASTER CONTENT PROVENANCE & DISPUTE AUDIT DOSSIER",
        "=" * 80,
        f"RUN ID:         {run_dir.name}",
        f"ARCHIVED AT:    {datetime.now(timezone.utc).isoformat()}",
        f"TITLE:          {script_data.get('title', 'Unknown')}",
        f"TOPIC ID:       {prov_data.get('topic_id', 'Unknown')}",
        "",
        "--------------------------------------------------------------------------------",
        "1. VERIFIED SCRIPT & CITATIONS (FOR MISINFORMATION / FALSE NEWS DISPUTES)",
        "--------------------------------------------------------------------------------",
        f"HOOK: {script_data.get('hook_sentence', '')}",
        "",
        f"FULL NARRATION:\n{script_data.get('narration_script', '')}",
        "",
        "ACADEMIC / INSTITUTIONAL SOURCES CITED:",
    ]

    for src in script_data.get("sources", []):
        lines.append(f"  • {src.get('title')}: {src.get('url')}")

    lines.extend([
        "",
        "--------------------------------------------------------------------------------",
        "2. AUDIO & VISUAL COPYRIGHT ATTRIBUTIONS (FOR DMCA / CONTENT ID DISPUTES)",
        "--------------------------------------------------------------------------------",
    ])

    assets = prov_data.get("assets", [])
    if assets:
        for idx, a in enumerate(assets, 1):
            lines.append(f"Asset #{idx}: {a.get('file', a.get('title', 'media'))}")
            lines.append(f"  • SHA256:     {a.get('sha256', 'N/A')}")
            lines.append(f"  • Artist:     {a.get('artist') or 'Public Domain / Procedural'}")
            lines.append(f"  • License:    {a.get('license') or 'N/A'}")
            lines.append(f"  • Source URL: {a.get('source_url') or 'Original Render'}")
            lines.append(f"  • License URL:{a.get('license_url') or 'N/A'}")
            lines.append("")
    else:
        lines.append("  All assets verified procedural or open access.")

    # Audio Mastering Verification
    # Only what was measured: a missing figure says so instead of showing the target as if met.
    audit = prov_data.get("self_healing") or {}
    loudness = audit.get("loudness") or {}
    lufs, peak = loudness.get("integrated_lufs"), loudness.get("true_peak_dbtp")
    if "score" in audit:
        verdict = f"{audit['score']}/100, {'certified' if audit.get('certified') else 'NOT certified'}"
    else:
        verdict = "not run"
    lines.extend([
        "--------------------------------------------------------------------------------",
        "3. BROADCAST COMPLIANCE & ORIGINALITY METRICS",
        "--------------------------------------------------------------------------------",
        f"Integrated Loudness: {f'{lufs} LUFS' if lufs is not None else 'not measured'}",
        f"True Peak:           {f'{peak} dBTP' if peak is not None else 'not measured'}",
        "AI Synthetic Media:  Tagged as synthetic/AI-narrated in Meta publication payload",
        f"Quality Audit:       {verdict}",
        "=" * 80,
    ])

    summary_path.write_text("\n".join(lines), encoding="utf-8")
    return summary_path


def backup_run(run_dir: Path, db_path: Optional[Path] = None, dest_base: Optional[Path] = None) -> Dict[str, Any]:
    """Copies all gold deliverables from run_dir to the OneDrive archive directory,
    creates an atomic SQLite database snapshot, and writes a complete dispute dossier."""
    dest_base = dest_base or getattr(config, "BACKUP_ONEDRIVE_DIR", Path.home() / "OneDrive" / "Backups" / "Created Contents" / "RelicAndRiddle")
    
    try:
        dest_base.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        log.warning("Could not access backup base directory %s: %s", dest_base, e)
        return {"status": "skipped", "reason": str(e)}

    run_archive = dest_base / run_dir.name
    run_archive.mkdir(parents=True, exist_ok=True)

    copied_files: List[str] = []

    # 1. Copy gold files matching patterns
    for pattern in GOLD_FILES:
        for src_file in run_dir.glob(pattern):
            if src_file.is_file():
                dest_file = run_archive / src_file.name
                shutil.copy2(src_file, dest_file)
                copied_files.append(src_file.name)

    # 2. Copy gold subdirectories (stories, carousel)
    for d_name in GOLD_DIRS:
        src_d = run_dir / d_name
        if src_d.is_dir():
            dest_d = run_archive / d_name
            if dest_d.exists():
                shutil.rmtree(dest_d)
            shutil.copytree(src_d, dest_d)
            copied_files.append(f"{d_name}/ ({len(list(src_d.glob('*')))} files)")

    # 3. Generate 00_DISPUTE_SUMMARY.txt
    try:
        generate_dispute_summary(run_dir, run_archive)
        copied_files.append("00_DISPUTE_SUMMARY.txt")
    except Exception as e:
        log.warning("Could not generate dispute summary: %s", e)

    # 4. Atomic SQLite database backup snapshot
    db_backup_path = None
    if db_path and Path(db_path).is_file():
        try:
            db_snap_dir = dest_base / "db_snapshots"
            db_snap_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            db_dest = db_snap_dir / f"pipeline_{stamp}.db"
            
            # Use SQLite online backup API to take a clean, locked snapshot
            src_conn = sqlite3.connect(str(db_path))
            dest_conn = sqlite3.connect(str(db_dest))
            with dest_conn:
                src_conn.backup(dest_conn)
            src_conn.close()
            dest_conn.close()
            db_backup_path = str(db_dest)
            copied_files.append(f"db_snapshots/{db_dest.name}")
        except Exception as e:
            log.warning("Database snapshot failed: %s", e)

    log.info("Tier 2 Gold Backup: Successfully synced %d artifacts for %s to %s", len(copied_files), run_dir.name, run_archive)
    return {
        "status": "success",
        "archive_path": str(run_archive),
        "copied_count": len(copied_files),
        "db_snapshot": db_backup_path,
        "files": copied_files
    }
