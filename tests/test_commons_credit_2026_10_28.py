"""Commons photo credits are one clean line naming the author (audit 2026-10-28).

The metadata below is the Commons API's own extmetadata for the files that failed in development
(fetched 2026-10-28); no test makes a network call."""
import logging

from src import commons
from src.credits import asset_attribution, is_attribution_line

# File:Wieliczka, Solné doly, chodba.JPG (run_wieliczka_salt_mine_1792974672, 2026-10-26)
WIELICZKA = {
    "Credit": {"value": '<span class="int-own-work">Self-photographed</span>'},
    "Artist": {"value": 'I would appreciate <a href="//commons.wikimedia.org/wiki/Special:EmailUser/Aktron" '
                        'title="Special:EmailUser/Aktron">being notified</a> if you use my work outside Wikimedia.\n'
                        'More of my work can be found in my <a href="//commons.wikimedia.org/wiki/'
                        'Category:Geocoded_images_of_User:Aktron" title="Category:Geocoded images of User:Aktron">'
                        'personal gallery</a>.'},
    "LicenseShortName": {"value": "CC BY-SA 3.0"},
    "AttributionRequired": {"value": "true"},
}

# File:Trepanned skull, New Britain Wellcome M0005286.jpg (run_trepanation_miracle_1792995462, 2026-10-26)
WELLCOME = {
    "ImageDescription": {"value": '<div class="description">\n<p>Trepanned skull, New Britain\n</p>\n'
                                  '<p>Wellcome Images</p>\nKeywords: indigenous: weapons; Ethnography</div>'},
    "Credit": {"value": '<p><a rel="nofollow" class="external free" href="https://wellcomeimages.org/indexplus/'
                        'obf_images/3b/c5/ccaebdd02e0890d38703cda1ef1d.jpg">https://wellcomeimages.org/indexplus/'
                        'obf_images/3b/c5/ccaebdd02e0890d38703cda1ef1d.jpg</a>\n</p>\n<ul><li>Gallery: <a rel="nofollow" '
                        'class="external free" href="https://wellcomeimages.org/indexplus/image/M0005286.html">'
                        'https://wellcomeimages.org/indexplus/image/M0005286.html</a></li>\n<li>Wellcome Collection '
                        'gallery (2018-04-06): <a rel="nofollow" class="external free" href="https://wellcomecollection.org'
                        '/works/m5bqbxfr">https://wellcomecollection.org/works/m5bqbxfr</a> <a rel="nofollow" '
                        'class="external text" href="https://creativecommons.org/licenses/by/4.0/">CC-BY-4.0</a></li></ul>'},
    "LicenseShortName": {"value": "CC BY 4.0"},
    "LicenseUrl": {"value": "https://creativecommons.org/licenses/by/4.0"},
    "AttributionRequired": {"value": "true"},
}


def _page(title, meta):
    return {"title": f"File:{title}.jpg", "categories": [], "imageinfo": [{
        "mime": "image/jpeg", "width": 4000, "height": 3000, "url": "u", "size": 1000,
        "descriptionurl": f"https://commons.wikimedia.org/wiki/File:{title}.jpg", "extmetadata": meta}]}


def _one(meta, title="Salt mine corridor"):
    return list(commons.candidates([_page(title, meta)], [], allow_sa=True))


def test_a_multi_line_artist_gives_one_clean_credit_line():
    multi = {"Artist": {"value": "Jan Novak\n<br>\n  Prague"}, "LicenseShortName": {"value": "CC BY 4.0"}}
    cand = _one(multi)[0]
    assert cand["artist"] == "Jan Novak Prague"
    credit = asset_attribution(cand)
    assert "\n" not in credit and is_attribution_line(credit)


def test_a_user_notice_is_not_the_author():
    cand = _one(WIELICZKA)[0]
    assert cand["artist"] == "Aktron"
    credit = asset_attribution(cand)
    assert len(credit.splitlines()) == 1 and credit.startswith("📷 Aktron | ")
    assert "appreciate" not in credit and "gallery" not in credit


def test_boilerplate_sentences_are_dropped_and_the_name_kept():
    meta = {"Artist": {"value": "<a href='//commons.wikimedia.org/wiki/User:X'>Maria Lopez</a>. "
                                "Please credit me when you reuse this. This file was uploaded with Commonist."},
            "LicenseShortName": {"value": "CC BY 4.0"}}
    assert _one(meta)[0]["artist"] == "Maria Lopez"


def test_the_structured_attribution_is_preferred_over_the_artist_blob():
    meta = {"Attribution": {"value": "Photo: Maria Lopez"},
            "Artist": {"value": "<div>Maria<br>Lopez, see my <a href='x'>gallery</a> for more</div>"},
            "LicenseShortName": {"value": "CC BY 4.0"}}
    assert _one(meta)[0]["artist"] == "Photo: Maria Lopez"


def test_wellcome_files_are_credited_to_the_collection_from_the_credit_field():
    cand = _one(WELLCOME, "Trepanned skull, New Britain Wellcome M0005286")[0]
    assert cand["artist"] == "Wellcome Collection"
    assert cand["description"] == "Trepanned skull, New Britain Wellcome Images Keywords: indigenous: weapons; Ethnography"
    assert asset_attribution(cand).startswith("📷 Wellcome Collection | File:Trepanned skull")


def test_a_cc_by_file_nobody_can_be_credited_for_is_refused_at_fetch(caplog):
    meta = {"Credit": {"value": '<span class="int-own-work">Own work</span>'},
            "LicenseShortName": {"value": "CC BY 4.0"}, "AttributionRequired": {"value": "true"}}
    with caplog.at_level(logging.INFO, logger="src.commons"):
        assert _one(meta, "Nameless") == []
    assert "File:Nameless.jpg" in caplog.text and "needs a named author" in caplog.text


def test_public_domain_files_without_an_author_are_still_fetched():
    meta = {"LicenseShortName": {"value": "Public domain"}, "AttributionRequired": {"value": "false"}}
    assert _one(meta)[0]["artist"] == "Unknown"


def test_the_search_asks_commons_for_credit_and_attribution(monkeypatch):
    seen = {}

    class R:
        def json(self):
            return {"query": {"pages": {}}}

    monkeypatch.setattr(commons, "_get", lambda url, params=None, **k: seen.update(params) or R())
    commons.search("salt mine")
    fields = seen["iiextmetadatafilter"].split("|")
    assert {"Artist", "Credit", "Attribution", "AttributionRequired", "LicenseShortName", "LicenseUrl"} <= set(fields)


def test_a_credit_from_any_source_stays_on_one_line():
    """museum_sources, footage and uploads feed asset_attribution too: a newline in any field must not split it."""
    from src.credits import asset_attribution, is_attribution_line
    line = asset_attribution({"attribution_required": True, "artist": "Jane\nSmith", "title": "A\r\nvault",
                              "license": "CC BY 4.0", "source_url": "https://x.example/a", "license_url": None})
    assert "\n" not in line and line.startswith("📷 Jane Smith | A vault | CC BY 4.0") and is_attribution_line(line)


def test_two_unknown_author_spans_are_not_a_named_author():
    """Replay 2026-10-28 (dev Wieliczka photos 06, 08, 12): adjacent spans ran together and passed as a name."""
    from src import commons
    assert commons._plain("<span>Unknown author</span><span>Unknown author</span>") == "Unknown author"
    assert commons._author({"Artist": {"value": "<span>Unknown author</span><span>Unknown author</span>"}}) == "Unknown"
    assert commons._plain("J. <b>Smith</b>, 1950") == "J. Smith, 1950"
