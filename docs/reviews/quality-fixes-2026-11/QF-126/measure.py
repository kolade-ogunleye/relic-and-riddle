"""QF-126 acceptance metric: the credits a licence asks for, complete, in the text each platform receives.

Usage: python measure.py <repo root>. Prints the metric as JSON. Runs the real code of the repo given (the pipeline's
own add_provenance_attributions, the release gate, MetaPublisher.build_envelope and the publishers' field builders,
PipelineCoordinator._carousel_request, music.register_download, font_evidence.font_rights, the ingest paths), with
inputs committed here or made in a temporary folder. No network, no output/ folder, nothing written in the repo.

What is a complete credit is decided here, not by the code under test: a photo's author, title, licence name, source
link and licence link on one 📷 line; a track's title, author, licence name, source link, licence link and a word
that it was edited on one 🎵 line; one line saying the photos were adapted.

1. `posts`: the three longest real credit sets (tests/fixtures/qf126/real_credit_sets.json: two development runs'
   records, and the production sample's longest set as line lengths only), each as a Reel, a documentary and a
   carousel, whole and cut to what fits. A post is "sent" when the release gate's credit checks pass and its request
   is built; then the text each platform receives is read for the credits.
2. `music`: every track of the committed library as its credit line: which parts it has.
3. `rules`: generated music into the library, a subset of IBM Plex under its own name, the excluded sources at each
   ingest point.

With RR_REAL_OUTPUT set to a folder of real run_* folders (read only; nothing is written there, the gate is not run
on them), `real` counts, for each run: the credits its ledger asks for, and how many its posted texts carry after
the pipeline credits its copy again; what the gate says of the stored copy; and its carousel's caption. Counts only.
"""

import copy
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

BY4 = "https://creativecommons.org/licenses/by/4.0/"
TRACK = {"title": "Slow Strings", "creator": "A. Composer", "license": "by", "license_version": "4.0", "reviewed": True,
         "source": "freesound", "source_url": "https://freesound.org/people/composer/sounds/1", "license_url": BY4}
PHOTO = {"artist": "Ann Lee", "title": "File:Vault.jpg", "license": "CC BY 4.0", "attribution_required": True,
         "source_url": "https://commons.wikimedia.org/wiki/File:Vault.jpg", "license_url": BY4}
BODY = ("Beneath the old city a stair goes down eighteen levels.\n"
        "Each level was cut by hand, and each was sealed with a rolling stone door.\n\n"
        "#History #Archaeology #AncientWorld")
CREDIT_CHECKS = ("image_attribution", "description_attribution", "share_alike_licence", "credit_lines",
                 "ambient_bed_rights", "licence_credits")
NO_CREDIT = ("cc0", "pdm", "pd", "public domain", "publicdomain")


# ---------------------------------------------------------------- what a complete credit is (independent)

def asked(images, tracks):
    from src.credits import author_name, credit_title
    out = []
    for r in images:
        licence = str(r.get("license") or "")
        if not (r.get("attribution_required") or licence.lower().startswith("cc by")):
            continue
        source = str(r.get("source_url") or "\x00")
        if source.lower().startswith("own work"):   # the operator's own photograph: no public page to link
            source = ""
        parts = ("📷", (author_name(r), credit_title(r), licence, source, str(r.get("license_url") or "\x00")))
        if parts not in out:
            out.append(parts)
    for t in tracks:
        licence = str(t.get("license") or "").lower()
        if not licence or licence.startswith(NO_CREDIT):
            continue
        name = "CC BY" if licence in ("by", "cc by") or licence.startswith(("by ", "cc by ")) else licence.upper()
        parts = ("🎵", (str(t.get("title")), str(t.get("creator")), name, str(t.get("license_version") or ""),
                       str(t.get("source_url") or "\x00"), str(t.get("license_url") or "\x00"), "edited"))
        if parts not in out:
            out.append(parts)
    return out


def carried(text, wanted, adapted_needed):
    lines = (text or "").splitlines()
    whole = sum(any(line.startswith(mark) and all(p in line for p in parts) for line in lines) for mark, parts in wanted)
    adapted = (not adapted_needed) or any("adapted" in line and line.startswith(("📷", "🎬")) for line in lines)
    return whole + int(adapted and adapted_needed), len(wanted) + int(adapted_needed)


# ---------------------------------------------------------------- 1. posts made from the real credit sets

def production_longest(sets):
    from src.credits import asset_attribution
    from src.music import credit_line
    images, music = [], []
    for n, length in enumerate(sets["production_longest"]["image_line_lengths"]):
        record = {**PHOTO, "artist": f"Photographer {n}", "title": f"File:Photo {n}.jpg",
                  "source_url": f"https://commons.wikimedia.org/wiki/File:Photo_{n}"}
        record["source_url"] += "_" + "x" * (length - len(asset_attribution(record)) - 1)
        images.append(record)
    for n, length in enumerate(sets["production_longest"]["music_line_lengths"]):
        track = {**TRACK, "title": f"Track {n}"}
        if credit_line(track).endswith("mixed"):   # the complete line: padded to the real line's length
            track["source_url"] += "0" * (length - len(credit_line(track)))
        music.append(track)
    if sets["production_longest"]["share_alike"]:
        images[0] = {**images[0], "license": "CC BY-SA 4.0"}
    return {"images": images, "music": music}


def make_run(folder, credit_set, payload):
    from src import music as music_module
    from src.audio_provenance import record_bed
    from src.credits import add_provenance_attributions, sha256_file
    folder.mkdir(parents=True, exist_ok=True)
    master = folder / "master.mp4"
    master.write_bytes(b"not a video")
    assets = []
    for i, record in enumerate(credit_set["images"]):
        photo = folder / f"photo_{i}.jpg"
        photo.write_bytes(bytes([i]) * 16)
        assets.append({**record, "path": photo.name, "sha256": sha256_file(photo), "rights_status": "verified"})
    ledger = {"status": "recorded", "issues": [], "master_video": {"path": master.name, "sha256": sha256_file(master)},
              "assets_count": len(assets), "assets": assets}
    payload = dict(payload)
    if credit_set.get("music"):
        tracks = []
        for i, track in enumerate(credit_set["music"]):
            source = folder / f"track_{i}.mp3"
            source.write_bytes(bytes([64 + i]) * 16)
            tracks.append({**track, "reviewed": True, "file": source.name, "path": str(source)})
        bed = folder / "ambient_bed.wav"
        bed.write_bytes(b"bed")
        credit = " \n".join(c for c in (music_module.credit_line(t) for t in tracks) if c)
        record_bed(bed, {"source": "library", "tracks": tracks, "credit": credit})
        ledger["ambient_bed"] = {"path": str(bed), "sha256": sha256_file(bed)}
        if credit:   # as main does once the bed is made
            payload["caption"] = payload["caption"] + "\n" + credit
            if payload.get("description"):
                payload["description"] += "\n\n" + credit
    (folder / "provenance.json").write_text(json.dumps(ledger), encoding="utf-8")
    add_provenance_attributions(payload, folder / "provenance.json")
    return {"run_id": folder.name, "topic_id": "t1", "master_video_path": str(master), "script_payload": payload}


