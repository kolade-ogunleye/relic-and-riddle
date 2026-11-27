"""Freeze cycle 6, CAPTIONS stream (panel 7 classes C1 to C4), at the level production calls: build_subtitles (the
caption file compose_video burns), the still badges (cover_design._provenance, youtube_thumbnail._provenance).

Real caption words: tests/fixtures/text_c5 (Amber 9:16, the panel's run) and the panel catalogue's committed runs
(docs/reviews/freeze-panel-2026-11-04/fixtures/runs: the Richat long run behind the panel's 16:9 caption case). The
same measures docs/reviews/freeze-system-2026-11-15/corpus/captions_c6.py runs over every dev run; the last test runs
that corpus judge over the committed runs.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest
from PIL import Image

from src import brand
from src import video_composer as vc

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "text_c5"
RUNS = ROOT / "docs" / "reviews" / "freeze-panel-2026-11-04" / "fixtures" / "runs"
CORPUS = ROOT / "docs" / "reviews" / "freeze-system-2026-11-15" / "corpus" / "captions_c6.py"


def _corpus():
    spec = importlib.util.spec_from_file_location("captions_c6", CORPUS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


C6 = _corpus()


def _amber() -> tuple[list[dict], list[str]]:
    d = json.loads((FIX / "amber_caption_words.json").read_text(encoding="utf-8"))
    return [dict(w) for w in d["words"]], d["emphasis_words"]


def _richat_long() -> list[dict]:
    return json.loads((RUNS / "run_richat_structure_sahara_eye_1792740961_long" / "words.json")
                      .read_text(encoding="utf-8"))


def _build(tmp_path: Path, words: list[dict], emphasis, wide: bool, **kw) -> tuple[dict, dict]:
    """(parsed bursts as the corpus reads them, the composer's record) of the caption file production writes."""
    ass = tmp_path / ("w.ass" if wide else "t.ass")
    comp = vc.VideoComposer()
    extra = {"width": 1920, "height": 1080} if wide else {"first_frame_whole": vc.CAPTION_FIRST_FRAME_WHOLE_S}
    comp.build_subtitles([dict(w) for w in words], ass, emphasis=emphasis, **extra, **kw)
    text = ass.read_text(encoding="utf-8")
    cap = C6._parse(text, words, vc._col_emphasis().upper())
    cap["ass"] = text
    return cap, comp.last_reading_rate


def _cut_faults(cap: dict) -> list[str]:
    out = []
    bursts = cap["bursts"]
    for b, n in zip(bursts, bursts[1:]):
        seq = b["raw"] + n["raw"]
        if vc.wide_cut_fault(seq, len(b["raw"]) - 1) is not None:
            out.append(" ".join(b["raw"]) + " | " + " ".join(n["raw"]))
    return out


# ------------------------------------------------------------------------------------------------ C2 (16:9)

def test_wide_bursts_end_on_phrase_bounds_where_a_legal_cut_exists(tmp_path, monkeypatch):
    """Panel 7 MO-C2 (Richat 16:9): 'when a subsurface igneous' | 'intrusion deformed the overlying' | 'sedimentary
    rock'; 'this circular geological' | 'formation'. The 16:9 path re-cuts each sentence by phrase
    (_wide_phrase_pass): far fewer faulted cuts, no new short or one-word burst, every word in order, recorded."""
    words = _richat_long()
    cap, rate = _build(tmp_path, words, None, True)
    joined = [" ".join(b["raw"]) for b in cap["bursts"]]
    assert cap["words_matched"] == len(words)
    for bad in ("when a subsurface igneous", "this circular geological", "intrusion deformed the overlying"):
        assert bad not in joined, bad
    assert "when a subsurface igneous intrusion" in joined
    monkeypatch.setattr(vc, "CAPTION_WIDE_PHRASE_PASS", False)
    old, _ = _build(tmp_path, words, None, True)
    assert len(_cut_faults(cap)) * 2 < len(_cut_faults(old))
    for key in ("under", "one"):
        count = (lambda c: sum((b["hide"] - b["show"] < 0.8) if key == "under" else len(b["raw"]) == 1
                               for b in c["bursts"]))
        assert count(cap) <= count(old)
    assert [e for e in rate["recut"] if e.get("wide_recut")] and all(
        e["rule"] == vc.WIDE_RULE for e in rate["recut"] if e.get("wide_recut"))


def test_a_wide_cut_fault_names_the_function_auxiliary_and_phrase_unit_ends():
    raw = "when a subsurface igneous intrusion deformed the overlying sedimentary rock".split()
    assert vc.wide_cut_fault(raw, 3) == "phrase_unit"           # IGNEOUS | INTRUSION
    assert vc.wide_cut_fault(raw, 7) == "phrase_unit"           # THE OVERLYING | SEDIMENTARY
    assert vc.wide_cut_fault(raw, 0) == "function_end"          # WHEN |
    assert vc.wide_cut_fault("it was carved".split(), 1) == "auxiliary_end"
    assert vc.wide_cut_fault(raw, 4) is None                    # INTRUSION | DEFORMED


# ------------------------------------------------------------------------------------------------ C1 (9:16)

def test_a_moved_word_stays_on_screen_and_the_remedies_keep_their_order(tmp_path, monkeypatch):
    """Round 2 rulings (rule 3): a stranded preposition moves to the previous burst only where it stays on screen
    STRAND_MOVE_MIN_S or more as its last word: Amber's OF (0.14 s) is not moved (base drew it at 0.14 s with the
    move), and no move anywhere shows its word for less; no re-cut ends a burst on a function word or auxiliary."""
    for words, emph in (_amber(), (_richat_long(), None)):
        cap, rate = _build(tmp_path, words, emph, False)
        rows = [e for e in rate["recut"] if e.get("strand_remedy")]
        assert all(e["moved_on_screen_s"] >= vc.STRAND_MOVE_MIN_S for e in rows if e["strand_remedy"] == "move")
        for e in rows:
            if e["strand_remedy"] == "recut":
                for part in e["into"].split(" | ")[:-1]:
                    last = part.split()[-1]
                    # round 3 ruling: a re-cut may end on a preposition (rule 3's conditions), never on an article,
                    # conjunction or auxiliary
                    assert vc._bare(last) not in vc._line_policy.brand_system.LINE_END_WEAK
                    assert not vc.never_end_end(last) or vc._is_preposition(last), e
        assert not any(" ".join(b["raw"]) == "German soldiers of" for b in cap["bursts"])
    assert vc.strand_remedies is not None


def test_a_preposition_is_the_only_function_word_a_recut_may_end_on():
    """Round 3 ruling: the re-cut and the move share rule 3's preposition test."""
    assert vc._is_preposition("across") and vc._is_preposition("on") and vc._is_preposition("into")
    for w in ("the", "an", "and", "when", "was", "that", "its"):
        assert not vc._is_preposition(w), w


def test_a_multi_word_hedge_line_above_its_figure_with_its_unit_is_legal():
    """Round 2 ruling (rule 2): MORE THAN / 590 SQUARE FEET and BETWEEN ROUGHLY / 41.75 METRES are legal forms; a
    single word alone on a line is not (WITH / OVER 13,000 POUNDS, FROM NEARLY / 1,090 TONNES keeps FROM)."""
    assert vc._hedge_line_legal("more than 590 square feet".split(), 2)
    assert vc._hedge_line_legal("between roughly 41.75 metres".split(), 2)
    assert vc.hedge_line_form(tuple("more than 590 square feet".split()), vc.FONT_SIZE) == 2
    # follow-up ruling (2026-11-27): the article or preposition leading into the hedge may open its line
    assert vc._hedge_line_legal("an estimated 720 tons".split(), 2)
    assert vc._hedge_line_legal("from nearly 1,090 tonnes".split(), 2)
    assert vc.hedge_line_form(tuple("from nearly 1,090 tonnes".split()), vc.FONT_SIZE) == 2
    assert not vc._hedge_line_legal("with 13,000 pounds".split(), 1)          # one word alone on a line stays illegal
    assert not vc._hedge_line_legal("nearly 1,090 tonnes".split(), 1)
    assert not vc._hedge_line_legal("of approximately 7,000 individuals".split(), 2)   # no unit: not a figure+unit
    assert not vc._hedge_line_legal("and nearly 1,090 tonnes".split(), 2)     # a conjunction does not lead in


def test_a_burst_one_rung_under_is_recorded_and_at_most_two_a_clip(tmp_path):
    """Round 2 ruling (rule 4): a burst with no other remedy sets alone one rung under the clip's size, recorded
    forced_rung (and under), at most CAPTION_FORCED_RUNG_MAX a clip; its drawn size is that rung."""
    for words, emph in (_amber(), (_richat_long(), None)):
        cap, rate = _build(tmp_path, words, emph, False)
        clip = rate["clip_size"]
        rows = clip.get("forced_rung") or []
        assert len(rows) <= vc.CAPTION_FORCED_RUNG_MAX
        for r in rows:
            assert r["px"] == vc._rung_below(clip["px"])
            assert any(e["text"] == r["text"] for e in clip["under"])


def test_every_forced_break_left_has_no_remedy(tmp_path):
    """The record 'no legal form' is true: the corpus enumeration (captions_c6._forced_proof) finds no remedy 1 or 2
    for any burst the composer still draws on a forced break, and no C1 line is drawn unrecorded."""
    for words, emph in (_amber(), (_richat_long(), None)):
        cap, rate = _build(tmp_path, words, emph, False)
        proof = C6._forced_proof(vc, words, emph, cap, rate)
        assert proof, "the fixtures hold forced breaks"
        for p in proof:
            if p["faults"]:
                assert p["recorded"], p
            if p["recorded"]:
                assert not p["remedies"], p


def test_a_line_of_one_closed_class_word_is_a_lone_function_line():
    """RECONSTRUCTION / INSIDE (dev Amber 1792781224) was drawn unrecorded: INSIDE is in the one closed-class
    vocabulary (NEVER_END), not in the captions' older FUNCTION_WORDS. Round 2: judged on the clip's final forms
    (final_form), so the phrasing before them and the clip's size are as at base."""
    assert vc.lone_closed_line(["reconstruction", "inside"], 1)
    assert vc.lone_closed_line(["of", "Acheulean", "artifacts", "alongside"], 3)
    assert not vc.lone_closed_line(["reconstruction", "inside,"], 1)
    assert not vc.lone_closed_line(["under", "expert", "supervision."], 2)


# ------------------------------------------------------------------------------------------------ C3 (gold)

def _gold(cap: dict) -> list[tuple[str, list[str]]]:
    return [(" ".join(b["raw"]), b["gold_words"]) for b in cap["bursts"] if b["gold_words"]]


@pytest.mark.parametrize("wide", [False, True])
def test_a_hedge_is_never_gold_and_a_figure_takes_its_unit(tmp_path, wide):
    """Panel 7 TY3 (ruling R8-8), Richat 16:9 burst 14: ALMOST 50 gold, KILOMETRES bone. Now 50 KILOMETRES gold,
    ALMOST bone; 40 KILOMETRES gold."""
    cap, rate = _build(tmp_path, _richat_long(), None, wide)
    gold = [g for _, gs in _gold(cap) for g in gs]
    assert "almost" not in [vc._norm(g) for g in gold]
    assert sum(vc._norm(g) == "kilometres" for g in gold) == 2
    assert rate["accent"]["rule"] == vc.CAPTION_ACCENT_RULE


def test_one_accent_rule_per_clip_every_mention(tmp_path):
    """Panel 7 TY (Amber): KÖNIGSBERG CASTLE gold at one mention, bone at the next. Every mention of a key term is
    gold (CAPTION_ACCENT_RULE), each recorded."""
    words, emph = _amber()
    cap, rate = _build(tmp_path, words, emph, False)
    castle = [g for t, g in _gold(cap) if "Castle" in t]
    assert len(castle) >= 2 and all("Castle" in g for g in castle)
    assert not rate["accent"]["bone"]
    assert sum("Castle" in e["text"] for e in rate["accent"]["gold"]) == len(castle)


# ------------------------------------------------------------------------------------------------ C4 (badge)

def _badge_events(text: str) -> list[tuple[str, str]]:
    return [(ln.split(",", 9)[3], ln.split(",", 9)[9]) for ln in text.splitlines()
            if ln.startswith("Dialogue:") and ",Recon" in ln]


def _poster(tmp_path: Path, corners: list[str]) -> Path:
    clip = tmp_path / "graphic_numbers.mp4"
    im = Image.new("RGB", (1080, 1920), brand.INK)
    for c in corners:   # a gold figure standing under the corner's plate (the panel's '36')
        x0, y0, x1, y1 = vc.recon_badge_geometry(1080, 1920, c)["box"]
        im.paste((242, 182, 48), (x0 + 20, y1 - 12, x0 + 220, y1 + 120))
    im.save(clip.with_suffix(".jpg"))
    return clip


def test_the_badge_takes_its_alternative_corner_over_a_graphic(tmp_path):
    """Panel 7 C4 (captions__amber_room_heist__badge__encoded: the plate overprinted the top of the '36'). Over a
    graphic whose ink stands in the badge's corner, the badge stands in its documented alternative (top right, two
    lines, no separator), recorded; never over the ink."""
    words, emph = _amber()
    clip = _poster(tmp_path, ["top_left"])
    cap, rate = _build(tmp_path, words, emph, False, reconstruction_intervals=[(0.0, 6.0)],
                       graphic_clips=[(0.0, 6.0, clip)])
    ev = _badge_events(cap["ass"])
    alt = vc.recon_badge_geometry(1080, 1920, "top_right")
    assert any(f"\\pos({alt['box'][0]},{alt['box'][1]})" in body for s, body in ev if s == "ReconPlate")
    texts = [re.sub(r"\{[^}]*\}", "", body) for s, body in ev if s == "ReconBadge"]
    assert texts == ["AI ILLUSTRATION", "NOT A PHOTO"]
    assert rate["badge"] == [{"start": 0.0, "end": 6.0, "corner": "top_right", "graphic": "graphic_numbers.mp4",
                              "why": rate["badge"][0]["why"]}] and rate["badge"][0]["why"]


def test_the_badge_yields_where_neither_corner_is_free_and_stays_on_a_photo(tmp_path):
    words, emph = _amber()
    clip = _poster(tmp_path, ["top_left", "top_right"])
    cap, rate = _build(tmp_path, words, emph, False, reconstruction_intervals=[(0.0, 8.0)],
                       graphic_clips=[(2.0, 6.0, clip)])
    rows = {(r["start"], r["end"]): r["corner"] for r in rate["badge"]}
    assert rows == {(0.0, 2.0): "top_left", (2.0, 6.0): None, (6.0, 8.0): "top_left"}
    plates = [body for s, body in _badge_events(cap["ass"]) if s == "ReconPlate"]
    assert len(plates) == 2
    # clear_badge_intervals (compose_video's pre-pass) agrees: it keeps the badge where a corner is free
    kept, yielded = vc.clear_badge_intervals([(0.0, 8.0)], [tmp_path / "gen_01.png", clip, tmp_path / "x.jpg"],
                                             [2.0, 4.0, 2.0], 1080, 1920)
    assert kept == [(0.0, 2.0), (6.0, 8.0)] and yielded
    one = _poster(tmp_path, ["top_left"])
    assert vc.clear_badge_intervals([(0.0, 8.0)], [tmp_path / "gen_01.png", one, tmp_path / "x.jpg"],
                                    [2.0, 4.0, 2.0], 1080, 1920) == ([(0.0, 8.0)], [])


def test_the_badge_lines_drop_the_separator_at_the_break():
    assert vc.badge_lines() == ["AI ILLUSTRATION", "NOT A PHOTO"]
    assert " ".join(vc.badge_lines()).replace(" ", "") == vc.RECON_BADGE_LABEL.replace(" · ", "").replace(" ", "")


def test_a_corner_is_judged_on_every_frame_the_badge_covers(tmp_path):
    """Review B1: the poster shows the alternative corner clear, but the graphic's frames draw ink there: the corner
    is not free (the badge yields), judged on the decoded frames, not the still."""
    import subprocess
    words, emph = _amber()
    clean = Image.new("RGB", (1080, 1920), brand.INK)
    inked = clean.copy()
    for c in vc.RECON_BADGE_CORNERS:
        x0, y0, x1, y1 = vc.recon_badge_geometry(1080, 1920, c)["box"]
        inked.paste((242, 182, 48), (x0 + 20, y1 - 12, x0 + 220, y1 + 120))
    clean.save(tmp_path / "a.png")
    inked.save(tmp_path / "b.png")
    clip = tmp_path / "graphic_map.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-t", "1", "-i", str(tmp_path / "a.png"), "-loop", "1",
                    "-t", "2", "-i", str(tmp_path / "b.png"), "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]",
                    "-map", "[v]", "-r", "30", "-pix_fmt", "yuv420p", str(clip)], check=True)
    clean.save(clip.with_suffix(".jpg"))   # the poster: clear in both corners
    # the whole interval over the graphic: by the poster both corners were free; the frames say neither is
    cap, rate = _build(tmp_path, words, emph, False, reconstruction_intervals=[(0.0, 3.0)],
                       graphic_clips=[(0.0, 3.0, clip)])
    assert [r["corner"] for r in rate["badge"]] == [None]
    assert not _badge_events(cap["ass"])
    # over the clean first second only, the badge keeps its place
    plan = vc.badge_plan([(0.0, 1.0)], [(0.0, 3.0, clip)], 1080, 1920)
    assert [r["corner"] for r in plan] == ["top_left"]


