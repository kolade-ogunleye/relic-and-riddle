"""Cover styles against the audience (2026-11-01): each Reel's cover style is known, and the weekly digest
compares the rotated styles on Instagram by rates that do not depend on a topic's reach."""

from pathlib import Path

from src import audience, still_layout
from src.still_layout import Layout


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


def test_a_retired_style_on_record_is_still_recognised(db, tmp_path: Path) -> None:
    db.create_run("run_l", "01_topic")
    cover = tmp_path / "thumbnail_cover.jpg"
    still_layout.save(cover, Layout("cover", (1080, 1920), (0, 0, 1080, 1920)), {"style": "lower_third"})
    assert audience.cover_style(db, "run_l", {"master_video_path": str(tmp_path / "m.mp4")}) == "lower_third"


def test_without_a_record_the_rotation_is_recovered(db) -> None:
    db.create_run("run_r", "01_topic")
    key = int(db.run_key("run_r"))
    assert audience.cover_style(db, "run_r", {}) == audience.LEGACY_ROTATION[key % len(audience.LEGACY_ROTATION)]


def test_two_styles_are_compared_by_median_and_flagged_early() -> None:
    rows = [_row("a1", "badge", 40, 0.010), _row("a2", "badge", 42, 0.012), _row("a3", "badge", 90, 0.0),
            _row("b1", "minimal", 30, 0.020), _row("b2", "minimal", 32, 0.018)]
    [line] = audience.cover_style_comparison(rows)
    assert line.startswith("• Cover styles · Instagram")
    assert line.index("minimal (2)") < line.index("badge (3)")          # lowest skip first
    assert "badge (3): skip 42%" in line                                 # the median: one outlier does not decide
    assert "Early signal" in line


def test_every_video_platform_gets_its_own_line() -> None:
    rows = [_row("a1", "badge", 40, 0.01), _row("b1", "minimal", 30, 0.02)]
    for platform in ("facebook", "youtube"):
        rows += [dict(_row("a1", "badge", None, 0.01), platform=platform, watch_ratio=0.4),
                 dict(_row("b1", "minimal", None, 0.02), platform=platform, watch_ratio=0.7)]
    lines = audience.cover_style_comparison(rows)
    assert [ln.split(" (")[0] for ln in lines] == ["• Cover styles · Instagram", "• Cover styles · Facebook",
                                                   "• Cover styles · YouTube"]
    fb = lines[1]
    assert fb.index("minimal (1)") < fb.index("badge (1)")               # no skip rate there: best watch first


def test_one_style_alone_is_not_a_comparison() -> None:
    assert audience.cover_style_comparison([_row("a1", "badge", 40, 0.01)]) == []


def test_enough_videos_drop_the_early_label() -> None:
    rows = [_row(f"a{i}", "badge", 40, 0.01) for i in range(audience.MIN_PER_STYLE)]
    rows += [_row(f"b{i}", "minimal", 30, 0.02) for i in range(audience.MIN_PER_STYLE)]
    assert "Early signal" not in audience.cover_style_comparison(rows)[0]


def test_the_digest_carries_the_comparison(db, monkeypatch) -> None:
    rows = [_row("a1", "badge", 40, 0.01), _row("b1", "minimal", 30, 0.02)]
    monkeypatch.setattr(audience, "table", lambda db: rows)
    assert any(line.startswith("• Cover styles") for line in audience.digest_lines(db))
