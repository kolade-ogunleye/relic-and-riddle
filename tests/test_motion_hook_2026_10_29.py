"""The paid motion clip is the motion hook (production Derinkuyu, 2026-10-29, runs 1793303149 and its re-render
1793314157). A Kling clip of a lamp-lit corridor passed the homography and content checks (inlier ratio 0.997, 0%
content change) while nothing in it moved (first and last frames 2.5/255 apart, no shift); it was placed third,
at 12.7 s, with a parallax still opening the video; and the re-render carried it over "already paid for".

  1. The clip opens the video or is not bought (opening_slots).
  2. A clip with no visible motion is rejected and the beat keeps its free parallax (motion_amount): calibrated on
     synthetic clips encoded as _finish does. On this file's texture (flow share of the height / regions moving):
     still with encoder noise and a 2.5/255 light flicker 0.02% / 0%; 0.5% push 0.17% / 0% (rejected); 2% push
     0.73% / 43%; 3% pan 1.5% / 94%; ripples over the bottom fifth 1.4% / 19%. Thresholds 0.5% / 8%.
  3. A clip on disk or carried over from the run a re-render replaces passes today's checks again.
  4. A clip is bought only for a photo whose record names something that can move (can_move): no new vision call.
No network, no paid call: providers are stubbed."""

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src import motion

W, H, FPS, DUR = 540, 960, 30, 6.5


def _texture(seed: int = 3) -> np.ndarray:
    """A photo-like grey texture: smoothed noise at two scales and a few hard edges."""
    import cv2
    rng = np.random.default_rng(seed)
    coarse = cv2.resize(rng.uniform(40, 200, (24, 14)), (W, H), interpolation=cv2.INTER_CUBIC)
    fine = cv2.GaussianBlur(rng.uniform(-40, 40, (H, W)), (0, 0), 2.0)
    img = coarse + fine
    for k in range(6):
        y = int(H * (k + 1) / 7)
        img[y:y + 6] -= 50
    return np.clip(np.repeat(img[..., None], 3, axis=2), 0, 255).astype(np.float32)


def _clip(path: Path, frame) -> Path:
    """A 6.5 s clip, frame(t in 0..1) -> HxWx3, through the H.264 CRF 17 encode _finish uses."""
    proc = subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
                             "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
                             "-pix_fmt", "yuv420p", str(path)], stdin=subprocess.PIPE)
    n = int(FPS * DUR)
    for i in range(n):
        proc.stdin.write(np.clip(frame(i / (n - 1)), 0, 255).astype(np.uint8).tobytes())
    proc.stdin.close()
    assert proc.wait() == 0
    return path


