"""Cover styles against the audience (2026-11-01): each Reel's cover style is known, and the weekly digest
compares the rotated styles on Instagram by rates that do not depend on a topic's reach."""

from pathlib import Path

from src import audience, still_layout
from src.still_layout import Layout
from src.visual_engine import COVER_STYLES


def _row(run_id: str, style: str, skip: float, share: float, platform: str = "instagram") -> dict:
    return {"run_id": run_id, "platform": platform, "snapshot": "7d", "fetched_at": "x", "title": run_id,
            "unavailable": None, "views": 100.0, "reach": 100.0, "watch_ratio": 0.5, "skip_rate": skip,
            "share_rate": share, "save_rate": 0.01, "likes": None, "comments": None, "cover_style": style}


def test_the_recorded_style_beside_the_cover_wins(db, tmp_path: Path) -> None:
    db.create_run("run_s", "01_topic")
    cover = tmp_path / "thumbnail_cover.jpg"
    still_layout.save(cover, Layout("cover", (1080, 1920), (0, 0, 1080, 1920)), {"style": "minimal"})
    run = {"master_video_path": str(tmp_path / "master.mp4")}
    assert audience.cover_style(db, "run_s", run) == "minimal"


def test_without_a_record_the_rotation_is_recovered(db) -> None:
    db.create_run("run_r", "01_topic")
    key = int(db.run_key("run_r"))
    assert audience.cover_style(db, "run_r", {}) == COVER_STYLES[key % len(COVER_STYLES)]


def test_two_styles_are_compared_by_median_and_flagged_early() -> None:
    rows = [_row("a1", "badge", 40, 0.010), _row("a2", "badge", 42, 0.012), _row("a3", "badge", 90, 0.0),
            _row("b1", "minimal", 30, 0.020), _row("b2", "minimal", 32, 0.018),
            _row("f1", "badge", 10, 0.5, platform="facebook")]
    line = audience.cover_style_comparison(rows)
    assert line.index("minimal (2)") < line.index("badge (3)")          # lowest skip first
    assert "badge (3): skip 42%" in line                                 # the median: one outlier does not decide
    assert "Early signal" in line


def test_one_style_alone_is_not_a_comparison() -> None:
    assert audience.cover_style_comparison([_row("a1", "badge", 40, 0.01)]) is None


def test_enough_reels_drop_the_early_label() -> None:
    rows = [_row(f"a{i}", "badge", 40, 0.01) for i in range(audience.MIN_PER_STYLE)]
    rows += [_row(f"b{i}", "minimal", 30, 0.02) for i in range(audience.MIN_PER_STYLE)]
    assert "Early signal" not in audience.cover_style_comparison(rows)


def test_the_digest_carries_the_comparison(db, monkeypatch) -> None:
    rows = [_row("a1", "badge", 40, 0.01), _row("b1", "minimal", 30, 0.02)]
    monkeypatch.setattr(audience, "table", lambda db: rows)
    assert any(line.startswith("• Cover styles") for line in audience.digest_lines(db))
