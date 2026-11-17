"""The music library is replaced atomically from a temporary file beside it (CI EXDEV, 2026-11-02)."""

import json
import os
from pathlib import Path

from src import music


def test_the_temporary_file_is_made_beside_the_library(tmp_path, monkeypatch) -> None:
    music_dir, library = tmp_path / "music", tmp_path / "elsewhere" / "library.json"
    monkeypatch.setattr(music, "MUSIC_DIR", music_dir)
    monkeypatch.setattr(music, "LIBRARY", library)
    made_in = []
    real = os.replace

    def spy(src, dst, *a, **k):   # QF-111: the library is staged and replaced by src/host_state.py
        made_in.append(Path(src).parent)
        return real(src, dst, *a, **k)
    monkeypatch.setattr(os, "replace", spy)
    music.save_library([{"file": "a.mp3", "title": "A"}])
    assert made_in == [library.parent]
    assert json.loads(library.read_text(encoding="utf-8")) == [{"file": "a.mp3", "title": "A"}]
    assert list(library.parent.iterdir()) == [library]  # no temporary file left behind
