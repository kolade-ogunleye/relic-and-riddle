"""QF-114: every post is bound to the review card you approved.

A master (with its composition record and provenance ledger) swapped after approval was caught only on QF-110's
fact re-check path, and the carousel's slot check (QF-81b). Now every real posting path checks the run against the
approved card first: the review fingerprint (copy, video, cover, Stories, carousel) and, for a card recorded since
QF-114, the master's composition record and provenance ledger. Nothing is uploaded when it does not match.
"""
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import main

RUN, MID = "run_b", 4242


@pytest.fixture
def approved(db, delivered_review):
    """A run delivered on card MID (its fingerprint and files recorded) and approved there."""
    def make(composition=True):
        folder = delivered_review(RUN, MID)
        master = folder / "master.mp4"
        if composition:
            master.with_suffix(".composition.json").write_text(json.dumps({"master_sha256": "x", "fonts": {}}))
            (folder / "provenance.json").write_text(json.dumps({"master": {"sha256": "x"}}))
            master.with_suffix(".srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nWords\n", encoding="utf-8")
            from src.telegram_state import put, review_fingerprint
            put(db, "review-card", f"{RUN}:{MID}", review_fingerprint(db, RUN))   # the card as delivered
        db.update_approval_decision(RUN, "approved", "operator")
        return folder
    return make


def _coordinator(db, sent):
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.db = db
    c.publisher = SimpleNamespace(
        publish_photos=lambda *a, **k: sent.append(("photos", a, k)) or {"status": "published", "post_urls": {}},
        publish_short=lambda *a, **k: sent.append(("short", a, k)) or {"status": "published", "post_urls": {}},
        build_envelope=lambda *a, **k: {"platforms": ["instagram"]})
    return c


def _gate_stub(monkeypatch):
    calls = []
    import src.release
    monkeypatch.setattr(src.release, "require_release", lambda run, dry_run=False: calls.append(run["run_id"]) or {})
    return calls


SWAPS = {
    "master": lambda f: (f / "master.mp4").write_bytes(b"another video"),
    "composition record": lambda f: (f / "master.composition.json").write_text('{"master_sha256": "y"}'),
    "provenance ledger": lambda f: (f / "provenance.json").write_text('{"master": {"sha256": "y"}}'),
    "captions": lambda f: (f / "master.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nOther words\n"),
}


@pytest.mark.parametrize("what", sorted(SWAPS))
def test_a_reel_whose_files_changed_after_approval_is_not_posted(db, approved, monkeypatch, what):
    folder = approved()
    gate = _gate_stub(monkeypatch)
    c = _coordinator(db, [])
    monkeypatch.setattr(c, "_recheck_facts", lambda run, retry_now=False: None)
    c._require_release(db.get_run(RUN))            # unchanged: passes, and the gate runs
    assert gate == [RUN]
    SWAPS[what](folder)
    from src.release import ReleaseBlocked
    with pytest.raises(ReleaseBlocked, match="after you approved the review"):
        c._require_release(db.get_run(RUN))
    assert gate == [RUN]                            # refused before the gate, before any upload


def test_changed_public_copy_is_not_posted(db, approved, monkeypatch):
    approved()
    _gate_stub(monkeypatch)
    c = _coordinator(db, [])
    monkeypatch.setattr(c, "_recheck_facts", lambda run, retry_now=False: None)
    run = db.get_run(RUN)
    db.update_run(RUN, "approved", script_payload={**(run.get("script_payload") or {}), "caption": "a new caption"})
    from src.release import ReleaseBlocked
    with pytest.raises(ReleaseBlocked, match="text changed"):
        c._require_release(db.get_run(RUN))


def test_stories_whose_master_changed_after_approval_are_not_posted(db, approved, tmp_path):
    folder = approved()
    (folder / "stories").mkdir(exist_ok=True)
    frame = folder / "stories" / "story_01_hook.jpg"
    frame.write_bytes(b"frame")
    from src.telegram_state import put, review_fingerprint
    put(db, "review-card", f"{RUN}:{MID}", review_fingerprint(db, RUN))   # delivered with its Stories
    post = db.add_scheduled_post(RUN, "story", datetime(2026, 11, 10, 17, 45, tzinfo=UTC).isoformat(), [str(frame)])
    SWAPS["master"](folder)
    sent, said = [], []
    c = _coordinator(db, sent)
    c._alert = lambda text, **item: said.append(text)
    booking = next(p for p in db.list_scheduled_posts(("pending",)) if p["id"] == post)
    assert c._post_photos(booking, db.get_run(RUN), "story") == "failed"
    assert sent == []
    detail = next(p for p in db.list_scheduled_posts(("failed",)) if p["id"] == post)["detail"]
    assert detail.get("binding") and "after you approved the review" in detail["error"]
    assert len(said) == 1 and "Stories not posted" in said[0] and "Nothing was posted" in said[0]


