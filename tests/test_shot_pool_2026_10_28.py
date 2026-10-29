"""
Production Nazca, 28 Oct 2026 (runs ..._1793192559 and ..._1793195722): three causes that reached the render.

1. Shot reuse. Six stills for nine photo beats; the judge said beat 7 repeated beat 5's photo, the repair withdrew
   that photo as a mismatch, and the re-plan cycled the rest (four photos shown twice) while the pool's fifth
   context photo was never used. A repeat finding never withdraws a photo now; a pool short of pictures takes its
   reserve context shots, then holds a picture over two lines; what still repeats is on the card.
2. A context photo (the highway through the desert) was animated by AI motion and carried into the regeneration:
   only Topic or Kept photos open the video or are animated.
3. An aircraft wing along the top 9% of photo 6: the picture screen names a foreign object along an edge, and the
   band is cropped away as a text band is (or the photo is set aside when it is larger).
Photo names and lines below are test data shaped like the Nazca pool.
"""

import json
from pathlib import Path

import pytest
from PIL import Image

from src import beat_images, config, image_review, motion, scene_planner as sp
from src.lineage import record_derived
from src.models import QAIssue, QAReport, ScenePlan


def _jpg(path: Path, colour=(120, 110, 90), size=(64, 48)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, colour).save(path)
    return path


def _pool(folder: Path, entries) -> list:
    """Photos with a credits.json: (file, shows_topic, relevance, vision reason)."""
    images = []
    for i, (name, shows, relevance, reason) in enumerate(entries):
        _jpg(folder / name, (20 + 9 * i, 100, 140 - 5 * i))
        images.append({"file": name, "title": f"File:{name}", "relevance": relevance, "width": 2400, "height": 1800,
                       "vision_review": {"shows_topic": shows, "usable": True, "safe": True, "duplicate_of": None,
                                         "reason": reason, "kept": True}})
    (folder / "credits.json").write_text(json.dumps({"reviewed": True, "images": images}), encoding="utf-8")
    sp._folder_credits.cache_clear()
    return [folder / name for name, *_ in entries]


NAZCA = [("02_commons.jpg", "yes", 5, "Clear aerial photograph of the hummingbird geoglyph."),
         ("06_commons.jpg", "yes", 5, "Wide aerial view of the hummingbird with adjacent trapezoids."),
         ("08_commons.jpg", "yes", 7, "Clear aerial view of trapezoidal geoglyphs on the plain."),
         ("10_commons.jpg", "yes", 5, "Centred view of the hillside figure."),
         ("03_commons.jpg", "partly", 7, "The desert ground from ground level."),
         ("04_commons.jpg", "partly", 5, "Museum display of a ceramic vessel with hummingbirds."),
         ("09_commons.jpg", "partly", 7, "The Pan-American Highway running through the reserve.")]


def _beats(n, length=3.2):
    return [{"start": round(i * length, 3), "end": round((i + 1) * length, 3), "text": f"Line {i + 1}."}
            for i in range(n)]


# ------------------------------------------------------------------ 1. shot reuse
REUSE = ("The footage in Beat 7 is an identical reuse of the wide shot from Beat 1 and Beat 5, showing no visual "
         "connection to the wooden stakes or surveying tools mentioned in the narration.")


@pytest.mark.parametrize("text, repeat", [
    (REUSE, True),
    ("Beat 4 repeats the photo of beat 2.", True),
    ("The same aerial shot is used again here.", True),
    ("This picture was already shown twice.", True),
    ("The footage shows a modern car driving on an asphalt highway, out of place for the narration.", False),
    ("The visual shows a drill core, which contradicts the narration about modern infrastructure.", False),
])
def test_a_finding_that_a_picture_repeats_is_told_apart(text, repeat):
    assert beat_images.repeat_finding(text) is repeat


def test_a_reuse_finding_withdraws_nothing_from_a_short_pool(tmp_path):
    stills = _pool(tmp_path / "assets", [n for n in NAZCA if n[0] != "04_commons.jpg"])   # six usable stills
    planned = [stills[i % 6] for i in range(9)]                                           # nine photo beats
    qa = QAReport(summary="", issues=[QAIssue(beat=7, severity="major", issue=REUSE)])
    assert beat_images.mismatched_beats(qa) == [7]        # still searched for: a new picture for that line helps
    assert beat_images.wrong_subject_beats(qa) == []
    assert sp.withdrawals(planned, beat_images.wrong_subject_beats(qa), stills) == set()
    assert len({sp.picture_id(p) for p in stills}) == 6   # the distinct count stands


