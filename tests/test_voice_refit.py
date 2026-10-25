"""A voiceover that reads over the ceiling is made once more, never time-stretched: Azure and Edge at a rate
raised by the measured overrun (production, 2026-10-25: 114 words in 53.7 s against 52), ElevenLabs as it is.
The estimate plans for the slow end: until a voice has been measured enough, and always for ElevenLabs."""
import json

from src import audio_engine, config
from src.script_engine import HARD_DURATION_CEILING, refit_speed


def test_an_overrun_is_refit_natively_within_the_cap(monkeypatch):
    monkeypatch.setattr(config, "SPEECH_SPEEDUP_MAX", 1.08)
    assert refit_speed(51.9) is None                                              # fits: no second take
    s = refit_speed(53.7)
    assert s == 1.05 and 53.7 / s < HARD_DURATION_CEILING                        # the production case, now fitting
    assert refit_speed(53.7, speed=1.03) == 1.08                                   # compounds an earlier speed-up
    assert refit_speed(60.0) is None                                              # beyond the cap: still fails


def test_the_pace_plans_for_the_slowest_take_until_it_is_measured_enough(monkeypatch, tmp_path):
    pace = tmp_path / "voice_pace.json"
    monkeypatch.setattr(audio_engine, "PACE_FILE", pace)
    pace.write_text(json.dumps({"azure:v": [1.0184, 1.0748]}), encoding="utf-8")
    assert audio_engine.pace_ratio("azure:v") == 1.0748
    pace.write_text(json.dumps({"azure:v": [1.01, 1.02, 1.03, 1.04, 1.09]}), encoding="utf-8")
    assert audio_engine.pace_ratio("azure:v") == 1.03                             # median once five are known
    assert audio_engine.pace_ratio("unknown") == 1.0


def _coordinator_with_take(tmp_path, provider, seconds_seq):
    import subprocess
    import main
    from src.audio_engine import AudioEngine
    take = tmp_path / "voiceover.mp3"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds_seq[0]}",
                    "-b:a", "192k", str(take)], check=True)
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    eng = AudioEngine.__new__(AudioEngine)
    eng.last_provider = provider
    c.audio_engine = eng
    return c, take


def test_an_elevenlabs_take_over_the_ceiling_is_read_again_never_stretched(tmp_path):
    # Operator, 2026-10-25: of the raw 53.8 s take and the same take stretched 4%, only the stretched one
    # sounded awkward. ElevenLabs ignores its speed setting, so an overrun gets one more natural take.
    c, take = _coordinator_with_take(tmp_path, "elevenlabs", [52.9])
    words = [{"word": "a", "start": 10.0, "dur": 1.0}]
    retakes = []

    def voice_at(speed):
        retakes.append(speed)
        return [{"word": "a", "start": 9.0, "dur": 1.0}]
    out, speed = c._fit_take(take, words, 1.0, voice_at, {})
    assert retakes == [1.0] and speed == 1.0                                          # read again, as it is
    from src.audio_engine import AudioEngine
    assert abs(AudioEngine.get_duration(take) - 52.9) < 0.15                          # the file was not altered
    assert out[0]["start"] == 9.0
    assert not hasattr(AudioEngine, "stretch_to_fit")


def test_a_take_that_fits_or_is_far_over_is_not_read_again(tmp_path):
    for seconds in (51.0, 60.0):
        c, take = _coordinator_with_take(tmp_path, "elevenlabs", [seconds])
        retakes = []
        c._fit_take(take, [], 1.0, lambda s: retakes.append(s) or [], {})
        assert retakes == []                                     # fits; or no take of this script could


def test_a_voice_that_honours_its_rate_is_read_faster_natively(tmp_path, monkeypatch):
    from src import config
    monkeypatch.setattr(config, "SPEECH_SPEEDUP_MAX", 1.08)
    c, take = _coordinator_with_take(tmp_path, "azure", [53.7])
    inputs, retakes = {}, []
    _, speed = c._fit_take(take, [], 1.0, lambda s: retakes.append(s) or [], inputs)
    assert retakes == [1.05] and speed == 1.05 and inputs["speed"] == 1.05


def test_elevenlabs_is_planned_for_its_slowest_take_and_never_read_faster(monkeypatch, tmp_path, curated_payload):
    from src import script_engine
    from src.models import ScriptPackage
    pace = tmp_path / "voice_pace.json"
    monkeypatch.setattr(audio_engine, "PACE_FILE", pace)
    pace.write_text(json.dumps({"elevenlabs:v:m": [1.01, 1.02, 1.03, 1.04, 1.09]}), encoding="utf-8")
    assert audio_engine.pace_ratio("elevenlabs:v:m") == 1.09                      # no speed-up to absorb a slow take
    assert not audio_engine.honours_speed("elevenlabs") and audio_engine.honours_speed("azure")
    monkeypatch.setattr(audio_engine, "production_voice_key", lambda: "elevenlabs:v:m")
    monkeypatch.setattr(audio_engine, "measure_spoken_seconds", lambda text, speed=1.0: 48.5 / speed)
    pkg = ScriptPackage.model_validate(curated_payload)                            # 48.5 x 1.09 = 52.9 s
    assert script_engine.fit_speed(pkg.narration_script) is None
    assert "rewrite the narration in" in " ".join(script_engine.spoken_length_problems(pkg))
    monkeypatch.setattr(audio_engine, "measure_spoken_seconds", lambda text, speed=1.0: 44.0 / speed)
    assert script_engine.fit_speed(pkg.narration_script) == 1.0 and script_engine.spoken_length_problems(pkg) == []


