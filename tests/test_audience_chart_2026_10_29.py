"""The audience chart (2026-10-29): views 7 days after posting for the last six Reels, per platform, as a dot
plot drawn from the stored audience_metrics rows only. The values below are test rows, never real figures;
renders made with sample=True carry the SAMPLE DATA watermark. Nothing here calls an API or Telegram."""
import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from src import audience, audience_chart as ac

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
TODAY = datetime(2026, 10, 29)


def run(db, run_id, title, posted, fmt="reel"):
    db.create_run(run_id, "01_topic", content_format=fmt)
    db.update_run(run_id, "approved", script_payload={"title": title})
    db.record_publication("01_topic", run_id, "upload-post", "published", {})
    with db._conn() as conn:
        conn.execute("UPDATE publications SET created_at = ? WHERE run_id = ?", (posted.isoformat(), run_id))


def metric(db, run_id, platform, snapshot, metrics, fetched=NOW):
    audience.ensure_schema(db)
    with db._conn() as conn:
        conn.execute("INSERT OR REPLACE INTO audience_metrics VALUES (?,?,?,?,?,?)",
                     (run_id, platform, snapshot, "upload-post", json.dumps(metrics), fetched.isoformat()))


def reel(title, ig, yt, fb):
    return {"run_id": title, "title": title, "views": {"instagram": ig, "youtube": yt, "facebook": fb}}


SIX = [reel("Derinkuyu: the city under Cappadocia", 3120, 1150, 1010),
       reel("Gol Gumbaz: the whispering gallery", 2410, 1260, 940),
       reel("The Richat Structure", 2630, 1080, 880),
       reel("Roman concrete that heals itself", 1960, 1450, 1020),
       reel("Zhang Heng's seismoscope", 2210, 1190, 760),
       reel("The Piri Reis map of 1513", 1840, 1320, 910)]


def texts(info):
    return [t["text"] for t in info["texts"]]


# ------------------------------------------------------------------ the stored rows
def test_empty_database_draws_the_no_data_state(db, tmp_path):
    out = tmp_path / "chart.png"
    info = ac.chart_for(db, out, today=TODAY)
    assert ac.reels_at_7d(db) == []
    assert info["headline"] == "No audience data yet" and info["rows"] == 0
    assert "NO AUDIENCE DATA YET" in texts(info)
    assert not any("SAMPLE" in t for t in texts(info))                 # a real render is never watermarked
    assert not any(re.fullmatch(r"[\d,.]+[KMB]?", t) for t in texts(info))  # no figure, no axis
    im = Image.open(out)
    assert im.size[0] == ac.W


def test_reader_uses_only_seven_day_reel_rows_newest_first(db):
    for i in range(8):
        run(db, f"run_{i}", f"Reel {i}", NOW - timedelta(days=30 - i))
        metric(db, f"run_{i}", "instagram", "7d", {"views": 100 + i})
    run(db, "run_young", "Too young", NOW - timedelta(days=2))
    metric(db, "run_young", "instagram", "1d", {"views": 999})               # no 7-day snapshot yet
    run(db, "run_doc", "Documentary", NOW - timedelta(days=9), fmt="deep_dive")
    metric(db, "run_doc", "youtube", "7d", {"views": 5000})                  # not a Reel
    metric(db, "run_7", "facebook", "7d", {"fb_reels_total_plays": 0})       # a real zero stays a zero
    metric(db, "run_7", "youtube", "7d", {"unavailable": "no metrics"})      # unmeasurable is a gap
    reels = ac.reels_at_7d(db)
    assert [r["run_id"] for r in reels] == [f"run_{i}" for i in range(7, 1, -1)]
    assert reels[0]["views"] == {"instagram": 107, "youtube": None, "facebook": 0}
    assert reels[0]["title"] == "Reel 7"


# ------------------------------------------------------------------ renders
def test_one_reel(tmp_path):
    im, info = ac.render(SIX[:1], tmp_path / "one.png", sample=True, today=TODAY)
    assert info["rows"] == 1 and info["titles"] == ["Derinkuyu: the city under Cappadocia"]
    assert info["headline"] == "Instagram leads on the one Reel so far"
    assert {"3,120", "1,150", "1,010"} <= set(texts(info))
    assert im.size[0] == ac.W and (tmp_path / "one.png").exists()