def test_a_retry_and_the_short_button_post_only_the_approved_review(db, approved, monkeypatch):
    folder = approved()
    frame = folder / "stories" / "story_02_reveal.jpg"
    frame.parent.mkdir(exist_ok=True)
    frame.write_bytes(b"frame")
    from src.telegram_state import put, review_fingerprint
    put(db, "review-card", f"{RUN}:{MID}", review_fingerprint(db, RUN))
    post = db.add_scheduled_post(RUN, "story", datetime(2026, 11, 10, 17, 45, tzinfo=UTC).isoformat(), [str(frame)])
    db.update_scheduled_post(post, "unknown", detail={"status": "unknown", "posted_parts": 1, "part": 2,
                                                      "failed": {"instagram": "media error"}})
    SWAPS["provenance ledger"](folder)
    sent = []
    c = _coordinator(db, sent)
    assert "Not retried" in c.retry_post(post) and sent == []
    from src import config
    monkeypatch.setattr(config, "YOUTUBE_SHORTS", True)
    db.update_run(RUN, "published", thumbnail_cover_path=folder / "thumbnail_cover.jpg",
                  thumbnail_grid_path=folder / "thumbnail_cover.jpg")
    assert "Not posted" in c.post_youtube_short(RUN) and sent == []


def test_an_approval_without_a_card_record_is_not_bound(db, tmp_path):
    folder = tmp_path / "cli"
    folder.mkdir()
    (folder / "master.mp4").write_bytes(b"v")
    db.create_run(RUN, "01_topic")
    db.update_run(RUN, "pending_approval", master_video_path=folder / "master.mp4")
    db.record_approval(RUN, "01_topic", None)        # the command line: no card
    db.update_approval_decision(RUN, "approved", "cli")
    assert _coordinator(db, [])._binding_problem(RUN) is None


def test_a_card_recorded_before_qf114_binds_its_fingerprint_only(db, approved):
    folder = approved()
    with db._conn() as conn:   # no file record, as a card delivered before this change
        conn.execute("DELETE FROM telegram_transport WHERE namespace='review-files'")
    c = _coordinator(db, [])
    SWAPS["composition record"](folder)
    assert c._binding_problem(RUN) is None
    SWAPS["master"](folder)
    assert "after you approved" in c._binding_problem(RUN)


def _uploaders():
    """{method: line of its first upload} for every PipelineCoordinator method that sends something to a platform
    itself: a call of a publisher's upload method (publish*, set_*, upload*: src/publisher.py) on self.publisher or
    on a publisher it makes (FacebookPageReelsPublisher(), UploadPostPublisher(), ...), a dry run aside. Found
    from the source, so a new uploader is covered without being named here."""
    import ast
    import re

    from src import publisher
    classes = {n for n, v in vars(publisher).items() if isinstance(v, type) and n.endswith("Publisher")}
    uploads = {m for n in classes for m in vars(getattr(publisher, n))
               if re.match(r"(publish|set_|upload)", m) and callable(getattr(getattr(publisher, n), m))}
    assert {"publish_reel", "publish_photos", "publish_short", "set_cover", "upload_captions",
            "set_youtube_thumbnail"} <= uploads
    tree = ast.parse(Path(main.__file__).read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PipelineCoordinator")
    found = {}
    for fn in (n for n in cls.body if isinstance(n, ast.FunctionDef)):
        # a local name bound to a publisher it makes (`fb = FacebookPageReelsPublisher()`) is a publisher too
        local = {t.id for node in ast.walk(fn) if isinstance(node, (ast.Assign, ast.AnnAssign))
                 and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
                 and node.value.func.id in classes
                 for t in (node.targets if isinstance(node, ast.Assign) else [node.target]) if isinstance(t, ast.Name)}
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in uploads):
                continue
            target = node.func.value
            on_publisher = (isinstance(target, ast.Attribute) and target.attr == "publisher") or (
                isinstance(target, ast.Call) and isinstance(target.func, ast.Name) and target.func.id in classes) or (
                isinstance(target, ast.Name) and target.id in local)
            dry = any(k.arg == "dry_run" and isinstance(k.value, ast.Constant) and k.value.value is True
                      for k in node.keywords)
            if on_publisher and not dry:
                found[fn.name] = min(found.get(fn.name, node.lineno), node.lineno)
    return found, tree, cls


