"""What a run already paid for is reused when it is made again (a resume, a restart, a re-plan): the
motion clip is found from its verified lineage on disk, not only from memory, and a licensed voiceover
for the same script is kept even when this pass may not pay for one (2026-10-24: re-rendering the
release-blocked Kailasa run would have bought its accepted Kling clip a second time)."""

from pathlib import Path

import pytest
from PIL import Image

from src import motion
from src.checkpoints import Checkpoints


def _paid_clip(tmp_path):
    photo, work = tmp_path / "beat_01.jpg", tmp_path / "motion"
    work.mkdir()
    Image.new("RGB", (40, 70), "gray").save(photo)
    anchor, clip = work / "motion_01_anchor.jpg", work / "motion_01.mp4"
    Image.new("RGB", (40, 70), "white").save(anchor)
    clip.write_bytes(b"accepted kling clip")
    motion._record_lineage(photo, anchor, (clip, "kling-3.0"), "kling-3.0")
    return photo, work, clip


@pytest.mark.parametrize("allow_paid", [True, False])
def test_an_accepted_clip_on_disk_is_reused_without_paying(monkeypatch, tmp_path, allow_paid):
    photo, work, clip = _paid_clip(tmp_path)
    monkeypatch.setattr(motion.config, "MEDIA_MONTHLY_BUDGET_USD", 30.0)
    monkeypatch.setattr(motion.config, "MOTION_MAX_CLIPS", 1)
    monkeypatch.setattr(motion, "animate", lambda *a, **k: pytest.fail("paid again for a clip on disk"))
    other = tmp_path / "beat_02.jpg"
    scenes, used = motion.apply(None, [photo, other], work, memo={}, allow_paid=allow_paid)  # a fresh process
    assert scenes == [clip, other] and used == ["kling-3.0"]


def test_a_clip_is_not_reused_when_its_photo_changed_or_left_the_plan(monkeypatch, tmp_path):
    photo, work, clip = _paid_clip(tmp_path)
    monkeypatch.setattr(motion.config, "MEDIA_MONTHLY_BUDGET_USD", 30.0)
    other = tmp_path / "beat_02.jpg"
    assert motion.apply(None, [other], work, allow_paid=False) == ([other], [])  # its photo is not on any beat
    Image.new("RGB", (40, 70), "black").save(photo)  # the photo was replaced: the clip shows the old one
    assert motion.apply(None, [photo], work, allow_paid=False) == ([photo], [])


def test_a_rejected_clip_has_no_lineage_and_is_never_reused(monkeypatch, tmp_path):
    work = tmp_path / "motion"
    work.mkdir()
    (work / "motion_01.mp4").write_bytes(b"rejected")
    assert motion._clips_on_disk(work) == {}


def _voice(tmp_path, inputs):
    cp = Checkpoints(tmp_path)
    vo = tmp_path / "voiceover.mp3"
    vo.write_bytes(b"elevenlabs voice")
    cp.complete("voice", inputs, [vo])
    return cp


def test_a_licensed_voiceover_for_the_same_script_is_kept_when_paid_voice_is_off(tmp_path):
    from main import PipelineCoordinator as P
    made = {"script": "S", "voice": "v", "licensed_only": False, "allow_paid": True}   # the paid dry run
    cp = _voice(tmp_path, made)
    now = {**made, "allow_paid": False}                                                # the resume, flags off
    assert P.voice_reusable(cp, now, {"provider": "elevenlabs", "licensed": True}, dry_run=True)
    assert P.voice_reusable(cp, {**made, "licensed_only": True}, {"licensed": True}, dry_run=False)  # live
    assert not P.voice_reusable(cp, {**now, "script": "Changed"}, {"licensed": True}, dry_run=True)
    assert not P.voice_reusable(cp, now, {"provider": "edge", "licensed": False}, dry_run=True)


def test_an_unlicensed_test_voice_is_still_never_reused_live(tmp_path, monkeypatch):
    from main import PipelineCoordinator as P
    monkeypatch.setattr("src.config.VOICE_REQUIRE_LICENSED", True)
    made = {"script": "S", "voice": "v", "licensed_only": True, "allow_paid": True}
    cp = _voice(tmp_path, made)
    assert not P.voice_reusable(cp, made, {"provider": "edge", "licensed": False}, dry_run=False)
    assert P.voice_reusable(cp, made, {"provider": "edge", "licensed": False}, dry_run=True)
    assert Path(tmp_path / "voiceover.mp3").is_file()
