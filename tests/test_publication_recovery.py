import subprocess
import sys

import pytest

from src.publication_lock import publication_lock


def test_live_owner_cannot_be_recovered(db):
    db.create_run("recover", "01_topic")
    db.update_run("recover", "approved")
    assert db.claim_publication("recover")
    with publication_lock(db.db_path, "recover"):
        with pytest.raises(ValueError, match="owner still holds"):
            db.recover_interrupted_publication("recover")
    assert db.get_run("recover")["status"] == "publishing"
    assert db.recover_interrupted_publication("recover")
    assert db.get_run("recover")["status"] == "publish_unknown"
    assert db.get_topic_by_id("01_topic")["status"] == "publish_unknown"
    assert not db.claim_publication("recover")
    assert not db.recover_interrupted_publication("recover")


def test_process_exit_releases_lock(db):
    code = "from src.publication_lock import publication_lock; import sys, os; " \
           "lock=publication_lock(sys.argv[1], 'crashed'); lock.__enter__(); os._exit(0)"
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    subprocess.run([sys.executable, "-c", code, str(db.db_path)], check=True, timeout=15, cwd=str(root))
    with publication_lock(db.db_path, "crashed"):
        pass
