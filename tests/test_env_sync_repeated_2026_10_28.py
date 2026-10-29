"""A setting written twice in .env, or under its old name beside the new one, is pointed out at start, and a
run's worker takes .env as it is now (prod, 2026-10-28: one of two IMAGES_ALLOW_SHARE_ALIKE lines was turned
on, the other stayed in effect, and the daemon's start-up values reached every run until a restart)."""

import os

from src import config, env_sync


def test_a_setting_written_twice_names_the_line_that_counts(tmp_path):
    env = tmp_path / ".env"
    env.write_text("IMAGES_ALLOW_SHARE_ALIKE=false\nX=1\nIMAGES_ALLOW_SHARE_ALIKE=true\n", encoding="utf-8")
    assert env_sync.repeated(env) == ["IMAGES_ALLOW_SHARE_ALIKE is on lines 1, 3: the first one counts; in effect: false"]


def test_the_old_name_beside_the_new_one_is_pointed_out(tmp_path):
    env = tmp_path / ".env"
    env.write_text("BEAT_IMAGES_ALLOW_SHARE_ALIKE=true\nIMAGES_ALLOW_SHARE_ALIKE=false\n", encoding="utf-8")
    assert env_sync.repeated(env) == [
        "BEAT_IMAGES_ALLOW_SHARE_ALIKE (line 1) does nothing while IMAGES_ALLOW_SHARE_ALIKE is set"]


def test_a_secret_written_twice_never_shows_its_value(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=sk-one\nOPENAI_API_KEY=sk-two\n", encoding="utf-8")
    notes = env_sync.repeated(env)
    assert notes and "sk-" not in " ".join(notes)


def test_the_settings_message_lists_them(tmp_path):
    env, example = tmp_path / ".env", tmp_path / ".env.example"
    env.write_text("A_FLAG=false\nA_FLAG=true\n", encoding="utf-8")
    example.write_text("A_FLAG=false\n", encoding="utf-8")
    text = env_sync.sync_on_start(env, example)
    assert "Written twice in .env" in text and "A_FLAG is on lines 1, 2" in text


def test_a_worker_takes_an_env_edit_without_a_restart(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("RR_TEST_FLAG=false\nRR_TEST_GONE=1\n", encoding="utf-8")
    monkeypatch.delenv("RR_TEST_FLAG", raising=False)
    monkeypatch.delenv("RR_TEST_GONE", raising=False)
    monkeypatch.setattr(config, "ENV_FILE_KEYS", set())
    try:
        config._load_env_file(env)
        assert os.environ["RR_TEST_FLAG"] == "false"
        env.write_text("RR_TEST_FLAG=true\nRR_TEST_FLAG=false\nRR_TEST_NEW=x\n", encoding="utf-8")
        worker = config.worker_environment(env)
        assert worker["RR_TEST_FLAG"] == "true"            # the edit; the first line wins, as at start
        assert worker["RR_TEST_NEW"] == "x"                # a line added since
        assert "RR_TEST_GONE" not in worker                # a line taken out since
    finally:
        os.environ.pop("RR_TEST_FLAG", None)
        os.environ.pop("RR_TEST_GONE", None)


def test_a_real_environment_variable_still_wins(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("RR_TEST_REAL=from_file\n", encoding="utf-8")
    monkeypatch.setenv("RR_TEST_REAL", "from_launcher")
    monkeypatch.setattr(config, "ENV_FILE_KEYS", set())
    config._load_env_file(env)
    env.write_text("RR_TEST_REAL=edited\n", encoding="utf-8")
    assert config.worker_environment(env)["RR_TEST_REAL"] == "from_launcher"
