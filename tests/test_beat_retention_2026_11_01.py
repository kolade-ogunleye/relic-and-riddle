"""Where viewers leave (2026-11-01): Facebook's retention curve read beat by beat, and the beat that loses the
most viewers filed for the script and the edit."""

from itertools import pairwise

from src import audience


def _graph(levels):
    """A 40-point retention graph from (fortieth, share) knots, linear between them."""
    knots = sorted(levels)
    out = {}
    for k in range(41):
        for (a, va), (b, vb) in pairwise(knots):
            if a <= k <= b:
                out[str(k)] = va + (vb - va) * (k - a) / (b - a)
                break
    return out


BEATS = [{"start": 0.0, "text": "Beneath a house in Cappadocia, a city lay hidden."},
         {"start": 4.0, "text": "Researchers calculate it sheltered up to 20,000 people."},
         {"start": 12.0, "text": "Stone doors rolled shut from inside."},
         {"start": 20.0, "text": "It is still being mapped today."}]


def test_each_beat_after_the_hook_has_its_loss() -> None:
    graph = _graph([(0, 1.0), (4, 0.8), (12, 0.48), (20, 0.44), (40, 0.4)])   # 40 s video: 1 fortieth = 1 s
    drops = audience.beat_drops({"post_video_retention_graph": graph}, 40.0, BEATS)
    assert [d["start"] for d in drops] == [3.0, 4.0, 12.0, 20.0]             # the first beat is read from 3 s
    worst = max(drops, key=lambda d: d["lost"])
    assert worst["text"].startswith("Researchers calculate") and abs(worst["lost"] - 0.4) < 1e-6


def test_the_last_beat_ends_where_the_end_card_starts() -> None:
    graph = _graph([(0, 1.0), (36, 0.5), (40, 0.1)])
    drops = audience.beat_drops({"post_video_retention_graph": graph}, 40.0, BEATS, end_s=36.0)
    to_the_end = audience.beat_drops({"post_video_retention_graph": graph}, 40.0, BEATS)
    assert drops[-1]["end"] == 36.0 and to_the_end[-1]["end"] == 40.0
    assert drops[-1]["lost"] < to_the_end[-1]["lost"] - 0.3                  # the card's own loss is not the beat's


def test_nothing_without_a_graph_or_beats() -> None:
    assert audience.beat_drops({}, 40.0, BEATS) == []
    assert audience.beat_drops({"post_video_retention_graph": _graph([(0, 1.0), (40, 0.5)])}, 40.0, []) == []


def test_a_beat_that_loses_many_viewers_is_filed_once(db, monkeypatch) -> None:
    rows = [{"run_id": f"r{i}", "platform": "facebook", "snapshot": "7d", "fetched_at": "x", "title": f"T{i}",
             "unavailable": None, "skip_rate": None, "watch_ratio": 0.5, "share_rate": 0.01, "views": 100.0,
             "reach": 100.0, "save_rate": None, "likes": None, "comments": None, "worst_beat": None}
            for i in range(5)]
    rows[2]["worst_beat"] = {"text": "Researchers calculate it sheltered up to 20,000 people.", "start": 4.0,
                             "end": 12.0, "lost": 0.4}
    rows[3]["worst_beat"] = {"text": "A gentle beat.", "start": 4.0, "end": 8.0, "lost": 0.05}
    monkeypatch.setattr(audience, "table", lambda db: rows)
    assert audience.file_findings(db) == 1
    assert audience.file_findings(db) == 0
    with db._conn() as conn:
        found = conn.execute("SELECT run_id, klass, detail FROM findings WHERE klass = 'audience.beat_drop'").fetchall()
    assert [f["run_id"] for f in found] == ["r2"]
    assert "lost 40% of viewers during" in found[0]["detail"] and "Researchers calculate" in found[0]["detail"]
