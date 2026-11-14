"""
Regenerate and inspect the Relic & Riddle SQLite topic queue.
"""

import json
import sys
from pathlib import Path

if "--i-know-this-is-archived" not in sys.argv:
    sys.exit("scripts/archive/regenerate_queue.py is an archived one-off and is not maintained.\n"
             "If run, it deletes and rewrites topic rows in the database.\n"
             "Refusing; see scripts/README.md, or pass --i-know-this-is-archived.")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.db import PipelineDatabase, calculate_vps


def main():
    db = PipelineDatabase()
    with open(db.topic_bank_file, "r", encoding="utf-8") as f:
        new_topics = json.load(f).get("topics", [])
    new_ids = {t["id"] for t in new_topics}

    with db._conn() as conn:
        # Delete old topics that are NOT in the new bank and not referenced by foreign keys
        referenced = {r[0] for r in conn.execute("SELECT DISTINCT topic_id FROM runs").fetchall()}
        all_db_ids = {r[0] for r in conn.execute("SELECT id FROM topics").fetchall()}
        to_delete = (all_db_ids - new_ids) - referenced
        for tid in to_delete:
            conn.execute("DELETE FROM topics WHERE id = ?", (tid,))
        # Reset queued status for any un-run topics
        conn.execute("UPDATE topics SET status = 'queued' WHERE id NOT IN (SELECT DISTINCT topic_id FROM runs WHERE status = 'published')")

    # Re-seed from data/topics/topic_bank.json
    db._seed_topics_from_json()

    with db._conn() as conn:
        rows = conn.execute(
            "SELECT id, title, sub_theme, hook, key_facts_json, location_json, status FROM topics ORDER BY rowid"
        ).fetchall()

    print(f"\n{'='*80}")
    print(f"REGENERATED TOPIC QUEUE ({len(rows)} TOPICS)")
    print(f"{'='*80}\n")

    theme_counts = {}
    for i, r in enumerate(rows, 1):
        t = dict(r)
        t["key_facts"] = json.loads(r["key_facts_json"])
        vps = calculate_vps(t)
        loc = json.loads(r["location_json"])
        country = loc.get("country", "Unknown")
        theme = t["sub_theme"]
        theme_counts[theme] = theme_counts.get(theme, 0) + 1

        print(f"{i:02d}. [{t['id']}]")
        print(f"    Title:     {t['title']}")
        print(f"    Sub-Theme: {theme}")
        print(f"    Location:  {loc.get('name')}, {country} ({loc.get('latitude')}, {loc.get('longitude')})")
        print(f"    VPS Score: {vps:.1f}/100 | Facts: {len(t['key_facts'])} | Status: {t['status']}")
        print(f"    Hook:      \"{t['hook']}\"")
        print()

    print(f"{'='*80}")
    print("SUB-THEME DISTRIBUTION:")
    for theme, count in theme_counts.items():
        print(f"  • {theme}: {count} topics")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    main()
