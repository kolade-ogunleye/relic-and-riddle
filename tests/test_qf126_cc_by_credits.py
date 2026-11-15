"""QF-126 (operator, 2026-11-14; catalogue audit 2026-11-14, risks R3 to R6): the complete CC BY credit line.

R3: the music credit was "🎵 Music: “title” by creator (CC BY 4.0)": no link to the track, none to the licence, no
word that it was edited (CC BY 4.0 s.3(a)(1)); nothing said the photos were adapted beside their per-photo lines; and
the credits were looked for in the Reel's caption OR its description, though a Reel posts no description, so a photo
credit that did not fit the caption was posted nowhere. A documentary's description is cut at 5,000 characters, and a
carousel's credits that fitted neither its caption nor its first comment were left out with a log line.
R4: no ElevenLabs-generated music in the library. R5: a modified face may not keep a Reserved Font Name and keeps its
OFL notices. R6: David Rumsey, BBC Sound Effects (RemArc) and non-commercial ESA media are refused at ingest.

The credit sets are real: tests/fixtures/qf126/real_credit_sets.json holds the two longest sets of development runs
(their ledgers' records) and the production sample's longest set as counts (the length of each line). No network.
"""

import json
import shutil
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_qf81_lazy_carousels as _lazy

ROOT = Path(__file__).resolve().parent.parent
SETS = json.loads((ROOT / "tests" / "fixtures" / "qf126" / "real_credit_sets.json").read_text(encoding="utf-8"))
BY4 = "https://creativecommons.org/licenses/by/4.0/"
TRACK = {"title": "Slow Strings", "creator": "A. Composer", "license": "by", "license_version": "4.0", "reviewed": True,
         "source": "freesound", "source_url": "https://freesound.org/people/composer/sounds/1", "license_url": BY4}
PHOTO = {"artist": "Ann Lee", "title": "File:Vault.jpg", "license": "CC BY 4.0", "attribution_required": True,
         "source_url": "https://commons.wikimedia.org/wiki/File:Vault.jpg", "license_url": BY4}
BODY = ("Beneath the old city a stair goes down eighteen levels.\n"
        "Each level was cut by hand, and each was sealed with a rolling stone door.\n\n"
        "#History #Archaeology #AncientWorld")


def _production_longest():
    """Records whose credit lines have the lengths of the production sample's longest set (counts only)."""
    from src.credits import asset_attribution
    from src.music import credit_line
    images, music = [], []
    for n, length in enumerate(SETS["production_longest"]["image_line_lengths"]):
        record = {**PHOTO, "artist": f"Photographer {n}", "title": f"File:Photo {n}.jpg",
                  "source_url": f"https://commons.wikimedia.org/wiki/File:Photo_{n}"}
        record["source_url"] += "_" + "x" * (length - len(asset_attribution(record)) - 1)
        assert len(asset_attribution(record)) == length
        images.append(record)
    for n, length in enumerate(SETS["production_longest"]["music_line_lengths"]):
        track = {**TRACK, "title": f"Track {n}"}
        if not credit_line(track).endswith("mixed"):   # before QF-126: the short line; its length is not the measure
            music.append(track)
            continue
        track["source_url"] += "0" * (length - len(credit_line(track)))
        assert len(credit_line(track)) == length
        music.append(track)
    if SETS["production_longest"]["share_alike"]:
        images[0] = {**images[0], "license": "CC BY-SA 4.0"}
    return {"images": images, "music": music}


def _set(name):
    return _production_longest() if name == "production_longest" else SETS[name]


def _run(tmp_path, credit_set, payload):
    """A run folder as a render leaves it: a provenance ledger for the set's photos, the bed with its rights record
    (audio_provenance.record_bed) when the set has music, and the payload's copy credited by the pipeline's own
    add_provenance_attributions. No video: the media checks fail on their own; the credit checks are the ones read."""
    from src import music as music_module
    from src.audio_provenance import record_bed
    from src.credits import add_provenance_attributions, sha256_file
    tmp_path.mkdir(parents=True, exist_ok=True)
    master = tmp_path / "master.mp4"
    master.write_bytes(b"not a video")
    assets = []
    for i, record in enumerate(credit_set["images"]):
        photo = tmp_path / f"photo_{i}.jpg"
        photo.write_bytes(bytes([i]) * 16)
        assets.append({**record, "path": photo.name, "sha256": sha256_file(photo), "rights_status": "verified"})
    ledger = {"status": "recorded", "issues": [], "master_video": {"path": master.name, "sha256": sha256_file(master)},
              "assets_count": len(assets), "assets": assets}
    payload = dict(payload)
    if credit_set.get("music"):
        tracks = []
        for i, track in enumerate(credit_set["music"]):
            source = tmp_path / f"track_{i}.mp3"
            source.write_bytes(bytes([64 + i]) * 16)
            tracks.append({**track, "reviewed": True, "file": source.name, "path": str(source)})
        bed = tmp_path / "ambient_bed.wav"
        bed.write_bytes(b"bed")
        credit = " \n".join(c for c in (music_module.credit_line(t) for t in tracks) if c)
        record_bed(bed, {"source": "library", "tracks": tracks, "credit": credit})
        ledger["ambient_bed"] = {"path": str(bed), "sha256": sha256_file(bed)}
        if credit:   # as main does once the bed is made
            payload["caption"] = payload["caption"] + "\n" + credit
            if payload.get("description"):
                payload["description"] += "\n\n" + credit
    (tmp_path / "provenance.json").write_text(json.dumps(ledger), encoding="utf-8")
    add_provenance_attributions(payload, tmp_path / "provenance.json")
    return {"run_id": tmp_path.name, "topic_id": "t1", "master_video_path": str(master), "script_payload": payload}


def _asked(credit_set):
    """What each credit must show, written here and not taken from the code under test: for a photo its author,
    title, licence name, source link and licence link on one 📷 line; for a track the same on one 🎵 line with a
    word that it was edited; and one line saying the photos were adapted."""
    from src.credits import author_name, credit_title
    photos = [("📷", [author_name(r), credit_title(r), r["license"], r["source_url"], r["license_url"]])
              for r in credit_set["images"]]
    tracks = [("🎵", [t["title"], t["creator"], "CC BY " + str(t["license_version"]), t["source_url"], t["license_url"],
                      "edited"]) for t in credit_set.get("music") or [] if t["license"] not in ("cc0", "pdm")]
    return photos + tracks


def _carried(text, credit_set):
    """How many of the set's credits `text` carries whole (every part on one line of its kind), and whether a line
    says the photos were adapted."""
    lines = text.splitlines()
    whole = sum(any(line.startswith(mark) and all(str(p) in line for p in parts) for line in lines)
                for mark, parts in _asked(credit_set))
    adapted = not credit_set["images"] or any("adapted" in line and line.startswith(("📷", "🎬")) for line in lines)
    return whole, adapted


def _checks(report):
    return {c["name"]: c for c in report["checks"]}


def _held(run):
    from src.release import write_release_report
    check = _checks(write_release_report(run)).get("licence_credits") or {}
    return check.get("status") == "fail", " ".join(str(d) for d in (check.get("detail") or [])
                                                    if isinstance(check.get("detail"), list))


def _publisher(db, monkeypatch):
    from src import ai_label
    from src.publisher import MetaPublisher
    monkeypatch.setattr(ai_label, "assess_video", lambda *a, **k: {"on": False, "reasons": []})
    return MetaPublisher(db=db, publishers=[])


def _envelope(db, monkeypatch, run, youtube=False):
    master = Path(run["master_video_path"])
    return _publisher(db, monkeypatch).build_envelope(run["run_id"], master, master, master, run["script_payload"],
                                                      platforms=["youtube"] if youtube else None)


# ---------------------------------------------------------------- R3: the music credit

def test_the_music_credit_names_the_track_its_author_both_links_the_licence_and_that_it_was_edited():
    from src.music import credit_line
    line = credit_line(TRACK)
    assert line.startswith("🎵 Music: “Slow Strings” by A. Composer") and "\n" not in line
    assert "CC BY 4.0" in line and TRACK["source_url"] in line and BY4 in line, line
    assert "edited" in line, line
    assert credit_line({**TRACK, "license": "CC BY 3.0", "license_version": "3.0"}).count("CC BY 3.0") == 1


@pytest.mark.parametrize("licence", ["cc0", "CC0 1.0", "pdm", "Public domain"])
def test_a_cc0_or_public_domain_track_is_given_no_cc_by_credit(licence):
    from src.music import credit_line
    assert credit_line({**TRACK, "license": licence, "license_version": "1.0"}) == ""


def test_a_public_domain_or_cc0_photo_is_not_credited_as_cc_by(tmp_path):
    pd = {**PHOTO, "license": "Public domain", "attribution_required": False, "license_url": None}
    cc0 = {**PHOTO, "title": "File:Door.jpg", "license": "CC0", "attribution_required": False}
    run = _run(tmp_path, {"images": [pd, cc0, PHOTO], "music": [{**TRACK, "license": "cc0", "license_version": "1.0"}]},
               {"caption": BODY, "description": "", "delivery_profile": "reel", "title": "The stair"})
    caption = run["script_payload"]["caption"]
    assert caption.count("📷") == 1 and caption.count("CC BY") == 1 and "🎵" not in caption, caption
    assert "File:Door.jpg" not in caption
    assert _held(run) == (False, "")


