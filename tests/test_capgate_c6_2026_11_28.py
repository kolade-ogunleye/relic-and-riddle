"""Freeze c6 capgate (2026-11-28; audit of dev run 1795857682, lander's rulings): the caption gate blocked every run
on a measuring fault. It cut libass's soft-edged coverage to on/off before the per-letter check, so each letter's
own half-covered edge counted as its ground (3.49:1 where the plate reads 11.8), and it judged 6 burst midpoints.

Ruling 1: a letter's ground never includes its own soft edge (ink above CAPTION_INK_COVERAGE, ground at or under
CAPTION_GROUND_COVERAGE, neither between). Ruling 2: every burst is judged once fully shown; a dip inside a fade for
no longer than the fade (from the fade tokens) is reported, anything else under 4.5:1 blocks. Ruling 4: the console's
outputs line, the timeline audit's log line and a checker-contradicted finding."""
import json
import shutil
import subprocess

import numpy as np
import pytest

from src import config, video_qa
from src.typography import contrast_ratio, text_contrast

BONE, GOLD, PLATE = (0xE2, 0xED, 0xF3), (0xF2, 0xB6, 0x30), (0x0B, 0x0E, 0x12)


# ---------------------------------------------------------------- ruling 1: the soft edge is never a letter's ground
def _stems(ground, ink, edge_right=110, edge_left=200, h=120, w=200):
    """Three letter stems on a flat `ground`, each with a soft (antialiased) edge column of known coverage on both
    sides, drawn as libass blends it (coverage x ink + (1 - coverage) x ground). (rgb, coverage)."""
    cov = np.zeros((h, w), np.uint8)
    for x in (40, 90, 140):
        cov[30:90, x:x + 12] = 255
        cov[30:90, x - 1] = edge_left
        cov[30:90, x + 12] = edge_right
    a = cov[..., None].astype(np.float64) / 255.0
    rgb = np.round(a * np.array(ink, np.float64) + (1 - a) * np.array(ground, np.float64)).astype(np.uint8)
    return rgb, cov


def test_a_letters_soft_edge_is_never_counted_as_its_ground():
    rgb, cov = _stems(PLATE, BONE)
    exact = contrast_ratio(BONE, PLATE)
    got = video_qa.caption_frame_contrast(rgb, cov)
    assert got == pytest.approx(exact, rel=0.005)            # the ink against the plate, nothing else (~16:1)
    # the defect, on the same pixels: the hard on/off cut counts the 110 edge column as ground
    hard = text_contrast(rgb, ((cov > 127) * 255).astype(np.uint8), (0, 0))
    assert hard < 0.6 * exact


def test_the_letter_mask_keeps_ink_ground_and_neither_apart():
    cov = np.array([[0, 1, 64, 127, 128, 200, 255]], np.uint8)
    m = video_qa.caption_letter_mask(cov)
    assert (m > 127).tolist() == (cov > video_qa.CAPTION_INK_COVERAGE).tolist()        # ink
    assert (m == 0).tolist() == (cov <= video_qa.CAPTION_GROUND_COVERAGE).tolist()     # ground
    assert m[0, 1] > 0 and m[0, 3] > 0                                                 # touched: neither


def test_the_coverage_thresholds_are_declared():
    assert video_qa.CAPTION_INK_COVERAGE == 127 and video_qa.CAPTION_GROUND_COVERAGE == 0


# ---------------------------------------------------------------- ruling 2: every burst, fully shown; fades
def _ass(bursts):
    """A caption file of 9:16 bursts as the composer draws them: [(start_s, end_s, [line words])]: per burst a
    plate, one event a word (the word spoken now easing in over CAPTION_WORD_EASE_MS, the words still to come
    hidden) and the last event fading out with the plate."""
    def ts(s):
        return f"0:00:{s:05.2f}"
    lines = ["[Script Info]", "PlayResX: 1080", "PlayResY: 1920", "", "[Events]"]
    for s, e, words in bursts:
        lines.append(f"Dialogue: 0,{ts(s)},{ts(e)},CapBox,,0,0,0,,{{\\pos(300,1100)\\fad(0,80)\\p1}}m 0 0 l 400 0{{\\p0}}")
        step = (e - s) / len(words)
        for wi in range(len(words)):
            ws, we = s + wi * step, (s + (wi + 1) * step if wi < len(words) - 1 else e)
            shown = " ".join(words[:wi])
            ease = f"{{\\alpha&HFF&\\t(0,{config.CAPTION_WORD_EASE_MS},\\alpha&H00&)}}{words[wi]}"
            hidden = "".join(f" {{\\alpha&HFF&}}{w}" for w in words[wi + 1:])
            fad = "{\\fad(0,80)}" if wi == len(words) - 1 else ""
            lines.append(f"Dialogue: 1,{ts(round(ws, 2))},{ts(round(we, 2))},CapText,,0,0,0,,{{\\pos(540,1150)\\q2}}"
                         f"{fad}{shown} {ease}{hidden}")
    return "\n".join(lines) + "\n"