def send(db, run, youtube):
    """(sent, why not, {platform text name: the string}): the gate's credit checks, then the request as built."""
    from src.publisher import (
        MetaPublisher,
        reel_fields,
        youtube_fields,
        youtube_short_fields,
    )
    from src.release import write_release_report
    failed = [c["name"] for c in write_release_report(run)["checks"]
              if c["name"] in CREDIT_CHECKS and c["status"] == "fail"]
    if failed:
        return False, "held by the release gate: " + ", ".join(failed), {}
    master = Path(run["master_video_path"])
    try:
        meta = MetaPublisher(db=db, publishers=[]).build_envelope(
            run["run_id"], master, master, master, run["script_payload"],
            platforms=["youtube"] if youtube else None)["metadata"]
    except ValueError as exc:
        return False, "not built: " + str(exc)[:60], {}
    if youtube:
        return True, "", {"YouTube description": youtube_fields(meta)["description"]}
    return True, "", {"Instagram caption": reel_fields(meta)["caption"], "Facebook Reel text": reel_fields(meta)["caption"],
                      "YouTube Short description": youtube_short_fields(meta)["description"]}


def carousel(credit_set, caption):
    """(sent, {text name: string}): PipelineCoordinator._carousel_request with the set's photo credit lines."""
    from main import PipelineCoordinator
    from src import multi_format
    from src.credits import asset_attribution
    lines = [asset_attribution(r) for r in credit_set["images"]] + [multi_format.CAROUSEL_ADAPTED]
    coordinator = object.__new__(PipelineCoordinator)
    coordinator.db = SimpleNamespace(get_run=lambda run_id: {"topic_id": "t1"})
    real = multi_format.carousel_credits
    multi_format.carousel_credits = lambda slides: (lines, True)
    try:
        text, comment = coordinator._carousel_request("r1", {"carousel_caption": caption}, ["s1.jpg"])
    except ValueError:
        return False, {}, 0
    finally:
        multi_format.carousel_credits = real
    return True, {"carousel caption": text}, len([ln for ln in comment.splitlines() if ln.strip()])


def old_carousel(folder, credit_set, caption):
    """(sent, texts, 0) for a carousel drawn before slide records were kept (its slides have no record), whose
    run's ledger holds the set's photos and whose Reel caption carries the summary credit line."""
    from main import PipelineCoordinator
    (folder / "carousel").mkdir(parents=True)
    slides = []
    for n in (1, 2):
        (folder / "carousel" / f"carousel_slide_{n}.jpg").write_bytes(b"slide")
        slides.append(str(folder / "carousel" / f"carousel_slide_{n}.jpg"))
    (folder / "provenance.json").write_text(json.dumps({"assets": credit_set["images"]}), encoding="utf-8")
    coordinator = object.__new__(PipelineCoordinator)
    coordinator.db = SimpleNamespace(get_run=lambda run_id: {"topic_id": "t1"})
    summary = ("📷 Images via Wikimedia Commons and partner archives: Ann Lee (CC BY 4.0) · adapted: cropped, with "
               "motion and text added")
    try:
        text, comment = coordinator._carousel_request(
            "r1", {"carousel_caption": caption, "caption": "Reel text." + chr(10) * 2 + summary}, slides)
    except ValueError:
        return False, {}, 0
    return True, {"carousel caption": text}, len([ln for ln in comment.splitlines() if ln.strip()])


def posts(root, tmp, db):
    sets = json.loads((root / "tests" / "fixtures" / "qf126" / "real_credit_sets.json").read_text(encoding="utf-8"))
    named = {"dev_longest": sets["dev_longest"], "dev_longest_with_music": sets["dev_longest_with_music"],
             "production_longest": production_longest(sets)}
    reel = {"caption": BODY, "description": "", "delivery_profile": "reel", "title": "The stair",
            "hashtags": ["#History", "#Archaeology", "#AncientWorld"], "topic_id": "t1"}
    documentary = {"caption": BODY, "description": "The full story of the stair.\n" + "A paragraph of it. " * 60,
                   "delivery_profile": "deep_dive", "title": "The stair", "youtube_title": "The stair", "topic_id": "t1"}
    rows = []

    def row(case, name, credit_set, sent, why, texts, in_comment=0, photos_only=False):
        wanted = asked(credit_set["images"], [] if photos_only else credit_set.get("music") or [])
        per = {label: carried(text, wanted, bool(credit_set["images"])) for label, text in texts.items()}
        rows.append({"case": case, "set": name, "credits_asked": len(wanted) + int(bool(credit_set["images"])),
                     "sent": sent, "why_not": why,
                     "carried": {label: have for label, (have, _) in per.items()},
                     "credits_in_a_first_comment": in_comment,
                     "complete": sent and all(have == want for have, want in per.values())})

    for name, credit_set in named.items():
        credit_set = {"images": list(credit_set["images"]), "music": list(credit_set.get("music") or [])}
        row("reel, whole set", name, credit_set, *send(db, make_run(tmp / f"{name}-reel", credit_set, reel), False))
        for keep in (5, 3):
            part = {"images": credit_set["images"][:keep], "music": credit_set["music"]}
            row(f"reel, first {keep} photos", name, part,
                *send(db, make_run(tmp / f"{name}-reel{keep}", part, reel), False))
        row("documentary", name, credit_set,
            *send(db, make_run(tmp / f"{name}-doc", credit_set, documentary), True))
        long = dict(documentary, description="The full story of the stair.\n" + "A paragraph of it. " * 250)
        row("documentary, 4,780-character description", name, credit_set,
            *send(db, make_run(tmp / f"{name}-doclong", credit_set, long), True))
        for case, caption in (("carousel", "Swipe through the rooms."),
                              ("carousel, 1,900-character caption", "Swipe through the rooms. " + "x" * 1900)):
            for keep in (len(credit_set["images"]), 5):
                part = {"images": credit_set["images"][:keep]}
                sent, texts, in_comment = carousel(part, caption)
                row(f"{case}, {'whole set' if keep == len(credit_set['images']) else 'first 5 photos'}", name, part,
                    sent, "" if sent else "not posted", texts, in_comment, photos_only=True)
        for keep in (len(credit_set["images"]), 5):   # round 2: a carousel whose slides have no record
            part = {"images": credit_set["images"][:keep]}
            sent, texts, in_comment = old_carousel(tmp / f"{name}-old{keep}", part, "Swipe through the rooms.")
            size = "whole set" if keep == len(credit_set["images"]) else "first 5 photos"
            row(f"carousel without slide records, {size}", name, part, sent, "" if sent else "held, not posted",
                texts, in_comment, photos_only=True)
    return rows