def test_a_script_whose_take_ran_long_is_shortened_when_kept_or_resumed(monkeypatch, tmp_path, db, curated_payload):
    import main
    from types import SimpleNamespace
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(audio_engine, "measure_spoken_seconds", lambda text, speed=1.0: 60.0 / speed)
    previous = tmp_path / "run_prev"
    previous.mkdir()
    (previous / "verified_script.json").write_text(json.dumps(dict(curated_payload, guardrail_passed=True)), encoding="utf-8")
    c = SimpleNamespace(db=db)
    c._previous_run_dir = lambda: previous
    assert main.PipelineCoordinator._script_from_previous(c, {"reason": "pictures", "from_run": "run_prev"}) is None
    coordinator = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    assert "runs 60.0s when spoken" in coordinator._spoken_length_now(curated_payload)
    monkeypatch.setattr(audio_engine, "measure_spoken_seconds", lambda text, speed=1.0: 46.0)
    assert coordinator._spoken_length_now(curated_payload) == ""
    assert coordinator._spoken_length_now({"narration_script": 5}) == ""           # unreadable: not a failure


def test_a_take_from_before_the_stretch_was_removed_is_not_reused(tmp_path):
    import main
    from src.checkpoints import Checkpoints
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.audio_engine = type("E", (), {"voice": "v", "rate": "+0%", "pitch": "+0Hz", "eleven_voice": "e"})()
    c.paid_voice_in_dry_run = False
    inputs = c.voice_inputs("Words.", dry_run=False)
    assert inputs["take"] == "natural"
    old = {k: v for k, v in inputs.items() if k != "take"}                       # a checkpoint from rc48 or earlier
    take = tmp_path / "voiceover.mp3"
    take.write_bytes(b"x")
    cp = Checkpoints(tmp_path)
    cp.complete("voice", old, [take])
    assert not main.PipelineCoordinator.voice_reusable(cp, inputs, {"licensed": True}, dry_run=False)


def _eleven_pace(monkeypatch, tmp_path):
    pace = tmp_path / "voice_pace.json"
    monkeypatch.setattr(audio_engine, "PACE_FILE", pace)
    pace.write_text(json.dumps({"elevenlabs:v:m": [1.0184, 1.0748, 1.0352, 1.0998, 1.0245, 1.0795, 0.9967]}), encoding="utf-8")
    monkeypatch.setattr(audio_engine, "production_voice_key", lambda: "elevenlabs:v:m")


def test_both_ends_of_the_window_are_judged_for_the_takes_that_reach_them(monkeypatch, tmp_path, curated_payload):
    # Production 2026-10-25 (rc49): planned for the slowest take only, a 93-word script read 41.1 s, under 42.
    from src import script_engine
    from src.models import ScriptPackage
    _eleven_pace(monkeypatch, tmp_path)
    assert audio_engine.pace_range("elevenlabs:v:m") == (0.9967, 1.0998)
    assert audio_engine.pace_range("azure:v") == (1.0, 1.0)
    pkg = ScriptPackage.model_validate(curated_payload)
    monkeypatch.setattr(audio_engine, "measure_spoken_seconds", lambda text, speed=1.0: 41.2)
    short = " ".join(script_engine.spoken_length_problems(pkg))
    assert "minimum 42s" in short and "extend the narration" in short                # the production case
    monkeypatch.setattr(audio_engine, "measure_spoken_seconds", lambda text, speed=1.0: 44.0)
    assert script_engine.spoken_length_problems(pkg) == []                          # fits for every take seen
    monkeypatch.setattr(audio_engine, "measure_spoken_seconds", lambda text, speed=1.0: 47.5)
    assert "limit 51.5s" in " ".join(script_engine.spoken_length_problems(pkg))


def test_the_word_target_fits_the_fastest_and_the_slowest_take(monkeypatch, tmp_path):
    from src import script_engine
    _eleven_pace(monkeypatch, tmp_path)
    words = tmp_path / "word_pace.json"
    monkeypatch.setattr(script_engine, "WORD_PACE_FILE", words)
    words.write_text(json.dumps([0.4436] * 5), encoding="utf-8")
    import re
    low, high = (int(x) for x in re.search(r"in (\d+)-(\d+) words", script_engine.length_target_note()).groups())
    assert low * 0.4436 * 0.9967 >= 42 and high * 0.4436 * 1.0998 <= 48.5 and low <= high


def test_a_take_a_little_under_the_minimum_is_read_once_more(tmp_path):
    for provider, seconds, expected in (("elevenlabs", 41.1, [1.0]), ("azure", 41.1, []), ("elevenlabs", 35.0, [])):
        c, take = _coordinator_with_take(tmp_path, provider, [seconds])
        retakes = []
        c._fit_take(take, [], 1.0, lambda s: retakes.append(s) or [], {})
        assert retakes == expected, (provider, seconds)
