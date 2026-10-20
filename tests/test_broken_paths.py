"""
tests/test_broken_paths.py
==========================
Regression tests for code paths that used to fail outright.
"""

import numpy as np
from PIL import Image

from src import visual_engine


def test_depth_layered_thumbnail_with_partial_foreground(monkeypatch, tmp_path):
    hero = tmp_path / "hero.jpg"
    Image.new("RGB", (1920, 1080), (90, 80, 70)).save(hero)
    mask = np.zeros((1080, 1920), dtype=np.uint8)
    mask[:, 1200:] = 255  # a subject on the right, clear of the title
    monkeypatch.setattr(visual_engine, "extract_foreground_mask", lambda im: (mask, 0.30))
    out = visual_engine.VisualEngine().generate_depth_layered_thumbnail(
        hero, {"thumbnail": {"line1": "TUNGUSKA", "line2": "1908 BLAST"}}, tmp_path / "yt.jpg")
    assert out.is_file()
    with Image.open(out) as im:
        assert im.size == (1920, 1080)


def _starving(monkeypatch):
    from src import fact_sheet, script_engine
    def starve(topic):
        raise fact_sheet.FactStarvedTopicError(f"Topic '{topic['id']}' yielded only 2 verified facts")
    monkeypatch.setattr(script_engine.fact_sheet, "ensure", starve)
    monkeypatch.setattr(script_engine, "_ready", lambda: True)
    monkeypatch.setattr(script_engine.free_llm, "enabled", lambda: True)
    return script_engine


def test_fact_starved_topic_surfaces_for_auto_advance(monkeypatch):
    import pytest
    from src.fact_sheet import FactStarvedTopicError
    script_engine = _starving(monkeypatch)
    monkeypatch.setattr(script_engine.ScriptEngine, "_load_curated", staticmethod(lambda tid: None))
    with pytest.raises(FactStarvedTopicError):
        script_engine.ScriptEngine().generate_script({"id": "thin_topic", "title": "Thin"})


def test_fact_starved_topic_still_uses_a_curated_script(monkeypatch, curated_payload):
    from src.models import ScriptPackage
    script_engine = _starving(monkeypatch)
    pkg = ScriptPackage.model_validate({k: v for k, v in curated_payload.items() if k in ScriptPackage.model_fields})
    monkeypatch.setattr(script_engine.ScriptEngine, "_load_curated", staticmethod(lambda tid: (pkg, True)))
    payload = script_engine.ScriptEngine().generate_script({"id": "thin_topic", "title": "Thin"})
    assert payload["origin"] == "curated"


def _bank_topic(topic_id):
    import json
    from src.config import TOPICS_DIR
    bank = json.loads((TOPICS_DIR / "topic_bank.json").read_text(encoding="utf-8"))["topics"]
    return next(t for t in bank if t["id"] == topic_id)


def _unesco():
    import pytest
    from src.unesco import UNESCOEngine
    engine = UNESCOEngine()
    if not engine.cache_file.exists() or len(engine.load_sites()) < 1000:
        pytest.skip("UNESCO registry cache not available")
    return engine


def test_unesco_never_injects_evidence_from_another_site():
    engine = _unesco()
    # Its location name is a province; Golestan Palace in Tehran is a different property.
    assert engine.match_topic(_bank_topic("great_wall_of_gorgan")) is None
    # "Zhang" is not "Chang'an", "Roman" is not "Villa Romana"; context sites are not the subject.
    assert engine.match_topic(_bank_topic("zhang_heng_seismoscope")) is None
    assert engine.match_topic(_bank_topic("richat_structure_sahara_eye")) is None
    assert engine.match_topic(_bank_topic("damascus_steel_nanotubes")) is None  # pinned: metallurgy, not the city


def test_unesco_finds_the_real_site():
    engine = _unesco()
    expected = {"kailasa_temple_monolith": 243, "gobekli_tepe_enclosure_d": 1572,  # Türkiye spelled as Turkey
                "hal_saflieni_hypogeum": 130, "wieliczka_salt_mine": 32,
                "roman_self_healing_concrete": 91, "nazca_lines_runway_paradox": 700}  # pinned
    for topic_id, site_id in expected.items():
        site = engine.match_topic(_bank_topic(topic_id))
        assert site is not None and site.id_number == site_id, topic_id
