"""QF-05 (2026-11-05): the music bed's transition whooshes. One fired at every pause of 0.2 s or more, or sentence end,
3.5 s apart, wherever the picture was: 5-7 in a Reel and about 30 in a 16:9 programme without act cues. Now a whoosh
lands only on a scene cut (the one nearest an act transition or a planned reveal), at most two in a Reel, 6 dB under
the level it had. Real narration: the Gol Gumbaz Reel's own word timings and sound plan (the freeze-panel fixtures).
"""
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

from src import end_card
from src.audio_engine import AudioEngine
from src.scene_planner import documentary_cuts, segment_beats
from src.video_composer import caption_cut_times

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docs" / "reviews" / "freeze-panel-2026-11-04" / "fixtures"))
import fixtures as _fx

RUN = Path("output") / "run_gol_gumbaz_whispering_gallery_1793278143"   # its committed copy (fixtures/runs)
VO_DUR = 44.76   # the run's voiceover.mp3 (ffprobe)
PLAN = {"moments": [{"t": 5.089, "kind": "tension", "effect": None}, {"t": 15.804, "kind": "reveal", "effect": None},
                    {"t": 33.571, "kind": "calm", "effect": None}, {"t": 42.959, "kind": "reveal", "effect": None}]}
SR = 48000


def _words():
    return json.loads(_fx.run_file(RUN, "words.json").read_text(encoding="utf-8"))


class _Silent(AudioEngine):
    """The same bed with the whoosh recording silenced: the difference is the whoosh layer exactly."""

    def _effect(self, name, synth, sr, loop_to=None):
        out = super()._effect(name, synth, sr, loop_to)
        return out * 0.0 if name == "sfx_whoosh.wav" else out


def _whoosh_layer(tmp_path, seconds, **kw):
    a = AudioEngine().generate_ambient_bed(seconds, tmp_path / "a.wav", **kw)
    b = _Silent().generate_ambient_bed(seconds, tmp_path / "b.wav", **kw)
    x, _ = sf.read(str(a), dtype="float64")
    y, _ = sf.read(str(b), dtype="float64")
    layer = x - y
    live = np.flatnonzero(np.abs(layer).max(axis=1) > 1e-6)
    if not live.size:
        return layer, []
    bursts = np.split(live, np.flatnonzero(np.diff(live) > int(0.1 * SR)) + 1)
    return layer, [round(int(s[0]) / SR + 0.15, 3) for s in bursts]   # the burst starts 0.15 s before its time


def test_no_whoosh_at_a_pause_where_the_picture_does_not_cut(tmp_path):
    # No scene cuts known, no act cues, no sound plan: the old pause rule put 5-7 whooshes on this narration.
    _, times = _whoosh_layer(tmp_path, VO_DUR + 0.5, words=_words())
    assert times == []


def test_a_reel_bed_whooshes_at_most_twice_only_on_its_scene_cuts_6_db_under(tmp_path):
    words = _words()
    cuts = caption_cut_times([b["end"] - b["start"] for b in segment_beats(words, VO_DUR)])   # as main.py passes them
    layer, times = _whoosh_layer(tmp_path, VO_DUR + end_card.bed_tail(True), words=words, sound_plan=PLAN,
                                 end_card_at=VO_DUR, scene_cuts=cuts)
    assert 1 <= len(times) <= AudioEngine.TRANSITION_WHOOSH_MAX == 2
    assert all(any(abs(t - c) <= 0.002 for c in cuts) for t in times), (times, cuts)
    assert times == [15.804]   # the first reveal's cut; the second reveal (42.96 s) is within 0.5 s of the bridge
    whoosh = AudioEngine()._effect("sfx_whoosh.wav", AudioEngine._synth_transition_whoosh(0.45, SR), SR)
    i0 = int((times[0] - 0.15) * SR)
    part = layer[i0:i0 + len(whoosh)]
    level = 20 * np.log10(np.sqrt(np.mean(part ** 2)) / np.sqrt(np.mean(whoosh ** 2)))
    assert abs(level - (-6.0)) < 0.1, level   # the recording as it was mixed before, 6 dB down


