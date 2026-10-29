import pytest

from src.unesco import UNESCOEngine, has_dataset
from src.audio_engine import normalize_speech_text
from src.video_composer import _is_atomic_boundary, partition_words_grammatical
from src.fact_sheet import _apply_rules
from src.models import DraftFact
from src.db import calculate_vps



def needs_unesco(test):
    """The real downloaded site list, read only; skipped unless it is a real dataset (parses, lists a site): an empty
    stub once un-skipped these tests (2026-10-29)."""
    return pytest.mark.real_unesco(pytest.mark.skipif(
        not has_dataset(), reason="UNESCO site list not downloaded (it is fetched on first use)")(test))


@needs_unesco
def test_unesco_matching_and_evidence():
    engine = UNESCOEngine()
    site = engine.find_site("Kailasa Temple, Ellora Caves", "India", 20.0238, 75.1793)
    assert site is not None
    assert "Ellora" in site.site
    quotes = engine.as_evidence(site)
    assert len(quotes) >= 1
    assert any(q["tier"] == 1 for q in quotes)
    assert any("Ellora" in q["quote"] for q in quotes)


def test_normalize_speech_text_regal_romans():
    assert normalize_speech_text("King Krishna I ruled") == "King Krishna the First ruled"
    assert normalize_speech_text("Louis XIV built Versailles") == "Louis the Fourteenth built Versailles"
    assert normalize_speech_text("Henry VIII had six wives") == "Henry the Eighth had six wives"
    assert normalize_speech_text("World War I ended in 1918") == "World War One ended in 1918"
    assert normalize_speech_text("Because I wanted to go") == "Because I wanted to go"
    assert normalize_speech_text("When I looked at the monolith") == "When I looked at the monolith"


def test_atomic_boundary_locking():
    # Regal numerals
    assert _is_atomic_boundary("Krishna", "I") is True
    assert _is_atomic_boundary("Louis", "XIV") is True
    assert _is_atomic_boundary("King", "Krishna") is True
    assert _is_atomic_boundary("the", "First") is True
    
    # Numbers and units
    assert _is_atomic_boundary("200,000", "tons") is True
    assert _is_atomic_boundary("100", "feet") is True
    assert _is_atomic_boundary("18", "years") is True
    assert _is_atomic_boundary("85", "meters") is True

    # Standard non-atomic words
    assert _is_atomic_boundary("carved", "from") is False
    assert _is_atomic_boundary("ancient", "temple") is False


def test_partition_words_preserves_atomic_pairs():
    # Test that "Krishna I" or "200,000 tons" are never broken across burst boundaries
    words = [
        {"word": "Built", "start": 0.0, "dur": 0.4},
        {"word": "under", "start": 0.4, "dur": 0.3},
        {"word": "King", "start": 0.7, "dur": 0.3},
        {"word": "Krishna", "start": 1.0, "dur": 0.4},
        {"word": "I", "start": 1.4, "dur": 0.2},
        {"word": "of", "start": 1.6, "dur": 0.2},
        {"word": "the", "start": 1.8, "dur": 0.2},
        {"word": "Rashtrakuta", "start": 2.0, "dur": 0.5},
        {"word": "dynasty.", "start": 2.5, "dur": 0.5},
    ]
    phrases = partition_words_grammatical(words, min_words=3, target_words=4, max_words=5)
    for p in phrases:
        p_words = [w["word"] for w in p]
        if "Krishna" in p_words:
            assert "I" in p_words  # "Krishna" and "I" must stay in the same phrase!


def test_fact_sheet_auto_hedge_weak_numbers():
    quotes = [{"url": "https://en.wikipedia.org/wiki/Kailasa", "quote": "over 200,000 tons of rock"}]
    # With auto_hedge=False, single-sourced unhedged number is dropped
    kept, dropped = _apply_rules([DraftFact(id="F1", statement="200,000 tons of rock", kind="number", quote_ids=[1], disputed=False, hedge="")], quotes, auto_hedge=False)
    assert len(kept) == 0
    assert len(dropped) == 1

    # With auto_hedge=True, it is preserved and auto-hedged as disputed
    kept, dropped = _apply_rules([DraftFact(id="F1", statement="200,000 tons of rock", kind="number", quote_ids=[1], disputed=False, hedge="")], quotes, auto_hedge=True)
    assert len(kept) == 1
    assert kept[0]["disputed"] is True
    assert "by one estimate" in kept[0]["hedge"]  # source-neutral: credits no one the evidence doesn't name


def test_vps_calculation():
    topic_strong = {
        "title": "The Kailasa Temple Monolith",
        "hook": "This 100-foot temple was not built from the ground up—it was carved out of a single mountain from top to bottom.",
        "key_facts": ["200,000 tons", "Top to bottom", "8th century", "King Krishna I", "18 years"],
    }
    score = calculate_vps(topic_strong)
    assert score >= 90.0  # numbers (+15) + tension (+15) + keywords (+10) + 5 facts (+10) + baseline (50) = 100.0


