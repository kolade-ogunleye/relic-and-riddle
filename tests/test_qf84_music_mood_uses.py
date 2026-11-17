"""QF-84: the music's mood comes from the sound plan, never a default; only a published Reel counts as a use; the
library rotates a 4-mood x 3-track set drawn from the tracks the project holds."""
import json
from collections import Counter
from typing import get_args

import numpy as np
import pytest
import soundfile as sf

from src import music
from src import sound_plan as sp
from src.llm import LLMError

NARRATION = "Builders raised the dome then fire took the city and nothing of the library survived the night"
WORDS = [{"word": w, "start": i * 1.0, "dur": 0.8} for i, w in enumerate(NARRATION.split())]


@pytest.fixture
def library(tmp_path, monkeypatch):
    """Two approved tracks in every mood, one retired, one unreviewed; real (silent) audio so the picks are real."""
    monkeypatch.setattr(music, "MUSIC_DIR", tmp_path)
    monkeypatch.setattr(music, "LIBRARY", tmp_path / "library.json")
    tracks = []
    for mood in music.MOODS:
        (tmp_path / mood).mkdir()
        for i in range(2):
            name = f"{mood}/{mood}_{i}.wav"
            sf.write(str(tmp_path / name), np.zeros((4800, 2), np.float32), 48000)
            tracks.append({"file": name, "title": f"{mood} {i}", "creator": "A", "license": "cc0", "mood": mood,
                           "duration_ms": 120_000, "reviewed": True, "uses": 5})
    tracks.append({**tracks[0], "file": "mysterious/retired.wav", "uses": 0})   # retired by rotation.json below
    tracks.append({**tracks[0], "file": "mysterious/unreviewed.wav", "reviewed": False, "uses": 0})
    for t in tracks[-2:]:
        sf.write(str(tmp_path / t["file"]), np.zeros((4800, 2), np.float32), 48000)
    music.save_library(tracks)
    (tmp_path / "rotation.json").write_text(json.dumps({"retired": {"mysterious/retired.wav": "QF-84: not in the set"}}),
                                            encoding="utf-8")
    return tmp_path


def _plan(sections, moments=((3, "reveal"),)):
    return sp.SoundPlan(moments=[sp.SoundMoment(word=w, kind=k) for w, k in moments],
                        sections=[sp.MoodSection(word=w, mood=m) for w, m in sections])


# ---------------------------------------------------------------- the mood
def test_a_failed_classifier_is_no_mood_not_mysterious(monkeypatch):
    monkeypatch.setattr("src.free_llm.ready", lambda: False)
    assert music.choose_mood({"title": "T", "narration_script": "x"}) is None        # was "mysterious"
    monkeypatch.setattr("src.free_llm.ready", lambda: True)

    def fails(*a, **k):
        raise LLMError("quota")
    monkeypatch.setattr("src.free_llm.call", fails)
    assert music.choose_mood({"title": "T", "narration_script": "x"}) is None        # was "mysterious"


def test_the_dominant_mood_is_the_one_covering_most_of_the_narration():
    # words 1-5 epic (0-5 s), 6-17 melancholic (5-16.8 s): melancholic is heard longest
    mood = sp.dominant_mood(_plan([(1, "epic"), (6, "melancholic"), (99, "tense")]), WORDS)
    assert mood["mood"] == "melancholic" and mood["reason"] == ""
    assert mood["seconds"] == {"epic": 5.0, "melancholic": 11.8} and mood["share"] == pytest.approx(11.8 / 16.8, abs=1e-3)
    assert [s["mood"] for s in mood["sections"]] == ["epic", "melancholic"]                  # word 99 does not exist
    assert sp.dominant_mood(_plan([(1, "tense"), (10, "epic")]), WORDS)["mood"] == "tense"  # 9 s v 7.8 s
    tie = [{"word": "a", "start": 0.0, "dur": 1.0}, {"word": "b", "start": 1.0, "dur": 1.0}]
    assert sp.dominant_mood(_plan([(1, "tense"), (2, "epic")], ()), tie)["mood"] == "tense"   # a tie: heard first
    empty = sp.dominant_mood(_plan([]), WORDS)
    assert empty["mood"] is None and "no mood sections" in empty["reason"]


