import json
import shutil
import sqlite3
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import DATA_DIR, TOPICS_DIR, ASSETS_DIR, OUTPUT_DIR
from src.db import DB_PATH

def strip_prefix(topic_id: str) -> str:
    if len(topic_id) > 3 and topic_id[:2].isdigit() and topic_id[2] == "_":
        return topic_id[3:]
    return topic_id

# 1. Archive existing output runs
archive_dir = OUTPUT_DIR / "_archive_preprod"
archive_dir.mkdir(parents=True, exist_ok=True)
run_folders = [d for d in OUTPUT_DIR.iterdir() if d.is_dir() and d.name.startswith("run_") and d.name != "_archive_preprod"]
print(f"Archiving {len(run_folders)} pre-prod test run folders to {archive_dir.name}...")
for d in run_folders:
    dest = archive_dir / d.name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.move(d, dest)
print("Output runs successfully archived.")

# 2. Update data/topics/topic_bank.json
bank_path = TOPICS_DIR / "topic_bank.json"
bank = json.loads(bank_path.read_text(encoding="utf-8"))
old_to_new = {}
for t in bank["topics"]:
    old_id = t["id"]
    new_id = strip_prefix(old_id)
    t["id"] = new_id
    old_to_new[old_id] = new_id

bank_path.write_text(json.dumps(bank, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"Updated {len(bank['topics'])} topic IDs in topic_bank.json.")

# 3. Update data/topics/image_queries.json
queries_path = TOPICS_DIR / "image_queries.json"
if queries_path.exists():
    queries = json.loads(queries_path.read_text(encoding="utf-8"))
    new_queries = {}
    for k, v in queries.items():
        new_k = strip_prefix(k)
        new_queries[new_k] = v
    queries_path.write_text(json.dumps(new_queries, indent=2, ensure_ascii=False), encoding="utf-8")
    print("Updated image_queries.json keys to semantic slugs.")

# 4. Migrate assets folders
print("Migrating asset folders in assets/...")
for folder in ASSETS_DIR.iterdir():
    if folder.is_dir() and folder.name != "luts":
        new_name = strip_prefix(folder.name)
        if new_name != folder.name:
            target = ASSETS_DIR / new_name
            if target.exists():
                # Merge contents if target already exists
                for item in folder.iterdir():
                    dest_file = target / item.name
                    if not dest_file.exists():
                        shutil.move(item, dest_file)
                shutil.rmtree(folder)
            else:
                shutil.move(folder, target)
print("Asset folders successfully migrated.")

# 5. Migrate fact sheets
fact_dir = DATA_DIR / "fact_sheets"
if fact_dir.exists():
    print("Migrating fact sheets in data/fact_sheets/...")
    for f in fact_dir.glob("*.json"):
        new_name = strip_prefix(f.stem) + ".json"
        if new_name != f.name:
            target = fact_dir / new_name
            if target.exists():
                f.unlink()
            else:
                shutil.move(f, target)
    print("Fact sheets successfully migrated.")

# 6. Purge Database and seed fresh topics in 4-pillar order
print("Purging database and resetting sequences in pipeline.db...")
conn = sqlite3.connect(str(DB_PATH))
conn.execute("PRAGMA foreign_keys = OFF")

# Drop and recreate tables to ensure clean serial sequence
conn.execute("DELETE FROM runs")
conn.execute("DELETE FROM approvals")
conn.execute("DELETE FROM alerts")
conn.execute("DELETE FROM issues")
conn.execute("DELETE FROM scheduled_posts")
conn.execute("DELETE FROM topics")
try:
    conn.execute("DELETE FROM sqlite_sequence")
except sqlite3.OperationalError:
    pass  # table might not exist if autoincrement hasn't been used yet

now = "2026-10-18T20:00:00+00:00"
for t in bank["topics"]:
    conn.execute("""
    INSERT INTO topics (
        id, title, sub_theme, hook, location_json, key_facts_json,
        hashtags_json, meta_categories_json, status, times_published,
        last_run_id, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, NULL, ?)
    """, (
        t["id"],
        t.get("title", ""),
        t.get("sub_theme", ""),
        t.get("hook", ""),
        json.dumps(t.get("location", {})),
        json.dumps(t.get("key_facts", [])),
        json.dumps(t.get("hashtags", [])),
        json.dumps(t.get("meta_categories", [])),
        now,
    ))

conn.commit()
conn.close()
print("Database purged and re-seeded with 33 queued topics in pure 4-pillar priority order.")