# ---------------------------------------------------------------- R3: the credits reach each platform's text

@pytest.mark.parametrize("name", ["dev_longest", "dev_longest_with_music", "production_longest"])
def test_a_reel_carries_every_credit_whole_on_each_platform_or_is_held(name, tmp_path, db, monkeypatch):
    """The longest real credit sets (2,400 to 2,550 characters of credits) do not fit Instagram's 2,200-character
    caption beside any caption: the Reel is held with the reason and nothing is posted, never posted without them.
    The same sets cut to what fits are posted with every credit whole in Instagram's and Facebook's caption and
    in the Short's description."""
    from src import publishable
    credit_set = _set(name)
    payload = {"caption": BODY, "description": "", "delivery_profile": "reel", "title": "The stair",
               "hashtags": ["#History", "#Archaeology", "#AncientWorld"], "topic_id": "t1"}
    run = _run(tmp_path / "whole", credit_set, payload)
    held, why = _held(run)
    assert held, "the longest real set cannot fit a 2,200-character caption: the Reel must be held"
    assert "Reel caption (Instagram and Facebook) and YouTube Short description: " in why
    assert "required credit(s) are not in it" in why and "characters over the platform's 2,200" in why
    with pytest.raises(ValueError, match="Not posted: a licence credit is missing") as refused:
        _envelope(db, monkeypatch, run)
    # no retry changes it: the booking is blocked with the reason (QF-110), not tried again every pass
    assert type(refused.value).__name__ == "ReleaseBlocked"
    # ... and nothing was cut to make room: every line the caption does carry is a whole credit
    asked = [parts for _, parts in _asked(credit_set)]
    for line in run["script_payload"]["caption"].splitlines():
        if line.startswith(("📷 ", "🎵 ")) and " | " in line:
            assert any(all(str(p) in line for p in parts) for parts in asked), line

    # The largest part of the same real set that fits: posted, with every credit on every platform.
    fitting = {"images": list(credit_set["images"]), "music": credit_set.get("music") or []}
    while True:
        run = _run(tmp_path / f"fit{len(fitting['images'])}", fitting, payload)
        if len(run["script_payload"]["caption"]) <= 2200 and not _held(run)[0]:
            break
        fitting["images"] = fitting["images"][:-1]
    assert len(fitting["images"]) >= 3, "at least three real photo credits fit beside a caption"
    meta = _envelope(db, monkeypatch, run)["metadata"]
    texts, limits = publishable.credit_texts(meta)
    assert set(texts) == {"Reel caption (Instagram and Facebook)", "YouTube Short description"}
    from src.publisher import reel_fields, youtube_short_fields
    assert texts["Reel caption (Instagram and Facebook)"] == reel_fields(meta)["caption"]
    assert texts["YouTube Short description"] == youtube_short_fields(meta)["description"]
    for label, text in texts.items():
        assert len(text) <= limits[label], (label, len(text))
        assert _carried(text, fitting) == (len(_asked(fitting)), True), (label, text)


def test_what_the_gate_holds_is_what_the_caption_lacks_and_an_edit_that_makes_room_clears_it(tmp_path):
    """The reason names the platform text and the credits it lacks; shortening the caption until they fit (what
    Edit does: add_provenance_attributions writes the credits again) clears the hold."""
    from src.credits import add_provenance_attributions
    credit_set = {"images": SETS["dev_longest"]["images"][:6], "music": SETS["dev_longest_with_music"]["music"]}
    payload = {"caption": BODY + "\n" + "A long caption. " * 60, "description": "", "delivery_profile": "reel",
               "title": "The stair"}
    run = _run(tmp_path, credit_set, payload)
    held, why = _held(run)
    assert held and "Shorten the text so they fit (Edit, for a caption)" in why and "photo “" in why, why
    assert "characters over the platform's 2,200" in why and why.count("required credit(s)") == 1   # said once
    run["script_payload"]["caption"] = BODY
    add_provenance_attributions(run["script_payload"], tmp_path / "provenance.json")
    assert _held(run) == (False, "")
    assert _carried(run["script_payload"]["caption"], credit_set) == (len(_asked(credit_set)), True)


@pytest.mark.parametrize("name", ["dev_longest", "dev_longest_with_music", "production_longest"])
def test_a_documentary_description_carries_every_credit_or_is_held_at_youtubes_5000_characters(name, tmp_path, db,
                                                                                           monkeypatch):
    from src.publisher import youtube_fields
    credit_set = _set(name)
    payload = {"caption": BODY, "description": "The full story of the stair.\n" + "A paragraph of it. " * 60,
               "delivery_profile": "deep_dive", "title": "The stair", "youtube_title": "The stair", "topic_id": "t1"}
    run = _run(tmp_path / "fits", credit_set, payload)
    assert _held(run) == (False, ""), _held(run)
    meta = _envelope(db, monkeypatch, run, youtube=True)["metadata"]
    sent = youtube_fields(meta)["description"]
    assert len(sent) <= 5000 and _carried(sent, credit_set) == (len(_asked(credit_set)), True), sent[-600:]

    # A description that leaves the credits no room: YouTube's text is cut at 5,000, which cut the credits off.
    long = dict(payload, description="The full story of the stair.\n" + "A paragraph of it. " * 250)
    run = _run(tmp_path / "cut", credit_set, long)
    sent = youtube_fields({"title": "The stair", "caption": "", "description": run["script_payload"]["description"]})
    assert _carried(sent["description"], credit_set)[0] < len(_asked(credit_set))    # the cut text has lost credits
    held, why = _held(run)
    assert held and "YouTube description" in why and "cut at the platform's 5,000 characters" in why, why
    with pytest.raises(ValueError, match="Not posted: a licence credit is missing"):
        _envelope(db, monkeypatch, run, youtube=True)
    # the check reads the very string the uploader sends
    from src import publishable
    assert publishable.credit_texts(meta, youtube=True)[0] == {"YouTube description": youtube_fields(meta)["description"]}


def test_a_credit_without_its_source_or_licence_link_holds_the_post(tmp_path):
    no_link = {**TRACK, "license_url": ""}
    run = _run(tmp_path / "music", {"images": [], "music": [no_link]},
               {"caption": BODY, "description": "", "delivery_profile": "reel", "title": "The stair"})
    held, why = _held(run)
    assert held and "music “Slow Strings” by A. Composer lacks its licence link" in why, why
    run = _run(tmp_path / "photo", {"images": [{**PHOTO, "source_url": ""}], "music": []},
               {"caption": BODY, "description": "", "delivery_profile": "reel", "title": "The stair"})
    held, why = _held(run)
    assert held and "photo “File:Vault.jpg” by Ann Lee lacks its source link" in why, why
    # the operator's own photograph has no source page (image_upload records "Own work ..."): nothing is lacking
    own = {**PHOTO, "source_url": "Own work (photographed by the operator)"}
    run = _run(tmp_path / "own", {"images": [own], "music": []},
               {"caption": BODY, "description": "", "delivery_profile": "reel", "title": "The stair"})
    assert _held(run) == (False, "")


def test_a_run_credited_before_the_change_is_held_and_rewriting_its_credits_completes_them(tmp_path):
    """A run waiting for approval when this is deployed carries the short music line and no adapted notice: it is
    held with the reason, and Edit or Regenerate (add_provenance_attributions) replaces the short line with the
    complete one, once, after which the music's rights check still passes."""
    from src.audio_provenance import inspect_audio_rights
    from src.credits import add_provenance_attributions, asset_attribution
    short = "🎵 Music: “Slow Strings” by A. Composer (CC BY 4.0)"
    run = _run(tmp_path, {"images": [PHOTO], "music": [TRACK]},
               {"caption": BODY, "description": "", "delivery_profile": "reel", "title": "The stair"})
    old_caption = BODY + "\n" + short + "\n\n" + asset_attribution(PHOTO)
    record_path = tmp_path / "ambient_bed.wav.rights.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["required_attribution"] = short                      # as a bed recorded before the change says
    record_path.write_text(json.dumps(record), encoding="utf-8")
    run["script_payload"].update(caption=old_caption, description=asset_attribution(PHOTO))
    held, why = _held(run)
    assert held and "music “Slow Strings” by A. Composer" in why and "the notice that the photos were adapted" in why
    add_provenance_attributions(run["script_payload"], tmp_path / "provenance.json")
    caption = run["script_payload"]["caption"]
    assert short not in caption and caption.count("🎵") == 1 and caption.count("📷") == 1, caption
    assert _carried(caption, {"images": [PHOTO], "music": [TRACK]}) == (2, True)
    assert _held(run) == (False, "")
    assert inspect_audio_rights(tmp_path / "ambient_bed.wav", "music", caption)[0] is True
    assert inspect_audio_rights(tmp_path / "ambient_bed.wav", "music", BODY)[0] is False   # no credit at all: refused


def test_the_pipelines_own_lines_are_kept_through_an_edit_and_the_hashtag_pass():
    """The complete music line and the adapted notice are credit lines: an edit keeps them, the hashtag pass and
    the carousel clean-up leave them as they are, and the copy checks do not read them as the writer's prose."""
    from src.credits import is_attribution_line, keep_attribution, split_attribution
    from src.music import credit_line
    from src.publishable import retag
    lines = [credit_line(TRACK), "🎬 Photos adapted for this video: cropped, with motion and text added."]
    assert all(is_attribution_line(line) for line in lines)
    from src.credits import ADAPTED_NOTICE
    assert lines[1] == ADAPTED_NOTICE
    old = "Old words.\n\n" + "\n\n".join(lines)
    assert split_attribution(old) == ("Old words.", lines)
    assert keep_attribution(old, "New words.").splitlines()[-2:] == lines
    assert retag(old + "\n#Other", ["#History"]).splitlines()[2:5] == [lines[0], "", lines[1]]


