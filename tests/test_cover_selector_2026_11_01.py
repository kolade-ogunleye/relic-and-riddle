"""Cover selection (2026-11-01): fit rules, neighbours differ, explore each style, then learn from Instagram."""

from collections import Counter

from src import cover_selector
from src.cover_design import monument_figure

FIGURE = {"eyebrow": "ANCIENT ACOUSTICS", "line1": "THE 144-FOOT", "line2": "WHISPERING DOME",
          "tagline": "BUILT WITHOUT RINGS"}
NO_FIGURE = {"eyebrow": "LOST CODE", "line1": "THE BOOK", "line2": "NOBODY CAN READ", "tagline": "STILL UNSOLVED"}


_RUN = iter(range(10**6))


def _row(style: str, skip: float, share: float, save: float = 0.0, platform: str = "instagram",
         run_id: str = "") -> dict:
    return {"platform": platform, "unavailable": None, "cover_style": style, "skip_rate": skip,
            "share_rate": share, "save_rate": save, "run_id": run_id or f"r{next(_RUN)}"}


def test_monument_needs_a_figure() -> None:
    assert "monument" in cover_selector.fitting(FIGURE)
    assert "monument" not in cover_selector.fitting(NO_FIGURE)
    assert monument_figure(FIGURE)[:2] == ("144", "FT")
    assert monument_figure({"line1": "THE 25-MILE", "line2": "SAHARA EYE"})[:2] == ("25", "MILES")
    assert monument_figure({"tagline": "DERINKUYU, 1963"})[:2] == ("1963", "")
    assert monument_figure({"line1": "5M VISITORS"}) is None          # "M" without a space is not metres


def test_the_previous_style_is_not_repeated_while_another_fits() -> None:
    for seed in "abcdefgh":
        style, _ = cover_selector.choose(FIGURE, [], previous="badge", seed=seed)
        assert style != "badge"


def test_least_tried_style_goes_first() -> None:
    rows = [_row(s, 30, 0.01) for s in ("badge", "minimal", "depth_sandwich") for _ in range(3)]
    style, why = cover_selector.choose(FIGURE, rows, seed="x")
    assert style == "monument" and why.startswith("exploring")


def test_after_exploration_the_better_style_goes_out_more_often() -> None:
    rows = [_row("badge", 20, 0.03) for _ in range(12)]          # strong: little skip, high engagement
    rows += [_row(s, 70, 0.002) for s in ("monument", "minimal", "depth_sandwich") for _ in range(12)]
    picks = Counter(cover_selector.choose(FIGURE, rows, seed=f"run{i}")[0] for i in range(200))
    assert picks["badge"] > 150
    assert cover_selector.choose(FIGURE, rows, seed="run7")[1].startswith("learned")


def test_the_choice_is_repeatable_for_a_run() -> None:
    rows = [_row(s, 40, 0.01) for s in ("badge", "monument", "minimal", "depth_sandwich") for _ in range(4)]
    assert len({cover_selector.choose(FIGURE, rows, seed="same")[0] for _ in range(5)}) == 1


def test_reward_is_bounded_and_reads_each_platforms_rates() -> None:
    assert cover_selector.reward(_row("badge", 0, 0.5)) == 1.0
    assert cover_selector.reward(_row("badge", 100, 0.0)) == 0.0
    assert cover_selector.reward({"skip_rate": None, "share_rate": None, "save_rate": None}) is None
    youtube = {"views": 1000.0, "likes": 50.0, "watch_ratio": 1.4}      # likes per view, a loop capped at 1
    assert cover_selector.reward(youtube) == 1.0


def test_every_platform_counts_once_per_run_and_against_its_own_level() -> None:
    rows = [_row("badge", 30, 0.02, run_id="r1"), _row("minimal", 60, 0.0, run_id="r2")]
    rows += [dict(_row("badge", None, 0.02, platform="facebook", run_id="r1"), skip_rate=None),
             dict(_row("minimal", None, 0.0, platform="facebook", run_id="r2"), skip_rate=None)]
    seen = cover_selector.evidence(rows)
    assert len(seen["badge"]) == 1 and len(seen["minimal"]) == 1   # one vote per run, both platforms in it
    assert seen["badge"][0] > 0.5 > seen["minimal"][0]              # each against its platform's median
    youtube_only = [dict(_row("badge", None, None, platform="youtube"), skip_rate=None, share_rate=None,
                         save_rate=None, views=100.0, likes=4.0)]
    assert list(cover_selector.evidence(youtube_only)) == ["badge"]
    assert abs(cover_selector.evidence(youtube_only)["badge"][0] - 0.5) < 1e-9


def test_for_run_uses_the_payload_copy_and_survives_missing_numbers(db, monkeypatch) -> None:
    db.create_run("run_x", "01_topic")
    monkeypatch.setattr("src.audience.table", lambda db: (_ for _ in ()).throw(RuntimeError("no table")))
    style, why = cover_selector.for_run(db, "run_x", {"thumbnail": NO_FIGURE})
    assert style in cover_selector.fitting(NO_FIGURE) and why
