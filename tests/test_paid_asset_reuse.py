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


def test_a_regeneration_carries_the_previous_runs_clip_over_with_fresh_lineage(monkeypatch, tmp_path):
    from src.lineage import source_chain
    photo, prev_work, clip = _paid_clip(tmp_path)                  # the run being replaced
    new_work = tmp_path / "run_b" / "motion"
    monkeypatch.setattr(motion.config, "MEDIA_MONTHLY_BUDGET_USD", 30.0)
    monkeypatch.setattr(motion.config, "MOTION_MAX_CLIPS", 1)
    monkeypatch.setattr(motion, "animate", lambda *a, **k: pytest.fail("paid again for a clip the previous run made"))
    scenes, used = motion.apply(None, [photo, tmp_path / "other.jpg"], new_work, memo={}, allow_paid=True, seed_dirs=[prev_work])
    assert used == ["kling-3.0"] and scenes[0] == new_work / "motion_01.mp4" and scenes[0].is_file()
    source_chain(scenes[0])                                         # the copy's lineage verifies on its own
    assert clip.is_file()                                           # the original run keeps its clip


def test_a_regeneration_carries_the_previous_runs_voiceover_over(db, tmp_path, monkeypatch):
    import json
    import main
    from types import SimpleNamespace
    from src.checkpoints import Checkpoints
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)
    previous, run_dir = tmp_path / "run_prev", tmp_path / "run_new"
    previous.mkdir(), run_dir.mkdir()
    (previous / "script_metadata.json").write_text(json.dumps({"narration_script": "Same words."}), encoding="utf-8")
    for n, body in (("voiceover.mp3", b"eleven"), ("words.json", b"[]"), ("voice.json", b'{"provider": "elevenlabs", "licensed": true}')):
        (previous / n).write_bytes(body)
    inputs = {"script": "Same words.", "voice": "v", "licensed_only": False, "allow_paid": True}
    Checkpoints(previous).complete("voice", inputs, [previous / "voiceover.mp3", previous / "words.json", previous / "voice.json"])
    coordinator = SimpleNamespace(_operator_feedback={"reason": "pictures", "from_run": "run_prev"}, db=db)
    coordinator._feedback = lambda: main.PipelineCoordinator._feedback(coordinator)
    coordinator._previous_run_dir = lambda: main.PipelineCoordinator._previous_run_dir(coordinator)
    main.PipelineCoordinator._seed_voice_from_previous(coordinator, run_dir, "Same words.")
    assert (run_dir / "voiceover.mp3").read_bytes() == b"eleven"
    assert Checkpoints(run_dir).reusable("voice", inputs)          # the voice stage will keep it
    (run_dir / "voiceover.mp3").unlink()
    main.PipelineCoordinator._seed_voice_from_previous(coordinator, run_dir, "Different words.")
    assert not (run_dir / "voiceover.mp3").exists()                 # another script: a new voiceover


def test_a_regeneration_carries_the_music_bed_over_with_the_same_voiceover(db, tmp_path, monkeypatch):
    import json
    import main
    from types import SimpleNamespace
    from src.checkpoints import Checkpoints, portable_inputs
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)
    previous, run_dir = tmp_path / "run_prev", tmp_path / "run_new"
    previous.mkdir(), run_dir.mkdir()
    for d in (previous, run_dir):
        (d / "voiceover.mp3").write_bytes(b"same voice")
    (previous / "words.json").write_bytes(b"[]")
    for n in main.PipelineCoordinator.BED_FILES:
        (previous / n).write_bytes(b"bed " + n.encode())
    inputs = {"voice_and_rules": portable_inputs(previous, [previous / "voiceover.mp3", previous / "words.json"]), "profile": {}}
    Checkpoints(previous).complete("bed", inputs, [previous / n for n in main.PipelineCoordinator.BED_FILES])
    coordinator = SimpleNamespace(_operator_feedback={"reason": "pictures", "from_run": "run_prev"}, db=db)
    coordinator._feedback = lambda: main.PipelineCoordinator._feedback(coordinator)
    coordinator._previous_run_dir = lambda: main.PipelineCoordinator._previous_run_dir(coordinator)
    coordinator.BED_FILES = main.PipelineCoordinator.BED_FILES
    main.PipelineCoordinator._seed_bed_from_previous(coordinator, run_dir, run_dir / "voiceover.mp3")
    assert (run_dir / "ambient_bed.wav").read_bytes() == b"bed ambient_bed.wav"
    (run_dir / "words.json").write_bytes(b"[]")
    fresh = {"voice_and_rules": portable_inputs(run_dir, [run_dir / "voiceover.mp3", run_dir / "words.json"]), "profile": {}}
    assert Checkpoints(run_dir).reusable("bed", fresh)          # the same record means the same here
    # The review was about the sound: a different bed is wanted, so nothing is carried over.
    other = tmp_path / "run_sound"; other.mkdir(); (other / "voiceover.mp3").write_bytes(b"same voice")
    coordinator._operator_feedback = {"reason": "sound", "from_run": "run_prev"}
    main.PipelineCoordinator._seed_bed_from_previous(coordinator, other, other / "voiceover.mp3")
    assert not (other / "ambient_bed.wav").exists()