def test_a_wrong_photo_is_withdrawn_while_three_stills_stay(tmp_path):
    stills = _pool(tmp_path / "assets", NAZCA[:6])
    crop = _jpg(tmp_path / "cache" / "ab" / "03_commons.jpg", (1, 2, 3))   # a cropped copy of the wrong photo
    record_derived(crop, [stills[4]], "text_crop", {})
    car = QAReport(summary="", issues=[QAIssue(beat=5, severity="major", issue="The footage shows a modern car on "
                                               "a highway, which contradicts the narration.")])
    wrong = beat_images.wrong_subject_beats(car)
    assert wrong == [5]
    # Even from a pool short of pictures (nine photo beats, six stills): the re-plan must not move it elsewhere.
    planned = [stills[i % 6] for i in range(9)]
    assert sp.withdrawals(planned, wrong, stills + [crop]) == {stills[4], crop}   # with its cropped copy
    # Never below three distinct stills (the gap search's new photos count: the caller asks after it).
    assert sp.withdrawals(planned, wrong, stills[2:5]) == set()
    assert sp.withdrawals(planned, wrong, stills[2:5] + [_jpg(tmp_path / "gap05_1.jpg", (9, 9, 9))]) == {stills[4]}


def _line_check(yes):
    """check(picture, line) for hold_for_short_pool: True for the (picture name, line) pairs in `yes`."""
    asked = []

    def check(picture, line):
        asked.append((Path(picture).name, line))
        return (Path(picture).name, line) in yes
    return check, asked


def test_a_verified_first_copy_keeps_its_line(tmp_path):
    """Review probe: H B A A C, beat 4 held (A shown again, it was found not to show line 4). A on line 3 was judged
    right: it stays; line 4 joins it as one longer shot, said as held over."""
    h, b, a, c = (_jpg(tmp_path / f"{n}.jpg", (i * 50, 3, 3)) for i, n in enumerate("HBAC"))
    durations = [3.0, 3.0, 2.5, 3.5, 4.0]
    beats, t = [], 0.0
    for i, d in enumerate(durations):
        beats.append({"start": t, "end": t + d, "text": f"line{i + 1}"})
        t += d
    fit = {"fits": [2, 3, 5], "held": [{"beat": 4, "from": "x.jpg", "to": "A.jpg"}],
           "unshown": [{"beat": 4, "fallback": "held", "line": "line4", "picture": "x.jpg", "reason": "r"}]}
    fits = {1: True, 2: True, 3: False, 4: True}
    check, asked = _line_check(set())
    out, scenes, durs, where, report = sp.hold_for_short_pool(beats, [h, b, a, a, c], durations, fits=fits, check=check)
    assert [p.name for p in scenes] == ["H.jpg", "B.jpg", "A.jpg", "C.jpg"] and durs == [3.0, 3.0, 6.0, 4.0]
    assert [x["text"] for x in out] == ["line1", "line2", "line3 line4", "line5"] and asked == []   # same picture
    remapped = sp.remap_beats(fit, where, scenes, report["shown"])
    assert remapped["unshown"] == [{"beat": 3, "fallback": "held_over", "line": "line4", "picture": "A.jpg", "reason": "r"}]
    assert "the previous shot is held over it" in sp.fit_note(remapped) and remapped["fits"] == [2, 3, 4]


def test_a_line_takes_a_neighbours_picture_only_when_it_is_judged_to_show_it(tmp_path):
    a, b, c = (_jpg(tmp_path / f"{n}.jpg", (i * 40, 0, 0)) for i, n in enumerate("abc"))
    beats = _beats(5, 3.0)
    fits = {i: True for i in range(1, 5)}
    # Nobody can look (or nothing fits): the repeats stay, and the card says so.
    for check in (None, _line_check(set())[0]):
        out, scenes, durations, where, report = sp.hold_for_short_pool(beats, [a, b, c, a, b], [3.0] * 5, fits=fits,
                                                                       check=check, available=3)
        assert scenes == [a, b, c, a, b] and where == [0, 1, 2, 3, 4] and report["merged"] == []
        assert [r["beat"] for r in report["repeats"]] == [4, 5] and report["short"]
        assert "beat 4 repeats beat 1's picture (a.jpg)" in sp.hold_note(report)
    # c is judged to show lines 4 and 5 (a not line 5): both later copies go, the first copies keep their lines.
    check, asked = _line_check({("c.jpg", "Line 4."), ("c.jpg", "Line 5.")})
    out, scenes, durations, where, report = sp.hold_for_short_pool(beats, [a, b, c, a, b], [3.0] * 5, fits=fits,
                                                                   check=check, available=3)
    assert scenes == [a, b, c] and durations == [3.0, 3.0, 9.0] and report["repeats"] == []
    assert sum(durations) == 15.0 and max(durations) <= sp.HELD_SHOT_MAX
    assert [x["start"] for x in out] == [0.0, 3.0, 6.0] and out[-1]["end"] == 15.0   # on the narration's beat starts
    assert where == [0, 1, 2, 2, 2] and report["shown"] == [4, 5] and report["merged"] == [3]
    note = sp.hold_note(report)
    assert note.startswith("🔁 Pictures: 3 distinct for 5 photo beats") and "beat 3 holds one picture" in note