# ---------------------------------------------------------------- R3: the carousel's caption

def _coordinator():
    from main import PipelineCoordinator
    coordinator = object.__new__(PipelineCoordinator)
    coordinator.db = SimpleNamespace(get_run=lambda run_id: {"topic_id": "t1"})
    return coordinator


@pytest.mark.parametrize("name", ["dev_longest", "dev_longest_with_music", "production_longest"])
def test_a_carousel_carries_every_photo_credit_in_its_caption_or_is_not_posted(name, monkeypatch):
    from src import multi_format
    from src.credits import asset_attribution
    lines = [asset_attribution(r) for r in _set(name)["images"]] + [multi_format.CAROUSEL_ADAPTED]
    monkeypatch.setattr(multi_format, "carousel_credits", lambda slides: (lines, True))
    coordinator = _coordinator()
    fits = next(n for n in range(len(lines), 0, -1)
                if len("Swipe through the rooms.") + 2 + len("\n".join(lines[-n:])) <= 2200)
    monkeypatch.setattr(multi_format, "carousel_credits", lambda slides: (lines[-fits:], True))
    caption, comment = coordinator._carousel_request("r1", {"carousel_caption": "Swipe through the rooms."}, ["s1.jpg"])
    assert comment == "" and len(caption) <= 2200 and caption.splitlines()[-fits:] == lines[-fits:]

    # A caption that leaves the credits no room: they went to a first comment (whose failure does not fail the
    # post), and past the comment's own limit they were left out. Now the carousel is not posted, with the reason:
    # a hold no redraw can lift (the same photos, the caption its review approved), which names no action.
    monkeypatch.setattr(multi_format, "carousel_credits", lambda slides: (lines, True))
    long = {"carousel_caption": "Swipe through the rooms. " + "x" * 1900}
    with pytest.raises(ValueError, match=r"^\d+ credit line\(s\) of its photos .* do not fit its caption .* the "
                                         r"caption was approved with its review and cannot be changed now$") as held:
        coordinator._carousel_request("r1", long, ["s1.jpg"])
    assert type(held.value).__name__ == "CarouselCreditHold" and held.value.final is True
    assert "Edit" not in str(held.value) and "etry" not in str(held.value)
    with pytest.raises(ValueError, match="do not fit its caption"):   # fits neither caption nor comment: left out before
        monkeypatch.setattr(multi_format, "carousel_credits", lambda slides: (lines * 2, True))
        coordinator._carousel_request("r1", long, ["s1.jpg"])


def test_a_carousel_photo_credit_without_its_source_link_is_not_posted(monkeypatch):
    from src import multi_format
    from src.credits import asset_attribution
    records = {"no_link.jpg": {**PHOTO, "source_url": ""},
               "own.jpg": {**PHOTO, "source_url": "Own work (photographed by the operator)", "own_work": True}}
    monkeypatch.setattr(multi_format, "slide_photos", lambda slide: [Path(slide.name)])
    monkeypatch.setattr(multi_format, "photo_credit", lambda photo: records[photo.name])
    monkeypatch.setattr(multi_format, "carousel_credits", lambda slides: (
        [asset_attribution(records[s.name]) for s in slides] + [multi_format.CAROUSEL_ADAPTED], True))
    with pytest.raises(ValueError, match="lacks its source link"):
        _coordinator()._carousel_request("r1", {"carousel_caption": "Swipe."}, ["no_link.jpg"])
    # the operator's own photograph has no source page: its credit (author, licence, licence link) is complete
    caption, comment = _coordinator()._carousel_request("r1", {"carousel_caption": "Swipe."}, ["own.jpg"])
    assert comment == "" and asset_attribution(records["own.jpg"]) in caption


def _recordless_carousel(tmp_path, images, ledger=True):
    """A carousel drawn before slide records were kept: its slides are there, no record says whose photo each shows.
    Its run's ledger holds `images`."""
    run_dir = tmp_path / "run_old"
    (run_dir / "carousel").mkdir(parents=True)
    slides = []
    for n in (1, 2):
        slide = run_dir / "carousel" / f"carousel_slide_{n}.jpg"
        slide.write_bytes(b"slide")
        slides.append(str(slide))
    if ledger:
        (run_dir / "provenance.json").write_text(json.dumps({"assets": images}) if ledger is True else ledger,
                                                 encoding="utf-8")
    return slides


SHARE_ALIKE_LINE = "🎬 This video is licensed CC BY-SA 4.0: https://creativecommons.org/licenses/by-sa/4.0/"
SUMMARY = "📷 Images via Wikimedia Commons and partner archives: Ann Lee (CC BY 4.0) · adapted: cropped, with motion and text added"


def test_a_carousel_without_slide_records_credits_every_photo_of_its_run_or_is_held(tmp_path):
    """Review of QF-126, round 1: 20 of 29 development carousels were drawn before slide records were kept; they
    were posted with the Reel caption's summary line (a name and a licence: no title, no link) or with no photo
    credit at all. Now the caption credits every photo of the run from its ledger, or the carousel is held."""
    from src import multi_format
    from src.credits import asset_attribution
    images = [PHOTO, {**PHOTO, "artist": "Jan Novak", "title": "File:Door.jpg", "license": "CC BY-SA 4.0",
                      "source_url": "https://commons.wikimedia.org/wiki/File:Door.jpg",
                      "license_url": "https://creativecommons.org/licenses/by-sa/4.0/"},
              {**PHOTO, "title": "File:Well.jpg", "license": "Public domain", "attribution_required": False}]
    slides = _recordless_carousel(tmp_path, images)
    assert multi_format.carousel_credits([Path(s) for s in slides]) == ([], False)
    payload = {"carousel_caption": "Swipe through the rooms.", "caption": "Reel text.\n\n" + SUMMARY}
    caption, comment = _coordinator()._carousel_request("r1", payload, slides)
    lines = caption.splitlines()
    assert comment == "" and len(caption) <= 2200
    assert all(asset_attribution(r) in lines for r in images[:2]), caption        # author, title, licence, both links
    assert multi_format.CAROUSEL_SHARE_ALIKE in lines and lines[-1] == multi_format.CAROUSEL_ADAPTED
    assert not any("Images via" in line for line in lines)                        # the borrowed summary is replaced
    assert caption.startswith("Swipe through the rooms.\n\n") and caption.count("File:Well.jpg") == 0

    # They do not fit: held (what can be done about it is said where it is held, see the next test).
    long = dict(payload, carousel_caption="Swipe through the rooms. " + "x" * 1900)
    with pytest.raises(ValueError, match=r"drawn before slide records were kept.*2 photo credit line\(s\).*over the "
                                         r"2,200 a caption takes$") as held:
        _coordinator()._carousel_request("r1", long, slides)
    assert type(held.value).__name__ == "CarouselCreditHold"
    # The ledger cannot be read: what it must credit is unknown, so it is not posted.
    with pytest.raises(ValueError, match="provenance record could not be read") as held:
        _coordinator()._carousel_request("r1", payload, _recordless_carousel(tmp_path / "bad", images, ledger="{not json"))
    assert type(held.value).__name__ == "CarouselCreditHold"
    # The ledger file is gone (review of QF-126, round 2, and its ruling 1): held whatever the run's copy says. Its
    # own credit lines are never taken as the whole set, and a copy without one cannot show that none is asked.
    gone = _recordless_carousel(tmp_path / "gone", images, ledger=False)
    per_photo = "\n\n".join([SHARE_ALIKE_LINE] + [asset_attribution(r) for r in images[:2]])
    for copy in (payload,                                                          # the real shape: a summary line only
                 dict(payload, caption="Reel text.\n\n" + asset_attribution(images[0])),   # part of the credits kept
                 dict(payload, description="The story.\n\n" + per_photo),          # every line the ledger had
                 dict(payload, carousel_caption="Swipe.\n\n" + asset_attribution(images[1])),
                 {"carousel_caption": "Swipe."}):                                  # no credit line at all
        with pytest.raises(ValueError, match="drawn before slide records were kept.*the episode's provenance record "
                                             "is missing$") as held:
            _coordinator()._carousel_request("r1", copy, gone)
        assert type(held.value).__name__ == "CarouselCreditHold" and held.value.final is False
    # A photo of the run whose credit is incomplete (no source link): not posted either.
    broken = _recordless_carousel(tmp_path / "broken", [{**PHOTO, "source_url": ""}])
    with pytest.raises(ValueError, match="the credit of one lacks its source link") as held:
        _coordinator()._carousel_request("r1", payload, broken)
    assert type(held.value).__name__ == "CarouselCreditHold"
    # the photo is named; its credit is not quoted with a link cut short
    assert "https://" not in str(held.value) and "photo “File:Vault.jpg” by Ann Lee" in str(held.value)
    # Slides with records beside one without: the ledger's lines, each once, and the records' own lines are kept.
    from src import multi_format as mf
    mixed = _recordless_carousel(tmp_path / "mixed", images)
    real = mf.carousel_credits
    try:
        mf.carousel_credits = lambda shown: ([mf.CAROUSEL_SHARE_ALIKE, asset_attribution(images[1]), mf.CAROUSEL_ADAPTED], False)
        caption, comment = _coordinator()._carousel_request("r1", payload, mixed)
    finally:
        mf.carousel_credits = real
    assert comment == "" and all(caption.count(asset_attribution(r)) == 1 for r in images[:2])
    assert caption.count(mf.CAROUSEL_ADAPTED) == 1 and caption.count(mf.CAROUSEL_SHARE_ALIKE) == 1
    # A run none of whose photos asks for a credit posts as before.
    free = _recordless_carousel(tmp_path / "free", [images[2]])
    assert _coordinator()._carousel_request("r1", {"carousel_caption": "Swipe."}, free) == ("Swipe.", "")


