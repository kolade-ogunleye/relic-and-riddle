"""QF-111 acceptance: runtime writers that change a file the release ships.

    python measure.py <repo root>

Runs the code of <repo root> (the base too). Each runtime writer of a tracked file found by the audit (README) is run
once against a stand-in app root in a temporary folder (config.BASE_DIR points there; seeds are generated, nothing is
read from the checkout's data), and the shipped file's bytes are compared before and after. Also checks that the
change is what the application reads back. Writes nothing outside the temporary folder; prints JSON.

- writers: {writer: {"shipped_file_changed": bool, "change_read_back": bool}}
- shipped_files_changed: how many writers changed the shipped file (acceptance: 0)
- read_back: how many writers' change the application reads back (acceptance: all)
- masking (review 1, B2): on the real tracked data, how much of a release's change a host with its own copy misses
  (acceptance: 0 masked corrections, 0 sheets researched again, 0 stale attributions; whole-copy changes said)
"""

import json
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))

from src import config


def seed(path: Path, value) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, indent=2).encode()
    path.write_bytes(data)
    return data


def music_approval(app: Path):
    from src import music
    library = app / "data" / "music" / "library.json"
    before = seed(library, [{"file": "mysterious/a.mp3", "title": "A", "mood": "mysterious", "reviewed": False,
                             "creator": "c", "duration_ms": 60000, "license": "cc0"}])
    music.LIBRARY, music.MUSIC_DIR = library, library.parent
    music.approve_track(music.track_key({"file": "mysterious/a.mp3"}))
    return library.read_bytes() != before, music.load_library(available_only=False)[0]["reviewed"] is True


def photo_review(app: Path):
    from src import credits
    topic = app / "assets" / "t"
    before = seed(topic / "credits.json", {"reviewed": False, "images": [{"file": "01.jpg"}]})
    (topic / "01.jpg").write_bytes(b"jpeg")
    record = credits.load_credits(topic)
    record.update(reviewed=True)
    credits.save_credits(topic, record)
    return (topic / "credits.json").read_bytes() != before, credits.load_credits(topic)["reviewed"] is True


def storyboard(app: Path):
    from src import storyboard as sb
    topic = app / "assets" / "t"
    before = seed(topic / "storyboard.json", [{"beat": 1}])
    sb.save_storyboard(topic, [{"beat": 1}, {"beat": 2}])
    return (topic / "storyboard.json").read_bytes() != before, sb.load_storyboard(topic) == [{"beat": 1}, {"beat": 2}]


def fact_sheet_rebuild(app: Path):
    from src import fact_sheet
    fact_sheet.SHEETS_DIR = app / "data" / "fact_sheets"
    shipped = fact_sheet.SHEETS_DIR / "t.json"
    before = seed(shipped, {"topic_id": "t", "facts": []})
    fact_sheet.save({"topic_id": "t", "facts": [], "built_at": "2026-11-16T00:00:00+00:00"})
    used = getattr(fact_sheet, "current_path", fact_sheet.path_for)("t")
    return shipped.read_bytes() != before, json.loads(used.read_text(encoding="utf-8")).get("built_at") is not None


def geocoding(app: Path):
    from src import geo
    cache = app / "data" / "geo_cache.json"
    before = seed(cache, {"old|place": {"lat": 1.0, "lon": 2.0}})
    geo.CACHE_FILE = cache
    geo.available = lambda: True
    geo.find_country = lambda name, source=False: {"name": name}
    geo._wikidata_point = lambda q, c, n: {"lat": 3.0, "lon": 4.0, "qid": "Q1", "country": n}
    geo.locate({"location": {"name": "New", "country": "Land"}})
    return cache.read_bytes() != before, "new|land" in geo._load_cache()


def topic_added(app: Path):
    from src import topic_store
    bank, queries = app / "data" / "topics" / "topic_bank.json", app / "data" / "topics" / "image_queries.json"
    before = seed(bank, {"total": 1, "topics": [{"id": "shipped"}]}) + seed(queries, {"shipped": {"queries": ["s"]}})
    with topic_store.transaction(bank):
        topic_store.commit(bank, queries, {"total": 2, "topics": [{"id": "shipped"}, {"id": "mine"}]},
                           {"shipped": {"queries": ["s"]}, "mine": {"queries": ["m"]}})
    reader = getattr(topic_store, "read_bank", lambda b: json.loads(Path(b).read_text(encoding="utf-8")))
    return (bank.read_bytes() + queries.read_bytes()) != before, \
        [t["id"] for t in reader(bank)["topics"]] == ["shipped", "mine"]


WRITERS = {"music library: approval (Telegram mkeep)": music_approval,
           "photo pool credits.json: review/fetch/drop (save_credits)": photo_review,
           "photo pool storyboard.json (fetch)": storyboard,
           "fact sheet: build or rebuild (fact_sheet.save)": fact_sheet_rebuild,
           "geocoding cache (geo.locate)": geocoding,
           "topic bank and photo searches: topic added (topic_store.commit)": topic_added}


# ------------------------------------------------------------------------------------------------ review 1, B2
# Masking: does a release's change to a shipped file reach a host that keeps its own copy? Run on the real tracked
# data of <repo root> (data/geo_cache.json, data/fact_sheets/*.json, assets/*/credits.json and the same records at
# 7d063bd4^, before QF-42's credit-line fix), copied into a stand-in app root. The fact-sheet clock is pinned to
# 2026-11-16 so the figures do not age. Code without host_state (the base) has no host copies: null.
import subprocess
from datetime import UTC as _UTC
from datetime import datetime as _datetime


