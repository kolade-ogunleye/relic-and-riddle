"""
src/db.py
=========
Atomic, thread-safe SQLite persistence layer operating in Write-Ahead Logging (WAL) mode.
Guarantees zero write-collisions, ACID compliance, and concurrency resilience between
the scheduled daily cron daemon and incoming Telegram approval callbacks.
Automatically maintains read-only JSON mirrors for human inspection.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

from .config import DATA_DIR, TOPICS_DIR

DB_PATH = DATA_DIR / "pipeline.db"


class PipelineDatabase:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _init_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._get_connection() as conn:
            # 1. Topics Table
            conn.execute("""
            CREATE TABLE IF NOT EXISTS topics (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                sub_theme TEXT,
                hook TEXT,
                location_json TEXT,
                key_facts_json TEXT,
                hashtags_json TEXT,
                meta_categories_json TEXT,
                status TEXT DEFAULT 'queued',
                times_published INTEGER DEFAULT 0,
                last_run_id TEXT,
                updated_at TEXT
            );
            """)

            # 2. Pipeline Runs Table
            conn.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                topic_id TEXT NOT NULL,
                status TEXT NOT NULL,
                script_payload_json TEXT,
                master_video_path TEXT,
                thumbnail_cover_path TEXT,
                thumbnail_grid_path TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (topic_id) REFERENCES topics(id)
            );
            """)

            # 3. HITL Approvals Table
            conn.execute("""
            CREATE TABLE IF NOT EXISTS approvals (
                run_id TEXT PRIMARY KEY,
                topic_id TEXT NOT NULL,
                telegram_message_id INTEGER,
                decision TEXT DEFAULT 'pending',
                decision_timestamp TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES runs(run_id)
            );
            """)

            # 4. Publications Log Table
            conn.execute("""
            CREATE TABLE IF NOT EXISTS publications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                status TEXT NOT NULL,
                response_payload_json TEXT,
                created_at TEXT NOT NULL
            );
            """)
            conn.commit()

        # Seed topics if empty
        self._seed_topics_from_json()

    def _seed_topics_from_json(self):
        json_file = TOPICS_DIR / "topic_bank.json"
        if not json_file.exists():
            return

        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as count FROM topics")
            count = cur.fetchone()["count"]
            if count == 0:
                print(f"[DB] Seeding topics table from {json_file}...")
                with open(json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    topics = data.get("topics", [])
                    now_iso = datetime.now(timezone.utc).isoformat()
                    for t in topics:
                        cur.execute("""
                        INSERT OR REPLACE INTO topics (
                            id, title, sub_theme, hook, location_json, key_facts_json,
                            hashtags_json, meta_categories_json, status, times_published, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?)
                        """, (
                            t["id"],
                            t.get("title", ""),
                            t.get("sub_theme", ""),
                            t.get("hook", ""),
                            json.dumps(t.get("location", {})),
                            json.dumps(t.get("key_facts", [])),
                            json.dumps(t.get("hashtags", [])),
                            json.dumps(t.get("meta_categories", [])),
                            now_iso
                        ))
                conn.commit()
                print(f"[DB] Successfully seeded {len(topics)} topics into SQLite WAL.")

    def get_next_topic(self) -> Dict[str, Any]:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM topics WHERE status = 'queued' ORDER BY rowid ASC LIMIT 1")
            row = cur.fetchone()
            if not row:
                # Cycle if all completed
                cur.execute("SELECT * FROM topics ORDER BY times_published ASC, updated_at ASC LIMIT 1")
                row = cur.fetchone()

            if not row:
                raise RuntimeError("No topics available in database.")

            return {
                "id": row["id"],
                "title": row["title"],
                "sub_theme": row["sub_theme"],
                "hook": row["hook"],
                "location": json.loads(row["location_json"] or "{}"),
                "key_facts": json.loads(row["key_facts_json"] or "[]"),
                "hashtags": json.loads(row["hashtags_json"] or "[]"),
                "meta_categories": json.loads(row["meta_categories_json"] or "[]"),
                "status": row["status"],
                "times_published": row["times_published"],
            }

    def get_topic_by_id(self, topic_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM topics WHERE id = ?", (topic_id,))
            row = cur.fetchone()
            if not row:
                return None
            return {
                "id": row["id"],
                "title": row["title"],
                "sub_theme": row["sub_theme"],
                "hook": row["hook"],
                "location": json.loads(row["location_json"] or "{}"),
                "key_facts": json.loads(row["key_facts_json"] or "[]"),
                "hashtags": json.loads(row["hashtags_json"] or "[]"),
                "meta_categories": json.loads(row["meta_categories_json"] or "[]"),
                "status": row["status"],
                "times_published": row["times_published"],
            }

    def list_all_topics(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM topics ORDER BY rowid ASC")
            rows = cur.fetchall()
            return [
                {
                    "id": r["id"],
                    "title": r["title"],
                    "status": r["status"],
                    "times_published": r["times_published"],
                    "updated_at": r["updated_at"],
                }
                for r in rows
            ]

    def record_topic_status(self, topic_id: str, status: str, run_id: Optional[str] = None):
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
            UPDATE topics
            SET status = ?,
                last_run_id = COALESCE(?, last_run_id),
                times_published = CASE WHEN ? = 'published' THEN times_published + 1 ELSE times_published END,
                updated_at = ?
            WHERE id = ?
            """, (status, run_id, status, now_iso, topic_id))
            conn.commit()

        # Update JSON history mirror
        self._mirror_history_to_json()

    def record_run(
        self,
        run_id: str,
        topic_id: str,
        status: str,
        script_payload: Optional[Dict[str, Any]] = None,
        master_video_path: Optional[str] = None,
        thumbnail_cover_path: Optional[str] = None,
        thumbnail_grid_path: Optional[str] = None,
    ):
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
            INSERT OR REPLACE INTO runs (
                run_id, topic_id, status, script_payload_json, master_video_path,
                thumbnail_cover_path, thumbnail_grid_path, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM runs WHERE run_id = ?), ?), ?)
            """, (
                run_id,
                topic_id,
                status,
                json.dumps(script_payload or {}),
                master_video_path,
                thumbnail_cover_path,
                thumbnail_grid_path,
                run_id,
                now_iso,
                now_iso,
            ))
            conn.commit()

    def record_approval(self, run_id: str, topic_id: str, message_id: Optional[int] = None):
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
            INSERT OR REPLACE INTO approvals (
                run_id, topic_id, telegram_message_id, decision, created_at
            ) VALUES (?, ?, ?, 'pending', COALESCE((SELECT created_at FROM approvals WHERE run_id = ?), ?))
            """, (run_id, topic_id, message_id, run_id, now_iso))
            conn.commit()

    def update_approval_decision(self, run_id: str, decision: str):
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
            UPDATE approvals
            SET decision = ?, decision_timestamp = ?
            WHERE run_id = ?
            """, (decision, now_iso, run_id))
            conn.commit()

    def get_approval(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM approvals WHERE run_id = ?", (run_id,))
            row = cur.fetchone()
            if not row:
                return None
            return {
                "run_id": row["run_id"],
                "topic_id": row["topic_id"],
                "telegram_message_id": row["telegram_message_id"],
                "decision": row["decision"],
                "decision_timestamp": row["decision_timestamp"],
                "created_at": row["created_at"],
            }

    def list_pending_approvals(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
            SELECT a.*, r.script_payload_json
            FROM approvals a
            LEFT JOIN runs r ON a.run_id = r.run_id
            WHERE a.decision = 'pending'
            ORDER BY a.rowid DESC
            """)
            rows = cur.fetchall()
            return [
                {
                    "run_id": r["run_id"],
                    "topic_id": r["topic_id"],
                    "created_at": r["created_at"],
                    "script_payload": json.loads(r["script_payload_json"] or "{}"),
                }
                for r in rows
            ]

    def record_publication(self, topic_id: str, run_id: str, channel: str, status: str, response_payload: Dict[str, Any]):
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
            INSERT INTO publications (topic_id, run_id, channel, status, response_payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """, (topic_id, run_id, channel, status, json.dumps(response_payload), now_iso))
            conn.commit()

    def _mirror_history_to_json(self):
        """Maintains human-readable read-only JSON mirrors in data/topics/history.json"""
        try:
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT id, title, status, times_published, last_run_id, updated_at FROM topics")
                rows = cur.fetchall()
                history_list = [
                    {
                        "topic_id": r["id"],
                        "title": r["title"],
                        "status": r["status"],
                        "times_published": r["times_published"],
                        "last_run_id": r["last_run_id"],
                        "updated_at": r["updated_at"],
                    }
                    for r in rows
                ]
            history_file = TOPICS_DIR / "history.json"
            with open(history_file, "w", encoding="utf-8") as f:
                json.dump({"history": history_list}, f, indent=2)
        except Exception:
            pass


if __name__ == "__main__":
    db = PipelineDatabase()
    next_topic = db.get_next_topic()
    print(f"SQLite WAL Active. Next topic: {next_topic['title']} ({next_topic['id']})")