def _gate(text, low=(), value=9.0, low_value=3.0, fps=30.0):
    bursts = video_qa.caption_bursts(text)
    rows, shown = video_qa.caption_frame_plan(bursts, fps)
    values = {n: (low_value if n in low else value) for n, _, _ in rows}
    return bursts, rows, shown, video_qa.caption_verdict(bursts, rows, shown, values, fps)


ONE = _ass([(0.0, 1.0, ["ONE", "TWO"])])   # word 2 eases in over 500-570 ms; the burst fades out over 920-1000 ms


def test_every_burst_is_judged_fully_shown_not_six_midpoints():
    text = _ass([(k * 1.0, k * 1.0 + 0.9, ["A", "B", "C"]) for k in range(12)])
    bursts, rows, _shown, v = _gate(text)
    assert len(bursts) == 12 and len(v["samples"]) == 12 and v["bursts"] == 12
    assert not v["under"]
    # one unreadable burst the 6 midpoints never sampled (12 events: bursts 0, 2, 4, 7, 9, 11) still blocks
    hit = [n for n, bi, _ in rows if bi == 5]
    _, _, _, v = _gate(text, low=set(hit))
    assert v["under"] and {u["burst"] for u in v["under"]} == {5}
    assert video_qa.caption_findings(v)


def test_the_fully_shown_frame_has_every_word_drawn_and_no_fade_in_progress():
    _, _, shown, v = _gate(ONE)
    n = shown[0]
    ms = video_qa._frame_ms(n, 30.0)
    assert 570 <= ms < 920                                    # after the last word's ease, before the fade-out
    assert v["samples"][0]["settled"] is True and v["samples"][0]["text"] == "ONE TWO"
    _, _, _, v = _gate(ONE, low={n})
    assert [u["kind"] for u in v["under"]] == ["still"] and v["under"][0]["contrast"] == 3.0


def test_a_one_frame_dip_inside_a_fade_is_a_note_and_never_blocks():
    fade_out = [n for n in range(40) if 920 <= video_qa._frame_ms(n, 30.0) < 1000]
    _, _, _, v = _gate(ONE, low={fade_out[0]})
    assert not v["under"] and not video_qa.caption_findings(v)
    assert len(v["dips"]) == 1 and v["dips"][0]["frames"] == 1
    assert v["dips"][0]["t"] == pytest.approx(fade_out[0] / 30.0, abs=1e-3) and v["dips"][0]["contrast"] == 3.0


def test_a_dip_longer_than_the_fade_blocks():
    ease = [n for n in range(40) if 500 <= video_qa._frame_ms(n, 30.0) < 570]
    assert len(ease) == 3                                      # 70 ms covers three frame instants here
    _, _, _, v = _gate(ONE, low=set(ease))
    assert [u["kind"] for u in v["under"]] == ["longer_than_fade"] and v["under"][0]["frames"] == 3
    assert "longer than a caption fade" in video_qa.caption_findings(v)[0]


def test_a_one_frame_dip_on_a_still_caption_blocks():
    still = [n for n in range(40) if 0 < video_qa._frame_ms(n, 30.0) < 500
             and not any(f0 <= video_qa._frame_ms(n, 30.0) < f1 for f0, f1 in video_qa.caption_bursts(ONE)[0]["fades"])]
    _, _, _, v = _gate(ONE, low={still[0]})
    assert [u["kind"] for u in v["under"]] == ["still"]