def _zoom(base, z):
    import cv2
    m = cv2.getRotationMatrix2D((W / 2, H / 2), 0, z)
    return cv2.warpAffine(base, m, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def _shift(base, dx):
    import cv2
    m = np.float32([[1, 0, dx], [0, 1, 0]])
    return cv2.warpAffine(base, m, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def _still(base):
    rng = np.random.default_rng(0)
    return lambda t: base + 2.5 * np.sin(t * 3) + rng.normal(0, 1.5, base.shape)   # the Derinkuyu clip's 2.5/255


def _ripples(base):
    def frame(t):
        f = base.copy()
        y0 = int(H * 0.8)
        yy, xx = np.mgrid[y0:H, 0:W]
        f[y0:] = f[y0:] * 0.6 + 60 + (18 * np.sin(xx / 9.0 + yy / 5.0 - t * 25))[..., None]
        return f
    return frame


CASES = {
    "still": (lambda b: _still(b), False),
    "push 0.5%": (lambda b: (lambda t: _zoom(b, 1 + 0.005 * t)), False),
    "push 2%": (lambda b: (lambda t: _zoom(b, 1 + 0.02 * t)), True),
    "pan 3%": (lambda b: (lambda t: _shift(b, 0.03 * W * t)), True),
    "ripples": (lambda b: _ripples(b), True),
}


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    folder = tmp_path_factory.mktemp("clips")
    base = _texture()
    return {name: _clip(folder / f"{name.replace(' ', '_').replace('%', '')}.mp4", make(base))
            for name, (make, _) in CASES.items()}


@pytest.mark.parametrize("name", list(CASES))
def test_visible_motion_passes_and_a_near_still_does_not(clips, name):
    measured = motion.motion_amount(clips[name])
    print(name, measured)
    assert motion.visible_motion(measured) is CASES[name][1], measured


def test_the_near_still_is_far_below_both_thresholds(clips):
    measured = motion.motion_amount(clips["still"])
    assert measured["flow_share"] < motion.MIN_FLOW_SHARE / 5 and measured["moving_share"] < motion.MIN_MOVING_SHARE / 5


def _passing_geometry(monkeypatch):
    qa = {"pass": True, "reason": "Passed homography structural integrity gate", "inlier_ratio": 0.997, "inliers": 1453,
          "determinant": 1.0}
    monkeypatch.setattr(motion, "verify_motion_hook_integrity", lambda f0, f1, archetype="megastructure": dict(qa))


def test_a_faithful_still_clip_is_rejected_and_kept(monkeypatch, tmp_path, clips):
    _passing_geometry(monkeypatch)
    out = tmp_path / "motion_01.mp4"
    out.write_bytes(clips["still"].read_bytes())
    with pytest.raises(motion.MotionError, match="no visible motion"):
        motion._gate(out, "megastructure")
    record = json.loads((tmp_path / "rejected" / "motion_01.qa.json").read_text(encoding="utf-8"))
    assert not out.exists() and record["pass"] is False and record["content_change_share"] == 0.0
    assert record["flow_share"] < motion.MIN_FLOW_SHARE


def test_an_accepted_clip_records_its_motion_gate_and_hash(monkeypatch, tmp_path, clips):
    _passing_geometry(monkeypatch)
    out = tmp_path / "motion_01.mp4"
    out.write_bytes(clips["push 2%"].read_bytes())
    motion._gate(out, "megastructure")
    record = json.loads(out.with_suffix(".qa.json").read_text(encoding="utf-8"))
    assert record["gate"] == motion.GATE_VERSION and record["sha256"] == motion._sha256(out)
    assert record["flow_share"] >= motion.MIN_FLOW_SHARE and record["accepted"] is True
    # Trusted for these bytes: not measured again
    monkeypatch.setattr(motion, "motion_amount", lambda clip: pytest.fail("measured a recorded clip again"))
    assert motion.passes_gates(out)


# ------------------------------------------------------------------ placement
def _strong(monkeypatch, weak=()):
    monkeypatch.setattr(motion, "animatable", lambda p: Path(p).name not in weak)
    monkeypatch.setattr(motion, "can_move", lambda p: True)
    monkeypatch.setattr(motion.config, "MEDIA_MONTHLY_BUDGET_USD", 30.0)
    monkeypatch.setattr(motion.config, "MOTION_MAX_CLIPS", 1)
    bought = []

    def animate(db, img, out, topic=None, fact_sheet=None):
        bought.append(Path(img).name)
        out.write_bytes(b"clip")
        return out, "kling-3.0"
    monkeypatch.setattr(motion, "animate", animate)
    return bought


def test_the_clip_opens_the_video(monkeypatch, tmp_path):
    bought = _strong(monkeypatch)
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    scenes, used = motion.apply(None, [a, b], tmp_path / "motion", durations=[4.0, 5.0])
    assert bought == ["a.jpg"] and scenes[0].name == "motion_01.mp4" and scenes[1] == b and used == ["kling-3.0"]


def test_no_clip_is_bought_for_a_later_beat_when_the_opening_cannot_take_one(monkeypatch, tmp_path):
    bought = _strong(monkeypatch, weak={"context.jpg"})
    ctx, b = tmp_path / "context.jpg", tmp_path / "b.jpg"
    assert motion.apply(None, [ctx, b], tmp_path / "motion", durations=[4.0, 5.0]) == ([ctx, b], [])
    assert bought == []


def test_an_opening_longer_than_the_clip_buys_none(monkeypatch, tmp_path):
    bought = _strong(monkeypatch)
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    assert motion.apply(None, [a, b], tmp_path / "motion", durations=[7.5, 5.0]) == ([a, b], [])
    assert bought == []


def test_a_paid_clip_is_never_placed_on_a_later_beat_while_the_opening_is_a_still(monkeypatch, tmp_path):
    _strong(monkeypatch)
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    clip = tmp_path / "motion_01.mp4"
    clip.write_bytes(b"clip")
    monkeypatch.setattr(motion.config, "MOTION_MAX_CLIPS", 2)
    memo = {str(b): (clip, "kling-3.0")}   # made for b; a re-plan put b on beat 2
    scenes, used = motion.apply(None, [a, b], tmp_path / "motion", memo=memo, allow_paid=False)
    assert scenes == [a, b] and used == []


def test_one_more_clip_goes_later_only_when_the_opening_already_moves(monkeypatch, tmp_path):
    bought = _strong(monkeypatch)
    monkeypatch.setattr(motion.config, "MOTION_MAX_CLIPS", 2)
    footage, b = tmp_path / "footage_01.mp4", tmp_path / "b.jpg"
    footage.write_bytes(b"0")
    scenes, used = motion.apply(None, [footage, b], tmp_path / "motion")
    assert bought == ["b.jpg"] and scenes[0] == footage
    monkeypatch.setattr(motion.config, "MOTION_MAX_CLIPS", 1)
    bought.clear()
    assert motion.apply(None, [footage, b], tmp_path / "motion2") == ([footage, b], []) and bought == []


# ------------------------------------------------------------------ what can move
def _recorded(tmp_path, name, reason, title="File:Site.jpg", description="", people=False):
    folder = tmp_path / name
    folder.mkdir()
    photo = folder / "01_commons.jpg"
    Image.new("RGB", (60, 100), "gray").save(photo)
    entry = {"file": photo.name, "title": title, "description": description,
             "vision_review": {"shows_topic": "yes", "usable": True, "safe": True, "reason": reason,
                               "people_prominent": people}}
    (folder / "credits.json").write_text(json.dumps({"images": [entry]}), encoding="utf-8")
    return photo


@pytest.mark.parametrize("reason,title,people,expected", [
    ("Good, clear view of a rolling stone door mechanism and corridor in Derinkuyu.", "File:Rolling stone door.jpg", False, False),
    ("Well-lit interior view of underground chambers and carved passages.", "File:Underground city.jpg", False, False),
    ("Carved stone gallery; no people visible.", "File:Gallery.jpg", False, False),
    ("Waves breaking against the harbour wall below the fort.", "File:Fort.jpg", False, True),
    ("Wide view of the temple terraces.", "File:Temple under a cloudy sky.jpg", False, True),
    ("The square during the festival.", "File:Square.jpg", True, True),
    ("Palm trees along the excavated causeway.", "File:Causeway.jpg", False, True),
])
def test_a_clip_is_asked_for_only_where_something_can_move(tmp_path, reason, title, people, expected):
    assert motion.can_move(_recorded(tmp_path, f"t{abs(hash(reason)) % 10**8}", reason, title, people=people)) is expected


def test_a_photo_you_supplied_has_no_record_and_is_left_to_the_motion_check(tmp_path):
    photo = tmp_path / "mine.jpg"
    Image.new("RGB", (60, 100), "gray").save(photo)
    assert motion.can_move(photo) is None


def test_a_solid_stone_opening_buys_no_clip(monkeypatch, tmp_path):
    photo = _recorded(tmp_path, "derinkuyu", "Good, clear view of a rolling stone door mechanism and corridor.")
    monkeypatch.setattr(motion.config, "MEDIA_MONTHLY_BUDGET_USD", 30.0)
    monkeypatch.setattr(motion.config, "MOTION_MAX_CLIPS", 1)
    monkeypatch.setattr(motion, "animate", lambda *a, **k: pytest.fail("bought a clip of solid stone"))
    assert motion.apply(None, [photo], tmp_path / "motion", durations=[4.0]) == ([photo], [])


# ------------------------------------------------------------------ carry-over
def _paid(folder: Path, photo: Path, source: Path, qa: dict = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    anchor, clip = folder / "motion_01_anchor.jpg", folder / "motion_01.mp4"
    Image.new("RGB", (60, 100), "white").save(anchor)
    clip.write_bytes(source.read_bytes())
    motion._record_lineage(photo, anchor, (clip, "kling-3.0"), "kling-3.0")
    if qa is not None:
        clip.with_suffix(".qa.json").write_text(json.dumps(qa), encoding="utf-8")
    return clip


# The Derinkuyu clip's record, written before the motion check existed
OLD_RECORD = {"pass": True, "inlier_ratio": 0.997, "determinant": 1.0, "archetype": "megastructure",
              "content_change_share": 0.0, "accepted": True}


def test_a_still_clip_is_not_carried_into_the_re_render(monkeypatch, tmp_path, clips):
    _passing_geometry(monkeypatch)
    photo = tmp_path / "beat_01.jpg"
    Image.new("RGB", (60, 100), "gray").save(photo)
    prev = _paid(tmp_path / "run_a" / "motion", photo, clips["still"], OLD_RECORD)
    _strong(monkeypatch)
    new = tmp_path / "run_b" / "motion"
    scenes, used = motion.apply(None, [photo], new, memo={}, allow_paid=False, seed_dirs=[prev.parent])
    assert scenes == [photo] and used == []
    assert prev.is_file() and not (new / "motion_01.mp4").exists()   # the other run's folder is left alone


def test_a_moving_clip_is_still_carried_over_and_measured_once(monkeypatch, tmp_path, clips):
    _passing_geometry(monkeypatch)
    photo = tmp_path / "beat_01.jpg"
    Image.new("RGB", (60, 100), "gray").save(photo)
    prev = _paid(tmp_path / "run_a" / "motion", photo, clips["push 2%"], OLD_RECORD)
    _strong(monkeypatch)
    new = tmp_path / "run_b" / "motion"
    motion.passes_gates(prev)   # this run's own pass: its record now names today's gates
    monkeypatch.setattr(motion, "motion_amount", lambda clip: pytest.fail("measured a carried clip twice"))
    scenes, used = motion.apply(None, [photo], new, memo={}, allow_paid=False, seed_dirs=[prev.parent])
    assert used == ["kling-3.0"] and scenes[0] == new / "motion_01.mp4"


def test_this_runs_own_still_clip_on_disk_is_rejected_on_resume(monkeypatch, tmp_path, clips):
    _passing_geometry(monkeypatch)
    photo = tmp_path / "beat_01.jpg"
    Image.new("RGB", (60, 100), "gray").save(photo)
    work = tmp_path / "run" / "motion"
    clip = _paid(work, photo, clips["still"], OLD_RECORD)
    _strong(monkeypatch)
    assert motion.apply(None, [photo], work, memo={}, allow_paid=False) == ([photo], [])
    assert not clip.exists() and (work / "rejected" / "motion_01.mp4").is_file()


def test_a_changed_clip_is_measured_again_whatever_its_record_says(monkeypatch, tmp_path, clips):
    _passing_geometry(monkeypatch)
    out = tmp_path / "motion_01.mp4"
    out.write_bytes(clips["push 2%"].read_bytes())
    motion._gate(out)
    out.write_bytes(clips["still"].read_bytes())   # same name, other bytes
    assert motion.passes_gates(out, keep=False) is False


def test_a_clip_refused_for_no_motion_keeps_the_parallax_and_says_so(monkeypatch, tmp_path):
    from src import provider_status
    photo = tmp_path / "a.jpg"
    Image.new("RGB", (60, 100), "gray").save(photo)
    monkeypatch.setattr(motion, "animatable", lambda p: True)
    monkeypatch.setattr(motion, "can_move", lambda p: True)
    monkeypatch.setattr(motion.config, "MEDIA_MONTHLY_BUDGET_USD", 30.0)
    monkeypatch.setattr(motion.config, "MOTION_MAX_CLIPS", 1)
    monkeypatch.setattr(motion.media_budget, "allow", lambda db, cost: True)
    monkeypatch.setattr(motion.media_budget, "record", lambda *a, **k: None)
    monkeypatch.setattr(provider_status, "capped", lambda: {})

    def still_clip(img, out, on_billed=None):
        on_billed()
        raise motion.MotionError("Motion check failed: no visible motion (flow 0.02% of the height < 0.5%)")
    monkeypatch.setattr(motion, "providers", lambda *a, **k: [("kling-3.0", lambda: True, still_clip, 0.84)])
    assert motion.apply(None, [photo], tmp_path / "motion", durations=[4.0]) == ([photo], [])
    assert motion.fallback_note([], tried_paid=True) == (
        "🎞 Opening motion: parallax (the paid clip had no visible motion; kept in motion/rejected)")
    motion.apply(None, [photo], tmp_path / "motion", allow_paid=False)   # the next run starts clean
    assert motion.fallback_note([], tried_paid=True) == ""