def test_six_reels_newest_on_top(tmp_path):
    im, info = ac.render(SIX + [reel("Seventh", 1, 1, 1)], sample=True, today=TODAY)
    assert info["rows"] == 6                                            # the last six only
    assert info["titles"] == [r["title"] for r in SIX]
    boxes = [next(t["box"] for t in info["texts"] if t["text"] == title) for title in info["titles"]]
    assert all(a[1] < b[1] for a, b in zip(boxes, boxes[1:]))           # the first (newest) Reel is drawn highest
    assert info["headline"] == "Instagram leads on every Reel"
    assert "SAMPLE DATA" in texts(info) and "SAMPLE DATA, NOT AUDIENCE FIGURES" in texts(info)


def test_missing_platform(tmp_path):
    reels = [{**r, "views": {**r["views"], "facebook": None}} for r in SIX]
    reels[2] = {**reels[2], "views": {**reels[2]["views"], "youtube": None}}
    _, info = ac.render(reels, sample=True, today=TODAY)
    assert info["platforms"] == ["instagram", "youtube"]
    assert "Facebook" not in texts(info)                               # no key entry for a platform with no figures
    assert "No Facebook figures yet." in info["subtitle"]
    assert "One Reel has one platform only, not compared." in info["subtitle"]
    assert "—" in texts(info)                                     # the row's missing YouTube figure
    assert info["headline"] == "Instagram leads on every Reel"         # five compared, all Instagram's


@pytest.mark.parametrize("title", [
    "The Sunken City of Heracleion, lost beneath the Bay of Abu Qir for twelve centuries and more",
    "Supercalifragilisticexpialidociousandmore" * 2,
])
def test_long_titles_refuse_instead_of_delivering_truncated_copy(tmp_path, title):
    output = tmp_path / "refused.png"
    with pytest.raises(ValueError, match="Checked copy does not fit"):
        ac.render([reel(title, 1_284_500, 96_400, 12_480)], output, sample=True, today=TODAY)
    assert not output.exists()


def test_large_numbers_and_complete_titles(tmp_path):
    reels = [reel("The Sunken City of Heracleion", 1_284_500, 96_400, 12_480),
             reel("A difficult name", 845_000, 1_020_000, 9_999),
             reel("Flannan Isles lighthouse \U0001F30A the keepers", 999_600, 0, 120)]
    _, info = ac.render(reels, sample=True, today=TODAY)
    probe = ac._Canvas(1)
    title_font = ac._Fonts().sans(ac.TEXT * ac.SS, 600)
    for t, original in zip(info["titles"][:2], reels[:2]):
        assert t == original["title"] and probe.width(t, title_font) <= info["title_width"]
    assert "\U0001F30A" not in info["titles"][2]                        # no emoji drawn as a missing-glyph box
    assert {"1.28M", "96.4K", "12.5K", "845K", "1.02M", "9,999", "1M", "0", "120"} <= set(texts(info))
    assert [ac.axis_label(t, info["ticks"][-1]) for t in info["ticks"]][-1] == "1.5M"


@pytest.mark.parametrize("v,s", [(0, "0"), (999, "999"), (9_999, "9,999"), (10_000, "10K"), (12_480, "12.5K"),
                                 (125_400, "125K"), (999_600, "1M"), (1_284_500, "1.28M"), (12_500_000, "12.5M"),
                                 (2_400_000_000, "2.4B"), (None, "—")])
def test_figures(v, s):
    assert ac.figure(v) == s


def test_ticks_are_whole_and_reach_the_top():
    for v in (1, 3, 7, 100, 3120, 4100, 9999, 12_480, 1_284_500):
        t = ac.ticks(v)
        assert t[0] == 0 and t[-1] >= v and 3 <= len(t) <= 5 and all(float(x).is_integer() for x in t)
    assert ac.ticks(4100) == [0, 2500, 5000]
    assert [ac.axis_label(x, 10_000) for x in ac.ticks(9_999)] == ["0", "2.5K", "5K", "7.5K", "10K"]


