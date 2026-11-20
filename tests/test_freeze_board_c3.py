"""Design-freeze board tooling, cycle 3 (docs/reviews/freeze-system-2026-11-15, QF-35a render_surfaces.py): the
evidence fixes are invariants of the board's own records, never pinned pictures.

- provenance: the input record is a union that refuses a file read with two contents; INPUT_HASHES covers every
  media file and does not depend on the order the inputs were read in;
- peer feeds: a partial --peers dir fills what it has and labels the rest STAND-IN; no supplied post repeats;
- pairing: another run of the ground's own topic is REAL copy, a cross-run one STAND-IN; the hook's test claim is
  STAND-IN on every ground;
- refusal tiles: a refused surface of a row is a tile on that row;
- the share block is found on the rest frame below the masthead and above the FOLLOW line, and the rise is
  measured on it;
- row 13: every surface entry point resolves on this tree, and every weight value names its readers;
- the copy set swaps words, never the run's structure.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "docs" / "reviews" / "freeze-system-2026-11-15"
QF35A = ROOT / "docs" / "reviews" / "quality-fixes-2026-11" / "QF-35a"


@pytest.fixture(scope="module")
def mods():
    for p in (str(BOARD), str(QF35A)):
        if p not in sys.path:
            sys.path.insert(0, p)
    names = ("board_inputs", "board_evidence", "board_motion", "board_table", "board", "render_surfaces")
    return {n: importlib.import_module(n) for n in names}


def test_inputs_union_refuses_a_changed_file(mods):
    bi = mods["board_inputs"]
    into = bi.merge({}, {"a": "1", "b": "2"})
    assert bi.merge(into, {"b": "2", "c": "3"}) == {"a": "1", "b": "2", "c": "3"}
    with pytest.raises(RuntimeError):
        bi.merge(into, {"a": "9"})


def test_input_hashes_cover_every_media_file_and_ignore_read_order(mods, tmp_path):
    bi = mods["board_inputs"]
    (tmp_path / "clips").mkdir()
    for rel in ("row01.png", "row01_half.jpg", "clips/x.mp4", "surfaces.json"):
        (tmp_path / rel).write_bytes(b"x")
    stamp = {"head": "h", "src_sha256": "s"}
    one = bi.input_hashes(tmp_path, stamp, "default", {"p1": "a", "p2": "b"})
    two = bi.input_hashes(tmp_path, stamp, "default", dict(reversed(list({"p1": "a", "p2": "b"}.items()))))
    assert set(one) == {"row01.png", "row01_half.jpg", "clips/x.mp4"} and one == two
    assert bi.input_hashes(tmp_path, stamp, "default", {"p1": "a", "p2": "c"})["row01.png"] != one["row01.png"]
    assert bi.input_hashes(tmp_path, stamp, "second", {"p1": "a", "p2": "b"})["row01.png"] != one["row01.png"]


def test_shards_merge_their_input_records(mods):
    b = mods["board"]
    merged = b.merge_shards([{"surfaces": {"x": {}}, "input_files": {"a": "1"}},
                             {"surfaces": {"y": {}}, "input_files": {"a": "1", "b": "2"}}])
    assert merged["input_files"] == {"a": "1", "b": "2"} and merged["count"] == 2


def _peer_dir(tmp_path, feed, n):
    d = tmp_path / feed
    d.mkdir(parents=True)
    for k in range(n):
        Image.new("RGB", (320, 180), (k * 20, 0, 0)).save(d / f"p{k}.jpg")
    return tmp_path


@pytest.mark.parametrize("n", [0, 3, 8, 11])
def test_peer_feed_fills_partial_dirs_and_labels_the_rest(mods, tmp_path, n):
    be = mods["board_evidence"]
    peers = _peer_dir(tmp_path, "yt_home", n)
    pool = [(f"cover__topic{k}_1234567890__youtube", Image.new("RGB", (1920, 1080), (0, k * 30, 0))) for k in range(3)]
    ours = [("cover__ours_1234567891__youtube", Image.new("RGB", (1920, 1080)))]
    tiles, note = be.peer_feed("yt_home", ours, pool, peers)
    assert len(tiles) == 1
    shown = min(n, be.PEER_SLOTS)
    if n == 0:
        assert note.startswith("PEER SLOTS: STAND-IN")
    elif n < be.PEER_SLOTS:
        assert f"{n} supplied" in note and f"other {be.PEER_SLOTS - n} slots STAND-IN" in note
    else:
        assert note.startswith("PEERS:") and "STAND-IN" not in note
    assert be.peer_note("yt_home", shown, peers).count("STAND-IN") == (0 if shown >= be.PEER_SLOTS else 1)


def test_reel_first_frame_block_says_what_it_is(mods):
    """Operator's peer research: a Reel tile that is the video's first frame is labelled so beside the cover."""
    be = mods["board_evidence"]
    pool = [(f"cover__topic{k}_1234567890__reel", Image.new("RGB", (1080, 1920))) for k in range(2)]
    ours = [("REEL FIRST FRAME as the tile (no designed cover) | card__hook__amber__916__f00 | STAND-IN: x",
             Image.new("RGB", (1080, 1920)))]
    for feed in ("ig_grid", "ig_feed", "fb_feed"):
        tiles, _ = be.peer_feed(feed, ours, pool, None)
        assert "REEL FIRST FRAME" in tiles[0][0] and "card__hook__amber__916__f00" in tiles[0][0]


def test_fb_reels_tab_shows_cover_and_first_frame_among_peers(mods, tmp_path):
    """The Page's Reels tab (operator captures, ~312 x 545 tiles): our designed Reel cover and our Reel's first frame
    each sit among the supplied peers at the tab's tile size, labelled."""
    be = mods["board_evidence"]
    d = tmp_path / "fb_reels"
    d.mkdir()
    for k in range(12):
        Image.new("RGB", (312 if k % 2 else 311, 545 if k < 6 else 543), (k * 20, 50, 90)).save(d / f"peer_{k:02d}.png")
    ours = [("cover__amber_room_heist__reel", Image.new("RGB", (1080, 1920), (200, 0, 0))),
            ("REEL FIRST FRAME as the tile (no designed cover) | card__hook__amber__916__f00 | STAND-IN: x",
             Image.new("RGB", (1080, 1920), (0, 200, 0)))]
    tiles, note = be.peer_feed("fb_reels", ours, [], tmp_path)
    assert len(tiles) == 2 and note.startswith("PEERS: 12 supplied") and "8 per block" in note and "STAND-IN" not in note
    assert "Facebook Page Reels tab at 312 px" in tiles[0][0] and "cover__amber_room_heist__reel" in tiles[0][0]
    assert "REEL FIRST FRAME" in tiles[1][0]
    tile = be.feed_tile(ours[0][1], "fb_reels")
    assert tile.size == (312, 545)
    # ours is the middle slot of the 3-column block, at the tab's tile size (red, the cover's colour)
    spec = be.FEEDS["fb_reels"]
    x = spec["gap"] + 1 * (spec["w"] + spec["gap"]) + 100
    y = spec["gap"] + 1 * (545 + spec["gap"]) + 100
    assert tiles[0][1].getpixel((x, y)) == (200, 0, 0)