def test_a_hold_in_a_pool_that_is_not_short_is_not_reported_as_one(tmp_path):
    a, b = (_jpg(tmp_path / f"{n}.jpg", (i * 90, 0, 0)) for i, n in enumerate("ab"))
    # The fit check held b for line 3 though the pool had 5 pictures: nothing is joined (the held shot stays a
    # reframed cut, as before), nothing is said, and the beats, durations and numbering stand.
    beats = _beats(3)
    out, scenes, durations, where, report = sp.hold_for_short_pool(beats, [a, b, b], [3.2] * 3,
                                                                   fits={1: True, 2: False}, available=5,
                                                                   check=lambda p, line: True)
    assert out == beats and scenes == [a, b, b] and durations == [3.2] * 3 and where == [0, 1, 2]
    assert report["merged"] == [] and not report["short"] and sp.hold_note(report) == ""


def test_an_approved_storyboard_is_not_joined_and_its_repeats_are_said(tmp_path):
    a, b, c = (_jpg(tmp_path / f"{n}.jpg", (i * 40, 0, 0)) for i, n in enumerate("abc"))
    _, scenes, _, where, report = sp.hold_for_short_pool(_beats(4, 3.0), [a, b, c, b], [3.0] * 4, fits={3: False},
                                                         check=lambda p, line: True, available=3, merge=False)
    assert scenes == [a, b, c, b] and where == [0, 1, 2, 3] and report["storyboard"]
    note = sp.hold_note(report)
    assert "beat 4 repeats beat 2's picture" in note and "approved storyboard is kept as it is" in note


def test_a_held_over_beat_is_said_once_on_the_card(tmp_path):
    a, b, c = (_jpg(tmp_path / f"{n}.jpg", (i * 40, 0, 0)) for i, n in enumerate("abc"))
    fit = {"fits": [2], "held": [{"beat": 3, "from": "x.jpg", "to": "b.jpg"}],
           "unshown": [{"beat": 3, "fallback": "held", "line": "Line 3.", "picture": "x.jpg", "reason": "r"}]}
    _, scenes, _, where, report = sp.hold_for_short_pool(_beats(4), [a, b, b, c], [3.2] * 4, fits={1: True, 2: False},
                                                         available=3)
    remapped = sp.remap_beats(fit, where, scenes, report["shown"])
    assert scenes == [a, b, c] and "held over it" in sp.fit_note(remapped)
    assert sp.hold_note(report, fit_report=remapped) == ""        # the fit check's line says it already


def test_a_repeat_that_cannot_be_held_is_on_the_card(tmp_path):
    a, b = (_jpg(tmp_path / f"{n}.jpg", (i * 90, 0, 0)) for i, n in enumerate("ab"))
    # Every beat is as long as a held shot may be with one more: nothing can join, the repeat stays and is said.
    _, scenes, durations, _, report = sp.hold_for_short_pool(_beats(3, 4.6), [a, b, a], [4.6] * 3,
                                                             check=lambda p, line: True)
    assert scenes == [a, b, a] and report["merged"] == []
    assert report["repeats"] == [{"beat": 3, "first": 1, "picture": "a.jpg"}]
    assert "beat 3 repeats beat 1's picture (a.jpg)" in sp.hold_note(report)


def test_clips_are_never_joined_and_a_full_pool_is_left_alone(tmp_path):
    a, b, c = (_jpg(tmp_path / f"{n}.jpg", (i * 40, 9, 9)) for i, n in enumerate("abc"))
    graphic = tmp_path / "graphic_map.mp4"
    graphic.write_bytes(b"0")
    beats = _beats(4)
    yes = lambda p, line: True  # noqa: E731
    same = sp.hold_for_short_pool(beats, [a, graphic, b, c], [3.2] * 4, check=yes)
    assert same[1] == [a, graphic, b, c] and same[3] == [0, 1, 2, 3] and sp.hold_note(same[4]) == ""
    # A repeat next to a graphic joins the photo on its other side, never the graphic.
    _, scenes, _, _, report = sp.hold_for_short_pool(beats, [a, b, graphic, b], [3.0] * 4, check=yes)
    assert scenes == [a, b, graphic, b] and report["repeats"]          # its only photo neighbour is the clip
    _, scenes, _, _, report = sp.hold_for_short_pool(_beats(5), [a, graphic, b, a, c], [3.0] * 5, check=yes)
    assert graphic in scenes and scenes.count(a) == 1 and report["repeats"] == []


def test_the_video_checks_beat_numbers_are_said_in_the_narrations_beats():
    where = [0, 1, 1, 2, 3, 3, 4]
    assert sp.renumber_feedback("Beat 2: the picture repeats Beat 4.", where) == "Beat 2+3: the picture repeats Beat 5+6."
    assert sp.renumber_feedback("Beats 4 and 5 repeat.", where) == "Beats 5+6 and 7 repeat."
    assert sp.renumber_feedback("beats 1, 2 and 4", where) == "beats 1, 2+3 and 5+6"
    assert sp.renumber_feedback("beats 2-3 show a car", where) == "beats 2-4 show a car"
    assert sp.original_beats([2, 4], where) == {2: [2, 3], 4: [5, 6]}
    assert sp.renumber_feedback("Beat 3: x", list(range(4))) == "Beat 3: x"
    # A count after a beat number is not a beat; only an unspaced dash (or "to") is a range.
    assert sp.renumber_feedback("Beat 4 - 5 people visible", where) == "Beat 5+6 - 5 people visible"
    assert sp.renumber_feedback("Beat 3 and 4 seconds later", where) == "Beat 4 and 4 seconds later"
    assert sp.renumber_feedback("beats 2 to 3", where) == "beats 2 to 4"


