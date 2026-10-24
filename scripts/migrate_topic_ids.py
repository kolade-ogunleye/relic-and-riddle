"""
Moves every topic from its numbered id (01_derinkuyu_underground_city) to its semantic id
(derinkuyu_underground_city), keeping all production state.

The semantic asset folders already exist; the numbered ones are Windows junctions that point
at them, and each junction's target is that topic's semantic id. Topics without assets drop
the number prefix.

    python scripts/migrate_topic_ids.py            # dry run: prints the plan, changes nothing
    python scripts/migrate_topic_ids.py --apply    # backs up, then migrates

Nothing is deleted. The database and the data files are backed up, and where both ids have
a copy of a file, the copy that loses is moved to archive/topic_id_migration/<stamp>/.
Rules: the newer fact sheet wins (by built_at); identical curated scripts keep one copy.
Runs, approvals and publications are renamed in one transaction, so topic statuses,
publish counts and history are preserved. Order: file moves, then the database and topic
bank together, then the compatibility links. The id map is saved in the archive folder.
After a completed run, running again reports nothing to migrate.
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import ASSETS_DIR, CURATED_SCRIPTS_DIR, DATA_DIR, TOPICS_DIR  # noqa: E402
from src.db import DB_PATH  # noqa: E402

FACT_SHEETS = DATA_DIR / "fact_sheets"
KEYED_FILES = [TOPICS_DIR / "image_queries.json", TOPICS_DIR / "topic_corrections.json"]


def junction_target(path: Path):
    """The folder a Windows junction (or symlink) points at, else None. (realpath resolves
    junctions; Path.is_junction needs Python 3.12.)"""
    if not path.exists():
        return None
    real = Path(os.path.realpath(path))
    return real if os.path.normcase(str(real)) != os.path.normcase(str(path.resolve(strict=False) if path.is_symlink() else path.absolute())) else None


def strip_number(topic_id: str) -> str:
    return topic_id[3:] if len(topic_id) > 3 and topic_id[:2].isdigit() and topic_id[2] == "_" else topic_id


def read_json(path: Path):
    raw = path.read_bytes().decode("utf-8")
    return json.loads(raw), ("\r\n" if "\r\n" in raw else "\n"), raw.endswith(("\n", "\r\n"))


def write_json(path: Path, data, newline: str, trailing: bool) -> None:
    text = json.dumps(data, indent=2, ensure_ascii=False) + ("\n" if trailing else "")
    path.write_bytes(text.replace("\n", newline).encode("utf-8"))


def id_map(bank_topics):
    mapping = {}
    for t in bank_topics:
        old = t["id"]
        target = junction_target(ASSETS_DIR / old)
        new = target.name if target is not None else strip_number(old)
        if new != old:
            mapping[old] = new
    news = list(mapping.values())
    duplicates = {n for n in news if news.count(n) > 1}
    clashes = {n for n in news if n in {t["id"] for t in bank_topics}}
    if duplicates or clashes:
        raise SystemExit(f"Refusing: id collisions {sorted(duplicates | clashes)}")
    return mapping


def plan(mapping):
    """[(description, action)] in apply order; each action is a zero-argument callable."""
    steps = []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = ROOT / "archive" / "topic_id_migration" / stamp

    def archive_file(path: Path):
        dest = archive / path.relative_to(ROOT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest))

    link_steps = []

    def backup():
        archive.mkdir(parents=True, exist_ok=True)
        (archive / "id_map.json").write_text(json.dumps(mapping, indent=2), encoding="utf-8")
        with sqlite3.connect(str(DB_PATH)) as src, sqlite3.connect(str(archive / "pipeline.db")) as dst:
            src.backup(dst)
        for p in [TOPICS_DIR / "topic_bank.json", *KEYED_FILES]:
            if p.exists():
                shutil.copy2(p, archive / p.name)
    steps.append((f"back up pipeline.db and topic data to {archive.relative_to(ROOT)}", backup))

    for old, new in mapping.items():
        old_sheet, new_sheet = FACT_SHEETS / f"{old}.json", FACT_SHEETS / f"{new}.json"
        if old_sheet.exists():
            def move_sheet(old_sheet=old_sheet, new_sheet=new_sheet, new=new):
                if new_sheet.exists():
                    keep_old = json.loads(old_sheet.read_text(encoding="utf-8"))["built_at"] > \
                        json.loads(new_sheet.read_text(encoding="utf-8"))["built_at"]
                    archive_file(new_sheet if keep_old else old_sheet)
                    if not keep_old:
                        return
                data, nl, trailing = read_json(old_sheet)
                data["topic_id"] = new
                write_json(new_sheet, data, nl, trailing)
                archive_file(old_sheet)
            steps.append((f"fact sheet {old}.json -> {new}.json (newer copy wins; other archived)", move_sheet))
        old_script, new_script = CURATED_SCRIPTS_DIR / f"{old}.json", CURATED_SCRIPTS_DIR / f"{new}.json"
        if old_script.exists():
            def move_script(old_script=old_script, new_script=new_script):
                if new_script.exists():
                    if old_script.read_bytes() != new_script.read_bytes():
                        raise SystemExit(f"Refusing: {old_script.name} and {new_script.name} differ; reconcile by hand")
                    archive_file(old_script)
                else:
                    shutil.move(str(old_script), str(new_script))
            steps.append((f"curated script {old}.json -> {new}.json", move_script))
        link = ASSETS_DIR / old
        if junction_target(link) is not None:
            link_steps.append((f"remove compatibility link assets/{old} -> assets/{new} (the link only)",
                               lambda link=link: os.rmdir(link)))  # rmdir on a junction removes the link, not the target
        elif link.is_dir():
            raise SystemExit(f"Refusing: assets/{old} is a real folder, not a link; move it by hand")

    def rename_in_database():
        conn = sqlite3.connect(str(DB_PATH))
        try:
            conn.execute("PRAGMA foreign_keys = OFF")  # parent and child ids change in one transaction
            with conn:
                for old, new in mapping.items():
                    conn.execute("UPDATE topics SET id = ? WHERE id = ?", (new, old))
                    for table in ("runs", "approvals", "publications"):
                        conn.execute(f"UPDATE {table} SET topic_id = ? WHERE topic_id = ?", (new, old))
                problems = conn.execute("PRAGMA foreign_key_check").fetchall()
                if problems:
                    raise SystemExit(f"Refusing: foreign key problems after rename: {problems[:5]}")
        finally:
            conn.close()
    steps.append(("rename topic ids in topics, runs, approvals and publications (one transaction)", rename_in_database))

    def rename_in_files():
        bank_path = TOPICS_DIR / "topic_bank.json"
        bank, nl, trailing = read_json(bank_path)
        for t in bank["topics"]:
            t["id"] = mapping.get(t["id"], t["id"])
        write_json(bank_path, bank, nl, trailing)
        for path in KEYED_FILES:
            if path.exists():
                data, nl, trailing = read_json(path)
                write_json(path, {mapping.get(k, k): v for k, v in data.items()}, nl, trailing)
    steps.append(("rename ids in topic_bank.json, image_queries.json and topic_corrections.json", rename_in_files))
    return steps + link_steps


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="perform the migration (default: dry run)")
    args = parser.parse_args()
    bank = json.loads((TOPICS_DIR / "topic_bank.json").read_text(encoding="utf-8"))["topics"]
    mapping = id_map(bank)
    if not mapping:
        print("Nothing to migrate: every topic already has its semantic id.")
        return 0
    print(f"{len(mapping)} topics to migrate:")
    for old, new in mapping.items():
        print(f"  {old} -> {new}")
    steps = plan(mapping)
    print("\nSteps:")
    for i, (description, _) in enumerate(steps, 1):
        print(f"  {i:2}. {description}")
    if not args.apply:
        print("\nDry run: nothing changed. Re-run with --apply (stop the bot and daemon first).")
        return 0
    for i, (description, action) in enumerate(steps, 1):
        action()
        print(f"  done {i:2}. {description}")
    print("\nMigrated. Restart the bot/daemon so they load the new ids.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