def test_the_badge_never_jumps_corner_for_a_short_stretch(tmp_path):
    """Review B2: a 0.2 s crossfade over a graphic whose ink takes the badge's place does not move the badge to its
    alternative corner for 6 frames: it yields there, recorded; the illustration's own beat keeps its place."""
    clip = _poster(tmp_path, ["top_left"])
    plan = vc.badge_plan([(0.0, 4.2)], [(4.0, 9.0, clip)], 1080, 1920)
    assert [(r["start"], r["end"], r["corner"]) for r in plan] == [(0.0, 4.0, "top_left"), (4.0, 4.2, None)]
    assert any("does not change corner" in w for w in plan[1]["why"])


def test_a_range_joiner_is_never_gold(tmp_path):
    """Review C3: TO 1,168 TONNES drew TO gold; a range's joiner is bone, its two figures with their units gold, and
    on 16:9 the range stays one gold span."""
    raw = "Its weight ranged from nearly 1,090 tonnes to 1,168 tonnes before work stopped.".split()
    words = [{"word": w, "start": 0.3 + 0.35 * i, "dur": 0.3} for i, w in enumerate(raw)]
    for wide in (False, True):
        cap, _ = _build(tmp_path, words, ["1,090 tonnes"], wide)
        gold = [vc._norm(g) for _, gs in _gold(cap) for g in gs]
        assert "to" not in gold and "nearly" not in gold
        assert gold.count("tonnes") == 2 and "1090" in gold and "1168" in gold, gold