def test_a_cancel_inside_the_held_shot_check_is_not_swallowed(tmp_path):
    from src.operator_jobs import JobCancelled
    a, b, c = (_jpg(tmp_path / f"{n}.jpg", (i * 40, 0, 0)) for i, n in enumerate("abc"))

    def cancelled(picture, line):
        raise JobCancelled("Cancelled by operator")
    with pytest.raises(JobCancelled):
        sp.hold_for_short_pool(_beats(5, 3.0), [a, b, c, a, b], [3.0] * 5, fits={i: True for i in range(5)},
                               check=cancelled, available=3)


def test_no_repeats_knows_a_cropped_copy_is_its_original(tmp_path):
    o, x, y = (_jpg(tmp_path / f"{n}.jpg", (i * 50, 1, 1)) for i, n in enumerate("oxy"))
    crop = _jpg(tmp_path / "crop" / "o.jpg", (3, 3, 3))
    record_derived(crop, [o], "text_crop", {})
    assert sp.picture_id(crop) == sp.picture_id(o) != sp.picture_id(x)
    assert sp.no_repeats([o, x, crop], [o, x, y]) == [o, x, y]
    # The reserve fills a repeat only once the pool's own unused photos are gone.
    r = _jpg(tmp_path / "r.jpg", (200, 1, 1))
    assert sp.no_repeats([o, x, o, x], [o, x, y], reserve=[r]) == [o, x, y, r]
    assert sp.no_repeats([o, x, y], [o, x, y], reserve=[r]) == [o, x, y]


def test_the_fit_report_follows_the_held_beats():
    report = {"unshown": [{"beat": 3, "fallback": "held", "line": "l", "picture": "p.jpg", "reason": "r"},
                          {"beat": 5, "fallback": "kept", "line": "m", "picture": "q.jpg", "reason": "r"},
                          {"beat": 6, "fallback": "kept", "line": "n", "picture": "z.jpg", "reason": "r"}],
              "swapped": [{"beat": 4, "from": "a", "to": "b"}]}
    scenes = [Path(f"{n}.jpg") for n in ("h", "p", "s", "y")]
    # Beat 6 joined beat 5's picture, which was judged to show its line: it is no longer unshown.
    out = sp.remap_beats(report, [0, 1, 1, 2, 3, 3], scenes, shown=[6])
    assert [u["beat"] for u in out["unshown"]] == [2, 4] and out["swapped"][0]["beat"] == 3
    assert out["unshown"][0]["fallback"] == "held_over" and out["unshown"][1]["fallback"] == "kept"
    assert [u["picture"] for u in out["unshown"]] == ["p.jpg", "y.jpg"]   # the picture each beat now shows
    assert "the previous shot is held over it" in sp.fit_note(out)
    assert report["unshown"][0]["beat"] == 3                         # the report given is not changed


