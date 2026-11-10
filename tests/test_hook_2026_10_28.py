"""The first seconds (Meta's Creator assistant on the operator's Reel, 2026-10-28): "Lead with the mystery";
"move that top-down aerial shot to the 0:00 mark; show the scale immediately"; "the opening shot stays
relatively still for the first three seconds". And no engagement bait (Meta demotes "like this if", "share with
N friends", "tag a friend", "comment YES"). Topic names below are test data only."""
import json
from pathlib import Path

import pytest
from PIL import Image

from src import parallax, scene_planner as sp, script_engine
from src.models import ScriptPackage
from src.script_engine import (SHARE_FALLBACK, ScriptEngine, draft_problems, engagement_bait, hook_problems,
                               normalize_copy, share_line_problems, share_prompt, validate_structure)

# ------------------------------------------------------------------ the writer's first sentence
LABEL_FIRST = [
    # the posted Reel's opening, and development's own run of the same topic
    "This Massive Hindu Temple Was Not Built from the ground up.",
    "This massive temple was never built from the ground up; carvers excavated downward from the summit.",
    "This is the Voynich Manuscript, a book nobody can read.",
    "Meet the Antikythera Mechanism, an ancient analog computer.",
    "Here is the Richat Structure, seen from space.",
    "The Antikythera Mechanism is a 2,000-year-old analog computer.",
    "Trepanation is the oldest documented surgical procedure.",
    "These tunnels were dug mostly by limestone miners.",
    "Have you ever wondered how the Romans made concrete?",
]

# Openings for real topics in data/topics/topic_bank.json (title -> first sentence): the fact or the riddle first.
GOOD_HOOKS = {
    "The Kailasa Temple Monolith": "They carved this 100-foot temple from one rock—starting at the top.",
    "The Kailasa Temple Monolith (question)": "How do you carve a temple from the top down?",
    "The Kailasa Temple Monolith (scale first)": "This 100-foot temple was carved from one rock.",
    "The Unsolved Voynich Manuscript": "A 600-year-old book has defeated every attempt to read it.",
    "The Tunguska Event of 1908": "In 1908, something flattened 80 million trees and left no crater.",
    "The Dyatlov Pass Mystery": "In 1959, nine hikers cut their way out of their tent.",
    "The 4th-Century Lycurgus Cup": "A 1,600-year-old Roman cup glows green, then turns red.",
    "Operation Acoustic Kitty": "In the 1960s, the CIA reportedly tried turning cats into microphones.",
    "The Flannan Isle Lighthouse Vanishing": "In December 1900, three lighthouse keepers vanished without a trace.",
    "The Richat Structure: The Eye of the Sahara": "Why does a 40-kilometre bullseye stare up from the Sahara?",
}


@pytest.mark.parametrize("first", LABEL_FIRST)
def test_a_label_first_opening_is_sent_back(first):
    problems = hook_problems(first + " More narration follows here.")
    assert problems and all(p.startswith(script_engine.HOOK_PROBLEM) for p in problems), first
    assert "label" in problems[0] or "Have you ever" in first


@pytest.mark.parametrize("title", sorted(GOOD_HOOKS))
def test_an_opening_on_the_fact_or_the_riddle_passes(title):
    assert hook_problems(GOOD_HOOKS[title] + " The rest of the story follows.") == [], title


def test_a_long_first_sentence_is_sent_back_even_without_a_label():
    long = ("In 1963, a man renovating his house in Cappadocia knocked through a wall and found a tunnel. "
            "It led down.")
    problems = hook_problems(long)
    assert len(problems) == 1 and "17-word" in problems[0]
    # A dash joins no words: "rock—starting" is two of the twelve.
    assert len(script_engine._spoken_words(GOOD_HOOKS["The Kailasa Temple Monolith"])) == script_engine.HOOK_MAX_WORDS


def test_the_hook_problems_file_under_the_hook_class_not_the_length_class():
    from src import improve
    for problem in hook_problems(LABEL_FIRST[1] + " " + "word " * 3):
        assert improve.classify(problem) == "script.hook", problem


def test_the_writer_is_told_the_rule():
    assert "Lead with the mystery" in script_engine.SYSTEM_PROMPT
    assert f"at most {script_engine.HOOK_MAX_WORDS} words" in script_engine.SYSTEM_PROMPT
    assert "{HOOK_WORDS}" not in script_engine.SYSTEM_PROMPT


def _pkg(curated_payload, first: str, **extra) -> ScriptPackage:
    narration = curated_payload["narration_script"]
    rest = narration.split(". ", 1)[1]
    return ScriptPackage.model_validate(dict(curated_payload, narration_script=f"{first} {rest}",
                                             hook_sentence=first, **extra))


