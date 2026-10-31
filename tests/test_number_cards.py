"""A number card echoes the narration (production, Antikythera, 2026-10-24: the gear count is a disputed
fact with 35/30 in one source and 29 in another, so a card and a narration could each trace to the sheet
and still disagree on screen). The automated video check samples a beat late, after an animated card has
settled; its midpoint frame read a count-up in progress as a contradiction."""

from src import video_qa
from src.trace_check import card_numbers_off_narration

NARRATION = ("By one estimate, computer X-ray tomography scans indicate the original mechanism contained 35 "
             "meshing bronze gears, 30 of which remain visible today. The remnants were retrieved in 1901.")


def test_a_card_figure_the_narration_never_says_is_a_finding():
    cards = [{"value": "Estimated 35", "label": "Original meshing bronze gears"},
             {"value": "30", "label": "Bronze gears visible today, one estimate"}]
    assert card_numbers_off_narration({"narration_script": NARRATION, "stat_cards": cards}) == []
    cards[1]["value"] = "29"
    assert card_numbers_off_narration({"narration_script": NARRATION, "stat_cards": cards}) == [
        "number card: 29 (Bronze gears visible today, one estimate) is not a figure the narration says"]
    assert card_numbers_off_narration({"narration_script": NARRATION}) == []         # no cards: nothing to echo


def test_the_video_check_looks_late_in_a_beat_never_before_its_middle():
    assert video_qa.sample_time(23.0, 28.0) == 27.0                 # four fifths through a 5 s beat
    assert video_qa.sample_time(10.0, 10.0) == 10.0                 # a degenerate beat
    assert video_qa.sample_time(0.0, 1.0) == 0.8