# ---------------------------------------------------------------- 2. the library's music credits

def music_lines(root):
    from src.music import credit_line
    library = json.loads((root / "data" / "music" / "library.json").read_text(encoding="utf-8"))
    out = {"tracks": len(library), "ask_a_credit": 0, "complete_lines": 0, "lines_for_cc0_or_public_domain": 0,
           "longest_line": 0, "parts": {"title": 0, "author": 0, "licence_name": 0, "source_link": 0, "licence_link": 0,
                                        "edited": 0}}
    for track in library:
        line = credit_line(track)
        if str(track.get("license") or "").lower().startswith(NO_CREDIT):
            out["lines_for_cc0_or_public_domain"] += int(bool(line))
            continue
        out["ask_a_credit"] += 1
        has = {"title": str(track["title"]) in line, "author": str(track["creator"]) in line,
               "licence_name": f"CC BY {track.get('license_version', '')}".strip() in line,
               "source_link": str(track.get("source_url") or "\x00") in line,
               "licence_link": str(track.get("license_url") or "\x00") in line, "edited": "edited" in line}
        for part, ok in has.items():
            out["parts"][part] += int(ok)
        out["complete_lines"] += int(all(has.values()))
        out["longest_line"] = max(out["longest_line"], len(line))
    return out


# ---------------------------------------------------------------- 3. the small licence rules

