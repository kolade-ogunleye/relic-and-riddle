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
