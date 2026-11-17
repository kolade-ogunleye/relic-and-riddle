"""QF-111: no runtime code writes a file the release owns.

Each runtime writer of a tracked file (the inventory in docs/reviews/quality-fixes-2026-11/QF-111/README.md) is run
here against a stand-in checkout in a temporary folder: the shipped file must stay byte-identical, and the change must
be what the application then reads (this host's copy, src/host_state.py). The whole suite runs under the guard in
tests/tracked_writes.py (conftest.py), which fails any test whose code writes a release-owned path.
"""
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A stand-in app root: config.BASE_DIR is it, this host's copies go under its data/host/."""
    from src import config
    repo = tmp_path / "app"
    (repo / "data").mkdir(parents=True)
    monkeypatch.setattr(config, "BASE_DIR", repo)
    monkeypatch.setattr(config, "HOST_STATE_DIR", repo / "data" / "host", raising=False)
    return repo


def _seed(path: Path, value) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, indent=2).encode()
    path.write_bytes(data)
    return data


def test_a_music_approval_leaves_the_shipped_library_as_it_is(checkout, monkeypatch):
    from src import music
    library = checkout / "data" / "music" / "library.json"
    seed = _seed(library, [{"file": "mysterious/a.mp3", "title": "A", "mood": "mysterious", "reviewed": False,
                            "creator": "c", "duration_ms": 60000, "license": "cc0"}])
    monkeypatch.setattr(music, "LIBRARY", library)
    monkeypatch.setattr(music, "MUSIC_DIR", library.parent)
    music.approve_track(music.track_key({"file": "mysterious/a.mp3"}))
    assert library.read_bytes() == seed
    assert music.load_library(available_only=False)[0]["reviewed"] is True


def test_a_photo_review_leaves_the_shipped_credits_as_they_are(checkout):
    from src import credits
    topic = checkout / "assets" / "some_topic"
    seed = _seed(topic / "credits.json", {"reviewed": False, "images": [{"file": "01.jpg"}]})
    (topic / "01.jpg").write_bytes(b"jpeg")
    record = credits.load_credits(topic)
    record.update(reviewed=True, reviewed_by="operator")
    credits.save_credits(topic, record)
    assert (topic / "credits.json").read_bytes() == seed
    assert credits.load_credits(topic)["reviewed"] is True


def test_a_storyboard_leaves_the_shipped_one_as_it_is(checkout):
    from src import storyboard
    topic = checkout / "assets" / "some_topic"
    seed = _seed(topic / "storyboard.json", [{"beat": 1}])
    storyboard.save_storyboard(topic, [{"beat": 1}, {"beat": 2}])
    assert (topic / "storyboard.json").read_bytes() == seed
    assert storyboard.load_storyboard(topic) == [{"beat": 1}, {"beat": 2}]


def test_a_rebuilt_fact_sheet_leaves_the_shipped_one_as_it_is(checkout, monkeypatch):
    from src import fact_sheet
    sheets = checkout / "data" / "fact_sheets"
    monkeypatch.setattr(fact_sheet, "SHEETS_DIR", sheets)
    seed = _seed(sheets / "some_topic.json", {"topic_id": "some_topic", "facts": []})
    fact_sheet.save({"topic_id": "some_topic", "facts": [], "built_at": "2026-11-16T00:00:00+00:00"})
    assert (sheets / "some_topic.json").read_bytes() == seed
    used = fact_sheet.current_path("some_topic")
    assert used != sheets / "some_topic.json" and json.loads(used.read_text())["built_at"].startswith("2026-11-16")


