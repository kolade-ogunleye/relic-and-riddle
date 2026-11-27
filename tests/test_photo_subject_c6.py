"""Derived subject boxes as data (design-freeze cycle 6, SUBJECT stream): the record beside the photograph, bound to
its bytes and to the method's version; a reviewed box first; a derived box stands for a subject only at or over
photo_subject.ACCEPT, else the photo system behaves exactly as without one."""
import hashlib
import json

from PIL import Image

from src import photo_fitness as pf
from src import photo_subject as ps


def _photo(tmp_path, colour=(120, 90, 60)):
    p = tmp_path / "01_commons.jpg"
    im = Image.new("RGB", (600, 400), colour)
    im.paste((240, 240, 240), (200, 120, 400, 300))
    im.save(p, quality=95)
    return p


def _rec(p, box=(0.3, 0.3, 0.7, 0.75), confidence=0.9, version=ps.VERSION):
    return {"version": version, "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "box": list(box),
            "confidence": confidence, "method": "test", "models": [], "measures": {}, "reviewed": False}


def test_record_is_bound_to_the_bytes_and_the_version(tmp_path):
    p = _photo(tmp_path)
    ps.write(p, _rec(p))
    assert ps.read(p)["box"] == [0.3, 0.3, 0.7, 0.75]
    assert ps.sidecar(p).name == "01_commons.jpg.subject.json"
    ps.write(p, _rec(p, version=ps.VERSION + 1))            # another method's record is not this one
    assert ps.read(p) is None
    ps.write(p, _rec(p))
    p.write_bytes(p.read_bytes() + b"\0")                   # changed bytes: the box no longer belongs to them
    assert ps.read(p) is None


def test_no_models_no_record(tmp_path, monkeypatch):
    p = _photo(tmp_path)
    monkeypatch.setattr(ps, "MODEL_DIR", tmp_path / "absent")
    assert ps.record(p) is None
    assert not ps.sidecar(p).exists()


def test_under_accept_nothing_is_consumed(tmp_path, monkeypatch):
    p = _photo(tmp_path)
    ps.write(p, _rec(p, confidence=0.99))
    monkeypatch.setattr(ps, "ACCEPT", 1.01)                # the shipped threshold: an instrument only
    assert ps.subject_focus(p) is None
    pf._cache.clear()
    assert pf.analysis_of(p).focus is None                  # the photo system sees no subject: today's behaviour


def test_at_accept_the_derived_box_is_the_subject(tmp_path, monkeypatch):
    p = _photo(tmp_path)
    ps.write(p, _rec(p, confidence=0.99))
    monkeypatch.setattr(ps, "ACCEPT", 0.5)
    got = ps.subject_focus(p)
    assert got["source"] == "derived" and got["focus_box"] == [0.3, 0.3, 0.7, 0.75] and got["reviewed"] == ""
    pf._cache.clear()
    a = pf.analysis_of(p)
    assert a.focus is not None and abs(a.focus[0] - 180) < 1 and abs(a.focus[3] - 300) < 1


def test_a_reviewed_box_takes_precedence(tmp_path, monkeypatch):
    p = _photo(tmp_path)
    ps.write(p, _rec(p, confidence=0.99))
    monkeypatch.setattr(ps, "ACCEPT", 0.0)
    reviewed = {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "focus_box": [0.1, 0.1, 0.5, 0.5],
                "reviewed": "test review"}
    got = ps.subject_focus(p, kept=reviewed)
    assert got["reviewed"] == "test review" and got["focus_box"] == [0.1, 0.1, 0.5, 0.5] and "source" not in got


def test_write_is_whole_json(tmp_path):
    p = _photo(tmp_path)
    ps.write(p, _rec(p))
    assert json.loads(ps.sidecar(p).read_text(encoding="utf-8"))["reviewed"] is False
    assert not list(tmp_path.glob("*.tmp"))
