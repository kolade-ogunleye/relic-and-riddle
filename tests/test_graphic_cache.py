from src import graphics
from src.lineage import sidecar


def test_generated_graphic_cache_rebuilds_for_changed_copy_or_tampered_output(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(graphics.geo, "locate", lambda topic: None)
    monkeypatch.setattr("src.encoding.get_video_encoder", lambda: ["-c:v", "libx264"])

    def render(spec, path, **kwargs):
        calls.append(spec)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"rendered clip")
        path.with_suffix(".jpg").write_bytes(b"poster")
        return path

    monkeypatch.setattr(graphics, "render_timeline", render)
    payload = {"timeline": [{"year": 100, "label": "First"}]}
    clip = graphics.make_all(payload, {}, tmp_path)[0]
    assert sidecar(clip).is_file()
    assert graphics.make_all(payload, {}, tmp_path) == [clip]
    assert len(calls) == 1
    clip.write_bytes(b"corrupt")
    graphics.make_all(payload, {}, tmp_path)
    assert len(calls) == 2
    payload["timeline"][0]["label"] = "Changed"
    graphics.make_all(payload, {}, tmp_path)
    assert len(calls) == 3
