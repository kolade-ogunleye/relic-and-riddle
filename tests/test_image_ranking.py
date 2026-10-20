"""Fetched photos are the best the source has, not the first it returns: Commons' own grades
(Featured > Quality > Valued) then resolution, alternating across shot queries; and fallback
sources obey the same licence rules."""
from scripts import fetch_commons_images as fetch
from src import commons


def page(title, w, h, grade_cat=None):
    cats = [{"title": grade_cat}] if grade_cat else []
    return {"title": f"File:{title}.jpg", "categories": cats, "imageinfo": [{
        "mime": "image/jpeg", "width": w, "height": h, "url": "u", "size": 1000, "descriptionurl": "d",
        "extmetadata": {"LicenseShortName": {"value": "CC BY 4.0"}}}]}


def test_grade_then_resolution():
    pages = [page("temple wide small", 2000, 1500), page("temple wide huge", 8000, 6000),
             page("temple wide featured", 3000, 2000, "Category:Featured pictures on Wikimedia Commons"),
             page("temple wide quality", 2500, 2000, "Category:Quality images")]
    ranked = commons.best_first(list(commons.candidates(pages, ["temple"])))
    assert [c["title"] for c in ranked] == ["File:temple wide featured.jpg", "File:temple wide quality.jpg",
                                            "File:temple wide huge.jpg", "File:temple wide small.jpg"]


def test_queries_take_turns_so_every_shot_type_is_covered():
    wide, detail = ["w1", "w2", "w3"], ["d1"]
    assert fetch.interleave([wide, detail]) == ["w1", "d1", "w2", "w3"]


def test_openverse_licences_pass_the_same_filter(monkeypatch):
    results = [{"license": lic, "license_version": ver, "url": f"u{i}", "title": lic}
               for i, (lic, ver) in enumerate([("by", "4.0"), ("by-nd", "4.0"), ("by-sa", "4.0"), ("cc0", "1.0"), ("pdm", "")])]

    class R:
        status_code = 200

        def json(self):
            return {"results": results}
    monkeypatch.setattr(fetch.requests, "get", lambda *a, **k: R())
    assert [c["license"] for c in fetch.search_openverse("x")] == ["CC BY 4.0", "CC0", "Public domain"]


class _Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body = status, body or {}
        self.headers = {"Content-Type": "application/json", **(headers or {})}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise commons.requests.HTTPError(str(self.status_code))


def test_api_calls_follow_wikimedia_etiquette(monkeypatch):
    calls, sleeps = [], []
    replies = [_Resp(body={"error": {"code": "maxlag"}}, headers={"Retry-After": "5"}), _Resp(body={"query": {}})]

    def get(url, params=None, headers=None, timeout=None):
        calls.append((params, headers))
        return replies.pop(0)
    monkeypatch.setattr(commons.requests, "get", get)
    monkeypatch.setattr(commons.time, "sleep", sleeps.append)
    commons._get(commons.API, params={"action": "query"})
    assert all(p["maxlag"] == commons.MAXLAG for p, _ in calls)          # maxlag on every API call
    assert len(calls) == 2 and 6 in sleeps                              # waited out Retry-After, then retried
    ua = calls[0][1]["User-Agent"]
    assert "Bot/" in ua and "python-requests/" in ua                    # policy-compliant User-Agent


def test_persistent_maxlag_is_an_error_not_an_empty_result(monkeypatch):
    monkeypatch.setattr(commons.requests, "get", lambda *a, **k: _Resp(body={"error": {"code": "maxlag"}},
                                                                        headers={"Retry-After": "999"}))
    monkeypatch.setattr(commons.time, "sleep", lambda s: None)
    import pytest
    with pytest.raises(commons.requests.RequestException):
        commons._get(commons.API, params={}, max_wait=10)


def test_downloads_take_the_original_unless_it_is_huge():
    # The cached rendition measured 32-37 dB PSNR against the original at final frame size.
    p = page("temple", 9000, 6000)
    p["imageinfo"][0].update(thumburl="https://upload.wikimedia.org/thumb/2880px.jpg", url="https://upload/original.jpg",
                             size=30_000_000)
    assert next(commons.candidates([p], ["temple"]))["download_url"].endswith("original.jpg")
    p["imageinfo"][0]["size"] = 60_000_000
    assert next(commons.candidates([p], ["temple"]))["download_url"].endswith("2880px.jpg")


def test_metadata_drives_relevance():
    on_site = {"title": "File:IMG_1234.jpg", "description": "Interior of the Kailasa temple", "categories": ["Ellora Caves"],
               "lat": 20.0237, "lon": 75.1790}
    elsewhere = {"title": "File:Kailasa temple replica.jpg", "description": "", "categories": [], "lat": 28.6, "lon": 77.2}
    place = {"lat": 20.0240, "lon": 75.1792}
    assert commons.relevance(on_site, ["kailasa", "ellora"], place) > commons.relevance(elsewhere, ["kailasa", "ellora"], place)
    assert commons.relevance({"title": "File:Kailasa souvenir shop.jpg"}, ["kailasa"]) < 0


def test_planner_sees_each_photos_archive_caption(tmp_path):
    import json
    from src import scene_planner as sp
    (tmp_path / "01_commons.jpg").write_bytes(b"x")
    (tmp_path / "credits.json").write_text(json.dumps({"images": [{
        "file": "01_commons.jpg", "title": "File:Kaymakli underground city tunnel.jpg",
        "description": "Tunnel in Kaymakli, not Derinkuyu", "year": 2012}]}), encoding="utf-8")
    sp._folder_credits.cache_clear()
    cap = sp._photo_caption(tmp_path / "01_commons.jpg")
    assert "Kaymakli" in cap and "2012" in cap
    assert sp._photo_caption(tmp_path / "unknown.jpg") == ""
