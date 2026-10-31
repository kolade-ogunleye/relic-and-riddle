"""Run audit 2026-10-29 (docs/reviews/run-audit-2026-10-29), items 4, 5, 9 and 10: false positives in the script's
fact and trace checks.
4. Production, Derinkuyu (rc71): a regenerated run's caption carried the last render's credit lines; their 📷/🎬 were
   counted as caption emoji, the copy edit stripped them, and the photo titles ("... 9843 Nevit") and Commons curids
   (31074216, printed 3.10746e+07) were then checked as untraced figures.
5. "number card: 8 (Recorded subterranean levels) is not a figure the narration says" over "eight to 18 levels".
9. A draft just past the plan's ceiling (51.6-53 s) gets one trim attempt and then passes: takes are kept to 54.5 s.
10. "Centuries of everyday life hid the massive world waiting below." was forced to untraced for its "centuries".
The LLM is stubbed throughout."""
import pytest

from src import credits, fact_sheet, script_engine, trace_check
from src.models import ScriptPackage
from src.script_engine import ScriptEngine, _CopyEdit, spoken_length_problems, validate_structure

SHEET = {"topic_id": "t1", "sources": [{"title": "X", "url": "https://en.wikipedia.org/wiki/X", "tier": 2}],
         "facts": [{"id": "f1", "statement": "Derinkuyu has 18 levels, by one estimate; 8 are open.", "kind": "other",
                    "disputed": False, "hedge": "", "evidence": []}]}

PHOTO = ("📷 Nevit Dilmen | File:Derinkuyu Underground City 9843 Nevit.JPG | CC BY-SA 3.0 | "
         "https://commons.wikimedia.org/w/index.php?curid=31074216 | https://creativecommons.org/licenses/by-sa/3.0")
CREDITS = [credits.SHARE_ALIKE_NOTICE, PHOTO,
           PHOTO.replace("9843", "9913").replace("31074216", "31074290"),
           "📷 Николай Максимович | File:Derinkuyu Underground City, Nevşehir Province, Turkey - panoramio.jpg | CC BY 3.0 | "
           "https://commons.wikimedia.org/wiki/File:Derinkuyu_Underground_City.jpg | https://creativecommons.org/licenses/by/3.0",
           PHOTO.replace("9843", "9871").replace("31074216", "31074255"),
           "🎵 Music: “Ancient Halls” by A. Composer (CC BY 4.0)"]


def _credited(caption: str) -> str:
    return caption.rstrip() + "\n\n" + "\n".join(CREDITS)


def _caption_lines(text: str):
    return [ln for ln in text.splitlines() if ln.strip()]


# ------------------------------------------------------------------ item 4: the credit lines
def test_the_credit_lines_emoji_do_not_count_as_the_captions(curated_payload):
    pkg = ScriptPackage.model_validate(dict(curated_payload, caption=_credited(curated_payload["caption"])))
    assert script_engine.EMOJI_RE.findall(pkg.caption).__len__() > 4               # 6 credit lines' 📷/🎬/🎵 alone
    assert not [p for p in validate_structure(pkg) if p.startswith("Caption uses")]
    own = curated_payload["caption"].rstrip() + " 🏛️ 🕯️ 🔦 ⛏️ 🗿"                   # the writer's own emoji still count
    assert [p for p in validate_structure(ScriptPackage.model_validate(dict(curated_payload, caption=_credited(own))))
            if p.startswith("Caption uses")]


def test_split_and_restore_keep_the_credits_in_their_order():
    text, held = credits.split_attribution("A line.\n\n#Tag #Two\n\n" + "\n".join(CREDITS))
    assert text == "A line.\n\n#Tag #Two" and held == CREDITS
    payload, kept = credits.hold_attribution({"caption": "A line.\n\n" + "\n".join(CREDITS), "title": "T"})
    assert payload["caption"] == "A line." and kept == {"caption": CREDITS}
    restored = credits.restore_attribution({"caption": "An edited line.\n\n#Tag"}, kept)
    assert _caption_lines(restored["caption"]) == ["An edited line.", "#Tag", *CREDITS]


def test_a_credit_line_without_its_emoji_is_still_a_credit_and_reads_no_figures():
    stripped = PHOTO.removeprefix("📷 ")
    assert credits.is_attribution_line(stripped)
    assert not credits.is_attribution_line("Nevit Dilmen photographed 18 levels in 1963.")
    assert fact_sheet.numbers_in(stripped) == []                                    # 9843, 3.0, 31074216, 3.0 before
    assert fact_sheet.numbers_in("licensed CC BY-SA 4.0: https://creativecommons.org/licenses/by-sa/4.0/") == []
    assert fact_sheet.numbers_in("CC0 1.0, GFDL 1.2 and File:Map 1513.png") == []
    assert fact_sheet.numbers_in("Found in 1963, 85 m deep, 20,000 people, 8th century") == [1963, 85, 20000, 8]