def test_nazca_pool_uses_its_fifth_context_photo_and_says_what_is_short(tmp_path, monkeypatch):
    """Seven approved photos (4 Topic, 3 Context), eleven beats with two graphics: the video takes 6 (at most 2
    Context), and 04 was never used while 02/03/06/10 were shown twice."""
    from src.visual_engine import VisualEngine
    assets = tmp_path / "assets"
    _pool(assets / "nazca", NAZCA)
    engine = VisualEngine(assets_dir=assets)
    credits = json.loads((assets / "nazca" / "credits.json").read_text(encoding="utf-8"))["images"]
    assert [c["file"] for c in image_review.usable_photos(credits)] == [
        "08_commons.jpg", "02_commons.jpg", "06_commons.jpg", "10_commons.jpg", "03_commons.jpg", "09_commons.jpg"]
    stills = [assets / "nazca" / c["file"] for c in image_review.usable_photos(credits)]
    reserve = engine.reserve_sequence({"id": "nazca"})
    assert [p.name for p in reserve] == ["04_commons.jpg"]
    assert image_review.readiness(credits)["usable"] == 6              # readiness and approval still count 6

    graphics = []
    for kind in ("map", "timeline"):
        clip = tmp_path / "graphics" / f"graphic_{kind}.mp4"
        clip.parent.mkdir(exist_ok=True)
        clip.write_bytes(b"0")
        _jpg(clip.with_suffix(".jpg"))
        graphics.append(clip)
    media = stills + graphics
    beats = _beats(11)
    assert sp.still_shortfall(len(beats), media) == 5   # as if every beat took a photo: the reserve is used only as needed

    by = {p.name: i + 1 for i, p in enumerate(media)}
    picks = ["08_commons.jpg", "graphic_map.mp4", "graphic_timeline.mp4", "02_commons.jpg", "03_commons.jpg",
             "06_commons.jpg", "02_commons.jpg", "10_commons.jpg", "09_commons.jpg", "03_commons.jpg", "06_commons.jpg"]
    monkeypatch.setattr(sp.free_llm, "enabled", lambda: True)
    monkeypatch.setattr(sp.free_llm, "available", lambda: True)
    monkeypatch.setattr(sp.free_llm, "call", lambda *a, **k: ScenePlan(images=[by[n] for n in picks], notes="planned"))
    monkeypatch.setattr(sp, "lock_narrative_graphics", lambda beats_, graphics_: {1: graphics[0], 2: graphics[1]})
    scenes, durations, _ = sp.plan_scenes(beats, media, use_storyboard=False, reserve=reserve)
    names = [p.name for p in scenes]
    assert "04_commons.jpg" in names and names[0] != "04_commons.jpg"   # used, but never as the opening
    assert names.count("02_commons.jpg") == 1                          # the first repeat took the reserve

    fits = {k: True for k, p in enumerate(scenes) if k and p.suffix == ".jpg"}   # every placed photo fits its line
    available = len(media) - len(graphics) + len(reserve)
    # Nobody can judge a neighbour's picture against a repeat's line: the repeats stay, and the card says so.
    _, kept, _, _, report = sp.hold_for_short_pool(beats, scenes, durations, fits=fits, available=available)
    assert kept == scenes and len(report["repeats"]) == 2 and report["short"]
    note = sp.hold_note(report, ["04_commons.jpg"])
    assert note.startswith("🔁 Pictures: 7 distinct for 9 photo beats")
    assert "1 context photo(s) beyond the usual cap used rather than repeat a picture (04_commons.jpg)" in note
    assert "beat 10 repeats beat 5's picture (03_commons.jpg)" in note
    # Where the neighbour's picture is judged to show the repeat's line, the line joins it: 03's second copy joins
    # 09. The last line repeats the opening's picture and could join only that held shot, past HELD_SHOT_MAX: said.
    beats2, held, durations2, where, report = sp.hold_for_short_pool(beats, scenes, durations, fits=fits,
                                                                     check=lambda p, line: True, available=available)
    assert [p.name for p in held][-2:] == ["09_commons.jpg", "06_commons.jpg"] and len(held) == 10
    assert report["merged"] == [9] and report["shown"] == [10]
    assert report["repeats"] == [{"beat": 10, "first": 1, "picture": "06_commons.jpg"}]
    assert abs(sum(durations2) - sum(durations)) < 1e-6 and all(d <= sp.HELD_SHOT_MAX for d in durations2)
    note = sp.hold_note(report, ["04_commons.jpg"])
    assert "beat 9 holds one picture over two or more lines" in note and "beat 10 repeats beat 1's picture" in note


# ------------------------------------------------------------------ 2. context shots never open or move
@pytest.fixture
def mixed(tmp_path):
    """A context photo first in the plan, then Topic photos."""
    return _pool(tmp_path / "assets", [
        ("09_commons.jpg", "partly", 7, "Aerial view of the highway running through the whole desert reserve."),
        ("08_commons.jpg", "yes", 7, "Shows trapezoidal lines on the plain."),
        ("02_commons.jpg", "yes", 5, "Shows the hummingbird figure."),
    ])


def test_a_context_shot_never_opens_the_video(mixed):
    ctx, top, top2 = mixed
    assert sp.strong_shot(top) and not sp.strong_shot(ctx)
    assert sp.hook_shot(mixed) is None                       # its scale words are a context photo's
    assert sp.open_on_scale([ctx, top2, top], mixed) == [top, top2, ctx]    # the first strong one, already placed
    assert sp.open_on_scale([ctx, top2], [ctx, top, top2]) == [top2, ctx]   # placed first, pool order otherwise
    # An approved storyboard's opening is the operator's: it stands, and a context shot there is said on the card.
    note = sp.storyboard_opening_note(sp.STORYBOARD_NOTES, [ctx, top])
    assert "09_commons.jpg" in note and note.startswith("⚠️ Opening")
    assert sp.storyboard_opening_note(sp.STORYBOARD_NOTES, [top, ctx]) == ""
    assert sp.storyboard_opening_note("planned", [ctx, top]) == ""
    # A pool with no strong photo at all keeps the plan (a frame needs a picture).
    assert sp.open_on_scale([ctx], [ctx]) == [ctx]


def test_a_photo_you_supplied_still_opens_and_moves(tmp_path):
    own = _jpg(tmp_path / "mine" / "01.jpg")
    assert sp.strong_shot(own) and motion.animatable(own)
    found = _jpg(tmp_path / "beat_images" / "beat02_1.jpg")   # a photo found for one line
    (found.parent / "credits.json").write_text(json.dumps({"images": [{"file": found.name}]}), encoding="utf-8")
    sp._folder_credits.cache_clear()
    assert not sp.strong_shot(found)


