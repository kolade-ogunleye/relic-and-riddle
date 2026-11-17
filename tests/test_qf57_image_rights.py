"""Original-source and geographic evidence, with all provider calls mocked."""

from unittest.mock import Mock

import pytest

from scripts import fetch_commons_images as fetch
from src import commons, config
from src import museum_sources as museums
from src.credits import rights_evidence_ok


def page(templates=(), licence="Public domain"):
    return {"title": "File:Artifact.jpg", "templates": [{"title": f"Template:{name}"} for name in templates],
            "imageinfo": [{"width": 3000, "height": 2400, "size": 100000, "mime": "image/jpeg",
                "url": "https://upload.wikimedia.org/fixture.jpg", "descriptionurl": "https://commons.wikimedia.org/wiki/File:Artifact.jpg",
                "extmetadata": {"LicenseShortName": {"value": licence}, "Artist": {"value": "Fixture author"}}}]}


def test_generic_or_us_only_pd_not_accepted_as_worldwide():
    records = list(commons.candidates([page(), page(["PD-US-expired"]), page(["PD-old-70"])], []))
    assert records == []
    # Operator ruling QF-57 (Option A, item 3): a US federal work is a worldwide basis (17 U.S.C. 105).
    [federal] = commons.candidates([page(["PD-USGov"])], [])
    assert federal["worldwide_basis"]["kind"] == "commons-pd-usgov"
    assert not rights_evidence_ok({"license": "Public domain", "source_url": "https://commons.wikimedia.org/wiki/File:Artifact.jpg"})


def test_qualified_pd_retains_both_bases_through_release_evidence():
    [record] = commons.candidates([page(["PD-old-70", "PD-US-expired"])], [])
    assert record["worldwide_basis"]["kind"] == "commons-pd-old"
    assert record["worldwide_basis"]["source_life_term"] and record["worldwide_basis"]["us_basis"]
    assert rights_evidence_ok(record)
    assert not rights_evidence_ok({**record, "worldwide_basis": {"kind": "anything", "evidence": "text"}})


def test_no_openverse_provider_call_when_flag_is_off(monkeypatch):
    monkeypatch.setattr(config, "MUSEUM_OPENVERSE_ENABLED", False)
    monkeypatch.setattr(museums, "_get", lambda *a, **k: pytest.fail("provider called while disabled"))
    monkeypatch.setattr(fetch.requests, "get", lambda *a, **k: pytest.fail("bypassed provider flag"))
    assert fetch.search_openverse("artifact") == []


def flickr_item():
    return {"title": "Artifact", "url": "https://live.staticflickr.com/12/123_secret.jpg",
            "foreign_landing_url": "https://www.flickr.com/photos/fixture/123/", "license": "by",
            "license_version": "4.0", "provider": "flickr", "creator": "Old name"}


def flickr_responses(monkeypatch, license_id="4", public=1):
    monkeypatch.setattr(config, "MUSEUM_OPENVERSE_ENABLED", True)
    monkeypatch.setattr(config, "FLICKR_API_KEY", "fixture-key", raising=False)
    calls = []

    def get(url, params=None, **kwargs):
        calls.append((url, params))
        if "openverse" in url:
            return {"results": [flickr_item()]}
        if params["method"] == "flickr.photos.getInfo":
            return {"stat": "ok", "photo": {"id": "123", "license": license_id,
                "visibility": {"ispublic": public}, "owner": {"realname": "Current author"}}}
        return {"stat": "ok", "licenses": {"license": [
            {"id": "4", "url": "https://creativecommons.org/licenses/by/2.0/"},
            {"id": "0", "url": "https://www.flickr.com/all-rights-reserved/"}]}}

    monkeypatch.setattr(museums, "_get", get)
    return calls


@pytest.mark.parametrize("license_id,public", [("0", 1), ("4", 0), ("4", "0")])
def test_flickr_revoked_or_private_photo_is_refused(monkeypatch, license_id, public):
    flickr_responses(monkeypatch, license_id, public)
    assert museums.openverse_search("artifact") == []


