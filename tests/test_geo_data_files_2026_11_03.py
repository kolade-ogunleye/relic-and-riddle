"""Map data files (2026-11-03): tools/ is gitignored, so production (deployed from GitHub) had no shaded relief and
nothing said so. A run now warns at start for each missing geo file with the command that fetches it, the relief
falls back to the 1:50m raster when only it is present, and scripts/fetch_relief.py fetches and checks the raster.
"""
import ast
import io
import logging
import sys
import zipfile
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main  # noqa: E402
from scripts import fetch_relief  # noqa: E402
from src import geo  # noqa: E402


@pytest.fixture
def geo_paths(tmp_path, monkeypatch):
    countries = tmp_path / "geo" / "ne_50m_admin_0_countries.geojson"
    hr, m50 = tmp_path / "geo" / "relief" / "SR_HR.tif", tmp_path / "geo" / "relief" / "SR_50M.tif"
    monkeypatch.setattr(geo, "COUNTRIES_FILE", countries)
    monkeypatch.setattr(geo, "RELIEF_HR_FILE", hr)
    monkeypatch.setattr(geo, "RELIEF_50M_FILE", m50)
    monkeypatch.setattr(geo, "RELIEF_FILE", hr)
    return countries, hr, m50


def _touch(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


def test_both_missing_geo_files_are_warned_about_with_their_fetch_commands(geo_paths, caplog):
    countries, hr, _ = geo_paths
    with caplog.at_level(logging.WARNING, logger="pipeline"):
        lines = main.warn_missing_geo_files()
    warned = [r for r in caplog.records if r.levelno == logging.WARNING and r.name == "pipeline"]
    assert len(lines) == len(warned) == 2
    text = "\n".join(r.getMessage() for r in warned)
    assert countries.name in text and "python scripts/fetch_geodata.py" in text
    assert hr.name in text and "python scripts/fetch_relief.py" in text
    assert "render flat" in text and "no map graphics" in text


def test_only_the_missing_file_is_warned_about(geo_paths, caplog):
    countries, hr, _ = geo_paths
    _touch(countries)
    with caplog.at_level(logging.WARNING, logger="pipeline"):
        lines = main.warn_missing_geo_files()
    assert len(lines) == 1 and "SR_HR.tif" in lines[0] and "fetch_relief.py" in lines[0]
    assert "fetch_geodata.py" not in lines[0]


def test_nothing_is_warned_when_both_files_are_present(geo_paths, caplog):
    countries, hr, _ = geo_paths
    _touch(countries), _touch(hr)
    with caplog.at_level(logging.WARNING, logger="pipeline"):
        assert main.warn_missing_geo_files() == []
    assert not [r for r in caplog.records if r.name == "pipeline"]


def test_the_medium_relief_counts_as_present_and_is_the_fallback(geo_paths):
    countries, hr, m50 = geo_paths
    _touch(countries), _touch(m50)
    assert geo.missing_files() == []
    assert geo._relief_file() == m50
    _touch(hr)
    assert geo._relief_file() == hr          # the high-res raster wins when both are there


def test_the_warning_runs_at_run_start_before_the_health_gate():
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    run_cycle = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "run_cycle")
    src = ast.get_source_segment((ROOT / "main.py").read_text(encoding="utf-8"), run_cycle)
    assert "warn_missing_geo_files()" in src
    assert src.index("warn_missing_geo_files()") < src.index('if not health["all_passed"]:')


def _zip_with(tmp_path: Path, members: dict) -> Path:
    z = tmp_path / "relief.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return z


def _tif_bytes(size, gradient=True) -> bytes:
    im = Image.linear_gradient("L").resize(size) if gradient else Image.new("L", size, 128)
    buf = io.BytesIO()
    im.save(buf, "TIFF")
    return buf.getvalue()


def test_fetch_relief_extracts_only_the_named_tif_and_checks_it(tmp_path):
    z = _zip_with(tmp_path, {"SR_HR/SR_HR.tif": _tif_bytes((40, 20)), "SR_HR/README.html": b"<p>x</p>"})
    out = tmp_path / "out.tif"
    assert fetch_relief.extract_member(z, "SR_HR.tif", out)
    assert fetch_relief.valid_relief(out, (40, 20))
    assert not fetch_relief.valid_relief(out, (21600, 10800))           # wrong dimensions


def test_fetch_relief_rejects_a_blank_raster_a_missing_member_and_an_oversized_one(tmp_path):
    flat = tmp_path / "flat.tif"
    flat.write_bytes(_tif_bytes((40, 20), gradient=False))
    assert not fetch_relief.valid_relief(flat, (40, 20))
    z = _zip_with(tmp_path, {"other.tif": _tif_bytes((40, 20))})
    assert not fetch_relief.extract_member(z, "SR_HR.tif", tmp_path / "a.tif")
    z = _zip_with(tmp_path, {"SR_HR.tif": _tif_bytes((40, 20))})
    assert not fetch_relief.extract_member(z, "SR_HR.tif", tmp_path / "b.tif", max_bytes=10)
    assert not (tmp_path / "b.tif").exists()


class _Resp:
    def __init__(self, chunks, length=None):
        self.chunks, self.headers = chunks, ({"Content-Length": str(length)} if length else {})

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, n):
        yield from self.chunks


def test_fetch_relief_download_stops_at_the_size_cap(tmp_path, monkeypatch):
    dest = tmp_path / "d.zip"
    monkeypatch.setattr(fetch_relief.requests, "get", lambda *a, **k: _Resp([b"a" * 6, b"b" * 6]))
    assert not fetch_relief.download("https://example.invalid/x.zip", dest, max_bytes=10)
    assert not dest.exists()
    monkeypatch.setattr(fetch_relief.requests, "get", lambda *a, **k: _Resp([b"a"], length=11))
    assert not fetch_relief.download("https://example.invalid/x.zip", dest, max_bytes=10)
    monkeypatch.setattr(fetch_relief.requests, "get", lambda *a, **k: _Resp([b"a" * 4, b"b" * 4]))
    assert fetch_relief.download("https://example.invalid/x.zip", dest, max_bytes=10)
    assert dest.read_bytes() == b"aaaabbbb"


def test_fetch_relief_saves_a_checked_raster_and_leaves_no_partial_files(tmp_path, monkeypatch):
    payload = _zip_with(tmp_path, {"SR_50M.tif": _tif_bytes((40, 20))}).read_bytes()
    dest = tmp_path / "relief" / "SR_50M.tif"
    monkeypatch.setitem(fetch_relief.VARIANTS, "medium", {**fetch_relief.VARIANTS["medium"], "size": (40, 20),
                                                          "dest": dest})
    monkeypatch.setattr(fetch_relief.requests, "get", lambda *a, **k: _Resp([payload]))
    assert fetch_relief.main(["--medium"]) == 0
    assert Image.open(dest).size == (40, 20)
    assert sorted(p.name for p in dest.parent.iterdir()) == ["SR_50M.tif"]