def test_the_plan_carries_its_mood_and_a_failed_plan_says_why(monkeypatch):
    assert set(get_args(sp.Mood)) == set(music.MOODS)            # the plan can name every set, and nothing else
    monkeypatch.setattr(sp.free_llm, "ready", lambda: True)
    monkeypatch.setattr(sp.free_llm, "call", lambda *a, **k: _plan([(1, "tense"), (4, "melancholic")]))
    plan, mood = sp.plan_and_mood(WORDS, "T")
    assert plan["moments"] and plan["mood"]["mood"] == mood["mood"] == "melancholic"
    assert "mood sections" in sp.SYSTEM                          # the model is asked for them
    # A plan with sections but no usable moment: the bed keeps its word cues (None) and the music keeps the mood.
    monkeypatch.setattr(sp.free_llm, "call", lambda *a, **k: _plan([(1, "epic")], ()))
    assert sp.plan_and_mood(WORDS, "T") == (None, sp.dominant_mood(_plan([(1, "epic")], ()), WORDS))

    def fails(*a, **k):
        raise LLMError("429 quota")
    monkeypatch.setattr(sp.free_llm, "call", fails)
    plan, mood = sp.plan_and_mood(WORDS, "T")
    assert plan is None and mood["mood"] is None and "429 quota" in mood["reason"]
    monkeypatch.setattr(sp.free_llm, "ready", lambda: False)
    assert sp.plan_and_mood(WORDS, "T")[1]["reason"] == "no planning model is configured"
    assert sp.plan_and_mood([], "T")[1]["mood"] is None


def test_no_mood_holds_the_music_and_the_card_says_why(library, monkeypatch):
    monkeypatch.setattr(music, "choose_mood", lambda p: pytest.fail("no classifier when the plan is given"))
    held = music.music_for({}, 45, library, {"tier": "free"}, mood_record=sp.no_mood("the sound plan failed (429)"))
    assert held["source"] == "none" and held["path"] is None and held["mood"] is None
    assert music.music_note(held) == "🎵 Music held: no mood (the sound plan failed (429)); synthesized drone used"
    picked = music.music_for({"topic_id": "t"}, 45, library, {"tier": "free"},
                             mood_record={"mood": "melancholic", "reason": "", "share": 0.72})
    assert picked["source"] == "library" and picked["tracks"][0]["mood"] == "melancholic"
    assert music.music_note(picked) == "🎵 Music: free library track (melancholic, the sound plan's mood for 72% of the narration)"


# ---------------------------------------------------------------- uses
def test_picking_and_dry_runs_never_count_a_use(library):
    before = (library / "library.json").read_bytes()
    for _ in range(3):
        music.music_for({"topic_id": "t"}, 45, library, {"tier": "free"}, mood_record={"mood": "epic", "reason": ""})
        music.music_for({"topic_id": "t"}, 45, library, {"tier": "free"}, is_documentary=True,
                        mood_record={"mood": "epic", "reason": ""})
    assert (library / "library.json").read_bytes() == before     # was +1 `uses` per pick, dry runs included


def _selected(db, run, file, mood="epic"):
    db.create_run(run, "01_topic")
    db.record_music_selection(run, [{"file": file, "sha256": "ab" * 32, "mood": mood}])


def test_only_a_confirmed_reel_publication_counts_once(db):
    _selected(db, "dry", "epic/a.wav")
    db.record_publication("01_topic", "dry", "dry-run", "simulated", {})
    db.record_publication("01_topic", "dry", "youtube-shorts", "simulated", {})
    _selected(db, "failed", "epic/a.wav")
    db.record_publication("01_topic", "failed", "upload-post", "failed", {})
    db.record_publication("01_topic", "failed", "coordinator", "unknown", {})
    _selected(db, "companion", "epic/a.wav")
    db.record_publication("01_topic", "companion", "upload-post:story", "published", {})
    db.record_publication("01_topic", "companion", "facebook:captions", "published", {})
    _selected(db, "rejected", "epic/a.wav")                       # rendered, never published
    assert db.music_published_uses() == {}
    _selected(db, "live", "epic/a.wav")
    for channel in ("upload-post", "youtube-shorts", "reel-retry"):  # two platforms and a retry: one Reel
        db.record_publication("01_topic", "live", channel, "published", {})
    _selected(db, "confirmed", "epic/b.wav")
    db.record_publication("01_topic", "confirmed", "upload-post", "unknown", {})
    assert db.music_published_uses() == {"epic/a.wav": 1}
    db.record_publication("01_topic", "confirmed", "manual-confirmation", "published", {"confirmed_by": "human"})
    assert db.music_published_uses() == {"epic/a.wav": 1, "epic/b.wav": 1}  # reconciled once, not twice
    db.record_music_selection("confirmed", [{"file": "epic/c.wav", "mood": "epic"}])  # its bed was remade: the last
    assert db.music_published_uses() == {"epic/a.wav": 1, "epic/c.wav": 1}


