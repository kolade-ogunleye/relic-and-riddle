"""Independent review of the operator batch (2026-11-03), round 3.

1. The loop seam's sound: the Reel's programme is cut at narration + LOOP_SECONDS, and the bed's 50 ms fade-out now
   lands inside that cut (it used to fall in the bed's headroom past it, so the delivered file ended at full level
   and every loop restarted on a level step and a click).
2. 80 px caption bursts: no burst of function words only, and no burst ending on an article, preposition or
   conjunction when another split fits.
3. The 9:16 render cache no longer keys on card_share (the loop bridge draws no share line); 16:9 still does.
"""
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import end_card

ROOT = Path(__file__).resolve().parent.parent
SR = 48000


# ---------------------------------------------------------------- 1. the loop seam's sound
def _render_reel(tmp_path):
    """A 9:16 master that ends on the loop bridge. The voice opens on 120 ms of silence, as narration does, so the
    master's first 20 ms are the bed alone, like its last 20 ms."""
    import soundfile as sf

    from src.audio_engine import AudioEngine
    from src.video_composer import VideoComposer
    vo_dur = 3.0
    t = np.arange(int(SR * vo_dur)) / SR
    env = np.clip((t - 0.12) / 0.02, 0.0, 1.0) * np.clip((2.8 - t) / 0.02, 0.0, 1.0)
    voice = tmp_path / "voiceover.wav"
    sf.write(voice, 0.3 * np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t)) * env, SR)
    music = tmp_path / "music.wav"
    tm = np.arange(int(SR * 10)) / SR
    sf.write(music, np.column_stack([0.2 * np.sin(2 * np.pi * 110 * tm)] * 2), SR)
    bed = AudioEngine().generate_ambient_bed(vo_dur + end_card.bed_tail(True), tmp_path / "ambient_bed.wav",
                                             music_path=music, end_card_at=vo_dur)
    scenes = []
    for i, colour in enumerate(((200, 170, 110), (60, 80, 120))):
        p = tmp_path / f"s{i}.jpg"
        im = Image.new("RGB", (1200, 2100), colour)
        ImageDraw.Draw(im).ellipse([300, 600, 900, 1400], fill=(240, 220, 160) if i else (40, 30, 20))
        im.save(p)
        scenes.append(p)
    words = [{"word": "Old", "start": 0.2, "dur": 0.4}, {"word": "maps.", "start": 0.7, "dur": 0.5},
             {"word": "Read", "start": 1.6, "dur": 0.4}, {"word": "them.", "start": 2.1, "dur": 0.5}]
    master = tmp_path / "seam_master.mp4"
    VideoComposer().compose_video(scenes, voice, bed, words, vo_dur, master, durations=[1.5, 1.5],
                                  end_card="Send this to the codebreaker you know.")
    return master, vo_dur


def _decoded_audio(path):
    """The delivered audio as a player loops it: decoded, then trimmed to the stream's declared length (the last AAC
    packet's duration: the ffmpeg CLI decodes its whole 1024-sample frame, encoder padding included)."""
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                            "stream=duration_ts,time_base", "-of", "default=nw=1", str(path)],
                           capture_output=True, text=True, check=True).stdout
    info = dict(line.split("=", 1) for line in probe.split())
    num, den = (int(x) for x in info["time_base"].split("/"))
    samples = round(int(info["duration_ts"]) * num / den * SR)
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a", "-f", "f32le", "-ac", "2",
                          "-ar", str(SR), "-"], capture_output=True, check=True)
    audio = np.frombuffer(out.stdout, np.float32).reshape(-1, 2).astype(np.float64)
    assert len(audio) >= samples
    return audio[:samples]


def _db(x):
    return 20 * np.log10(max(float(np.sqrt(np.mean(np.square(x)))), 1e-9))


