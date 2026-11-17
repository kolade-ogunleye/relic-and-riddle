"""QF-82: only delivered covers, with no learning from Reel-content metrics."""
from pathlib import Path

import pytest
from PIL import Image

from src import cover_selector, still_layout
from src.control import make_command_handler
from src.visual_engine import VisualEngine

COPY = {"eyebrow": "ANCIENT ENGINEERING", "line1": "STONE THAT", "line2": "STILL STANDS",
        "tagline": "What held it together?"}


def _misleading_rows():
    return [{"run_id": f"r{style}{i}", "platform": platform, "cover_style": style,
             "unavailable": None, "skip_rate": 0 if style == "depth_sandwich" else 100,
             "watch_ratio": 1.0 if style == "depth_sandwich" else 0.01,
             "share_rate": .1 if style == "depth_sandwich" else 0.0,
             "save_rate": .1 if style == "depth_sandwich" else 0.0,
             "views": 1000000, "reach": 1000000, "likes": 99999,
             "cover_ctr": 1.0, "cover_impressions": 1000000}
            for i in range(20) for style in ("badge", "monument", "depth_sandwich")
            for platform in ("instagram", "facebook", "youtube")]


@pytest.mark.parametrize("previous", [None, "badge", "monument", "depth_sandwich"])
def test_cover_choice_is_fixed_despite_reel_rewards_and_neighbor_history(previous):
    for seed in ("run-a", "run-b", "run-c"):
        style, reason = cover_selector.choose(COPY, _misleading_rows(), previous=previous,
                                              seed=seed, recent=[previous, "depth_sandwich"])
        assert style == "badge"
        assert "frozen" in reason and "cover-specific" in reason


