"""
src/db.py
=========
SQLite persistence layer (WAL mode) and the single source of truth for topics, runs,
approvals and publications. Every write is one short transaction, so the daily daemon
and CLI review commands can safely run at the same time.

Topic statuses: queued -> in_production -> published | failed | rejected
Run statuses:   producing -> pending_approval -> approved -> published | simulated
                                              +-> rejected | regenerate | timeout
                (any stage) -> failed;  publish outcome unknown -> publish_unknown
"""

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, Iterator, List, Optional

from .config import DATA_DIR, TOPICS_DIR

DB_PATH = DATA_DIR / "pipeline.db"
# A topic stuck "in_production" longer than this is assumed to be from a crashed run.
STALE_PRODUCTION_HOURS = 6

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PipelineDatabase:
    def __init__(self, db_path: Path = DB_PATH, topic_bank_file: Optional[Path] = None):
        self.db_path = db_path
        self.topic_bank_file = topic_bank_file or (TOPICS_DIR / "topic_bank.json")
        self._init_db()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        try:
            with conn:  # commits on success, rolls back on exception
                yield conn
        finally:
            conn.close()

    def _init_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript("""
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
            CREATE TABLE IF NOT EXISTS approvals (
                run_id TEXT PRIMARY KEY,
                topic_id TEXT NOT NULL,
                telegram_message_id INTEGER,
                decision TEXT DEFAULT 'pending',
                decision_timestamp TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES runs(run_id)
            );
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
            # Additive migrations for databases created by earlier versions.
            self._add_column(conn, "runs", "error", "TEXT")
            self._add_column(conn, "approvals", "decided_by", "TEXT")
            conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
            conn.execute("""
            CREATE TABLE IF NOT EXISTS llm_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model TEXT NOT NULL,
                input_tokens INTEGER, output_tokens INTEGER, web_searches INTEGER,
                cost_usd REAL NOT NULL,
                created_at TEXT NOT NULL
            )""")
        self._seed_topics_from_json()

    @staticmethod
    def _add_column(conn: sqlite3.Connection, table: str, column: str, decl: str):
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    def _seed_topics_from_json(self):
        """
        Syncs the topic bank into the database: new topics are added, and edits to existing
        topics' content (title, hook, facts, hashtags...) are applied. Production state
        (status, publish count, last run) is never touched.
        """
        if not self.topic_bank_file.exists():
            return
        with open(self.topic_bank_file, "r", encoding="utf-8") as f:
            topics = json.load(f).get("topics", [])
        now = _now()
        with self._conn() as conn:
            existing = {r["id"] for r in conn.execute("SELECT id FROM topics")}
            changed = 0
            for t in topics:
                cur = conn.execute("""
                INSERT INTO topics (
                    id, title, sub_theme, hook, location_json, key_facts_json,
                    hashtags_json, meta_categories_json, status, times_published, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title, sub_theme = excluded.sub_theme, hook = excluded.hook,
                    location_json = excluded.location_json, key_facts_json = excluded.key_facts_json,
                    hashtags_json = excluded.hashtags_json, meta_categories_json = excluded.meta_categories_json
                WHERE topics.title IS NOT excluded.title OR topics.hook IS NOT excluded.hook
                   OR topics.sub_theme IS NOT excluded.sub_theme OR topics.location_json IS NOT excluded.location_json
                   OR topics.key_facts_json IS NOT excluded.key_facts_json OR topics.hashtags_json IS NOT excluded.hashtags_json
                   OR topics.meta_categories_json IS NOT excluded.meta_categories_json
                """, (
                    t["id"], t.get("title", ""), t.get("sub_theme", ""), t.get("hook", ""),
                    json.dumps(t.get("location", {})), json.dumps(t.get("key_facts", [])),
                    json.dumps(t.get("hashtags", [])), json.dumps(t.get("meta_categories", [])),
                    now,
                ))
                changed += cur.rowcount
        added = len({t["id"] for t in topics} - existing)
        if changed:
            log.info("Topic bank sync: %d new, %d updated (%s).", added, changed - added, self.topic_bank_file.name)

    # ------------------------------------------------------------------ topics
    @staticmethod
    def _topic_from_row(row: sqlite3.Row) -> Dict[str, Any]:
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
            "last_run_id": row["last_run_id"],
            "updated_at": row["updated_at"],
        }

    def get_next_topic(self) -> Dict[str, Any]:
        """
        Next topic to produce: never-produced topics first, then topics whose last run
        failed (retry), then the least-published topic. Human-rejected topics are skipped
        until requeued; topics stuck in production for hours are treated as failed.
        """
        stale_before = (datetime.now(timezone.utc) - timedelta(hours=STALE_PRODUCTION_HOURS)).isoformat()
        with self._conn() as conn:
            row = conn.execute("""
            SELECT * FROM topics
            WHERE status IN ('queued', 'failed', 'published')
               OR (status = 'in_production' AND updated_at < ?)
            ORDER BY CASE status WHEN 'queued' THEN 0 WHEN 'published' THEN 2 ELSE 1 END,
                     times_published ASC, rowid ASC
            LIMIT 1
            """, (stale_before,)).fetchone()
        if not row:
            raise RuntimeError("No producible topics: all are rejected or currently in production.")
        return self._topic_from_row(row)

    def get_topic_by_id(self, topic_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM topics WHERE id = ?", (topic_id,)).fetchone()
        return self._topic_from_row(row) if row else None

    def list_all_topics(self) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM topics ORDER BY rowid ASC").fetchall()
        return [self._topic_from_row(r) for r in rows]

    def record_topic_status(self, topic_id: str, status: str, run_id: Optional[str] = None):
        with self._conn() as conn:
            cur = conn.execute("""
            UPDATE topics
            SET status = ?,
                last_run_id = COALESCE(?, last_run_id),
                times_published = CASE WHEN ? = 'published' THEN times_published + 1 ELSE times_published END,
                updated_at = ?
            WHERE id = ?
            """, (status, run_id, status, _now(), topic_id))
            if cur.rowcount == 0:
                raise KeyError(f"Unknown topic '{topic_id}'")
        self._mirror_history_to_json()

    def requeue_topic(self, topic_id: str):
        """Returns a topic to the queue without touching its publish count."""
        self.record_topic_status(topic_id, "queued")

    def repair_simulated_publications(self) -> List[str]:
        """
        Earlier versions counted dry-run/simulated publishes as real ones. Requeues every
        topic marked published that has no live publication on record.
        """
        with self._conn() as conn:
            rows = conn.execute("""
            SELECT t.id FROM topics t
            WHERE t.status = 'published' AND NOT EXISTS (
                SELECT 1 FROM publications p WHERE p.topic_id = t.id AND p.status = 'published'
            )
            """).fetchall()
            ids = [r["id"] for r in rows]
            for tid in ids:
                conn.execute(
                    "UPDATE topics SET status = 'queued', times_published = 0, updated_at = ? WHERE id = ?",
                    (_now(), tid),
                )
        if ids:
            self._mirror_history_to_json()
        return ids

    # -------------------------------------------------------------------- runs
    def create_run(self, run_id: str, topic_id: str):
        now = _now()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO runs (run_id, topic_id, status, created_at, updated_at) VALUES (?, ?, 'producing', ?, ?)",
                (run_id, topic_id, now, now),
            )

    def update_run(self, run_id: str, status: str, error: Optional[str] = None, **fields: Any):
        allowed = {"script_payload", "master_video_path", "thumbnail_cover_path", "thumbnail_grid_path"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unknown run fields: {unknown}")
        sets = ["status = ?", "updated_at = ?", "error = ?"]
        values: List[Any] = [status, _now(), error]
        for k, v in fields.items():
            col = "script_payload_json" if k == "script_payload" else k
            sets.append(f"{col} = ?")
            values.append(json.dumps(v) if k == "script_payload" else (str(v) if v is not None else None))
        values.append(run_id)
        with self._conn() as conn:
            cur = conn.execute(f"UPDATE runs SET {', '.join(sets)} WHERE run_id = ?", values)
            if cur.rowcount == 0:
                raise KeyError(f"Unknown run '{run_id}'")

    def run_key(self, run_id: str) -> str:
        """Short, stable key for a run (its row number), for Telegram buttons (64-byte limit)."""
        with self._conn() as conn:
            row = conn.execute("SELECT rowid FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return str(row["rowid"]) if row else run_id

    def resolve_run(self, ref: str) -> str:
        """Accepts a run key (row number) or a full run ID; returns the run ID."""
        if ref.isdigit():
            with self._conn() as conn:
                row = conn.execute("SELECT run_id FROM runs WHERE rowid = ?", (int(ref),)).fetchone()
            if row:
                return row["run_id"]
        return ref

    def run_label(self, run_id: str) -> str:
        """Human-readable name for a run: "Title" (topic) · date."""
        run = self.get_run(run_id)
        if not run:
            return run_id
        topic = self.get_topic_by_id(run["topic_id"]) or {}
        title = run["script_payload"].get("title") or topic.get("title") or run["topic_id"]
        place = (topic.get("location") or {}).get("name") or topic.get("title", "")
        when = ""
        try:
            when = datetime.fromisoformat(run["created_at"]).astimezone().strftime("%d %b %H:%M")
        except (TypeError, ValueError):
            pass
        label = f"“{title}”"
        if place and place.lower() not in title.lower():
            label += f" ({place})"
        return label + (f" · {when}" if when else "")

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            return None
        run = dict(row)
        run["script_payload"] = json.loads(run.pop("script_payload_json") or "{}")
        return run

    # --------------------------------------------------------------- approvals
    def record_approval(self, run_id: str, topic_id: str, message_id: Optional[int] = None):
        with self._conn() as conn:
            conn.execute("""
            INSERT INTO approvals (run_id, topic_id, telegram_message_id, decision, created_at)
            VALUES (?, ?, ?, 'pending', ?)
            ON CONFLICT(run_id) DO UPDATE SET telegram_message_id = excluded.telegram_message_id
            """, (run_id, topic_id, message_id, _now()))

    def update_approval_decision(self, run_id: str, decision: str, decided_by: Optional[str] = None) -> bool:
        """Records a decision only while the approval is still pending. Returns True if applied."""
        with self._conn() as conn:
            cur = conn.execute("""
            UPDATE approvals SET decision = ?, decision_timestamp = ?, decided_by = ?
            WHERE run_id = ? AND decision = 'pending'
            """, (decision, _now(), decided_by, run_id))
            return cur.rowcount == 1

    # ---------------------------------------------------------------- settings
    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        with self._conn() as conn:
            conn.execute("""
            INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """, (key, value, _now()))

    # ------------------------------------------------------------- llm usage
    def record_llm_usage(self, model: str, input_tokens: int, output_tokens: int, web_searches: int, cost_usd: float):
        with self._conn() as conn:
            conn.execute("""
            INSERT INTO llm_usage (model, input_tokens, output_tokens, web_searches, cost_usd, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """, (model, input_tokens, output_tokens, web_searches, cost_usd, _now()))

    def llm_spend(self, month: str) -> float:
        """Total estimated Claude spend (USD) for a month given as 'YYYY-MM' (UTC)."""
        with self._conn() as conn:
            row = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) AS s FROM llm_usage WHERE substr(created_at, 1, 7) = ?",
                               (month,)).fetchone()
        return float(row["s"])

    def list_recent_runs(self, limit: int = 5) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT run_id, topic_id, status, error, updated_at FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def reopen_approval(self, run_id: str) -> bool:
        """Puts a timed-out approval back to pending so the run can be reviewed again."""
        with self._conn() as conn:
            cur = conn.execute("""
            UPDATE approvals SET decision = 'pending', decision_timestamp = NULL, decided_by = NULL
            WHERE run_id = ? AND decision = 'timeout'
            """, (run_id,))
            return cur.rowcount == 1

    def get_approval(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM approvals WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def list_pending_approvals(self) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("""
            SELECT a.run_id, a.topic_id, a.created_at, r.script_payload_json
            FROM approvals a LEFT JOIN runs r ON a.run_id = r.run_id
            WHERE a.decision = 'pending'
            ORDER BY a.rowid DESC
            """).fetchall()
        return [
            {
                "run_id": r["run_id"],
                "topic_id": r["topic_id"],
                "created_at": r["created_at"],
                "script_payload": json.loads(r["script_payload_json"] or "{}"),
            }
            for r in rows
        ]

    # ------------------------------------------------------------ publications
    def record_publication(self, topic_id: str, run_id: str, channel: str, status: str, response_payload: Dict[str, Any]):
        with self._conn() as conn:
            conn.execute("""
            INSERT INTO publications (topic_id, run_id, channel, status, response_payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """, (topic_id, run_id, channel, status, json.dumps(response_payload, default=str), _now()))

    def list_publications(self, run_id: str) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM publications WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
        return [dict(r) for r in rows]

    def _mirror_history_to_json(self):
        """Human-readable, read-only mirror at data/topics/history.json (written atomically)."""
        history = [
            {k: t[k] for k in ("id", "title", "status", "times_published", "last_run_id", "updated_at")}
            for t in self.list_all_topics()
        ]
        target = self.topic_bank_file.parent / "history.json"
        tmp = target.with_suffix(".json.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"history": history}, f, indent=2)
            os.replace(tmp, target)
        except OSError as e:
            log.warning("Could not write history mirror: %s", e)
