"""One spoken-length window for every instruction (production, Hal Saflieni 2026-10-27,
run_hal_saflieni_hypogeum_1793106146): the cut was worked out at the slowest take only and the extension at
the fastest only, so 103 words (54.3 s) were told "85-90 words", 86 words then read 39.3 s (too short), 91
read 40.3 s and 100 read 51.8 s (too long again), and all three attempts were spent. Every range the checks
and the prompt's note suggest now lies inside both limits at once."""
import math
import re

import pytest

from src import audio_engine, config, script_engine
from src.models import ScriptPackage

LONGEST = config.HARD_DURATION_CEILING - script_engine._MEASURE_MARGIN_SEC
RANGE = re.compile(r"(?:in|to) (\d+)-(\d+) words")


def _judged(m, fast, slow):
    """The paces the window is judged at, worked out independently: the middle one when the fastest take's
    minimum and the slowest take's ceiling leave no word count that fits both."""
    if math.ceil(config.TARGET_DURATION_MIN / (m * fast) - 1e-9) > math.floor(LONGEST / (m * slow) + 1e-9):
        fast = slow = (fast + slow) / 2
    return fast, slow


def _fits(n, m, fast, slow):
    return n * m * fast >= config.TARGET_DURATION_MIN - 1e-6 and n * m * slow <= LONGEST + 1e-6


def _draft(curated_payload, words):
    return ScriptPackage.model_validate(dict(curated_payload, narration_script=" ".join(["word"] * (words - 1)) + " end."))


@pytest.fixture
def paced(monkeypatch):
    """measure_spoken_seconds at m s a word, the narrator's (fast, slow) take ratios, no faster read (ElevenLabs)."""
    def set_pace(m, fast, slow):
        monkeypatch.setattr(audio_engine, "measure_spoken_seconds", lambda text, speed=1.0: len(text.split()) * m / speed)
        monkeypatch.setattr(script_engine, "_pace_window", lambda: (fast, slow))
    monkeypatch.setattr(script_engine, "fit_speed", lambda text, seconds=None: None)
    monkeypatch.setattr(script_engine, "record_word_pace", lambda spw, text="": None)
    return set_pace


def test_every_suggested_range_lies_inside_both_limits(paced, curated_payload):
    checked = 0
    for m in [0.40 + i * 0.01 for i in range(21)]:
        for fast, slow in ((1.0, 1.0), (0.95, 1.05), (0.914, 1.054), (0.9, 1.1), (0.85, 1.15), (0.8, 1.25)):
            paced(m, fast, slow)
            jf, js = _judged(m, fast, slow)
            window = script_engine.length_window(m, fast, slow)
            assert (window.fast, window.slow) == pytest.approx((jf, js))
            assert window.low <= window.comfortable <= window.high
            for words in range(60, 141, 3):
                problems = " ".join(script_engine.spoken_length_problems(_draft(curated_payload, words)))
                found = RANGE.search(problems)
                if not problems:
                    assert _fits(words, m, jf, js), (m, fast, slow, words)  # passed only because it fits
                    continue
                assert found, problems
                low, high = int(found.group(1)), int(found.group(2))
                assert low <= high
                assert all(_fits(n, m, jf, js) for n in range(low, high + 1)), (m, fast, slow, words, low, high)
                checked += 1
    assert checked > 1000


def test_the_prompt_note_lies_inside_the_same_window(monkeypatch, tmp_path):
    import json
    pace = tmp_path / "word_pace.json"
    monkeypatch.setattr(script_engine, "WORD_PACE_FILE", pace)
    for m in (0.42, 0.457, 0.5, 0.527, 0.56):
        for fast, slow in ((1.0, 1.0), (0.914, 1.054), (0.8, 1.25)):
            monkeypatch.setattr(script_engine, "_pace_window", lambda f=fast, s=slow: (f, s))
            pace.write_text(json.dumps([m] * 3), encoding="utf-8")
            low, high = (int(x) for x in RANGE.search(script_engine.length_target_note()).groups())
            jf, js = _judged(m, fast, slow)
            assert low <= high and all(_fits(n, m, jf, js) for n in range(low, high + 1)), (m, fast, slow)


def test_the_hal_saflieni_drafts_are_told_a_range_that_fits_both_takes(paced, curated_payload):
    # Production 0.527 s a word at the slowest take and 0.457 at the fastest: m 0.5 with ratios 1.054 / 0.914.
    paced(0.5, 0.914, 1.054)
    slow_pw, fast_pw = 0.5 * 1.054, 0.5 * 0.914
    long = " ".join(script_engine.spoken_length_problems(_draft(curated_payload, 103)))
    assert "Narration runs 54.3s" in long and "This draft is 103 words" in long
    low, high = (int(x) for x in RANGE.search(long).groups())
    assert low >= 92 and high <= 97  # was "85-90": every count there read under 42 s at the fastest take
    assert low * fast_pw >= config.TARGET_DURATION_MIN and high * slow_pw <= LONGEST
    short = " ".join(script_engine.spoken_length_problems(_draft(curated_payload, 86)))
    assert "Narration runs 39.3s" in short and "extend the narration" in short
    low, high = (int(x) for x in RANGE.search(short).groups())
    assert low * fast_pw >= config.TARGET_DURATION_MIN and high * slow_pw <= LONGEST  # 100 words no longer allowed
    for words in range(low, high + 1):  # a draft written to either range passes the next check
        assert script_engine.spoken_length_problems(_draft(curated_payload, words)) == []


def test_the_word_floor_never_lifts_the_range_past_the_ceiling():
    # A voice so slow that WORD_COUNT_FLOOR words overrun the ceiling is held to the ceiling.
    window = script_engine.length_window(0.65, 1.0, 1.0)
    assert window.high * 0.65 <= LONGEST and window.low <= window.high < config.WORD_COUNT_FLOOR
    # Otherwise the floor still holds.
    assert script_engine.length_window(0.4, 1.0, 1.0).low == 105  # 42 s at 0.4 s a word
    assert script_engine.length_window(0.5, 1.0, 1.0).low == config.WORD_COUNT_FLOOR      # 84 lifted to 85
