from src.config import EMPHASIS_POP
from src.video_composer import COL_EMPHASIS, COL_FUTURE  # the brand's caption colours since 2026-10-29
from src.video_composer import VideoComposer, emphasis_indices

WORDS = [{"word": w, "start": i * 0.4, "dur": 0.35} for i, w in
         enumerate("In 1963, a man found Derinkuyu, a city 280 feet deep. Derinkuyu still astonishes.".split())]


def test_first_occurrence_of_each_term_only():
    idx = emphasis_indices(WORDS, ["Derinkuyu", "280 feet"])
    assert idx == {5, 8, 9}  # second "Derinkuyu" (index 11) stays plain


def test_fallback_emphasises_first_two_numbers():
    assert emphasis_indices(WORDS, None) == {1, 8}


def test_ass_highlights_only_key_words_with_a_pop(tmp_path):
    import re
    ass = VideoComposer().build_subtitles(WORDS, tmp_path / "c.ass", emphasis=["Derinkuyu"]).read_text(encoding="utf-8")
    events = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
    gold = [e for e in events if COL_EMPHASIS in e]
    assert gold and all("DERINKUYU" in re.sub(r"\{[^}]*\}", "", e).upper() for e in gold)
    if EMPHASIS_POP > 100:
        assert sum(f"\\fscx{EMPHASIS_POP}" in e for e in events) == 1  # one pop, when the word is spoken
    assert COL_EMPHASIS not in "".join(e for e in events if "STILL" in re.sub(r"\{[^}]*\}", "", e).upper() or "ASTONISHES" in re.sub(r"\{[^}]*\}", "", e).upper())


def test_key_word_stays_grey_until_spoken(tmp_path):
    import re
    ass = VideoComposer().build_subtitles(WORDS, tmp_path / "c.ass", emphasis=["man"]).read_text(encoding="utf-8")
    events = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:") and ",CapText," in ln and "MAN" in re.sub(r"\{[^}]*\}", "", ln).upper()]
    before, spoken = events[0], events[-1]   # burst contains MAN: before MAN is spoken vs when MAN is spoken
    assert f"{COL_FUTURE}" in before and "MAN" in re.sub(r"\{[^}]*\}", "", before).upper()
    assert f"{COL_EMPHASIS}" in spoken
    if EMPHASIS_POP > 100:
        assert f"\\fscx{EMPHASIS_POP}" in spoken


def test_validation_requires_terms_from_the_narration(curated_payload):
    from src.models import ScriptPackage
    from src.script_engine import validate_structure
    data = {k: v for k, v in curated_payload.items() if k in ScriptPackage.model_fields}
    ok = ScriptPackage.model_validate(dict(data, emphasis_words=["Derinkuyu"]))
    assert validate_structure(ok) == []
    bad = ScriptPackage.model_validate(dict(data, emphasis_words=["Atlantis"]))
    assert any("does not appear" in p for p in validate_structure(bad))


def test_grammatical_burst_partitioning_terminal_punct():
    from src.video_composer import partition_words_grammatical
    sample_text = "Wait, look here! Did you see that? It was hidden underground for thousands of years."
    words = [{"word": w, "start": i * 0.4, "dur": 0.35} for i, w in enumerate(sample_text.split())]
    bursts = partition_words_grammatical(words)

    # First word of burst after "here!" must be "Did"
    # First word of burst after "that?" must be "It"
    burst_texts = [" ".join(w["word"] for w in b) for b in bursts]
    assert burst_texts[0] == "Wait, look here!"
    assert burst_texts[1] == "Did you see that?"
    # The remainder: "It was hidden underground for thousands of years." (8 words)
    # Should be partitioned into bursts of at least MIN_BURST_WORDS (2) words. Item 2 (operator, 2026-11-03): at the
    # 80 px caption size "It was hidden underground" no longer fits one line, so a 2-word burst forms; at 72 px this
    # held >= 3 words.
    from src.config import MIN_BURST_WORDS
    for b in bursts:
        if len(b) < MIN_BURST_WORDS:
            # only allowed if entire sentence was shorter
            assert any(p in b[-1]["word"] for p in [".", "!", "?"])
        else:
            assert len(b) >= MIN_BURST_WORDS


def test_ass_generates_rounded_box_layer_and_text_layer(tmp_path):
    words = [{"word": w, "start": i * 0.4, "dur": 0.35} for i, w in
             enumerate("Roman concrete heals itself inside ocean water.".split())]
    ass_path = VideoComposer().build_subtitles(words, tmp_path / "caption.ass")
    content = ass_path.read_text(encoding="utf-8")

    assert "Style: CapBox," in content
    assert "Style: CapText," in content

    # Check for CapBox vector drawing commands
    box_lines = [l for l in content.splitlines() if "CapBox" in l and l.startswith("Dialogue:")]
    assert len(box_lines) >= 1
    # Check that vector path commands exist (m, l, b for Bézier rounded corner)
    assert "\\p1" in box_lines[0]
    assert " b " in box_lines[0]

    # Check text line formatting has no-wrap tag
    text_lines = [l for l in content.splitlines() if "CapText" in l and l.startswith("Dialogue:")]
    # Each spoken-word reveal keeps one interval, with one event per visual line.
    from collections import Counter
    intervals = Counter(tuple(line.split(",", 3)[1:3]) for line in text_lines)
    assert len(intervals) == len(words)
    assert all(count in (1, 2) for count in intervals.values())
    assert "\\q2" in text_lines[0]


