import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db import PipelineDatabase, calculate_vps
from src.config import TOPICS_DIR

bank_path = TOPICS_DIR / "topic_bank.json"
bank = json.loads(bank_path.read_text(encoding="utf-8"))
topics = bank["topics"]

print(f"Loaded {len(topics)} topics from {bank_path.name}")

db = PipelineDatabase()
with db._conn() as conn:
    existing_states = {
        row["id"]: {
            "status": row["status"],
            "times_published": row["times_published"],
            "last_run_id": row["last_run_id"],
            "updated_at": row["updated_at"],
        }
        for row in conn.execute("SELECT id, status, times_published, last_run_id, updated_at FROM topics")
    }

# Split topics into active queued vs others (failed/published/in_production)
active_pool = [t for t in topics if existing_states.get(t["id"], {}).get("status") == "queued"]
other_pool = [t for t in topics if existing_states.get(t["id"], {}).get("status") != "queued"]

def interleave_pool(pool_items, initial_last_theme=None):
    ordered = []
    remaining = list(pool_items)
    last_theme = initial_last_theme
    while remaining:
        candidates = [t for t in remaining if (t.get("sub_theme") or "").lower() != last_theme]
        sub_pool = candidates if candidates else remaining
        best = sorted(sub_pool, key=lambda t: (-calculate_vps(t), remaining.index(t)))[0]
        ordered.append(best)
        remaining.remove(best)
        last_theme = (best.get("sub_theme") or "").lower()
    return ordered, last_theme

# Order active queued topics first with alternating themes and highest VPS
ordered_active, last_t = interleave_pool(active_pool)
ordered_others, _ = interleave_pool(other_pool, initial_last_theme=last_t)

ordered_topics = ordered_active + ordered_others

# Update topic_bank.json
bank["topics"] = ordered_topics
bank_path.write_text(json.dumps(bank, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"Successfully wrote reordered topics to {bank_path.name}")

# Now re-sync the SQLite database table so that row insertion order matches
with db._conn() as conn:

    # 2. Re-create topics table with identical schema
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DELETE FROM topics")

    # 3. Insert topics in the new 4-pillar order, preserving their runtime state
    for t in ordered_topics:
        tid = t["id"]
        st = existing_states.get(tid, {})
        status = st.get("status", "queued")
        times_published = st.get("times_published", 0)
        last_run_id = st.get("last_run_id")
        updated_at = st.get("updated_at", "2026-10-18T00:00:00+00:00")

        conn.execute("""
        INSERT INTO topics (
            id, title, sub_theme, hook, location_json, key_facts_json,
            hashtags_json, meta_categories_json, status, times_published,
            last_run_id, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            tid,
            t.get("title", ""),
            t.get("sub_theme", ""),
            t.get("hook", ""),
            json.dumps(t.get("location", {})),
            json.dumps(t.get("key_facts", [])),
            json.dumps(t.get("hashtags", [])),
            json.dumps(t.get("meta_categories", [])),
            status,
            times_published,
            last_run_id,
            updated_at,
        ))

print("Successfully rebuilt SQLite topics table with 4-pillar insertion order.")

# Verification
next_topic = db.get_next_topic()
all_queued = [t for t in db.list_all_topics() if t["status"] == "queued"]

print("\nVerification:")
print(f"Top Queued Topic (#1 in queue): {all_queued[0]['id']} — {all_queued[0]['title']}")
print(f"Next Topic from Scheduler:      {next_topic['id']} — {next_topic['title']}")
print(f"Do they match perfectly?: {all_queued[0]['id'] == next_topic['id']}")

print("\nFirst 10 Topics in Database:")
for i, t in enumerate(db.list_all_topics()[:10], 1):
    vps = calculate_vps(t)
    print(f"{i:2d}. [{t['status']:<12}] {t['id']:35s} | VPS={vps:.1f} | {t['sub_theme']}")