@pytest.fixture
def paid(monkeypatch):
    monkeypatch.setattr(config, "MEDIA_MONTHLY_BUDGET_USD", 5.0)
    monkeypatch.setattr(config, "MOTION_MAX_CLIPS", 2)
    animated = []

    def animate(db, img, out, **k):
        animated.append(Path(img).name)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"clip")
        return out, "kling-3.0"
    monkeypatch.setattr(motion, "animate", animate)
    return animated


def test_motion_animates_only_the_topic_photo(db, mixed, paid, tmp_path):
    ctx, top, _ = mixed
    scenes, used = motion.apply(db, [ctx, top], tmp_path / "motion")
    assert paid == ["08_commons.jpg"] and used == ["kling-3.0"]
    assert scenes[0] == ctx and scenes[1].name.startswith("motion_")


def test_a_clip_made_from_a_context_photo_is_not_reused(db, mixed, paid, tmp_path):
    ctx, top, _ = mixed
    old_clip = tmp_path / "old" / "motion_01.mp4"
    old_clip.parent.mkdir()
    old_clip.write_bytes(b"paid earlier")
    memo = {str(ctx): (old_clip, "kling-3.0")}
    scenes, used = motion.apply(db, [ctx, top], tmp_path / "motion", memo=memo)
    assert scenes[0] == ctx and old_clip not in scenes and paid == ["08_commons.jpg"]
    # Without a budget (a dry run) nothing new is bought, and the context clip still is not placed.
    scenes, used = motion.apply(db, [ctx], tmp_path / "motion", memo=memo, allow_paid=False)
    assert scenes == [ctx] and used == []


def test_a_held_shot_longer_than_the_clip_is_not_animated(db, mixed, paid, tmp_path):
    _, top, top2 = mixed
    scenes, _ = motion.apply(db, [top, top2], tmp_path / "motion", durations=[motion.SECONDS_OUT + 1.0, 3.2])
    assert paid == ["02_commons.jpg"] and scenes[0] == top


# ------------------------------------------------------------------ 3. a foreign object along an edge
@pytest.fixture
def screen(monkeypatch, tmp_path):
    monkeypatch.setattr(beat_images, "TEXT_CROP_DIR", tmp_path / "text_crop")
    monkeypatch.setattr(config, "PICTURE_VISION_CHECK", True)


def _with_wing(path: Path, band=0.09, size=(1600, 2400)) -> Path:
    """A desert photo with a dark red wing across the top `band` of it."""
    im = Image.new("RGB", size, (196, 170, 130))
    w, h = size
    im.paste((120, 20, 25), (0, 0, w, int(h * band)))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, "JPEG", quality=92)
    return path


def _looks(monkeypatch, wing_box, height=2400):
    looks = []

    def look(path, query, size=512):
        with Image.open(path) as im:
            h = im.height
        looks.append((Path(path).name, h))
        base = {"checked": True, "shows": True, "burned_in_text": False, "reason": "aerial view of the geoglyphs"}
        if h == height:   # the original, with its wing
            return {**base, "edge_object": "an aircraft wing", "object_edge": "top", "object_box": wing_box}
        return base
    monkeypatch.setattr(beat_images, "picture_look", look)
    return looks


def test_a_wing_along_the_top_edge_is_cropped_away(screen, monkeypatch, tmp_path):
    photos = [_with_wing(tmp_path / "06_commons.jpg"), _jpg(tmp_path / "08_commons.jpg", size=(1600, 1200))]
    looks = _looks(monkeypatch, [0, 0, 90, 1000])
    kept = beat_images.without_burned_in_text(photos, "Nazca Lines", minimum=1)
    crop = kept[0]
    assert crop != photos[0] and crop.name == "06_commons.jpg" and kept[1] == photos[1]
    with Image.open(crop) as im:
        assert im.height == 2400 - round(2400 * 0.105)
        r, g, b = im.convert("RGB").getpixel((800, 2))
        assert r > 150 and g > 130                                     # the first row is desert, not the wing
    record = json.loads(Path(str(crop) + ".lineage.json").read_text(encoding="utf-8"))
    assert record["operation"] == beat_images.EDGE_OBJECT_CROP
    assert record["settings"]["edge"] == "top" and record["settings"]["object"] == "an aircraft wing"
    assert Path(record["sources"][0]["path"]) == photos[0].resolve()
    assert ("06_commons.jpg", 2148) in looks                           # the copy was looked at again
    cache = json.loads((tmp_path / beat_images.TEXT_SCREEN_FILE).read_text(encoding="utf-8"))
    entry = next(v for v in cache.values() if v.get("object"))
    assert entry["object_edge"] == "top" and entry["screen"] == beat_images.TEXT_SCREEN_VERSION == 3
    looks.clear()
    assert beat_images.without_burned_in_text(photos, "Nazca Lines", minimum=1) == kept and looks == []   # cached


