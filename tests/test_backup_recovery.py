import json
import os
import sqlite3
import zipfile
from pathlib import Path

import pytest

from src.backup_crypto import BackupError, encrypt_file, decrypt_file, sha256_file
from src.backup_snapshot import create_snapshot, restore_snapshot, relocated


def recovery_root(tmp_path):
    root = tmp_path / "source"
    (root / "data").mkdir(parents=True)
    (root / "output" / "run_a").mkdir(parents=True)
    (root / "assets" / "topic").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "main.py").write_text("# application code\n")
    (root / ".env").write_text("TELEGRAM_BOT_TOKEN=secret-must-not-escape")
    (root / "assets" / "topic" / "photo.jpg").write_bytes(b"source image and rights evidence")
    video = root / "output" / "run_a" / "a_master.mp4"
    video.write_bytes(b"video")
    (video.parent / "provenance.json").write_text(json.dumps({"path": str(video), "sha256": sha256_file(video)}))
    db = sqlite3.connect(root / "data" / "pipeline.db")
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE runs(run_id TEXT PRIMARY KEY,status TEXT,master_video_path TEXT);
        CREATE TABLE publications(id INTEGER PRIMARY KEY,run_id TEXT,status TEXT,response_payload_json TEXT);
        CREATE TABLE llm_usage(id INTEGER PRIMARY KEY,cost_usd REAL);
        CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);
    """)
    db.execute("INSERT INTO runs VALUES('run_a','published',?)", (str(video),))
    db.execute("INSERT INTO publications VALUES(1,'run_a','published','{\"platform_id\":\"keep-me\"}')")
    db.execute("INSERT INTO llm_usage VALUES(1,2.75)")
    db.execute("INSERT INTO settings VALUES('human_approver','123')")
    db.commit()  # Keep the connection alive: the online snapshot must include WAL state.
    return root, db


def test_encrypted_snapshot_restore_preserves_history_and_relocates_without_secrets(tmp_path):
    root, source_db = recovery_root(tmp_path)
    try:
        key = os.urandom(32)
        job = create_snapshot(root, tmp_path / "spool", key, environment="production")
        assert job["state"] == "staged" and not job.get("receipt")
        encrypted = Path(job["directory"]) / "snapshot.enc"
        assert b"secret-must-not-escape" not in encrypted.read_bytes()
        destination = tmp_path / "restored"
        report = restore_snapshot(encrypted, destination, key, snapshot_id=job["snapshot_id"])
        assert report["state"] == "restore-tested" and report["revalidation_required"]
        assert not (destination / ".env").exists()
        assert (destination / ".restore-isolated").is_file()
        assert (destination / ".recovery" / "main.original.py").read_bytes() == (root / "main.py").read_bytes()
        import subprocess
        import sys
        blocked = subprocess.run([sys.executable, str(destination / "main.py"), "daemon"], capture_output=True, text=True)
        assert blocked.returncode != 0 and "Isolated recovery copy" in blocked.stderr
        assert (destination / "assets" / "topic" / "photo.jpg").read_bytes().startswith(b"source image")
        with sqlite3.connect(destination / "data" / "pipeline.db") as db:
            assert db.execute("SELECT status,master_video_path FROM runs").fetchone() == (
                "published", str(destination / "output" / "run_a" / "a_master.mp4"))
            assert db.execute("SELECT SUM(cost_usd) FROM llm_usage").fetchone()[0] == 2.75
            assert db.execute("SELECT response_payload_json FROM publications").fetchone()[0] == '{"platform_id":"keep-me"}'
            assert db.execute("SELECT value FROM settings").fetchone()[0] == "123"
        original = destination / ".recovery" / "original-evidence" / "output" / "run_a" / "provenance.json"
        assert json.loads(original.read_text())["path"] == str(root / "output" / "run_a" / "a_master.mp4")
        assert json.loads((destination / "output" / "run_a" / "provenance.json").read_text())["path"] == str(
            destination / "output" / "run_a" / "a_master.mp4")
        assert (root / ".env").read_text().endswith("secret-must-not-escape")
    finally:
        source_db.close()


@pytest.mark.parametrize("damage", ["wrong-key", "tamper", "truncated", "wrong-context"])
def test_cipher_rejects_corruption_and_wrong_identity_without_leaving_plaintext(tmp_path, damage):
    plain, encrypted, output = (tmp_path / name for name in ("plain", "encrypted", "output"))
    plain.write_bytes(os.urandom(1200000))
    key = os.urandom(32)
    encrypt_file(plain, encrypted, key, context="snapshot-a")
    blob = encrypted.read_bytes()
    if damage == "tamper":
        encrypted.write_bytes(blob[:100] + bytes([blob[100] ^ 1]) + blob[101:])
    elif damage == "truncated":
        encrypted.write_bytes(blob[:-32])
    with pytest.raises(BackupError, match="authentication"):
        decrypt_file(encrypted, output, os.urandom(32) if damage == "wrong-key" else key,
                     context="snapshot-b" if damage == "wrong-context" else "snapshot-a")
    assert not output.exists()


def test_decrypt_never_deletes_an_existing_target(tmp_path):
    encrypted, target = tmp_path / "invalid.enc", tmp_path / "existing"
    encrypted.write_bytes(b"not a backup")
    target.write_bytes(b"keep me")
    with pytest.raises(BackupError):
        decrypt_file(encrypted, target, os.urandom(32), context="a")
    assert target.read_bytes() == b"keep me"


def test_restore_never_overwrites_existing_destination(tmp_path):
    target = tmp_path / "live"
    target.mkdir()
    (target / "production.db").write_bytes(b"live state")
    with pytest.raises(BackupError, match="must not exist"):
        restore_snapshot(tmp_path / "missing.enc", target, os.urandom(32), snapshot_id="x")
    assert (target / "production.db").read_bytes() == b"live state"


def test_unsafe_archive_paths_are_rejected_before_extraction(tmp_path):
    key = os.urandom(32)
    archive, encrypted = tmp_path / "evil.zip", tmp_path / "evil.enc"
    manifest = {"schema": 1, "snapshot_id": "evil", "files": [
        {"path": "../escape", "size": 1, "sha256": "bad"},
        {"path": "data/pipeline.db", "size": 1, "sha256": "bad"}]}
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../escape", b"x")
        bundle.writestr("data/pipeline.db", b"x")
        bundle.writestr("backup-manifest.json", json.dumps(manifest))
    encrypt_file(archive, encrypted, key, context="evil")
    with pytest.raises(BackupError, match="Unsafe path"):
        restore_snapshot(encrypted, tmp_path / "target", key, snapshot_id="evil")
    assert not (tmp_path / "target").exists()
    assert not (tmp_path / "escape").exists()


def test_windows_path_relocation_is_host_independent():
    value = {r"C:\old\production\assets\a.jpg": {"path": r"C:\old\production\output\a.mp4"},
             "unrelated": r"C:\old\production-other\secret.txt"}
    updated = relocated(value, r"C:\old\production", Path("/new/production"))
    assert str(Path("/new/production/assets/a.jpg")) in updated
    assert updated["unrelated"] == value["unrelated"]


def test_restore_marker_blocks_runtime_and_direct_publishers(monkeypatch, tmp_path):
    from src import config, publisher
    import main
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    (tmp_path / ".restore-isolated").write_text("isolated")
    assert main.main(["daemon"]) == 2
    with pytest.raises(publisher.PublishFailed, match="isolated"):
        publisher.UploadPostPublisher(api_key="test", user="test").publish({})