def test_a_carousel_held_for_its_credits_offers_redraw_only_when_a_redraw_can_be_made(tmp_path, monkeypatch):
    """The hold names what can be done, and only what can: a run with a plan to draw from gets the hold alert's
    Redraw; a run drawn before carousels were planned has nothing to redraw from (the button would only answer
    that), so its alert says it will not post. Measured: every real carousel held this way is of the second kind."""
    import main
    from src import carousel_lazy
    said, saved = [], []
    monkeypatch.setattr(main, "_notice", lambda self, text, **kw: said.append((text, kw)))
    coordinator = _coordinator()
    coordinator.db = SimpleNamespace(get_run=lambda run_id: {"topic_id": "t1"}, run_label=lambda run_id: "The stair",
                                     run_key=lambda run_id: "k1",
                                     update_scheduled_post=lambda post_id, status, **kw: saved.append((post_id, status, kw)))
    # (were it sent, as before this rule: a publisher that says so, so the difference is this test's assertion)
    coordinator.publisher = SimpleNamespace(publish_photos=lambda *a, **k: {"status": "published"})
    coordinator._close_booking_item = lambda post: None
    slides = _recordless_carousel(tmp_path, [PHOTO], ledger="{not json")
    post = {"id": 7, "run_id": "r1", "kind": "carousel", "files": slides}
    run = {"script_payload": {"carousel_caption": "Swipe."}, "topic_id": "t1"}

    assert carousel_lazy.plan_problem(tmp_path / "run_old")                      # no plan: nothing to redraw from
    assert main.PipelineCoordinator._post_photos(coordinator, post, run, "carousel") == "failed"
    text, kw = said[-1]
    assert "Carousel not posted" in text and "provenance record could not be read" in text
    assert "It cannot be redrawn" in text and "This carousel will not post" in text
    assert "carredraw" not in str(kw.get("buttons")) and "retrypost" not in str(kw)
    assert saved[-1][1] == "failed" and saved[-1][2]["detail"]["final"] is True

    monkeypatch.setattr(carousel_lazy, "plan_problem", lambda run_dir: None)      # a planned run: a redraw can be made
    assert main.PipelineCoordinator._post_photos(coordinator, post, run, "carousel") == "failed"
    text, kw = said[-1]
    assert "Carousel held" in text and "provenance record could not be read" in text
    assert "A redraw gives its slides records" in text and "carredraw:7" in str(kw["buttons"])
    assert saved[-1][1] == "failed" and saved[-1][2]["detail"].get("held") is True and "final" not in saved[-1][2]["detail"]


def _recorded_carousel(tmp_path, photos, credits=True, ledger=None, lineage=None):
    """A carousel whose slides keep records (still_layout.json: the photo each shows), as every run since slide
    records does and every carousel drawn at its slot (QF-81). `photos`: {file name: its credit record}; `credits`:
    True writes credits.json beside the photos, a string writes that text as the file, None writes none; `ledger`:
    the run's provenance.json (a function of the run folder giving its assets, or a string written as it is); `lineage`: {photo name: the name of the file
    it was made from}, written as the photo's lineage file. Returns (slide paths, run folder)."""
    from src.credits import sha256_file
    run_dir = tmp_path / "run_new"
    (run_dir / "carousel").mkdir(parents=True)
    (run_dir / "photos").mkdir()
    slides, records = [], {}
    for n, name in enumerate(photos, 1):
        (run_dir / "photos" / name).write_bytes(name.encode())
        slide = run_dir / "carousel" / f"carousel_slide_{n:02d}.jpg"
        slide.write_bytes(b"slide")
        records[slide.name] = {"recipe": {"photo": str(run_dir / "photos" / name)}}
        slides.append(str(slide))
    (run_dir / "carousel" / "still_layout.json").write_text(json.dumps(records), encoding="utf-8")
    if credits is True:
        credits = json.dumps({"images": [{**record, "file": name} for name, record in photos.items()]})
    if credits is not None:
        (run_dir / "photos" / "credits.json").write_text(credits, encoding="utf-8")
    for name, source in (lineage or {}).items():
        made_from = run_dir / "photos" / source
        made_from.write_bytes(source.encode())
        (run_dir / "photos" / (name + ".lineage.json")).write_text(json.dumps({"sources": [
            {"path": str(made_from), "sha256": sha256_file(made_from)}]}), encoding="utf-8")
    if ledger is not None:
        (run_dir / "provenance.json").write_text(
            ledger if isinstance(ledger, str) else json.dumps({"assets": ledger(run_dir)}), encoding="utf-8")
    return slides, run_dir


def _ledger_asset(run_dir, name, record, **more):
    """A photo's record as credits.generate_provenance_ledger keeps it: the file's resolved path and checksum, then
    its rights."""
    import hashlib
    return {"path": str((run_dir / "photos" / name).resolve()), "sha256": hashlib.sha256(name.encode()).hexdigest(),
            "file": name, "rights_status": "verified", "origin": "source_media", **record, **more}


DOOR = {**PHOTO, "artist": "Jan Novak", "title": "File:Door.jpg",
        "source_url": "https://commons.wikimedia.org/wiki/File:Door.jpg"}


@pytest.mark.parametrize("credits", [True, None, "{not json"], ids=["present", "missing", "corrupt"])
def test_a_recorded_slide_is_credited_from_the_ledger_when_its_credit_entry_cannot_be_read(tmp_path, credits):
    """Review of QF-126, round 2: a slide's photo whose credits.json entry could not be read (the file missing or
    corrupt) was skipped, and the carousel was sent with no credit for it, though the run's ledger asks one (18 of
    19 production-sample carousels with slide records, read where their credit files are not). The ledger's record
    of the photo decides: the same caption whether the entry is read or not."""
    from src.credits import asset_attribution
    from src.multi_format import CAROUSEL_ADAPTED
    slides, _ = _recorded_carousel(tmp_path, {"vault.jpg": PHOTO}, credits=credits, ledger=lambda d: [
        _ledger_asset(d, "vault.jpg", PHOTO), _ledger_asset(d, "door.jpg", DOOR)])
    caption, comment = _coordinator()._carousel_request("r1", {"carousel_caption": "Swipe through the vault."}, slides)
    assert comment == ""
    # the photo it shows, and only that one: the ledger's other photo is not on a slide
    assert caption == "Swipe through the vault.\n\n" + asset_attribution(PHOTO) + "\n" + CAROUSEL_ADAPTED