def test_whoosh_times_snap_to_cuts_prefer_act_transitions_and_are_capped():
    words = _words()
    cuts = caption_cut_times([b["end"] - b["start"] for b in segment_beats(words, VO_DUR)])
    many = {"moments": [{"t": c + 0.3, "kind": "reveal"} for c in cuts]}   # a reveal next to every cut
    times = AudioEngine.transition_whoosh_times(VO_DUR, scene_cuts=cuts, sound_plan=many, words=words)
    assert len(times) == 2 and set(times) <= {round(c, 3) for c in cuts}
    # A reveal with no cut within TRANSITION_WHOOSH_SNAP gets no whoosh; with the cuts unknown it lands on the reveal.
    far = {"moments": [{"t": 13.4, "kind": "reveal"}]}   # cuts at 10.94 and 15.8
    assert AudioEngine.transition_whoosh_times(VO_DUR, scene_cuts=cuts, sound_plan=far) == []
    assert AudioEngine.transition_whoosh_times(VO_DUR, sound_plan=far) == [13.4]
    # A documentary: one whoosh per act transition, each on the documentary's cut nearest it.
    doc_cuts = caption_cut_times([c["end"] - c["start"] for c in documentary_cuts(words, VO_DUR)])
    acts = [doc_cuts[0] + 0.2, doc_cuts[1] - 0.4, doc_cuts[-1] + 0.1]
    times = AudioEngine.transition_whoosh_times(VO_DUR + 0.5, scene_cuts=doc_cuts, sound_plan=PLAN, act_cues=acts)
    assert len(doc_cuts) >= 3 and times == [round(doc_cuts[i], 3) for i in (0, 1, len(doc_cuts) - 1)]   # no reveal on top


def test_picture_holds_remix_the_saved_bed_before_render_without_new_music(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from main import PipelineCoordinator
    from src import audio_provenance, music

    words = _words()
    durations = [b["end"] - b["start"] for b in segment_beats(words, VO_DUR)]
    # Hold the first two pictures across their old boundary, then repair another hold.
    held = [sum(durations[:2]), *durations[2:]]
    repaired = [sum(durations[:3]), *durations[3:]]
    selected = {"path": None, "source": "none", "mood": "fixture", "credit": ""}
    (tmp_path / "music_selection.json").write_text(json.dumps(selected), encoding="utf-8")
    (tmp_path / "sound_plan.json").write_text(json.dumps(PLAN), encoding="utf-8")
    bed_calls, records, keys = [], [], []

    def generate(seconds, output, **kwargs):
        bed_calls.append((seconds, kwargs))
        output.write_bytes(b"fixture bed")

    def render(*args):
        bed_for_cuts = args[-1]
        assert callable(bed_for_cuts)
        bed_for_cuts(durations)  # unchanged plan needs no remix
        assert not bed_calls
        bed_for_cuts(held)
        bed_for_cuts(held)  # repeat render reuses that mix
        assert len(bed_calls) == 1
        bed_for_cuts(repaired)
        return "rendered"

    monkeypatch.setattr(music, "load_library", list)
    monkeypatch.setattr(audio_provenance, "audio_source_inputs", lambda path: {})
    monkeypatch.setattr(audio_provenance, "record_bed", lambda *a, **kw: records.append((a, kw)))
    coord = object.__new__(PipelineCoordinator)
    coord._check_cancel = lambda run_id: None
    coord._seed_bed_from_previous = lambda *a: None
    coord._render_approve_publish = render
    coord.audio_engine = SimpleNamespace(generate_ambient_bed=generate, last_sfx_sources=[])
    selections = []   # QF-84: the reused bed's tracks are recorded for the run (the drone: none)
    coord.db = SimpleNamespace(record_music_selection=lambda run_id, rows: selections.append((run_id, rows)))
    checkpoints = SimpleNamespace(reusable=lambda *a: True,
                                  complete=lambda stage, key, outputs: keys.append(dict(key)))
    result = coord._produce_bed_and_render(
        {"id": "fixture"}, "fixture", tmp_path, {"caption": "fixture"}, [], None,
        tmp_path / "voice.wav", tmp_path / "words.json", VO_DUR, words,
        checkpoints, lambda paths: {}, False, True, None, None)
    assert result == "rendered"
    assert selections == [("fixture", [])]
    assert len(bed_calls) == len(records) == len(keys) == 2
    for (_, kw), key, final_durations in zip(bed_calls, keys, [held, repaired]):
        assert kw["scene_cuts"] == key["scene_cuts"] == caption_cut_times(final_durations)
        assert kw["sound_plan"] == PLAN
        assert kw["music_path"] is None
        assert kw["end_card_at"] == VO_DUR
