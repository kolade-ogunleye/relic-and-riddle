"""The one-time production reset (operator, 2026-10-25): one run left, numbered 1; only its records kept;
every other topic queued; everything removed moved into a snapshot; and it lists before it changes."""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("production_reset", Path(__file__).resolve().parent.parent / "scripts" / "production_reset.py")
reset = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reset)

KEEP = "run_kailasa_temple_monolith_1792854124"


def _seed(db, tmp_path):
    from src.audience import ensure_schema
    ensure_schema(db)
    topics = [t["id"] for t in db.list_all_topics()]
    k_topic, other = topics[0], topics[1]
    with db._conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS findings (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, topic_id TEXT, source TEXT, klass TEXT, detail TEXT, created_at TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, title TEXT)")
    for i, run_id in enumerate(["run_old_1", "run_old_2", KEEP, "run_after_1"], 1):
        db.create_run(run_id, k_topic if run_id == KEEP else other)
        with db._conn() as c:
            c.execute("UPDATE runs SET created_at = ?, status = ? WHERE run_id = ?",
                      (f"2026-10-24T1{i}:00:00+00:00", "published" if run_id == KEEP else "failed", run_id))
    with db._conn() as c:
        c.execute("INSERT INTO approvals (run_id, topic_id, decision, decision_timestamp, created_at) VALUES (?, ?, 'approved', '2026-10-24T13:20:00+00:00', '2026-10-24T13:15:00+00:00')", (KEEP, k_topic))
        c.execute("INSERT INTO approvals (run_id, topic_id, decision, created_at) VALUES ('run_old_1', ?, 'rejected', '2026-10-24T11:10:00')", (other,))
        for run_id, channel, status in ((KEEP, "dry-run", "simulated"), ("run_old_1", "dry-run", "simulated"), (KEEP, "upload-post", "published"),
                                        (KEEP, "upload-post:story", "unknown"), (KEEP, "upload-post:carousel", "simulated")):
            c.execute("INSERT INTO publications (topic_id, run_id, channel, status, created_at) VALUES (?, ?, ?, ?, '2026-10-24T23:00:00')", (k_topic, run_id, channel, status))
        for t, cost in (("2026-10-24T11:30:00+00:00", 0.5), ("2026-10-24T13:05:00+00:00", 0.1), ("2026-10-24T13:18:00+00:00", 0.2), ("2026-10-24T14:30:00+00:00", 0.4)):
            c.execute("INSERT INTO llm_usage (model, cost_usd, created_at) VALUES ('gemini', ?, ?)", (cost, t))
        c.execute("INSERT INTO media_usage (provider, item, cost_usd, created_at) VALUES ('kling', 'pilot:x', 0.84, '2026-10-20T18:00:00')")
        c.execute("INSERT INTO media_usage (provider, item, cost_usd, created_at) VALUES ('kling', ?, 0.84, '2026-10-24T13:10:00+00:00')", (f"{KEEP}/motion_01",))
        for payload, t in (("qrender:x", "2026-10-24T11:00:00"), ("approve:3", "2026-10-24T13:20:00"), ("promote:3", "2026-10-24T21:00:00"),
                           ("approve:1", "2026-10-24T11:10:00"), ("retrypost:2", "2026-10-25T01:09:00")):
            c.execute("INSERT INTO audit_log (user_id, action, payload, status, created_at) VALUES ('1', 'callback', ?, 'allowed', ?)", (payload, t))
        for kind in ("reel", "story", "carousel"):
            c.execute("INSERT INTO scheduled_posts (run_id, kind, status, created_at, updated_at) VALUES (?, ?, 'published', 'x', 'x')", (KEEP, kind))
        c.execute("INSERT INTO findings (run_id, detail, created_at) VALUES ('run_old_2', 'x', 'x')")
        c.execute("INSERT INTO findings (run_id, detail, created_at) VALUES (?, 'kept', 'x')", (KEEP,))
        c.execute("INSERT INTO alerts (text, created_at) VALUES ('old notice', 'x')")
        c.execute("INSERT INTO proposals (id, title) VALUES ('imp_1', 'from old runs')")
        c.execute("INSERT INTO audience_metrics VALUES ('run_old_1', 'ig', 'd1', 'x', '{}', 'x')")
    for key in ("daemon_schedule", "scheduler_paused", "operator_job:abc", "run_mode:run_old_1", f"run_mode:{KEEP}", "busy:the production run"):
        db.set_setting(key, "x" if not key.startswith("busy") else "")
    with db._conn() as c:
        c.execute("UPDATE topics SET status = 'rejected', priority = 5 WHERE id = ?", (other,))
        c.execute("UPDATE topics SET status = 'published' WHERE id = ?", (k_topic,))
    app = tmp_path / "app"
    for name in ("run_old_1", "run_old_2", KEEP, "run_after_1", "r99"):
        (app / "output" / name).mkdir(parents=True)
        (app / "output" / name / "f.txt").write_text("x")
    (app / "archive" / "validation").mkdir(parents=True)
    (app / "archive" / "backups").mkdir(parents=True)
    (app / "docs" / "issues").mkdir(parents=True)
    (app / "logs").mkdir()
    (app / "logs" / "pipeline.log").write_text("old")
    (app / "data" / "publication_locks").mkdir(parents=True)
    (app / "data" / "publication_locks" / "run_old_1.lock").write_text("")
    (app / "data" / "publication_locks" / f"{KEEP}.lock").write_text("")
    (app / "assets" / "topic").mkdir(parents=True)
    import shutil
    shutil.copy(db.db_path, app / "data" / "pipeline.db")
    return app, k_topic, other


