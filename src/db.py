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
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
import time
from typing import Dict, Any, ClassVar, Iterator, List, Optional

from .config import APPROVAL_TIMEOUT_SECONDS, DATA_DIR, TOPICS_DIR

DB_PATH = DATA_DIR / "pipeline.db"
# A topic stuck "in_production" longer than this is assumed to be from a crashed run.
STALE_PRODUCTION_HOURS = 6
TIMEOUT_HOLD_DAYS = 7  # a timed-out review's topic waits this long for Resend or Reject before it is made again
# A review decision the worker is about to act on (its decision time), and the decisions a card's Undo may reopen
# while the worker has not claimed them (undo_review_decision; main.undone_before_acting)
REVIEW_CLAIM_PREFIX = "review-claim:"
UNDOABLE = ("approved", "rejected")
UNDONE_BY = "undone"   # decided_by of a review an Undo reopened: the worker's wait goes on (it never acts on it)

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
            self._add_column(conn, "topics", "added_json", "TEXT")  # who added the topic, how and when
            # QF-73: the topic's sources, sensitivity, region_group and hold (topic_policy.EDITORIAL_KEYS)
            self._add_column(conn, "topics", "editorial_json", "TEXT")
            self._add_column(conn, "runs", "parent_run_id", "TEXT")
            self._add_column(conn, "runs", "content_format", "TEXT DEFAULT 'reel'")
            self._add_column(conn, "approvals", "decided_by", "TEXT")
            self._add_column(conn, "approvals", "expires_at", "REAL")
            self._add_column(conn, "approvals", "revision", "INTEGER DEFAULT 0")
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
            conn.execute("""
            CREATE TABLE IF NOT EXISTS telegram_cards (
                card_id TEXT PRIMARY KEY,
                topic_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1,
                card_kind TEXT NOT NULL,
                payload TEXT NOT NULL,
                expires_at TEXT,
                updated_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")
            # The library music each run's bed was made from (QF-84). A selection is not a use: published uses are
            # counted from these rows joined to the run's confirmed Reel receipts (music_published_uses).
            conn.execute("""
            CREATE TABLE IF NOT EXISTS music_selections (
                run_id TEXT NOT NULL,
                file TEXT NOT NULL,
                sha256 TEXT,
                mood TEXT,
                position INTEGER NOT NULL DEFAULT 0,
                recorded_at TEXT NOT NULL,
                PRIMARY KEY (run_id, file)
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_telegram_cards_topic ON telegram_cards(topic_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_telegram_cards_expires ON telegram_cards(expires_at)")
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
        from .topic_policy import editorial
        with self._conn() as conn:
            existing = {r["id"] for r in conn.execute("SELECT id FROM topics")}
            changed = 0
            for t in topics:
                cur = conn.execute("""
                INSERT INTO topics (
                    id, title, sub_theme, hook, location_json, key_facts_json,
                    hashtags_json, meta_categories_json, added_json, editorial_json, status, times_published,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title, sub_theme = excluded.sub_theme, hook = excluded.hook,
                    location_json = excluded.location_json, key_facts_json = excluded.key_facts_json,
                    hashtags_json = excluded.hashtags_json, meta_categories_json = excluded.meta_categories_json,
                    added_json = excluded.added_json, editorial_json = excluded.editorial_json
                WHERE topics.title IS NOT excluded.title OR topics.hook IS NOT excluded.hook
                   OR topics.added_json IS NOT excluded.added_json
                   OR topics.editorial_json IS NOT excluded.editorial_json
                   OR topics.sub_theme IS NOT excluded.sub_theme OR topics.location_json IS NOT excluded.location_json
                   OR topics.key_facts_json IS NOT excluded.key_facts_json OR topics.hashtags_json IS NOT excluded.hashtags_json
                   OR topics.meta_categories_json IS NOT excluded.meta_categories_json
                """, (
                    t["id"], t.get("title", ""), t.get("sub_theme", ""), t.get("hook", ""),
                    json.dumps(t.get("location", {})), json.dumps(t.get("key_facts", [])),
                    json.dumps(t.get("hashtags", [])), json.dumps(t.get("meta_categories", [])),
                    json.dumps(t["added"]) if t.get("added") else None,
                    json.dumps(editorial(t)),
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
            **json.loads(row["editorial_json"] or "{}"),   # every topics row has it (_init_db adds the column)
            "id": row["id"],
            "title": row["title"],
            "sub_theme": row["sub_theme"],
            "hook": row["hook"],
            "location": json.loads(row["location_json"] or "{}"),
            "key_facts": json.loads(row["key_facts_json"] or "[]"),
            "hashtags": json.loads(row["hashtags_json"] or "[]"),
            "meta_categories": json.loads(row["meta_categories_json"] or "[]"),
            "added": json.loads(row["added_json"] or "{}") if "added_json" in row.keys() else {},
            "status": row["status"],
            "priority": row["priority"] if "priority" in row.keys() else 0,
            "times_published": row["times_published"],
            "last_run_id": row["last_run_id"],
            "updated_at": row["updated_at"],
        }

    def ranked_topics(self) -> List[Dict[str, Any]]:
        """
        Topics in the order production would pick them using a 4-pillar round-robin scheduler:
        1. Identifies eligible candidate topics (queued, and stale in-production; a failed one waits for you).
        2. Scores each candidate's Virality Potential Score (VPS).
        3. Identifies the primary sub_theme of the most recently produced/published topic.
        4. Interleaves diverse themes: prioritizes candidates from a DIFFERENT sub_theme
           to avoid viewer fatigue from consecutive similar topics.
        5. Within eligible candidates, ranks by:
           - Status ('queued' over stale 'in_production')
           - Virality Potential Score (highest VPS first)
           - Times published (least published first)
           - Insertion order (rowid ASC)
        """
        stale_before = (datetime.now(timezone.utc) - timedelta(hours=STALE_PRODUCTION_HOURS)).isoformat()
        with self._conn() as conn:
            # A failed topic waits for you (Back to the queue, /requeue): it was picked again on its own while the
            # queue listed it under "Can be restored", and the failure card said it was set aside (2026-10-25).
            rows = conn.execute("""
            SELECT * FROM topics
            WHERE status = 'queued'
               OR (status = 'in_production' AND updated_at < ?)
            ORDER BY CASE status WHEN 'queued' THEN 0 ELSE 1 END, times_published ASC, rowid ASC
            """, (stale_before,)).fetchall()

            last_topic = conn.execute("""
            SELECT sub_theme FROM topics
            WHERE status = 'published' OR times_published > 0
            ORDER BY updated_at DESC
            LIMIT 1
            """).fetchone()
            # Dry runs that reached a simulated publish. Nothing went out, so the topic returns to the queue
            # unpublished, and it came first again: validation remade the same topic run after run
            # (dev, 2026-10-23). A rehearsed topic now waits behind those not yet made.
            rehearsed = dict(conn.execute(
                "SELECT topic_id, COUNT(*) FROM runs WHERE status = 'simulated' GROUP BY topic_id").fetchall())
            # A render whose review timed out waits for Resend or Reject; its topic is not made again meanwhile
            # (a later run produced it a second time while the first render still waited: 2026-10-23 audit).
            held = {r[0] for r in conn.execute(
                "SELECT topic_id FROM runs WHERE status = 'timeout' AND updated_at >= ?",
                ((datetime.now(timezone.utc) - timedelta(days=TIMEOUT_HOLD_DAYS)).isoformat(),))}
            # QF-73: a topic on an editorial hold, or a sensitive one whose audience day is not clear, waits.
            from .topic_policy import topic_problem
            editorial_held = {r["id"] for r in rows if r["id"] not in held and topic_problem(conn, r["id"])}
        rows = [r for r in rows if r["id"] not in held | editorial_held]

        if not rows:
            if editorial_held:
                raise QueueEmpty("Every topic left is on an editorial hold or waits for a clear day between sensitive "
                                 "topics (reconcile any unconfirmed publication).")
            if held:  # not empty: what is left waits for a decision on a timed-out review
                raise QueueEmpty("Every topic left waits for a timed-out review: Resend or Reject it (Reviews).")
            raise QueueEmpty("No unpublished topics left. Add new ones with /ideas research (or `python main.py topic propose`).")

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
            # An operator's Bump (priority) still comes first; then topics not yet rehearsed in a dry run.
            return (-priority, rehearsed.get(t["id"], 0), theme_clash, status_prio, -vps, times_pub)

        return sorted(candidates, key=candidate_sort_key)

    def get_next_topic(self) -> Dict[str, Any]:
        return self.ranked_topics()[0]

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
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT COALESCE(MAX(priority), 0) FROM topics").fetchone()
            max_p = row[0] if row else 0
            t_row = conn.execute("SELECT status FROM topics WHERE id = ?", (topic_id,)).fetchone()
            if t_row and t_row[0] in ("archived", "rejected", "failed"):
                cur = conn.execute("UPDATE topics SET priority = ?, status = 'queued', updated_at = ? "
                                   "WHERE id = ? AND status IN ('archived', 'rejected', 'failed')",
                                   (max_p + 1, _now(), topic_id))
            else:
                cur = conn.execute("UPDATE topics SET priority = ?, updated_at = ? WHERE id = ? AND status = 'queued'",
                                   (max_p + 1, _now(), topic_id))
            return cur.rowcount > 0

    def drop_topic(self, topic_id: str) -> bool:
        """Removes a topic from the active queue by marking it 'archived'."""
        with self._conn() as conn:
            cur = conn.execute("UPDATE topics SET status = 'archived', priority = 0, updated_at = ? WHERE id = ? AND status='queued'",
                               (_now(), topic_id))
            return cur.rowcount > 0

    def requeue_topic(self, topic_id: str, stale: bool = False) -> bool:
        """Returns a topic to the queue (an explicit operator decision; published topics included) without
        touching its publish count. Refused while the topic is being made or already queued; `stale` lets a
        topic still marked in production come back when the caller found its last run over (control.stale_topics)."""
        blocked = "('queued')" if stale else "('queued', 'in_production')"
        with self._conn() as conn:
            cur = conn.execute(f"""UPDATE topics SET priority = 0, status = 'queued', updated_at = ?
                WHERE id = ? AND status NOT IN {blocked}""", (_now(), topic_id))
            return cur.rowcount == 1

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
            # QF-73: the spacing check and the claim are one transaction, so two sensitive runs cannot both pass it.
            conn.execute("BEGIN IMMEDIATE")
            from .topic_policy import topic_problem
            run = conn.execute("SELECT topic_id FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if run and topic_problem(conn, run["topic_id"], run_id=run_id, spacing_only=True):
                return False
            result = conn.execute(
                "UPDATE runs SET status = 'publishing', updated_at = ? WHERE run_id = ? "
                "AND status IN ('approved', 'pending_approval', 'scheduled', 'failed', "
                "'simulated', 'timeout')", (_now(), run_id))
            return result.rowcount == 1

    def topic_problem(self, topic_id: str, at: Any = None, *, run_id: str = "", spacing_only: bool = False) -> str:
        """Why the topic cannot be made or published at `at` (now by default): its editorial hold, or a sensitive
        topic's spacing (src/topic_policy.topic_problem); "" when it can."""
        from .topic_policy import topic_problem
        with self._conn() as conn:
            return topic_problem(conn, topic_id, at, run_id=run_id, spacing_only=spacing_only)

    @staticmethod
    def _check_topic_booking(conn: sqlite3.Connection, run_id: str, kind: str, due_at: str | None,
                             status: str) -> None:
        """A Reel booked or moved onto a day the topic's spacing does not allow is refused (QF-73)."""
        if kind != "reel" or status not in ("pending", "waiting"):
            return
        from .topic_policy import topic_problem
        row = conn.execute("SELECT topic_id FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        problem = topic_problem(conn, row["topic_id"], due_at, run_id=run_id, spacing_only=True) if row else ""
        if problem:
            raise ValueError(problem)

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
            from .local_time import local
            when = local(run["created_at"]).strftime("%d %b %H:%M")  # the operator's DEFAULT_TIMEZONE
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
            ON CONFLICT(run_id) DO UPDATE SET telegram_message_id = excluded.telegram_message_id, revision = revision + 1, expires_at = NULL,
                created_at = excluded.created_at,  -- delivered now: the stale sweep counts from the newest card (2026-10-25)
                decided_by = CASE WHEN decided_by = ? THEN NULL ELSE decided_by END
                -- a new card ends what an Undo gave back: its wait narrows the 24 h placeholder again (UI-02 review 3)
            """, (run_id, topic_id, message_id, _now(), UNDONE_BY))
            conn.execute("UPDATE approvals SET expires_at=? WHERE run_id=?", (time.time() + 86400, run_id))

    def update_approval_decision(self, run_id: str, decision: str, decided_by: Optional[str] = None) -> bool:
        """Records a decision only while the approval is still pending. Returns True if applied."""
        with self._conn() as conn:
            cur = conn.execute("""
            UPDATE approvals SET decision = ?, decision_timestamp = ?, decided_by = ?
            WHERE run_id = ? AND decision = 'pending'
            """, (decision, _now(), decided_by, run_id))
            return cur.rowcount == 1

    def decide_telegram_review(self, run_id, message_id, decision, actor):
        """Only the current delivered card may decide an unexpired review."""
        with self._conn() as conn:
            cur = conn.execute("""UPDATE approvals SET decision=?, decision_timestamp=?, decided_by=?
                WHERE run_id=? AND decision='pending' AND telegram_message_id=? AND expires_at>?
                AND EXISTS (SELECT 1 FROM runs WHERE runs.run_id=approvals.run_id
                            AND status IN ('pending_approval', 'producing'))""",
                (decision, _now(), actor, run_id, message_id, time.time()))
            return cur.rowcount == 1

    def undo_review_decision(self, run_id, decision, message_id, within_seconds, window_seconds=None):
        """Reopens a review decided on its card (decision back to pending), only while nothing has been done with
        that decision: it is still `decision`, taken on this card (`message_id`) at most `within_seconds` ago,
        the run still waits on its review, the worker has not claimed it (REVIEW_CLAIM_PREFIX holds another time),
        and nothing is booked or posted for the run. One statement: a claim and an undo never both win.
        The review gets back the time it had left when it was decided, and at least `within_seconds` from now
        (expires_at): an Undo after its window had closed gave back an Approve that refused (review 2 of UI-02).
        What it gets back is at most the review window, `window_seconds` (default APPROVAL_TIMEOUT_SECONDS): a
        decision taken before any wait narrowed the delivery's 24 h placeholder gave back about 24 h, and the
        review wait (with the production lock) followed it (review 3 of UI-02).
        Returns True when it was reopened."""
        if decision not in UNDOABLE:
            return False
        cutoff = (datetime.now(UTC) - timedelta(seconds=within_seconds)).isoformat()
        with self._conn() as conn:
            row = conn.execute("SELECT expires_at, decision_timestamp FROM approvals WHERE run_id=?", (run_id,)).fetchone()
            left = 0.0
            try:
                left = float(row["expires_at"]) - datetime.fromisoformat(str(row["decision_timestamp"])).timestamp()
            except (TypeError, ValueError, KeyError, IndexError):
                pass
            cap = max(float(APPROVAL_TIMEOUT_SECONDS if window_seconds is None else window_seconds), 0.0)
            now = time.time()
            kept = min(float((row["expires_at"] if row else None) or 0), now + cap)
            expires = max(kept, now + max(min(left, cap), within_seconds))
            cur = conn.execute("""UPDATE approvals SET decision='pending', decision_timestamp=NULL, decided_by=?,
                expires_at=?
                WHERE run_id=? AND decision=? AND telegram_message_id=? AND decision_timestamp>=?
                AND EXISTS (SELECT 1 FROM runs WHERE runs.run_id=approvals.run_id AND status='pending_approval')
                AND NOT EXISTS (SELECT 1 FROM settings WHERE key=? AND value=approvals.decision_timestamp)
                AND NOT EXISTS (SELECT 1 FROM scheduled_posts WHERE scheduled_posts.run_id=approvals.run_id
                                AND status IN ('pending', 'waiting', 'blocked', 'published', 'unknown'))
                AND NOT EXISTS (SELECT 1 FROM publications WHERE publications.run_id=approvals.run_id
                                AND status IN ('published', 'unknown'))""",
                (UNDONE_BY, expires, run_id, decision, message_id, cutoff, REVIEW_CLAIM_PREFIX + run_id))
            return cur.rowcount == 1

    def commit_review_copy(self, run_id, revision, payload):
        """CAS the reviewed payload and invalidate its buttons in the same transaction."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute("""UPDATE approvals SET telegram_message_id=NULL, revision=revision+1
                WHERE run_id=? AND revision=? AND decision='pending'
                AND EXISTS (SELECT 1 FROM runs WHERE runs.run_id=approvals.run_id AND status='pending_approval')""", (run_id, revision))
            if cur.rowcount != 1:
                return False
            conn.execute("UPDATE runs SET script_payload_json=?, updated_at=? WHERE run_id=? AND status='pending_approval'",
                         (json.dumps(payload), _now(), run_id))
            return True

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

    def request_run_cancel(self, run_id):
        """Accept only while producing; never cancel an upload or infer its outcome."""
        with self._conn() as conn:
            result = conn.execute("""INSERT INTO settings (key,value,updated_at)
                SELECT ?, 'requested', ? FROM runs WHERE run_id=? AND status='producing'
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                ("cancel_run:" + run_id, _now(), run_id))
            return result.rowcount > 0

    # -------------------------------------------------------- active progress
    def set_active_progress(self, run_id: str, topic_title: str, step: int, total_steps: int,
                            details: str = "", message_id: Optional[int] = None,
                            start_time: Optional[float] = None) -> None:
        """Stores the active running render state so Telegram /clear and /status can track it."""
        current = self.get_active_progress() or {}
        if current.get("run_id") != run_id:
            current = {}
        st = start_time if start_time is not None else current.get("start_time", time.time())
        mid = message_id if message_id is not None else current.get("message_id")
        # When each step was first reported (step 1 starts with the run), and since when the detail line
        # has said what it says: the card shows how long a step has been running instead of a stale note.
        steps = dict(current.get("step_started") or {"1": st})
        steps.setdefault(str(step), time.time())
        same_detail = current.get("step") == step and current.get("details") == details
        payload = {
            "run_id": run_id,
            "topic_title": topic_title,
            "step": step,
            "total_steps": total_steps,
            "details": details,
            "message_id": mid,
            "start_time": st,
            "step_started": steps,
            "details_since": current.get("details_since", time.time()) if same_detail else time.time(),
            "updated_at": _now(),
        }
        from .process_state import identity
        payload["owner_pid"] = current.get("owner_pid", os.getpid())
        payload["owner_identity"] = current.get("owner_identity", identity(os.getpid()))
        self.set_setting("active_progress", json.dumps(payload))

    def get_active_progress(self) -> Optional[Dict[str, Any]]:
        """Returns the active running render state, or None if idle. A record whose run is no longer
        being made (it finished, failed or was approved) is stale: it is cleared and None returned."""
        val = self.get_setting("active_progress")
        if not val:
            return None
        try:
            run_id = json.loads(val).get("run_id")
        except (ValueError, AttributeError):
            run_id = None
        run = self.get_run(run_id) if run_id else None
        # Stale when its run finished or no longer exists (a record for a missing run never cleared, so the
        # dashboard kept offering Progress instead of making the next topic). Runs are created first.
        if run is None or run.get("status") != "producing":
            self.clear_active_progress(run_id)
            return None
        try:
            progress = json.loads(val)
            from .process_state import identity
            owner = progress.get("owner_identity")
            observed = identity(progress["owner_pid"]) if progress.get("owner_pid") else "unknown"
            if owner and owner != "unknown" and observed != "unknown" and observed != owner:
                progress["worker_state"] = "interrupted"
            else:
                progress["worker_state"] = "running" if owner and owner != "unknown" and observed == owner else "unverified"
            return progress
        except Exception:
            return None

    # Retention per table, as published in docs/privacy.md (keep the two in step). audience_*: per-post platform
    # metrics kept two years (the channel's own medians), the media ids they were read by as long, and each
    # source's outcome (API messages, redacted) 90 days.
    RETENTION_DAYS = {"audit_log": 90, "alerts": 90, "telegram_media_cache": 30, "findings": 365,
                      "audience_metrics": 730, "audience_media_ids": 730, "audience_sources": 90}
    RETENTION_COLUMN: ClassVar[dict[str, str]] = {"telegram_media_cache": "updated_at", "audience_metrics": "fetched_at",
                                                   "audience_media_ids": "found_at", "audience_sources": "collected_at"}

    def prune_history(self) -> Dict[str, int]:
        """Deletes old rows from the tables that only grow (activity log, alerts, Telegram media cache,
        improvement findings, audience metrics). Runs, approvals, issues, spend and publications are the
        audit trail and are never pruned. Returns rows deleted per table."""
        from datetime import timedelta
        deleted = {}
        with self._conn() as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table, days in self.RETENTION_DAYS.items():
                if table not in tables:
                    continue
                column = self.RETENTION_COLUMN.get(table, "created_at")
                cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
                deleted[table] = conn.execute(f"DELETE FROM {table} WHERE {column} < ?", (cutoff,)).rowcount
        return deleted

    PROGRESS_HISTORY_KEY = "progress_step_history"

    def record_progress_history(self, run_id: str, keep: int = 8) -> None:
        """At a run's successful end: how long each progress step took, for the next runs' estimates."""
        active = self.get_active_progress()
        if not active or active.get("run_id") != run_id or not active.get("step_started"):
            return
        total = int(active.get("total_steps") or 0)
        starts = {int(k): float(v) for k, v in active["step_started"].items()}
        if total < 1 or sorted(starts) != list(range(1, total + 1)):
            return  # a step was never reported: durations would be wrong
        ends = [starts[n + 1] for n in range(1, total)] + [time.time()]
        durations = [round(max(0.0, ends[n - 1] - starts[n]), 1) for n in range(1, total + 1)]
        try:
            history = json.loads(self.get_setting(self.PROGRESS_HISTORY_KEY) or "[]")
        except ValueError:
            history = []
        history = [h for h in history if isinstance(h, list) and len(h) == total][-(keep - 1):] + [durations]
        self.set_setting(self.PROGRESS_HISTORY_KEY, json.dumps(history))

    def typical_step_seconds(self, total: int) -> Optional[List[float]]:
        """The median length of each progress step over recent successful runs (None without history)."""
        try:
            history = [h for h in json.loads(self.get_setting(self.PROGRESS_HISTORY_KEY) or "[]")
                       if isinstance(h, list) and len(h) == total]
        except ValueError:
            return None
        if not history:
            return None
        import statistics
        return [float(statistics.median(h[n] for h in history)) for n in range(total)]

    def clear_active_progress(self, run_id=None) -> None:
        """Clears active render state when a job completes, fails, or is cancelled."""
        with self._conn() as conn:
            conn.execute("DELETE FROM settings WHERE key = 'active_progress' AND (? IS NULL OR json_extract(value, '$.run_id')=?)", (run_id,run_id))

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

    def media_spend(self, month: str, provider: str | None = None) -> float:
        """The month's media spend, all providers or one."""
        sql = "SELECT COALESCE(SUM(cost_usd), 0) AS s FROM media_usage WHERE substr(created_at, 1, 7) = ?"
        args: tuple[Any, ...] = (month,)
        if provider is not None:
            sql, args = sql + " AND provider = ?", (month, provider)
        with self._conn() as conn:
            row = conn.execute(sql, args).fetchone()
        return float(row["s"])

    def llm_spend_by_task(self, month: str) -> List[Dict[str, Any]]:
        """[{"task", "calls", "cost"}] for the month, most expensive first."""
        with self._conn() as conn:
            rows = conn.execute("""
            SELECT COALESCE(task, 'other') AS task, COUNT(*) AS calls, SUM(cost_usd) AS cost FROM llm_usage
            WHERE substr(created_at, 1, 7) = ? GROUP BY COALESCE(task, 'other') ORDER BY cost DESC""", (month,)).fetchall()
        return [dict(r) for r in rows]

    def timed_out_reviews(self) -> List[Dict[str, Any]]:
        """Renders whose review timed out within TIMEOUT_HOLD_DAYS: they wait for Resend or Reject."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=TIMEOUT_HOLD_DAYS)).isoformat()
        with self._conn() as conn:
            rows = conn.execute("SELECT run_id, topic_id, updated_at FROM runs WHERE status = 'timeout' AND updated_at >= ? "
                                "ORDER BY updated_at DESC", (cutoff,)).fetchall()
        return [dict(r) for r in rows]

    def list_recent_runs(self, limit: int = 5) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                # rowid breaks ties: two runs created in the same millisecond sorted in an undefined order (a
                # flaky dashboard test on Windows' clock, 2026-10-24).
                "SELECT run_id, topic_id, status, error, parent_run_id, content_format, created_at, updated_at, master_video_path FROM runs ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def reopen_approval(self, run_id: str) -> bool:
        """Puts a timed-out approval back to pending so the run can be reviewed again."""
        with self._conn() as conn:
            cur = conn.execute("""
            UPDATE approvals SET decision = 'pending', decision_timestamp = NULL, decided_by = NULL, created_at = ?
            WHERE run_id = ? AND decision = 'timeout'
            """, (_now(), run_id))
            return cur.rowcount == 1

    def reset_approval(self, run_id: str) -> None:
        """A run made again needs a new decision: the earlier one was for the version it replaces
        (2026-10-24: a re-render read the earlier Approve 8 ms after its new card was sent)."""
        with self._conn() as conn:
            conn.execute("UPDATE approvals SET decision = 'pending', decision_timestamp = NULL, decided_by = NULL "
                         "WHERE run_id = ?", (run_id,))

    def get_approval(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM approvals WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def list_pending_approvals(self) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("""
            SELECT a.run_id, a.topic_id, a.created_at, a.telegram_message_id, r.script_payload_json
            FROM approvals a LEFT JOIN runs r ON a.run_id = r.run_id
            WHERE a.decision = 'pending'
            ORDER BY a.rowid DESC
            """).fetchall()
        return [
            {
                "run_id": r["run_id"],
                "topic_id": r["topic_id"],
                "created_at": r["created_at"],
                "telegram_message_id": r["telegram_message_id"],
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

    # A video's own receipts: every channel but the companions ("upload-post:story", "facebook:cover", ...), the dry
    # run and the coordinator's placeholder. One run counts once whatever the number of platforms or retries. A
    # published documentary (its upload goes through upload-post too) counts each of its movements once: the
    # track was heard in a published video, which is what a published use means.
    REEL_RECEIPT_EXCLUDED = ("dry-run", "coordinator")

    def record_music_selection(self, run_id: str, tracks: list[dict[str, Any]]) -> None:
        """The library tracks (each {"file", "sha256", "mood"}) this run's bed was made from, replacing what an
        earlier bed of the same run recorded (one transaction). An empty list clears it (drone, generated score)."""
        now = _now()
        with self._conn() as conn:
            conn.execute("DELETE FROM music_selections WHERE run_id = ?", (run_id,))
            for position, track in enumerate(tracks):
                conn.execute("INSERT OR IGNORE INTO music_selections (run_id, file, sha256, mood, position, recorded_at)"
                             " VALUES (?, ?, ?, ?, ?, ?)",
                             (run_id, track["file"], track.get("sha256"), track.get("mood"), position, now))

    MUSIC_BACKFILL_KEY = "music_selections_backfilled"

    def backfill_music_selections(self, output_dir: Path) -> int:
        """Once per database (QF-84): the selection of every run made before music_selections existed, read from
        its folder's music_selection.json (library tracks only), so a Reel published before this release, or one
        waiting for review at the release, counts its track once its receipts say so. A run that already has rows,
        or no readable selection, is left alone; nothing is ever counted from the old `uses`. Returns the runs filled."""
        from .credits import sha256_file
        if self.get_setting(self.MUSIC_BACKFILL_KEY):
            return 0
        with self._conn() as conn:
            runs = [r["run_id"] for r in conn.execute(
                "SELECT run_id FROM runs WHERE run_id NOT IN (SELECT run_id FROM music_selections)")]
        filled = 0
        for run_id in runs:
            try:
                music = json.loads((Path(output_dir) / run_id / "music_selection.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(music, dict) or music.get("source") != "library":
                continue
            tracks = [t for t in music.get("tracks") or [] if isinstance(t, dict) and t.get("file")] or (
                [{"file": music["file"], "mood": music.get("mood"), "path": music.get("path")}] if music.get("file") else [])
            rows = [{"file": t["file"], "mood": t.get("mood"),
                     "sha256": (sha256_file(Path(t["path"])) or None) if t.get("path") else None} for t in tracks]
            if rows:
                self.record_music_selection(run_id, rows)
                filled += 1
        self.set_setting(self.MUSIC_BACKFILL_KEY, _now())
        return filled

    def music_published_uses(self) -> dict[str, int]:
        """Published uses per library file: the runs whose bed used it and whose video (a Reel, or a documentary:
        each of its movements) has a confirmed publication (status 'published' on a video channel, a retry or a
        human's posted confirmation). Picks, renders, dry runs,
        simulated, failed, unknown, scheduled, rejected and regenerated runs count nothing; a retry or a second
        platform of the same Reel does not count twice. Derived at read time, so there is no counter to drift."""
        excluded = self.REEL_RECEIPT_EXCLUDED
        with self._conn() as conn:
            rows = conn.execute(f"""
            SELECT m.file AS file, COUNT(DISTINCT m.run_id) AS n FROM music_selections m
            WHERE EXISTS (SELECT 1 FROM publications p WHERE p.run_id = m.run_id AND p.status = 'published'
                          AND p.channel NOT LIKE '%:%' AND p.channel NOT IN ({','.join('?' * len(excluded))}))
            GROUP BY m.file""", excluded).fetchall()
        return {r["file"]: int(r["n"]) for r in rows}

    def list_publications(self, run_id: str) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM publications WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
        return [dict(r) for r in rows]

    # --------------------------------------------------------- scheduled posts
    def add_scheduled_post(self, run_id: str, kind: str, due_at: Optional[str], files: List[str],
                           status: str = "pending") -> int:
        now = _now()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._check_topic_booking(conn, run_id, kind, due_at, status)
            cur = conn.execute("""
            INSERT INTO scheduled_posts (run_id, kind, due_at, status, files_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""", (run_id, kind, due_at, status, json.dumps(files), now, now))
            return cur.lastrowid

    def update_scheduled_post(self, post_id: int, status: str, due_at: Optional[str] = None,
                              detail: Optional[Dict[str, Any]] = None, files: list[str] | None = None):
        """`files`: the post's image files, when they are drawn after booking (a carousel at its slot, QF-81)."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT run_id, kind, due_at FROM scheduled_posts WHERE id = ?", (post_id,)).fetchone()
            if row and due_at and due_at != row["due_at"]:   # a move; a status change on its own day is never refused
                self._check_topic_booking(conn, row["run_id"], row["kind"], due_at, status)
            conn.execute("""
            UPDATE scheduled_posts SET status = ?, due_at = COALESCE(?, due_at),
                   detail_json = COALESCE(?, detail_json), files_json = COALESCE(?, files_json),
                   updated_at = ? WHERE id = ?""",
                         (status, due_at, json.dumps(detail, default=str) if detail is not None else None,
                          json.dumps(files) if files is not None else None, _now(), post_id))

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
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "telegram_transport" in tables:
                for row in conn.execute("SELECT value FROM telegram_transport WHERE namespace='outbound'"):
                    mids.add(int(json.loads(row[0])))
            return sorted(mids, reverse=True)

    def record_issue(self, issue_id: str, critique: str, run_id: Optional[str] = None, user_id: Optional[str] = None, evidence_path: Optional[str] = None) -> Dict[str, Any]:
        now = _now()
        with self._conn() as conn:
            conn.execute("""
            INSERT INTO issues (id, run_id, critique, user_id, evidence_path, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'open', ?)
            """, (issue_id, run_id, critique, str(user_id) if user_id else None, str(evidence_path) if evidence_path else None, now))
        return {"id": issue_id, "run_id": run_id, "critique": critique, "created_at": now}

    def resolve_issue(self, issue_id: str, diagnosis: str) -> bool:
        """Closes an open issue with what was found and done; an already closed one is left as it is."""
        with self._conn() as conn:
            return conn.execute("UPDATE issues SET status='fixed', diagnosis=? WHERE id=? AND status='open'",
                                (diagnosis, issue_id)).rowcount == 1

    def count_issues(self, status: str) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM issues WHERE status=?", (status,)).fetchone()[0]

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

    # ------------------------------------------------------------ Telegram v2 Card Store & CAS
    def put_telegram_card(self, card_id: str, topic_id: int, message_id: int, card_kind: str,
                          payload: Dict[str, Any], expires_at: Optional[str] = None,
                          revision: int = 1) -> Dict[str, Any]:
        """Stores or replaces a live card representation in SQLite."""
        now = _now()
        payload_json = json.dumps(payload, ensure_ascii=False)
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO telegram_cards (card_id, topic_id, message_id, revision, card_kind, payload, expires_at, updated_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(card_id) DO UPDATE SET
                    topic_id = excluded.topic_id,
                    message_id = excluded.message_id,
                    revision = excluded.revision,
                    card_kind = excluded.card_kind,
                    payload = excluded.payload,
                    expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at
            """, (card_id, int(topic_id), int(message_id), int(revision), card_kind, payload_json, expires_at, now, now))
        return self.get_telegram_card(card_id)

    def get_telegram_card(self, card_id: str) -> Optional[Dict[str, Any]]:
        """Fetches a card record with deserialized payload."""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM telegram_cards WHERE card_id = ?", (card_id,)).fetchone()
            if not row:
                return None
            data = dict(row)
            try:
                data["payload"] = json.loads(data["payload"])
            except Exception:
                pass
            return data

    def list_telegram_cards(self, topic_id: Optional[int] = None, card_kind: Optional[str] = None) -> List[Dict[str, Any]]:
        """Lists cards, optionally filtered by topic or card kind."""
        query = "SELECT * FROM telegram_cards WHERE 1=1"
        params = []
        if topic_id is not None:
            query += " AND topic_id = ?"
            params.append(int(topic_id))
        if card_kind is not None:
            query += " AND card_kind = ?"
            params.append(card_kind)
        query += " ORDER BY updated_at DESC"
        with self._conn() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                try:
                    d["payload"] = json.loads(d["payload"])
                except Exception:
                    pass
                results.append(d)
            return results

    def cas_update_telegram_card(self, card_id: str, old_revision: int, new_payload: Dict[str, Any],
                                 message_id: Optional[int] = None, expires_at: Optional[str] = None) -> bool:
        """Atomically updates a card's payload and increments revision iff revision == old_revision."""
        now = _now()
        payload_json = json.dumps(new_payload, ensure_ascii=False)
        with self._conn() as conn:
            if message_id is not None:
                cur = conn.execute("""
                    UPDATE telegram_cards
                    SET revision = revision + 1, payload = ?, message_id = ?, expires_at = ?, updated_at = ?
                    WHERE card_id = ? AND revision = ?
                """, (payload_json, int(message_id), expires_at, now, card_id, int(old_revision)))
            else:
                cur = conn.execute("""
                    UPDATE telegram_cards
                    SET revision = revision + 1, payload = ?, expires_at = COALESCE(?, expires_at), updated_at = ?
                    WHERE card_id = ? AND revision = ?
                """, (payload_json, expires_at, now, card_id, int(old_revision)))
            return cur.rowcount > 0

    def delete_telegram_card(self, card_id: str) -> bool:
        """Deletes a card record from the store."""
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM telegram_cards WHERE card_id = ?", (card_id,))
            return cur.rowcount > 0

    def sweep_expired_telegram_cards(self, max_age_hours: float = 40.0) -> List[Dict[str, Any]]:
        """Returns cards that have passed their expires_at OR exceed max_age_hours since created_at."""
        from datetime import timedelta
        now_dt = datetime.now(timezone.utc)
        now_str = now_dt.isoformat()
        cutoff_str = (now_dt - timedelta(hours=max_age_hours)).isoformat()
        with self._conn() as conn:
            # The clean chat's layout rows (kind "layout", UI-01) are never swept: their panels are re-posted
            # in order before 48 h instead, and a swept row would leave its message in the chat.
            rows = conn.execute("""
                SELECT * FROM telegram_cards
                WHERE card_kind <> 'layout'
                  AND ((expires_at IS NOT NULL AND expires_at <= ?)
                   OR (created_at <= ?))
            """, (now_str, cutoff_str)).fetchall()
            expired = []
            for r in rows:
                d = dict(r)
                try:
                    d["payload"] = json.loads(d["payload"])
                except Exception:
                    pass
                expired.append(d)
            if expired:
                ids = [d["card_id"] for d in expired]
                placeholders = ",".join("?" * len(ids))
                conn.execute(f"DELETE FROM telegram_cards WHERE card_id IN ({placeholders})", ids)
            return expired