@needs_unesco
def test_discover_engineering_wonders():
    engine = UNESCOEngine()
    wonders = engine.discover_engineering_wonders(limit=4, sub_theme="hydraulic")
    assert len(wonders) >= 1
    for w in wonders:
        assert "(i)" in w["criteria"] or "(iv)" in w["criteria"]
        assert any(k in ["aqueduct", "canal", "hydraulic", "qanat", "cistern", "dam", "reservoir", "water"] for k in w["matched_keywords"])

    # Test exclusion
    first_title = wonders[0]["title"]
    filtered = engine.discover_engineering_wonders(limit=4, sub_theme="hydraulic", exclude_titles=[first_title])
    assert not any(f["title"] == first_title for f in filtered)


def test_compute_focal_point(tmp_path):
    from PIL import Image, ImageDraw
    from src.video_composer import VideoComposer

    # Blank image
    blank_p = tmp_path / "blank.png"
    Image.new("RGB", (100, 100), (128, 128, 128)).save(blank_p)
    cx, cy = VideoComposer.compute_focal_point(blank_p)
    assert abs(cx - 0.5) < 0.05
    assert abs(cy - 0.5) < 0.05

    # Left-weighted image
    left_p = tmp_path / "left.png"
    im_l = Image.new("RGB", (100, 100), (0, 0, 0))
    d = ImageDraw.Draw(im_l)
    d.rectangle([5, 5, 25, 95], fill=(255, 255, 255))
    im_l.save(left_p)
    cx_l, _ = VideoComposer.compute_focal_point(left_p)
    assert cx_l < 0.35

    # Right-weighted image
    right_p = tmp_path / "right.png"
    im_r = Image.new("RGB", (100, 100), (0, 0, 0))
    d = ImageDraw.Draw(im_r)
    d.rectangle([75, 5, 95, 95], fill=(255, 255, 255))
    im_r.save(right_p)
    cx_r, _ = VideoComposer.compute_focal_point(right_p)
    assert cx_r > 0.65


def _discover_handler(db, monkeypatch, tmp_path):
    import json
    from src.control import make_command_handler
    from src import topic_proposer as tp
    bank_file = tmp_path / "topic_bank.json"
    bank_file.write_text(json.dumps({"total": 0, "topics": []}), encoding="utf-8")
    queries_file = tmp_path / "image_queries.json"
    queries_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(tp, "BANK", bank_file)
    monkeypatch.setattr(tp, "QUERIES", queries_file)
    return make_command_handler(db, lambda: None, lambda: "")


def _adds(buttons):
    return [b["callback_data"] for row in buttons for b in row if b["callback_data"].startswith("uadd:")]


@needs_unesco
def test_control_discover_command(db, monkeypatch, tmp_path):
    # One marvel per page (like Waiting ideas): Add/Skip, paging, then back to ideas and the dashboard.
    h = _discover_handler(db, monkeypatch, tmp_path)
    text, buttons = h("discover", "hydraulic")
    assert "UNESCO marvel 1 of" in text and len(_adds(buttons)) == 1
    assert buttons[0][0]["text"] == "➕ Add" and buttons[-1][0]["callback_data"] == "cdash:"
    assert "Added" in h("uadd", _adds(buttons)[0].split(":")[1])


@needs_unesco
def test_discover_pagination_with_offset():
    engine = UNESCOEngine()
    batch1 = engine.discover_engineering_wonders(limit=3, offset=0, sub_theme="subterranean")
    batch2 = engine.discover_engineering_wonders(limit=3, offset=3, sub_theme="subterranean")
    assert len(batch1) == 3
    assert len(batch2) >= 1
    # Items in batch 2 should be different from batch 1
    ids1 = {w["id_number"] for w in batch1}
    ids2 = {w["id_number"] for w in batch2}
    assert ids1.isdisjoint(ids2)


@needs_unesco
def test_control_discover_umore_and_duplicate_uadd(db, monkeypatch, tmp_path):
    h = _discover_handler(db, monkeypatch, tmp_path)
    text, buttons = h("discover", "subterranean")
    assert len(_adds(buttons)) == 1
    nxt = [b["callback_data"] for row in buttons for b in row if b["text"] == "▶"]  # ▶ next marvel
    assert nxt == ["cdisc:subterranean:1"]
    second_text, more = h("cdisc", "subterranean:1")              # the next marvel replaces this one
    assert set(_adds(more)).isdisjoint(_adds(buttons)) and "marvel 2 of" in second_text
    added_text, after = h("uadd", _adds(more)[0].split(":", 1)[1])  # Add from the panel keeps the place
    assert "Added UNESCO Marvel" in added_text and "marvel 2 of" in added_text
    assert set(_adds(after)).isdisjoint(_adds(more))
    first_id = _adds(buttons)[0].split(":")[1]
    assert "Added UNESCO Marvel" in h("uadd", first_id)
    assert "already queued" in h("uadd", first_id)                # a repeated tap never duplicates


def test_added_accented_marvel_is_excluded():
    from src.unesco import _clean_title
    assert _clean_title("Pyrénées - Mont Perdu") == _clean_title("pyrenees mont perdu") == "pyrenees mont perdu"
