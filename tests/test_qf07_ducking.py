"""QF-07 (review 2026-11-05 #9): the bed's duck under the narration must not pump between words.

The composer's own _audio_chain runs through ffmpeg on deterministic inputs made here: a narration of voiced words
(a pulse train shaped by three formants, 0.5 s words separated by 0.15-0.45 s gaps, then a long pause) and a steady
bed (a chord pad). The ducked bed is isolated from the graph's output with the graph unchanged: the mix with the bed
minus the mix with the bed silenced (the voice branch is identical in both), against the mix with the voice
silenced (the bed undocked). On the start commit (220 ms release, no hold, full-band key) the bed swelled back a
median 4.7 dB inside these gaps and its depth under a word followed the word's level (a 6.6 dB spread).
"""
from __future__ import annotations

import re
import subprocess
from itertools import pairwise

import numpy as np
import pytest
import soundfile as sf
from scipy.signal import butter, sosfilt

from src import end_card
from src.video_composer import _audio_chain

SR = 48000
BLOCK = 480                      # 10 ms
GAPS = (0.15, 0.25, 0.35, 0.45, 0.2, 0.4, 0.3)
WORD = 0.5
LEAD = 0.5                       # s of bed before the first word
PAUSE = 2.5                      # s of bed after the last word (the Reel's loop bridge is 1.05 s)

def _words():
    t, spans = LEAD, []
    for gap in (*GAPS, None):
        spans.append((t, t + WORD))
        t += WORD + (gap or 0.0)
    return spans


def _narration(path, spans, seconds):
    n = int(seconds * SR)
    t = np.arange(n) / SR
    f0 = 118.0 * (1 + 0.05 * np.sin(2 * np.pi * 0.4 * t))
    pulses = (np.diff(np.floor(np.cumsum(f0) / SR), prepend=0.0) > 0).astype(float)
    src = sosfilt(butter(1, 900, "lp", fs=SR, output="sos"), pulses) * 8.0
    voiced = sum(sosfilt(butter(2, [f * 0.85, f * 1.15], "bp", fs=SR, output="sos"), src) * g
                 for f, g in ((550, 1.0), (1500, 0.55), (2600, 0.3)))
    env = np.zeros(n)
    for a, b in spans:
        i, j = int(a * SR), int(b * SR)
        k = np.arange(j - i) / SR
        # a word with a quiet second syllable: the duck must hold its depth under a quiet word too
        syll = np.where(k < WORD / 2, 1.0, 0.35)
        env[i:j] = np.minimum(1.0, np.minimum(k / 0.03, (k[-1] - k) / 0.04)) * syll
    x = voiced * env
    x *= 0.25 / np.abs(x).max()
    sf.write(str(path), np.column_stack([x, x]).astype(np.float32), SR, subtype="FLOAT")


def _bed(path, seconds):
    t = np.arange(int(seconds * SR)) / SR
    x = sum(np.sin(2 * np.pi * f * t + i) * g for i, (f, g) in enumerate(((110.0, 1.0), (164.8, 0.7), (220.0, 0.6),
                                                                          (329.6, 0.4))))
    x *= 0.3 / np.abs(x).max()
    sf.write(str(path), np.column_stack([x, x]).astype(np.float32), SR, subtype="FLOAT")


def _render(chain, vo, bed):
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-v", "error", "-i", str(vo), "-i", str(bed),
                          "-filter_complex", chain, "-f", "f32le", "-ac", "2", "-ar", str(SR), "-"],
                         capture_output=True, check=True, timeout=300).stdout
    return np.frombuffer(out, dtype=np.float32).reshape(-1, 2).astype(np.float64)


@pytest.fixture(scope="module")
def duck(tmp_path_factory):
    """The duck's gain on the bed (dB) per 10 ms block, and the word spans."""
    tmp = tmp_path_factory.mktemp("qf07")
    spans = _words()
    seconds = spans[-1][1] + PAUSE
    vo, bed = tmp / "vo.wav", tmp / "bed.wav"
    _narration(vo, spans, seconds)
    _bed(bed, seconds + 1.0)
    silent_vo, silent_bed = tmp / "vo0.wav", tmp / "bed0.wav"
    sf.write(str(silent_vo), np.zeros((int(seconds * SR), 2), np.float32), SR, subtype="FLOAT")
    sf.write(str(silent_bed), np.zeros((int((seconds + 1.0) * SR), 2), np.float32), SR, subtype="FLOAT")
    chain = _audio_chain(0, 1)
    a, b, c = _render(chain, vo, bed), _render(chain, vo, silent_bed), _render(chain, silent_vo, bed)
    n = min(len(a), len(b), len(c)) // BLOCK * BLOCK
    ducked = np.sum((a[:n] - b[:n]).reshape(-1, BLOCK, 2) ** 2, axis=(1, 2))
    dry = np.sum(c[:n].reshape(-1, BLOCK, 2) ** 2, axis=(1, 2))
    return 10 * np.log10(np.maximum(ducked, 1e-20) / np.maximum(dry, 1e-20)), spans


def _blocks(t0, t1):
    return slice(int(t0 * SR) // BLOCK, -(-int(t1 * SR) // BLOCK))


def test_the_bed_does_not_swell_back_inside_word_gaps(duck):
    g, spans = duck
    recovery = []
    for (a, b), (nxt, _) in pairwise(spans):
        under = np.median(g[_blocks(a, b)])
        recovery.append(max(0.0, float(g[_blocks(b, nxt)].max() - under)))
    assert len(recovery) == len(GAPS)
    assert np.median(recovery) <= 2.0, recovery          # the start commit: 4.7 dB
    assert max(recovery) <= 3.0, recovery


def test_the_duck_holds_its_depth_under_speech_and_returns_in_the_pause(duck):
    g, spans = duck
    under = np.concatenate([g[_blocks(a + 0.05, b)] for a, b in spans])
    assert np.median(under) <= -5.0                       # the bed still ducks under the narration ...
    assert np.percentile(under, 90) - np.percentile(under, 10) <= 3.0   # ... at one depth, quiet syllable or loud
    end = spans[-1][1]
    bridge = g[_blocks(end + end_card.bed_tail(True) - 0.05, end + end_card.bed_tail(True))]
    assert np.all(bridge >= -1.0)                         # back by the end of the Reel's loop bridge (no seam step)
    assert np.all(np.abs(g[_blocks(0.05, LEAD - 0.05)]) <= 0.5)   # and undocked before the first word


def test_the_key_is_band_limited_to_speech():
    chain = _audio_chain(0, 1)
    key = ";".join(p for p in chain.split(";") if p.startswith(("[v_sc]", "[v_key")))
    assert re.search(r"highpass=f=200\b", key) and re.search(r"lowpass=f=4000\b", key)
    assert "[bg_carved][v_key]sidechaincompress=" in chain    # the key, not the voice itself, drives the duck
