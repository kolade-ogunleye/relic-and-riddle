"""QF-126b (lander, 2026-11-14, under the operator's delegation): complete credits that fit the caption.

QF-126 holds a post whose required credits do not fit its text, and one line a photo held 14 of 28 development Reels
(and 14 of 32 in the production sample). The photos' credits are now written one line a licence when the one-photo
lines do not fit: the licence and its link once, each author once, every photo with its title and the link to its
own page; a Wikimedia Commons page may be named by the page id the Commons API gave for it. Nothing a licence asks for
is dropped, no credit is cut, and the gate reads the compact line photo by photo.

The runs are real: tests/fixtures/qf126b/dev_runs.json holds the 28 development runs that ask for a credit (their
stored copy, their ledgers' credit fields, their music) and dev_page_ids.json the page ids the Commons API returned
for their photos. What a complete credit is, is read here by this file's own reader, not by the code under test.
No network.
"""

import copy
import inspect
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
RUNS = json.loads((FIXTURES / "qf126b" / "dev_runs.json").read_text(encoding="utf-8"))["runs"]
PAGE_IDS = json.loads((FIXTURES / "qf126b" / "dev_page_ids.json").read_text(encoding="utf-8"))["page_ids"]
SETS = json.loads((FIXTURES / "qf126" / "real_credit_sets.json").read_text(encoding="utf-8"))
BY4 = "https://creativecommons.org/licenses/by/4.0/"
PHOTO = {"artist": "Ann Lee", "title": "File:Vault.jpg", "license": "CC BY 4.0", "attribution_required": True,
         "source_url": "https://commons.wikimedia.org/wiki/File:Vault.jpg", "license_url": BY4}
BODY = ("Beneath the old city a stair goes down eighteen levels.\n"
        "Each level was cut by hand, and each was sealed with a rolling stone door.\n\n"
        "#History #Archaeology #AncientWorld")
URL = r"https?://[^\s|<>\"]+"
ENTRY = re.compile(r"^(?:(?P<author>.+?): )?“(?P<title>.*)” (?P<link>" + URL + r")$")


def _photo(n, **changes):
    return {**PHOTO, "title": f"File:Room {n}.jpg", "source_url": f"https://commons.wikimedia.org/wiki/File:Room_{n}.jpg",
            **changes}


def _many(n, **changes):
    """`n` photos by three authors whose one-photo lines are long (a long file name, as real ones are)."""
    return [_photo(i, artist=("Ann Lee", "Sam Roe", "Kit Poe")[i % 3],
                   title=f"File:Underground city, level {i}, the rolling stone door.jpg",
                   source_url=f"https://commons.wikimedia.org/wiki/File:Underground_city,_level_{i},_the_rolling_stone_door.jpg",
                   **changes) for i in range(n)]


def _stage(folder, assets, music=(), page_ids=None):
    """A run folder's ledger (and its bed's rights record, and the page ids recorded for it)."""
    folder.mkdir(parents=True, exist_ok=True)
    ledger = {"assets": list(assets)}
    if music:
        ledger["ambient_bed"] = {"path": "ambient_bed.wav"}
        (folder / "ambient_bed.wav.rights.json").write_text(json.dumps({"sources": list(music)}), encoding="utf-8")
    (folder / "provenance.json").write_text(json.dumps(ledger), encoding="utf-8")
    if page_ids is not None:
        (folder / "source_links.json").write_text(json.dumps({"page_ids": page_ids}), encoding="utf-8")
    return folder / "provenance.json"


def _credit(folder, assets, payload, music=(), page_ids=None, **kwargs):
    from src.credits import add_provenance_attributions
    ledger = _stage(folder, assets, music, page_ids)
    payload = copy.deepcopy(payload)
    add_provenance_attributions(payload, ledger, **kwargs)
    return payload, ledger


def _posted(payload, ledger):
    """(why the gate holds the post, {text name: the string each platform receives}, {name: its limit})."""
    from src import publishable
    from src.credits import ledger_credit_problems
    meta = publishable.envelope_metadata({**payload, "title": payload.get("title") or ""}, None)
    texts, limits = publishable.credit_texts(meta, publishable.is_documentary(payload))
    return ledger_credit_problems(ledger, texts, limits), texts, limits


def _read(text):
    """([each photo a 📷 line credits, as (author, title, licence, page link, licence link, form)], the 📷 lines
    that are neither a whole credit of either form nor the summary line). This file's own reader."""
    works, not_whole = [], []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("📷") or line.startswith("📷 Images via"):
            continue
        fields = [f.strip() for f in line.removeprefix("📷").split(" | ")]
        if len(fields) == 5 and all(re.fullmatch(URL, f) or f == "Unknown" for f in fields[3:]):
            works.append((*fields, "photo"))
            continue
        head = fields[0].rsplit(" ", 1)
        entries = [ENTRY.match(f) for f in fields[1:]]
        if len(head) != 2 or not re.fullmatch(URL, head[1]) or not entries or not all(entries) \
                or not entries[0].group("author"):
            not_whole.append(line)
            continue
        author = ""
        for entry in entries:
            author = entry.group("author") or author
            works.append((author, entry.group("title"), head[0], entry.group("link"), head[1], "licence"))
    return works, not_whole


def _asked(assets):
    """The five parts each photo's credit must show (the same parts in either form)."""
    from src.credits import author_name, credit_title
    out = []
    for r in assets:
        licence = str(r.get("license") or "")
        if not (r.get("attribution_required") or licence.lower().startswith("cc by")):
            continue
        parts = (author_name(r), credit_title(r), licence, str(r.get("source_url") or ""), str(r.get("license_url") or ""))
        if parts not in out:
            out.append(parts)
    return out