# ------------------------------------------------------------------ legibility on the image itself
@pytest.mark.parametrize("reels", [[], SIX[:1], SIX, [reel("A", 5, None, 0), reel("B", None, None, 7)]])
def test_every_text_meets_contrast_and_size_on_the_rendered_image(reels):
    im, info = ac.render(reels, sample=bool(reels), today=TODAY)
    px = im.load()
    assert info["texts"]
    for t in info["texts"]:
        assert t["size"] * ac.PREVIEW_W / ac.W >= ac.MIN_TEXT_AT_PREVIEW, t
        x0, y0, x1, y1 = (int(round(v)) for v in t["box"])
        pixels = [px[x, y] for x in range(max(x0, 0), min(x1, im.width)) for y in range(max(y0, 0), min(y1, im.height))]
        ring = [px[x, y] for x in range(max(x0 - 4, 0), min(x1 + 4, im.width)) for y in (max(y0 - 4, 0), min(y1 + 3, im.height - 1))]
        ground = max(set(ring), key=ring.count)                        # the ground just outside the text's box
        ink = max(pixels, key=lambda p: sum((a - b) ** 2 for a, b in zip(p, ground)))
        assert sum(abs(a - b) for a, b in zip(ground, ac.INK)) <= 12, t  # every text sits on ink
        assert ac.contrast(ink, ground) >= 4.5, (t["text"], ink, ground)
        assert ac.contrast(t["fill"], ac.INK) >= 4.5, t


def test_series_stay_apart_for_colour_blind_readers():
    cvd = ac.cvd_separation()
    for kind in ("normal", "protan", "deutan", "tritan"):
        assert min(cvd[kind].values()) >= 30, (kind, cvd[kind])
    assert min(cvd["contrast_vs_ink"].values()) >= 4.5
    assert len({k for _, _, _, k in ac.PLATFORMS}) == 3               # shape carries the series too


# ------------------------------------------------------------------ the headline is counted, never asserted
@pytest.mark.parametrize("rows,expected", [
    ([], "No audience data yet"),
    ([reel("a", 10, 5, 1)], "Instagram leads on the one Reel so far"),
    ([reel("a", 10, 5, 1), reel("b", 9, 5, 1)], "Instagram leads on every Reel"),
    ([reel("a", 10, 5, 1)] * 5 + [reel("f", 1, 50, 2)], "Instagram leads on 5 of 6 Reels"),
    ([reel("a", 1, 50, 2)] * 4 + [reel("e", 10, 5, 1)] * 2, "YouTube leads on 4 of 6 Reels"),
    ([reel("a", 10, 5, 1), reel("b", 1, 50, 2)], "Instagram and YouTube lead 1 Reel each"),
    ([reel("a", 10, 10, 1), reel("b", 0, 0, 0)], "No platform leads yet"),        # a tie and all zeros: no winner
    ([reel("a", 10, 10, 1), reel("b", 3, 9, 1)], "YouTube leads on 1 of 2 Reels"),
    ([reel("a", 10, None, None), reel("b", 8, None, None)], "Only Instagram has figures yet"),
    ([reel("a", 10, None, None), reel("b", None, 5, None)], "No Reel has two platforms to compare yet"),
    ([reel("a", None, 5, 9), reel("b", 7, None, None)], "Facebook leads on the one Reel compared"),
])
def test_headline_is_computed(rows, expected):
    assert ac.headline(rows) == expected


def test_headline_follows_the_stored_rows(db, tmp_path):
    for i, (ig, yt, fb) in enumerate([(900, 1200, 300), (800, 400, 100), (700, 650, 0)]):
        run(db, f"run_{i}", f"Reel {i}", NOW - timedelta(days=20 - i))
        for p, v in (("instagram", ig), ("youtube", yt), ("facebook", fb)):
            metric(db, f"run_{i}", p, "7d", {"views": v})
        metric(db, f"run_{i}", "instagram", "28d", {"views": 99_999})       # a later snapshot is not the 7-day one
    info = ac.chart_for(db, tmp_path / "c.png", today=TODAY)
    assert info["headline"] == "Instagram leads on 2 of 3 Reels" and info["caption"] == "Instagram leads on 2 of 3 Reels."
    assert info["titles"] == ["Reel 2", "Reel 1", "Reel 0"]
    assert "99,999" not in texts(info) and "100K" not in texts(info)


def test_figures_fall_back_to_a_repo_font_without_plex(tmp_path):
    fonts = ac._Fonts(tmp_path)                                       # an empty folder: no Plex Mono
    assert fonts.mono_name == "Montserrat (fallback)"
    assert fonts.mono(36).size == 36