def test_a_geocoding_lookup_leaves_the_shipped_cache_as_it_is(checkout, monkeypatch):
    from src import geo
    cache = checkout / "data" / "geo_cache.json"
    seed = _seed(cache, {"old|place": {"lat": 1.0, "lon": 2.0}})
    monkeypatch.setattr(geo, "CACHE_FILE", cache)
    monkeypatch.setattr(geo, "available", lambda: True)
    monkeypatch.setattr(geo, "find_country", lambda name, source=False: {"name": name})
    monkeypatch.setattr(geo, "_wikidata_point", lambda q, c, n: {"lat": 3.0, "lon": 4.0, "qid": "Q1", "country": n})
    found = geo.locate({"location": {"name": "New", "country": "Land"}})
    assert found["lat"] == 3.0
    assert cache.read_bytes() == seed
    assert set(geo._load_cache()) == {"old|place", "new|land"}   # the shipped lookups and this host's


def test_a_topic_added_in_telegram_leaves_the_shipped_bank_as_it_is(checkout, monkeypatch):
    from src import topic_store
    bank, queries = checkout / "data" / "topics" / "topic_bank.json", checkout / "data" / "topics" / "image_queries.json"
    seed_bank = _seed(bank, {"total": 1, "topics": [{"id": "shipped", "title": "Shipped"}]})
    seed_queries = _seed(queries, {"shipped": {"queries": ["s"]}})
    with topic_store.transaction(bank):
        new_bank = {"total": 2, "topics": [{"id": "shipped", "title": "Shipped"}, {"id": "mine", "title": "Mine"}]}
        topic_store.commit(bank, queries, new_bank, {"shipped": {"queries": ["s"]}, "mine": {"queries": ["m"]}})
    assert bank.read_bytes() == seed_bank and queries.read_bytes() == seed_queries
    assert [t["id"] for t in topic_store.read_bank(bank)["topics"]] == ["shipped", "mine"]
    assert set(topic_store.read_queries(queries)) == {"shipped", "mine"}
    # a release that later ships an edit to its own topic still reaches this host
    _seed(bank, {"total": 1, "topics": [{"id": "shipped", "title": "Shipped, edited"}]})
    assert [t["title"] for t in topic_store.read_bank(bank)["topics"]] == ["Shipped, edited", "Mine"]


def test_this_hosts_copies_and_runtime_side_files_are_ignored_by_git():
    paths = ["data/host/data/music/library.json", "data/host/assets/x/credits.json", "docs/issues/iss_new.md",
             "docs/issues/evidence/evidence_new.jpg", "docs/improvements/queue.md", "data/prompt_patches.json",
             "data/topics/topic_bank.transaction.json", "assets/x/callout_locate.json", "assets/x/01.jpg.quality.json",
             "assets/x/01.jpg.presentation.json", "assets/x/01.jpg.source", "assets/x/.orientation_backup/01.jpg"]
    out = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "--no-index", *paths], capture_output=True, text=True,
                         check=False)
    ignored = {line.strip() for line in out.stdout.splitlines()}
    assert ignored == set(paths), sorted(set(paths) - ignored)


def test_the_deployers_refusal_names_the_file_and_what_to_do():
    for script in ("deployment/windows/lib/ContentReleaseDeployer.ps1", "deployment/windows/Update-ContentReleaseTree.ps1"):
        text = (ROOT / script).read_text(encoding="utf-8")
        assert "resolve it manually" not in text, script
        assert "Production changed $file differently from the release, so the release was not installed." in text
        assert "copy $file there" in text and "git checkout -- $file" in text and "data\\host\\$file" in text


def test_the_guard_refuses_a_write_to_a_release_owned_path(tmp_path):
    from tests import tracked_writes
    owned = tmp_path / "app" / "shipped.json"
    owned.parent.mkdir()
    owned.write_text("{}", encoding="utf-8")
    guard = tracked_writes.Guard({tracked_writes._norm(owned)}, root=owned.parent)
    with pytest.raises(tracked_writes.TrackedWriteRefused):
        guard.hook("open", (str(owned), "w", 0))
    with pytest.raises(tracked_writes.TrackedWriteRefused):
        guard.hook("os.replace", (str(tmp_path / "x.tmp"), str(owned), -1, -1))
    guard.hook("open", (str(owned), "r", 0))   # reading is fine
    guard.hook("shutil.copyfile", (str(owned), str(tmp_path / "copy.json")))   # so is copying it elsewhere
    guard.hook("os.remove", ("shipped.json", 7))   # a name relative to an open directory (rmtree), not the checkout
    assert len(guard.take()) == 2