def test_the_fade_allowance_follows_the_fade_tokens(monkeypatch):
    assert video_qa.caption_fade_allowance(30) == 2            # 80 ms (the plate's fade-out) at 30 fps
    ease = [n for n in range(40) if 500 <= video_qa._frame_ms(n, 30.0) < 570]
    monkeypatch.setattr(config, "CAPTION_PLATE_FADE_OUT_MS", 120)
    assert video_qa.caption_fade_allowance(30) == 3
    _, _, _, v = _gate(ONE, low=set(ease))
    assert not v["under"] and len(v["dips"]) == 1              # three frames inside a 120 ms fade: a note
    for token in ("CAPTION_WORD_EASE_MS", "CAPTION_PLATE_FADE_IN_MS", "CAPTION_PLATE_FADE_OUT_MS"):
        monkeypatch.setattr(config, token, 30)
    assert video_qa.caption_fade_allowance(30) == 0
    fade_out = [n for n in range(40) if 920 <= video_qa._frame_ms(n, 30.0) < 1000]
    _, _, _, v = _gate(ONE, low={fade_out[0]})
    assert [u["kind"] for u in v["under"]] == ["longer_than_fade"]
    assert video_qa.caption_fade_allowance(60) == 1


def test_captions_under_the_end_card_are_left_out():
    text = _ass([(0.0, 1.0, ["A"]), (2.0, 3.0, ["B"]), (4.5, 5.0, ["C"])])
    assert [b["text"] for b in video_qa.caption_bursts(text, until=4.55)] == ["A", "B"]
    assert video_qa.caption_bursts(text, until=2.05)[-1]["end"] <= 2050


# ---------------------------------------------------------------- the real pipeline: libass, x264, the gate
FF = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not FF, reason="ffmpeg with libass is needed to draw a master")

CASE = """[Script Info]
ScriptType: v4.00+
PlayResX: 360
PlayResY: 640
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: CapText,Anton,64,&H00F3EDE2&,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0.5,0,1,0,0,5,0,0,0,1
Style: CapBox,Anton,64,{plate},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:01.00,CapBox,,0,0,0,,{{\\pos(30,280)\\p1}}m 0 0 l 300 0 l 300 {ph} l 0 {ph}{{\\p0}}
Dialogue: 1,0:00:00.00,0:00:01.00,CapText,,0,0,0,,{{\\pos(180,320)\\q2}}{{\\c{colour}}}STONE ARCH
"""
ASS_BONE, ASS_GOLD = "&H00F3EDE2&", "&H0030B6F2&"


def _master(tmp_path, plate, colour, ph=80, ground="0x404040"):
    ass = tmp_path / "c.ass"
    ass.write_bytes(CASE.format(plate=plate, colour=colour, ph=ph).encode("utf-8"))
    master = tmp_path / "m.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={ground}:s=360x640:r=30:d=1", "-vf",
                    f"subtitles='{video_qa._ffmpeg_path(ass)}':fontsdir='{video_qa._ffmpeg_path(config.FONTS_DIR)}'",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-pix_fmt", "yuv420p", str(master)],
                   check=True, timeout=120)
    return master, ass


@needs_ffmpeg
def test_a_readable_caption_passes_on_a_real_master(tmp_path):
    master, ass = _master(tmp_path, "&H200B0E12&", ASS_BONE)          # bone on the brand's dark plate
    v = video_qa.caption_contrast(master, ass, tmp_path / "out")
    assert v["bursts"] == 1 and v["samples"][0]["contrast"] > 10 and not video_qa.caption_findings(v)
    assert v["frames_measured"] == v["frames"] == 30
    record = json.loads((tmp_path / "out" / "caption_frames.json").read_text(encoding="utf-8"))
    assert len(record["frames"]) == 30


@needs_ffmpeg
@pytest.mark.parametrize("plate, colour, ph, ground", [
    ("&H00C8C8C8&", ASS_BONE, 80, "0x404040"),     # bone text on a light plate
    ("&H00808080&", ASS_GOLD, 80, "0x404040"),     # gold on a mid-grey plate
    ("&H200B0E12&", ASS_BONE, 40, "0xF0F0F0"),     # text half off its plate, its lower half on a light ground
], ids=["bone_on_light_plate", "gold_on_mid_grey_plate", "half_off_its_plate"])
def test_an_unreadable_caption_still_fails(tmp_path, plate, colour, ph, ground):
    master, ass = _master(tmp_path, plate, colour, ph, ground)
    v = video_qa.caption_contrast(master, ass, tmp_path / "out")
    found = video_qa.caption_findings(v)
    assert found and v["samples"][0]["contrast"] < 4.5 and not v["dips"]
    assert "fully shown" in " ".join(found)


