"""Design handover (2026-11-04): a proper name is never split across caption bursts (RASHTRAKUTA | DYNASTY), in 9:16
and 16:9, nor across sidecar cues. A name is a run of capitalised words, linked by a lower-case particle only between
capitalised words, and may end on a generic head noun (src/video_composer.py name_bonds)."""
import pytest

from src.video_composer import (
    CAPTION_WIDE_MAX_CHARS,
    CAPTION_WIDE_MAX_WORDS,
    CAPTION_WIDE_MIN_WORDS,
    CAPTION_WIDE_TARGET_WORDS,
    NAME_MAX_CHARS,
    NAME_MAX_WORDS,
    _fits_wide,
    caption_bonds,
    name_bonds,
    partition_sidecar_cues,
    partition_words_grammatical,
)


def _timed(text, step=0.36, dur=0.32):
    return [{"word": w, "start": round(k * step, 3), "dur": dur} for k, w in enumerate(text.split())]


def _pairs(text):
    raw = text.split()
    return {(raw[i], raw[i + 1]) for i in name_bonds(raw)}


def _cuts(phrases):
    cuts, pos = set(), -1
    for p in phrases:
        pos += len(p)
        cuts.add(pos)
    return cuts


def _reel(words):
    return partition_words_grammatical(words)


def _wide(words):
    def fits(ph):
        return _fits_wide(ph, int(1920 * 0.75))
    return partition_words_grammatical(words, CAPTION_WIDE_MIN_WORDS, CAPTION_WIDE_TARGET_WORDS,
                                       CAPTION_WIDE_MAX_WORDS, CAPTION_WIDE_MAX_CHARS, fits=fits,
                                       glance=(24, CAPTION_WIDE_MAX_CHARS), bonds=caption_bonds(words))


@pytest.mark.parametrize("text,pairs", [
    ("It was carved under the Rashtrakuta dynasty in the eighth century.", {("the", "Rashtrakuta"), ("Rashtrakuta", "dynasty")}),
    ("monks at the Ellora Caves hold the Temple of Kailasa, cut from one rock.",
     {("the", "Ellora"), ("Ellora", "Caves"), ("the", "Temple"), ("Temple", "of"), ("of", "Kailasa,")}),
    ("The Great Wall of Gorgan runs for two hundred kilometres.",
     {("The", "Great"), ("Great", "Wall"), ("Wall", "of"), ("of", "Gorgan")}),
    ("They reached the Black Sea, then turned north.", {("the", "Black"), ("Black", "Sea,")}),
    ("monks of the Mahayana school came", {("the", "Mahayana"), ("Mahayana", "school")}),
])
def test_names_are_bonds(text, pairs):
    assert _pairs(text) == pairs


@pytest.mark.parametrize("text", [
    "It was hidden underground for centuries.",            # pronoun + verb: no name (IT WAS | HIDDEN UNDERGROUND)
    "The monks came from far away to see it.",             # a capitalised article opens no name
    "Nobody knows who carved the statues or why they stopped.",
    "Then the walls fell.",                                # a sentence adverb opens no name
    "the temple of the moon",                              # lower case: no name
    "Rome. Carthage fell.",                                # never across a sentence end
    "Rome, Carthage and Athens",                           # never across a comma
    "Up to 20,000 people could shelter",                   # a capitalised opener before a lower-case word
])
def test_no_false_names(text):
    assert name_bonds(text.split()) == set()


def test_long_run_is_no_bond():
    """A run longer than one burst holds is no bond, so the partition keeps its last-resort cut."""
    raw = "the United Nations Educational Scientific Cultural Organization met".split()
    assert name_bonds(raw) == set()
    raw = "near the Hal Saflieni Hypogeum today".split()
    assert name_bonds(raw) == {1, 2, 3} and NAME_MAX_WORDS >= 3 and NAME_MAX_CHARS >= len("Hal Saflieni Hypogeum")


@pytest.mark.parametrize("layout", [_reel, _wide])
@pytest.mark.parametrize("text", [
    "It was carved under the Rashtrakuta dynasty in the eighth century.",
    "Ellora Caves hold the Temple of Kailasa, cut from one rock.",
    "Stone walls rose around the Hal Saflieni Hypogeum in Malta.",
    "In 1819 a British officer named John Smith found Cave 10 by accident.",
    "The Great Wall of Gorgan runs for two hundred kilometres across the plain.",
    "Soldiers of Army Group North carried the Amber Room to Königsberg Castle in 1941.",
])
def test_bursts_keep_names_whole(layout, text):
    words = _timed(text)
    raw = [w["word"] for w in words]
    phrases = layout(words)
    assert [w for p in phrases for w in p] == words
    assert not (name_bonds(raw) & _cuts(phrases)), [" ".join(w["word"] for w in p) for p in phrases]