def test_an_unread_credit_entry_asks_what_the_ledger_records_for_that_photo(tmp_path):
    from src.credits import asset_attribution
    from src.multi_format import CAROUSEL_ADAPTED, CAROUSEL_SHARE_ALIKE
    payload = {"carousel_caption": "Swipe."}

    def request(name, record_of, **kw):
        slides, _ = _recorded_carousel(tmp_path / name, {"vault.jpg": PHOTO}, credits=None, ledger=record_of, **kw)
        return _coordinator()._carousel_request("r1", payload, slides)

    # An illustration the run drew itself asks for no credit, and neither does a public-domain photo: sent as it is.
    drawn = {"artist": "AI illustration (an image model)", "license": "AI-generated illustration, not a photograph",
             "attribution_required": False, "origin": "archaeological_reconstruction",
             "illustration": {"origin": "ai_illustration", "model": "an image model"}}
    assert request("drawn", lambda d: [_ledger_asset(d, "vault.jpg", drawn)]) == ("Swipe.", "")
    free = {**PHOTO, "license": "Public domain", "license_url": None, "attribution_required": False}
    assert request("free", lambda d: [_ledger_asset(d, "vault.jpg", free)]) == ("Swipe.", "")
    # ... but an illustration drawn from a CC BY photo carries that photo's credit, as the Reel does
    based = {**drawn, "illustration": {**drawn["illustration"], "references": [
        {"title": DOOR["title"], "artist": DOOR["artist"], "licence": DOOR["license"], "url": DOOR["source_url"],
         "license_url": DOOR["license_url"]}]}}
    caption, _ = request("based", lambda d: [_ledger_asset(d, "vault.jpg", based)])
    assert caption.splitlines()[2:] == [asset_attribution(DOOR), CAROUSEL_ADAPTED]
    # A share-alike photo brings the slides' share-alike notice with its line.
    alike = {**PHOTO, "license": "CC BY-SA 4.0", "license_url": "https://creativecommons.org/licenses/by-sa/4.0/"}
    caption, _ = request("alike", lambda d: [_ledger_asset(d, "vault.jpg", alike)])
    assert caption.splitlines()[2:] == [CAROUSEL_SHARE_ALIKE, asset_attribution(alike), CAROUSEL_ADAPTED]
    # Found by checksum through its lineage: the slide shows a crop, the ledger holds the photo it was cut from
    # (recorded under another folder's path, as a run folder read on another machine has it).
    import hashlib
    moved = {**PHOTO, "path": "elsewhere/photos/original.jpg", "file": "original.jpg",
             "sha256": hashlib.sha256(b"original.jpg").hexdigest()}
    caption, _ = request("crop", lambda d: [moved], lineage={"vault.jpg": "original.jpg"})
    assert caption.splitlines()[2:] == [asset_attribution(PHOTO), CAROUSEL_ADAPTED]
    # ... and the other way: the ledger holds a crop made from the photo the slide shows.
    cut = {**PHOTO, "path": "elsewhere/graphics/crop.jpg", "sha256": "0" * 64}
    caption, _ = request("cut", lambda d: [{**cut, "source_paths": [str((d / "photos" / "vault.jpg").resolve())]}])
    assert caption.splitlines()[2:] == [asset_attribution(PHOTO), CAROUSEL_ADAPTED]
    # A credit the ledger holds incomplete (no source link) is not posted, and the photo is named, not regenerated:
    # its Reel is already out.
    with pytest.raises(ValueError, match="the credit of a photo it shows .* lacks its source link") as held:
        request("lacking", lambda d: [_ledger_asset(d, "vault.jpg", {**PHOTO, "source_url": ""})])
    assert type(held.value).__name__ == "CarouselCreditHold" and "Regenerate" not in str(held.value)

    # The ledger does not hold the photo (a carousel may show a photo its Reel did not use), or holds it without a
    # licence: nothing says what it asks, and the run's other credits are no credit for it. Held, never filled from
    # them; and held when the ledger is missing or cannot be read.
    for name, assets, why in (
            ("absent", lambda d: [_ledger_asset(d, "door.jpg", DOOR)], "does not hold that photo with a licence"),
            ("no_licence", lambda d: [_ledger_asset(d, "vault.jpg", {"title": "vault"}), _ledger_asset(d, "door.jpg", DOOR)],
             "does not hold that photo with a licence"),
            ("missing", None, "is missing"), ("corrupt", "{not json", "could not be read")):
        with pytest.raises(ValueError, match="^the credit record of 1 of its photos .* cannot be read, and the "
                                             f"episode's provenance record {why}, so nothing says") as held:
            request(name, assets)
        assert type(held.value).__name__ == "CarouselCreditHold" and held.value.final is False, name
        assert "Restore or correct the photo's credit record" in held.value.redraw
    # A derived copy whose lineage file is gone is the same case: nothing leads back to its photo's entry.
    slides, _ = _recorded_carousel(tmp_path / "lineage_gone", {"vault.jpg": PHOTO}, credits=json.dumps(
        {"images": [{**PHOTO, "file": "original.jpg"}]}))
    with pytest.raises(ValueError, match="credit record of 1 of its photos .* provenance record is missing"):
        _coordinator()._carousel_request("r1", payload, slides)
    # A deck whose every entry is read needs no ledger: it is sent, never held for a missing one.
    slides, _ = _recorded_carousel(tmp_path / "whole", {"vault.jpg": PHOTO, "door.jpg": DOOR})
    caption, comment = _coordinator()._carousel_request("r1", payload, slides)
    assert comment == "" and caption.splitlines()[2:] == [asset_attribution(PHOTO), asset_attribution(DOOR),
                                                          CAROUSEL_ADAPTED]


def _holding_coordinator(monkeypatch):
    """A coordinator whose notices and booking updates are kept, and whose publisher says "published" (were the
    carousel sent, as before these rules, the test's own assertion is what fails)."""
    import main
    said, saved = [], []
    monkeypatch.setattr(main, "_notice", lambda self, text, **kw: said.append((text, kw)))
    coordinator = _coordinator()
    coordinator.db = SimpleNamespace(get_run=lambda run_id: {"topic_id": "t1"}, run_label=lambda run_id: "The stair",
                                     run_key=lambda run_id: "k1",
                                     update_scheduled_post=lambda post_id, status, **kw: saved.append((post_id, status, kw)))
    coordinator.publisher = SimpleNamespace(publish_photos=lambda *a, **k: {"status": "published"})
    coordinator._close_booking_item = lambda post: None
    return coordinator, said, saved


@pytest.mark.parametrize("planned", [False, True], ids=["no_plan", "planned"])
def test_credits_that_do_not_fit_beside_slide_records_hold_a_carousel_that_will_not_post(tmp_path, monkeypatch, planned):
    """Review of QF-126, round 2: the refusal told the operator to shorten the caption with Edit at its review, and
    its one button was Retry. The review is approved and the Reel posted by then (Edit answers "no longer waiting
    for review"), and a retry or a redraw meets the same photos and caption. It is held as a carousel that will not
    post, with what is true and Details only, whether or not it has a plan."""
    import main
    from src import carousel_lazy
    coordinator, said, saved = _holding_coordinator(monkeypatch)
    slides, _ = _recorded_carousel(tmp_path, {"vault.jpg": PHOTO, "door.jpg": DOOR})
    if planned:
        monkeypatch.setattr(carousel_lazy, "plan_problem", lambda run_dir: None)
    post = {"id": 7, "run_id": "r1", "kind": "carousel", "files": slides}
    run = {"script_payload": {"carousel_caption": "Swipe through the rooms. " + "x" * 1900}, "topic_id": "t1"}
    assert main.PipelineCoordinator._post_photos(coordinator, post, run, "carousel") == "failed"
    (text, kw), = said
    assert text.startswith("❌ <b>Carousel not posted</b> for The stair: 3 credit line(s) of its photos (")
    assert "do not fit its caption (1,925 of 2,200 characters used)" in text
    assert "the caption was approved with its review and cannot be changed now." in text
    assert text.endswith("This carousel will not post. Nothing was posted, and its Reel and other posts are not affected.")
    assert "Edit" not in text and "etry" not in text and "edraw" not in text
    assert kw["buttons"] == [[{"text": "📋 Details", "callback_data": "cpost:k1"}]] and not kw.get("actions")
    assert kw["section"] == "needs" and kw["key"] == "post-failed:7" and kw["replace"] is True
    assert saved == [(7, "failed", {"detail": {"error": saved[0][2]["detail"]["error"], "held": True, "final": True}})]


def test_a_credit_hold_is_said_whole_and_names_only_what_can_be_done(tmp_path, monkeypatch):
    """Review of QF-126, round 2, notes: a hold notice was cut at 400 characters inside its last clause and quoted a
    credit with its link cut; "Regenerate without this photo" was said of a carousel whose Reel had posted."""
    import main
    from src import carousel_lazy
    coordinator, said, _ = _holding_coordinator(monkeypatch)
    run = {"script_payload": {"carousel_caption": "Swipe. " + "x" * 2100}, "topic_id": "t1"}
    # a slide without a record, a ledger credit that lacks both links, and a plan that cannot be read: every clause
    # is said (the reason alone passes the 400 characters other holds are cut at), and the photo is named, not quoted
    no_plan = ("its plan (carousel_plan.json) cannot be read (JSONDecodeError: Expecting property name enclosed in "
               "double quotes: line 1 column 2 (char 1)), so its slides cannot be drawn")
    monkeypatch.setattr(carousel_lazy, "plan_problem", lambda run_dir: no_plan)
    lacking = {**PHOTO, "artist": "Workshop of the Master of the Long Gallery, Upper Town",
               "title": "File:The lower stair and its rolling stone door, east side.jpg", "source_url": "",
               "license_url": ""}
    slides = _recordless_carousel(tmp_path / "long", [lacking, DOOR])
    assert main.PipelineCoordinator._post_photos(
        coordinator, {"id": 8, "run_id": "r1", "kind": "carousel", "files": slides}, run, "carousel") == "failed"
    text, kw = said[-1]
    reason = text.split(": ", 1)[1].split(chr(10) * 2)[0]
    assert len(reason) > 400 and reason.endswith(
        "lacks its source link and its licence link (photo “File:The lower stair and its rolling stone door, east…” "
        "by Workshop of the Master of the Long Gallery, Upper Town). It cannot be redrawn: " + no_plan + "."), text
    assert text.endswith(chr(10) * 2 + "This carousel will not post. Nothing was posted, and its Reel and other posts "
                         "are not affected.") and "https://" not in text
    assert "retrypost" not in str(kw) and "carredraw" not in str(kw)

    # an author the source names printed "Unknown" on a slide's photo: named, never "Regenerate" (the Reel is out);
    # with a plan the Redraw is offered with what must be done first, without one it will not post
    unknown = {**PHOTO, "artist": "Hnos. Garcia Sta. Cruz"}
    slides, _ = _recorded_carousel(tmp_path / "author", {"vault.jpg": unknown})
    run = {"script_payload": {"carousel_caption": "Swipe."}, "topic_id": "t1"}
    post = {"id": 9, "run_id": "r1", "kind": "carousel", "files": slides}
    assert main.PipelineCoordinator._post_photos(coordinator, post, run, "carousel") == "failed"
    text, kw = said[-1]
    assert "photo “File:Vault.jpg” by Unknown) lacks its author (the source names one: check the credit)" in text
    assert "Regenerate" not in text and "https://" not in text and "This carousel will not post" in text
    assert "carredraw" not in str(kw) and "retrypost" not in str(kw)
    monkeypatch.setattr(carousel_lazy, "plan_problem", lambda run_dir: None)
    assert main.PipelineCoordinator._post_photos(coordinator, post, run, "carousel") == "failed"
    text, kw = said[-1]
    assert "Carousel held" in text and "Regenerate" not in text and "retrypost" not in str(kw)
    assert "credit record (credits.json beside the photo) first; a redraw then credits the photo from it" in text
    assert kw["buttons"] == [[{"text": "🔁 Redraw and post next slot", "callback_data": "carredraw:9"}]]
    assert all(len(b["text"]) <= 28 for row in kw["buttons"] for b in row)


