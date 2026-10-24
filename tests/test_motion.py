
import pytest
from PIL import Image

from src import media_budget, motion

_REAL_FINISH = motion._finish  # the autouse fixture below stubs it for the API tests


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


def test_rejected_clip_is_kept_with_its_measurements(monkeypatch, tmp_path):
    # A rejected clip was paid for; keeping it and the geometry numbers is what lets the threshold
    # be calibrated from real cases instead of guessed.
    import json
    import subprocess
    raw = tmp_path / "raw.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x568:rate=30:duration=6.5",
                    "-pix_fmt", "yuv420p", str(raw)], check=True)
    qa = {"pass": False, "reason": "Inlier ratio below threshold (0.19 < 0.25)", "inlier_ratio": 0.19, "inliers": 40}
    monkeypatch.setattr(motion, "verify_motion_hook_integrity", lambda f0, f1, archetype: qa)
    out = tmp_path / "motion_01.mp4"
    with pytest.raises(motion.MotionError):
        _REAL_FINISH(raw, out)
    kept = tmp_path / "rejected"
    assert not out.exists() and (kept / "motion_01.mp4").is_file()
    assert json.loads((kept / "motion_01.qa.json").read_text())["inlier_ratio"] == 0.19
    assert (kept / "motion_01_verify_f0.jpg").is_file() and (kept / "motion_01_verify_fend.jpg").is_file()


def test_content_rewrite_is_caught_where_moving_light_is_not():
    import numpy as np
    rng = np.random.default_rng(1)
    first = rng.uniform(0, 255, (480, 270)).astype(np.float32)
    light = first * 1.05                       # a gentle lighting change across the frame
    rewrite = first.copy()
    rewrite[:300] = rng.uniform(0, 255, (300, 270))  # the top 60% replaced by new content
    assert motion.changed_share(first, [light]) <= motion.MAX_CONTENT_CHANGE
    assert motion.changed_share(first, [light, rewrite]) > motion.MAX_CONTENT_CHANGE


def test_dry_runs_buy_no_motion_unless_asked(monkeypatch, tmp_path):
    monkeypatch.setattr(motion.config, "MEDIA_MONTHLY_BUDGET_USD", 30.0)
    monkeypatch.setattr(motion, "providers", lambda *a, **k: pytest.fail("a dry run bought a clip"))
    scenes = [tmp_path / "a.jpg"]
    assert motion.apply(None, scenes, tmp_path, allow_paid=False) == (scenes, [])