def _git(*args) -> bytes:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True).stdout


def masking() -> dict | None:
    try:
        from src import host_state
    except ImportError:
        return None
    from src import credits, fact_sheet, geo, music
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp) / "app"
        (app / "data").mkdir(parents=True)
        config.BASE_DIR = app
        config.HOST_STATE_DIR = app / "data" / "host"

        # geocoding cache: this host makes one lookup of its own, then a release corrects every shipped place
        shipped = json.loads((root / "data" / "geo_cache.json").read_text(encoding="utf-8"))
        cache = app / "data" / "geo_cache.json"
        seed(cache, shipped)
        geo.CACHE_FILE = cache
        geo.available = lambda: True
        geo.find_country = lambda name, source=False: {"name": name}
        geo._wikidata_point = lambda q, c, n: {"lat": 3.0, "lon": 4.0, "qid": "Q1", "country": n}
        geo.locate({"location": {"name": "A place of this host", "country": "Land"}})
        corrected = {k: {**v, "corrected": True} for k, v in shipped.items()}
        seed(cache, corrected)
        now = geo._load_cache()
        out["geocoding cache"] = {"shipped places": len(shipped),
                                  "release corrections masked by the host copy": sum(
                                      now.get(k) != v for k, v in corrected.items()),
                                  "host's own lookup kept": "a place of this host|land" in now}

        # fact sheets: a host copy left from an earlier rules version beside each real shipped sheet
        class Fixed(_datetime):
            @classmethod
            def now(cls, tz=None):
                return _datetime(2026, 11, 16, tzinfo=tz or _UTC)
        fact_sheet.datetime = Fixed
        fact_sheet.SHEETS_DIR = app / "data" / "fact_sheets"
        usable = researched = 0
        sheets = sorted((root / "data" / "fact_sheets").glob("*.json"))
        for path in sheets:
            sheet = json.loads(path.read_text(encoding="utf-8"))
            topic = path.stem
            target = fact_sheet.SHEETS_DIR / path.name
            seed(target, sheet)
            copy = host_state.host_copy(target)
            seed(copy, {**sheet, "rules_version": "older"})
            shipped_ok = sheet.get("rules_version") == fact_sheet.RULES_VERSION
            usable += shipped_ok
            if shipped_ok and fact_sheet.load(topic) is None:
                researched += 1   # load() gives nothing: ensure() would research the topic again
        out["fact sheets"] = {"shipped sheets": len(sheets), "usable under today's rules": usable,
                              "researched again although the shipped sheet is usable": researched}

        # credits.json: this host's review copy made from the record before QF-42, the release shipping QF-42's fix
        pools = [p for p in _git("ls-files", "assets/*/credits.json").decode().split() if p]
        stale_entries = fixed_pools = 0
        for rel in pools:
            try:
                before = json.loads(_git("show", f"7d063bd4^:{rel}"))
            except subprocess.CalledProcessError:
                continue
            after = json.loads((root / rel).read_bytes())
            if before == after:
                continue
            fixed_pools += 1
            topic = app / rel.rsplit("/", 1)[0]
            target = topic / "credits.json"
            seed(target, before)
            for entry in (before.get("images") or []) if isinstance(before, dict) else []:
                (topic / entry["file"]).write_bytes(b"photo")
            copy = host_state.host_copy(target)
            seed(copy, before)                       # this host's own copy, as it was made then
            seed(target, after)                      # the release's corrected record
            read = getattr(credits, "pool_record", lambda p: json.loads(host_state.current(p).read_text()))(target)
            wanted = {e["file"]: e for e in (after.get("images") or []) + (after.get("rejected_by_review") or [])
                      if isinstance(e, dict) and e.get("file")}
            for entry in (read.get("images") or []) + (read.get("rejected_by_review") or []):
                ship = wanted.get(entry.get("file")) if isinstance(entry, dict) else None
                fields = getattr(credits, "ATTRIBUTION_FIELDS", ("artist", "author", "credit", "title", "license"))
                if ship and any(entry.get(k) != ship.get(k) for k in fields if k in ship):
                    stale_entries += 1
        out["photo credits"] = {"pools whose shipped record changed (QF-42's fix)": fixed_pools,
                                "photos read with a stale attribution": stale_entries}

        # whole copies: the music library changes under this host's copy
        library = app / "data" / "music" / "library.json"
        seed(library, json.loads((root / "data" / "music" / "library.json").read_text(encoding="utf-8")))
        music.LIBRARY, music.MUSIC_DIR = library, library.parent
        music.save_library(json.loads(library.read_text(encoding="utf-8")))
        seed(library, [])
        said = getattr(host_state, "shipped_changed", list)()
        out["whole copies"] = {"shipped changes under a host copy said at run start": len(said)}
    return out



out = {}
with tempfile.TemporaryDirectory() as tmp:
    for i, (name, writer) in enumerate(WRITERS.items()):
        app = Path(tmp) / f"app{i}"
        (app / "data").mkdir(parents=True)
        config.BASE_DIR = app
        config.HOST_STATE_DIR = app / "data" / "host"
        changed, read_back = writer(app)
        out[name] = {"shipped_file_changed": bool(changed), "change_read_back": bool(read_back)}
print(json.dumps({"writers": out,
                  "shipped_files_changed": sum(v["shipped_file_changed"] for v in out.values()),
                  "read_back": sum(v["change_read_back"] for v in out.values()),
                  "writers_measured": len(out),
                  "masking": masking()}, indent=1))
