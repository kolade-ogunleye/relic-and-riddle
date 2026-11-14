"""Render generated beat-cut fixtures through the target checkout's composer; no services or ignored inputs.

Usage: python measure.py <repo root>
"""
import importlib.util
import inspect
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent


def main(root):
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location('cut_fixture', HERE.parents[3] / 'tests/test_qf04_beat_cuts.py')
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    results = {}
    for name, implicit, acts in [('explicit_beats', False, None), ('equal_beats', True, None),
                                 ('act_boundary', False, [2.0])]:
        with tempfile.TemporaryDirectory(prefix='qf04_') as temp, pytest.MonkeyPatch.context() as patches:
            supported = 'act_cuts' in inspect.signature(fixture.vc.VideoComposer.compose_video).parameters
            out, graph = fixture.render_cuts(Path(temp), patches, implicit=implicit,
                                            acts=acts if supported else None)
            raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(out), '-vf', 'crop=8:8',
                                  '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'],
                                 capture_output=True, check=True).stdout
            frames = np.frombuffer(raw, np.uint8).reshape(-1, 8, 8, 3).mean(axis=(1, 2))
            jumps = np.max(np.abs(np.diff(frames, axis=0)), axis=1)
            safe_graph = graph.replace(str(Path(temp)).replace('\\', '/').replace(':', '\\:'), '<fixture>')
            safe_graph = safe_graph.replace(str(root).replace('\\', '/').replace(':', '\\:'), '<repo>')
            safe_graph = re.sub(r"subtitles='[^']*'", "subtitles='<fixture>/master_captions.ass'", safe_graph)
            results[name] = {'frames': len(frames), 'hard_cut_frames': (np.flatnonzero(jumps > 50) + 1).tolist(),
                             'dissolves': graph.count('xfade=transition=dissolve'),
                             'xfades': graph.count('xfade='),
                             'forbidden': 'transition=fadeblack' in graph or 'transition=zoomin' in graph,
                             'graph': safe_graph}
    results['acceptance'] = (
        all(results[k]['hard_cut_frames'] == [30, 60, 90, 120] and results[k]['xfades'] == 0
            for k in ['explicit_beats', 'equal_beats'])
        and results['act_boundary']['dissolves'] == results['act_boundary']['xfades'] == 1
        and all(not results[k]['forbidden'] and results[k]['frames'] == 150
                for k in ['explicit_beats', 'equal_beats', 'act_boundary']))
    return results


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('usage: python measure.py <repo root>')
    print(json.dumps(main(Path(sys.argv[1]).resolve()), indent=2))