def test_an_intrusion_larger_than_the_band_sets_the_photo_aside(screen, monkeypatch, tmp_path):
    photos = [_with_wing(tmp_path / "06_commons.jpg", band=0.3), _jpg(tmp_path / "08_commons.jpg", size=(1600, 1200))]
    _looks(monkeypatch, [0, 0, 300, 1000])
    assert beat_images.without_burned_in_text(photos, "Nazca Lines", minimum=1) == [photos[1]]
    assert not beat_images.TEXT_CROP_DIR.exists() or not any(beat_images.TEXT_CROP_DIR.rglob("*.jpg"))
    # Never fewer than the minimum: then it stays, as a photo with text does.
    assert beat_images.without_burned_in_text(photos, "Nazca Lines", minimum=2) == photos


def test_a_found_picture_with_a_wing_is_kept_cropped(screen, monkeypatch, tmp_path):
    p = _with_wing(tmp_path / "beat_images" / "beat05_1.jpg")
    _looks(monkeypatch, [0, 0, 90, 1000])
    shows, why, use = beat_images.screened_picture(p, "Nazca lines aerial")
    assert shows and use != p and use.name == p.name and why == "aerial view of the geoglyphs"
    _looks(monkeypatch, [0, 0, 400, 1000])
    shows, why, use = beat_images.screened_picture(p, "Nazca lines aerial")
    assert not shows and use == p and why == beat_images.OBJECT_PREFIX + "an aircraft wing"


def test_older_verdicts_and_answers_without_the_new_fields_still_read(screen, monkeypatch, tmp_path):
    from src.credits import sha256_file
    v = beat_images.PictureVerdict(shows_subject=True, reason="ok")          # an answer that leaves them out
    assert v.edge_object == "" and v.object_edge == "none" and v.object_box == []
    fields = beat_images._text_fields(beat_images.PictureVerdict(shows_subject=True, reason="r", edge_object=" a wing ",
                                                                 object_edge="Top ", object_box=[90, 0, 10, 1000]))
    assert fields["edge_object"] == "a wing" and fields["object_edge"] == "top" and fields["object_box"] == []
    p = _jpg(tmp_path / "00_commons.jpg", size=(1600, 1200))
    (tmp_path / beat_images.TEXT_SCREEN_FILE).write_text(json.dumps(
        {sha256_file(p): {"text": False, "reason": "screen 2", "edge": "none", "box": [], "screen": 2}}))
    monkeypatch.setattr(beat_images, "picture_look", lambda path, q, size=512: {
        "checked": False, "shows": False, "burned_in_text": False, "reason": beat_images.UNAVAILABLE})
    assert beat_images.without_burned_in_text([p], "s", minimum=1) == [p]    # the screen-2 verdict stands


def test_the_screen_asks_about_edge_intrusions():
    text = beat_images.PICTURE_SYSTEM.lower()
    for word in ("wing", "window frame", "finger", "lens hood", "edge_object", "object_edge", "object_box"):
        assert word in text
    assert "vignette" not in text            # common in archive photos, and a corner is not one edge band


@pytest.mark.parametrize("edge, box", [("none", []), ("top", []), ("top", [90, 0, 10, 1000])])
def test_an_intrusion_that_cannot_be_placed_in_a_band_keeps_the_photo(screen, monkeypatch, tmp_path, edge, box):
    photos = [_with_wing(tmp_path / "06_commons.jpg"), _jpg(tmp_path / "08_commons.jpg", size=(1600, 1200))]

    def look(path, query, size=512):
        return {"checked": True, "shows": True, "burned_in_text": False, "reason": "r",
                "edge_object": "a dark corner", "object_edge": edge, "object_box": box}
    monkeypatch.setattr(beat_images, "picture_look", look)
    assert beat_images.without_burned_in_text(photos, "s", minimum=1) == photos


def test_a_crop_that_cannot_be_looked_at_again_keeps_the_original(screen, monkeypatch, tmp_path):
    photos = [_with_wing(tmp_path / "06_commons.jpg"), _jpg(tmp_path / "08_commons.jpg", size=(1600, 1200))]

    def look(path, query, size=512):
        with Image.open(path) as im:
            if im.height != 2400:
                return {"checked": False, "shows": False, "burned_in_text": False, "reason": beat_images.UNAVAILABLE}
        return {"checked": True, "shows": True, "burned_in_text": False, "reason": "r",
                "edge_object": "an aircraft wing", "object_edge": "top", "object_box": [0, 0, 90, 1000]}
    monkeypatch.setattr(beat_images, "picture_look", look)
    assert beat_images.without_burned_in_text(photos, "s", minimum=1) == photos