def test_hook_quality_is_separate_from_basic_structure_validation(monkeypatch, curated_payload):
    monkeypatch.setattr(script_engine, "spoken_length_problems", lambda pkg: [])
    weak = _pkg(curated_payload, LABEL_FIRST[0])
    assert not validate_structure(weak)  # final verification separately enforces hook quality
    assert any(p.startswith(script_engine.HOOK_PROBLEM) for p in draft_problems(weak))
    assert draft_problems(_pkg(curated_payload, GOOD_HOOKS["The Dyatlov Pass Mystery"])) == []


def _engine(monkeypatch, drafts):
    monkeypatch.setattr(script_engine, "_ready", lambda: True)
    monkeypatch.setattr(script_engine, "spoken_length_problems", lambda pkg: [])
    engine = ScriptEngine()
    monkeypatch.setattr(engine, "_sheet", lambda topic: None)
    monkeypatch.setattr(engine, "_finalize", lambda pkg, topic, **k: {"narration_script": pkg.narration_script})
    feedbacks = []
    queue = list(drafts)
    monkeypatch.setattr(engine, "_generate_with_claude",
                        lambda topic, feedback: feedbacks.append(feedback) or queue.pop(0))
    return engine, feedbacks


def test_a_weak_opening_alone_requires_a_corrected_draft(monkeypatch, curated_payload):
    weak = _pkg(curated_payload, LABEL_FIRST[0])
    good = _pkg(curated_payload, GOOD_HOOKS["The Dyatlov Pass Mystery"])
    engine, feedbacks = _engine(monkeypatch, [weak, good])
    out = engine.generate_script({"id": "t", "title": "T"})
    assert len(feedbacks) == 2 and out["narration_script"].startswith(GOOD_HOOKS["The Dyatlov Pass Mystery"])


def test_a_rewrite_for_another_fault_is_told_to_fix_the_opening_too(monkeypatch, curated_payload):
    weak_and_long_title = _pkg(curated_payload, LABEL_FIRST[0], title="x" * 70)
    good = _pkg(curated_payload, GOOD_HOOKS["The Dyatlov Pass Mystery"])
    engine, feedbacks = _engine(monkeypatch, [weak_and_long_title, good])
    out = engine.generate_script({"id": "t", "title": "T"})
    assert len(feedbacks) == 2 and "Title is 70 characters" in feedbacks[1]
    assert script_engine.HOOK_PROBLEM + "the first sentence opens on a label" in feedbacks[1]
    assert out["narration_script"].startswith("In 1959")


# ------------------------------------------------------------------ the first caption burst
def test_the_first_caption_burst_is_the_first_sentences_opening_words():
    """Captions follow the narration word for word: the first burst is the first sentence's first words, so
    the writer's rule is what puts the strongest words on screen first ("This Massive Hindu" was exactly that
    burst on the posted Reel)."""
    from src.video_composer import partition_words_grammatical
    def bursts(text):
        words = [{"word": w, "start": i * 0.3, "dur": 0.3} for i, w in enumerate(text.split())]
        return [" ".join(x["word"] for x in b) for b in partition_words_grammatical(words)]
    assert bursts("This Massive Hindu Temple Was Not Built from the ground up.")[0].startswith("This Massive")
    assert bursts(GOOD_HOOKS["The Kailasa Temple Monolith"])[0].startswith("They carved")


# ------------------------------------------------------------------ engagement bait
BAIT = [
    "Share this with 5 friends who love history!",
    "Send this to ten people, and follow for more.",
    "Share if you agree.",
    "Tag a friend who loves maps.",
    "Tag someone who would go down there.",
    "Like this if you'd go down there.",
    "Hit like if you knew this.",
    "Double tap if this surprised you.",
    "Comment YES if you'd go in.",
    "Agree? Comment yes.",
    "Type 1 if you'd go in, 2 if you'd stay out.",
    "Drop a 🔥 if this blew your mind.",
    "Vote with a ❤️ or a 😮.",
    "React with 😮 if you knew.",
    "Like for planned, love for lucky.",
]
NOT_BAIT = [
    "Send this to the codebreaker you know, and follow for the next relic.",   # the prompt's own example
    SHARE_FALLBACK,
    "What would you have tried first? Tell us in the comments.",
    "Comment your best guess.",
    "Which would you trust: the records or the ruins?",
    "The Romans would vote with stones.",
    "A type II supernova lit the sky.",
    "Its price tag was the size of a city's budget.",
    "Workers would send it to 10,000 households.",
    "They liked it if the gears turned smoothly.",
]


@pytest.mark.parametrize("text", BAIT)
def test_bait_is_found(text):
    assert engagement_bait(text), text


@pytest.mark.parametrize("text", NOT_BAIT)
def test_a_genuine_ask_or_a_fact_is_not_bait(text):
    assert engagement_bait(text) == [], text