def test_the_number_check_reads_no_figures_in_credits_that_lost_their_emoji():
    caption = "Beneath Cappadocia, 18 levels by one estimate.\n\n" + "\n".join(ln.lstrip("📷🎬🎵 ") for ln in CREDITS)
    payload = {"title": "T", "narration_script": "Eighteen levels.", "caption": caption, "first_comment": ""}
    assert trace_check.untraced_numbers(payload, SHEET) == []


@pytest.fixture
def engine(monkeypatch, curated_payload):
    engine = ScriptEngine()
    monkeypatch.setattr(script_engine, "_ready", lambda: True)
    monkeypatch.setattr(script_engine.free_llm, "enabled", lambda: False)
    monkeypatch.setattr(engine, "_sheet", lambda topic: SHEET)
    monkeypatch.setattr(engine, "_sheet_context", lambda sheet: ("notes", []))
    monkeypatch.setattr(engine, "_with_graphics", lambda core, notes, retrieved, feedback: core)
    monkeypatch.setattr(engine, "_finalize", lambda pkg, topic, **k: dict(pkg.model_dump(), **k))
    monkeypatch.setattr(script_engine, "hook_problems", lambda narration: [])
    monkeypatch.setattr(script_engine.improve, "note_problems", lambda problems, source: None)
    monkeypatch.setattr(script_engine.improve, "note", lambda *a, **k: None)
    return engine


def test_a_regenerated_runs_credits_reach_no_writer_and_come_back_after(monkeypatch, engine, curated_payload):
    seen = []
    monkeypatch.setattr(script_engine, "draft_problems", lambda pkg: [])
    monkeypatch.setattr(engine, "_revise_surgically", lambda previous, findings, sheet: seen.append(previous["caption"])
                        or ScriptPackage.model_validate(previous))
    previous = dict(curated_payload, caption=_credited(curated_payload["caption"]), fact_sheet=True)
    out = engine.generate_script({"id": "t1", "title": "T"}, revision_notes="'a claim here' (overstated)",
                                 previous=previous)
    assert seen and not any(ln in seen[0] for ln in CREDITS)                         # the reviser never saw them
    assert _caption_lines(out["caption"])[-len(CREDITS):] == CREDITS                  # appended again, in order
    assert sum(out["caption"].count(ln) for ln in CREDITS) == len(CREDITS)            # once each


def test_the_copy_edit_sees_the_written_caption_only_and_keeps_the_credits(monkeypatch, engine, curated_payload):
    first = ("Beneath an ordinary house in Cappadocia, a renovation broke through into a city that once sheltered "
             "thousands of people, their animals and their stores")            # one sentence over 125 characters
    rest = "\n".join(curated_payload["caption"].splitlines()[1:])
    pkg = ScriptPackage.model_validate(dict(curated_payload, caption=_credited(first + "\n" + rest)))
    problems = [p for p in validate_structure(pkg) if p.startswith("Caption's first line is")]
    assert problems
    prompts = []

    def call(system, prompt, schema, **k):
        assert schema is _CopyEdit
        prompts.append(prompt)
        return _CopyEdit(caption="A renovation broke into a hidden city. 🏛️\n" + rest)
    monkeypatch.setattr(script_engine, "structured_call", call)
    fixed = engine._edit_copy(pkg, problems, SHEET)
    assert prompts and not any(ln in prompts[0] for ln in CREDITS) and "📷" not in prompts[0]
    assert fixed is not None and _caption_lines(fixed.caption)[-len(CREDITS):] == CREDITS


# ------------------------------------------------------------------ item 5: number cards read number words
def test_a_card_figure_the_narration_says_in_words_is_said():
    narration = "Beneath Cappadocia, by one estimate, eight to 18 levels were dug into the soft rock."
    card = {"value": "8", "label": "Recorded subterranean levels"}
    assert trace_check.card_numbers_off_narration({"narration_script": narration, "stat_cards": [card]}) == []
    assert trace_check.card_numbers_off_narration({"narration_script": narration,
                                                   "stat_cards": [dict(card, value="9")]}) == [
        "number card: 9 (Recorded subterranean levels) is not a figure the narration says"]


# ------------------------------------------------------------------ item 10: uncounted time words are framing
@pytest.mark.parametrize("claim", ["Centuries of everyday life hid the massive world waiting below.",
                                   "For decades, nobody looked beneath the floor.",
                                   "A centuries-old secret lay under the town."])
def test_an_uncounted_span_of_time_is_framing(claim):
    assert trace_check._empirical_rhetoric(claim) is False


