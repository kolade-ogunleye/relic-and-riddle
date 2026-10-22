from pathlib import Path
import json
import sqlite3

from src.backup import backup_run


def test_backup_run_copies_gold_assets_and_dispute_summary(tmp_path):
    run_dir = tmp_path / "run_01_test_123456"
    run_dir.mkdir()
    
    # Create mock gold assets
    master_video = run_dir / "01_test_master.mp4"
    master_video.write_bytes(b"video_bytes")
    
    cover = run_dir / "thumbnail_cover.jpg"
    cover.write_bytes(b"cover_bytes")
    
    script_file = run_dir / "verified_script.json"
    script_file.write_text(json.dumps({
        "title": "Ancient Relic",
        "hook_sentence": "A strange discovery underground.",
        "narration_script": "A strange discovery underground that baffled scientists.",
        "sources": [{"title": "UNESCO", "url": "https://whc.unesco.org/en/list/1"}]
    }), encoding="utf-8")
    
    prov_file = run_dir / "provenance.json"
    prov_file.write_text(json.dumps({
        "topic_id": "01_test",
        "assets": [{
            "file": "image_01.jpg",
            "sha256": "abc123sha",
            "artist": "Jane Doe",
            "license": "CC BY 4.0",
            "source_url": "https://commons.wikimedia.org/wiki/File:Test.jpg",
            "license_url": "https://creativecommons.org/licenses/by/4.0"
        }]
    }), encoding="utf-8")
    
    stories_dir = run_dir / "stories"
    stories_dir.mkdir()
    (stories_dir / "story_01.jpg").write_bytes(b"story_bytes")

    # Create mock database
    db_file = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE t (id INT)")
    conn.commit()
    conn.close()

    dest_dir = tmp_path / "backup_dest"
    
    res = backup_run(run_dir, db_path=db_file, dest_base=dest_dir)
    assert res["status"] == "staged" and res["cloud_status"] == "unverified"
    
    archive_path = Path(res["archive_path"])
    assert (archive_path / "01_test_master.mp4").is_file()
    assert (archive_path / "thumbnail_cover.jpg").is_file()
    assert (archive_path / "stories" / "story_01.jpg").is_file()
    assert (archive_path / "00_DISPUTE_SUMMARY.txt").is_file()
    
    summary_txt = (archive_path / "00_DISPUTE_SUMMARY.txt").read_text(encoding="utf-8")
    assert "Ancient Relic" in summary_txt
    assert "Jane Doe" in summary_txt
    assert "CC BY 4.0" in summary_txt
    assert "UNESCO" in summary_txt
    
    assert res["db_snapshot"] is not None
    assert Path(res["db_snapshot"]).is_file()
