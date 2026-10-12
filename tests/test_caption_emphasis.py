from src.config import COL_EMPHASIS, COL_FUTURE, EMPHASIS_POP
from src.video_composer import VideoComposer, emphasis_indices

WORDS = [{"word": w, "start": i * 0.4, "dur": 0.35} for i, w in
         enumerate("In 1963, a man found Derinkuyu, a city 280 feet deep. Derinkuyu still astonishes.".split())]


def test_first_occurrence_of_each_term_only():
    idx = emphasis_indices(WORDS, ["Derinkuyu", "280 feet"])
    assert idx == {5, 8, 9}  # second "Derinkuyu" (index 11) stays plain


def test_fallback_emphasises_first_two_numbers():
    assert emphasis_indices(WORDS, None) == {1, 8}


def test_ass_highlights_only_key_words_with_a_pop(tmp_path):
    ass = VideoComposer().build_subtitles(WORDS, tmp_path / "c.ass", emphasis=["Derinkuyu"]).read_text(encoding="utf-8")
    events = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
    gold = [e for e in events if COL_EMPHASIS in e]
    assert gold and all("DERINKUYU" in e for e in gold)
    assert sum(f"\\fscx{EMPHASIS_POP}" in e for e in events) == 1  # one pop, when the word is spoken
    assert COL_EMPHASIS not in "".join(e for e in events if "STILL" in e or "ASTONISHES" in e)


def test_key_word_stays_grey_until_spoken(tmp_path):
    ass = VideoComposer().build_subtitles(WORDS, tmp_path / "c.ass", emphasis=["man"]).read_text(encoding="utf-8")
    events = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:") and "MAN" in ln]
    before, spoken = events[0], events[1]   # burst "A MAN": first while "A" is spoken, then "MAN"
    assert f"{{\\c{COL_FUTURE}\\fscx100\\fscy100}}MAN" in before
    assert f"{{\\c{COL_EMPHASIS}\\fscx{EMPHASIS_POP}" in spoken


def test_validation_requires_terms_from_the_narration(curated_payload):
    from src.models import ScriptPackage
    from src.script_engine import validate_structure
    data = {k: v for k, v in curated_payload.items() if k in ScriptPackage.model_fields}
    ok = ScriptPackage.model_validate(dict(data, emphasis_words=["Derinkuyu"]))
    assert validate_structure(ok) == []
    bad = ScriptPackage.model_validate(dict(data, emphasis_words=["Atlantis"]))
    assert any("does not appear" in p for p in validate_structure(bad))
