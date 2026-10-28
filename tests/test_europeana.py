"""
tests/test_europeana.py
=======================
Europeana as a photo source (operator, 2026-10-28): only what may be reused, edited and published is taken, and
only with a key of our own.
"""

from src import config, museum_sources


def _item(rights, media="http://fotothek.slub-dresden.de/fotos/df_dk_0009142.jpg", **kw):
    return {"title": ["Town map of Wieliczka, copper engraving, 1766"], "rights": [rights], "edmIsShownBy": [media],
            "dataProvider": ["Deutsche Fotothek"], "guid": "https://www.europeana.eu/item/350/x",
            "dcCreator": ["http://semantics.gr/authorities/x"], "year": ["1766"], **kw}


def test_only_open_rights_are_reusable():
    o = museum_sources.open_licence
    assert o("http://creativecommons.org/publicdomain/mark/1.0/")[0] == "Public domain"
    assert o("http://creativecommons.org/publicdomain/zero/1.0/")[0] == "CC0"
    assert o("https://creativecommons.org/licenses/by/4.0/")[0] == "CC BY 4.0"
    assert o("http://creativecommons.org/licenses/by-sa/3.0/")[0] == "CC BY-SA 3.0"
    for closed in ("http://rightsstatements.org/vocab/InC/1.0/", "http://rightsstatements.org/vocab/InC-EDU/1.0/",
                   "http://creativecommons.org/licenses/by-nc/4.0/", "http://creativecommons.org/licenses/by-nd/4.0/",
                   "http://creativecommons.org/licenses/by-nc-nd/3.0/de/", ""):
        assert o(closed) is None, closed


def test_the_source_is_off_without_a_key(monkeypatch):
    monkeypatch.setattr(config, "EUROPEANA_API_KEY", "")
    monkeypatch.setattr(museum_sources, "_get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no call")))
    assert museum_sources.europeana_search("Wieliczka") == []


def test_candidates_carry_their_rights_over_https_and_respect_share_alike(monkeypatch):
    monkeypatch.setattr(config, "EUROPEANA_API_KEY", "k")
    sent = {}
    items = [_item("http://creativecommons.org/licenses/by/4.0/", dcCreatorLangAware={"def": ["Johann Smith"]}),
             _item("http://creativecommons.org/licenses/by-sa/4.0/"),
             _item("http://rightsstatements.org/vocab/InC-EDU/1.0/"),
             _item("http://creativecommons.org/publicdomain/mark/1.0/", media="https://www.euscreen.eu/item.html?id=X",
                   edmIsShownBy=[])]
    monkeypatch.setattr(museum_sources, "_get", lambda url, params=None, **k: sent.update(params) or {"items": items})
    monkeypatch.setattr(config, "IMAGES_ALLOW_SHARE_ALIKE", False)
    found = museum_sources.europeana_search("Wieliczka")
    assert sent["reusability"] == "open" and sent["qf"] == "TYPE:IMAGE"
    assert [c["license"] for c in found] == ["CC BY 4.0"]                    # SA off, InC-EDU never, no file no take
    c = found[0]
    assert c["download_url"].startswith("https://fotothek.slub-dresden.de/") and c["artist"] == "Johann Smith"
    assert c["source_url"] and c["license_url"] and c["attribution_required"] and c["provider"] == "europeana"
    monkeypatch.setattr(config, "IMAGES_ALLOW_SHARE_ALIKE", True)
    assert [x["license"] for x in museum_sources.europeana_search("Wieliczka")] == ["CC BY 4.0", "CC BY-SA 4.0"]
    assert museum_sources.europeana_search("Wieliczka")[1]["artist"] == "Deutsche Fotothek"   # the holder, not a URI


def test_europeana_is_one_of_the_photo_sources_and_its_key_is_masked():
    from src.ops import SecretMasker
    assert "europeana" in museum_sources.SOURCES and "EUROPEANA_API_KEY" in SecretMasker.SECRET_NAMES
