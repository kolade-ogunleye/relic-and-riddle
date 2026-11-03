"""Caption panel round 3 (2026-11-02): the spoken word eases in, the 9:16 plate keeps its width through a run and
fades only at pauses, a figure's hedge is gold with it, no caption gold while a graphic is up, a reading-rate guard,
a designed contrast record, an optically centred plate measured as libass draws the line, 4-word bursts, and the
16:9 burn-in shown whole and still, its plate 130 px above the frame's bottom."""
import json
import re
from pathlib import Path

import pytest

from src import brand, safe_zones
from src.config import (
    CAPTION_PLATE_FADE_IN_MS,
    CAPTION_PLATE_FADE_OUT_MS,
    CAPTION_WORD_EASE_MS,
    MAX_BURST_WORDS,
)
from src.video_composer import (
    CAPTION_HIDDEN,
    COL_EMPHASIS,
    COL_FUTURE,
    REEL_CAP_H,
    REEL_PAD_Y,
    REEL_PLATE_H,
    VideoComposer,
    _caption_text_width,
    _font_vmetrics,
    burst_timings,
    caption_contrast_record,
    caption_graphic_intervals,
    emphasis_indices,
    format_word_caption,
    merge_short_bursts,
    quantise_plate_widths,
    reading_need,
)
from src.video_qa import caption_pills, graphic_contrast_problems

NARR = ("Beneath Cappadocia lies a city carved by hand. Derinkuyu reaches roughly 280 feet deep, about eighteen "
        "levels of tunnels and halls. Up to 20,000 people could shelter here, with livestock and food. Then it was "
        "forgotten.")


def _timed(text, step=0.36, dur=0.32, sentence_pause=0.0):
    words, t = [], 0.0
    for w in text.split():
        words.append({"word": w, "start": round(t, 3), "dur": dur})
        t += step + (sentence_pause if w.endswith((".", "?", "!")) else 0.0)
    return words


def _events(ass_text, style):
    out = []
    for line in ass_text.splitlines():
        if line.startswith("Dialogue:") and f",{style}," in line:
            parts = line.split(",", 9)
            h, m, s = parts[1].split(":")
            h2, m2, s2 = parts[2].split(":")
            out.append((int(h) * 3600 + int(m) * 60 + float(s), int(h2) * 3600 + int(m2) * 60 + float(s2), parts[9]))
    return out


def _plain(tagged):
    return re.sub(r"\{[^}]*\}", "", tagged)


def _build(tmp_path, words, **kw):
    path = VideoComposer().build_subtitles(words, tmp_path / "c.ass", **kw)
    return path, path.read_text(encoding="utf-8")


# ---------------------------------------------------------------- the spoken word eases in
def test_only_the_spoken_word_eases_in_and_without_a_size_pop():
    ease = f"{{\\alpha&HFF&\\t(0,{CAPTION_WORD_EASE_MS},\\alpha&H00&)}}"
    assert format_word_caption("Roman", "&H00FFFFFF&", False, True, False, ease_ms=70).startswith(ease)
    assert "\\t(" not in format_word_caption("Roman", "&H00FFFFFF&", False, False, False, ease_ms=70)
    assert "\\t(" not in format_word_caption("Roman", "&H00FFFFFF&", False, True, False)   # default unchanged


def test_each_word_event_eases_exactly_its_new_word(tmp_path):
    _, ass = _build(tmp_path, _timed("Roman concrete heals itself inside ocean water."))
    for _, _, text in _events(ass, "CapText"):
        visible, _, _ = text.partition(CAPTION_HIDDEN)
        assert visible.count("\\t(0,") == 1                       # one easing word: the one spoken now
        last_visible_word = re.split(r" (?=\{)", visible)[-1]
        assert "\\t(0," in last_visible_word                       # and it is the newest visible word
        assert "\\fscx1" not in text.replace("\\fscx100", "")      # no pop


# ---------------------------------------------------------------- plate continuity
def test_quantised_plate_widths_never_shrink_and_keep_to_three_sizes():
    widths = [500, 520, 610, 300, 330, 640, 410]
    q = quantise_plate_widths(widths, steps=3, slack=96)
    assert all(a >= b for a, b in zip(q, widths)) and max(q) == max(widths)
    assert len(set(q)) <= 3
    assert quantise_plate_widths([]) == [] and quantise_plate_widths([420]) == [420]
    assert quantise_plate_widths([600, 560, 590], steps=3, slack=96) == [600, 600, 600]   # no strobe


