"""The Reel programme ends on its own content, to the sample, whatever order ffmpeg feeds it (CI, 2026-11-04).

On a loaded runner the delivered master's last 20 ms were exact digital zeros (test_loop_seam_and_captions_2026_11_03
::test_the_delivered_master_loops_without_a_level_step_or_a_click: -180 dB against -37 dB at the start). The cause:
the bed reaches the mix through sidechaincompress, which ends its output on the first EOF it sees from either input
and drops what the other input still had queued, and amix=duration=first then ran on to the cut with the voice's
silent pad alone. How much was dropped depends on the frame sizes queued at each input when the EOF arrives, that is
on how ffmpeg's scheduler happened to feed the inputs. The same perturbation also cut the programme short of the cut
(amix drops its first input's queue on that input's EOF), so the fade never ran and the loop restarted on a click.

The tests force the condition by re-framing each branch of the composer's programme chain (asetnsamples), which is
what a loaded scheduler does to the frames queued at those filters, and require the same programme every time.
"""
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from src import end_card

SR = 48000
VO_DUR = 3.0
TOTAL = VO_DUR + end_card.LOOP_SECONDS
N = round(TOTAL * SR)
EDGE = 0.05
# (bed branch, sidechain branch, voice branch) frame sizes; 0 leaves the branch as ffmpeg frames it. Before the fix
# (ffmpeg 6.1.1), rows 2 to 4 ended on 363 to 3433 trailing zero samples (the CI defect) and rows 5 to 8 came out
# 2944 to 15232 samples short of the cut; rows 1, 9 and 10 happened to come out whole.
REFRAMINGS = [(0, 0, 0), (0, 0, 1024), (0, 0, 8192), (1024, 0, 8192), (0, 1024, 0), (0, 4096, 0), (0, 20000, 0),
              (256, 8192, 0), (4096, 2048, 1024), (20000, 256, 8192)]

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg is required to run the programme chain")


@pytest.fixture(scope="module")
def sources(tmp_path_factory):
    """A voice that opens and closes on silence, as narration does, and the Reel's bed, made the way main.py makes
    it (generate_ambient_bed with end_card_at): it runs end_card.bed_tail past the narration."""
    import soundfile as sf

    from src.audio_engine import AudioEngine
    tmp = tmp_path_factory.mktemp("seam_sources")
    t = np.arange(int(SR * VO_DUR)) / SR
    env = np.clip((t - 0.12) / 0.02, 0.0, 1.0) * np.clip((2.8 - t) / 0.02, 0.0, 1.0)
    voice = tmp / "voiceover.wav"
    sf.write(voice, 0.3 * np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t)) * env, SR)
    music = tmp / "music.wav"
    tm = np.arange(int(SR * 10)) / SR
    sf.write(music, np.column_stack([0.2 * np.sin(2 * np.pi * 110 * tm)] * 2), SR)
    bed = AudioEngine().generate_ambient_bed(VO_DUR + end_card.bed_tail(True), tmp / "ambient_bed.wav",
                                             music_path=music, end_card_at=VO_DUR)
    return voice, bed, tmp


def _reframed(chain, bed_n, sc_n, main_n):
    """The chain with its three branches re-framed into n-sample frames (0: as ffmpeg frames them)."""
    def frames(n):
        return f"asetnsamples=n={n}:p=0" if n else "anull"
    assert chain.count("[bg_carved];") == 1 and chain.count("asplit[v_main][v_sc];") == 1
    chain = chain.replace("[bg_carved];", f",{frames(bed_n)}[bg_carved];")
    return chain.replace("asplit[v_main][v_sc];", f"asplit[v_main0][v_sc0];[v_sc0]{frames(sc_n)}[v_sc];"
                                                  f"[v_main0]{frames(main_n)}[v_main];")