def test_flickr_verifies_exact_photo_and_records_current_licence(monkeypatch):
    calls = flickr_responses(monkeypatch)
    [record] = museums.openverse_search("artifact")
    assert record["license"] == "CC BY 2.0" and record["artist"] == "Current author"
    assert record["license_verified_at"] and record["license_verification_source"].endswith("/123/")
    assert any((params or {}).get("method") == "flickr.photos.getInfo" for url, params in calls)
    assert rights_evidence_ok(record)


def test_flickr_key_missing_and_mismatched_image_id_are_held(monkeypatch):
    flickr_responses(monkeypatch)
    monkeypatch.setattr(config, "FLICKR_API_KEY", "")
    assert museums.openverse_search("artifact") == []
    monkeypatch.setattr(config, "FLICKR_API_KEY", "fixture-key")
    assert museums._verify_openverse_source({**flickr_item(), "url": "https://live.staticflickr.com/12/999_secret.jpg"}) is None


def test_openverse_commons_pd_is_rechecked_against_exact_file(monkeypatch):
    monkeypatch.setattr(config, "MUSEUM_OPENVERSE_ENABLED", True)
    source = page(["PD-old-100", "PD-US-expired"])
    info = source["imageinfo"][0]
    item = {"title": "Artifact", "license": "pdm", "url": info["url"], "foreign_landing_url": info["descriptionurl"]}
    monkeypatch.setattr(museums, "_get", lambda *a, **k: {"results": [item]})
    response = Mock()
    response.json.return_value = {"query": {"pages": {"1": source}}}
    monkeypatch.setattr(commons, "_get", lambda *a, **k: response)
    [record] = museums.openverse_search("artifact")
    assert record["worldwide_basis"]["kind"] == "commons-pd-old" and record["license_verified_at"]
    info["url"] = "https://upload.wikimedia.org/a-different-file.jpg"
    assert museums.openverse_search("artifact") == []


def test_nasa_shared_agency_credits_do_not_become_nasa_own_work(monkeypatch):
    def get(url, params=None, **kwargs):
        if url.endswith("/search"):
            return {"collection": {"items": [{"data": [{"nasa_id": str(index), "secondary_creator": credit}]}
                                  for index, credit in enumerate(["NASA/ESA", "NASA/CNES", "NASA/JPL"])]}}
        return {"collection": {"items": [{"href": "https://images-assets.nasa.gov/fixture~orig.jpg"}]}}
    monkeypatch.setattr(museums, "_get", get)
    records = museums.nasa_search("artifact")
    assert len(records) == 1 and records[0]["artist"] == "NASA/JPL"
    assert records[0]["worldwide_basis"]["kind"] == "nasa-own-work"


def test_licence_link_host_cannot_impersonate_creative_commons():
    assert museums.open_licence("https://evil.example/creativecommons.org/licenses/by/4.0/") is None
    assert museums.open_licence("https://rightsstatements.org/vocab/NoC-US/1.0/") is None


def test_cached_openverse_cannot_bypass_disabled_flag(monkeypatch):
    import time
    monkeypatch.setattr(config, "MUSEUM_OPENVERSE_ENABLED", False)
    key = museums._cache_key("openverse", "artifact", museums.LINE_LIMIT)
    monkeypatch.setattr(museums, "_load_cache", lambda: {key: {"at": time.time(), "found": [{
        "title": "Artifact", "download_url": flickr_item()["url"], "license": "CC BY 4.0",
        "license_verified_at": "2026-11-06T00:00:00+00:00"}]}})
    assert museums.cached_search("artifact", [], sources=("openverse",)) == []




def test_cached_flickr_licence_is_reverified_after_revocation(monkeypatch, tmp_path):
    import time
    flickr_responses(monkeypatch, license_id="0")
    monkeypatch.setattr(museums, "CACHE_FILE", tmp_path / "cache.json")
    key = museums._cache_key("openverse", "artifact", museums.LINE_LIMIT)
    monkeypatch.setattr(museums, "_load_cache", lambda: {key: {"at": time.time(), "found": [{
        "title": "Artifact", "download_url": flickr_item()["url"], "license": "CC BY 4.0",
        "license_verified_at": "2026-11-06T00:00:00+00:00"}]}})
    assert museums.cached_search("artifact", [], sources=("openverse",)) == []