def _deed(link):
    return re.sub(r"^http://", "https://", link).rstrip("/")


def _complete(text, assets, page_ids=None):
    """How many of the photos `text` credits with all five parts on one line: its link is the photo's own page (its
    recorded link, or the page id recorded for that very link) and its licence link is the licence's (creativecommons.org
    serves one deed over http or https, with or without the final "/")."""
    credited = _read(text)[0]
    count = 0
    for author, title, licence, source, licence_url in _asked(assets):
        links = {source}
        if source in (page_ids or {}):
            links.add(f"https://commons.wikimedia.org/?curid={page_ids[source]}")
        count += any(c[:3] == (author, title, licence) and c[3] in links and _deed(c[4]) == _deed(licence_url)
                     for c in credited)
    return count


def _fixture_run(folder, run, lookup=None):
    kwargs = {"lookup": lookup} if lookup is not None else {}
    payload, ledger = _credit(folder, run["assets"], run["payload"], run["music"], **kwargs)
    return payload, ledger


# ---------------------------------------------------------------- the real development runs

def test_the_real_runs_are_no_longer_held_for_credits_that_can_be_written_to_fit(tmp_path):
    """QF-126 held 14 of these 28 runs. One line a licence, 5 are held; with the page-id links the lookup finds, 1
    (its stored caption carries the remains of a credit broken before QF-42). Every run sent carries every photo's
    five parts in every text it posts, within the platform's limit, with no credit line cut."""
    from src.credits import add_provenance_attributions
    assert "lookup" in inspect.signature(add_provenance_attributions).parameters
    held = {"form": 0, "page_ids": 0}
    for n, run in enumerate(RUNS):
        for mode in held:
            def lookup(urls):
                return {u: PAGE_IDS[u] for u in urls if u in PAGE_IDS}
            payload, ledger = _fixture_run(tmp_path / mode / str(n), run, lookup if mode == "page_ids" else None)
            problems, texts, limits = _posted(payload, ledger)
            if problems:
                held[mode] += 1
                continue
            for name, text in texts.items():
                assert len(text) <= limits[name], (run["run"], name, len(text))
                assert _read(text)[1] == [], (run["run"], name)
                asked = _asked(run["assets"])
                assert _complete(text, run["assets"], PAGE_IDS) == len(asked), (run["run"], name, text[-900:])
                assert not asked or any("adapted" in line for line in text.splitlines() if line.startswith(("📷", "🎬")))
    assert held["form"] <= 5, held
    assert held["page_ids"] <= 1, held


def test_a_page_id_is_looked_up_only_when_the_full_links_do_not_fit(tmp_path):
    from src.credits import add_provenance_attributions
    assert "lookup" in inspect.signature(add_provenance_attributions).parameters
    asked_for = []
    for n, run in enumerate(RUNS):
        def lookup(urls, name=run["run"]):
            asked_for.append(name)
            return {u: PAGE_IDS[u] for u in urls if u in PAGE_IDS}
        _fixture_run(tmp_path / str(n), run, lookup)
    assert 0 < len(asked_for) <= 6 and len(set(asked_for)) == len(asked_for), asked_for


# ---------------------------------------------------------------- the form

@pytest.mark.parametrize("name", ["dev_longest", "dev_longest_with_music"])
def test_the_longest_real_sets_fit_beside_a_caption_with_every_part_of_every_credit(name, tmp_path):
    """2,532 and 2,460 characters as one line a photo: beside the same caption QF-126 could post neither. The first
    fits one line a licence with its full links; the second (three licences, long file names) with the links by page
    id recorded for the run."""
    credit_set = SETS[name]
    payload, ledger = _credit(tmp_path, credit_set["images"], {"caption": BODY, "title": "The stair",
                                                              "hashtags": ["#History"], "delivery_profile": "reel"},
                              credit_set.get("music") or [], page_ids=PAGE_IDS if name.endswith("music") else None)
    problems, texts, limits = _posted(payload, ledger)
    assert problems == [], problems
    asked = _asked(credit_set["images"])
    for label, text in texts.items():
        assert len(text) <= limits[label]
        works, not_whole = _read(text)
        assert not_whole == [] and _complete(text, credit_set["images"], PAGE_IDS) == len(asked), text
        heads = [line.split(" | ")[0] for line in text.splitlines() if line.startswith("📷") and "“" in line]
        licences = {(a[2], _deed(a[4])) for a in asked}
        assert len(heads) == len(set(heads)) == len(licences), heads           # one line, one link, a licence
        for line in (ln for ln in text.splitlines() if ln.startswith("📷") and "“" in ln):
            named = [ENTRY.match(f).group("author") for f in line.split(" | ")[1:] if ENTRY.match(f).group("author")]
            assert len(named) == len(set(named)), line                          # each author once on the line
        assert sum(1 for w in works if w[5] == "licence") == len(asked)         # no photo credited twice