def test_the_reel_cover_badge_is_the_caption_badge_treatment():
    """Panel 7 BC / TY: three treatments and 'AI ILLUSTRATION · /'. The cover's two-line label carries no separator
    at its line end, in BONE on the badge plate tokens."""
    from src import cover_design, still_layout
    lay = still_layout.Layout("reel_cover", (1080, 1920), (0, 0, 1080, 1920))
    lay.meta["composition"] = {"image_box": [300, 560, 760, 1100]}   # a narrow window: the label takes two lines
    im = Image.new("RGB", (1080, 1920), (240, 236, 228))
    out = cover_design._provenance(im, lay)
    rec = lay.meta["provenance"]
    assert rec["lines"] == ["AI ILLUSTRATION", "NOT A PHOTO"]
    x0, y0, x1, y1 = rec["plate"]
    plate = out.convert("RGB").getpixel((x0 + 2 + vc.BADGE_RADIUS, y1 - 3))
    want = tuple(round(vc.BADGE_PLATE_OPACITY * i + (1 - vc.BADGE_PLATE_OPACITY) * g)
                 for i, g in zip(brand.INK, (240, 236, 228)))
    assert max(abs(a - b) for a, b in zip(plate, want)) <= 2
    assert out.convert("RGB").getpixel((x0, y0)) == (240, 236, 228)   # a rounded corner, as the caption plate


