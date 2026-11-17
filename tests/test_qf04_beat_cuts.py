"""QF-04: actual delivered cuts stay on beats; only act changes dissolve."""
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from src import video_composer as vc


def render_cuts(tmp_path, monkeypatch, *, acts=None, implicit=False, wide=False):
    voice = tmp_path / 'voice.wav'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    'sine=frequency=220:sample_rate=48000', '-t', '5.5', str(voice)], check=True)
    scenes = []
    for i, colour in enumerate(('red', 'blue', 'lime', 'yellow', 'purple')):
        path = tmp_path / f'scene{i}.png'
        Image.new('RGB', (64, 64), colour).save(path)
        scenes.append(path)
    monkeypatch.setattr(vc, '_get_video_encoder', lambda: ['-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '18'])
    graphs = []
    real = vc._run_ffmpeg

    def capture(cmd, *args, **kwargs):
        if '-filter_complex' in cmd:
            graphs.append(cmd[cmd.index('-filter_complex') + 1])
        return real(cmd, *args, **kwargs)

    monkeypatch.setattr(vc, '_run_ffmpeg', capture)
    out = tmp_path / 'master.mp4'
    kwargs = {'act_cuts': acts} if acts is not None else {}
    if wide:
        kwargs.update(width=1920, height=1080)
    vc.VideoComposer().compose_video(scenes, voice, voice, [], 5.0, out,
                                     durations=None if implicit else [1.0] * 5, **kwargs)
    graph = next(g for g in graphs if '[v_final]' in g)
    return out, graph


@pytest.mark.parametrize('implicit', [False, True])
def test_hard_cuts_land_on_every_beat_without_transition_overlap(tmp_path, monkeypatch, implicit):
    out, graph = render_cuts(tmp_path, monkeypatch, implicit=implicit)
    assert 'xfade=' not in graph, graph
    assert graph.count('concat=n=2:v=1:a=0') == 4
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(out), '-vf', 'crop=8:8',
                          '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'], capture_output=True, check=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, 8, 8, 3).mean(axis=(1, 2))
    jumps = np.max(np.abs(np.diff(frames, axis=0)), axis=1)
    assert (np.flatnonzero(jumps > 50) + 1).tolist() == [30, 60, 90, 120]
    assert len(frames) == 150


@pytest.mark.parametrize('wide', [False, True])
def test_only_an_explicit_act_change_has_an_eight_frame_dissolve(tmp_path, monkeypatch, wide):
    out, graph = render_cuts(tmp_path, monkeypatch, acts=[2.0], wide=wide)
    assert graph.count('xfade=') == 1
    assert 'xfade=transition=dissolve:duration=0.266666667:offset=1.866666667' in graph
    assert graph.count('concat=n=2:v=1:a=0') == 3
    assert 'fadeblack' not in graph and 'transition=zoomin' not in graph
    seconds = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                              '-show_entries', 'stream=duration', '-of', 'csv=p=0', str(out)],
                             capture_output=True, text=True, check=True).stdout
    assert float(seconds) == pytest.approx(5.0, abs=1 / 30)


def test_documentary_render_forwards_act_boundaries_and_keys_its_cache(tmp_path):
    from main import PipelineCoordinator

    class ReachedRender(Exception):
        pass

    calls = []

    def render(**kwargs):
        calls.append(kwargs)
        raise ReachedRender

    inputs = []

    def reusable(stage, key):
        inputs.append(key)
        return False

    coordinator = object.__new__(PipelineCoordinator)
    coordinator.video_composer = SimpleNamespace(compose_deep_dive=render)
    with pytest.raises(ReachedRender):
        coordinator._render_and_certify_deep_dive(
            topic={}, tid='fixture', run_id='fixture', run_dir=tmp_path,
            parent_run_id=None, payload={}, words=[], vo_dur=5.0,
            vo_path=tmp_path / 'voice.wav', bed_path=tmp_path / 'bed.wav',
            words_path=tmp_path / 'words.json', extended_scenes=[], durations=[1.0] * 5,
            cuts=[], master_mp4=tmp_path / 'master.mp4',
            checkpoints=SimpleNamespace(reusable=reusable), file_inputs=lambda paths: {},
            cached_artifacts=lambda *args: None, fut_thumbs=None,
            auto_approve=False, dry_run=True, act_cuts=[2.0])
    assert calls[0]['act_cuts'] == inputs[0]['act_cuts'] == [2.0]