@pytest.mark.parametrize("claim", ["The tunnels were widened in the 8th century.",
                                   "Three centuries later, the city was abandoned.",
                                   "Builders extended it in the eighth century.",
                                   "A century later, the shafts were sealed.",
                                   "Concrete that outlasts modern mixes."])
def test_a_counted_or_dated_span_is_still_a_claim(claim):
    assert trace_check._empirical_rhetoric(claim) is True


def test_framing_traced_without_a_fact_stays_traced():
    report = trace_check.TraceReport(claims=[trace_check.TracedClaim(
        claim="Centuries of everyday life hid the massive world waiting below.", where="narration",
        claim_type="rhetorical_framing", verdict="traced", note="framing")])
    assert trace_check._validate_trace(report, SHEET).claims[0].verdict == "traced"


# ------------------------------------------------------------------ item 9: a near miss gets one trim, then passes
PER_WORD = 0.5   # seconds a word at the measurement voice: 104 words read 52.0 s, 108 words 54.0 s


@pytest.fixture
def measured(monkeypatch):
    monkeypatch.setattr(script_engine.audio_engine, "measure_spoken_seconds",
                        lambda text, speed=1.0: len(text.split()) * PER_WORD)
    monkeypatch.setattr(script_engine, "_pace_window", lambda: (1.0, 1.0))
    monkeypatch.setattr(script_engine, "fit_speed", lambda text, seconds=None: None)
    monkeypatch.setattr(script_engine, "record_word_pace", lambda *a, **k: None)
    script_engine._near_misses_kept.clear()
    yield
    script_engine._near_misses_kept.clear()
    script_engine.begin_length_history()


def _draft(payload, words):
    closing = payload["closing_sentence"]
    body = " ".join(f"w{i}" for i in range(words - len(closing.split())))
    return ScriptPackage.model_validate(dict(payload, narration_script=f"{body}. {closing}"))


def test_a_kept_near_miss_passes_the_length_check_and_nothing_further_does(measured, curated_payload):
    assert script_engine.NEAR_MISS_CEILING == 53.0
    near, far = _draft(curated_payload, 104), _draft(curated_payload, 107)          # 52.0 s and 53.5 s
    assert script_engine.near_miss_seconds(spoken_length_problems(near)) == 52.0
    assert script_engine.near_miss_seconds(spoken_length_problems(far)) is None
    script_engine.keep_near_miss(near, 52.0)
    script_engine.keep_near_miss(far, 53.5)
    assert spoken_length_problems(near) == []
    assert spoken_length_problems(far)                                               # past 53 s: never kept


def _length_engine(monkeypatch, engine):
    for name in ("validate_structure", "story_problems", "headline_problems"):
        monkeypatch.setattr(script_engine, name, lambda *a, **k: [])


def test_a_draft_just_past_the_ceiling_is_trimmed_once_then_kept(monkeypatch, measured, engine, curated_payload):
    _length_engine(monkeypatch, engine)
    written, edits = [], []
    monkeypatch.setattr(engine, "_generate_with_claude", lambda topic, feedback: written.append(1) or
                        _draft(curated_payload, 105))                                # 52.5 s
    monkeypatch.setattr(engine, "_revise_surgically", lambda previous, findings, sheet: edits.append(findings) or
                        _draft(curated_payload, 104))                                # the trim reaches 52.0 s only
    out = engine.generate_script({"id": "t1", "title": "T"})
    assert len(written) == 1 and 1 <= len(edits) <= 2 and len(out["narration_script"].split()) == 104
    assert spoken_length_problems(ScriptPackage.model_validate(out)) == []           # the run's final check agrees


def test_a_failed_trim_keeps_the_near_miss_draft_as_written(monkeypatch, measured, engine, curated_payload):
    _length_engine(monkeypatch, engine)
    written = []
    monkeypatch.setattr(engine, "_generate_with_claude", lambda topic, feedback: written.append(1) or
                        _draft(curated_payload, 106))                                # 53.0 s
    monkeypatch.setattr(engine, "_revise_surgically", lambda previous, findings, sheet: None)
    out = engine.generate_script({"id": "t1", "title": "T"})
    assert len(written) == 1 and len(out["narration_script"].split()) == 106


def test_a_draft_further_past_the_ceiling_is_still_rewritten(monkeypatch, measured, engine, curated_payload):
    _length_engine(monkeypatch, engine)
    written = []
    monkeypatch.setattr(engine, "_generate_with_claude", lambda topic, feedback: written.append(1) or
                        _draft(curated_payload, 108 if len(written) == 1 else 100))  # 54.0 s, then 50.0 s
    monkeypatch.setattr(engine, "_revise_surgically", lambda previous, findings, sheet: None)
    out = engine.generate_script({"id": "t1", "title": "T"})
    assert len(written) == 2 and len(out["narration_script"].split()) == 100
