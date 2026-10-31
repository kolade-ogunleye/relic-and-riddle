"""Museum and agency sources accept only clearly-licensed images and keep their provenance."""
from src import museum_sources as ms


def _replies(monkeypatch, mapping):
    def get(url, params=None, timeout=30, headers=None):
        for key, value in mapping.items():
            if key in url:
                return value(params) if callable(value) else value
        return None
    monkeypatch.setattr(ms, "_get", get)


def test_met_keeps_only_public_domain_objects(monkeypatch):
    _replies(monkeypatch, {
        "v1.1/search": {"total": 2, "objectIDs": [1, 2]},
        "objects/1": {"isPublicDomain": True, "primaryImage": "https://images.metmuseum.org/1.jpg", "title": "Shamshir",
                      "objectDate": "18th century", "culture": "Iran", "medium": "Watered steel", "objectURL": "https://met/1"},
        "objects/2": {"isPublicDomain": False, "primaryImage": "https://images.metmuseum.org/2.jpg", "title": "Knife"},
    })
    got = ms.met_search("watered steel")
    assert [c["license"] for c in got] == ["CC0"] and "Watered steel" in got[0]["description"]
    assert got[0]["source_url"] == "https://met/1" and got[0]["provider"] == "met"


def test_nasa_skips_third_party_credits_and_takes_the_original(monkeypatch):
    item = lambda nid, creator: {"data": [{"nasa_id": nid, "title": nid, "center": "JPL", "date_created": "2002-01-01",
                                           **({"secondary_creator": creator} if creator else {})}]}
    _replies(monkeypatch, {
        "/search": {"collection": {"items": [item("A", "NASA/JPL"), item("B", "ESA/Hubble"), item("C", None)]}},
        "/asset/": {"collection": {"items": [{"href": "http://x/~large.jpg"}, {"href": "http://x/~orig.jpg"}]}},
    })
    got = ms.nasa_search("richat")
    assert [c["title"].split(" (")[0] for c in got] == ["A", "C"] and got[0]["download_url"] == "https://x/~orig.jpg"


def test_smithsonian_takes_cc0_media_only(monkeypatch):
    row = lambda access: {"title": "Seal", "content": {"descriptiveNonRepeating": {"unit_code": "NMNH", "record_link": "r",
                          "online_media": {"media": [{"usage": {"access": access}, "content": "https://ids/1.jpg"}]}}}}
    _replies(monkeypatch, {"api.si.edu": {"response": {"rows": [row("CC0"), row("Usage conditions apply")]}}})
    assert [c["license"] for c in ms.smithsonian_search("x")] == ["CC0"]


def test_smithsonian_key_travels_in_a_header_not_the_url(monkeypatch):
    seen = {}

    def get(url, params=None, timeout=30, headers=None):
        seen.update(params=params, headers=headers)
        return {"response": {"rows": []}}
    monkeypatch.setattr(ms, "_get", get)
    monkeypatch.setattr("src.config.SMITHSONIAN_API_KEY", "secret-key")  # read once, in config
    ms.smithsonian_search("x")
    assert seen["headers"] == {"X-Api-Key": "secret-key"} and "secret-key" not in str(seen["params"])