def test_pd_old_auto_expired_template_clears_commons_basis():
    [record] = commons.candidates([page(["PD-old-auto-expired", "PD-US-expired"])], [])
    assert record["worldwide_basis"]["kind"] == "commons-pd-old"
    assert record["worldwide_basis"]["source_life_term"] == "pd-old-auto-expired"
    assert rights_evidence_ok(record)

    [art_record] = commons.candidates([page(["PD-Art", "PD-old-auto-expired"])], [])
    assert art_record["worldwide_basis"]["kind"] == "commons-pd-art"
    assert rights_evidence_ok(art_record)


def test_wikidata_p570_author_death_verification(monkeypatch):
    # Mock Wikidata wbgetentities response with P570 death date
    def fake_get(url, params=None, **kwargs):
        qid = params.get("ids", "")
        year = "+1920-05-15T00:00:00Z" if qid == "Q12345" else "+1990-01-01T00:00:00Z"
        resp = Mock()
        resp.json.return_value = {
            "entities": {
                qid: {
                    "claims": {
                        "P570": [{"mainsnak": {"datavalue": {"value": {"time": year, "precision": 11}}}}]
                    }
                }
            }
        }
        return resp

    monkeypatch.setattr(commons, "_get", fake_get)

    # Author died in 1920 (>70 years ago) -> admitted
    pg_old = page(["PD-US-expired"])
    pg_old["imageinfo"][0]["extmetadata"]["Artist"] = {
        "value": '<a href="https://www.wikidata.org/wiki/Q12345">Historic Artist</a>'
    }
    [rec_old] = commons.candidates([pg_old], [])
    assert rec_old["worldwide_basis"]["kind"] == "commons-pd-old"
    assert "wikidata-p570:Q12345:1920" in rec_old["worldwide_basis"]["source_life_term"]
    assert rights_evidence_ok(rec_old)

    # Author died in 1990 (<70 years ago) -> refused
    pg_recent = page(["PD-US-expired"])
    pg_recent["imageinfo"][0]["extmetadata"]["Artist"] = {
        "value": '<a href="https://www.wikidata.org/wiki/Q67890">Recent Artist</a>'
    }
    assert list(commons.candidates([pg_recent], [])) == []


def test_wikidata_p570_boundary_and_bce_dates(monkeypatch):
    from datetime import UTC, datetime
    current_year = datetime.now(UTC).year

    def fake_get(url, params=None, **kwargs):
        qid = params.get("ids", "")
        # Boundary: current_year - 70 (e.g. 1956 in 2026) -> strictly not expired yet
        if qid == "Q70001":
            year_str = f"+{current_year - 70}-06-01T00:00:00Z"
        # Eligible: current_year - 71 (e.g. 1955 in 2026) -> expired
        elif qid == "Q70002":
            year_str = f"+{current_year - 71}-01-01T00:00:00Z"
        # BCE: -300 BCE
        elif qid == "Q70003":
            year_str = "-0300-01-01T00:00:00Z"
        else:
            year_str = "+2000-01-01T00:00:00Z"

        resp = Mock()
        resp.json.return_value = {
            "entities": {
                qid: {
                    "claims": {
                        "P570": [{"mainsnak": {"datavalue": {"value": {"time": year_str, "precision": 9}}}}]
                    }
                }
            }
        }
        return resp

    monkeypatch.setattr(commons, "_get", fake_get)

    # Boundary year: death_year = current_year - 70 -> refused
    pg_bound = page(["PD-US-expired"])
    pg_bound["imageinfo"][0]["extmetadata"]["Artist"] = {"value": 'https://www.wikidata.org/wiki/Q70001'}
    assert list(commons.candidates([pg_bound], [])) == []

    # Eligible year: death_year = current_year - 71 -> admitted
    pg_elig = page(["PD-US-expired"])
    pg_elig["imageinfo"][0]["extmetadata"]["Artist"] = {"value": 'https://www.wikidata.org/wiki/Q70002'}
    [rec_elig] = commons.candidates([pg_elig], [])
    assert rec_elig["worldwide_basis"]["kind"] == "commons-pd-old"
    assert f"wikidata-p570:Q70002:{current_year - 71}" in rec_elig["worldwide_basis"]["source_life_term"]

    # BCE year: death_year = -300 -> admitted with negative sign preserved
    pg_bce = page(["PD-US-expired"])
    pg_bce["imageinfo"][0]["extmetadata"]["Artist"] = {"value": 'https://www.wikidata.org/wiki/Q70003'}
    [rec_bce] = commons.candidates([pg_bce], [])
    assert rec_bce["worldwide_basis"]["kind"] == "commons-pd-old"
    assert "wikidata-p570:Q70003:-300" in rec_bce["worldwide_basis"]["source_life_term"]