def test_the_plate_fades_only_where_it_appears_after_or_leaves_before_a_pause(tmp_path):
    words = _timed(NARR, sentence_pause=1.2)
    _, ass = _build(tmp_path, words)
    boxes = _events(ass, "CapBox")
    for i, (start, end, text) in enumerate(boxes):
        joined_prev = i > 0 and abs(boxes[i - 1][1] - start) < 0.011
        joined_next = i + 1 < len(boxes) and abs(boxes[i + 1][0] - end) < 0.011
        fad = re.search(r"\\fad\((\d+),(\d+)\)", text)
        fade_in, fade_out = (int(fad.group(1)), int(fad.group(2))) if fad else (0, 0)
        assert fade_in == (0 if joined_prev else CAPTION_PLATE_FADE_IN_MS)
        assert fade_out == (0 if joined_next else CAPTION_PLATE_FADE_OUT_MS)


def test_bursts_heard_back_to_back_in_a_sentence_share_plate_widths(tmp_path):
    words = _timed("It was hidden underground for thousands of years and then a farmer broke through a wall "
                   "while he was rebuilding his house.")
    path, _ = _build(tmp_path, words)
    pills = caption_pills(path)
    assert len(pills) >= 4 and len({p[2] - p[0] for p in pills}) <= 3


def test_reel_plates_stay_inside_the_reel_box_and_below_the_graphics_floor(tmp_path):
    path, _ = _build(tmp_path, _timed(NARR + " Pseudoarchaeoastronomical claims followed."))
    pills = caption_pills(path)
    box = safe_zones.safe_box("reel")
    assert pills and all(safe_zones.inside(p, box) for p in pills)
    assert all(p[1] >= safe_zones.caption_band_top() and p[1] > safe_zones.graphics_box()[3] for p in pills)
    assert all(p[3] - p[1] == REEL_PLATE_H for p in pills)       # one plate height per Reel
    assert all(1228 <= p[3] <= 1248 - 20 for p in pills)           # 20 px over the platforms' band (was 8)


def test_the_plate_is_sized_to_the_line_libass_draws(tmp_path):
    words = _timed("Beneath Cappadocia lies")
    path, _ = _build(tmp_path, words)
    (x0, _, x1, _), = caption_pills(path)
    from src.config import FONT_SIZE   # item 2 (2026-11-03): the caption size, 80 px (was a literal 72)
    drawn = _caption_text_width(["Beneath", "Cappadocia", "lies"], FONT_SIZE, drawn=True)
    assert drawn > _caption_text_width(["Beneath", "Cappadocia", "lies"], FONT_SIZE)   # the em libass draws is larger
    assert x1 - x0 == min(round(drawn + 56), 696)


def test_the_capitals_are_optically_centred_in_the_plate(tmp_path):
    path, ass = _build(tmp_path, _timed("Beneath Cappadocia lies"))
    (_, y0, _, y1), = caption_pills(path)
    (_, _, text), = _events(ass, "CapText")[-1:]
    cy = int(re.search(r"\\pos\(\d+,(\d+)\)", text).group(1))
    _, asc, desc = _font_vmetrics("Anton")
    from src.config import FONT_SIZE_ASS   # item 2 (2026-11-03): 142 for 80 px (was a literal 128, the 72 px size)
    baseline_y = cy - FONT_SIZE_ASS / 2 + asc * FONT_SIZE_ASS / (asc + desc)   # \an5 centres the line box on \pos
    cap_top = baseline_y - REEL_CAP_H
    assert abs((cap_top - y0) - (y1 - baseline_y)) <= 1.5        # equal room above and below the capitals
    assert abs((cap_top - y0) - REEL_PAD_Y) <= 1.5


def test_bursts_hold_at_most_four_words(tmp_path):
    _, ass = _build(tmp_path, _timed(NARR))
    finals = [_plain(t) for _, _, t in _events(ass, "CapText") if CAPTION_HIDDEN not in t]
    assert MAX_BURST_WORDS == 4 and all(len(f.split()) <= 4 for f in finals)


# ---------------------------------------------------------------- gold
def test_a_figure_takes_its_hedge_into_gold():
    words = [{"word": w} for w in ["Derinkuyu", "reaches", "roughly", "280", "feet", "deep", "and", "up", "to", "20,000", "people"]]
    assert emphasis_indices(words, ["280 feet"]) == {2, 3, 4}
    assert emphasis_indices(words, ["20,000 people"]) == {7, 8, 9, 10}
    assert emphasis_indices(words, ["Derinkuyu"]) == {0}                   # a name takes no neighbour
    assert emphasis_indices(words, None) == {2, 3, 7, 8, 9}                # figures without terms: hedges too
    plain = [{"word": w} for w in ["It", "is", "280", "feet", "deep"]]
    assert emphasis_indices(plain, ["280 feet"]) == {2, 3}