# ---------------------------------------------------------------- lander ruling 1 (follow-up): no preview words
def _drawn_words(text):
    """Each word chunk of a CapText event as libass draws it: (visible, colour, text). Alpha and colour carry from
    one override block to the next; an alpha ease (\\t to &H00&) makes a word visible."""
    import re
    alpha, colour, out = "00", None, []
    for tags, chunk in re.findall(r"\{([^}]*)\}|([^{]+)", text):
        if tags:
            for a in re.findall(r"\\alpha&H([0-9A-Fa-f]{2})&", tags.split(r'\t(')[0]):
                alpha = a.upper()
            if re.search(r"\\t\([^)]*\\alpha&H00&", tags):
                alpha = "00"
            for c in re.findall(r"\\c&H([0-9A-Fa-f]{6,8})&", tags):
                colour = c.upper()[-6:]
        elif chunk.strip():
            out.append((alpha != "FF", colour, chunk.strip()))
    return out


@pytest.mark.parametrize("kw", [{}, {"first_frame_whole": 0.5}, {"width": 1920, "height": 1080}],
                         ids=["9x16_reveal", "9x16_first_frame_whole", "16x9_whole_burst"])
def test_the_current_composer_never_draws_a_word_still_to_be_spoken(tmp_path, kw):
    """Lander ruling 1 (2026-11-28): grey preview words COUNT as text a viewer reads, and the retired design drew the
    words still to come visible in grey at 3.2-4.49:1 (27 dev masters). The current composer hides a word until it is
    spoken (9:16 reveal) or draws the whole burst in its reading colours (16:9, a first frame shown whole): no visible
    word is ever in the upcoming-word colour or any colour but the reading ones, so that state cannot return."""
    import re
    from src import video_composer as vc
    narration = ("Beneath the old city lies a maze carved by hand. It reaches roughly 280 feet deep, about eighteen "
                 "levels of tunnels and halls. Up to 20,000 people could shelter here, with livestock and food.")
    words, t = [], 0.0
    for w in narration.split():
        words.append({"word": w, "start": round(t, 3), "dur": 0.32})
        t += 0.36
    path = vc.VideoComposer().build_subtitles(words, tmp_path / "c.ass", emphasis=["280 feet"], **kw)
    reading = {c.upper()[-7:-1] for c in (vc._col_past(), vc._col_active(), vc._col_emphasis())}
    future = vc._col_future().upper()[-7:-1]
    seen = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not (line.startswith("Dialogue:") and ",CapText," in line):
            continue
        for visible, colour, chunk in _drawn_words(line.split(",", 9)[9]):
            if colour is None or re.fullmatch(r"\W*", chunk):
                continue
            if visible:
                seen += 1
                assert colour in reading and colour != future, (chunk, colour, line[:160])
    assert seen > 20


# ---------------------------------------------------------------- review round (2026-11-28): the bar, never-settled
def test_the_bar_is_4_5_to_1_on_the_real_bound():
    """Review B1 (G1): the bar is brand.TEXT_CONTRAST, 4.5:1, unchanged: a burst fully shown at 4.4 blocks and at 4.6
    passes, read against the real bound (no bound passed)."""
    from src.brand import TEXT_CONTRAST
    assert TEXT_CONTRAST == 4.5
    bursts = video_qa.caption_bursts(ONE)
    rows, shown = video_qa.caption_frame_plan(bursts, 30.0)
    for reading, blocks in ((4.4, True), (4.6, False), (4.49, True), (4.5, False)):
        values = {n: 9.0 for n, _, _ in rows}
        values[shown[0]] = reading
        v = video_qa.caption_verdict(bursts, rows, shown, values, 30.0)
        assert bool(v["under"]) is blocks, reading
        assert bool(video_qa.caption_findings(v)) is blocks, reading