def _programme(sources, reframing):
    """The Reel's programme as compose_video builds it (card level +2.6 dB, gain +3 dB), written as the first pass
    writes it: pcm_s24le, cut with -t at narration + LOOP_SECONDS."""
    import soundfile as sf

    from src.audio_engine import AudioEngine
    from src.video_composer import _audio_chain, _master_chain, _seam_tail
    voice, bed, tmp = sources
    chain = _audio_chain(0, 1, end_card.LOOP_SECONDS, VO_DUR, 2.6, end=TOTAL) + _master_chain(3.0)
    chain = _reframed(chain, *reframing) + _seam_tail(TOTAL, AudioEngine.LOOP_BRIDGE_EDGE) + "[a]"
    out = tmp / f"programme_{reframing[0]}_{reframing[1]}_{reframing[2]}.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(voice), "-i", str(bed), "-filter_complex", chain,
                    "-map", "[a]", "-c:a", "pcm_s24le", "-ar", str(SR), "-ac", "2", "-t", f"{TOTAL:.3f}", str(out)],
                   check=True, capture_output=True)
    audio, rate = sf.read(out, always_2d=True)
    assert rate == SR
    return audio


def _db(x):
    return 20 * np.log10(max(float(np.sqrt(np.mean(np.square(x)))), 1e-9))


@pytest.mark.parametrize("reframing", REFRAMINGS, ids=lambda r: f"bed{r[0]}-sc{r[1]}-voice{r[2]}")
def test_the_programme_runs_on_its_own_content_to_the_exact_cut_however_its_inputs_are_framed(sources, reframing):
    audio = _programme(sources, reframing)
    assert len(audio) == N                                    # exactly narration + LOOP_SECONDS, never short
    live = np.nonzero(np.abs(audio).max(axis=1) > 0)[0]
    assert live[-1] == N - 1                                  # no trailing digital silence: the bed runs to the cut
    n20 = int(0.020 * SR)
    last, first = audio[-n20:], audio[:n20]
    assert _db(last) > -60                                    # the CI failure: -180 dB here
    assert abs(_db(last) - _db(first)) <= 3.0, (_db(last), _db(first))
    # the seam's fade lies inside the cut: the last 5 ms well under the bridge's held level
    held = audio[int((VO_DUR + 0.4) * SR):int((VO_DUR + 0.6) * SR)]
    assert _db(audio[-int(0.005 * SR):]) < _db(held) - 12


def test_reframing_does_not_change_the_programme_before_its_tail(sources):
    """The fix makes the tail deterministic; what precedes it is the same mix, frame sizes aside (the card's
    level ramp is evaluated per frame, so a few hundredths of a dB of difference are expected there)."""
    base = _programme(sources, REFRAMINGS[0])
    for reframing in REFRAMINGS[1:3]:
        other = _programme(sources, reframing)
        assert abs(_db(other[:int(VO_DUR * SR)]) - _db(base[:int(VO_DUR * SR)])) < 0.1
        assert abs(_db(other[-int(0.2 * SR):]) - _db(base[-int(0.2 * SR):])) < 1.0


def test_the_chain_cuts_by_sample_count_with_no_eof_inside_the_mix():
    from src.video_composer import _audio_chain, _seam_tail
    chain = _audio_chain(0, 1, end_card.LOOP_SECONDS, VO_DUR, 2.6, end=TOTAL)
    assert "pad_dur" not in chain                     # the voice's pad has no end of its own ...
    assert "-3.0,apad[bg_carved]" in chain            # ... nor the bed's: neither reaches sidechaincompress or amix
    assert f"normalize=0,atrim=end_sample={N}," in chain   # the mix is cut by sample count, right after amix
    tail = _seam_tail(TOTAL, EDGE)
    fade = round(EDGE * SR)
    assert tail == (f",asetpts=N/SR/TB,afade=t=out:ss={N - fade}:ns={fade}"
                    f",apad=whole_len={N},atrim=end_sample={N}")
    # no end: the 16:9 and measurement chains are unchanged
    assert "atrim" not in _audio_chain(0, 1, 0.0) and "apad" not in _audio_chain(0, 1, 0.0)
    assert f"apad=pad_dur={end_card.LOOP_SECONDS:.3f}," in _audio_chain(0, 1, end_card.LOOP_SECONDS)


def test_the_reel_master_and_the_card_level_use_the_exact_cut():
    src = (Path(__file__).resolve().parent.parent / "src" / "video_composer.py").read_text(encoding="utf-8")
    assert "end=vo_dur + card_seconds if card else None" in src
    assert "seam_fade = _seam_tail(vo_dur + card_seconds, edge)" in src
    assert "_audio_chain(0, 1, pad, end=card_at + pad if pad > 0 else None)" in src
