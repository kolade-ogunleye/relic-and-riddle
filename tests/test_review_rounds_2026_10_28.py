"""
tests/test_review_rounds_2026_10_28.py
=====================================
The rc68 review rounds (2026-10-28): every case the independent reviews found, pinned, so a later rule change can't
bring one back. Public text is rewritten by neutral_cues only where it speaks to the viewer; engagement bait is
caught as an instruction, never as a fact; credits name their sources and say the photos were adapted.
"""

import pytest

from src.script_engine import engagement_bait, facebook_photo_caption, neutral_cues

KEEP = ["lies beyond the Grand Gallery above it.", "The post below it marks the old border.", "the gallery above.",
        "the river down below.", "the chamber below is sealed", "The comments below it are wild.",
        "Comments below this post", "The thread below it.", "Monks held a discussion below in the crypt",
        "the photos above it show the vault", "30 m below the surface"]
CLEAN = {"Tell us in the comments below!": "Tell us in the comments!",
         "Comment down below what you think": "Comment what you think",
         "Comment below which theory you back.": "Comment which theory you back.",
         "Tell us below who built it.": "Tell us who built it.",
         "Comment below and tell us why.": "Comment and tell us why.",
         "Vote below or share it.": "Vote or share it.",
         "Sound off below and we'll read every one.": "Sound off and we'll read every one.",
         "Tell us in the comments below which one.": "Tell us in the comments which one.",
         "Join the discussion below.": "Join the discussion.",
         "See the photos below": "See the photos",
         "swipe right to see more": "swipe through to see more"}
BAIT = ["Comment YES if you knew this!", "Type AMEN", "Reply YES", "Comment FIRE for part two", "Comment ROME or GREECE.",
        "Comment ROME, or GREECE", 'Comment your answer: "ROME"', "Comment A or B", "Type A for Rome, B for Greece",
        "Comment B.", "Comment C if you think aliens.", "Comment C. Then wait.", "Now comment B.",
        "Just comment ROME or ATHENS.", "Please type A for Rome.", "So type A.", "Tag a friend who loves history",
        "Share this with 5 friends",
        # rc68 review 5: an emoji or "in the comments" after the reply
        "Reply YES 🔥", "Comment 1 🔥", "Would you go? Comment YES 🏺", "Comment YES ❤️ if you agree",
        "Comment YES in the comments", "Comment YES below", "Reply 1 in the comments!", "Comment A or B in the comments",
        "Comment A or B and tell us why", "Comment yes or no",
        # rc68 review 6: punctuation before the emoji, arrows, ", and tell us"
        "Comment YES! 👇", "Type 1. 🔥", "Comment YES ⬇️", "Reply 1 ⬇️", "Type A ⬇️", "Comment YES ↓",
        "Comment YES ⤵️", "Comment YES, and tell us why.",
        # rc68 review 7: lists of choices
        "Comment A, B or C", "Type 1, 2 or 3",
        # rc68 review 8: an ask after the choices, slashes, a list without "or"
        "Comment A or B if you agree", "Comment A or B for Rome", "Comment A or B to vote", "Reply A or B when you know",
        "Type A or B now", "Comment A, B or C if you know", "Comment 1, 2 or 3 to vote", "Comment 1 or 2 below",
        "Comment A/B", "Comment 1/2", "Comment A, B, C", "Type 1, 2, 3",
        # rc68 review 9: chained "or", quoted numbers, YES/NO
        "Comment 1 or 2 or 3", "Comment 1 or 2 or 3!", "Type 1 or 2 or 3 below", "Comment A or B or C!",
        'Comment "1" or "2"', "Comment 1 or “2” below", "Comment YES/NO", "Comment yes/no", "Comment 1 to vote",
        "Comment 1 or 2 If You Agree", "Comment A or B Below",
        "Comment 1 to Rome, 2 to Greece", "Comment YES to Rome or NO to Greece", "Type 1 for Rome, 2 for Greece",
        # rc68 review 11: Title Case asks, other joiners in a mapping
        "Comment 1 To Vote", "Comment YES To Agree", "Comment A Or B To Vote", "Comment 1 Or 2 To Vote",
        "Comment 1 To Agree", "Comment 1 to Rome and 2 to Greece", "Type YES to Rome and NO to Greece",
        "Comment 1 to Rome; 2 to Greece", "Comment 1 to Rome / 2 to Greece", "Comment 1 to Rome — 2 to Greece",
        "Comment 1 to Rome, comment 2 to Greece",
        # rc68 review 12: Title Case calls to action
        "Comment YES To Get Part 2", "Type YES To Unlock Part 2", "Comment 1 To See Part 2",
        "Reply YES To Receive The Guide", "Comment 1 To Support Rome", "Comment LINK To Get The Map"]
