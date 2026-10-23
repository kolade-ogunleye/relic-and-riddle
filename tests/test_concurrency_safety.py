"""Native objects used by parallel stages are never shared between threads (audit, 2026-10-23)."""
import threading


def test_each_thread_gets_its_own_face_detector():
    # Face blurring runs images in parallel; one shared OpenCV detector is not thread-safe.
    from src import privacy
    seen = []
    t = threading.Thread(target=lambda: seen.append(privacy._get_cascade()))
    t.start(); t.join()
    mine = privacy._get_cascade()
    assert mine is privacy._get_cascade()  # reused within a thread
    assert seen and seen[0] is not mine     # not shared across threads (kept alive, so no id reuse)


def test_a_hung_ffmpeg_ends_the_render_with_an_error(monkeypatch):
    import subprocess
    import pytest
    from src import video_composer
    def hang(*a, **k):
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=k.get("timeout"))
    monkeypatch.setattr(video_composer.subprocess, "run", hang)
    with pytest.raises(video_composer.RenderError, match="did not finish"):
        video_composer._run_ffmpeg(["ffmpeg", "-i", "x"])


def test_a_run_whose_process_ended_is_cleared_at_startup_and_a_live_one_is_kept(db, monkeypatch):
    # A crashed worker left its run "producing", blocking the next deployment stop (dev, 2026-10-23).
    import json
    import main
    from src import process_state
    db.create_run("dead", "01_topic")
    db.update_run("dead", "producing")
    db.create_run("live", "02_topic")
    db.update_run("live", "producing")
    db.create_run("young", "03_topic")
    db.update_run("young", "producing")
    with db._conn() as conn:  # "dead" and "live" started an hour ago; "young" just now
        conn.execute("UPDATE runs SET updated_at = '2026-01-01T00:00:00+00:00' WHERE run_id IN ('dead', 'live')")
    db.set_setting("active_progress", json.dumps({"run_id": "live", "owner_pid": 4242, "owner_identity": "id-4242"}))
    monkeypatch.setattr(process_state, "identity", lambda pid: "id-4242" if pid == 4242 else None)
    statuses = []
    engine = type("T", (), {"record_status": lambda self, tid, status, run_id=None: statuses.append((tid, status))})()
    assert main.reconcile_interrupted_runs(db, engine) == ["dead"]
    assert db.get_run("dead")["status"] == "failed" and "Interrupted" in db.get_run("dead")["error"]
    assert db.get_run("live")["status"] == "producing" and db.get_run("young")["status"] == "producing"
    assert statuses == [("01_topic", "queued")]
