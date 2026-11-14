"""QF-96: a test that configures logging leaves no root handler behind on a stream pytest has closed.

The two tests run in file order on one worker. The first configures logging as main.main does, under capsys (as
test_script_library does), so the StreamHandler binds to that test's capture stream; the second flushes every root
handler, as test_integrity_gates::test_logs_persist_to_a_masked_rotating_file does."""

import logging


def test_a_test_configures_pipeline_logging(monkeypatch, tmp_path, capsys):
    from src import config, ops
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    ops.setup_logging()
    logging.getLogger("qf96").warning("configured")


def test_the_next_test_finds_no_handler_on_a_closed_stream():
    root = logging.getLogger()
    for handler in root.handlers:
        handler.flush()  # raised "ValueError: I/O operation on closed file" before QF-96
    assert not getattr(root, "_pipeline_configured", False)