def test_no_caption_gold_while_a_graphic_is_on_screen(tmp_path):
    words = _timed("In 1963, a man found Derinkuyu, a city 280 feet deep.")
    _, with_gold = _build(tmp_path, words, emphasis=["Derinkuyu"])
    assert COL_EMPHASIS in with_gold
    _, during = _build(tmp_path, words, emphasis=["Derinkuyu"], graphic_intervals=[(0.0, 10.0)])
    assert COL_EMPHASIS not in during and _plain(during) == _plain(with_gold)
    _, wide = _build(tmp_path, words, emphasis=["Derinkuyu"], width=1920, height=1080, graphic_intervals=[(0.0, 10.0)])
    assert COL_EMPHASIS not in "".join(t for _, _, t in _events(wide, "CapText"))


def test_graphic_intervals_follow_the_scene_list():
    scenes = [Path("photo_01.jpg"), Path("graphic_scale.mp4"), Path("photo_02.jpg"), Path("graphic_map.mp4")]
    assert caption_graphic_intervals(scenes, [2.0, 3.0, 1.5, 2.5]) == [(2.0, 5.0), (6.5, 9.0)]


# ---------------------------------------------------------------- reading rate
def test_a_burst_too_quick_to_read_holds_into_the_following_pause():
    phrases = [[{"word": "Underground", "start": 0.0, "dur": 0.2}, {"word": "cities.", "start": 0.22, "dur": 0.2}],
               [{"word": "Then", "start": 3.0, "dur": 0.3}, {"word": "silence.", "start": 3.4, "dur": 0.4}]]
    (show, hide, _), (show2, _, _) = burst_timings(phrases)
    assert hide - show >= reading_need(phrases[0]) - 1e-9 and hide <= show2
    tight = [phrases[0], [{"word": "Then", "start": 0.9, "dur": 0.3}, {"word": "silence.", "start": 1.3, "dur": 0.4}]]
    (_, hide_t, _), (show_t, _, _) = burst_timings(tight)
    assert hide_t <= show_t + 1e-9                                         # never past the next burst


def test_a_burst_up_for_less_than_its_reading_time_joins_a_neighbour():
    def fits(p):
        return True

    fast = [[{"word": "Extraordinarily", "start": 0.0, "dur": 0.3}, {"word": "complicated", "start": 0.32, "dur": 0.3}],
            [{"word": "tunnels", "start": 0.66, "dur": 0.3}, {"word": "below.", "start": 1.0, "dur": 0.3}]]
    assert len(merge_short_bursts(fast, fits, 4, min_dur=0.6)) == 1
    assert len(merge_short_bursts(fast, fits, 4, min_dur=0.6, reading_rate=False)) == 2


# ---------------------------------------------------------------- contrast record
def test_the_designed_contrast_record_is_written_in_the_graphics_record_layout(tmp_path):
    # The layout graphic_contrast_problems reads; video_qa.text_contrast_audit does not read this file yet.
    _build(tmp_path, _timed("In 1963, a man found Derinkuyu."), emphasis=["Derinkuyu"])
    record = json.loads((tmp_path / "c.contrast.json").read_text(encoding="utf-8"))
    assert record["bone"] >= brand.TEXT_CONTRAST and record["gold"] >= brand.TEXT_CONTRAST
    assert any(e["name"].endswith("gold") for e in record["elements"])
    assert graphic_contrast_problems(tmp_path) == []


def test_a_plate_too_thin_for_the_gold_fails_the_record():
    record = caption_contrast_record([{"burst": 0, "text": "X", "gold": True}], plate="&HD0120E0B&")
    assert min(e["drawn"] for e in record["elements"]) < brand.TEXT_CONTRAST