def test_the_guard_is_on_for_this_suite():
    """Every test of the suite runs under the guard when the suite runs in a git checkout (CI always does)."""
    from tests import tracked_writes
    guard = tracked_writes._guard
    assert guard is not None and guard.active
    assert tracked_writes._norm(ROOT / "src" / "config.py") in guard.tracked
    assert guard.owned(tracked_writes._norm(ROOT / "data" / "new_file.json"))       # a new, not-ignored path
    assert not guard.owned(tracked_writes._norm(ROOT / "data" / "host" / "x.json"))   # this host's own copies


# ----------------------------------------------------------------------------------------------- review 1, B1
def test_no_pool_reader_bypasses_this_hosts_copy():
    """A pool's record is looked for through src/host_state.py (or load_credits/pool_record), never by its shipped
    path alone: a pool fetched on this host exists only in data/host/ (QF-111 review 1, B1)."""
    import re
    pattern = re.compile(r"""(CREDITS_FILE|["']credits\.json["']|STORYBOARD_FILE|["']storyboard\.json["'])\)\s*\."""
                         r"""(exists|is_file|read_text|read_bytes|stat|open)\(""")
    found = []
    for path in [*sorted((ROOT / "src").glob("*.py")), ROOT / "main.py", *sorted((ROOT / "scripts").glob("*.py"))]:
        if path.name == "host_state.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                found.append(f"{path.relative_to(ROOT).as_posix()}:{number}")
    assert not found, found


def _photos(topic: Path, n: int) -> None:
    from PIL import Image
    topic.mkdir(parents=True, exist_ok=True)
    for i in range(1, n + 1):
        Image.new("RGB", (64, 48), (i * 20 % 255, 80, 120)).save(topic / f"{i:02d}_commons.jpg")
        (topic / f"{i:02d}_commons.jpg.quality.json").write_text("{}", encoding="utf-8")   # the fetcher's evidence


def test_a_pool_fetched_on_this_host_is_listed_reviewed_and_never_used_unreviewed(checkout, monkeypatch):
    from src import control, credits
    from src.visual_engine import MissingAssetsError, VisualEngine
    assets = checkout / "assets"
    topic = assets / "new_topic"
    _photos(topic, 12)
    images = [{"file": f"{i:02d}_commons.jpg", "vision_review": {"shows_topic": "yes", "usable": True, "safe": True}}
              for i in range(1, 13)]
    credits.save_credits(topic, {"reviewed": False, "images": images})   # only in data/host/
    assert not (topic / "credits.json").exists()
    assert control._fetched_sets(assets) == [topic]                      # /photos lists it
    assert control._resolve_topic_dir("new_topic", assets) == topic
    engine = VisualEngine(assets_dir=assets)
    with pytest.raises(MissingAssetsError, match="not reviewed"):         # never the folder's photos unreviewed
        engine.get_scene_sequence({"id": "new_topic"})
    assert engine.reserve_sequence({"id": "new_topic"}) == []
    record = credits.load_credits(topic)
    record["reviewed"] = True
    credits.save_credits(topic, record)
    used = engine.get_scene_sequence({"id": "new_topic"})
    assert used and all(p.parent == topic for p in used)


def test_fetched_photos_without_any_record_are_never_used(checkout):
    from src.visual_engine import MissingAssetsError, VisualEngine
    topic = checkout / "assets" / "lost_record"
    _photos(topic, 8)
    with pytest.raises(MissingAssetsError, match="photo record"):
        VisualEngine(assets_dir=checkout / "assets").get_scene_sequence({"id": "lost_record"})