def test_a_voice_from_another_provider_is_not_carried_over_once_the_run_would_use_a_different_one(db, tmp_path, monkeypatch):
    # 2026-10-24: with ELEVENLABS_IN_DRY_RUNS switched on in production, a remake of an Azure-voiced run must
    # be voiced by ElevenLabs, not inherit the Azure voiceover with the script.
    import json
    import main
    from types import SimpleNamespace
    from src import audio_engine
    from src.checkpoints import Checkpoints
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)
    previous, run_dir = tmp_path / "run_prev", tmp_path / "run_new"
    previous.mkdir(), run_dir.mkdir()
    (previous / "script_metadata.json").write_text(json.dumps({"narration_script": "Same words."}), encoding="utf-8")
    for n, body in (("voiceover.mp3", b"azure"), ("words.json", b"[]"), ("voice.json", b'{"provider": "azure", "licensed": true}')):
        (previous / n).write_bytes(body)
    inputs = {"script": "Same words.", "voice": "v", "licensed_only": False, "allow_paid": False}
    Checkpoints(previous).complete("voice", inputs, [previous / "voiceover.mp3", previous / "words.json", previous / "voice.json"])
    coordinator = SimpleNamespace(_operator_feedback={"reason": "pictures", "from_run": "run_prev"}, db=db)
    coordinator._feedback = lambda: main.PipelineCoordinator._feedback(coordinator)
    coordinator._previous_run_dir = lambda: main.PipelineCoordinator._previous_run_dir(coordinator)
    main.PipelineCoordinator._seed_voice_from_previous(coordinator, run_dir, "Same words.", wanted="elevenlabs")
    assert not (run_dir / "voiceover.mp3").exists()                 # ElevenLabs is wanted now: re-voice
    main.PipelineCoordinator._seed_voice_from_previous(coordinator, run_dir, "Same words.", wanted="azure")
    assert (run_dir / "voiceover.mp3").read_bytes() == b"azure"       # the same provider: kept
    # which provider a run would use first
    monkeypatch.setattr(audio_engine, "VOICE_PREFER", "elevenlabs")
    monkeypatch.setattr(audio_engine, "ELEVENLABS_API_KEY", "k" * 30)
    assert audio_engine.AudioEngine.preferred_provider(True) == "elevenlabs"
    assert audio_engine.AudioEngine.preferred_provider(False) == "azure"     # a dry run that may not spend
    monkeypatch.setattr(audio_engine, "VOICE_PREFER", "azure")
    assert audio_engine.AudioEngine.preferred_provider(True) == "azure"


def test_a_carried_voiceover_passes_the_release_rights_check_and_a_changed_one_still_fails(db, tmp_path, monkeypatch):
    """Audit 2026-10-28: every regenerated run on production reached review as "Release gate: blocked ...
    voiceover_rights / ambient_bed_rights: Audio changed after rights evidence was recorded" though the copies were
    byte-identical; the record named the previous run's folder. The copy is now rebound; a changed file is not."""
    import json
    import main
    from types import SimpleNamespace
    from src.audio_provenance import inspect_audio_rights, rebind_rights, record_voice
    from src.checkpoints import Checkpoints
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)
    previous, run_dir = tmp_path / "run_prev", tmp_path / "run_new"
    previous.mkdir(), run_dir.mkdir()
    (previous / "script_metadata.json").write_text(json.dumps({"narration_script": "Same words."}), encoding="utf-8")
    for n, body in (("voiceover.mp3", b"eleven take"), ("words.json", b"[]"), ("voice.json", b'{"provider": "elevenlabs"}')):
        (previous / n).write_bytes(body)
    record_voice(previous / "voiceover.mp3", "elevenlabs", True, "Same words.")
    inputs = {"script": "Same words.", "voice": "v", "licensed_only": False, "allow_paid": True}
    Checkpoints(previous).complete("voice", inputs, [previous / "voiceover.mp3", previous / "words.json", previous / "voice.json"])
    coordinator = SimpleNamespace(_operator_feedback={"reason": "pictures", "from_run": "run_prev"}, db=db)
    coordinator._feedback = lambda: main.PipelineCoordinator._feedback(coordinator)
    coordinator._previous_run_dir = lambda: main.PipelineCoordinator._previous_run_dir(coordinator)
    main.PipelineCoordinator._seed_voice_from_previous(coordinator, run_dir, "Same words.")
    ok, record = inspect_audio_rights(run_dir / "voiceover.mp3", "voice", "", script="Same words.")
    assert ok is True and record["carried_from"] == str((previous / "voiceover.mp3").resolve())
    assert inspect_audio_rights(previous / "voiceover.mp3", "voice", "", script="Same words.")[0] is True  # untouched
    assert Checkpoints(run_dir).reusable("voice", inputs)                      # still reused, not re-voiced
    # A file that changed after it was recorded is never rebound: the gate still refuses it.
    other = tmp_path / "run_changed"; other.mkdir()
    (other / "voiceover.mp3").write_bytes(b"a different take")
    (other / "voiceover.mp3.rights.json").write_bytes((previous / "voiceover.mp3.rights.json").read_bytes())
    assert rebind_rights(other / "voiceover.mp3") is False
    assert inspect_audio_rights(other / "voiceover.mp3", "voice", "", script="Same words.") == (
        False, "Audio changed after rights evidence was recorded")