coord = _lazy.coord   # the slot loop of QF-81 (a carousel drawn only when it takes its slot), with that row's fixture


def test_a_carousel_drawn_at_its_slot_is_credited_from_the_ledger_or_held(coord, monkeypatch):
    """The lazy slot path (QF-81): its slides always have records, and carousel_lazy checks its photos' bytes, not
    their credit entries. A photo whose entry cannot be read is credited from the run's ledger at the slot; with no
    ledger either, the carousel is held and nothing is posted."""
    from src.credits import asset_attribution, sha256_file
    from src.multi_format import CAROUSEL_ADAPTED
    sent = []
    monkeypatch.setattr(coord.publisher, "publish_photos", lambda run_id, tid, kind, files, caption="", **k:
                        sent.append((run_id, caption, k)) or {"status": "published"})
    draw = coord.multiformat_gen.generate_carousel_sequence

    def recorded(stills, payload, output_dir, facebook=False, **kw):
        slides = draw(stills, payload, output_dir, facebook=facebook, **kw)
        (slides[0].parent / "still_layout.json").write_text(json.dumps({
            s.name: {"elements": [], "recipe": {"photo": str(stills[0])}} for s in slides}),
            encoding="utf-8")
        return slides
    monkeypatch.setattr(coord.multiformat_gen, "generate_carousel_sequence", recorded)
    without, _ = coord.make("r_none", _lazy.TUE - timedelta(days=3))
    (without / "provenance.json").unlink(missing_ok=True)        # this run's ledger is gone
    with_ledger, _ = coord.make("r_ledger", _lazy.TUE - timedelta(days=3, hours=1))   # the newer episode: first in line
    photo = with_ledger / "photos" / "p1.jpg"
    (with_ledger / "provenance.json").write_text(json.dumps({"assets": [
        {**PHOTO, "path": str(photo.resolve()), "sha256": sha256_file(photo)}]}), encoding="utf-8")
    assert not (photo.parent / "credits.json").exists()      # no credit entry beside either run's photos

    _lazy._slot(coord, _lazy.TUE)                                 # the newer episode takes Tuesday's slot
    assert [run for run, _, _ in sent] == ["r_ledger"]
    assert sent[0][1].splitlines()[-2:] == [asset_attribution(PHOTO), CAROUSEL_ADAPTED] and "first_comment" not in sent[0][2]
    _lazy._slot(coord, _lazy.TUE + timedelta(days=1))            # the other one is drawn on Wednesday, and held
    assert [run for run, _, _ in sent] == ["r_ledger"]
    post = _lazy._carousels(coord, "r_none")[0]
    assert post["status"] == "failed" and post["detail"].get("held") is True
    assert "credit record of 1 of its photos" in coord.alerts[-1] and "provenance record is missing" in coord.alerts[-1]
    assert "Carousel held" in coord.alerts[-1] and "carredraw" in str(coord.buttons[-1])


# ---------------------------------------------------------------- R4: no generated music in the library

def test_generated_music_never_enters_or_plays_from_the_library(tmp_path, monkeypatch):
    from src import music
    monkeypatch.setattr(music, "MUSIC_DIR", tmp_path / "music")
    monkeypatch.setattr(music, "LIBRARY", tmp_path / "music" / "library.json")
    generated = {"title": "Score", "creator": "ElevenLabs", "source": "elevenlabs", "file": "score.mp3",
                 "source_url": "https://elevenlabs.io/music/1", "license": "by", "license_version": "4.0"}
    with pytest.raises(ValueError, match="generated music"):
        music.register_download(generated, b"audio")
    with pytest.raises(ValueError, match="generated music"):   # a run's score copied in under another source's name
        music.register_download({**TRACK, "file": "music_elevenlabs.mp3"}, b"audio")
    assert not (tmp_path / "music" / "score.mp3").exists() and music.load_library(available_only=False) == []
    # ... and a record put in the library file by hand is never picked
    assert music.in_rotation({**generated, "reviewed": True}, retired={}) is False
    assert music.in_rotation({**TRACK, "file": "strings.mp3"}, retired={}) is True
    assert music.register_download({**TRACK, "file": "strings.mp3"}, b"audio") is True


def test_no_library_track_is_generated_music():
    lib = json.loads((ROOT / "data" / "music" / "library.json").read_text(encoding="utf-8"))
    named = [t["file"] for t in lib if "elevenlabs" in json.dumps(t).lower()]
    assert lib and named == []


# ---------------------------------------------------------------- R5: reserved font names and OFL notices

def _derived_face(tmp_path, source: Path, rename: str = "", drop: tuple = ()):
    """A subset of a bundled face in its own fonts folder, with evidence as scripts/build_fallback_fonts.py writes
    it (the subset's own hash, "subset with fontTools", the upstream licence text beside it)."""
    from fontTools import subset
    from fontTools.ttLib import TTFont

    from src.credits import sha256_file
    folder = tmp_path / "fonts"
    (folder / "licenses").mkdir(parents=True, exist_ok=True)
    evidence = json.loads((source.parent / "license_evidence.json").read_text(encoding="utf-8"))
    record = dict(next(r for r in evidence["fonts"] if r["file"] == source.name))
    options = subset.Options()
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    font = TTFont(str(source))
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=range(0x20, 0x7F))
    subsetter.subset(font)
    for name in list(font["name"].names):
        if name.nameID in drop:
            font["name"].removeNames(nameID=name.nameID)
        elif rename and name.nameID in (1, 3, 4, 6, 16, 18, 21):
            text = name.toUnicode().replace("IBM Plex Mono", rename).replace("IBMPlexMono", rename.replace(" ", ""))
            font["name"].setName(text, name.nameID, name.platformID, name.platEncID, name.langID)
    out = folder / "Derived-Face.ttf"
    font.save(str(out))
    licence = folder / "licenses" / "Derived-Face.ttf.OFL.txt"
    shutil.copyfile(source.parent / record["license_file"], licence)
    record.update(file=out.name, sha256=sha256_file(out), license_file="licenses/" + licence.name,
                  license_sha256=sha256_file(licence), release="subset with fontTools to U+0020-007E")
    (folder / "license_evidence.json").write_text(json.dumps({"fonts": [record]}), encoding="utf-8")
    return out


def test_a_derived_face_may_not_keep_a_reserved_font_name_and_keeps_its_ofl_notices(tmp_path):
    from src.font_evidence import font_rights
    plex = ROOT / "fonts" / "IBMPlexMono-Medium.ttf"
    valid, record = font_rights(_derived_face(tmp_path / "kept", plex))
    assert not valid, "a subset of IBM Plex that still calls itself Plex passed the font rights check"
    assert "Reserved Font Name \"Plex\"" in " ".join(record["licence_problems"])
    assert font_rights(_derived_face(tmp_path / "renamed", plex, rename="RR Mono"))[0] is True
    for drop, missing in (((0,), "no copyright notice"), ((13,), "no SIL Open Font License notice")):
        valid, record = font_rights(_derived_face(tmp_path / f"drop{drop[0]}", plex, rename="RR Mono", drop=drop))
        assert not valid and missing in " ".join(record["licence_problems"]), (drop, record)
    # a face whose licence reserves no name keeps its name when subset (the Noto builds), with both notices
    assert font_rights(_derived_face(tmp_path / "noto", ROOT / "fonts" / "NotoSans-LGC.ttf"))[0] is True


def test_every_bundled_face_keeps_its_notices_and_plex_is_an_unmodified_copy():
    from src.font_evidence import (
        face_licence_problems,
        font_rights,
        recorded_unmodified,
        reserved_font_names,
    )
    fonts = ROOT / "fonts"
    evidence = {r["file"]: r for r in json.loads((fonts / "license_evidence.json").read_text(encoding="utf-8"))["fonts"]}
    faces = sorted(fonts.glob("*.[ot]tf"))
    assert faces and {f.name for f in faces} == set(evidence)        # every face has a licence record, and no other
    reserved = {}
    for face in faces:
        record = evidence[face.name]
        licence = (fonts / record["license_file"]).read_text(encoding="utf-8")
        assert font_rights(face) == (True, record), face.name
        assert face_licence_problems(face, licence, unmodified=recorded_unmodified(record)) == [], face.name
        if reserved_font_names(licence):
            reserved[face.name] = reserved_font_names(licence)
            assert recorded_unmodified(record), f"{face.name} carries a reserved name and is not an upstream copy"
    # "unmodified" is read as the record's own statement, not as a word anywhere in it (review of round 1)
    assert recorded_unmodified({"release": "x 1.0; TTF copied  unmodified"})
    assert not recorded_unmodified({"release": "a subset of the unmodified upstream"})
    assert reserved == {"IBMPlexMono-Medium.ttf": ["Plex"], "IBMPlexMono-SemiBold.ttf": ["Plex"]}