# ----------------------------------------------------------------------------------------------- review 1, B2
def test_a_corrected_shipped_place_reaches_a_host_with_its_own_lookups(checkout, monkeypatch):
    from src import geo, host_state
    cache = checkout / "data" / "geo_cache.json"
    _seed(cache, {"x|land": {"lat": 1.0}})
    monkeypatch.setattr(geo, "CACHE_FILE", cache)
    monkeypatch.setattr(geo, "available", lambda: True)
    monkeypatch.setattr(geo, "find_country", lambda name, source=False: {"name": name})
    monkeypatch.setattr(geo, "_wikidata_point", lambda q, c, n: {"lat": 3.0, "lon": 4.0, "qid": "Q1", "country": n})
    geo.locate({"location": {"name": "New", "country": "Land"}})
    assert set(json.loads(host_state.host_copy(cache).read_text())) == {"new|land"}   # only its own lookups
    _seed(cache, {"x|land": {"lat": 99.0}})                                           # the release corrects X
    assert geo._load_cache()["x|land"] == {"lat": 99.0} and "new|land" in geo._load_cache()
    # a copy written whole by the first build of QF-111 is read under the shipped cache too
    host_state.host_copy(cache).write_text(json.dumps({"x|land": {"lat": 1.0}}), encoding="utf-8")
    assert geo._load_cache()["x|land"] == {"lat": 99.0}