def test_the_delivered_master_loops_without_a_level_step_or_a_click(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg is required to render a master")
    master, vo_dur = _render_reel(tmp_path)
    audio = _decoded_audio(master)
    assert len(audio) / SR == pytest.approx(vo_dur + end_card.LOOP_SECONDS, abs=0.03)
    n = int(0.020 * SR)
    last, first = audio[-n:], audio[:n]
    # the loop's restart: the last 20 ms against the first 20 ms, no step over 3 dB
    assert abs(_db(last) - _db(first)) <= 3.0, (_db(last), _db(first))
    # no discontinuity: the jump from the last sample to the first is no bigger than the largest sample-to-sample
    # step inside either window (a cut at full level jumps by up to the waveform's amplitude)
    seam = np.abs(first[0] - last[-1]).max()
    inside = max(np.abs(np.diff(last, axis=0)).max(), np.abs(np.diff(first, axis=0)).max())
    assert seam <= inside + 1e-3, (seam, inside)
    # and the bed's fade lies inside the cut: the file ends near silence rather than at the bridge's held level
    held = audio[int((vo_dur + 0.4) * SR):int((vo_dur + 0.6) * SR)]
    assert _db(audio[-int(0.005 * SR):]) < _db(held) - 12


def test_the_composer_fades_the_bed_at_the_cut_not_past_it():
    src = (ROOT / "src" / "video_composer.py").read_text(encoding="utf-8")
    assert '"-t", f"{total:.2f}"' not in src        # the delivery cut is the programme's cut, to the millisecond
    # the bed file keeps its headroom past the cut; the programme's fade, not the bed file's, ends the Reel
    assert end_card.bed_tail(True) > end_card.LOOP_SECONDS


def test_the_bridge_level_returns_to_the_bed_level_at_the_seam():
    from src.video_composer import SEAM_RETURN_S, _audio_chain
    chain = _audio_chain(0, 1, end_card.LOOP_SECONDS, 3.0, 2.6)
    assert f"(4.0000-t)/{SEAM_RETURN_S:.3f}" in chain            # back to unity by the cut
    assert "4.0000-t" not in _audio_chain(0, 1, 0.0, 3.0, 2.6)    # no pad, no seam: the old ramp only
    assert "volume" not in _audio_chain(0, 1, end_card.LOOP_SECONDS, 3.0, 0.0)


# ---------------------------------------------------------------- 2. 80 px caption bursts
EXAMPLES = [
    "Built under King Krishna I of the Rashtrakuta dynasty.",
    "They removed an estimated 200,000 tons of rock.",
    "No one knows who carved the statues or why they stopped.",
]
DOCUMENTARY = [
    "The temple was carved from a single rock in the eighth century.",
    "Workers cut down from the top of the cliff and worked their way to the floor.",
    "Beneath the sand lay a city that no map had ever shown.",
    "The scroll was found in a clay jar by a shepherd in 1947.",
    "Its walls are covered with carvings of gods and kings.",
    "For centuries the tomb was hidden under a hill of rubble.",
    "The ship sank off the coast of Antikythera around 60 BC.",
    "Divers brought up a lump of bronze that turned out to be a computer.",
    "Nobody expected to find gears inside a block of corroded metal.",
    "The pyramid's builders left no record of how they lifted the stones.",
    "Some blocks weigh more than two elephants and sit higher than a house.",
    "The city of Petra was carved into rose red sandstone by the Nabataeans.",
    "Water was channelled through the desert in pipes cut from the rock.",
    "In 1922 Howard Carter opened the sealed doorway of the tomb.",
    "Inside were chariots, beds and a golden mask of the young king.",
    "The Terracotta Army was buried with the first emperor of China.",
    "Each of the soldiers has a face unlike any of the others.",
    "Farmers digging a well in 1974 found the first fragments.",
    "The walls of the underground city could shelter thousands of people.",
    "Rolling stone doors sealed each level from the one above it.",
    "The monks copied the manuscript by candlelight over many winters.",
    "A single page could take a scribe more than a week to finish.",
    "The stones at Stonehenge were dragged from Wales across hundreds of miles.",
    "Archaeologists still argue about why the circle was built at all.",
    "The library at Alexandria held scrolls from across the known world.",
    "When it burned, much of what the ancients knew was lost with it.",
    "The statues on Easter Island face inland, toward the villages.",
    "Their eyes were made of white coral and red stone.",
    "Under the floor of the cave lay the bones of an extinct bear.",
    "The painting is older than writing, older than farming and older than cities.",
]


def _timed(text):
    return [{"word": w, "start": 0.3 * i, "dur": 0.25} for i, w in enumerate(text.split())]


def _sentences(words):
    from src.video_composer import TERMINAL_PUNCT, _is_abbreviation
    out, cur, off = [], [], 0
    for i, w in enumerate(words):
        if not cur:
            off = i
        cur.append(w)
        if TERMINAL_PUNCT.search(w["word"]) and not _is_abbreviation(w["word"]):
            out.append((off, cur))
            cur = []
    if cur:
        out.append((off, cur))
    return out


def _fewest_defects(sent, off, bonds):
    """The fewest defects (a function-word-only burst: 2; a burst ending mid-sentence on an article, preposition,
    conjunction or relative word: 1) of any split the Reel's rules allow, by brute force over every split: a burst
    of 2+ words fits at the burst floor (a bonded run at the bond floor), no cut splits a bond or an atomic pair,
    at most MAX_BURST_WORDS (bonded words past it), and a lone burst only of a content word."""
    from functools import lru_cache

    from src.config import MAX_BURST_WORDS
    from src.video_composer import (DANGLING_ENDS, FUNCTION_WORDS, _fits_caption_bonded, _fits_caption_burst,
                                    _is_atomic_boundary)
    bare = [__import__("re").sub(r"[^\w]", "", w["word"]).lower() for w in sent]
    n = len(sent)

    @lru_cache(maxsize=None)
    def best(i):
        if i == n:
            return 0
        found = None
        for size in range(1, min(n - i, MAX_BURST_WORDS + 2) + 1):
            j = i + size
            if size > MAX_BURST_WORDS and not all(off + i + k - 1 in bonds for k in range(MAX_BURST_WORDS, size)):
                continue
            if j < n and (off + j - 1 in bonds or _is_atomic_boundary(sent[j - 1]["word"], sent[j]["word"])):
                continue
            chunk = sent[i:j]
            function_only = all(b in FUNCTION_WORDS for b in bare[i:j])
            if size == 1 and function_only and n > 1:
                continue
            if size > 1 and not _fits_caption_burst(chunk) and not (
                    all(off + i + k in bonds for k in range(1, size - 1)) and _fits_caption_bonded(chunk)):
                continue
            rest = best(j)
            if rest is None:
                continue
            cost = rest + (2 if function_only else (1 if j < n and bare[j - 1] in DANGLING_ENDS else 0))
            found = cost if found is None else min(found, cost)
        return found
    return best(0)


def _check(text):
    from src.video_composer import (FUNCTION_WORDS, _split_defects, caption_bonds, partition_words_grammatical)
    words = _timed(text)
    bonds = caption_bonds(words)
    phrases = partition_words_grammatical(words)
    assert [w for p in phrases for w in p] == words
    shown = [" ".join(w["word"] for w in p).upper() for p in phrases]
    for p in phrases:        # never a burst of function words only
        assert not all(__import__("re").sub(r"[^\w]", "", w["word"]).lower() in FUNCTION_WORDS for w in p), shown
    k = 0
    for off, sent in _sentences(words):
        split = []
        while k < len(phrases) and sum(map(len, split)) < len(sent):
            split.append(phrases[k])
            k += 1
        fewest = _fewest_defects(sent, off, bonds)
        assert fewest is not None, shown        # every sentence here has a split the rules allow
        # a dangling end only where no other split fits: as few defects as any split the rules allow
        assert _split_defects(split) == fewest, (shown, _split_defects(split), fewest)
    return shown


def test_the_three_review_sentences():
    built, removed, carved = (_check(t) for t in EXAMPLES)
    # operator decision 2026-11-04 (deliberate change; a proper name is never split, "UNDER | THE RASHTRAKUTA
    # DYNASTY"): both names stay whole at no size under the floors, and no burst ends on THE. Was: no burst ends
    # on UNDER, and one starts UNDER KING KRISHNA I (which left RASHTRAKUTA | DYNASTY split).
    assert "OF THE" not in built and not any(b.endswith(" THE") for b in built), built
    assert any("KING KRISHNA I" in b for b in built) and built[-1].endswith("THE RASHTRAKUTA DYNASTY."), built
    assert not any(b.endswith(" OR WHY") or b.endswith(" OR") for b in carved), carved
    assert any(b.startswith("OR WHY") for b in carved), carved
    # THEY REMOVED AN | ESTIMATED 200,000 TONS stays: every other split breaks a rule that ranks above a dangling
    # end (a lone AN is a function-word-only burst; AN ESTIMATED 200,000 TONS fits only at 67 px, under the burst
    # floor, as tests/test_operator_batch_2026_11_03.py pins; AN ESTIMATED | 200,000 splits the hedge's bond)
    assert "ESTIMATED 200,000 TONS" in removed


@pytest.mark.parametrize("text", DOCUMENTARY)
def test_documentary_sentences_split_without_function_word_bursts_or_avoidable_dangling_ends(text):
    _check(text)


def test_a_dangling_end_costs_more_than_a_lone_content_word():
    from src.video_composer import (DANGLING_PENALTY, FUNCTION_ONLY_PENALTY, LONE_WORD_PENALTY,
                                    SHRUNK_BURST_PENALTY)
    assert DANGLING_PENALTY > LONE_WORD_PENALTY
    assert DANGLING_PENALTY > LONE_WORD_PENALTY + SHRUNK_BURST_PENALTY      # a lone word and a shrunk burst together
    assert FUNCTION_ONLY_PENALTY > DANGLING_PENALTY


# ---------------------------------------------------------------- 3. the 9:16 render cache
def test_the_reel_render_cache_keys_on_whether_a_bridge_is_drawn_not_on_the_share_text():
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    assert '"end_card": card_share,' not in src
    assert 'card_key = card_share if LOOP_LINE else bool((card_share or "").strip())' in src
    assert '"end_card": card_key,' in src
    assert "end_card=card_share," in src           # the composer still gets the line (empty: no bridge)
    assert end_card.LOOP_LINE is False             # so two share lines key the same 9:16 master
