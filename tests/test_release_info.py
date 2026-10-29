"""
tests/test_release_info.py
==========================
The dashboard names the running release ("PRODUCTION (v1.0.0-rc11)"), read from .git itself
because production's account may not run git on the checkout.
"""

import shutil
import subprocess

import pytest

from src import release_info

# These tests build small repositories with the git program. The release deployer runs the suite as an
# account without git on its PATH, where rc12 failed on them (2026-10-23); the feature itself reads .git
# directly and needs no git, so the fixtures are skipped there.


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@example.invalid", "-c", "user.name=T",
                    "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
                   check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is not installed for this account")
    _git(tmp_path, "init", "-q")
    (tmp_path / "a.txt").write_text("1")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "one")
    release_info._cached.cache_clear()
    return tmp_path


@pytest.fixture
def no_git(monkeypatch):
    monkeypatch.setattr(release_info, "_describe", lambda root: None)  # as production's account


def _head(repo):
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def test_an_annotated_release_tag_is_read_without_git(repo, no_git):
    _git(repo, "tag", "-a", "-m", "rc11", "v1.0.0-rc11")
    _git(repo, "checkout", "-q", "--detach", "v1.0.0-rc11")   # how releases are installed
    assert release_info.release_label(repo) == "v1.0.0-rc11"


def test_packed_tags_are_read_too(repo, no_git):
    _git(repo, "tag", "-a", "-m", "rc10", "v1.0.0-rc10")
    _git(repo, "tag", "v1.0.0-rc9")                            # lightweight
    _git(repo, "pack-refs", "--all")
    _git(repo, "gc", "-q")                                     # the tag object leaves the loose store
    assert release_info.release_label(repo) == "v1.0.0-rc10"


def test_a_loose_tag_ref_whose_object_is_packed(repo, no_git):
    """How a fetch leaves a release on production: the ref loose, the tag object in a pack (rc68 showed
    the short commit on the dashboard, 2026-10-28)."""
    _git(repo, "tag", "-a", "-m", "rc68", "v1.0.0-rc68")
    _git(repo, "repack", "-a", "-d", "-q")                     # objects packed, refs left loose
    _git(repo, "prune")
    sha = (repo / ".git" / "refs" / "tags" / "v1.0.0-rc68").read_text().strip()
    assert not (repo / ".git" / "objects" / sha[:2] / sha[2:]).exists()
    _git(repo, "checkout", "-q", "--detach", "v1.0.0-rc68")
    assert release_info.release_label(repo) == "v1.0.0-rc68"


def test_a_lightweight_tag_on_a_branch_head(repo, no_git):
    _git(repo, "tag", "v1.0.0")
    assert release_info.release_label(repo) == "v1.0.0"


def test_other_tags_are_not_releases(repo, no_git):
    _git(repo, "tag", "-a", "-m", "x", "experiment")
    assert release_info.release_label(repo) == _head(repo)[:7]


def test_without_a_release_tag_development_shows_git_describe(repo):
    _git(repo, "tag", "-a", "-m", "rc11", "v1.0.0-rc11")
    (repo / "a.txt").write_text("2")
    _git(repo, "commit", "-q", "-am", "two")
    assert release_info.release_label(repo).startswith("v1.0.0-rc11-1-g")


def test_the_label_follows_a_new_release(repo, no_git):
    _git(repo, "tag", "-a", "-m", "rc10", "v1.0.0-rc10")
    assert release_info.release_label(repo) == "v1.0.0-rc10"
    (repo / "a.txt").write_text("2")
    _git(repo, "commit", "-q", "-am", "two")
    _git(repo, "tag", "-a", "-m", "rc11", "v1.0.0-rc11")
    assert release_info.release_label(repo) == "v1.0.0-rc11"   # HEAD moved: not the cached label


def test_not_a_checkout(tmp_path):
    assert release_info.release_label(tmp_path) is None


def test_the_dashboard_names_the_release(db, monkeypatch):
    from src import config, control
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "8837268021:test")
    monkeypatch.setattr("src.release_info.release_label", lambda root=None: "v1.0.0-rc11")
    text, _ = control.startup_dashboard_card(db, mode="daemon", time_str="07:00", timezone_str="UTC")
    assert "PRODUCTION (v1.0.0-rc11)" in text


def test_the_dashboard_survives_an_unreadable_release(db, monkeypatch):
    from src import config, control
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "8837268021:test")
    def broken(root=None):
        raise OSError("unreadable")
    monkeypatch.setattr("src.release_info.release_label", broken)
    text, _ = control.startup_dashboard_card(db, mode="daemon", time_str="07:00", timezone_str="UTC")
    assert "PRODUCTION" in text and "PRODUCTION (" not in text
