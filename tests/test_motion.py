from pathlib import Path

import pytest
from PIL import Image

from src import media_budget, motion


class R:
    def __init__(self, data, status=200):
        self._d, self.status_code, self.content, self.text = data, status, b"{}", str(data)

    def json(self):
        return self._d


@pytest.fixture
def photo(tmp_path):
    p = tmp_path / "p.jpg"
    Image.new("RGB", (1600, 1200), (120, 100, 80)).save(p)
    return p


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(motion, "POLL_SECONDS", 0)
    monkeypatch.setattr(motion.time, "sleep", lambda s: None)
    monkeypatch.setattr(motion, "_finish", lambda raw, out: (out.write_bytes(b"clip"), out)[1])


def test_kling_request_and_poll(monkeypatch, photo, tmp_path):
    monkeypatch.setattr("src.config.KLING_API_KEY", "k" * 30)
    sent = {}

    def post(url, headers, json, timeout):
        sent.update(url=url, body=json, auth=headers["Authorization"])
        return R({"code": 0, "data": {"id": "t1"}})
    polls = iter([R({"data": [{"status": "processing"}]}),
                  R({"data": [{"status": "succeeded", "outputs": [{"type": "video", "url": "https://p2-kling.klingai.com/v.mp4"}]}]})])
    monkeypatch.setattr(motion.requests, "post", post)
    monkeypatch.setattr(motion.requests, "get", lambda *a, **k: next(polls))
    monkeypatch.setattr(motion, "download", lambda url, prov, dest, extra_headers=None: dest)
    motion.animate_kling(photo, tmp_path / "k.mp4")
    assert sent["url"] == "https://api-singapore.klingai.com/image-to-video/kling-3.0" and sent["auth"].startswith("Bearer ")
    assert sent["body"]["settings"] == {"resolution": "1080p", "duration": 6, "audio": "off", "multi_shot": False}
    assert sent["body"]["options"]["watermark_info"]["enabled"] is False


def test_chain_falls_through_and_respects_budget(monkeypatch, db, photo, tmp_path):
    monkeypatch.setattr("src.config.MEDIA_MONTHLY_BUDGET_USD", 2.0)
    calls = []

    def veo(img, out, on_billed=None):
        calls.append("veo")
        raise motion.MotionError("filtered")

    def kling(img, out, on_billed=None):
        calls.append("kling")
        return out
    monkeypatch.setattr(motion, "providers", lambda: [("veo", lambda: True, veo, 0.96), ("kling", lambda: True, kling, 0.84)])
    assert motion.animate(db, photo, tmp_path / "a.mp4")[1] == "kling" and calls == ["veo", "kling"]
    assert round(media_budget.spent(db), 2) == 0.84  # only the successful call is charged
    monkeypatch.setattr("src.config.MEDIA_MONTHLY_BUDGET_USD", 1.0)
    calls.clear()
    assert motion.animate(db, photo, tmp_path / "b.mp4") is None and calls == []  # over budget: nothing called


def test_apply_is_off_without_budget_and_skips_videos(monkeypatch, db, photo, tmp_path):
    clip = tmp_path / "graphic_map.mp4"
    clip.write_bytes(b"0")
    assert motion.apply(db, [photo, clip], tmp_path / "m") == ([photo, clip], [])  # budget 0 by default
    monkeypatch.setattr("src.config.MEDIA_MONTHLY_BUDGET_USD", 5.0)
    monkeypatch.setattr("src.config.MOTION_MAX_CLIPS", 2)
    paid = []
    monkeypatch.setattr(motion, "animate", lambda db, img, out: paid.append(img) or (out, "veo"))
    memo = {}
    scenes, used = motion.apply(db, [clip, photo], tmp_path / "m", memo=memo)
    assert scenes[0] == clip and scenes[1].name.startswith("motion_") and used == ["veo"]
    motion.apply(db, [clip, photo], tmp_path / "m", memo=memo)  # a re-plan with the same photo
    assert len(paid) == 1  # reused, not paid twice