def test_a_screen_that_cannot_run_is_not_asked_again_for_every_photo(screen, monkeypatch, tmp_path):
    photos = [_jpg(tmp_path / f"{i:02d}_commons.jpg", (i * 30, 5, 5), size=(1600, 1200)) for i in range(4)]
    looks = []
    monkeypatch.setattr(beat_images, "picture_look", lambda path, q, size=512: looks.append(Path(path).name) or {
        "checked": False, "shows": False, "burned_in_text": False, "reason": beat_images.UNAVAILABLE})
    assert beat_images.without_burned_in_text(photos, "s", minimum=1) == photos
    assert looks == ["00_commons.jpg"]       # one look (with its own retry), not one per photo


def test_the_render_stage_asks_the_line_check_within_a_run_budget(db, monkeypatch, tmp_path):
    """main._hold_short_pool: a neighbour's picture is judged against the repeat's line through the fit check
    (cached verdicts free), at most HELD_CHECK_CALLS new looks a run and none after a look fails; the fit report
    follows the new numbering, and the fit findings are filed for the lines still unshown after the hold."""
    import main
    from main import PipelineCoordinator
    from src import improve
    a, b, c = (_jpg(tmp_path / f"{n}.jpg", (i * 40, 0, 0)) for i, n in enumerate("abc"))
    monkeypatch.setattr(config, "PICTURE_VISION_CHECK", True)
    monkeypatch.setattr(main.beat_images, "load_fit_cache", lambda: {})
    saved = []
    monkeypatch.setattr(main.beat_images, "save_fit_cache", lambda cache: saved.append(dict(cache)))
    asked = []

    def line_fit(path, line, subject, cache, digest=None):
        asked.append((Path(path).name, line))
        assert digest                                     # hashed once, by the caller
        return {"fits": Path(path).name == "c.jpg", "reason": "r", "cached": False}
    monkeypatch.setattr(main.beat_images, "line_fit", line_fit)
    from src import credits as credits_module
    hashed, real_hash = [], credits_module.sha256_file
    monkeypatch.setattr(credits_module, "sha256_file", lambda p: hashed.append(Path(p).name) or real_hash(p))
    coord = PipelineCoordinator(db)
    coord._held_looks = None
    improve._buffer.clear()
    fit = {"fits": [2, 3, 5], "unshown": [{"beat": 4, "fallback": "kept", "line": "Line 4.", "picture": "a.jpg",
                                           "reason": "r"}]}
    beats, scenes, durations, fit2, held, where = coord._hold_short_pool(
        "r1", {"id": "t", "title": "T"}, _beats(5, 3.0), [a, b, c, a, b], [3.0] * 5, fit, 3)
    assert scenes == [a, b, c] and where == [0, 1, 2, 2, 2] and fit2["fits"] == [2, 3] and saved
    assert ("c.jpg", "Line 4.") in asked and ("c.jpg", "Line 5.") in asked
    assert hashed.count("c.jpg") == 1                    # one hash per photo, not per photo and line
    assert fit2["unshown"] == [] and not [i for i in improve._buffer if i["source"] == "picture_fit"]  # line 4 now shown
    left = coord._held_looks
    assert 0 <= left < PipelineCoordinator.HELD_CHECK_CALLS
    # The budget is the run's: a second hold in the same run spends what is left, then asks nothing.
    coord._held_looks = 0
    asked.clear()
    _, scenes, _, _, held, _ = coord._hold_short_pool("r1", {"id": "t"}, _beats(5, 3.0), [a, b, c, a, b], [3.0] * 5,
                                                      fit, 3)
    assert asked == [] and scenes == [a, b, c, a, b] and len(held["repeats"]) == 2
    assert [i["klass"] for i in improve._buffer if i["source"] == "picture_fit"] == ["video.picture_relevance"]
    # A look that cannot run ends the looks for the run (each further one would wait out its retry).
    coord._held_looks = None
    monkeypatch.setattr(main.beat_images, "line_fit", lambda *a, **k: asked.append(1))
    coord._hold_short_pool("r1", {"id": "t"}, _beats(5, 3.0), [a, b, c, a, b], [3.0] * 5, fit, 3)
    assert asked == [1] and coord._held_looks == 0
    # The fit check found the model down: no look at all.
    coord._held_looks, asked[:] = None, []
    coord._hold_short_pool("r1", {"id": "t"}, _beats(5, 3.0), [a, b, c, a, b], [3.0] * 5,
                           dict(fit, stopped="the picture check could not run"), 3)
    assert asked == []


def test_a_hold_that_fails_leaves_the_plan_as_it_was(db, monkeypatch, tmp_path):
    import main
    from main import PipelineCoordinator
    a, b = (_jpg(tmp_path / f"{n}.jpg", (i * 90, 0, 0)) for i, n in enumerate("ab"))
    monkeypatch.setattr(sp, "hold_for_short_pool", lambda *a, **k: 1 / 0)
    beats = _beats(3)
    out = PipelineCoordinator(db)._hold_short_pool("r1", {"id": "t"}, beats, [a, b, a], [3.2] * 3, {}, 2)
    assert out[0] == beats and out[1] == [a, b, a] and out[5] == [0, 1, 2] and out[4]["merged"] == []