def test_frozen_cover_choice_never_invokes_the_learning_bandit(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Reel-content rewards must not reach the cover bandit")
    monkeypatch.setattr("src.learning.choose", forbidden)
    assert cover_selector.choose(COPY, _misleading_rows(), previous="badge")[0] == "badge"


def test_pipeline_cover_selection_keeps_fixed_policy(db, monkeypatch):
    db.create_run("run_qf82", "01_topic")
    monkeypatch.setattr("src.audience.table", lambda db: _misleading_rows())
    style, reason = cover_selector.for_run(db, "run_qf82", {"thumbnail": COPY})
    assert style == "badge" and "frozen" in reason


@pytest.mark.parametrize("legacy_variants", [None, True, False])
def test_real_renderer_writes_only_final_and_grid(tmp_path, legacy_variants):
    hero = tmp_path / "hero.png"
    Image.new("RGB", (1600, 2400), (71, 84, 96)).save(hero)
    output = tmp_path / "run"
    kwargs = {} if legacy_variants is None else {"variants": legacy_variants}
    cover, grid = VisualEngine().generate_thumbnails(hero, {"thumbnail": COPY}, output, **kwargs)
    assert {p.name for p in output.glob("*.jpg")} == {"thumbnail_cover.jpg", "thumbnail_profile_grid.jpg"}
    with Image.open(cover) as image:
        assert image.size == (1080, 1920)
    with Image.open(grid) as image:
        assert image.size == (1080, 1080)
    assert still_layout.load(cover)
    assert not list(output.glob("thumbnail_variant*"))


def test_cover_panel_opens_final_only_run_instead_of_an_older_variant_run(db, monkeypatch, tmp_path):
    monkeypatch.setattr("src.config.OUTPUT_DIR", tmp_path)
    run = tmp_path / "run_qf82_pending"
    run.mkdir()
    db.create_run(run.name, "01_topic")
    db.update_run(run.name, "pending_approval", master_video_path=str(run / "master.mp4"))
    db.record_approval(run.name, "01_topic", 82)
    for name, size in (("thumbnail_cover.jpg", (108, 192)), ("thumbnail_profile_grid.jpg", (108, 108))):
        Image.new("RGB", size, "navy").save(run / name)
    older = tmp_path / "run_old_variants"
    older.mkdir()
    Image.new("RGB", (108, 192), "red").save(older / "thumbnail_variant_b_intrigue.jpg")
    sent = []
    handler = make_command_handler(db, lambda: None, lambda: "",
                                   send_photo=lambda path, caption, buttons, cache_key=None:
                                   sent.append((Path(path), caption, buttons, cache_key)) or True)
    assert handler("covers", "") == ""
    assert [item[0].name for item in sent] == ["thumbnail_cover.jpg"]
    grid_button = next(button for row in sent[0][2] for button in row
                       if button["callback_data"].endswith(":grid"))
    assert handler("ccov", grid_button["callback_data"].split(":", 1)[1]) == ""
    assert [item[0].name for item in sent] == ["thumbnail_cover.jpg", "thumbnail_profile_grid.jpg"]
    assert all(item[0].parent == run for item in sent)
    assert "fixed" in sent[0][1].lower()
    assert not any(button["callback_data"].startswith(("cov:", "covlock:"))
                   for item in sent for row in item[2] for button in row)
    assert sent[0][3] != sent[1][3]
    # A repaired cover in the same run must not reuse Telegram's older cached image.
    Image.new("RGB", (108, 192), "green").save(run / "thumbnail_cover.jpg")
    assert handler("covers", run.name) == ""
    assert sent[-1][0] == sent[0][0] and sent[-1][3] != sent[0][3]
    (run / "thumbnail_profile_grid.jpg").unlink()
    response = handler("ccov", grid_button["callback_data"].split(":", 1)[1])
    assert "not available" in response[0]
    assert len(sent) == 3


def test_historical_variant_evidence_is_not_deleted_or_overwritten(tmp_path):
    hero = tmp_path / "hero.png"
    Image.new("RGB", (1600, 2400), (71, 84, 96)).save(hero)
    output = tmp_path / "run"
    output.mkdir()
    historical = output / "thumbnail_variant_a_blueprint.jpg"
    historical.write_bytes(b"retained historical publication evidence")
    VisualEngine().generate_thumbnails(hero, {"thumbnail": COPY}, output)
    assert historical.read_bytes() == b"retained historical publication evidence"


@pytest.mark.parametrize("recorded", ["profile", "cover_path", "folder_only"])
def test_cover_panel_never_shows_a_documentarys_unposted_9x16_cover(db, monkeypatch, tmp_path, recorded):
    # Review 1: a documentary has always held only the final cover and grid tile, but it posts its 16:9 thumbnail;
    # the panel must not open it and show the unposted 9:16 picture as the "Final cover".
    monkeypatch.setattr("src.config.OUTPUT_DIR", tmp_path)
    run = tmp_path / "run_qf82_doc_long"
    run.mkdir()
    for name, size in (("thumbnail_cover.jpg", (108, 192)), ("thumbnail_profile_grid.jpg", (108, 108)),
                       ("thumbnail_youtube_16x9.jpg", (192, 108))):
        Image.new("RGB", size, "navy").save(run / name)
    if recorded == "folder_only":
        (run / "qf82_doc_documentary_16x9.mp4").write_bytes(b"")
    else:
        db.create_run(run.name, "01_topic")
        fields = {"master_video_path": str(run / "qf82_doc_documentary_16x9.mp4")}
        if recorded == "profile":
            fields["script_payload"] = {"delivery_profile": "deep_dive_16x9"}
        else:
            fields["thumbnail_cover_path"] = str(run / "thumbnail_youtube_16x9.jpg")
        db.update_run(run.name, "pending_approval", **fields)
        db.record_approval(run.name, "01_topic", 82)
    sent = []
    handler = make_command_handler(db, lambda: None, lambda: "",
                                   send_photo=lambda path, caption, buttons, cache_key=None:
                                   sent.append(Path(path)) or True)
    reply = handler("covers", "")
    assert sent == [] and "No rendered video has covers yet" in reply[0]
    if recorded != "folder_only":
        reply = handler("covers", run.name)
        assert sent == [] and "documentary" in reply[0].lower()
