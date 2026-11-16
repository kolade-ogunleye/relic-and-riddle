"""Writes a run's caption file with the reconstruction badge on screen, from the tree given (base or head).
argv: tree words.json out.ass width height badge_end_s"""
import json
import os
import sys
from pathlib import Path

tree, words_path, out, width, height, end = sys.argv[1:7]
os.chdir(tree)
sys.path.insert(0, tree)
from src.video_composer import VideoComposer, _badge_family

words = json.loads(Path(words_path).read_text(encoding="utf-8"))
VideoComposer().build_subtitles(words, Path(out), width=int(width), height=int(height),
                                reconstruction_intervals=[(0.0, float(end))])
print(json.dumps({"badge_family": _badge_family()}))