def rules(root, tmp):
    from src import commons, museum_sources, music, telegram_images
    from src.credits import rights_evidence_ok, sha256_file
    from src.font_evidence import font_rights
    out = {}
    # R4: generated music into the library
    saved = music.MUSIC_DIR, music.LIBRARY
    music.MUSIC_DIR, music.LIBRARY = tmp / "music", tmp / "music" / "library.json"
    try:
        def registered(track):
            try:
                return bool(music.register_download(track, b"audio"))
            except ValueError:
                return False
        generated = [{**TRACK, "source": "elevenlabs", "creator": "ElevenLabs", "file": "score.mp3",
                      "source_url": "https://elevenlabs.io/music/1"},
                     {**TRACK, "file": "music_elevenlabs.mp3", "source_url": "https://freesound.org/s/2"}]
        out["generated_tracks_accepted_into_the_library"] = sum(registered(t) for t in generated)
        out["generated_tracks_in_rotation"] = sum(music.in_rotation({**t, "reviewed": True}, retired={}) for t in generated)
        out["licensed_track_accepted"] = registered({**TRACK, "file": "strings.mp3"})
        bbc = {**TRACK, "file": "bbc.mp3", "source": "BBC Sound Effects",
               "source_url": "https://sound-effects.bbcrewind.co.uk/search?q=1"}
        refused_music = not registered(bbc)
    finally:
        music.MUSIC_DIR, music.LIBRARY = saved
    committed = json.loads((root / "data" / "music" / "library.json").read_text(encoding="utf-8"))
    out["committed_library_tracks_naming_elevenlabs"] = sum("elevenlabs" in json.dumps(t).lower() for t in committed)

    # R5: fonts
    from fontTools import subset
    from fontTools.ttLib import TTFont
    fonts = root / "fonts"
    evidence = json.loads((fonts / "license_evidence.json").read_text(encoding="utf-8"))["fonts"]
    out["bundled_faces"] = len(evidence)
    out["bundled_faces_passing_font_rights"] = sum(bool(font_rights(fonts / r["file"])[0]) for r in evidence)

    def derived(name, source, rename="", drop=()):
        folder = tmp / name / "fonts"
        (folder / "licenses").mkdir(parents=True)
        record = dict(next(r for r in evidence if r["file"] == source))
        options = subset.Options()
        options.name_IDs, options.name_languages = ["*"], ["*"]
        font = TTFont(str(fonts / source))
        subsetter = subset.Subsetter(options)
        subsetter.populate(unicodes=range(0x20, 0x7F))
        subsetter.subset(font)
        for entry in list(font["name"].names):
            if entry.nameID in drop:
                font["name"].removeNames(nameID=entry.nameID)
            elif rename and entry.nameID in (1, 3, 4, 6, 16, 18, 21):
                text = entry.toUnicode().replace("IBM Plex Mono", rename).replace("IBMPlexMono", rename.replace(" ", ""))
                font["name"].setName(text, entry.nameID, entry.platformID, entry.platEncID, entry.langID)
        face = folder / "Derived-Face.ttf"
        font.save(str(face))
        licence = folder / "licenses" / "Derived-Face.ttf.OFL.txt"
        shutil.copyfile(fonts / record["license_file"], licence)
        record.update(file=face.name, sha256=sha256_file(face), license_file="licenses/" + licence.name,
                      license_sha256=sha256_file(licence), release="subset with fontTools to U+0020-007E")
        (folder / "license_evidence.json").write_text(json.dumps({"fonts": [record]}), encoding="utf-8")
        return bool(font_rights(face)[0])
    out["fonts"] = {
        "plex_subset_keeping_the_name_plex_passes": derived("kept", "IBMPlexMono-Medium.ttf"),
        "plex_subset_renamed_passes": derived("renamed", "IBMPlexMono-Medium.ttf", rename="RR Mono"),
        "renamed_subset_without_its_copyright_notice_passes": derived("nocopy", "IBMPlexMono-Medium.ttf", "RR Mono", (0,)),
        "renamed_subset_without_its_ofl_notice_passes": derived("noofl", "IBMPlexMono-Medium.ttf", "RR Mono", (13,)),
        "noto_subset_keeping_its_name_passes": derived("noto", "NotoSans-LGC.ttf")}

    # R6: the excluded sources at each ingest point (refused / asked)
    def page(credit, licence="CC BY 4.0"):
        return {"title": "File:Map.jpg", "categories": [], "imageinfo": [{
            "mime": "image/jpeg", "width": 4000, "height": 3000, "url": "u", "size": 1000,
            "descriptionurl": "https://commons.wikimedia.org/wiki/File:Map.jpg",
            "extmetadata": {"Artist": {"value": "A. Cartographer"}, "Credit": {"value": credit},
                            "LicenseShortName": {"value": licence}}}]}

    def commons_refused(credit, licence="CC BY 4.0"):
        return not list(commons.candidates([page(credit, licence)], [], allow_sa=True))
    excluded = {"David Rumsey": "https://www.davidrumsey.com/luna/servlet/detail/1",
                "BBC RemArc": "https://sound-effects.bbcrewind.co.uk/search?q=1",
                "ESA, not CC BY-SA 3.0 IGO": "https://www.esa.int/ESA_Multimedia/Images/x"}
    record = {"license": "CC BY 4.0", "license_url": BY4, "title": "Map", "artist": "Cartographer"}

    def museum_refused(url):
        saved_sources, saved_names = museum_sources.SOURCES, museum_sources._names_subject
        museum_sources.SOURCES = {"fake": lambda query: [{**record, "source_url": url, "download_url": "https://x.example/a.jpg"}]}
        museum_sources._names_subject = lambda cand, kws: True
        try:
            return not museum_sources.search_all("map", ["map"], sources=("fake",))
        finally:
            museum_sources.SOURCES, museum_sources._names_subject = saved_sources, saved_names

    def upload_refused(url, licence="CC BY 4.0"):
        saved_topic = telegram_images.topic_for
        telegram_images.topic_for = lambda db, ref: (_ for _ in ()).throw(LookupError("past the source check"))
        try:
            telegram_images.add_image(None, tmp / "assets", "t1", {"source": url, "licence": licence, "author": "A"},
                                      b"image", "2")
        except ValueError:
            return True
        except LookupError:
            return False
        finally:
            telegram_images.topic_for = saved_topic
        return False
    points = {
        "commons_search": [commons_refused(f'<a href="{url}">source</a>') for url in excluded.values()],
        "museum_search": [museum_refused(url) for url in excluded.values()],
        "rights_decision": [not rights_evidence_ok({**record, "source_url": url}) for url in excluded.values()],
        "operator_upload": [upload_refused(url) for url in excluded.values()]}
    out["esa_rule"] = esa_rule()
    out["author"] = authors()
    out["excluded_sources"] = {"sources": list(excluded), "refused_of_3": {k: sum(v) for k, v in points.items()},
                               "bbc_track_refused_by_the_music_library": refused_music,
                               "esa_under_cc_by_sa_3_0_igo_refused_at_upload":
                                   upload_refused(excluded["ESA, not CC BY-SA 3.0 IGO"], "CC BY-SA 3.0 IGO"),
                               "commons_own_work_refused": commons_refused("Own work")}
    return out


def esa_rule():
    """Round 2: records the source rule must let through and records it must refuse (the review's probes)."""
    try:
        from src.source_policy import blocked_source
    except ImportError:   # before QF-126: no rule
        def blocked_source(record):
            return ""
    base = {"license": "CC BY 4.0", "license_url": BY4, "source_url": "https://commons.wikimedia.org/wiki/File:N.jpg"}
    igo = {"license": "CC BY-SA 3.0 IGO", "license_url": "https://creativecommons.org/licenses/by-sa/3.0/igo/"}
    sa4 = {"license": "CC BY-SA 4.0", "license_url": "https://creativecommons.org/licenses/by-sa/4.0/"}
    lawful = [
        {"artist": "ESA/Hubble", "source_url": "https://esahubble.org/images/heic0601a/"},
        {"artist": "ESA/Hubble & NASA"},
        {"artist": "ESA/Webb, NASA & CSA, J. Lee", "source_url": "https://esawebb.org/images/weic2216b/"},
        {"artist": "ESA", "source_url": "https://www.spacetelescope.org/images/heic0601a/"},
        {"artist": "Esa Virtanen"}, {"artist": "Photo by Esa Virtanen"}, {"artist": "Mesa Verde Museum"},
        {"artist": "ESA/Rosetta/NavCam", **igo}, {"artist": "ESA", **igo, "license": "CC BY-SA IGO 3.0"},
        {"artist": "ESA", **igo, "license": "CC-BY-SA-3.0-IGO"}, {"artist": "ESA", **igo, "license": "CC BY-SA 3.0"},
        {"artist": "NASA, ESA and the Hubble Heritage Team", "license": "Public domain"},
        {"artist": "NASA/ESA/CSA/STScI", "license": "Public domain"}, {"artist": "A. Author"}]
    unlawful = [
        {"artist": "ESA"}, {"artist": "Credit: ESA/ATG medialab"}, {"artist": "DLR / ESA", **sa4},
        {"artist": "A. Author", "credit": "Image: ESA"}, {"artist": "ESA/Hubble", **sa4},
        {"source_url": "https://www.esa.int/ESA_Multimedia/Images/2020/01/x", "artist": "A. Author"},
        {"artist": "European Space Agency"}]
    return {"lawful_records": len(lawful),
            "lawful_records_refused": sum(bool(blocked_source({**base, **r})) for r in lawful),
            "esa_records_without_a_usable_licence": len(unlawful),
            "esa_records_without_a_usable_licence_refused": sum(bool(blocked_source({**base, **r})) for r in unlawful)}