def test_author_dedications_and_us_federal_works_admitted():
    # Author dedication: PD-self
    [rec_self] = commons.candidates([page(["PD-self"])], [])
    assert rec_self["worldwide_basis"]["kind"] == "commons-pd-user"
    assert rights_evidence_ok(rec_self)

    # Author dedication: PD-user
    [rec_user] = commons.candidates([page(["PD-user"])], [])
    assert rec_user["worldwide_basis"]["kind"] == "commons-pd-user"
    assert rights_evidence_ok(rec_user)

    # US Federal works: PD-CIA
    [rec_cia] = commons.candidates([page(["PD-CIA"])], [])
    assert rec_cia["worldwide_basis"]["kind"] == "commons-pd-usgov"
    assert rights_evidence_ok(rec_cia)

    # US Federal works: PD-USGS
    [rec_usgs] = commons.candidates([page(["PD-USGS"])], [])
    assert rec_usgs["worldwide_basis"]["kind"] == "commons-pd-usgov"
    assert rights_evidence_ok(rec_usgs)


def test_sdc_creator_lookup_identifies_author_qid(monkeypatch):
    def fake_get(url, params=None, **kwargs):
        resp = Mock()
        if "M12345" in str(params.get("ids")):
            resp.json.return_value = {
                "entities": {
                    "M12345": {  # MediaInfo answers carry "statements", as Commons sends them
                        "statements": {
                            "P170": [{"mainsnak": {"datavalue": {"type": "wikibase-entityid", "value": {"id": "Q55555"}}}}]
                        }
                    }
                }
            }
        elif "Q55555" in str(params.get("ids")):
            resp.json.return_value = {
                "entities": {
                    "Q55555": {
                        "claims": {
                            "P570": [{"mainsnak": {"datavalue": {"value": {"time": "+1900-01-01T00:00:00Z", "precision": 11}}}}]
                        }
                    }
                }
            }
        else:
            resp.json.return_value = {"entities": {}}
        return resp

    monkeypatch.setattr(commons, "_get", fake_get)

    pg_sdc = page(["PD-US-expired"])
    pg_sdc["pageid"] = 12345
    # Artist field has plain text, no wikidata link
    pg_sdc["imageinfo"][0]["extmetadata"]["Artist"] = {"value": "Plain Text Artist"}
    [rec_sdc] = commons.candidates([pg_sdc], [])
    assert rec_sdc["worldwide_basis"]["kind"] == "commons-pd-old"
    assert "wikidata-p570:Q55555:1900" in rec_sdc["worldwide_basis"]["source_life_term"]
    assert rights_evidence_ok(rec_sdc)


def test_archive_film_operator_approval_basis():
    from src.image_rights import basis, recorded_basis_ok

    raw_clip = {
        "provider": "archive.org",
        "license": "Public domain",
        "source_url": "https://archive.org/details/film1",
    }
    # Raw/pending clip without operator approval is rejected
    assert not rights_evidence_ok(raw_clip)

    # Approved clip carries operator-approved basis
    approved = {
        **raw_clip,
        "check_note": "Found in an open archive; its licence checked; approved by the operator",
        "worldwide_basis": basis(
            "archive-film-operator-approved",
            raw_clip["source_url"],
            check_note="Found in an open archive; its licence checked; approved by the operator",
        ),
    }
    assert recorded_basis_ok(approved)
    assert rights_evidence_ok(approved)