def test_rotation_is_by_published_uses_within_the_mood_only(library):
    a, b = "tense/tense_0.wav", "tense/tense_1.wav"
    first = music.pick_library_track("tense", 30, published={}, strict=True, tie="t")["file"]
    other = b if first == a else a
    assert music.pick_library_track("tense", 30, published={first: 1}, strict=True, tie="t")["file"] == other
    assert music.pick_library_track("tense", 30, published={a: 1, b: 1}, strict=True, tie="t")["file"] == first
    picks = {music.pick_library_track("mysterious", 30, published={}, strict=True, tie=str(i))["file"] for i in range(40)}
    assert picks == {"mysterious/mysterious_0.wav", "mysterious/mysterious_1.wav"}   # never retired or unreviewed
    music.save_library([t for t in music.load_library(False) if t["mood"] != "melancholic"])
    assert music.pick_library_track("melancholic", 30, published={}, strict=True) is None  # no other mood fills in
    assert music.music_for({}, 45, library, {"tier": "free"}, mood_record={"mood": "melancholic", "reason": ""})[
        "held"] == "no approved melancholic track"


def test_twelve_published_reels_three_per_mood_use_every_track_of_a_three_track_set(library, db):
    lib = music.load_library(False)
    for mood in music.MOODS:                                     # a third track per mood
        name = f"{mood}/{mood}_2.wav"
        sf.write(str(library / name), np.zeros((4800, 2), np.float32), 48000)
        lib.append({**next(t for t in lib if t["mood"] == mood), "file": name})
    music.save_library(lib)
    for i, mood in enumerate(list(music.MOODS) * 3):
        run = f"r{i}"
        chosen = music.music_for({"topic_id": f"topic{i}"}, 45, library, {"tier": "free"},
                                 mood_record={"mood": mood, "reason": ""}, published=db.music_published_uses())
        db.create_run(run, "01_topic")
        db.record_music_selection(run, music.music_selection_rows(chosen))
        db.record_publication("01_topic", run, "upload-post", "published", {})
    uses = db.music_published_uses()
    eligible = {t["file"] for t in music.load_library() if music.in_rotation(t)}
    assert len(eligible) == 12 and {f: uses.get(f, 0) for f in eligible} == {f: 1 for f in eligible}


# ---------------------------------------------------------------- the pipeline
def test_the_reel_chooses_music_from_its_plan_and_records_the_selection(db, monkeypatch, tmp_path, curated_payload):
    from tests.test_concurrency import _resumable_run
    lib = json.loads(music.LIBRARY.read_text(encoding="utf-8"))
    track = next(t for t in lib if music.in_rotation(t))
    order = []
    monkeypatch.setattr(sp, "plan_and_mood", lambda words, title="": order.append("plan") or (
        None, {"mood": track["mood"], "reason": "", "share": 0.6}))
    real = music.music_for

    def music_for(*a, **k):
        order.append("music")
        assert k["mood_record"]["mood"] == track["mood"] and k["published"] == {}
        return real(*a, **k)
    monkeypatch.setattr("main.music_for", music_for)
    coord = _resumable_run(db, monkeypatch, tmp_path, curated_payload)
    monkeypatch.setattr(music, "load_library", lambda available_only=True: [track])
    seen = {}
    monkeypatch.setattr(coord, "_render_approve_publish", lambda *a: seen.update(payload=a[3]) or {"status": "simulated"})
    before = music.LIBRARY.read_bytes()
    coord.resume_run("r5", dry_run=True)
    assert order == ["plan", "music"]
    assert music.LIBRARY.read_bytes() == before                  # the dry run counted no use
    assert "the sound plan's mood for 60%" in seen["payload"]["music_note"]
    with db._conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT run_id, file, mood FROM music_selections")]
    assert rows == [{"run_id": "r5", "file": track["file"], "mood": track["mood"]}]
    assert db.music_published_uses() == {}