NEVER_SETTLED = "\n".join([
    "[Script Info]", "PlayResX: 1080", "PlayResY: 1920", "", "[Events]",
    "Dialogue: 0,0:00:00.00,0:00:00.60,CapBox,,0,0,0,,{\\pos(300,1100)\\fad(0,80)\\p1}m 0 0 l 400 0{\\p0}",
    "Dialogue: 1,0:00:00.00,0:00:00.50,CapText,,0,0,0,,{\\pos(540,1150)\\q2}A {\\alpha&HFF&}B",
    "Dialogue: 1,0:00:00.50,0:00:00.60,CapText,,0,0,0,,{\\pos(540,1150)\\q2}{\\fad(0,80)}A "
    "{\\alpha&HFF&\\t(0,70,\\alpha&H00&)}B",
]) + "\n"


def test_a_never_settled_burst_under_the_bar_fully_shown_blocks():
    """Review B1 (G2): a burst whose last word arrives inside its own fade-out is never still; its fully shown frame
    lies inside a fade, so a one-frame reading under the bar there is not a dip: the burst fully shown blocks."""
    bursts = video_qa.caption_bursts(NEVER_SETTLED)
    rows, shown = video_qa.caption_frame_plan(bursts, 30.0)
    n = shown[0]
    assert all(fading for m, _, fading in rows if video_qa._frame_ms(m, 30.0) >= 500)   # never still once whole
    values = {m: 9.0 for m, _, _ in rows}
    values[n] = 3.0
    v = video_qa.caption_verdict(bursts, rows, shown, values, 30.0)
    assert v["samples"][0]["settled"] is False and not v["dips"]
    assert [u["kind"] for u in v["under"]] == ["fully_shown"]
    assert "fully shown" in video_qa.caption_findings(v)[0]


# ---------------------------------------------------------------- review round: faint (partly opaque) text
def test_a_faint_letter_is_measured_as_drawn():
    """Ruling R2 (review N1): text held at partial opacity is still text. Stems drawn at 43.5 % opacity (alpha
    &H90&, peak coverage 111, under CAPTION_INK_COVERAGE everywhere) are measured: the composited colour against the
    ground, the analytic value."""
    op = (255 - 0x90) / 255.0
    ground = (0x50, 0x50, 0x50)
    rgb, cov = _stems(ground, BONE, edge_right=round(110 * op), edge_left=round(200 * op))
    full = cov == 255
    cov = np.where(full, round(255 * op), cov).astype(np.uint8)
    a = cov[..., None].astype(np.float64) / 255.0
    rgb = np.round(a * np.array(BONE, np.float64) + (1 - a) * np.array(ground, np.float64)).astype(np.uint8)
    drawn = tuple(int(c) for c in rgb[60, 45])
    got = video_qa.caption_frame_contrast(rgb, cov)
    assert got is not None and got == pytest.approx(contrast_ratio(drawn, ground), rel=0.005)
    assert got < 4.5                                         # 43.5 % bone on 505050: unreadable, and now it says so


def test_a_piece_that_never_reaches_five_percent_is_not_drawn_text():
    rgb, cov = _stems(PLATE, BONE)
    cov = np.where(cov > 0, np.minimum(cov, video_qa.CAPTION_FAINT_MIN_PEAK - 1), 0).astype(np.uint8)
    assert video_qa.caption_frame_contrast(rgb, cov) is None


# ---------------------------------------------------------------- review round: the mask's coverage scale (N2)
def test_the_mask_file_draws_coverage_on_the_full_scale():
    for text in (CASE.format(plate="&H200B0E12&", colour=ASS_BONE, ph=80),
                 CASE.format(plate="&H200B0E12&", colour=ASS_BONE, ph=80).replace(
                     "[Script Info]\n", "[Script Info]\nYCbCr Matrix: TV.601\n")):
        mask = video_qa._caption_mask_ass(text)
        heads = [ln for ln in mask.splitlines() if ln.lower().startswith("ycbcr matrix:")]
        assert heads == ["YCbCr Matrix: None"]
        assert "CapBox" not in "".join(ln for ln in mask.splitlines() if ln.startswith("Dialogue:"))