def test_a_usable_shipped_fact_sheet_is_used_over_a_stale_copy_and_not_rebuilt(checkout, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from src import fact_sheet, host_state
    sheets = checkout / "data" / "fact_sheets"
    monkeypatch.setattr(fact_sheet, "SHEETS_DIR", sheets)
    now = datetime.now(UTC)
    shipped = sheets / "t.json"
    _seed(shipped, {"topic_id": "t", "facts": [], "rules_version": fact_sheet.RULES_VERSION,
                    "built_at": (now - timedelta(days=2)).isoformat()})
    copy = host_state.host_copy(shipped)
    _seed(copy, {"topic_id": "t", "facts": [], "rules_version": "older", "built_at": now.isoformat()})
    assert fact_sheet.current_path("t") == shipped                      # the copy is under older rules
    assert fact_sheet.load("t") is not None                             # so nothing is researched again
    _seed(copy, {"topic_id": "t", "facts": [], "rules_version": fact_sheet.RULES_VERSION,
                 "built_at": (now - timedelta(days=1)).isoformat()})
    assert fact_sheet.current_path("t") == copy                         # both usable: the newer
    _seed(shipped, {"topic_id": "t", "facts": [], "rules_version": fact_sheet.RULES_VERSION,
                    "built_at": now.isoformat()})
    assert fact_sheet.current_path("t") == shipped                      # a release's newer research arrives


SHIP = "https://commons.wikimedia.org/wiki/File:Shipwreck.jpg"
MODEL = "https://commons.wikimedia.org/wiki/File:Model_(7751351552).jpg"


def _review_copy(credits, topic, images):
    """This host's review copy of `images`, as save_credits makes it."""
    record = credits.load_credits(topic)
    record["images"] = images
    record["reviewed"] = True
    credits.save_credits(topic, record)


def test_a_shipped_credit_correction_reaches_a_host_with_its_own_review(checkout):
    from src import credits
    topic = checkout / "assets" / "t"
    _photos(topic, 1)
    _seed(topic / "credits.json", {"reviewed": False, "images": [{"file": "01_commons.jpg", "artist": "x@example.com",
                                                                    "license": "CC BY 4.0", "source_url": SHIP}]})
    record = credits.load_credits(topic)
    record["reviewed"] = True
    record["images"][0]["operator_keep"] = True
    credits.save_credits(topic, record)                                 # this host's review
    _seed(topic / "credits.json", {"reviewed": False, "images": [{"file": "01_commons.jpg", "artist": "A. Person",
                                                                    "license": "CC BY 4.0", "source_url": SHIP}]})
    now = credits.load_credits(topic)                                   # the release's fix, for the same photo
    assert now["reviewed"] is True and now["images"][0]["operator_keep"] is True     # the review stays this host's
    assert now["images"][0]["artist"] == "A. Person"                                  # the credit is the release's
    assert credits.pool_record(topic / "credits.json")["images"][0]["artist"] == "A. Person"


def test_a_same_name_photo_that_is_another_photo_keeps_its_own_credit(checkout):
    """QF-111 review 2, B4: pool photos are named by position, so the release's 14_commons.jpg can be a different
    photo from this host's (production sample: 21 of 196 same-name photos). Its credit is never taken."""
    from src import credits
    topic = checkout / "assets" / "t"
    _photos(topic, 1)
    _seed(topic / "credits.json", {"images": [{"file": "01_commons.jpg", "title": "File:Shipwreck.jpg",
                                               "artist": "Diver", "license": "CC BY-SA 4.0", "source_url": SHIP}]})
    mine = {"file": "01_commons.jpg", "title": "File:Model (7751351552).jpg", "artist": "Museum",
            "license": "CC BY 2.0", "source_url": MODEL}
    _review_copy(credits, topic, [dict(mine)])
    for read in (credits.load_credits(topic)["images"][0], credits.pool_record(topic / "credits.json")["images"][0]):
        assert {k: read[k] for k in mine} == mine          # its own title, artist, licence and source
    record = credits.load_credits(topic)
    credits.save_credits(topic, record)                     # a review write persists nothing of the other photo
    assert credits.load_credits(topic)["images"][0]["source_url"] == MODEL


def test_the_same_photo_under_another_name_takes_the_release_credit_but_keeps_its_source(checkout):
    from src import credits
    topic = checkout / "assets" / "t"
    _photos(topic, 2)
    _seed(topic / "credits.json", {"images": [{"file": "02_commons.jpg", "artist": "A. Person (corrected)",
                                               "license": "CC BY 4.0", "source_url": SHIP}]})
    (topic / "02_commons.jpg").write_bytes((topic / "01_commons.jpg").read_bytes())   # the same bytes, another name
    _review_copy(credits, topic, [{"file": "01_commons.jpg", "artist": "x@example.com", "license": "CC BY 4.0",
                                   "source_url": SHIP}])
    read = credits.load_credits(topic)["images"][0]
    assert read["file"] == "01_commons.jpg" and read["source_url"] == SHIP and read["artist"] == "A. Person (corrected)"
    # the same source but different bytes, or a recorded hash that differs: not the same photo
    (topic / "02_commons.jpg").write_bytes(b"other bytes")
    assert credits.load_credits(topic)["images"][0]["artist"] == "x@example.com"
    (topic / "02_commons.jpg").unlink()
    _seed(topic / "credits.json", {"images": [{"file": "01_commons.jpg", "artist": "A. Person (corrected)",
                                               "source_url": SHIP, "sha256": "a" * 64}]})
    _review_copy(credits, topic, [{"file": "01_commons.jpg", "artist": "x@example.com", "source_url": SHIP,
                                   "sha256": "b" * 64}])
    assert credits.load_credits(topic)["images"][0]["artist"] == "x@example.com"


def test_a_shipped_change_under_a_whole_copy_is_said(checkout, monkeypatch):
    import main
    from src import host_state, music
    library = checkout / "data" / "music" / "library.json"
    _seed(library, [{"file": "a.mp3", "title": "A", "reviewed": False}])
    monkeypatch.setattr(music, "LIBRARY", library)
    monkeypatch.setattr(music, "MUSIC_DIR", library.parent)
    music.save_library([{"file": "a.mp3", "title": "A", "reviewed": True}])
    assert host_state.shipped_changed() == []
    _seed(library, [{"file": "a.mp3", "title": "A (corrected)", "reviewed": False}])   # a release changes it
    said = []
    owner = SimpleNamespace(_alert=lambda text, **item: said.append((text, item)))
    assert main.warn_shipped_changes(owner) == ["data/music/library.json"]
    assert len(said) == 1 and "data/music/library.json" in said[0][0]