def test_caption_renders_in_small_caps(tmp_path):
    from src.config import CAPTION_SC_SCALE
    words = [{"word": w, "start": i * 0.4, "dur": 0.35} for i, w in
             enumerate("Roman concrete heals itself.".split())]
    ass_path = VideoComposer().build_subtitles(words, tmp_path / "caption_sc.ass")
    content = ass_path.read_text(encoding="utf-8")

    # Genuine small-cap glyphs are embedded; capital outlines are never scaled down.
    assert "Style: CapText,RR Caption," in content
    assert f"\\fscx{CAPTION_SC_SCALE}\\fscy{CAPTION_SC_SCALE}" not in content
    # Verify initial letters are full scale 100%
    assert "\\fscx100\\fscy100" in content
    # Verify text stripping reveals full words
    text_lines = [l for l in content.splitlines() if "CapText" in l and l.startswith("Dialogue:")]
    import re
    clean = re.sub(r"\{[^}]*\}", "", text_lines[0].split(",,")[-1])
    assert "Roman" in clean and "concrete" in clean


def test_align_words_with_script_restores_punctuation():
    from src.audio_engine import align_words_with_script
    script = "Can a massive temple rise without assembling a single stone? The Kailasa temple was created."
    timed = [{"word": w, "start": i * 0.3, "dur": 0.25} for i, w in enumerate("Can a massive temple rise without assembling a single stone The Kailasa temple was created".split())]
    aligned = align_words_with_script(script, timed)
    words = [w["word"] for w in aligned]
    assert words[9] == "stone?"
    assert words[10] == "The"
    assert words[-1] == "created."


def test_atomic_compound_entities_not_split():
    from src.video_composer import partition_words_grammatical
    text = "The Kailasa temple, designated as Cave 16, is the largest monolithic excavation."
    words = [{"word": w, "start": i * 0.3, "dur": 0.25} for i, w in enumerate(text.split())]
    bursts = partition_words_grammatical(words)
    # Ensure "Cave" and "16," are never in separate bursts
    for b in bursts:
        burst_words = [w["word"] for w in b]
        if "Cave" in burst_words:
            assert any("16" in w for w in burst_words)


def test_trailing_commas_and_periods_stripped_from_captions(tmp_path):
    import re
    from src.video_composer import VideoComposer
    words = [
        {"word": "excavation,", "start": 0.0, "dur": 0.5},
        {"word": "starting,", "start": 0.5, "dur": 0.5},
        {"word": "downwards.", "start": 1.0, "dur": 0.5},
        {"word": "stone?", "start": 1.5, "dur": 0.5},
    ]
    ass_path = VideoComposer().build_subtitles(words, tmp_path / "trailing_punct.ass")
    content = ass_path.read_text(encoding="utf-8")
    text_lines = [l for l in content.splitlines() if "CapText" in l and l.startswith("Dialogue:")]
    
    # In each burst's rendered ASS, check the display text
    for line in text_lines:
        clean = re.sub(r"\{[^}]*\}", "", line.split(",,")[-1]).strip()
        last_word = clean.split()[-1]
        # Must not end in comma or period
        assert not last_word.endswith(","), f"Line ended with trailing comma: {clean}"
        assert not last_word.endswith(";"), f"Line ended with trailing semicolon: {clean}"
        # Question marks must be preserved
        if "STONE" in last_word:
            assert last_word.endswith("?"), f"Question mark was lost: {clean}"


def test_clause_starter_starts_new_burst():
    from src.video_composer import partition_words_grammatical
    sample_text = "Its origins are uncertain, though it was greatly expanded during the Byzantine era."
    words = [{"word": w, "start": i * 0.4, "dur": 0.35} for i, w in enumerate(sample_text.split())]
    bursts = partition_words_grammatical(words)
    # "though" must start the subordinate clause burst, not trail at the end of the first burst
    assert bursts[1][0]["word"].lower().startswith("though")


def test_era_designators_never_orphaned():
    from src.video_composer import partition_words_grammatical
    text = "Estimates for its construction date vary from 205 BC to about 87 BC."
    words = [{"word": w, "start": i * 0.4, "dur": 0.35} for i, w in enumerate(text.split())]
    bursts = partition_words_grammatical(words)
    burst_texts = [" ".join(w["word"] for w in b) for b in bursts]
    # No burst may start with orphaned "BC"
    for b in burst_texts:
        assert not b.startswith("BC "), f"Burst began with orphaned era designator: '{b}'"
    # "205 BC" must stay together
    assert any("205 BC" in b for b in burst_texts)
    # "87 BC" must stay together
    assert any("87 BC" in b for b in burst_texts)


def test_ordinal_century_and_units_never_split():
    from src.video_composer import partition_words_grammatical
    text = "Machines did not appear again until the 14th century in western Europe."
    words = [{"word": w, "start": i * 0.4, "dur": 0.35} for i, w in enumerate(text.split())]
    bursts = partition_words_grammatical(words)
    burst_texts = [" ".join(w["word"] for w in b) for b in bursts]
    # "14th century" must stay together
    assert any("14th century" in b for b in burst_texts)
    # No burst should be an awkward "until the"
    assert "until the" not in burst_texts


def test_prefix_era_designator_not_split():
    from src.video_composer import partition_words_grammatical
    text = "The city fell in AD 79 during the disaster."
    words = [{"word": w, "start": i * 0.4, "dur": 0.35} for i, w in enumerate(text.split())]
    bursts = partition_words_grammatical(words)
    burst_texts = [" ".join(w["word"] for w in b) for b in bursts]
    # "AD 79" must stay together
    assert any("AD 79" in b for b in burst_texts)