def test_listing_changes_nothing_and_apply_leaves_one_run_numbered_one(db, tmp_path, monkeypatch, capsys):
    app, k_topic, other = _seed(db, tmp_path)
    before = (app / "data" / "pipeline.db").read_bytes()
    assert reset.main(["--app", str(app)]) == 0
    assert (app / "data" / "pipeline.db").read_bytes() == before and (app / "output" / "run_old_1").exists()
    assert "Listed only" in capsys.readouterr().out

    monkeypatch.setattr(reset, "running_processes", lambda app: [])
    assert reset.main(["--app", str(app), "--apply"]) == 0
    c = sqlite3.connect(app / "data" / "pipeline.db")
    assert c.execute("SELECT rowid, run_id FROM runs").fetchall() == [(1, KEEP)]
    assert [r[0] for r in c.execute("SELECT run_id FROM approvals")] == [KEEP]
    assert c.execute("SELECT id, channel, status FROM publications ORDER BY id").fetchall() == [(1, "upload-post", "published"), (2, "upload-post:story", "unknown")]
    assert c.execute("SELECT seq FROM sqlite_sequence WHERE name = 'publications'").fetchone() == (2,)
    assert c.execute("SELECT id, cost_usd FROM llm_usage ORDER BY id").fetchall() == [(1, 0.1), (2, 0.2)]           # the run's window only
    assert c.execute("SELECT id, item FROM media_usage").fetchall() == [(1, f"{KEEP}/motion_01")]
    assert sorted(r[0] for r in c.execute("SELECT payload FROM audit_log")) == ["approve:3", "promote:3", "retrypost:2"]
    assert c.execute("SELECT detail FROM findings").fetchall() == [("kept",)]
    assert [r[0] for r in c.execute("SELECT id FROM scheduled_posts ORDER BY id")] == [1, 2, 3]
    for table in ("alerts", "proposals", "audience_metrics"):
        assert c.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,), table
    keys = {r[0] for r in c.execute("SELECT key FROM settings")}
    assert {"daemon_schedule", "scheduler_paused", f"run_mode:{KEEP}"} <= keys and not keys & {"operator_job:abc", "run_mode:run_old_1"}
    assert c.execute("SELECT status, priority FROM topics WHERE id = ?", (other,)).fetchone() == ("queued", 5)
    assert c.execute("SELECT status FROM topics WHERE id = ?", (k_topic,)).fetchone() == ("published",)
    assert {r[0] for r in c.execute("SELECT status FROM topics")} == {"queued", "published"}
    # files: only the kept run's folder stays; everything else is in the snapshot
    assert sorted(p.name for p in (app / "output").iterdir()) == [KEEP]
    snap = next((app / "archive").glob("reset-*"))
    assert sorted(p.name for p in (snap / "output").iterdir()) == ["r99", "run_after_1", "run_old_1", "run_old_2"]
    assert (snap / "pipeline.db").exists() and (snap / "archive" / "validation").exists() and (app / "archive" / "backups").exists()
    assert (snap / "logs" / "pipeline.log").exists() and not (app / "docs" / "issues").exists()
    assert sorted(p.name for p in (app / "data" / "publication_locks").iterdir()) == [f"{KEEP}.lock"]
    assert (app / "assets" / "topic").exists()
    record = json.loads((snap / "reset.json").read_text(encoding="utf-8"))
    assert record["kept_run"] == KEEP and len(record["deleted_runs"]) == 3
    old = sqlite3.connect(snap / "pipeline.db")
    assert old.execute("SELECT COUNT(*) FROM runs").fetchone() == (4,)                                            # reversible


def test_apply_refuses_while_the_pipeline_runs(db, tmp_path, monkeypatch, capsys):
    app, *_ = _seed(db, tmp_path)
    monkeypatch.setattr(reset, "running_processes", lambda app: ["4242 python main.py daemon"])
    assert reset.main(["--app", str(app), "--apply"]) == 1
    assert "Refused" in capsys.readouterr().out and (app / "output" / "run_old_1").exists()
    assert not list((app / "archive").glob("reset-*"))


def test_an_unknown_run_is_never_reset(db, tmp_path):
    app, *_ = _seed(db, tmp_path)
    with pytest.raises(SystemExit):
        reset.main(["--app", str(app), "--keep", "run_does_not_exist"])