def test_the_caption_badge_plate_has_the_stills_height():
    """Follow-up ruling (2026-11-27): one badge treatment: the caption badge plate is the cap height plus the padding
    above and below, as the Reel cover and the thumbnail set theirs (it was 49 px against 41 px)."""
    from src.typography import cap_height, get_font
    f = get_font(brand.BADGE_FONT, vc.RECON_SIZE_PORTRAIT, weight=vc.BADGE_WEIGHT)
    assert abs(vc.recon_badge_geometry(1080, 1920)["plate"][1] - (cap_height(f) + 2 * vc.BADGE_PAD[1])) <= 1


def test_the_thumbnail_badge_is_the_caption_badge_treatment():
    from src import youtube_thumbnail as yt
    im = Image.new("RGB", (yt.W, yt.H), (240, 236, 228))
    out, plate = yt._provenance(im)
    assert tuple(plate.fill[:3]) == tuple(brand.BONE[:3]) if hasattr(plate, "fill") else True
    px = out.convert("RGB").getpixel((yt.LEFT + vc.BADGE_RADIUS + 2, yt.H - 64 - 2))
    want = tuple(round(vc.BADGE_PLATE_OPACITY * i + (1 - vc.BADGE_PLATE_OPACITY) * g)
                 for i, g in zip(brand.INK, (240, 236, 228)))
    assert max(abs(a - b) for a, b in zip(px, want)) <= 2


# ------------------------------------------------------------------------------------------------ corpus judge

def test_the_corpus_judge_finds_no_unrecorded_violation_on_the_committed_runs(tmp_path):
    """captions_c6.py's collector and judge over the panel catalogue's committed runs (both formats)."""
    out = tmp_path / "c.json"
    C6.collect(str(ROOT), str(RUNS.parent), str(out))
    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data["runs"]) >= 6
    table, tot = C6.judge(data)
    bad = {k: v for k, v in tot.items() if k.endswith("UNRECORDED") and v}
    assert not bad, (bad, [r for r in table if r[-1] == "FAIL"][:10])