def test_the_share_line_stays_allowed_but_can_never_become_share_with_n_friends():
    ours = "Send this to the codebreaker you know, and follow for the next relic."
    assert share_line_problems(ours) == [] and share_prompt({"share_line": ours}) == ours
    for bait in ("Share this with 5 friends who love codes, and follow for the next relic.",
                 "Tag the codebreaker you know, and follow for the next relic."):
        assert any("engagement bait" in p for p in share_line_problems(bait)), bait
        assert share_prompt({"share_line": bait}) == SHARE_FALLBACK               # the captions and the card
        assert script_engine.end_card_share({"share_line": bait})[1] is True      # the end card's fallback


def test_bait_in_any_public_text_is_a_structure_problem(curated_payload):
    for field, value in (("caption", "Ancient wonder.\n\nComment YES if you'd visit!\n\n#a #b #c"),
                         ("first_comment", "Tag a friend who'd go in."),
                         ("carousel_caption", "Like this if you knew.\n\n#a #b #c")):
        pkg = ScriptPackage.model_validate(dict(curated_payload, **{field: value}))
        problems = [p for p in validate_structure(pkg) if "Engagement bait" in p]
        assert problems and field in problems[0], field
    assert not [p for p in validate_structure(ScriptPackage.model_validate(curated_payload)) if "bait" in p]


def test_normalize_copy_drops_a_baiting_share_line_for_the_plain_one(curated_payload):
    pkg = ScriptPackage.model_validate(dict(curated_payload, share_line="Share this with 3 friends and follow us."))
    fixed = normalize_copy(pkg)
    assert fixed.share_line is None and share_prompt(fixed.model_dump()) == SHARE_FALLBACK
    assert not [p for p in validate_structure(fixed) if "share_line" in p]


# ------------------------------------------------------------------ the opening shot
def _pool(tmp_path, entries):
    """Photos with a credits.json beside them: (file, vision reason, extra fields)."""
    images = []
    for name, reason, extra in entries:
        Image.new("RGB", (60, 40)).save(tmp_path / name)
        images.append({"file": name, "title": f"File:{name}", "width": 2400, "height": 1800,
                       "vision_review": {"shows_topic": "yes", "usable": True, "safe": True, "duplicate_of": None,
                                         "reason": reason, "kept": True, **extra.pop("verdict", {})}, **extra})
    (tmp_path / "credits.json").write_text(json.dumps({"images": images}), encoding="utf-8")
    sp._folder_credits.cache_clear()
    return [tmp_path / name for name, _, _ in entries]


def test_the_hook_takes_the_widest_recorded_view(tmp_path):
    stills = _pool(tmp_path, [
        ("01_commons.jpg", "Shows a small shrine inside the complex.", {}),
        ("02_commons.jpg", "Detailed close-up of the carved frieze.", {}),
        ("03_commons.jpg", "Wide aerial view of the whole complex cut into the cliff.", {}),
        ("04_commons.jpg", "Elevated view of the courtyard.", {"verdict": {"people_prominent": True}}),  # crowded
    ])
    assert sp.hook_shot(stills) == stills[2]
    # Planned elsewhere, it trades places with the opening; the set of pictures is unchanged.
    assert sp.open_on_scale([stills[0], stills[1], stills[2]], stills) == [stills[2], stills[1], stills[0]]
    # A motion clip or footage at the opening already moves and stays.
    clip = tmp_path / "footage_01.mp4"
    assert sp.open_on_scale([clip, stills[0]], stills) == [clip, stills[0]]


def test_no_recorded_scale_leaves_the_plan_alone(tmp_path):
    stills = _pool(tmp_path, [("01_commons.jpg", "Shows a relief.", {}), ("02_commons.jpg", "Shows a pillar.", {})])
    assert sp.hook_shot(stills) is None
    assert sp.open_on_scale([stills[1], stills[0]], stills) == [stills[1], stills[0]]


def test_a_weak_or_unchecked_photo_never_opens(tmp_path):
    stills = _pool(tmp_path, [
        ("01_commons.jpg", "Shows a relief.", {}),
        ("02_commons.jpg", "Wide aerial view of a different site.", {"verdict": {"shows_topic": "no"}}),
    ])
    assert sp.hook_shot(stills) is None


def test_the_planned_reel_opens_on_the_scale_shot(monkeypatch, tmp_path):
    monkeypatch.setattr(sp, "claude_available", lambda: False)
    monkeypatch.setattr(sp.free_llm, "enabled", lambda: False)
    stills = _pool(tmp_path, [
        ("01_commons.jpg", "Shows a small shrine inside the complex.", {}),
        ("02_commons.jpg", "Shows the carved pillars.", {}),
        ("03_commons.jpg", "Panoramic top-down view of the entire temple.", {}),
    ])
    beats = [{"start": 0, "end": 3, "text": "a"}, {"start": 3, "end": 6, "text": "b"}, {"start": 6, "end": 9, "text": "c"}]
    scenes, _, _ = sp.plan_scenes(beats, stills)
    assert scenes[0] == stills[2] and sorted(scenes) == sorted(stills)