def test_the_discovery_follows_a_publisher_held_in_a_local_name(monkeypatch, tmp_path):
    """Review 2 (QF-114 nit): `fb = FacebookPageReelsPublisher(); fb.set_cover(...)` is found as an upload."""
    source = Path(main.__file__).read_text(encoding="utf-8")
    probe = source.replace("    def set_facebook_cover(self, run_id: str) -> str:", """    def _probe_upload(self, run_id):
        from src.publisher import FacebookPageReelsPublisher
        fb = FacebookPageReelsPublisher()
        return fb.set_cover("v", None)

    def set_facebook_cover(self, run_id: str) -> str:""", 1)
    mutated = tmp_path / "main.py"
    mutated.write_text(probe, encoding="utf-8")
    monkeypatch.setattr(main, "__file__", str(mutated))
    found, _tree, _cls = _uploaders()
    assert "_probe_upload" in found


def test_every_real_upload_path_checks_the_binding_first():
    """The class: every coordinator method that uploads for real checks the binding (or the release path that does)
    before its first upload."""
    import ast
    found, _tree, cls = _uploaders()
    assert {"_publish_run_locked", "_post_photos", "retry_post", "post_youtube_short", "retry_reel",
            "set_facebook_cover", "add_facebook_captions", "set_youtube_thumbnail"} <= set(found)
    checks = ("_binding_problem", "_require_release", "_require_binding")
    for fn in (n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in found):
        first_check = min((node.lineno for node in ast.walk(fn) if isinstance(node, ast.Call)
                           and isinstance(node.func, ast.Attribute) and node.func.attr in checks), default=None)
        assert first_check is not None and first_check < found[fn.name], fn.name


@pytest.mark.parametrize("what", ["captions", "master"])
def test_the_episode_buttons_upload_only_the_approved_review(db, approved, monkeypatch, what):
    """The Facebook cover and captions and the YouTube thumbnail, from the episode's buttons after posting (QF-114
    review 1, B3): refused when the review changed, the upload never made."""
    folder = approved()
    import src.publisher
    calls = []

    class Recorder:
        def __init__(self, *a, **k):
            pass

        def __getattr__(self, name):
            return lambda *a, **k: calls.append(name) or {"set": True, "locale": "en_US"}
    monkeypatch.setattr(src.publisher, "FacebookPageReelsPublisher", Recorder)
    monkeypatch.setattr(src.publisher, "UploadPostPublisher", Recorder)
    db.update_run(RUN, "published", thumbnail_cover_path=folder / "thumbnail_cover.jpg")
    c = _coordinator(db, [])
    monkeypatch.setattr(main.PipelineCoordinator, "facebook_video_id", staticmethod(lambda db, run_id: "fb1"))
    monkeypatch.setattr(main.PipelineCoordinator, "youtube_short_id", staticmethod(lambda db, run_id: "yt1"))
    assert c._binding_problem(RUN) is None
    SWAPS[what](folder)
    assert "Cover not set" in c.set_facebook_cover(RUN)
    assert "Captions not added" in c.add_facebook_captions(RUN)
    assert "Thumbnail not set" in c.set_youtube_thumbnail(RUN)
    assert calls == []


def test_a_carousel_whose_records_changed_is_held_before_it_takes_its_slot(db, approved):
    folder = approved()
    c = _coordinator(db, [])
    assert c._approval_broken(RUN) is None
    SWAPS["provenance ledger"](folder)
    assert "provenance ledger changed after you approved the review" in c._approval_broken(RUN)


def test_a_telegram_approval_whose_card_record_is_gone_is_held_and_records_are_kept(db, approved, monkeypatch):
    approved()
    c = _coordinator(db, [])
    with db._conn() as conn:   # a record purged by the 90-day sweep before this change
        conn.execute("DELETE FROM telegram_transport WHERE namespace IN ('review-card', 'review-files')")
    assert "record of the review card you approved is gone" in c._binding_problem(RUN)
    assert "record of the review card you approved is gone" in c._approval_broken(RUN)
    # the sweep keeps review records now, whatever their age
    from src import telegram_state
    telegram_state.put(db, "review-card", f"{RUN}:{MID}", "fp")
    real = telegram_state.time.time
    monkeypatch.setattr(telegram_state.time, "time", lambda: real() + 400 * 86400)
    telegram_state.put(db, "other", "k", 1)   # any later put runs the sweep
    assert telegram_state.get(db, "review-card", f"{RUN}:{MID}") == "fp"
    assert telegram_state.get(db, "review-files", f"{RUN}:{MID}") is not None
