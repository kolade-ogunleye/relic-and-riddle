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
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time
from typing import Dict, Any, Iterator, List, Optional

from .config import DATA_DIR, TOPICS_DIR

DB_PATH = DATA_DIR / "pipeline.db"
# A topic stuck "in_production" longer than this is assumed to be from a crashed run.
STALE_PRODUCTION_HOURS = 6

log = logging.getLogger(__name__)


class QueueEmpty(RuntimeError):
    """Every topic is published, rejected or in production; nothing new to make."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def calculate_vps(topic: Dict[str, Any]) -> float:
    """Calculates Virality Potential Score (VPS 0-100) based on algorithmic engagement drivers:
    - Concrete high-stakes numbers / scale in hook (+15)
    - Contrarian cognitive gap or paradox words (+15)
    - High-retention tactile & monumental keywords (+10)
    - Rich verified factual depth (+10)
    """
    score = 50.0  # baseline
    hook = topic.get("hook", "").lower()
    title = topic.get("title", "").lower()

    # 1. Concrete numbers or scale in hook
    if re.search(r"\b\d+[\d,]*(?:\.\d+)?\b", hook):
        score += 15.0

    # 2. Contrarian tension or cognitive gap words
    tension_words = ["not", "never", "actually", "impossible", "mystery", "secret", "without", "before", "vanished", "baffled", "defies", "instead"]
    if any(w in hook for w in tension_words):
        score += 15.0

    # 3. High-retention tactile & monumental keywords
    retention_words = ["underground", "monolith", "carved", "concrete", "stone", "pyramid", "megalith", "tomb", "shaft", "vault", "chamber", "ocean"]
    if any(w in hook or w in title for w in retention_words):
        score += 10.0

    # 4. Rich verified key facts
    facts_count = len(topic.get("key_facts", []))
    if facts_count >= 5:
        score += 10.0
    elif facts_count >= 3:
        score += 5.0

    return min(100.0, score)


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
            try:
                conn.execute("ALTER TABLE topics ADD COLUMN priority INTEGER DEFAULT 0")
            except Exception:
                pass
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
                priority INTEGER DEFAULT 0,
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
            self._add_column(conn, "runs", "parent_run_id", "TEXT")
            self._add_column(conn, "runs", "content_format", "TEXT DEFAULT 'reel'")
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
            self._add_column(conn, "llm_usage", "task", "TEXT")
            conn.execute("""
            CREATE TABLE IF NOT EXISTS media_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                item TEXT,
                cost_usd REAL NOT NULL,
                created_at TEXT NOT NULL
            )""")
            # Reel / Story / carousel posts waiting for their slot (due_at in UTC; NULL = next carousel slot).
            conn.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                due_at TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                files_json TEXT,
                detail_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""")
            conn.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER,
                text TEXT NOT NULL,
                kind TEXT DEFAULT 'persistent',
                expires_at TEXT,
                purged INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )""")
            conn.execute("""
            CREATE TABLE IF NOT EXISTS issues (
                id TEXT PRIMARY KEY,
                run_id TEXT,
                critique TEXT NOT NULL,
                user_id TEXT,
                evidence_path TEXT,
                status TEXT DEFAULT 'open',
                diagnosis TEXT,
                created_at TEXT NOT NULL
            )""")
            conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                username TEXT,
                action TEXT NOT NULL,
                payload TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")
            conn.execute("""
            CREATE TABLE IF NOT EXISTS telegram_media_cache (
                cache_key TEXT PRIMARY KEY,
                file_id TEXT NOT NULL,
                file_type TEXT DEFAULT 'photo',
                updated_at TEXT NOT NULL
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
            "priority": row["priority"] if "priority" in row.keys() else 0,
            "times_published": row["times_published"],
            "last_run_id": row["last_run_id"],
            "updated_at": row["updated_at"],
        }

    def get_next_topic(self) -> Dict[str, Any]:
        """
        Next topic to produce using a 4-pillar round-robin scheduler:
        1. Identifies eligible candidate topics (queued first, then failed/stale).
        2. Scores each candidate's Virality Potential Score (VPS).
        3. Identifies the primary sub_theme of the most recently produced/published topic.
        4. Interleaves diverse themes: prioritizes candidates from a DIFFERENT sub_theme
           to avoid viewer fatigue from consecutive similar topics.
        5. Within eligible candidates, ranks by:
           - Status ('queued' over 'failed')
           - Virality Potential Score (highest VPS first)
           - Times published (least published first)
           - Insertion order (rowid ASC)
        """
        stale_before = (datetime.now(timezone.utc) - timedelta(hours=STALE_PRODUCTION_HOURS)).isoformat()
        with self._conn() as conn:
            rows = conn.execute("""
            SELECT * FROM topics
            WHERE status IN ('queued', 'failed')
               OR (status = 'in_production' AND updated_at < ?)
            ORDER BY CASE status WHEN 'queued' THEN 0 ELSE 1 END, times_published ASC, rowid ASC
            """, (stale_before,)).fetchall()

            last_topic = conn.execute("""
            SELECT sub_theme FROM topics
            WHERE status = 'published' OR times_published > 0
            ORDER BY updated_at DESC
            LIMIT 1
            """).fetchone()

        if not rows:
            raise QueueEmpty("No unpublished topics left. Add new ones with /propose (or `python main.py topic propose`).")

        candidates = [self._topic_from_row(r) for r in rows]
        last_theme = (last_topic["sub_theme"] or "").lower().strip() if last_topic else ""

        # Score and rank candidates
        def candidate_sort_key(t: Dict[str, Any]) -> tuple:
            # Theme diversity: 0 if different theme from last run, 1 if same theme
            cur_theme = (t.get("sub_theme") or "").lower().strip()
            theme_clash = 1 if (last_theme and cur_theme == last_theme and len(candidates) > 1) else 0
            status_prio = 0 if t.get("status") == "queued" else 1
            vps = calculate_vps(t)
            times_pub = t.get("times_published", 0)
            priority = t.get("priority", 0)
            return (-priority, theme_clash, status_prio, -vps, times_pub)

        ranked = sorted(candidates, key=candidate_sort_key)
        return ranked[0]

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
    def bump_topic(self, topic_id: str) -> bool:
        """Promotes a topic to highest priority in the queue."""
        with self._conn() as conn:
            row = conn.execute("SELECT COALESCE(MAX(priority), 0) FROM topics").fetchone()
            max_p = row[0] if row else 0
            t_row = conn.execute("SELECT status FROM topics WHERE id = ?", (topic_id,)).fetchone()
            if t_row and t_row[0] in ("archived", "rejected", "failed"):
                cur = conn.execute("UPDATE topics SET priority = ?, status = 'queued', updated_at = ? WHERE id = ?",
                                   (max_p + 1, _now(), topic_id))
            else:
                cur = conn.execute("UPDATE topics SET priority = ?, updated_at = ? WHERE id = ?",
                                   (max_p + 1, _now(), topic_id))
            return cur.rowcount > 0

    def drop_topic(self, topic_id: str) -> bool:
        """Removes a topic from the active queue by marking it 'archived'."""
        with self._conn() as conn:
            cur = conn.execute("UPDATE topics SET status = 'archived', priority = 0, updated_at = ? WHERE id = ?",
                               (_now(), topic_id))
            return cur.rowcount > 0

    def requeue_topic(self, topic_id: str):
        """Returns a topic to the queue without touching its publish count."""
        with self._conn() as conn:
            conn.execute("UPDATE topics SET priority = 0, updated_at = ? WHERE id = ?", (_now(), topic_id))
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
    def create_run(
        self,
        run_id: str,
        topic_id: str,
        parent_run_id: Optional[str] = None,
        content_format: str = "reel",
    ):
        now = _now()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO runs (run_id, topic_id, status, parent_run_id, content_format, created_at, updated_at) VALUES (?, ?, 'producing', ?, ?, ?, ?)",
                (run_id, topic_id, parent_run_id, content_format, now, now),
            )

    def update_run(self, run_id: str, status: str, error: Optional[str] = None, **fields: Any):
        allowed = {
            "script_payload", "master_video_path", "thumbnail_cover_path", "thumbnail_grid_path",
            "parent_run_id", "content_format",
        }
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

    def claim_publication(self, run_id: str) -> bool:
        """Only one caller may initiate an upload for a finished run. A run a human rejected (or
        sent back for regeneration) can never be claimed, whatever code path asks."""
        with self._conn() as conn:
            result = conn.execute(
                "UPDATE runs SET status = 'publishing', updated_at = ? WHERE run_id = ? "
                "AND status IN ('approved', 'pending_approval', 'scheduled', 'failed', "
                "'simulated', 'timeout')", (_now(), run_id))
            return result.rowcount == 1

    def recover_interrupted_publication(self, run_id: str) -> bool:
        """Hold the OS upload lock, then quarantine a claim left by a dead worker.

        This never retries an upload. Account reconciliation remains mandatory.
        Stop all older workers that predate publication locks before using this API.
        """
        from .publication_lock import publication_lock
        with publication_lock(self.db_path, run_id):
            with self._conn() as conn:
                row = conn.execute("SELECT topic_id FROM runs WHERE run_id = ? AND status = 'publishing'", (run_id,)).fetchone()
                if not row:
                    return False
                conn.execute("UPDATE runs SET status = 'publish_unknown', updated_at = ?, error = ? WHERE run_id = ? AND status = 'publishing'",
                             (_now(), "Interrupted upload: verify platform accounts before reconciliation", run_id))
                conn.execute("UPDATE topics SET status = 'publish_unknown' WHERE id = ?", (row["topic_id"],))
                return True

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
        label = f'"{title}"'
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

    # -------------------------------------------------------- active progress
    def set_active_progress(self, run_id: str, topic_title: str, step: int, total_steps: int,
                            details: str = "", message_id: Optional[int] = None,
                            start_time: Optional[float] = None) -> None:
        """Stores the active running render state so Telegram /clear and /status can track it."""
        current = self.get_active_progress() or {}
        st = start_time if start_time is not None else current.get("start_time", time.time())
        mid = message_id if message_id is not None else current.get("message_id")
        payload = {
            "run_id": run_id,
            "topic_title": topic_title,
            "step": step,
            "total_steps": total_steps,
            "details": details,
            "message_id": mid,
            "start_time": st,
            "updated_at": _now(),
        }
        self.set_setting("active_progress", json.dumps(payload))

    def get_active_progress(self) -> Optional[Dict[str, Any]]:
        """Returns the active running render state, or None if idle."""
        val = self.get_setting("active_progress")
        if not val:
            return None
        try:
            return json.loads(val)
        except Exception:
            return None

    def clear_active_progress(self) -> None:
        """Clears active render state when a job completes, fails, or is cancelled."""
        with self._conn() as conn:
            conn.execute("DELETE FROM settings WHERE key = 'active_progress'")

    # ------------------------------------------------------------- llm usage
    def record_llm_usage(self, model: str, input_tokens: int, output_tokens: int, web_searches: int, cost_usd: float,
                         task: Optional[str] = None):
        with self._conn() as conn:
            conn.execute("""
            INSERT INTO llm_usage (model, input_tokens, output_tokens, web_searches, cost_usd, created_at, task)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (model, input_tokens, output_tokens, web_searches, cost_usd, _now(), task))

    def llm_spend(self, month: str) -> float:
        """Total estimated Claude spend (USD) for a month given as 'YYYY-MM' (UTC)."""
        with self._conn() as conn:
            row = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) AS s FROM llm_usage WHERE substr(created_at, 1, 7) = ?",
                               (month,)).fetchone()
        return float(row["s"])

    def record_media_usage(self, provider: str, item: str, cost_usd: float):
        with self._conn() as conn:
            conn.execute("INSERT INTO media_usage (provider, item, cost_usd, created_at) VALUES (?, ?, ?, ?)",
                         (provider, item, cost_usd, _now()))

    def media_spend(self, month: str) -> float:
        with self._conn() as conn:
            row = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) AS s FROM media_usage WHERE substr(created_at, 1, 7) = ?",
                               (month,)).fetchone()
        return float(row["s"])

    def llm_spend_by_task(self, month: str) -> List[Dict[str, Any]]:
        """[{"task", "calls", "cost"}] for the month, most expensive first."""
        with self._conn() as conn:
            rows = conn.execute("""
            SELECT COALESCE(task, 'other') AS task, COUNT(*) AS calls, SUM(cost_usd) AS cost FROM llm_usage
            WHERE substr(created_at, 1, 7) = ? GROUP BY COALESCE(task, 'other') ORDER BY cost DESC""", (month,)).fetchall()
        return [dict(r) for r in rows]

    def list_recent_runs(self, limit: int = 5) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT run_id, topic_id, status, error, parent_run_id, content_format, updated_at FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
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

    # --------------------------------------------------------- scheduled posts
    def add_scheduled_post(self, run_id: str, kind: str, due_at: Optional[str], files: List[str],
                           status: str = "pending") -> int:
        now = _now()
        with self._conn() as conn:
            cur = conn.execute("""
            INSERT INTO scheduled_posts (run_id, kind, due_at, status, files_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""", (run_id, kind, due_at, status, json.dumps(files), now, now))
            return cur.lastrowid

    def update_scheduled_post(self, post_id: int, status: str, due_at: Optional[str] = None,
                              detail: Optional[Dict[str, Any]] = None):
        with self._conn() as conn:
            conn.execute("""
            UPDATE scheduled_posts SET status = ?, due_at = COALESCE(?, due_at),
                   detail_json = COALESCE(?, detail_json), updated_at = ? WHERE id = ?""",
                         (status, due_at, json.dumps(detail, default=str) if detail is not None else None, _now(), post_id))

    def list_scheduled_posts(self, statuses: tuple = ("pending", "waiting"), run_id: Optional[str] = None) -> List[Dict[str, Any]]:
        q = f"SELECT * FROM scheduled_posts WHERE status IN ({','.join('?' * len(statuses))})"
        args: List[Any] = list(statuses)
        if run_id:
            q += " AND run_id = ?"
            args.append(run_id)
        with self._conn() as conn:
            rows = conn.execute(q + " ORDER BY COALESCE(due_at, '9999'), id", args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["files"] = json.loads(d.pop("files_json") or "[]")
            d["detail"] = json.loads(d.pop("detail_json") or "{}")
            out.append(d)
        return out

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

    # ------------------------------------------------------------ alerts & issues
    def record_alert(self, text: str, message_id: Optional[int] = None, kind: str = "persistent", expires_at: Optional[str] = None) -> int:
        now = _now()
        with self._conn() as conn:
            cur = conn.execute("""
            INSERT INTO alerts (message_id, text, kind, expires_at, purged, created_at)
            VALUES (?, ?, ?, ?, 0, ?)
            """, (message_id, text, kind, expires_at, now))
            return cur.lastrowid

    def get_expired_alerts(self, now: Optional[str] = None) -> List[Dict[str, Any]]:
        now = now or _now()
        with self._conn() as conn:
            rows = conn.execute("""
            SELECT * FROM alerts WHERE purged = 0 AND expires_at IS NOT NULL AND expires_at <= ? AND message_id IS NOT NULL
            """, (now,)).fetchall()
            return [dict(r) for r in rows]

    def mark_alert_purged(self, alert_id: int):
        with self._conn() as conn:
            conn.execute("UPDATE alerts SET purged = 1 WHERE id = ?", (alert_id,))

    def list_recent_alerts(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("""
            SELECT * FROM alerts ORDER BY id DESC LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]

    def list_known_message_ids(self) -> List[int]:
        """Returns all known Telegram message IDs recorded by the bot across alerts and approvals."""
        with self._conn() as conn:
            mids = set()
            try:
                for r in conn.execute("SELECT message_id FROM alerts WHERE message_id IS NOT NULL").fetchall():
                    mids.add(r[0])
            except Exception:
                pass
            try:
                for r in conn.execute("SELECT telegram_message_id FROM approvals WHERE telegram_message_id IS NOT NULL").fetchall():
                    mids.add(r[0])
            except Exception:
                pass
            return sorted(mids, reverse=True)

    def record_issue(self, issue_id: str, critique: str, run_id: Optional[str] = None, user_id: Optional[str] = None, evidence_path: Optional[str] = None) -> Dict[str, Any]:
        now = _now()
        with self._conn() as conn:
            conn.execute("""
            INSERT INTO issues (id, run_id, critique, user_id, evidence_path, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'open', ?)
            """, (issue_id, run_id, critique, str(user_id) if user_id else None, str(evidence_path) if evidence_path else None, now))
        return {"id": issue_id, "run_id": run_id, "critique": critique, "created_at": now}

    def list_open_issues(self) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM issues WHERE status = 'open' ORDER BY created_at DESC").fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------ audit log & telegram media cache
    def record_audit_event(self, user_id: str, username: Optional[str] = None, action: str = "command",
                           payload: Optional[str] = None, status: str = "allowed",
                           chat_id: Optional[str] = None, event_type: Optional[str] = None, **kwargs: Any) -> int:
        act = event_type or action
        now = _now()
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO audit_log (user_id, username, action, payload, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (str(user_id), username or "", act, payload or "", status, now)
            )
            return cur.lastrowid

    def list_audit_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_cached_telegram_media(self, cache_key: str) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT file_id FROM telegram_media_cache WHERE cache_key = ?", (cache_key,)
            ).fetchone()
            return row["file_id"] if row else None

    def set_cached_telegram_media(self, cache_key: str, file_id: str, file_type: str = "photo") -> None:
        now = _now()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO telegram_media_cache (cache_key, file_id, file_type, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(cache_key) DO UPDATE SET "
                "file_id = excluded.file_id, file_type = excluded.file_type, updated_at = excluded.updated_at",
                (cache_key, file_id, file_type, now)
            )