def test_reserved_font_names_are_read_from_the_copyright_lines_only():
    from src.font_evidence import reserved_font_names
    assert reserved_font_names('Copyright © 2017 IBM Corp. with Reserved Font Name "Plex"\n\nThis Font Software '
                               'is licensed under the SIL Open Font License') == ["Plex"]
    assert reserved_font_names('Copyright (c) 2010, A. Author,\nwith Reserved Font Names "Alpha" and "Beta Sans".') \
        == ["Alpha", "Beta Sans"]
    assert reserved_font_names("Copyright 2011 A. Author, with Reserved Font Name Gamma.") == ["Gamma"]
    assert reserved_font_names('Copyright 2020 The Anton Project Authors\n\nThis Font Software is licensed under '
                               'the SIL Open Font License, Version 1.1.\n"Reserved Font Name" refers to any names '
                               "specified as such after the copyright statement(s).") == []


# ---------------------------------------------------------------- R6: sources refused at ingest

RUMSEY = {"license": "CC BY 4.0", "source_url": "https://www.davidrumsey.com/luna/servlet/detail/1",
          "license_url": BY4, "title": "Map", "artist": "Cartographer"}


def _commons_page(credit, licence="CC BY 4.0", url="https://commons.wikimedia.org/wiki/File:Map.jpg"):
    return {"title": "File:Map.jpg", "categories": [], "imageinfo": [{
        "mime": "image/jpeg", "width": 4000, "height": 3000, "url": "u", "size": 1000, "descriptionurl": url,
        "extmetadata": {"Artist": {"value": "A. Cartographer"}, "Credit": {"value": credit},
                        "LicenseShortName": {"value": licence}}}]}


def test_a_commons_file_from_an_excluded_source_is_not_a_candidate():
    from src import commons
    def found(page):
        return len(list(commons.candidates([page], [], allow_sa=True)))
    assert found(_commons_page("Own work")) == 1
    assert found(_commons_page('<a href="https://www.davidrumsey.com/x">David Rumsey Map Collection</a>')) == 0
    assert found(_commons_page("David Rumsey Historical Map Collection")) == 0
    assert found(_commons_page("https://www.esa.int/ESA_Multimedia/Images/2020/01/x")) == 0
    assert found(_commons_page('<a href="//www.davidrumsey.com/luna/servlet/detail/1">source</a>')) == 0   # by its link
    assert found(_commons_page("ESA/Rosetta/NavCam")) == 0
    assert found(_commons_page("European Space Agency", licence="CC BY-SA 3.0 IGO")) == 1    # the one ESA licence
    assert found(_commons_page("ESA/Rosetta/NavCam", licence="CC BY-SA 3.0 IGO")) == 1
    joint = found(_commons_page("NASA, ESA and the Hubble Heritage Team", licence="Public domain"))
    assert joint == found(_commons_page("NASA and the Hubble Heritage Team", licence="Public domain"))   # NASA's record
    assert found(_commons_page("A photograph of the BBC building, own work")) == 1          # a subject, not a source


def test_an_excluded_source_is_never_counted_as_cleared_or_offered_by_a_search(monkeypatch):
    from src import museum_sources
    from src.credits import rights_evidence_ok
    assert rights_evidence_ok({**RUMSEY, "source_url": "https://commons.wikimedia.org/wiki/File:Map.jpg"}) is True
    assert rights_evidence_ok(RUMSEY) is False
    assert rights_evidence_ok({**RUMSEY, "source_url": "https://www.esa.int/x", "license": "CC BY 4.0"}) is False
    assert rights_evidence_ok({**RUMSEY, "source_url": "https://www.esa.int/x", "license": "CC BY-SA 3.0 IGO",
                               "license_url": "https://creativecommons.org/licenses/by-sa/3.0/igo/"}) is \
        rights_evidence_ok({**RUMSEY, "source_url": "https://example.org/x", "license": "CC BY-SA 3.0 IGO",
                            "license_url": "https://creativecommons.org/licenses/by-sa/3.0/igo/"})
    good = {"title": "Stone stair", "license": "CC0", "download_url": "https://example.org/a.jpg",
            "source_url": "https://example.org/a"}
    blocked = {**good, "source_url": "https://www.davidrumsey.com/a", "download_url": "https://example.org/b.jpg"}
    monkeypatch.setattr(museum_sources, "SOURCES", {"fake": lambda query: [dict(good), dict(blocked)]})
    monkeypatch.setattr(museum_sources, "_names_subject", lambda cand, kws: True)
    assert [c["source_url"] for c in museum_sources.search_all("stair", ["stair"], sources=("fake",))] == [good["source_url"]]


def test_an_excluded_source_is_refused_at_upload_and_in_the_music_library(tmp_path, monkeypatch):
    from src import music
    from src.image_upload import ImageUpload
    monkeypatch.setattr(music, "MUSIC_DIR", tmp_path / "music")
    monkeypatch.setattr(music, "LIBRARY", tmp_path / "music" / "library.json")
    bbc = {**TRACK, "file": "bbc.mp3", "source": "BBC Sound Effects",
           "source_url": "https://sound-effects.bbcrewind.co.uk/search?q=07042178"}
    with pytest.raises(ValueError, match="excluded.*BBC Sound Effects"):
        music.register_download(bbc, b"audio")
    assert not (tmp_path / "music" / "bbc.mp3").exists()

    # The guided upload: the source page is refused where it is typed; ESA waits for the licence.
    states = {}
    settings = SimpleNamespace(get_setting=lambda key: states.get(key), set_setting=lambda key, value: states.update({key: value}))
    upload = ImageUpload(settings, tmp_path / "staging", tmp_path / "assets")

    def at_source():
        states[upload.key(1, 2)] = json.dumps({"chat": 1, "user": 2, "id": "u1", "topic": "t1", "title": "T",
                                               "stage": "source", "message_id": 1, "rights": {}})
    at_source()
    with pytest.raises(ValueError, match="excluded.*David Rumsey"):
        upload.answer(1, 2, "https://www.davidrumsey.com/luna/servlet/detail/1")
    assert upload.load(1, 2)["stage"] == "source" and upload.load(1, 2)["rights"] == {}
    assert upload.answer(1, 2, "https://www.esa.int/ESA_Multimedia/Images/x")["stage"] != "source"
    at_source()
    assert upload.answer(1, 2, "https://commons.wikimedia.org/wiki/File:Map.jpg")["stage"] != "source"

    # ... and the image is refused when it is added, whatever licence was typed; ESA only under CC BY-SA 3.0 IGO.
    from src import telegram_images
    reached = []
    monkeypatch.setattr(telegram_images, "topic_for", lambda db, ref: reached.append(ref) or (_ for _ in ()).throw(
        LookupError("past the source check")))

    def add(source, licence):
        telegram_images.add_image(None, tmp_path / "assets", "t1", {"source": source, "licence": licence,
                                                                    "author": "A"}, b"image", "2")
    with pytest.raises(ValueError, match="excluded.*David Rumsey"):
        add("https://www.davidrumsey.com/luna/servlet/detail/1", "CC BY 4.0")
    with pytest.raises(ValueError, match="excluded.*ESA"):
        add("https://www.esa.int/ESA_Multimedia/Images/x", "CC BY 4.0")
    assert reached == []
    for source, licence in (("https://www.esa.int/ESA_Multimedia/Images/x", "CC BY-SA 3.0 IGO"),
                            ("https://commons.wikimedia.org/wiki/File:Map.jpg", "CC BY 4.0")):
        with pytest.raises(LookupError):
            add(source, licence)
    assert reached == ["t1", "t1"]


# ---------------------------------------------------------------- review of round 1

ESA_BY4 = {"license": "CC BY 4.0", "license_url": BY4, "title": "Nebula",
           "source_url": "https://commons.wikimedia.org/wiki/File:Nebula.jpg"}


def _cleared(**fields):
    """Whether the source policy lets the record through (the licence gate is another check)."""
    try:
        from src.source_policy import blocked_source
    except ImportError:   # before QF-126 there was no rule: every record was let through
        return True
    return not blocked_source({**ESA_BY4, **fields})


def test_esa_hubble_and_esa_webb_releases_are_accepted_under_cc_by_4():
    """(a) ESA/Hubble and ESA/Webb publish under CC BY 4.0 on their own sites (ruled 2026-11-14)."""
    assert _cleared(artist="ESA/Hubble", source_url="https://esahubble.org/images/heic0601a/")
    assert _cleared(artist="ESA/Hubble & NASA")                                   # a Commons copy
    assert _cleared(artist="ESA/Webb, NASA & CSA, J. Lee", source_url="https://esawebb.org/images/weic2216b/")
    assert _cleared(artist="ESA", source_url="https://www.spacetelescope.org/images/heic0601a/")
    assert _cleared(artist="ESA/Hubble", license="Creative Commons Attribution 4.0 International",
                    license_url="https://creativecommons.org/licenses/by/4.0/deed.en")      # by its address
    # ... with that licence only, and esa.int's own media stay out
    assert not _cleared(artist="ESA/Hubble", license="CC BY-SA 4.0",
                        license_url="https://creativecommons.org/licenses/by-sa/4.0/")
    assert not _cleared(artist="ESA", source_url="https://www.esa.int/ESA_Multimedia/Images/2020/01/x")