def test_panoramio_is_a_photo_site_not_a_view():
    entry = {"title": "File:Temple - panoramio (4).jpg", "vision_review": {"shows_topic": "yes", "usable": True,
                                                                           "safe": True, "reason": "Shows a pillar."}}
    assert sp.scale_score(entry) < 0.5


# ------------------------------------------------------------------ the opening moves from frame 0
def test_the_hook_move_starts_at_frame_zero_and_the_others_are_unchanged():
    frames = int(round(4.2 * 30))
    step = 1 / (frames - 1)
    assert parallax.ease(0.0, lead=True) == 0.0 and parallax.ease(1.0, lead=True) == 1.0
    assert parallax.ease(step, lead=True) > 1.9 * step            # full speed at once, no hold
    assert parallax.ease(step) < 0.05 * step                      # smoothstep starts at rest (the old hook)
    assert parallax.ease(0.5) == 0.5 and parallax.PRESETS["push"] == (0.10, 0.0, 0.0)
    assert parallax.PRESETS[parallax.HOOK_PRESET][0] > parallax.PRESETS["push"][0]


def test_only_the_opening_still_gets_the_hook_move(monkeypatch, tmp_path):
    from src.lineage import record_derived
    monkeypatch.setattr(parallax, "available", lambda: True)
    monkeypatch.setattr(parallax, "_session", lambda: None)
    model = tmp_path / "model.onnx"
    model.write_bytes(b"model")
    monkeypatch.setattr(parallax, "MODEL_PATH", model)
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    presets = {}

    def fake_render(image, out, duration, preset, variant=0):
        presets[out.name] = preset
        out.write_bytes(preset.encode())
        record_derived(out, [image], "parallax", {"preset": preset})
        return out
    monkeypatch.setattr(parallax, "render_clip", fake_render)
    parallax.render_scenes([a, b, a], [3.0, 3.0, 3.0], tmp_path / "px", 0.4)
    assert presets["scene_01.mp4"] == parallax.HOOK_PRESET
    assert parallax.HOOK_PRESET not in (presets["scene_02.mp4"], presets["scene_03.mp4"])
    assert presets["scene_03.mp4"] != "push"                    # the hook photo's repeat gets another move
    # The composer's transitions still read the opening as a push.
    assert parallax.preset_for(0, 3) == "push"


def test_an_ai_motion_clip_opening_gets_a_push_from_frame_zero():
    from src import video_composer as vc
    assert vc.is_ai_motion_clip(Path("motion_01.mp4"))
    assert not vc.is_ai_motion_clip(Path("hook_motion.mp4")) and not vc.is_ai_motion_clip(Path("footage_01.mp4"))
    zoom = lambda on: eval(vc.HOOK_CLIP_ZOOM, {"min": min}, {"on": on})  # noqa: E731
    assert zoom(0) == 1.0 and zoom(1) > 1.0 and zoom(10_000) == 1.24
    f = vc.hook_clip_filter(0, 1080, 1920)
    assert "zoompan=z='" + vc.HOOK_CLIP_ZOOM + "'" in f and ":d=1:" in f and f.endswith("[v0]")



def test_capitals_after_type_or_comment_are_bait_only_as_an_instruction():
    """rc68 review, 2026-10-28: "Type AB is rarer" and "Comment DNA results confirmed it" are facts."""
    from src.script_engine import engagement_bait
    for fact in ("Type O blood. Type AB is rarer.", "Comment DNA results confirmed it."):
        assert not engagement_bait(fact), fact
    for bait in ("Comment YES if you knew this!", "Type AMEN", "Reply YES", "Comment FIRE for part two"):
        assert engagement_bait(bait), bait



def test_choice_bait_with_capitals_or_letters_is_caught():
    """rc68 review 2, 2026-10-28: "Comment ROME or GREECE" and "Comment A or B" passed the narrowed pattern."""
    from src.script_engine import engagement_bait
    for bait in ("Comment ROME or GREECE.", "Comment ROME, or GREECE", "Comment your answer: ROME or GREECE?",
                 "Comment A or B", "Type A for Rome, B for Greece", "Comment B."):
        assert engagement_bait(bait), bait
    for fact in ("Type AB is rarer.", "Type O blood. Type AB is rarer.", "Comment DNA results confirmed it."):
        assert not engagement_bait(fact), fact