# ---------------------------------------------------------------- 16:9
def test_widescreen_bursts_are_shown_whole_and_still(tmp_path):
    words = _timed(NARR, sentence_pause=0.5)
    _, wide = _build(tmp_path, words, emphasis=["Derinkuyu", "280 feet"], width=1920, height=1080)
    texts, boxes = _events(wide, "CapText"), _events(wide, "CapBox")
    assert len(texts) == len(boxes)                                        # one text event per burst
    for (s, e, t), (bs, be, _) in zip(texts, boxes):
        assert (s, e) == (bs, be)
        assert CAPTION_HIDDEN not in t and COL_FUTURE not in t and "\\t(" not in t and "\\fad" not in t
    gold = [w for _, _, t in texts for c, w in re.findall(r"\\c(&H[0-9A-F]+&)\}([^{ ]+)", t) if c == COL_EMPHASIS]
    # Round 4 (caption panel): one gold span a still 16:9 line, the figure with its hedge (single_gold_span); this
    # burst carried two spans, DERINKUYU and ROUGHLY 280 FEET, until then.
    assert gold == ["roughly", "280", "feet"]
    # the 9:16 reveal is unchanged: word by word with placeholders
    _, reel = _build(tmp_path, words)
    assert any(CAPTION_HIDDEN in t for _, _, t in _events(reel, "CapText"))


@pytest.mark.parametrize("height", [1080, 2160])
def test_widescreen_plate_clears_the_youtube_controls_and_stays_under_the_graphics(tmp_path, height):
    width = height * 16 // 9
    path, _ = _build(tmp_path, _timed(NARR), width=width, height=height)
    pills = caption_pills(path)
    s = height / 1080
    stamp = (1620 * s, 950 * s, 1920 * s, 1080 * s)                       # the YouTube timestamp corner
    assert pills and all(p[3] <= 950 * s + 1 and p[3] <= 0.88 * height for p in pills)
    assert all(p[1] >= 820 * s for p in pills)                            # under the 16:9 graphics floor
    assert all(not (p[0] < stamp[2] and p[2] > stamp[0] and p[1] < stamp[3] and p[3] > stamp[1]) for p in pills)


# ---------------------------------------------------------------- round 3 review fixes (2026-11-02)
def _visible_words(tagged):
    """[(ASS colour, plain word)] of the words shown in a 9:16 word event (before the transparent placeholders)."""
    shown = tagged.split(CAPTION_HIDDEN)[0]
    shown = re.sub(r"^\{\\pos\([^)]*\)\\q2\}", "", shown)
    out = []
    for token in shown.split(" "):
        colours = re.findall(r"\\c(&H[0-9A-F]+&)", token)
        if _plain(token).strip():
            out.append((colours[0], _plain(token).strip()))
    return out


def test_a_hedge_turns_gold_only_when_its_figure_is_revealed(tmp_path):
    _, ass = _build(tmp_path, _timed(NARR), emphasis=["Derinkuyu", "280 feet"])
    seen = 0
    for _, _, t in _events(ass, "CapText"):
        words = {w: c for c, w in _visible_words(t)}
        if "ROUGHLY" not in words:
            continue
        seen += 1
        assert (words["ROUGHLY"] == COL_EMPHASIS) == ("280" in words)    # bone alone, gold beside 280
    assert seen >= 2


def test_hedge_figures_maps_each_hedge_to_its_figure():
    from src.video_composer import hedge_figures
    words = [{"word": w} for w in ["Derinkuyu", "reaches", "roughly", "280", "feet", "and", "up", "to", "20,000"]]
    assert hedge_figures(words, ["280 feet"]) == {2: 3}
    assert hedge_figures(words, None) == {2: 3, 6: 8, 7: 8}
    assert hedge_figures(words, ["Derinkuyu"]) == {}
    assert hedge_figures(words, ["roughly", "280 feet"]) == {}             # a key term itself is no hedge


def test_a_hedge_is_never_gold_on_a_plate_without_its_figure(tmp_path):
    # "roughly" ends one sentence-final burst; its figure is not on that plate
    words = _timed("It reaches roughly. 280 feet deep is the floor.", sentence_pause=0.9)
    _, ass = _build(tmp_path, words, emphasis=["280 feet"])
    for _, _, t in _events(ass, "CapText"):
        words_shown = {w: c for c, w in _visible_words(t)}
        if "ROUGHLY." in words_shown:
            assert words_shown["ROUGHLY."] != COL_EMPHASIS


def test_the_contrast_record_measures_the_colours_the_file_draws():
    rows = [{"burst": 0, "text": "X", "gold": True}]
    drawn = caption_contrast_record(rows)
    dull = caption_contrast_record(rows, text="&H00808080&", emphasis="&H00606060&")
    assert dull["bone"] < drawn["bone"] and dull["gold"] < drawn["gold"]
    assert min(e["drawn"] for e in dull["elements"]) < brand.TEXT_CONTRAST