def test_a_photographer_called_esa_is_not_the_space_agency():
    """(b) the agency's token is upper case, as a word."""
    assert _cleared(artist="Esa Virtanen") and _cleared(artist="Photo by Esa Virtanen")
    assert _cleared(artist="Mesa Verde Museum") and _cleared(artist="VESA")
    assert not _cleared(artist="ESA")


def test_the_one_esa_licence_is_recognised_however_it_is_spelled():
    """(c) CC BY-SA 3.0 IGO by a normalised name, or by its address."""
    igo = "https://creativecommons.org/licenses/by-sa/3.0/igo/"
    for spelled in ("CC BY-SA 3.0 IGO", "CC BY-SA IGO 3.0", "CC-BY-SA-3.0-IGO", "cc by-sa 3.0 igo"):
        assert _cleared(artist="ESA/Rosetta/NavCam", license=spelled, license_url=igo), spelled
    assert _cleared(artist="ESA/Rosetta/NavCam", license="CC BY-SA 3.0", license_url=igo)
    assert not _cleared(artist="ESA/Rosetta/NavCam", license="CC BY-SA 3.0",
                        license_url="https://creativecommons.org/licenses/by-sa/3.0/")


def test_esa_is_found_anywhere_in_a_credit_and_a_nasa_first_credit_is_nasas():
    """(d) not only as the first word; "NASA, ESA ..." stays NASA's record."""
    from src.credits import rights_evidence_ok
    assert rights_evidence_ok({**ESA_BY4, "artist": "A. Author"}) is True
    assert rights_evidence_ok({**ESA_BY4, "artist": "Credit: ESA/ATG medialab"}) is False
    assert not _cleared(artist="Credit: ESA/ATG medialab")
    assert not _cleared(artist="DLR / ESA", license="CC BY-SA 4.0",
                        license_url="https://creativecommons.org/licenses/by-sa/4.0/")
    assert not _cleared(artist="A. Author", credit="Image: ESA")
    assert _cleared(artist="NASA, ESA and the Hubble Heritage Team") == _cleared(artist="NASA and the Hubble Heritage Team")
    assert _cleared(artist="NASA/ESA/CSA/STScI")


def test_a_copy_written_before_the_rule_whose_credits_fit_is_never_told_to_shorten(tmp_path):
    """Review of round 1, blocking 3: every run waiting at deploy lacks the adapted notice and the complete music
    line, and for most of them the credits fit; the reason said "Shorten the text" all the same. The reason now says
    they fit and what writes them, and sending the review again writes them."""
    from src.credits import asset_attribution
    short = "🎵 Music: “Slow Strings” by A. Composer (CC BY 4.0)"
    run = _run(tmp_path, {"images": [PHOTO], "music": [TRACK]},
               {"caption": BODY, "description": "", "delivery_profile": "reel", "title": "The stair"})
    stored = {"caption": BODY + "\n" + short + "\n\n" + asset_attribution(PHOTO),
              "description": asset_attribution(PHOTO), "delivery_profile": "reel", "title": "The stair"}
    run = {**run, "status": "pending_approval", "script_payload": dict(stored)}
    held, why = _held(run)
    assert held and "They fit" in why and "nothing needs to be cut" in why, why
    assert "re-send the review or save any Edit" in why and "needs a new version" in why, why      # an action each
    assert "Shorten" not in why and "Regenerate" not in why and "characters over" not in why, why

    # Sent again for review: its credits are written first, saved, and the gate has nothing to hold.
    coordinator = _coordinator()
    saved = []
    coordinator.db = SimpleNamespace(update_run=lambda run_id, status, **fields: saved.append((run_id, status, fields)))
    again = coordinator._recredited(run)
    caption = again["script_payload"]["caption"]
    assert _carried(caption, {"images": [PHOTO], "music": [TRACK]}) == (2, True) and short not in caption
    assert saved == [(run["run_id"], "pending_approval", {"script_payload": again["script_payload"]})]
    assert json.loads((tmp_path / "script_metadata.json").read_text(encoding="utf-8")) == again["script_payload"]
    assert run["script_payload"] == stored                       # the run it was given is not changed in place
    assert _held(again) == (False, "")
    assert coordinator._recredited(again) is again and len(saved) == 1      # a second pass changes nothing

    # ... and that is what a Resend does, before the card is sent: the card shows the copy Approve posts.
    old = {**run, "thumbnail_cover_path": str(tmp_path / "cover.jpg")}
    sent, saved = [], []
    coordinator = _coordinator()
    coordinator.db = SimpleNamespace(
        get_run=lambda run_id: old, get_setting=lambda key: None, get_approval=lambda run_id: None,
        record_approval=lambda *a, **k: None,
        update_run=lambda run_id, status, **fields: saved.append(fields.get("script_payload")))
    coordinator.telegram_bot = SimpleNamespace(
        send_approval_request=lambda video, cover, payload, run_id: sent.append((payload, list(saved))))
    coordinator._decide_review = lambda *a, **k: (None, None)
    assert coordinator.request_approval(run["run_id"])["decision"] == "pending"
    payload, saved_before_the_card = sent[0]
    assert _carried(payload["caption"], {"images": [PHOTO], "music": [TRACK]}) == (2, True), payload["caption"]
    assert payload in saved_before_the_card                      # saved first: Approve reads the run, not the card


def test_a_copy_whose_credits_cannot_fit_is_still_told_so_after_it_is_credited_again(tmp_path):
    credit_set = _set("dev_longest")
    run = _run(tmp_path, credit_set, {"caption": BODY, "description": "", "delivery_profile": "reel", "title": "T"})
    held, why = _held(run)
    assert held and "characters over the platform's 2,200" in why and "They fit" not in why
    coordinator = _coordinator()
    coordinator.db = SimpleNamespace(update_run=lambda *a, **k: None)
    again = coordinator._recredited({**run, "status": "pending_approval"})
    held, why = _held(again)
    assert held and "characters over the platform's 2,200" in why and "They fit" not in why
    assert "Shorten the text so they fit (Edit, for a caption), or Regenerate" in why         # its action


def test_an_author_the_source_names_is_printed_and_an_unknown_beside_a_filled_artist_field_is_held(tmp_path):
    """Review of round 1, blocking 4: "Andrey Korchagin from St. Petersburg, Russia" was credited "Unknown" (the
    full stop of "St." cut the field in two), and the gate called the credit complete."""
    from src.credits import author_name
    assert author_name({"artist": "Andrey Korchagin from St. Petersburg, Russia"}) == "Andrey Korchagin from St. Petersburg, Russia"
    assert author_name({"artist": "John Smith from St. Louis, USA"}) == "John Smith from St. Louis, USA"
    assert author_name({"artist": "Dr. Jane Doe"}) == "Dr. Jane Doe"
    assert author_name({"artist": "Jorge Láscar from Melbourne, Australia"}) == "Jorge Láscar from Melbourne, Australia"
    assert author_name({"artist": "Jane Doe. Please credit me."}) == "Jane Doe"          # a sentence still ends
    payload = {"caption": BODY, "description": "", "delivery_profile": "reel", "title": "The stair"}
    note = "You may use this picture for any purpose as long as the licence terms are followed and a link is given."
    assert author_name({"artist": note}) == "Unknown"
    held, why = _held(_run(tmp_path / "note", {"images": [{**PHOTO, "artist": note}], "music": []}, payload))
    assert held and "lacks its author (the source names one: check the credit, or Regenerate without" in why, why
    for unknown in ("Unknown", "Unknown artistUnknown artist", "", "Anonymous"):
        run = _run(tmp_path / f"u{len(unknown)}", {"images": [{**PHOTO, "artist": unknown}], "music": []}, payload)
        assert "lacks its author" not in _held(run)[1], unknown


def test_the_refusal_list_names_its_sources_only_as_exact_approved_exceptions():
    """The name scan (QF-86: no broadcaster or product name as a quality claim) meets the refusal list, which must
    name the sources it refuses. Each such line is an exact approved exception (file and text hash, with its
    reason); a word added to one of them is unexpected again, as for every other exception."""
    import importlib.util
    import subprocess
    script = ROOT / "docs/reviews/quality-fixes-2026-11/QF-86/measure.py"
    spec = importlib.util.spec_from_file_location("qf86_policy_for_qf126", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    matches = subprocess.check_output(["git", "grep", "-n", "-i", "-E", module.GREP, "--", "src", "scripts", "assets",
                                       "main.py"], cwd=ROOT, text=True, encoding="utf-8").splitlines()
    listed = [line for line in matches if line.startswith(("src/source_policy.py:", "src/music.py:"))]
    assert listed and module.audit_names(ROOT, matches)["unexpected"] == []
    policy = json.loads((script.parent / "approved-name-exceptions.json").read_text(encoding="utf-8"))
    ours = [x for x in policy["domain_contexts"] if x["path"] in ("src/source_policy.py", "src/music.py")]
    assert len(ours) == len(listed) and all("refuse" in x["reason"] and x["text"] for x in ours)
    for line in listed:   # each line on its own: a claim added to it is not covered by its exception
        tampered = [m + " # BBC grade" if m == line else m for m in matches]
        assert module.audit_names(ROOT, tampered)["unexpected"] == [line + " # BBC grade"]