@needs_ffmpeg
def test_the_mask_peaks_at_full_coverage_without_the_header(tmp_path):
    """Review N2: a caption file without `YCbCr Matrix: None` (every older master) drew the mask's white at 235."""
    text = CASE.format(plate="&H200B0E12&", colour=ASS_BONE, ph=80)
    assert "YCbCr" not in text
    m = tmp_path / "m.ass"
    m.write_bytes(video_qa._caption_mask_ass(text).encode("utf-8"))
    out = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=360x640:r=30:d=0.5,format=rgb24",
                          "-vf", f"subtitles='{video_qa._ffmpeg_path(m)}':fontsdir='{video_qa._ffmpeg_path(config.FONTS_DIR)}'",
                          "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True,
                         timeout=120).stdout
    assert max(out) == 255


# ---------------------------------------------------------------- review round: decoding (N5), crop pad (N6), record (N7)
@needs_ffmpeg
def test_a_truncated_master_fails_closed_wherever_the_missing_frames_fall(tmp_path):
    """Review N5: the master ends at 1.5 s while a caption runs to 2.0 s; the fully shown frame (1.0 s) is decoded,
    the burst's last half second is not. The measure refuses (it skipped them silently), and the audit fails closed."""
    ass = tmp_path / "x_master_captions.ass"
    ass.write_bytes(CASE.format(plate="&H200B0E12&", colour=ASS_BONE, ph=80).replace("0:00:01.00,CapBox",
                    "0:00:02.00,CapBox").replace("0:00:01.00,CapText", "0:00:02.00,CapText").encode("utf-8"))
    master = tmp_path / "x_master.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0x404040:s=360x640:r=30:d=1.5", "-vf",
                    f"subtitles='{video_qa._ffmpeg_path(ass)}':fontsdir='{video_qa._ffmpeg_path(config.FONTS_DIR)}'",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-pix_fmt", "yuv420p", str(master)],
                   check=True, timeout=120)
    with pytest.raises(RuntimeError, match="could not be decoded"):
        video_qa.caption_contrast(master, ass, tmp_path / "out")
    audit = video_qa.text_contrast_audit(tmp_path, master, ass)
    assert audit["certified"] is False
    assert any("caption contrast could not be measured" in i["issue"] and "could not be decoded" in i["issue"]
               for i in audit["issues"] if i["severity"] == "major")


def test_the_crop_reads_what_the_whole_frame_reads():
    """Review N6: the frame is measured in the box around the coverage, padded by _CAPTION_CROP_PAD; a light band 4-9
    px right of the last letter's ink lies in its surround (1-4 px) and the ring (2-10 px), and outside a 2 px pad, so
    a pad too small to hold them changes the reading."""
    rgb, cov = _stems(PLATE, BONE)
    xs = np.nonzero(cov.any(axis=0))[0]
    rgb = rgb.copy()
    rgb[30:90, xs.max() + 3:xs.max() + 9] = (0x90, 0x90, 0x90)   # 4-9 px out of the ink: surround and ring
    whole = text_contrast(rgb, video_qa.caption_letter_mask(cov), (0, 0))
    got = video_qa.caption_frame_contrast(rgb, cov)
    clean = video_qa.caption_frame_contrast(*_stems(PLATE, BONE))
    assert got == whole and got < clean * 0.9


def test_the_record_keeps_every_reading_unrounded():
    """Review N7: 4.4996 was written 4.500, so the record said a frame passed that the verdict counted under."""
    rec = video_qa.caption_frames_record([(5, 0, False), (6, 0, True)], {5: 4.4996, 6: None}, 30.0, 2)
    assert rec["frames"][0]["contrast"] == 4.4996 and rec["frames"][0]["contrast"] < 4.5
    assert rec["frames"][1]["contrast"] is None and rec["allowance_frames"] == 2


# ---------------------------------------------------------------- review round: the real pipeline, faint word and badge
def _render(tmp_path, ass_text, ground="0x404040", size="360x640", dur=1):
    ass = tmp_path / "c.ass"
    ass.write_bytes(ass_text.encode("utf-8"))
    master = tmp_path / "m.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={ground}:s={size}:r=30:d={dur}", "-vf",
                    f"subtitles='{video_qa._ffmpeg_path(ass)}':fontsdir='{video_qa._ffmpeg_path(config.FONTS_DIR)}'",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-pix_fmt", "yuv420p", str(master)],
                   check=True, timeout=120)
    return master, ass