def authors():
    """Round 2: the author a credit prints for an artist field that names one with an abbreviation in its place."""
    from src.credits import author_name
    named = ["Andrey Korchagin from St. Petersburg, Russia", "John Smith from St. Louis, USA", "Dr. Jane Doe",
             "Jorge Láscar from Melbourne, Australia"]
    return {"artist_fields_naming_an_author": len(named),
            "printed_unknown": sum(author_name({"artist": a}) == "Unknown" for a in named)}


def stored_copy(tmp):
    """Round 2: a copy written before the rule (the short music line, no adapted notice) whose credits fit: what the
    gate tells the operator, and what sending its review again does."""
    from main import PipelineCoordinator
    from src.credits import asset_attribution
    from src.release import write_release_report
    run = make_run(tmp / "stored", {"images": [PHOTO], "music": [TRACK]},
                   {"caption": BODY, "description": "", "delivery_profile": "reel", "title": "The stair"})
    short = "🎵 Music: “Slow Strings” by A. Composer (CC BY 4.0)"
    run["script_payload"].update(caption=BODY + chr(10) + short + chr(10) * 2 + asset_attribution(PHOTO),
                                 description=asset_attribution(PHOTO))
    run["status"] = "pending_approval"

    def reason(r):
        check = next((c for c in write_release_report(r)["checks"] if c["name"] == "licence_credits"), {})
        detail = check.get("detail")
        return check.get("status"), " ".join(str(d) for d in detail) if isinstance(detail, list) else ""
    status, why = reason(run)
    out = {"held_by_licence_credits": status == "fail",
           "told_to_shorten_or_regenerate": "Shorten" in why or "Regenerate" in why, "told_they_fit": "They fit" in why}
    coordinator = object.__new__(PipelineCoordinator)
    coordinator.db = SimpleNamespace(update_run=lambda *a, **k: None)
    again = coordinator._recredited(run) if hasattr(coordinator, "_recredited") else run
    out["held_after_its_review_is_sent_again"] = reason(again)[0] == "fail"
    out["credits_whole_in_the_caption_after_its_review_is_sent_again"] = carried(
        again["script_payload"]["caption"], asked([PHOTO], [TRACK]), True)[0]
    return out


# ---------------------------------------------------------------- real runs (optional, read only, counts only)