def test_libass_sees_the_variable_montserrat_only_as_its_thin_default():
    from src.video_composer import _libass_face
    names, weight = _libass_face(Path("fonts") / "Montserrat-Variable.ttf")
    assert "montserrat thin" in names and "montserrat" not in names and weight == 100
    assert _libass_face(Path("fonts") / "license_evidence.json") is None


def test_libass_font_problems_names_a_missing_face_and_a_synthetic_weight(tmp_path):
    from src.config import FONT_NAME
    from src.video_composer import libass_font_problems
    fonts = Path("fonts")
    assert libass_font_problems(fonts, [(FONT_NAME, 400)]) == []           # Anton: the 9:16 face resolves
    missing = libass_font_problems(fonts, [("Montserrat", 700)])
    assert len(missing) == 1 and "no bundled font" in missing[0]
    thin = libass_font_problems(fonts, [("Montserrat Thin", 700)])
    assert len(thin) == 1 and "synthetic" in thin[0]
    (tmp_path / "IBMPlexMono-SemiBold.ttf").write_bytes((fonts / "IBMPlexMono-SemiBold.ttf").read_bytes())
    assert libass_font_problems(tmp_path, [("IBM Plex Mono SmBld", 600)]) == []


def _libass_pick(fonts, fontname, tmp_path):
    import shutil
    import subprocess
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    ass = tmp_path / "probe.ass"
    ass.write_text(
        "[Script Info]\nPlayResX: 320\nPlayResY: 180\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, "
        "PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: T,{fontname},40,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1\n"
        "\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:00.00,0:00:01.00,T,,0,0,0,,Test\n", encoding="utf-8")
    res = subprocess.run(["ffmpeg", "-v", "trace", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x180:d=1", "-vf",
                          f"subtitles={ass}:fontsdir={fonts.resolve()}", "-frames:v", "1", str(tmp_path / "p.png")],
                         capture_output=True, text=True, timeout=60, check=False)
    picks = re.findall(r"fontselect: \(([^,]+), \d+, \d+\) -> ([^,]+),", res.stderr)
    if not picks:
        pytest.skip("this ffmpeg logs no libass font selection")
    return picks[0][1]


@pytest.mark.parametrize("fontname", ["Anton", "Montserrat", "Montserrat Thin"])
def test_libass_font_problems_agrees_with_libass(tmp_path, fontname):
    from src.video_composer import libass_font_problems
    fonts = Path("fonts").resolve()
    picked = _libass_pick(fonts, fontname, tmp_path)
    bundled = not picked.startswith("/") or picked.startswith(str(fonts))   # memory fonts log their name only
    named = not any("no bundled font" in p for p in libass_font_problems(fonts, [(fontname, 400)]))
    assert bundled == named


def test_compose_video_will_not_burn_in_16x9_captions_in_a_fallback_face(tmp_path):
    from src.video_composer import RenderError, libass_font_problems
    if not libass_font_problems(Path("fonts"), [("Montserrat", 700)]):
        pytest.skip("a static Montserrat Bold is bundled: the 16:9 face resolves")
    from PIL import Image
    img = tmp_path / "scene.jpg"
    Image.new("RGB", (64, 64), (120, 110, 90)).save(img)
    vo, bed = tmp_path / "vo.wav", tmp_path / "bed.wav"
    vo.write_bytes(b"RIFF"), bed.write_bytes(b"RIFF")
    with pytest.raises(RenderError, match="would not draw in the bundled font"):
        VideoComposer().compose_video([img], vo, bed, _timed("Derinkuyu reaches roughly 280 feet deep."), 3.0,
                                      tmp_path / "out.mp4", width=1920, height=1080, aspect_ratio="16:9")


def test_caption_constants_are_defined_once_in_config():
    import ast
    tree = ast.parse(Path("src/config.py").read_text(encoding="utf-8"))
    names = [t.id for node in tree.body if isinstance(node, ast.Assign) for t in node.targets
             if isinstance(t, ast.Name) and (t.id.startswith(("CAPTION_", "SIDECAR_")) or "BURST" in t.id)]
    assert sorted(n for n in set(names) if names.count(n) > 1) == []
    from src import config
    assert config.MAX_BURST_WORDS == 4 and config.CAPTION_WIDE_MARGIN_V == 130
    assert "y 1000 at 1080" not in Path("src/config.py").read_text(encoding="utf-8")