def test_the_reel_holds_its_music_when_the_plan_gives_no_mood(db, monkeypatch, tmp_path, curated_payload):
    from tests.test_concurrency import _resumable_run
    coord = _resumable_run(db, monkeypatch, tmp_path, curated_payload)   # no timed words: no plan, no mood
    monkeypatch.setattr(music, "choose_mood", lambda p: pytest.fail("a default or a second guess"))
    seen = {}
    monkeypatch.setattr(coord, "_render_approve_publish", lambda *a: seen.update(payload=a[3]) or {"status": "simulated"})
    coord.resume_run("r5", dry_run=True)
    assert seen["payload"]["music_note"] == ("🎵 Music held: no mood (the narration has no timed words); "
                                             "synthesized drone used")
    with db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM music_selections").fetchone()[0] == 0   # the drone is no track


# ---------------------------------------------------------------- the set
def test_the_committed_rotation_keeps_three_measured_tracks_per_mood():
    from src.config import DATA_DIR
    lib = json.loads((DATA_DIR / "music" / "library.json").read_text(encoding="utf-8"))
    retired = json.loads((DATA_DIR / "music" / "rotation.json").read_text(encoding="utf-8"))["retired"]
    live = [t for t in lib if music.in_rotation(t, retired)]
    assert Counter(t["mood"] for t in live) == {m: 3 for m in music.MOODS}
    approved = {t["file"] for t in lib if t.get("reviewed")}
    assert retired and set(retired) <= approved and all(r.startswith("QF-84: ") for r in retired.values())
    assert all(t.get("source_url") and t.get("license_url") for t in live)   # licensed tracks the project holds
    assert len(lib) == 24                                        # nothing removed: a retired track keeps its record


def test_the_library_every_host_rewrites_carries_no_rotation(tmp_path):
    """library.json is rewritten on every host (approvals; the old pick counts), and the deployer refuses a release
    that changes a file production changed differently: the set lives in rotation.json, which no host writes."""
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    lib = (root / "data" / "music" / "library.json").read_bytes()
    assert b'"retired"' not in lib
    (tmp_path / "data" / "music").mkdir(parents=True)
    (tmp_path / "data" / "music" / "library.json").write_bytes(lib)
    script = root / "docs" / "reviews" / "quality-fixes-2026-11" / "QF-84" / "music_set.py"
    subprocess.run([sys.executable, str(script), str(tmp_path), "--write"], check=True, capture_output=True)
    assert (tmp_path / "data" / "music" / "library.json").read_bytes() == lib                 # --write leaves it
    assert (tmp_path / "data" / "music" / "rotation.json").read_bytes() == (
        root / "data" / "music" / "rotation.json").read_bytes()                               # and reproduces the set
    # (no pick writes it either: test_picking_and_dry_runs_never_count_a_use)


def test_the_music_screens_show_the_rotation_and_published_uses(library, db):
    from src.control import make_command_handler
    _selected(db, "live", "epic/epic_0.wav")
    db.record_publication("01_topic", "live", "upload-post", "published", {})
    h = make_command_handler(db, lambda: None, lambda: "")
    text, _ = h("musicmenu", "")
    assert "9 approved (8 in rotation) · 1 waiting for review" in text
    pages = "".join(h("mlibrary", str(p))[0] for p in range(2))
    assert "epic · CC0 1 published" not in pages and "epic · CC0 · 1 published" in pages
    assert pages.count(" · retired") == 1 and pages.count(" · 0 published") == 7