def test_the_film_card_says_what_using_a_film_asserts(tmp_path):
    # Operator ruling QF-57 (2026-11-10): the "use" tap is the film's worldwide rights basis, so the card says so.
    import json as _json

    from src import archive_film
    pending = tmp_path / "t" / "footage" / "pending"
    pending.mkdir(parents=True)
    (pending / "a_film.mp4").write_bytes(b"")
    (pending / "a_film.json").write_text(_json.dumps({
        "id": "a", "title": "A film", "source_url": "https://archive.org/details/a", "license": "Public domain",
        "duration": 30, "provider": "archive.org"}), encoding="utf-8")
    card = archive_film.card(tmp_path, {"id": "t", "title": "T"}, key="1")
    assert "1. " in card and "records your approval as its worldwide rights basis" in card


def test_a_claims_shaped_sdc_answer_is_not_what_commons_sends(monkeypatch):
    # Commons MediaInfo answers carry "statements"; the round-4 lookup read "claims" and cleared no real record.
    from src import image_rights
    image_rights.creator_qid_from_sdc.cache_clear()
    answer = {"entities": {"M7": {"type": "mediainfo", "statements": {"P170": [
        {"mainsnak": {"datavalue": {"value": {"id": "Q762"}}}, "rank": "normal"}]}}}}
    monkeypatch.setattr(commons, "_get", lambda *a, **k: Mock(json=Mock(return_value=answer)))
    assert image_rights.creator_qid_from_sdc(7) == "Q762"


def test_a_death_date_needs_year_precision_and_a_live_rank(monkeypatch):
    from src import image_rights
    image_rights.wikidata_author_death.cache_clear()
    claims = [
        {"rank": "deprecated", "mainsnak": {"datavalue": {"value": {"time": "+1500-01-01T00:00:00Z", "precision": 11}}}},
        {"rank": "normal", "mainsnak": {"datavalue": {"value": {"time": "+1900-00-00T00:00:00Z", "precision": 7}}}},
        {"rank": "normal", "mainsnak": {"datavalue": {"value": {"time": "+1960-05-01T00:00:00Z", "precision": 11}}}},
        {"rank": "preferred", "mainsnak": {"datavalue": {"value": {"time": "+1958-01-01T00:00:00Z", "precision": 9}}}}]
    answer = {"entities": {"Q9": {"claims": {"P570": claims}}}}
    monkeypatch.setattr(commons, "_get", lambda *a, **k: Mock(json=Mock(return_value=answer)))
    assert image_rights.wikidata_author_death("Q9") == 1958  # preferred; never the deprecated or the century


@pytest.mark.parametrize("categories, artist, kind", [
    (["PD-USGov-NASA"], "NASA/JPL", "nasa-own-work"),
    (["PD-USGov-NASA"], "Vanessa Smith", "nasa-own-work"),  # "esa" inside a name is no ESA credit
    (["PD-USGov-NASA"], "ESA/Hubble", None),                # a NASA tag over a third-party credit
    (["PD-USGov"], "", "commons-pd-usgov"),
    (["PD-USGS"], "Courtesy of a private archive", None),  # federal works only without third-party claims
    (["PD-self"], "", "commons-pd-user"),
    (["CC-Zero", "CC-PD-Mark"], "", "cc0"),               # Commons' CC0 category is a worldwide waiver
    (["PD-old-100-expired"], "", "commons-pd-old"),
    (["PD-old-auto: no death date", "PD-US-expired"], "", None),  # no death date: life term unproven
    (["PD-US"], "", None),                                    # US-only
    ([], "", None),                                           # a bare label
])
def test_a_stored_records_own_evidence_decides_its_basis(categories, artist, kind):
    from src import image_rights
    record = {"license": "Public domain", "source_url": "https://commons.wikimedia.org/wiki/File:X.jpg",
              "categories": categories, "artist": artist}
    found = image_rights.basis_from_record(record)
    assert (found or {}).get("kind") == kind