def test_the_compact_line_carries_the_same_five_parts_as_the_one_photo_line(tmp_path):
    from src.credits import (
        asset_attribution,
        compact_credit_works,
        compact_photo_credits,
    )
    photos = _many(4) + [_photo(9, artist="Ann Lee", license="CC BY-SA 3.0",
                               license_url="https://creativecommons.org/licenses/by-sa/3.0")]
    lines, covered = compact_photo_credits(photos)
    assert len(lines) == 2 and covered == [asset_attribution(p) for p in photos]
    read = [work for line in lines for work in compact_credit_works(line)]
    for photo in photos:
        fields = [f.strip() for f in asset_attribution(photo).removeprefix("📷").split(" | ")]
        assert tuple(fields) in read, (fields, read)
    assert lines[0].startswith("📷 CC BY 4.0 " + BY4 + " | Ann Lee: “File:Underground city, level 0,")
    assert lines[0].count(BY4) == 1 and lines[0].count("Ann Lee: ") == 1 and lines[0].count("Sam Roe: ") == 1


def test_one_licence_is_named_once_whichever_way_its_link_is_recorded(tmp_path):
    """Records hold a deed's address with and without its final "/" and over http: one licence, one line, one link."""
    photos = [_photo(0), _photo(1, license_url=BY4.rstrip("/")), _photo(2, license_url=BY4.replace("https", "http"))]
    payload, ledger = _credit(tmp_path, photos, {"caption": BODY + "\n" + "A long caption. " * 100, "title": "t"})
    lines = [ln for ln in payload["caption"].splitlines() if ln.startswith("📷")]
    assert len(lines) == 1 and lines[0].count("creativecommons.org") == 1, lines
    assert _posted(payload, ledger)[0] == [] and _complete(payload["caption"], photos) == 3
    # a different licence, or a ported one, is never folded into it
    other = _photo(3, license="CC BY 3.0", license_url="https://creativecommons.org/licenses/by/3.0/")
    ported = _photo(4, license="CC BY-SA 3.0 pl", license_url="https://creativecommons.org/licenses/by-sa/3.0/pl/deed.en")
    from src.credits import compact_photo_credits
    heads = [ln.split(" | ")[0] for ln in compact_photo_credits(photos + [other, ported])[0]]
    assert heads == ["📷 CC BY 4.0 " + BY4, "📷 CC BY 3.0 https://creativecommons.org/licenses/by/3.0/",
                     "📷 CC BY-SA 3.0 pl https://creativecommons.org/licenses/by-sa/3.0/pl/deed.en"], heads


def test_the_one_photo_lines_are_still_written_wherever_they_fit(tmp_path):
    """The form QF-42 and QF-126 wrote is unchanged for a caption with room, and for the description."""
    from src.credits import ADAPTED_NOTICE, asset_attribution
    photos = _many(3)
    payload, ledger = _credit(tmp_path, photos, {"caption": BODY, "description": "The story.", "title": "t"})
    assert payload["caption"] == BODY + "".join("\n\n" + asset_attribution(p) for p in photos) + "\n\n" + ADAPTED_NOTICE
    assert _posted(payload, ledger)[0] == []
    long = _many(9)
    payload, ledger = _credit(tmp_path / "long", long, {"caption": BODY, "description": "The story.", "title": "t"})
    assert all(asset_attribution(p) in payload["description"] for p in long)      # the description: one line a photo
    assert "“" not in payload["description"]
    assert [w[5] for w in _read(payload["caption"])[0]] == ["licence"] * 9


def test_writing_the_credits_again_changes_nothing_and_a_shorter_caption_gets_the_one_photo_lines_back(tmp_path):
    from src.credits import add_provenance_attributions, asset_attribution
    photos = _many(9)
    payload, ledger = _credit(tmp_path, photos, {"caption": BODY, "title": "t"})
    first = payload["caption"]
    assert "“" in first
    add_provenance_attributions(payload, ledger)
    assert payload["caption"] == first                                           # no second copy, no drift
    # fewer photos in the video (a regeneration): the line an earlier render left is not kept beside the new credits
    fewer, ledger = _credit(tmp_path / "fewer", photos[:2], {"caption": first, "title": "t"})
    assert "“" not in fewer["caption"] and fewer["caption"].count("📷") == 2
    assert all(asset_attribution(p) in fewer["caption"] for p in photos[:2])
    assert _posted(fewer, ledger)[0] == []


def test_a_caption_written_one_line_a_photo_before_the_change_still_passes(tmp_path):
    from src.credits import ADAPTED_NOTICE, asset_attribution
    photos = _many(3)
    ledger = _stage(tmp_path, photos)
    old = {"caption": BODY + "".join("\n\n" + asset_attribution(p) for p in photos) + "\n\n" + ADAPTED_NOTICE, "title": "t"}
    assert _posted(old, ledger)[0] == []


# ---------------------------------------------------------------- the gate is not weakened

def test_a_credit_that_still_does_not_fit_is_held_and_never_cut(tmp_path):
    photos = _many(12)
    payload, ledger = _credit(tmp_path, photos, {"caption": BODY + "\n" + "A long caption. " * 80, "title": "t"})
    problems = _posted(payload, ledger)[0]
    assert problems and "required credit(s) are not in it" in problems[0], problems
    works, not_whole = _read(payload["caption"])
    assert not_whole == [] and len(payload["caption"]) <= 2200
    assert all(w in [(*a, "photo") for a in _asked(photos)] for w in works)      # what it carries is whole