def test_the_documentary_plans_first_and_a_resumed_one_keeps_its_held_music_line(db, tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    import main
    from src import audio_provenance
    from src.models import DeepDivePackage
    monkeypatch.setattr("src.config.VIDEO_FORMATS", ["reel", "documentary"])
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)
    act = {"act_number": 1, "act_title": "A", "target_duration_sec": 40, "narration_text": NARRATION,
           "visual_intent": "v"}
    pkg = DeepDivePackage(topic_id="01_topic", title="T", youtube_title="T", description="d", tags=[], acts=[act],
                          closing_debate_question="q", sources=[])

    class Stop(Exception):
        pass

    def voice(text, path, **k):
        path.write_bytes(b"voice")
        return WORDS
    plans, notes = [], []
    monkeypatch.setattr(sp, "plan_and_mood", lambda words, title="": plans.append(1) or (None, sp.no_mood("429 quota")))
    from src.audio_engine import rights_file
    monkeypatch.setattr(audio_provenance, "record_voice", lambda path, *a, **k: rights_file(path).write_text("{}"))
    monkeypatch.setattr(main, "documentary_cover", lambda title: {"title": title})
    monkeypatch.setattr(main, "ensure_all", lambda media: (_ for _ in ()).throw(Stop()))   # stop after the bed
    coordinator = object.__new__(main.PipelineCoordinator)
    coordinator.db, coordinator.telegram_bot = db, None
    coordinator.visual_engine = SimpleNamespace(get_scene_sequence=lambda t: [], hero_image=lambda m: None)
    coordinator.script_engine = SimpleNamespace(generate_deep_dive=lambda t, feedback="": pkg,
                                                verify_deep_dive_act_by_act=lambda p, t: (True, []))
    coordinator.audio_engine = SimpleNamespace(
        generate_narration=voice, get_duration=lambda p: 17.0, voice_quota=lambda: {"tier": "free"},
        generate_ambient_bed=lambda seconds, path, **k: path.write_bytes(b"drone"), last_sfx_sources=[],
        last_provider="edge-tts", last_licensed=False)
    coordinator._screen_photos = lambda topic: None
    coordinator._collect_findings = lambda *a, **k: None
    coordinator._alert = lambda *a, **k: None
    coordinator.voice_inputs = lambda text, dry_run: {"licensed_only": False, "allow_paid": False}
    coordinator._check_cancel = lambda run_id: notes.append(sys._getframe(1).f_locals.get("payload", {}).get("music_note"))
    for _ in range(2):
        coordinator.produce_deep_dive({"id": "01_topic", "title": "Topic 1"}, dry_run=True, run_id="run_doc")
    held = "🎵 Music held: no mood (429 quota); synthesized drone used"
    assert plans == [1]                                  # planned once, before the music; the resume reuses the bed
    assert notes.count(held) == 2                        # the line on the first run and on the resumed one
    assert json.loads((tmp_path / "run_doc" / "music_selection.json").read_text(encoding="utf-8"))["held"]


def test_the_plan_call_requires_mood_sections():
    with pytest.raises(ValueError):
        sp.PlannedSound(moments=[])                              # the call's schema: 1 to 4 sections, required
    assert "sections" in sp.PlannedSound.model_json_schema()["required"]
    assert sp.PlannedSound(moments=[], sections=[sp.MoodSection(word=1, mood="epic")]).sections


def test_the_first_start_backfills_earlier_runs_once(db, tmp_path):
    def run(name, music_record, status="published"):
        db.create_run(name, "01_topic")
        (tmp_path / name).mkdir()
        (tmp_path / name / "music_selection.json").write_text(json.dumps(music_record), encoding="utf-8")
        db.record_publication("01_topic", name, "upload-post", status, {})
    track = {"file": "epic/a.wav", "mood": "epic", "path": str(tmp_path / "missing.wav")}
    run("old", {"source": "library", "file": "epic/a.wav", "tracks": [track]})
    run("older", {"source": "library", "file": "epic/b.wav", "mood": "epic"})   # a record from before "tracks"
    run("failed", {"source": "library", "file": "epic/a.wav", "tracks": [track]}, status="failed")
    run("drone", {"source": "none", "file": ""})
    db.create_run("new", "01_topic")
    db.record_music_selection("new", [{"file": "epic/c.wav", "mood": "epic"}])     # recorded by the new code
    db.record_publication("01_topic", "new", "upload-post", "published", {})
    assert db.backfill_music_selections(tmp_path) == 3            # old, older and failed: the drone has no track
    assert db.music_published_uses() == {"epic/a.wav": 1, "epic/b.wav": 1, "epic/c.wav": 1}
    assert db.backfill_music_selections(tmp_path) == 0            # once per database
    assert db.music_published_uses() == {"epic/a.wav": 1, "epic/b.wav": 1, "epic/c.wav": 1}


def test_ask_counts_the_rotation_not_the_retired_tracks(library, db, monkeypatch):
    from src import free_llm, probe
    monkeypatch.setattr(free_llm, "call", lambda *a, **k: pytest.fail("a count needs no model"))
    out = probe.probe_system("how many music tracks are approved?", db=db)
    assert "9 approved · 1 waiting for review · 8 in rotation" in out
    assert "In rotation by mood: mysterious 2, epic 2, melancholic 2, tense 2" in out


def test_startup_runs_the_backfill(db, monkeypatch, tmp_path):
    from types import SimpleNamespace

    import main
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(main, "reconcile_interrupted_runs", lambda *a: [])
    main._report_interrupted(SimpleNamespace(db=db, topic_engine=None), SimpleNamespace(notify=lambda *a, **k: None))
    assert db.get_setting(db.MUSIC_BACKFILL_KEY)