def test_fb_collage_matches_the_feeds_multi_photo_geometry(mods):
    """Facebook's feed draws a multi-photo post as 2 square halves over 3 square thirds (measured on the operator's
    1137 px capture: top x 0-566 / 570-1137, 566 tall; bottom x 0-376 / 380-756 / 760-1137, ~377 tall; 4 px gaps)."""
    be = mods["board_evidence"]
    boxes = be.fb_collage_boxes(1137)
    assert boxes[:2] == [(0, 0, 566, 566), (570, 0, 1137, 566)]
    assert [(b[0], b[2]) for b in boxes[2:]] == [(0, 376), (380, 756), (760, 1137)]
    assert all(b[1] == 570 and abs((b[3] - b[1]) - 377) <= 1 for b in boxes[2:])
    for w in (390, 500, 1137):   # every width: halves and thirds, each square, no overlap, the full width
        bx = be.fb_collage_boxes(w)
        assert bx[1][2] == w and bx[-1][2] == w
        assert all(abs((b[2] - b[0]) - (b[3] - b[1])) <= 1 for b in bx)
        assert bx[0][2] < bx[1][0] and bx[2][2] < bx[3][0] < bx[3][2] < bx[4][0]


@pytest.mark.parametrize("n", [3, 5, 8])
def test_fb_collage_draws_the_first_five_and_counts_the_rest(mods, n):
    be = mods["board_evidence"]
    photos = [Image.new("RGB", (1080, 1350), (30 * k, 200, 10)) for k in range(n)]
    im = be.fb_collage(photos, 390)
    boxes = be.fb_collage_boxes(390)
    assert im.size == (390, boxes[-1][3])
    for k, b in enumerate(boxes[:min(n, 5 if n <= 5 else 4)]):   # the 5th is dimmed under "+N"
        cx, cy = (b[0] + b[2]) // 2, b[1] + 5
        assert im.getpixel((cx, cy)) == (30 * k, 200, 10)
    b = boxes[4]
    if n > 5:   # "+N" over the 5th: dimmed, white type at its centre
        assert im.getpixel(((b[0] + b[2]) // 2, b[1] + 3)) != (120, 200, 10)
        centre = im.crop((b[0] + 10, b[1] + 10, b[2] - 10, b[3] - 10))
        assert int(np.asarray(centre)[..., 0].max()) > 240
    elif n < 5:  # an empty box stays the gap colour
        assert im.getpixel(((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)) == (39, 39, 39)


def test_a_lone_peer_is_never_repeated_as_more_peers(mods, tmp_path):
    be = mods["board_evidence"]
    peers = _peer_dir(tmp_path, "fb_feed", 1)
    ours = [("social__alpha_site_1793272675__fb_collage", Image.new("RGB", (1137, 950)))]
    tiles, _ = be.peer_feed("fb_feed", ours, [], peers)   # no other run to stand in
    assert len(tiles) == 1 and "each once; no STAND-IN" in tiles[0][0] and "ours: slot 2" in tiles[0][0]


DEV = Path(os.environ.get("RR_FIXTURE_REPO") or r"C:\Users\omokolade\GitHub\relic-and-riddle")
SOCIAL_RUN = "run_richat_structure_sahara_eye_1793272675"


@pytest.mark.skipif(not (DEV / "output" / SOCIAL_RUN / "stories" / "still_layout.json").is_file(),
                    reason="needs the dev checkout's recorded social run")
def test_social_draws_only_what_production_delivers(tmp_path):
    """Lane D's verification: the board redrew the retired Story poll frame and stale archetypes. A retired frame is
    recorded, not drawn; every carousel slide takes today's routing; each Story frame carries its own layout record's
    title size."""
    from src.multi_format import RETIRED_STORY_FRAMES, MultiFormatGenerator
    jobs = tmp_path / "jobs"
    jobs.write_text(f"social {SOCIAL_RUN}\n", encoding="utf-8")
    out = tmp_path / "surfaces.json"
    subprocess.run([sys.executable, str(BOARD / "run_surfaces.py"), str(QF35A / "render_surfaces.py"), str(ROOT),
                    str(DEV), str(out), "--jobs", str(jobs)], check=True, timeout=1500, capture_output=True)
    res = json.loads(out.read_text(encoding="utf-8"))
    tag = SOCIAL_RUN[4:]
    retired = {f"social__{tag}__story_{Path(n).stem}" for n in RETIRED_STORY_FRAMES}
    assert retired and retired <= set(res.get("retired") or {})
    assert not retired & set(res["surfaces"]) and not retired & set(res["errors"])
    stories = {k: v for k, v in res["story_title_px"].items()}
    assert stories and all(isinstance(v, dict) and v["drawn"] and v["story"] for v in stories.values())
    # poll {} as production passes it (QF-80): no frame turns into a question frame, so each draws at the Story's size
    assert all(v["drawn"] == v["story"] for v in stories.values()), stories
    for sub, kind in (("carousel", "carousel"), ("carousel_fb", "fb")):
        recs = json.loads((DEV / "output" / SOCIAL_RUN / sub / "still_layout.json").read_text(encoding="utf-8"))
        rs = sorted((v["recipe"] for v in recs.values() if v.get("recipe")), key=lambda r: int(r["index"]))
        now = MultiFormatGenerator.sequence_archetypes([r["card"] for r in rs])
        for r, a in zip(rs, now):
            key = next(k for k in recs if (recs[k].get("recipe") or {}).get("index") == r["index"])
            name = f"social__{tag}__{kind}_{Path(key).stem}"
            if r["archetype"] not in ("diagram", a):
                assert res["rerouted"][name] == {"recorded": r["archetype"], "current": a}
            else:
                assert name not in (res.get("rerouted") or {})


def test_bridge_ladder_is_the_composers(mods):
    """Lane E: the board draws the bridge through the composer's ladder; its rungs are held to the composer's own
    source, so the two cannot drift apart unseen."""
    import inspect

    from src import end_card
    from src import video_composer as vc
    src = inspect.getsource(vc.VideoComposer._render_with_loop_bridge)
    for fragment in ('"line": card_mod.LOOP_LINE', '"close_push": 0.0, "open_push": 0.0',
                     '"crossfade": card_mod.LOOP_CROSSFADE[1]', '("push", still)', '("FOLLOW", {**kw, "follow": False})',
                     '("push and FOLLOW", {**still, "follow": False})'):
        assert fragment in src, fragment
    bm = mods["board_motion"]
    rungs = dict(bm.bridge_ladder(True))
    assert list(rungs) == ["as made", "without its push", "without FOLLOW", "without its push and FOLLOW"]
    assert rungs["as made"] == {"line": end_card.LOOP_LINE, "follow": True}
    assert rungs["without its push"] == {"line": end_card.LOOP_LINE, "follow": True, "close_push": 0.0,
                                         "open_push": 0.0, "crossfade": end_card.LOOP_CROSSFADE[1]}
    assert rungs["without its push and FOLLOW"]["follow"] is False
    assert [v for v, _ in bm.bridge_ladder(False)] == ["as made", "without its push"]


def test_shipped_bridge_takes_the_first_passing_rung(mods, monkeypatch):
    from src import end_card
    bm = mods["board_motion"]
    calls = []

    def gate(last, first, share, **kw):
        ok = kw.get("close_push") == 0.0
        return {"ok": ok, "problems": [] if ok else ["the seam steps 9.1"], "stats": {"mad_last": 4.0 if ok else 9.1},
                "note": None}

    def bridge(last, first, share, stats, **kw):
        calls.append(kw)
        stats["mad_last"] = 4.0
        return iter(["frame"])
    monkeypatch.setattr(end_card, "check_loop_bridge", gate)
    monkeypatch.setattr(end_card, "loop_bridge", bridge)
    st: dict = {}
    assert list(bm.shipped_bridge(None, None, "x", st, follow=True)) == ["frame"]
    assert st["variant"] == "without its push" and calls == [dict(bm.bridge_ladder(True))["without its push"]]
    assert [t["ok"] for t in st["ladder"]] == [False, True] and st["ladder"][0]["seam"]["mad_last"] == 9.1
    monkeypatch.setattr(end_card, "check_loop_bridge",
                        lambda *a, **k: {"ok": False, "problems": ["hitch"], "stats": {}, "note": None})
    st = {}
    with pytest.raises(bm.BridgeNotShipped):
        bm.shipped_bridge(None, None, "x", st, follow=False)
    assert st["variant"] is None and len(st["ladder"]) == 2


@pytest.mark.skipif(not (DEV / "output" / "run_wieliczka_salt_mine_1792974672").is_dir(),
                    reason="needs the dev checkout's runs")
def test_run_loop_motion_reads_the_runs_own_record(mods):
    """The camera gap: where the run's composition record determines the seam motion (clips the composer does not
    camera-move at both ends), the bridge takes it, as the composer passes loop_motion's."""
    bm = mods["board_motion"]
    motion, source = bm.run_loop_motion(DEV / "output" / "run_wieliczka_salt_mine_1792974672")
    assert motion == {"close_zoom": 1.0, "close_push": 0.0, "open_push": 0.0}
    assert "composition record" in source and "scene_10.mp4" in source and "scene_01.mp4" in source


def test_run_loop_motion_says_when_only_the_default_push_is_available(mods, tmp_path):
    bm = mods["board_motion"]
    run = tmp_path / "run_alpha_site_1793000000"
    run.mkdir()
    motion, source = bm.run_loop_motion(run)
    assert motion is None and source.startswith(bm.DEFAULT_PUSH) and "no composition record" in source
    for k in (1, 2):
        Image.new("RGB", (1600, 1200), (90, 90, 90)).save(run / f"scene_{k:02d}.jpg")
    (run / "alpha_site_master.composition.json").write_text(json.dumps(
        {"scenes": {str(run / "scene_01.jpg"): "x", str(run / "scene_02.jpg"): "y"}}), encoding="utf-8")
    motion, source = bm.run_loop_motion(run)
    assert motion is None and source.startswith(bm.DEFAULT_PUSH) and "camera-moved" in source
    rungs = dict(bm.bridge_ladder(False, {"close_zoom": 1.1, "close_push": 0.002, "open_push": 0.001}))
    assert rungs["as made"]["close_push"] == 0.002 and rungs["without its push"]["close_push"] == 0.0
    assert rungs["without its push"]["close_zoom"] == 1.1   # as the composer's {**kw, ...}: the zoom is kept


@pytest.mark.skipif(not (DEV / "output" / SOCIAL_RUN / "carousel_fb" / "still_layout.json").is_file(),
                    reason="needs the dev checkout's recorded social run")
def test_fb_post_is_publishers_pick(mods):
    """Lane D: the fb_feed collage holds what the publisher posts (facebook_pick: the cover, the strongest middles,
    the closing slide, at most five, in slide order), read with today's archetypes."""
    be = mods["board_evidence"]
    from src.multi_format import FACEBOOK_PHOTOS
    run = DEV / "output" / SOCIAL_RUN
    every = sorted(p.name for p in (run / "carousel_fb").glob("carousel_slide_*.jpg"))
    picked = be.fb_post_slides(run, {"carousel_slide_05.jpg": "colossal_stat"})
    assert len(picked) == min(len(every), FACEBOOK_PHOTOS) and picked == sorted(picked)
    assert picked[0] == every[0] and picked[-1] == every[-1]
    if len(every) > FACEBOOK_PHOTOS:   # today's figure slide is the strongest middle: it makes the post
        assert "carousel_slide_05.jpg" in picked
    assert be.fb_post_slides(DEV / "output" / "no_such_run") == []


@pytest.mark.skipif(not (DEV / "output" / "run_wieliczka_salt_mine_1792974672").is_dir(),
                    reason="needs the dev checkout's runs")
def test_wide_end_screen_ground_says_what_it_is(mods, monkeypatch):
    """Lane E: a 16:9 end screen of a run with no 16:9 master is fed its 9:16 last frame centre-cropped, labelled
    STAND-IN with that reason; a real 16:9 run's own landscape last frame is REAL."""
    bm, be = mods["board_motion"], mods["board_evidence"]
    monkeypatch.setenv("RR_FIXTURE_REPO", str(DEV))
    monkeypatch.syspath_prepend(str(ROOT / "docs" / "reviews" / "freeze-panel-2026-11-04" / "fixtures"))
    fx = importlib.import_module("fixtures")
    cases = fx.load("endcard")
    i = next(k for k, c in enumerate(cases) if c["topic"] == "wieliczka_salt_mine" and c.get("format") == "16:9")
    im, cls, why = bm.wide_last(fx, cases[i])
    assert im.size == (1920, 1080) and cls == be.STAND_IN and "centre-cropped" in why and "no 16:9 master" in why
    be.WIDE_GROUNDS[i] = (cls, why)
    assert be.pairing(f"endscreen__{i}_wieliczka_salt_mine") == (cls, why)
    j = next(k for k, c in enumerate(cases) if c.get("format") == "16:9" and "stands in" not in str(c.get("label")))
    _im, cls_j, why_j = bm.wide_last(fx, cases[j])
    assert cls_j == be.REAL and "16:9 master" in why_j   # Richat's 16:9 run delivered one


def test_seam_reading_is_the_renderers(mods, monkeypatch):
    from src import end_card
    bm = mods["board_motion"]
    r = bm.seam_reading({"mad_last": 3.2, "mad_measure": "270x480 luma"})
    assert r["mad_last"] == 3.2 and r["measure"] == "270x480 luma" and r["gate_max"] == end_card.LOOP_MAX_MAD
    monkeypatch.setattr(end_card, "LOOP_SEAM_MEASURE", "the tree's measure", raising=False)
    assert bm.seam_reading({"mad_last": 1.0})["measure"] == "the tree's measure"


def test_peer_feed_without_a_dir_names_the_option(mods):
    be = mods["board_evidence"]
    assert "board.py --peers <dir>" in be.peer_note("ig_grid", 0, None)


def test_pairing_prefers_the_grounds_own_topic(mods):
    be = mods["board_evidence"]
    own = be.pairing("cover__alpha_site__bright__youtube__copy_alpha_site_1792993374")
    other = be.pairing("cover__alpha_site__bright__youtube__copy_beta_site_1792790550")
    forced = be.pairing("thumbnail_badge__alpha_site__youtube__copy_alpha_site_1792993374")
    assert own[0] == be.REAL and other[0] == be.STAND_IN and forced[0] == be.FORCED
    assert be.run_topic("run_alpha_site_1792740961_long") == "alpha_site"


@pytest.mark.parametrize("ground", ["richat", "amber", "obelisk"])
def test_the_hook_test_claim_is_never_real(mods, ground):
    be = mods["board_evidence"]
    cls, why = be.pairing(f"card__hook__{ground}__916__f00")
    assert cls == be.STAND_IN and "test" in why


def test_refused_surfaces_get_tiles_on_their_own_row(mods):
    b = mods["board"]
    surfaces = {"surfaces": {"cover__wieliczka_salt_mine__youtube": {"refused": "too long", "class": "LayoutError"},
                             "cover__wieliczka_salt_mine__reel": {"sha256": "x"},
                             "cover__other_place__youtube": {"refused": "x"}},
                "errors": {"cover__amber_room_heist__youtube": "Traceback\nValueError: boom", "captions": "tb"}}
    pngs = {"cover__wieliczka_salt_mine__reel", "cover__amber_room_heist__reel"}
    t4 = b.undrawn_tiles(4, ("cover__",), pngs, surfaces)
    names = [n.split(" | ")[0] for n, _ in t4]
    assert names == ["cover__amber_room_heist__youtube", "cover__wieliczka_salt_mine__youtube"]
    assert all("REFUSED" in n or "ERROR" in n for n, _ in t4)
    assert b.undrawn_tiles(3, ("cover__",), pngs, surfaces) == []   # a YouTube refusal is not on the cover row


class _Seq:
    def __init__(self, frames, stats, size):
        self.kept = dict(enumerate(frames))
        self.n = len(frames)
        self.stats, self.size = stats, size


def _card(offset, words=4):
    """A synthetic 9:16 end card: dim ground, a masthead and rule up top, a 3-line share block `offset` px low, a
    FOLLOW line."""
    im = Image.new("RGB", (1080, 1920), (22, 22, 26))
    d = ImageDraw.Draw(im)
    d.rectangle((300, 500, 780, 560), fill=(235, 225, 205))       # masthead
    d.rectangle((480, 620, 600, 625), fill=(200, 160, 60))        # rule
    for k in range(3):
        y = 820 + 72 * k + offset
        d.rectangle((260, y, 260 + 150 * min(words, 4), y + 44), fill=(240, 236, 228))
    d.rectangle((330, 1103, 750, 1129), fill=(240, 236, 228))     # FOLLOW
    return im


def test_share_block_found_and_rise_measured(mods):
    bm = mods["board_motion"]
    stats = {"box": [330, 1103, 750, 1129], "follow": True, "share_size": 58, "safe_width": 860,
             "masthead": {"box": [300, 500, 780, 560]}}
    frames = [_card(o) for o in (13, 9, 5, 2, 0, 0)]
    seq = _Seq(frames, stats, (1080, 1920))
    box, how = bm.share_block_box(seq, seq.n - 1)
    assert box is not None and 800 <= box[1] <= 830 and 1000 <= box[3] <= 1015, (box, how)
    assert box[1] > 625 and box[3] < 1103        # below the rule, above FOLLOW
    rise = bm.rise_ink_px(seq, box, seq.n - 1)
    assert [rise[k] for k in range(seq.n)] == [13, 9, 5, 2, 0, 0]


def test_row13_entry_points_and_weights_come_from_this_tree(mods, monkeypatch):
    monkeypatch.chdir(ROOT)
    bt = mods["board_table"]
    rows = bt.code_rows(ROOT / "src")
    assert len(rows) == len(bt.ENTRY) + 1
    assert not [r for r in rows[1:] if r[1] == "not in this tree"], rows
    weights = bt.weight_rows(mods["board"].specimen_weights(), ROOT / "src")
    assert len(weights) > 1 and all(r[3] for r in weights[1:])


def test_copy_swap_keeps_structure(mods):
    rs = mods["render_surfaces"]
    entry = json.loads((BOARD / "portability_copy.json").read_text(encoding="utf-8"))[0]
    payload = {"title": "T", "carousel_slides": [{"heading": "a", "body": "b", "x": 1}] * 6,
               "story_slides": [{"kicker": "k"}], "thumbnail": {"line1": "L", "extra": 2}, "stat_cards": [1]}
    p = rs.copy_swap(payload, entry)
    assert len(p["carousel_slides"]) == 6 and all(s["x"] == 1 for s in p["carousel_slides"])
    assert p["thumbnail"]["extra"] == 2 and p["thumbnail"]["line1"] == entry["thumbnail"]["line1"]
    assert p["stat_cards"] == [1] and payload["title"] == "T"
    words = [{"word": "w", "start": i * 0.3, "dur": 0.2} for i in range(5)]
    swapped = rs.copy_words(words, entry)
    assert [w["start"] for w in swapped] == [w["start"] for w in words]
    assert [w["word"] for w in swapped] == entry["narration_script"].split()[:5]


def test_copy_set_is_generic(mods):
    """Portability (operator 2026-11-19): the stress copy names no topic of this channel."""
    text = (BOARD / "portability_copy.json").read_text(encoding="utf-8").lower()
    topics = {p.stem.replace("_", " ") for p in (ROOT / "data" / "fact_sheets").glob("*.json")}
    assert not [t for t in topics if t in text]
    assert "relic" not in text and "riddle" not in text