@needs_ffmpeg
def test_a_faint_word_beside_solid_text_blocks(tmp_path):
    """Ruling R2 (the reviewer's faint_alpha90_word_beside_solid): bone "ARCH" at alpha &H90& (43.5 % opacity) beside
    solid "STONE", on a 505050 plate, for the whole second: measured as drawn, it reads under the bar and blocks."""
    text = CASE.format(plate="&H00505050&", colour=ASS_BONE, ph=80).replace("STONE ARCH",
                                                                           "STONE {\\alpha&H90&}ARCH")
    master, ass = _render(tmp_path, text)
    v = video_qa.caption_contrast(master, ass, tmp_path / "out")
    found = video_qa.caption_findings(v)
    assert found and v["samples"][0]["contrast"] < 4.5, (v["samples"], found)
    solid = CASE.format(plate="&H00505050&", colour=ASS_BONE, ph=80)   # the same, solid: readable
    master, ass = _render(tmp_path, solid)
    v = video_qa.caption_contrast(master, ass, tmp_path / "out2")
    assert not video_qa.caption_findings(v) and v["samples"][0]["contrast"] > 4.5


BADGE = """[Script Info]
ScriptType: v4.00+
PlayResX: 360
PlayResY: 640
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: CapText,Anton,64,&H00F3EDE2&,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0.5,0,1,0,0,5,0,0,0,1
Style: CapBox,Anton,64,&H200B0E12&,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: ReconBadge,IBM Plex Mono SemiBold,30,{badge},&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,1.5,0,1,0,0,7,0,0,0,1
Style: ReconPlate,IBM Plex Mono SemiBold,30,&H26120E0B&,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:01.00,CapBox,,0,0,0,,{{\\pos(30,280)\\p1}}m 0 0 l 300 0 l 300 80 l 0 80{{\\p0}}
Dialogue: 1,0:00:00.00,0:00:01.00,CapText,,0,0,0,,{{\\pos(180,320)\\q2}}{{\\c&H00F3EDE2&}}STONE ARCH
{plate}Dialogue: 3,0:00:00.00,0:00:01.00,ReconBadge,,0,0,0,,{{\\an7\\pos(30,46)}}AI ILLUSTRATION
"""
BADGE_PLATE = "Dialogue: 2,0:00:00.00,0:00:01.00,ReconPlate,,0,0,0,,{\\pos(20,40)\\p1}m 0 0 l 300 0 l 300 46 l 0 46{\\p0}\n"


@needs_ffmpeg
@pytest.mark.parametrize("badge, plate, ground, blocks", [
    ("&H00F3EDE2&", True, "0x707070", False),     # bone on its ink plate: the badge as drawn
    ("&H00404040&", True, "0x707070", True),      # the reviewer's unreadable badge: 404040 on its ink plate
    ("&H00F3EDE2&", False, "0xF0F0F0", True),     # shown without its plate over a bright ground
], ids=["readable_badge", "unreadable_404040_badge", "badge_without_plate_on_bright"])
def test_the_ai_badge_is_measured(tmp_path, badge, plate, ground, blocks):
    """Lander ruling N8: the AI badge's letters (ReconBadge, drawn by the caption file) are measured in their own
    colour against what is under them, at 4.5:1, on every interval the badge is shown; the captions are unaffected."""
    text = BADGE.format(badge=badge, plate=BADGE_PLATE if plate else "")
    master, ass = _render(tmp_path, text, ground=ground)
    v = video_qa.caption_contrast(master, ass, tmp_path / "out")
    assert v["badge"] is not None and v["badge"]["bursts"] == 1 and v["badge"]["samples"][0]["contrast"] is not None
    badge_found = [f for f in video_qa.caption_findings(v) if f.startswith("AI badge:")]
    assert bool(badge_found) is blocks, (v["badge"]["samples"], badge_found)
    assert video_qa.caption_unmeasured(v) is None


def test_a_badge_that_cannot_be_measured_fails_closed():
    v = {"samples": [{"t": 0.5, "contrast": 9.0}], "bursts": 1, "under": [], "dips": [],
         "badge": {"samples": [{"t": 0.5, "contrast": None}], "bursts": 1, "under": [], "dips": []}}
    assert "AI badge" in video_qa.caption_unmeasured(v)
    v["badge"]["samples"] = []
    assert "AI badge" in video_qa.caption_unmeasured(v)