def test_reel_rashtrakuta_dynasty_is_one_burst():
    phrases = _reel(_timed("It was carved under the Rashtrakuta dynasty in the eighth century."))
    texts = [" ".join(w["word"] for w in p) for p in phrases]
    assert any("Rashtrakuta dynasty" in t for t in texts), texts


def test_sidecar_cues_keep_names_whole():
    words = _timed("For centuries the Rashtrakuta dynasty ruled from Manyakheta, and the Temple of Kailasa at Ellora "
                   "Caves was their greatest work.")
    raw = [w["word"] for w in words]
    cues = partition_sidecar_cues(words)   # (start, end, text)
    texts = [c[2].split() for c in cues]
    assert [w for t in texts for w in t] == raw
    pos, cuts = -1, set()
    for t in texts:
        pos += len(t)
        cuts.add(pos)
    assert not (name_bonds(raw) & cuts), [c[2] for c in cues]


def test_regnal_numeral_stays_with_its_name():
    """KING KRISHNA I: a Roman numeral after a name is part of it, not the pronoun I."""
    assert _pairs("built under King Krishna I of the Rashtrakuta dynasty.") == {
        ("King", "Krishna"), ("Krishna", "I"), ("the", "Rashtrakuta"), ("Rashtrakuta", "dynasty.")}
    assert name_bonds("Then I saw it".split()) == set()


@pytest.mark.parametrize("text", [
    "Near Ouadane the rings rise.",               # a sentence-opening word is no name unless capitalised elsewhere
    "Ancient Roman builders came.",
    "Historians I spoke to agree.",                # I after a word that opens no name: the pronoun
])
def test_sentence_openers_open_no_name(text):
    raw = text.split()
    assert 0 not in name_bonds(raw), _pairs(text)


def test_sentence_opener_that_recurs_is_a_name():
    raw = "Ellora Caves hold temples. Monks came to Ellora later.".split()
    assert 0 in name_bonds(raw)


def test_number_words_and_dates_continue_a_name():
    assert ("War", "Two,") in _pairs("planes came during World War Two, at night")
    assert ("August", "5") in _pairs("the siege began on August 5 and lasted")


def test_a_name_too_wide_for_the_bond_floor_is_no_bond():
    """Review 2026-11-04: a bonded run that cannot fit one burst at CAPTION_BOND_MIN_SIZE forced worse cuts
    elsewhere (ON THE | ADRAR PLATEAU OF | MAURITANIA), so it is left to the partition."""
    from src.video_composer import CAPTION_BOND_MIN_SIZE, MAX_CAPTION_WIDTH, _caption_text_width
    raw = "lies near the Podkamennaya Tunguska River in Siberia".split()
    wide = _caption_text_width(["the", "Podkamennaya", "Tunguska", "River"], CAPTION_BOND_MIN_SIZE) > MAX_CAPTION_WIDTH
    assert wide == (not ({3, 4} & name_bonds(raw)))


def test_article_goes_with_its_name_or_the_name_is_no_bond():
    """A burst never ends on THE (operator decision 2026-11-04): a name's article is bonded to it, and a name whose
    article cannot fit one burst with it is no bond."""
    for text in ["the walls of the Amber Room were packed", "monks within the Adrar Region of Mauritania lived"]:
        raw = text.split()
        b = name_bonds(raw)
        k = raw.index("the", 2) if text.startswith("the walls") else raw.index("the")
        assert (k in b) == bool(b), (text, b)


def test_a_possessive_ends_a_name():
    """Review 2026-11-04 (real run amber_room_heist_1792805842): RUSSIA'S and CATHERINE PALACE are two names, so the
    hook stays INSIDE RUSSIA'S | CATHERINE PALACE (HEAD) and never INSIDE | RUSSIA'S CATHERINE PALACE."""
    raw = "Inside Russia's Catherine Palace, a room glowed.".split()
    assert 1 not in name_bonds(raw) and 2 in name_bonds(raw)
    texts = [" ".join(w["word"] for w in p) for p in _reel(_timed(" ".join(raw)))]
    assert texts[0] == "Inside Russia's", texts
    assert ("Shah's", "death,") not in _pairs("upon Mohammad Adil Shah's death, leaving")
    assert ("Adil", "Shah's") in _pairs("upon Mohammad Adil Shah's death, leaving")


def test_numbers_and_i_after_a_plain_name():
    assert ("Egypt", "3000") not in _pairs("in Egypt 3000 years ago")
    assert ("Carter", "I") not in _pairs("as Carter I believe found it")
    assert ("Cave", "10") in _pairs("they found Cave 10 by accident")