def test_a_compact_line_cut_short_or_changed_credits_nothing(tmp_path):
    """The gate reads the line photo by photo: a line cut by a platform or an edit, another author, another photo's
    link, another licence's link: the photo is not credited and the post is held."""
    photos = _many(9)
    payload, ledger = _credit(tmp_path, photos, {"caption": BODY, "title": "t"})
    assert _posted(payload, ledger)[0] == []
    line = next(ln for ln in payload["caption"].splitlines() if ln.startswith("📷"))
    for changed in (line[:len(line) // 2],                                              # cut in the middle
                    line[:-10],                                                         # its last link cut
                    line.replace("Sam Roe: ", "Someone Else: "),                        # another author
                    line.replace("level_4,", "level_44,"),                              # a link to another page
                    line.replace("/by/4.0/", "/by-nc/4.0/"),                            # another licence's link
                    line.replace("“File:Underground city, level 7,", "“File:Another photo, level 7,")):
        assert changed != line
        held = _posted({**payload, "caption": payload["caption"].replace(line, changed)}, ledger)[0]
        assert held and "required credit(s) are not in it" in " ".join(held), changed


def test_a_photo_the_compact_form_cannot_carry_keeps_its_own_line(tmp_path):
    """No source link (held by the gate, as before), the operator's own photograph, a public-domain photo a source
    asks a credit for: never folded into a licence's line."""
    from src.credits import asset_attribution, compact_photo_credits
    own = _photo(20, source_url="Own work (photographed by the operator)", own_work=True)
    no_link = _photo(21, source_url="")
    public = _photo(22, license="Public domain", license_url=None, attribution_required=True)
    quoted = _photo(23, artist="The “Vault” Project")
    lines, covered = compact_photo_credits(_many(3) + [own, no_link, public])
    assert len(lines) == 1 and len(covered) == 3
    assert compact_photo_credits([quoted, _photo(24, artist="The “Vault” Project")]) == ([], [])
    photos = _many(9) + [own, public]
    payload, ledger = _credit(tmp_path, photos, {"caption": BODY, "title": "t"})
    assert asset_attribution(own) in payload["caption"] and asset_attribution(public) in payload["caption"]
    assert _posted(payload, ledger)[0] == []
    payload, ledger = _credit(tmp_path / "nolink", _many(9) + [no_link], {"caption": BODY, "title": "t"})
    assert any("lacks its source link" in p for p in _posted(payload, ledger)[0])


# ---------------------------------------------------------------- the link to a photo's page

def test_a_commons_page_is_named_by_its_page_id_only_when_that_id_is_recorded_for_it(tmp_path):
    from src.credits import commons_file_title, short_source_url
    assert short_source_url({**PHOTO, "pageid": 123}) == "https://commons.wikimedia.org/?curid=123"
    assert short_source_url(PHOTO) == PHOTO["source_url"]
    for bad in (0, -5, True, "123", 1.5, None):
        assert short_source_url({**PHOTO, "pageid": bad}) == PHOTO["source_url"], bad
    for other in ("https://www.flickr.com/photos/a/1", "https://commons.wikimedia.org.example.com/wiki/File:Vault.jpg",
                  "https://en.wikipedia.org/wiki/File:Vault.jpg", "https://commons.wikimedia.org/wiki/Category:Vaults",
                  "https://commons.wikimedia.org/w/index.php?curid=5"):
        assert commons_file_title(other) == "" and short_source_url({**PHOTO, "source_url": other, "pageid": 123}) == other
    assert commons_file_title("https://commons.wikimedia.org/wiki/File:A_vault_%28Rome%29.jpg") == "File:A vault (Rome).jpg"


def test_the_gate_accepts_a_page_id_link_only_for_the_page_it_was_recorded_for(tmp_path):
    photos = _many(9)
    ids = {p["source_url"]: 1000 + n for n, p in enumerate(photos)}
    payload, ledger = _credit(tmp_path, photos, {"caption": BODY + "\n" + "A long caption. " * 45, "title": "t"},
                              page_ids=ids)
    caption = payload["caption"]
    assert caption.count("https://commons.wikimedia.org/?curid=") == 9 and "/wiki/File:" not in caption
    assert _posted(payload, ledger)[0] == [] and _complete(caption, photos, ids) == 9
    # the same caption for a run whose folder records no id (or another id) for those pages: not its credits
    (tmp_path / "source_links.json").unlink()
    assert _posted(payload, ledger)[0]
    (tmp_path / "source_links.json").write_text(json.dumps({"page_ids": {u: i + 1 for u, i in ids.items()}}), encoding="utf-8")
    assert _posted(payload, ledger)[0]


def test_the_lookup_is_used_when_needed_and_a_failed_lookup_changes_nothing(tmp_path):
    from src.credits import add_provenance_attributions
    assert "lookup" in inspect.signature(add_provenance_attributions).parameters
    photos = _many(9) + [_photo(30, source_url="https://www.flickr.com/photos/someone/30")]
    copy_ = {"caption": BODY + "\n" + "A long caption. " * 45, "title": "t"}
    calls = []

    def lookup(urls):
        calls.append(list(urls))
        return {u: 500 + n for n, u in enumerate(urls)} | {"https://www.flickr.com/photos/someone/30": 7}

    plain, ledger = _credit(tmp_path / "none", photos, copy_)
    assert _posted(plain, ledger)[0], "nine full links do not fit beside this caption"
    found, ledger = _credit(tmp_path / "found", photos, copy_, lookup=lookup)
    assert len(calls) == 1 and all("commons.wikimedia.org/wiki/File:" in u for u in calls[0]) and len(calls[0]) == 9
    assert _posted(found, ledger)[0] == []
    assert "https://www.flickr.com/photos/someone/30" in found["caption"] and "curid=7" not in found["caption"]
    recorded = json.loads((tmp_path / "found" / "source_links.json").read_text(encoding="utf-8"))["page_ids"]
    assert sorted(recorded) == sorted(calls[0])                 # only what was asked for is recorded
    add_provenance_attributions(found, ledger, lookup=lookup)
    assert len(calls) == 1                                     # known ids are not asked for again

    def down(urls):
        raise OSError("no route to host")
    failed, ledger = _credit(tmp_path / "down", photos, copy_, lookup=down)
    assert failed["caption"] == plain["caption"] and not (tmp_path / "down" / "source_links.json").exists()
    for answer in (None, {}, {"https://commons.wikimedia.org/wiki/File:Not_asked.jpg": 9}, {photos[0]["source_url"]: "12"}):
        odd, ledger = _credit(tmp_path / f"odd{len(str(answer))}", photos, copy_, lookup=lambda urls, a=answer: a)
        assert odd["caption"] == plain["caption"], answer


def test_the_setting_turns_the_lookup_off(monkeypatch):
    from src import commons, config, credits
    assert credits.short_link_lookup() is commons.file_page_ids
    monkeypatch.setattr(config, "CREDIT_SHORT_SOURCE_LINKS", False)
    assert credits.short_link_lookup() is None
    assert "CREDIT_SHORT_SOURCE_LINKS=" in (ROOT / ".env.example").read_text(encoding="utf-8")


def test_commons_page_ids_are_taken_only_from_the_api_answer_for_that_very_file(monkeypatch):
    from src import commons
    asked = []
    pages = {"File:Vault.jpg": {"pageid": 11, "ns": 6, "title": "File:Vault.jpg"},
             "File:New name.jpg": {"pageid": 22, "ns": 6, "title": "File:New name.jpg"},
             "File:Gone.jpg": {"ns": 6, "title": "File:Gone.jpg", "missing": True},
             "File:Talk.jpg": {"pageid": 44, "ns": 7, "title": "File:Talk.jpg"}}

    class Answer:
        def __init__(self, titles):
            self.titles = titles

        def json(self):
            normal = [{"from": t, "to": t.replace("_", " ")} for t in self.titles if "_" in t]
            wanted = [t.replace("_", " ") for t in self.titles]
            moved = [{"from": "File:Old name.jpg", "to": "File:New name.jpg"}] if "File:Old name.jpg" in wanted else []
            wanted = ["File:New name.jpg" if t == "File:Old name.jpg" else t for t in wanted]
            return {"query": {"normalized": normal, "redirects": moved,
                              "pages": [pages[t] for t in wanted if t in pages]}}

    def get(url, params=None, **kwargs):
        assert url == commons.API and params["action"] == "query" and params["redirects"] == 1
        asked.append(params["titles"].split("|"))
        return Answer(asked[-1])
    monkeypatch.setattr(commons, "_get", get)
    base = "https://commons.wikimedia.org/wiki/"
    found = commons.file_page_ids([base + "File:Vault.jpg", base + "File:Old_name.jpg", base + "File:Gone.jpg",
                                   base + "File:Talk.jpg", base + "File:Unknown.jpg",
                                   "https://www.flickr.com/photos/a/1", base + "Category:Vaults"])
    assert found == {base + "File:Vault.jpg": 11, base + "File:Old_name.jpg": 22}
    assert len(asked) == 1 and "Category:Vaults" not in asked[0] and len(asked[0]) == 5
    asked.clear()
    commons.file_page_ids([f"{base}File:Photo_{n}.jpg" for n in range(120)])
    assert [len(batch) for batch in asked] == [50, 50, 20]


def test_a_fetched_commons_photo_records_its_page_id():
    from src import commons
    page = {"title": "File:Map.jpg", "pageid": 987, "categories": [], "imageinfo": [{
        "mime": "image/jpeg", "width": 4000, "height": 3000, "url": "u", "size": 1000,
        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Map.jpg",
        "extmetadata": {"Artist": {"value": "A. Cartographer"}, "LicenseShortName": {"value": "CC BY 4.0"},
                        "LicenseUrl": {"value": BY4}}}]}
    found = list(commons.candidates([page], [], allow_sa=True))
    assert len(found) == 1 and found[0].get("pageid") == 987


def test_the_page_id_recorded_at_fetch_reaches_the_ledger_and_the_credit_with_no_lookup(tmp_path, monkeypatch):
    """A photo fetched from Commons keeps the page id the search answer gave (credits.json), the run's ledger carries
    it, and a caption without room names the page by it: no lookup, no source_links.json."""
    from src.credits import add_provenance_attributions, generate_provenance_ledger
    monkeypatch.setattr("src.config.ASSETS_DIR", tmp_path / "assets")
    run = tmp_path / "run"
    run.mkdir()
    photos = _many(9)
    for n, photo in enumerate(photos):
        (run / f"photo{n}.jpg").write_bytes(b"photo %d" % n)
        photo.update(file=f"photo{n}.jpg", pageid=2000 + n)
    (run / "credits.json").write_text(json.dumps({"images": photos}), encoding="utf-8")
    ledger = generate_provenance_ledger(run, {"id": "test"}, {}, run / "photo0.jpg",
                                        media_items=[run / f"photo{n}.jpg" for n in range(9)])
    assert [a.get("pageid") for a in json.loads(ledger.read_text(encoding="utf-8"))["assets"]] == list(range(2000, 2009))
    payload = {"caption": BODY + "\n" + "A long caption. " * 45, "title": "t"}

    def never(urls):
        raise AssertionError("every id is in the ledger: nothing to look up")
    add_provenance_attributions(payload, ledger, lookup=never)
    assert payload["caption"].count("https://commons.wikimedia.org/?curid=20") == 9
    assert _posted(payload, ledger)[0] == [] and not (run / "source_links.json").exists()


# ---------------------------------------------------------------- what else reads a credit line

def test_the_compact_line_is_a_credit_line_everywhere_a_credit_line_is_read(tmp_path):
    from src.credits import (
        credit_line_problems,
        is_attribution_line,
        keep_attribution,
        rewrite_credit_lines,
        split_attribution,
    )
    from src.publishable import retag
    payload, _ = _credit(tmp_path, _many(9), {"caption": BODY, "title": "t"})
    line = next(ln for ln in payload["caption"].splitlines() if ln.startswith("📷"))
    assert is_attribution_line(line) and is_attribution_line(line.removeprefix("📷 "))    # with or without its 📷
    assert split_attribution("Words.\n\n" + line) == ("Words.", [line])
    assert keep_attribution("Old.\n\n" + line, "New words.").splitlines()[-1] == line
    assert line in retag(payload["caption"], ["#History"]).splitlines()
    assert rewrite_credit_lines(line) == line and credit_line_problems(line) == []
    five = "📷 CC BY 4.0 " + BY4 + " | Ann Lee: “a” https://commons.wikimedia.org/?curid=1 | “b” https://commons.wikimedia.org/?curid=2" \
           " | Sam Roe: “c” https://commons.wikimedia.org/?curid=3 | “d” https://commons.wikimedia.org/?curid=4"
    assert len(five.split(" | ")) == 5 and rewrite_credit_lines(five) == five and credit_line_problems(five) == []
    # an edited line with a handle or a link in a name or a title is still refused by the gate's credit_lines check
    assert any("handle" in p for p in credit_line_problems(line.replace("Sam Roe: ", "Sam Roe @samroe: ")))
    assert any("link" in p for p in credit_line_problems(line.replace("the rolling stone door.jpg”", "see t.ly/x”", 1)))


def test_the_review_card_lists_a_compact_line_whole_or_shortened_with_every_author(tmp_path):
    from src.telegram_bot import _credit_card_line
    payload, _ = _credit(tmp_path, _many(9), {"caption": BODY, "title": "t"})
    line = next(ln for ln in payload["caption"].splitlines() if ln.startswith("📷"))
    assert _credit_card_line(line, 0) == line
    forms = [_credit_card_line(line, form) for form in (1, 2, 3)]
    assert all("http" not in f and f.startswith("📷 CC BY 4.0 · ") for f in forms), forms
    assert all(name in f for f in forms for name in ("Ann Lee", "Sam Roe", "Kit Poe"))
    assert len(forms[0]) > len(forms[1]) > len(forms[2]) and forms[2].endswith("Kit Poe (×3)")


def test_a_run_with_an_illustration_counts_its_disclosure_when_the_form_is_chosen(tmp_path):
    """The caption is posted with the illustration disclosure after it: credits that fit only without it are written
    in the form that fits with it."""
    from src.publisher import ILLUSTRATION_DISCLOSURE
    photos = _many(6)
    for words in range(1, 60):   # the longest caption beside which the one-photo lines still fit
        filler = "A long caption. " * words
        plain, ledger = _credit(tmp_path / f"plain{words}", photos, {"caption": BODY + "\n" + filler, "title": "t"})
        if len(plain["caption"]) > 2200 - len(ILLUSTRATION_DISCLOSURE) - 2:
            break
    assert "“" not in plain["caption"] and 2200 - len(ILLUSTRATION_DISCLOSURE) - 2 < len(plain["caption"]) <= 2200
    drawn, ledger = _credit(tmp_path / "drawn", photos, {"caption": BODY + "\n" + filler, "title": "t",
                                                        "picture_fit": {"generated": [{"beat": 2}]}})
    assert "“" in drawn["caption"]
    problems, texts, limits = _posted(drawn, ledger)
    assert problems == [] and all(ILLUSTRATION_DISCLOSURE in t and len(t) <= limits[n] for n, t in texts.items())


def test_the_documentary_keeps_one_line_a_photo(tmp_path):
    from src.credits import asset_attribution
    photos = _many(9)
    payload, ledger = _credit(tmp_path, photos, {"caption": BODY, "description": "The full story.", "title": "t",
                                                 "youtube_title": "t", "delivery_profile": "deep_dive"})
    problems, texts, _ = _posted(payload, ledger)
    assert problems == [] and list(texts) == ["YouTube description"]
    assert all(asset_attribution(p) in texts["YouTube description"] for p in photos)


# ---------------------------------------------------------------- round 2: on the passed QF-126 (carousels, holds)

def _carousel_photos(n):
    """`n` CC BY photos with long real-shaped names, as {file name: credit record}."""
    return {f"room_{i}.jpg": record for i, record in enumerate(_many(n))}


def test_a_carousel_whose_one_photo_lines_do_not_fit_posts_them_one_line_a_licence(tmp_path):
    """Review 3 of QF-126: 2 of the 19 production-sample carousels with slide records are finally held because their
    photos' one-photo lines do not fit the approved caption. The same photos, each with its five parts, one line a
    licence: the caption carries them whole, nothing goes to a first comment, and the gate's reader finds every one."""
    from src.credits import asset_attribution, credit_present
    from src.multi_format import CAROUSEL_ADAPTED
    from tests.test_qf126_cc_by_credits import _coordinator, _recorded_carousel
    photos = _carousel_photos(6)
    slides, _ = _recorded_carousel(tmp_path, photos)
    base = "Swipe through the rooms. " + "x" * 1000
    long_lines = "\n".join(asset_attribution(r) for r in photos.values())
    assert len(base) + 2 + len(long_lines) + 1 + len(CAROUSEL_ADAPTED) > 2200    # one line a photo: does not fit
    caption, comment = _coordinator()._carousel_request("r1", {"carousel_caption": base}, slides)
    assert comment == "" and len(caption) <= 2200 and caption.startswith(base + "\n\n")
    assert caption.splitlines()[-1] == CAROUSEL_ADAPTED
    assert all(credit_present(r, caption) for r in photos.values())
    assert _complete(caption, list(photos.values())) == 6 and _read(caption)[1] == [CAROUSEL_ADAPTED]


def test_a_carousel_names_a_page_by_its_recorded_id_and_never_looks_one_up(tmp_path, monkeypatch):
    from src import commons
    from tests.test_qf126_cc_by_credits import _coordinator, _recorded_carousel
    monkeypatch.setattr(commons, "file_page_ids", lambda *a, **k: pytest.fail("no lookup at posting time"))
    photos = _carousel_photos(9)
    slides, run_dir = _recorded_carousel(tmp_path, photos)
    ids = {r["source_url"]: 1000 + i for i, r in enumerate(photos.values())}
    base = "Swipe through the rooms. " + "x" * 900
    with pytest.raises(ValueError, match="do not fit its caption"):   # the full links alone do not fit beside it
        _coordinator()._carousel_request("r1", {"carousel_caption": base}, slides)
    (run_dir / "source_links.json").write_text(json.dumps({"page_ids": ids}), encoding="utf-8")
    caption, comment = _coordinator()._carousel_request("r1", {"carousel_caption": base}, slides)
    assert comment == "" and len(caption) <= 2200 and "?curid=1000" in caption
    assert _complete(caption, list(photos.values()), ids) == 9


def test_a_carousel_whose_compact_credits_still_do_not_fit_is_held_as_before(tmp_path, monkeypatch):
    import main
    from tests.test_qf126_cc_by_credits import _holding_coordinator, _recorded_carousel
    coordinator, said, saved = _holding_coordinator(monkeypatch)
    slides, _ = _recorded_carousel(tmp_path, _carousel_photos(6))
    post = {"id": 7, "run_id": "r1", "kind": "carousel", "files": slides}
    run = {"script_payload": {"carousel_caption": "Swipe through the rooms. " + "x" * 1900}, "topic_id": "t1"}
    assert main.PipelineCoordinator._post_photos(coordinator, post, run, "carousel") == "failed"
    (text, _), = said
    assert "credit line(s) of its photos" in text and "This carousel will not post" in text
    assert saved[0][2]["detail"]["final"] is True


def test_a_carousel_without_slide_records_fits_every_photo_of_its_run_one_line_a_licence(tmp_path):
    from src.credits import asset_attribution
    from src.multi_format import CAROUSEL_ADAPTED
    from tests.test_qf126_cc_by_credits import _coordinator, _recordless_carousel
    photos = _many(10)
    slides = _recordless_carousel(tmp_path, photos)
    assert len("\n".join(asset_attribution(r) for r in photos)) > 2200     # one line a photo: over
    caption, comment = _coordinator()._carousel_request("r1", {"carousel_caption": "Swipe."}, slides)
    assert comment == "" and len(caption) <= 2200
    assert _complete(caption, photos) == 10 and _read(caption)[1] == [CAROUSEL_ADAPTED]


def test_a_hold_says_the_credits_fit_when_writing_them_again_would_fit_them_one_line_a_licence(tmp_path):
    """Round 2 of QF-126 tells the operator "They fit ... re-send the review or save any Edit" only when writing the
    credits again fits them, else "N characters over". Writing them again now writes them one line a licence where
    they do not fit one line a photo, so that is what the hold measures (the documentary's description, written one
    line a photo, is measured that way)."""
    from src.credits import credit_reach_problems, required_credits
    photos = _many(9)
    ledger = _stage(tmp_path, photos)
    required = required_credits(json.loads(ledger.read_text(encoding="utf-8")), tmp_path)
    caption = BODY + "\n" + "A long caption. " * 20
    assert len(caption) + sum(len(i["line"]) + 2 for i in required) > 2200      # one line a photo: over
    (why,) = credit_reach_problems({"Reel caption (Instagram and Facebook)": caption},
                                   required, {"Reel caption (Instagram and Facebook)": 2200})
    assert "They fit" in why and "characters over" not in why
    (why,) = credit_reach_problems({"YouTube description": caption}, required, {"YouTube description": 2200})
    assert "characters over" in why and "They fit" not in why


def test_a_review_sent_again_writes_the_credits_in_the_form_that_fits_with_the_page_ids_it_finds(tmp_path, monkeypatch):
    """QF-126's re-credit when a review is sent again uses the same writing as the render, with the lookup."""
    from types import SimpleNamespace

    from src import commons
    from tests.test_qf126_cc_by_credits import _coordinator
    photos = _many(12)
    asked = []
    monkeypatch.setattr(commons, "file_page_ids", lambda urls, **k: asked.append(list(urls)) or {
        u: 5000 + n for n, u in enumerate(urls)})
    ledger = _stage(tmp_path, photos)
    (tmp_path / "master.mp4").write_bytes(b"v")
    payload = {"caption": BODY + "\n" + "A long caption. " * 25, "description": "", "title": "t"}
    coordinator = _coordinator()
    coordinator.db = SimpleNamespace(update_run=lambda *a, **k: None)
    run = coordinator._recredited({"run_id": "r1", "master_video_path": str(tmp_path / "master.mp4"),
                                   "script_payload": payload})
    caption = run["script_payload"]["caption"]
    assert asked and "?curid=5000" in caption and len(caption) <= 2200
    problems, _, _ = _posted(run["script_payload"], ledger)
    page_ids = json.loads((tmp_path / "source_links.json").read_text(encoding="utf-8"))["page_ids"]
    assert problems == [] and _complete(caption, photos, page_ids) == 12


def test_a_stored_copy_without_recorded_page_ids_is_told_its_credits_fit_when_a_re_send_would_fit_them(tmp_path,
                                                                                                        monkeypatch):
    """Round 3: a copy stored before its run recorded any page id (every run fetched before QF-126b, a re-send whose
    lookup failed) whose credits fit one line a licence only with the page-id links is told "They fit", not a false
    "N characters over ... or Regenerate": the gate looks nothing up, it measures each unrecorded Commons page with the
    longest id (nine digits). A re-send, which looks the ids up, then posts it; a copy over even so is still told
    "over"; with the lookup turned off the full links are what is written, and what is measured. Round 4: a copy
    over is told the figure measured with the bound too, never below what the Edit must cut, not the full links'."""
    from src import config
    photos = _many(12)
    stored = {"caption": BODY + "\n" + "A long caption. " * 27, "description": "", "title": "t"}
    ledger = _stage(tmp_path / "run", photos)
    assert not (tmp_path / "run" / "source_links.json").exists()
    problems, _, _ = _posted(stored, ledger)
    assert len(problems) == 1, problems
    assert "They fit" in problems[0] and "page links are shortened" in problems[0], problems[0]
    assert "characters over" not in problems[0] and "Regenerate" not in problems[0]

    # what the hold promises: written again with the ids a lookup gives (nine digits), every credit fits and it posts
    resent, _ = _credit(tmp_path / "run", photos, stored,
                        lookup=lambda urls: {u: 987_654_300 + n for n, u in enumerate(urls)})
    page_ids = json.loads((tmp_path / "run" / "source_links.json").read_text(encoding="utf-8"))["page_ids"]
    assert _posted(resent, ledger)[0] == [] and _complete(resent["caption"], photos, page_ids) == 12

    # over even with the longest ids: still "over", with the advice to shorten
    longer = {**stored, "caption": BODY + "\n" + "A long caption. " * 60}
    (why,) = _posted(longer, _stage(tmp_path / "long", photos))[0]
    assert "characters over" in why and "They fit" not in why
    # round 4: the figure it is told is measured with the links the Edit writes (the bound), never below what the
    # Edit must cut (what the gate states once a 9-digit re-send has written them), not with the full links
    told = _over_figure(why)
    resent_longer, long_ledger = _credit(tmp_path / "long", photos, longer,
                                         lookup=lambda urls: {u: 987_654_300 + n for n, u in enumerate(urls)})
    (after,) = _posted(resent_longer, long_ledger)[0]
    assert "Shorten" in why and _over_figure(after) <= told <= _over_figure(after) + 12, (why, after)

    # one real stored copy that is truly over (development run_wieliczka_salt_mine_1792974672, through the release
    # gate's own texts): told 201 characters over (its 9-digit bound), not 482 (its full links); 194 with its real ids
    from src import publishable
    from src.credits import ledger_credit_problems
    (run,) = [r for r in RUNS if r["run"] == "run_wieliczka_salt_mine_1792974672"]
    real_ledger = _stage(tmp_path / "wieliczka", run["assets"], run["music"])
    texts, limits = publishable.payload_credit_texts(copy.deepcopy(run["payload"]),
                                                     publishable.is_documentary(run["payload"]))
    (why,) = [p for p in ledger_credit_problems(real_ledger, texts, limits) if "required credit(s)" in p]
    assert _over_figure(why) == 201 and "Shorten" in why, why

    # the lookup turned off: a re-send writes full links, so the hold measures them
    monkeypatch.setattr(config, "CREDIT_SHORT_SOURCE_LINKS", False)
    (why,) = _posted(stored, _stage(tmp_path / "off", photos))[0]
    assert "characters over" in why and "They fit" not in why
    # and the figure is the full links' (a re-send then writes them): above the bound's for the same copy
    (why,) = _posted(longer, _stage(tmp_path / "long_off", photos))[0]
    assert _over_figure(why) > told, (why, told)


def _over_figure(why):
    """The "N characters over" figure a hold states."""
    found = re.search(r"([\d,]+) characters over", why)
    assert found, why
    return int(found.group(1).replace(",", ""))


def test_a_run_backup_keeps_the_page_ids_its_compact_credit_lines_link_by(tmp_path):
    """Round 3 (review 2's note): a run restored from its backup keeps source_links.json beside its ledger, so a
    caption whose compact lines link by page id is still credited by the gate, not held until a re-send."""
    from src.backup import backup_run
    run_dir = tmp_path / "run_x_1"
    _stage(run_dir, [PHOTO], page_ids={PHOTO["source_url"]: 12345})
    res = backup_run(run_dir, dest_base=tmp_path / "dest")
    archive = Path(res["archive_path"])
    assert json.loads((archive / "source_links.json").read_text(encoding="utf-8")) == {
        "page_ids": {PHOTO["source_url"]: 12345}}