def test_legacy_records_pass_the_release_check_without_a_migration():
    # Operator ruling QF-57: no data step; dev and production records pass by what they already carry.
    from src.credits import rights_evidence_ok
    legacy = {"license": "Public domain", "source_url": "https://commons.wikimedia.org/wiki/File:X.jpg",
              "categories": ["PD-old-100-expired"]}
    assert rights_evidence_ok(legacy)
    assert not rights_evidence_ok({**legacy, "categories": ["PD-US"]})
    film = {"license": "Public domain", "source_url": "https://archive.org/details/a", "provider": "archive.org",
            "check_note": "Found in an open archive; its licence checked; approved by the operator"}
    assert rights_evidence_ok(film)
    assert not rights_evidence_ok({**film, "check_note": "Found in an open archive"})


def test_a_held_photo_is_never_usable_even_if_kept():
    from src import image_review
    held = {"file": "a.jpg", "license": "Public domain", "source_url": "u", "categories": ["PD-US"],
            "operator_keep": True, "vision_review": {"shows_topic": "yes", "usable": True, "safe": True}}
    assert image_review.tier(held) == "weak"
    assert image_review.tier({**held, "categories": ["PD-self"]}) == "kept"


def _curated(monkeypatch, tmp_path, record):
    from src import footage
    folder = tmp_path / "assets" / "t" / "footage"
    folder.mkdir(parents=True)
    (folder / "Film.mp4").write_bytes(b"")
    (folder / "Film.json").write_text(__import__("json").dumps(record), encoding="utf-8")
    monkeypatch.setattr(config, "ASSETS_DIR", tmp_path / "assets")
    monkeypatch.setattr(config, "FOOTAGE_ENABLED", True)
    monkeypatch.setattr(footage, "probe", lambda path: {})
    monkeypatch.setattr(footage, "make_segment", lambda src, info, out: out.write_bytes(b"") or out)
    return footage.source([], {"id": "t"}, tmp_path / "run")


def test_a_film_approved_before_qf57_is_released_through_the_run_time_credit(monkeypatch, tmp_path):
    # The run-time credit replaces check_note ("Human curated and approved footage"); the basis read from the asset
    # record must survive into it, or the film renders and is then blocked at release (review 5).
    approved = {"id": "a", "title": "Film", "provider": "archive.org", "license": "Public domain",
                "source_url": "https://archive.org/details/a",
                "check_note": "Found in an open archive; its licence checked; approved by the operator"}
    clips, credits = _curated(monkeypatch, tmp_path, approved)
    [credit] = credits
    assert len(clips) == 1 and credit["check_note"] == "Human curated and approved footage"
    assert credit["worldwide_basis"]["kind"] == "archive-film-operator-approved"
    assert rights_evidence_ok(credit)


def test_a_film_without_a_worldwide_basis_is_never_used(monkeypatch, tmp_path):
    unapproved = {"id": "b", "title": "Film", "provider": "archive.org", "license": "Public domain",
                  "source_url": "https://archive.org/details/b", "check_note": "Found in an open archive"}
    assert _curated(monkeypatch, tmp_path, unapproved) == ([], [])


def test_the_upload_wizard_offers_no_public_domain_button():
    # A public-domain upload carries no recorded worldwide basis and would be held: the wizard does not offer it.
    import inspect

    from src import image_upload, telegram_bot
    source = inspect.getsource(telegram_bot)
    assert '["CC0", "CC BY 4.0"] + (["CC BY-SA 4.0"]' in source and '"CC BY 4.0", "Public domain"' not in source
    assert "free worldwide" in image_upload.PROMPTS["licence"]


def test_disputed_death_dates_take_the_latest(monkeypatch):
    # Two sourced dates of equal rank: the later year keeps the work protected longest (review 5).
    from src import image_rights
    image_rights.wikidata_author_death.cache_clear()
    claims = [{"rank": "normal", "mainsnak": {"datavalue": {"value": {"time": f"+{year}-01-01T00:00:00Z",
                                                                        "precision": 9}}}} for year in (1950, 1960)]
    answer = {"entities": {"Q8": {"claims": {"P570": claims}}}}
    monkeypatch.setattr(commons, "_get", lambda *a, **k: Mock(json=Mock(return_value=answer)))
    assert image_rights.wikidata_author_death("Q8") == 1960