FACTS = ["Type O blood. Type AB is rarer.", "Comment DNA results confirmed it.", "Type A, for example, is rarer.",
         "Type A or type B personality?", "Type A personalities", "Comment A.D. 79 was the year",
         "Comment your guess: who built it, and why?", "Comment your answer: what would you have done?",
         "Reply your pick: the keepers or the sea?",
         # rc68 review 4
         "Type 1 diabetes was described by Aretaeus.", "Type 97 cipher machines were used by Japan's diplomats.",
         "Type 2 fimbriae were found.", "Reply 12 from the Admiralty arrived late.",
         "Comment 3 in the margin names the copyist.", "Type No. 5 was the cheapest brick.",
         "Type A or B blood made no difference to the plague.",
         # rc68 review 7: sideways arrows join facts
         "Type 1 ↔ Type 2 comparison", "Type 2 ⇒ insulin resistance.", "Type A ⬆ more common in Europe.",
         "Reply 12 → the Admiralty, 1901.", "Type 97 ⇄ Type 91 ciphers.", "Route: Type 2 ⬅ Type 1.",
         "In 1901, reply 2 → reply 3 took weeks.", "Type 1 ➡ Type 2 is the usual order.",
         # rc68 review 8: a noun after the last number
         "Type 1 or 2 diabetes was unknown to them.", "Type 1, 2 or 3 sites lie in Egypt.",
         "Type 2 or 3 cipher keys rotated daily.", "Type 12 or 14 ships", "Reply 12 or 13 from the Admiralty arrived late.",
         "Comment 3 or 4 in the margin names the copyist.", "Type 1 ➡️ Type 2", "Type A, B and O blood was unknown.",
         "Type 1/2 diabetes was unknown.", "Gates A, B or C were sealed in 1914, 1915 or 1916.",
         # rc68 review 9: "to" + a noun describes the reply
         "Reply 12 or 13 to the Admiralty arrived late.", "Reply 12 to the Admiralty arrived late.",
         "Comment 3 or 4 to the bishop survives.", "Reply 4, 5 to the Admiralty arrived.",
         "Type A or B or C blood was unknown.", "Type 1 or 2 or 3 diabetes was unknown.",
         # rc68 review 10: "to" + a name, number or demonstrative; Title Case after the choices
         "Reply 12 to Nelson arrived late.", "Reply 12 to Admiral Nelson was lost.", "Reply 4 to Parliament was blunt.",
         "Reply 3 to Rome never came.", "Reply 7 to Washington, dated 1777, survives.", "Reply 2 to this letter came in May.",
         "Type 1 to 2 years was the sentence.", "Reply 12 To Nelson", "Type 1 To 2 Years",
         "Type 1 Or 2 Diabetes Was Unknown", "Type 1 or 2 Diabetes in Ancient Egypt", "Type A Or B Blood Was Rare",
         "Type A or B or C Blood Was Unknown", "Type A or B Blood: The Discovery"]


@pytest.mark.parametrize("text", KEEP)
def test_facts_about_places_are_never_rewritten(text):
    assert neutral_cues(text) == text


@pytest.mark.parametrize("draft, expected", CLEAN.items())
def test_viewer_prompts_lose_their_direction(draft, expected):
    assert neutral_cues(draft) == expected
    assert "below" not in facebook_photo_caption(draft).lower()


@pytest.mark.parametrize("text", BAIT)
def test_bait_is_caught(text):
    assert engagement_bait(text)


@pytest.mark.parametrize("text", FACTS)
def test_facts_and_open_questions_are_not_bait(text):
    assert not engagement_bait(text)


def test_credits_name_partner_archives_say_adapted_and_replace_old_lines():
    from src.beat_images import CREDIT_PREFIX, credit_caption
    from src.credits import CREDIT_ADAPTED, caption_credit_line
    line = caption_credit_line({"images": [{"artist": "A", "license": "CC BY 4.0", "attribution_required": True}]})
    assert line.startswith(CREDIT_PREFIX) and "partner archives" in line and line.endswith(CREDIT_ADAPTED)
    old = "Story.\n\n📷 Images via Wikimedia Commons: A (CC BY 4.0)"
    new = credit_caption(old, {"images": [{"artist": "A", "license": "CC BY 4.0", "attribution_required": True}]}, [])
    assert new.count("📷") == 1 and "partner archives" in new                     # replaced, never doubled


def test_a_carousel_borrowing_the_reel_credit_says_adapted_once():
    import main
    from src.credits import CREDIT_ADAPTED
    from src.multi_format import CAROUSEL_ADAPTED
    payload = {"carousel_caption": "Look through.",
               "caption": "Reel.\n\n📷 Images via Wikimedia Commons and partner archives: A (CC BY 4.0)" + CREDIT_ADAPTED}
    caption, _ = main.PipelineCoordinator._carousel_post(payload, [])
    assert CREDIT_ADAPTED not in caption and caption.count(CAROUSEL_ADAPTED) == 1



def test_only_the_narrations_closing_sentence_is_checked_for_bait():
    """rc68 reviews 4-5: a fact-checked narration line never costs a verified script its place, but a closing ask
    that reaches viewers is still caught."""
    from src.models import ScriptPackage
    from src.script_engine import _bait_fields
    pkg = ScriptPackage.model_construct(title="T", narration_script="Type 1 diabetes was described early. Comment YES if you'd go.",
                                        caption="c", carousel_caption="", first_comment="", share_line="",
                                        poll=type("P", (), {"question": "q"})(), story_slides=[], carousel_slides=[])
    fields = dict(_bait_fields(pkg))
    assert fields["narration's closing sentence"] == "Comment YES if you'd go."
    assert "Type 1 diabetes" not in " ".join(fields.values())



@pytest.mark.parametrize("tail", [" " * 2000, "\n" * 500, " \t" * 800])
def test_a_long_blank_run_never_stalls_the_check(tail):
    """rc68 review 6: "Comment YES" + 250 spaces took 17 s; whitespace runs are collapsed before matching."""
    import time
    for head in ("Comment YES", "Type 1", "Comment ROME", "Comment A or B"):
        start = time.perf_counter()
        engagement_bait(head + tail)
        assert time.perf_counter() - start < 0.5, head