def real(folder):
    from src import credits, publishable
    from src.publisher import reel_fields, youtube_fields, youtube_short_fields
    out = {"run_folders": len(list(Path(folder).glob("run_*"))), "runs": 0, "runs_asking_a_credit": 0, "credits_asked": 0, "longest_credit_set_characters": 0,
           "ledger_assets": 0, "ledger_assets_from_an_excluded_source": 0,
           "photo_credits": 0, "photo_credits_printing_unknown_from_a_filled_artist_field": 0,
           "stored_copy_failing_the_new_check": 0, "of_them_told_to_shorten_though_their_credits_fit": 0,
           "of_them_told_their_credits_fit": 0, "second_crediting_pass_changes_the_copy": 0,
           "stored_copy": {"runs_with_every_credit_in_each_text": 0, "credits_carried_in_the_reel_caption_or_description": 0},
           "credited_again": {"sent": 0, "sent_with_every_credit_in_each_text": 0, "sent_lacking_a_credit": 0,
                              "held_with_the_reason": 0, "credits_lacking_in_texts_sent": 0},
           "carousels": {"carousels": 0, "with_a_slide_without_a_record": 0,
                         "of_them_in_a_run_whose_ledger_asks_photo_credits": 0,
                         "of_them_sent_without_every_photo_credit_line": 0,
                         "of_them_sent_with_every_photo_credit_of_the_run": 0, "of_them_held": 0,
                         "held_that_a_redraw_can_clear": 0, "longest_caption_sent": 0,
                         "held_because": {},
                         "with_every_slide_recorded": {
                             "carousels": 0, "slide_photos": 0, "photos_whose_credit_entry_cannot_be_read": 0,
                             "of_them_found_in_the_runs_ledger": 0, "of_them_not_in_the_runs_ledger": 0,
                             "sent_with_every_credit_its_photos_ask": 0,
                             "sent_lacking_a_credit_its_photos_ask": 0, "held": 0,
                             "held_though_every_credit_is_known_complete_and_fits": 0}},
           # run folders the loop below cannot read a ledger for (round 3: they were skipped, so a carousel sent
           # from one was not seen)
           "run_folders_without_a_ledger": {
               "folders": 0, "with_a_carousel": 0, "carousels_that_cannot_say_what_every_slide_shows": 0,
               "of_them_whose_copy_carries_a_credit_line": 0, "of_them_sent": 0, "of_them_held": 0,
               "carousels_whose_every_photo_credit_is_read": 0, "of_those_sent_with_every_credit": 0,
               "of_those_held": 0}}
    for run in sorted(Path(folder).glob("run_*")):
        try:
            data = json.loads((run / "provenance.json").read_text(encoding="utf-8"))
            assert isinstance(data, dict)
        except (OSError, ValueError, AssertionError):
            unledgered_carousel(run, out["run_folders_without_a_ledger"])
            continue
        try:
            payload = json.loads((run / "script_metadata.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        tracks = []
        bed = (data.get("ambient_bed") or {}).get("path")
        if bed:
            try:
                name = str(bed).replace("\\", "/").rsplit("/", 1)[-1] + ".rights.json"
                tracks = [s for s in json.loads((run / name).read_text(encoding="utf-8")).get("sources") or []
                          if isinstance(s, dict) and s.get("title")]
            except (OSError, ValueError):
                tracks = []
        images = list(data.get("assets") or []) + credits._reference_records(data)
        out["ledger_assets"] += len(data.get("assets") or [])
        try:
            from src.source_policy import blocked_source
            out["ledger_assets_from_an_excluded_source"] += sum(bool(blocked_source(a)) for a in data.get("assets") or [])
        except ImportError:   # before QF-126: no list
            pass
        wanted = asked(images, tracks)
        adapted = any(mark == "📷" for mark, _ in wanted)
        out["runs"] += 1
        if not wanted:
            continue
        youtube = publishable.is_documentary(payload)

        def texts(p, youtube=youtube):
            meta = publishable.envelope_metadata({**p, "title": p.get("title") or ""}, None)
            if youtube:
                return {"YouTube description": youtube_fields(meta)["description"]}
            return {"Reel caption": reel_fields(meta)["caption"], "Short description": youtube_short_fields(meta)["description"]}
        out["runs_asking_a_credit"] += 1
        out["credits_asked"] += len(wanted) + int(adapted)
        printed = {}
        for record in images:
            line = credits.asset_attribution(record)
            if line:
                printed[line] = record
        out["photo_credits"] += len(printed)
        out["photo_credits_printing_unknown_from_a_filled_artist_field"] += sum(
            line.startswith("📷 Unknown |") and str(r.get("artist") or "").strip().lower().rstrip(".") not in
            ("", "unknown", "unknown artist", "unknown artistunknown artist", "anonymous") for line, r in printed.items())
        stored = [carried(t, wanted, adapted) for t in texts(payload).values()]
        out["stored_copy"]["runs_with_every_credit_in_each_text"] += int(all(h == w for h, w in stored))
        out["stored_copy"]["credits_carried_in_the_reel_caption_or_description"] += min(h for h, _ in stored)
        again = copy.deepcopy(payload)
        credits.add_provenance_attributions(again, run / "provenance.json")
        new = texts(again)
        if hasattr(credits, "ledger_credit_problems"):
            limits = publishable.credit_texts(publishable.envelope_metadata({**again, "title": again.get("title") or ""}, None),
                                              youtube)
            held = bool(credits.ledger_credit_problems(run / "provenance.json", *limits))
            out["longest_credit_set_characters"] = max(out["longest_credit_set_characters"], sum(
                len(c["line"]) + 2 for c in credits.required_credits(data, run)))
        else:   # the base's gate: a photo credit found in the caption or the description passes
            held = any(not credits.credit_present(a, again.get("caption", ""))
                       and not credits.credit_present(a, again.get("description", "")) for a in data.get("assets") or [])
        twice = copy.deepcopy(again)
        credits.add_provenance_attributions(twice, run / "provenance.json")
        out["second_crediting_pass_changes_the_copy"] += int(twice != again)
        if hasattr(credits, "ledger_credit_problems"):
            # the stored copy as the release gate reads it (publishable.payload_credit_texts)
            said = " ".join(credits.ledger_credit_problems(
                run / "provenance.json", *publishable.payload_credit_texts(payload, youtube)))
            out["stored_copy_failing_the_new_check"] += int(bool(said))
            out["of_them_told_to_shorten_though_their_credits_fit"] += int("Shorten" in said and not held)
            out["of_them_told_their_credits_fit"] += int("They fit" in said)
        real_carousel(run, again, [w for w in wanted if w[0] == "📷"], out["carousels"], data)
        after = [carried(t, wanted, adapted) for t in new.values()]
        section = out["credited_again"]
        if held:
            section["held_with_the_reason"] += 1
            continue
        section["sent"] += 1
        complete = all(h == w for h, w in after)
        section["sent_with_every_credit_in_each_text" if complete else "sent_lacking_a_credit"] += 1
        section["credits_lacking_in_texts_sent"] += max(w - h for h, w in after)
    return out


def shown_photos(shown, data):
    """What a carousel's slides show and what that asks, worked out here and not by the code measured: (whether a
    slide has no record, photos shown, photos whose credits.json entry cannot be read, how many of those the run's
    ledger holds with a licence, the credit records the readable ones ask). A photo is looked for in the ledger by
    its recorded path only (an asset's own path, or a path it was made from)."""
    from src import credits, multi_format

    def key(path):
        return str(path or "").replace("\\", "/").lower()

    assets = [a for a in (data or {}).get("assets") or [] if isinstance(a, dict)]
    recordless = any(not multi_format.slide_record(slide) for slide in shown)
    photos = list(dict.fromkeys(photo for slide in shown for photo in multi_format.slide_photos(slide)))
    records, unread, found = [], 0, 0
    for photo in photos:
        entry = multi_format.photo_credit(photo)
        if not entry:
            unread += 1
            held = [a for a in assets
                    if key(photo) in {key(a.get("path")), *(key(x) for x in a.get("source_paths") or [])}]
            if not held or not all(a.get("license") for a in held):
                continue
            found += 1
            records += held + credits._reference_records({"assets": held})
        else:
            records.append(entry)
    return recordless, len(photos), unread, found, records


def carousel_request(run, payload, slides):
    """(caption, "") as PipelineCoordinator._carousel_request would send the run's carousel, or ("", why it is held)."""
    from main import PipelineCoordinator
    coordinator = object.__new__(PipelineCoordinator)
    coordinator.db = SimpleNamespace(get_run=lambda run_id: {"topic_id": payload.get("topic_id") or "t1"})
    try:
        return coordinator._carousel_request(run.name, payload, slides)[0], ""
    except ValueError as e:
        return "", str(e) or "held"


def hold_kind(why):
    for mark, kind in (("provenance record is missing", "the_ledger_is_missing"),
                       ("provenance record could not be read", "the_ledger_cannot_be_read"),
                       ("does not hold th", "a_photo_is_in_neither_its_credit_file_nor_the_ledger"),
                       ("lacks", "a_credit_is_incomplete"),
                       ("over the 2,200", "the_runs_credits_do_not_fit_the_caption"),
                       ("do not fit", "the_slides_credits_do_not_fit_the_caption")):
        if mark in why:
            return kind
    return "its_caption_is_not_the_one_reviewed_or_another_reason"


def real_carousel(run, payload, photos, out, data=None):
    """Counts for the run's carousel as PipelineCoordinator._carousel_request would send it (the copy credited
    again): a carousel with a slide that has no record must carry every photo credit of its run, or be held; one
    whose slides all have records must carry the credit of every photo they show, also of a photo whose credits.json
    entry cannot be read (the run's ledger then says what it asks), or be held; and it must be held when a photo it
    shows is in neither (the run's other credits are no credit for that photo)."""
    slides = sorted(str(p) for p in (run / "carousel").glob("carousel_slide_*.jpg"))
    if not slides:
        return
    out["carousels"] += 1
    shown = [Path(s) for s in slides] + sorted((run / "carousel_fb").glob("carousel_slide_*.jpg"))   # both copies
    recordless, shown_count, unread, found, records = shown_photos(shown, data)
    if not recordless:
        section = out["with_every_slide_recorded"]
        section["carousels"] += 1
        section["slide_photos"] += shown_count
        section["photos_whose_credit_entry_cannot_be_read"] += unread
        section["of_them_found_in_the_runs_ledger"] += found
        section["of_them_not_in_the_runs_ledger"] += unread - found
        wanted = asked(records, [])
        caption, why = carousel_request(run, payload, slides)
        if why:
            section["held"] += 1
            out["held_because"][hold_kind(why)] = out["held_because"].get(hold_kind(why), 0) + 1
            from src import credits
            lines = [credits.asset_attribution(r) for r in records]
            base = (payload.get("carousel_caption") or "").strip() or payload.get("caption", "")
            incomplete = getattr(credits, "incomplete_credit", lambda item: "")
            complete = unread == found and not any(
                incomplete({"kind": "image", "line": ln, "record": r}) for ln, r in zip(lines, records) if ln)
            section["held_though_every_credit_is_known_complete_and_fits"] += int(
                complete and len(base) + 200 + sum(len(ln) + 1 for ln in set(lines)) <= 2200
                and hold_kind(why).startswith(("the_", "a_")))
            return
        have, want = carried(caption, wanted, bool(wanted))
        # sent with a photo neither its entry nor the ledger speaks for: its credit, if one is asked, is lacking
        section["sent_with_every_credit_its_photos_ask" if have == want and unread == found
                else "sent_lacking_a_credit_its_photos_ask"] += 1
        out["longest_caption_sent"] = max(out["longest_caption_sent"], len(caption))
        return
    out["with_a_slide_without_a_record"] += 1
    if not photos:
        return
    out["of_them_in_a_run_whose_ledger_asks_photo_credits"] += 1
    caption, why = carousel_request(run, payload, slides)
    if why:
        out["of_them_held"] += 1
        out["held_because"][hold_kind(why)] = out["held_because"].get(hold_kind(why), 0) + 1
        try:
            from src import carousel_lazy
            out["held_that_a_redraw_can_clear"] += int(carousel_lazy.plan_problem(run) is None)
        except (ImportError, AttributeError):
            pass
        return
    have, want = carried(caption, photos, True)
    out["of_them_sent_with_every_photo_credit_of_the_run" if have == want
        else "of_them_sent_without_every_photo_credit_line"] += 1
    out["longest_caption_sent"] = max(out["longest_caption_sent"], len(caption))


def unledgered_carousel(run, out):
    """Counts for a run folder whose provenance ledger is missing or cannot be read. Nothing there says what the
    run's photos ask, so its carousel may be sent only when every slide has a record and every photo's own credit
    entry is read; any other carousel must be held, whatever credit lines its copy carries."""
    out["folders"] += 1
    slides = sorted(str(p) for p in (run / "carousel").glob("carousel_slide_*.jpg"))
    if not slides:
        return
    out["with_a_carousel"] += 1
    try:
        payload = json.loads((run / "script_metadata.json").read_text(encoding="utf-8"))
        assert isinstance(payload, dict)
    except (OSError, ValueError, AssertionError):
        payload = {}
    shown = [Path(s) for s in slides] + sorted((run / "carousel_fb").glob("carousel_slide_*.jpg"))
    recordless, _, unread, _, records = shown_photos(shown, None)
    caption, why = carousel_request(run, payload, slides)
    if recordless or unread:
        out["carousels_that_cannot_say_what_every_slide_shows"] += 1
        out["of_them_whose_copy_carries_a_credit_line"] += int(any(
            line.startswith("📷") for field in ("caption", "description", "carousel_caption")
            for line in str(payload.get(field) or "").splitlines()))
        out["of_them_held" if why else "of_them_sent"] += 1
        return
    out["carousels_whose_every_photo_credit_is_read"] += 1
    wanted = asked(records, [])
    if why:
        out["of_those_held"] += 1
    else:
        have, want = carried(caption, wanted, bool(wanted))
        out["of_those_sent_with_every_credit"] += int(have == want)


def carousel_faults(tmp):
    """Round 3: the carousel's fault paths, each built in `tmp` and sent through PipelineCoordinator._carousel_request
    (the last one through _post_photos, to read its notice)."""
    import hashlib

    import main as app
    from src.credits import asset_attribution
    door = {**PHOTO, "artist": "Jan Novak", "title": "File:Door.jpg",
            "source_url": "https://commons.wikimedia.org/wiki/File:Door.jpg"}
    coordinator = object.__new__(app.PipelineCoordinator)
    coordinator.db = SimpleNamespace(get_run=lambda run_id: {"topic_id": "t1"})

    def request(payload, slides):
        try:
            return coordinator._carousel_request("r1", payload, slides)[0]
        except ValueError:
            return None

    def deck(name, photos=None, credits=None, ledger=None):
        """A run folder with two slides: with records of the photos they show when `photos` is given."""
        run = tmp / "faults" / name
        (run / "carousel").mkdir(parents=True)
        (run / "photos").mkdir()
        slides, records = [], {}
        for n, photo in enumerate(photos or [None, None], 1):
            slide = run / "carousel" / f"carousel_slide_{n:02d}.jpg"
            slide.write_bytes(b"slide")
            slides.append(str(slide))
            if photo:
                (run / "photos" / photo).write_bytes(photo.encode())
                records[slide.name] = {"recipe": {"photo": str(run / "photos" / photo)}}
        if records:
            (run / "carousel" / "still_layout.json").write_text(json.dumps(records), encoding="utf-8")
        if credits is not None:
            (run / "photos" / "credits.json").write_text(credits, encoding="utf-8")
        if ledger is not None:
            assets = [{**record, "path": str((run / "photos" / photo).resolve()), "file": photo,
                       "sha256": hashlib.sha256(photo.encode()).hexdigest()} for photo, record in ledger.items()]
            (run / "provenance.json").write_text(json.dumps({"assets": assets}), encoding="utf-8")
        return slides

    out = {}
    # 1. no slide records and no ledger file: nothing says what the slides show or what the run's photos ask
    summary = ("📷 Images via Wikimedia Commons and partner archives: Ann Lee, Jan Novak (CC BY 4.0) · adapted: "
               "cropped, with motion and text added")
    copies = {"its_copy_has_the_summary_line_only": {"carousel_caption": "Swipe.", "caption": "Reel text.\n\n" + summary},
              "its_copy_kept_one_of_two_photo_lines": {"carousel_caption": "Swipe.",
                                                       "caption": "Reel text.\n\n" + asset_attribution(PHOTO)},
              "its_copy_has_no_credit_line": {"carousel_caption": "Swipe."}}
    sent = {name: request(payload, deck("bare_" + name)) is not None for name, payload in copies.items()}
    out["no_slide_records_and_no_ledger"] = {"cases": len(sent), "sent": sum(sent.values()),
                                             "held": len(sent) - sum(sent.values()), "sent_by_case": sent}
    # 2. slides with records; the photo's credits.json entry present, missing, corrupt; the run's ledger holds it
    entry = json.dumps({"images": [{**PHOTO, "file": "vault.jpg"}]})
    line = asset_attribution(PHOTO)
    got = {}
    for name, credits in (("present", entry), ("missing", None), ("corrupt", "{not json")):
        caption = request({"carousel_caption": "Swipe."}, deck("entry_" + name, ["vault.jpg"], credits, {"vault.jpg": PHOTO}))
        got[name] = "held" if caption is None else "sent with its credit" if line in caption.splitlines() else "sent without"
    out["recorded_slide_whose_credit_entry_is_present_missing_corrupt"] = {
        "cases": len(got), "sent_with_the_photos_credit": sum(v == "sent with its credit" for v in got.values()),
        "sent_without_it": sum(v == "sent without" for v in got.values()), "by_case": got}
    # 3. the entry cannot be read and the ledger does not hold the photo, or is missing: nothing says what it asks
    got = {"the_ledger_holds_another_photo": request({"carousel_caption": "Swipe."},
                                                     deck("absent", ["vault.jpg"], None, {"door.jpg": door})),
           "the_ledger_is_missing": request({"carousel_caption": "Swipe."}, deck("gone", ["vault.jpg"]))}
    out["recorded_slide_whose_photo_is_in_neither_record"] = {
        "cases": len(got), "sent": sum(v is not None for v in got.values()), "held": sum(v is None for v in got.values())}
    # 4. a photo the ledger says asks for no credit (the run's own illustration; public domain): sent as it is
    free = {"illustration": {"license": "AI-generated illustration, not a photograph", "attribution_required": False,
                             "origin": "archaeological_reconstruction", "illustration": {"origin": "ai_illustration"}},
            "public_domain": {**PHOTO, "license": "Public domain", "license_url": None, "attribution_required": False}}
    got = {name: request({"carousel_caption": "Swipe."}, deck("free_" + name, ["vault.jpg"], None, {"vault.jpg": record}))
           for name, record in free.items()}
    out["unread_entry_of_a_photo_that_asks_no_credit"] = {
        "cases": len(got), "sent_as_it_is": sum(v == "Swipe." for v in got.values()),
        "held": sum(v is None for v in got.values())}
    # 5. slides with records whose credits do not fit the caption: what the operator is told
    said = []
    notice, app._notice = app._notice, lambda owner, text, **kw: said.append((text, kw))
    posted = []
    coordinator.db = SimpleNamespace(get_run=lambda run_id: {"topic_id": "t1"}, run_label=lambda run_id: "The stair",
                                     run_key=lambda run_id: "k1", update_scheduled_post=lambda *a, **k: None)
    coordinator.publisher = SimpleNamespace(
        publish_photos=lambda *a, **k: posted.append(k.get("first_comment") or "") or {"status": "published"})
    coordinator._close_booking_item = lambda post: None
    try:
        slides = deck("fit", ["vault.jpg", "door.jpg"], json.dumps({"images": [
            {**PHOTO, "file": "vault.jpg"}, {**door, "file": "door.jpg"}]}))
        app.PipelineCoordinator._post_photos(
            coordinator, {"id": 7, "run_id": "r1", "kind": "carousel", "files": slides},
            {"script_payload": {"carousel_caption": "Swipe. " + "x" * 1950}, "topic_id": "t1"}, "carousel")
    finally:
        app._notice = notice
    text, kw = said[-1] if said else ("", {})
    buttons = json.dumps([kw.get("buttons"), kw.get("actions")], ensure_ascii=False)
    out["credits_that_do_not_fit_beside_slide_records"] = {
        "posted": bool(posted), "posted_with_credit_lines_in_a_first_comment": bool(posted and posted[0]),
        "held_as_a_carousel_that_will_not_post": "This carousel will not post" in text,
        "notice_offers_retry": "retrypost" in buttons, "notice_offers_redraw": "carredraw" in buttons,
        "notice_names_edit": "Edit" in text, "notice_says_nothing_was_posted_and_the_reel_is_not_affected":
            "Nothing was posted, and its Reel and other posts are not affected" in text}
    return out


def main():
    root = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(root))
    os.chdir(root)
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    tmp = Path(tempfile.mkdtemp(prefix="qf126-"))
    try:
        from src import ai_label, config
        from src.db import PipelineDatabase
        ai_label.assess_video = lambda *a, **k: {"on": False, "reasons": []}
        config.YOUTUBE_SHORTS = True
        bank = tmp / "topic_bank.json"
        bank.write_text(json.dumps({"topics": []}), encoding="utf-8")
        db = PipelineDatabase(db_path=tmp / "measure.db", topic_bank_file=bank)
        rows = posts(root, tmp, db)
        sent = [r for r in rows if r["sent"]]
        result = {
            "posts": {"cases": len(rows), "sent": len(sent),
                      "sent_with_every_credit_whole_in_every_text": sum(r["complete"] for r in sent),
                      "sent_lacking_a_credit": sum(not r["complete"] for r in sent),
                      "sent_with_credits_only_in_a_first_comment": sum(bool(r["credits_in_a_first_comment"]) for r in sent),
                      "held_with_the_reason": len(rows) - len(sent)},
            "music": music_lines(root),
            "rules": rules(root, tmp),
            "copy_written_before_the_rule_whose_credits_fit": stored_copy(tmp),
            "carousel_fault_paths": carousel_faults(tmp),
            "post_rows": rows}
        if os.environ.get("RR_REAL_OUTPUT"):
            result = {"real": real(os.environ["RR_REAL_OUTPUT"])}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
