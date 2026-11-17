"""QF-127 measurement: who voices a run, and what its record says about the licence, with the code at <repo root>.

    python docs/reviews/quality-fixes-2026-11/QF-127/measure.py <repo root>

Prints one JSON object and writes nothing under the repository (the publish cases use a temporary folder, removed
at the end). No network: every voice provider and every uploader is a stand-in.

"real_runs" / "dry_runs": the engine at <repo root> (src/audio_engine.py, AudioEngine.generate_narration) asked for
a narration in every combination of
    VOICE_PREFER          elevenlabs, azure
    ElevenLabs            no key, plan unknown, Free plan, paid plan, paid plan failing, paid plan out of characters,
                          a plan name that is not one of ElevenLabs' paid plans, a paid plan name while trialing
    Azure Speech          no key, key with no tier declared, key with AZURE_SPEECH_TIER=F0, key with =S0
as a real run asks (licensed_only=True, allow_paid=True) and as a dry run asks (licensed_only=False,
allow_paid=False). A take is judged here, independently of the code measured: commercially licensed only when it
was voiced by ElevenLabs on a paid plan (one ElevenLabs names, its subscription active) or by Azure Speech with the
paid tier S0 declared.
    voiced_unlicensed    takes voiced by anything else (the acceptance: 0 for real runs)
    marked_licensed_but_unlicensed   of those, takes the engine recorded as licensed
    held_saying_free_plan_for_another_plan   real runs held with a reason that says "Free plan" although the
                         ElevenLabs account was not the Free plan (its plan could not be read: acceptance 0)

"publish": the publisher at <repo root> (src/publisher.py, MetaPublisher.publish_reel and publish_short, the only two
functions that send a master's audio to a platform) asked for a real upload of a master whose voice.json is each of
RECORDS (12 shapes: 3 licensed, 9 not, judged here), as a Reel and as a YouTube Short.
    gate_on.unlicensed_uploaded     uploads of an unlicensed master with VOICE_REQUIRE_LICENSED=true (acceptance: 0)
    gate_on.licensed_uploaded       uploads of a licensed master (acceptance: every one, 3 Reels and 3 Shorts)
    gate_off.unlicensed_uploaded    the same with the switch off: they go out, as before the row
    gate_off.unlicensed_on_record   of those, uploads that returned published and have, each for its own run, an
                                    audit event saying "posted with" and the same fact kept for the receipt
    gate_off.not_published_asked    the 9 unlicensed records again with the switch off and an upload that does NOT
                                    publish (OUTCOMES: the Reel failed, unknown, no account set up; the Short failed,
                                    unknown, skipped)
    gate_off.not_published_said_posted   of those, uploads for which an audit event or a kept note says "posted"
                                    (acceptance: 0; nothing went up)

"dev_runs": the saved voice records (voice.json) of the runs under <repo root>/output/run_*, or, when <repo root>
is a git worktree with no run of its own, of the main checkout's (read-only; counts only, no run is named).
"marked_licensed" is what the record says, which is what publishing read before QF-127; "licensed_now" is the
record read by src/voice_licence.record_licensed at <repo root> (null where that module does not exist yet).
"""
import asyncio
import importlib
import json
import sys
from collections import Counter
from pathlib import Path

WORDS = [{"word": "Hi", "start": 0.0, "dur": 0.3}]
ELEVEN = {"no key": None, "plan unknown": {}, "free plan": {"tier": "free", "used": 0, "limit": 10_000},
          "paid plan": {"tier": "creator", "used": 0, "limit": 100_000},
          "paid plan failing": {"tier": "creator", "used": 0, "limit": 100_000},
          "paid plan out of characters": {"tier": "creator", "used": 99_999, "limit": 100_000},
          "plan name not on the table": {"tier": "zzz", "used": 0, "limit": 100_000},
          "trialing on a paid plan name": {"tier": "creator", "status": "trialing", "used": 0, "limit": 100_000}}
AZURE = {"no key": None, "key, no tier declared": "", "key, F0 declared": "F0", "key, S0 declared": "S0"}
TEXT = "A short narration for the measurement."


def truly_licensed(provider: str, eleven: str, azure: str) -> bool:
    if provider == "elevenlabs":
        return eleven.startswith("paid plan")    # not "plan name not on the table", not "trialing on a paid plan name"
    return provider == "azure" and azure == "key, S0 declared"


def take(engine_module, default_tier, prefer: str, eleven: str, azure: str, real: bool, out: Path) -> dict:
    engine_module.VOICE_PREFER = prefer
    engine_module.AZURE_SPEECH_TIER = AZURE[azure] or default_tier   # as config read it; unused before QF-127
    eng = engine_module.AudioEngine(eleven_key="k" * 20 if ELEVEN[eleven] is not None else "", eleven_voice="v",
                                    azure_key="a" * 20 if AZURE[azure] is not None else "",
                                    azure_region="eastus" if AZURE[azure] is not None else "")
    eng.voice_quota = lambda: (ELEVEN[eleven] or None)

    def eleven_take(text, path):
        if eleven == "paid plan failing":
            raise RuntimeError("down")
        return list(WORDS)

    async def edge_take(text, path):
        return list(WORDS)
    eng._generate_elevenlabs = eleven_take
    eng._generate_azure = lambda text, path: list(WORDS)
    eng._generate_edge_tts = edge_take
    try:
        eng.generate_narration(TEXT, out, licensed_only=real, allow_paid=real)
    except engine_module.NoLicensedVoice as e:
        return {"voiced_by": None, "marked_licensed": False, "licensed": False,
                "wrong_free_plan": "Free plan" in str(e) and eleven != "free plan"}
    return {"voiced_by": eng.last_provider, "marked_licensed": bool(eng.last_licensed),
            "licensed": truly_licensed(eng.last_provider, eleven, azure)}


def engine_cases(root: Path) -> dict:
    sys.path.insert(0, str(root))
    engine_module = importlib.import_module("src.audio_engine")
    default_tier = getattr(importlib.import_module("src.config"), "AZURE_SPEECH_TIER", "")
    engine_module.asyncio.run = lambda coro: asyncio.new_event_loop().run_until_complete(coro)
    out = {}
    for real in (True, False):
        takes = [take(engine_module, default_tier, prefer, eleven, azure, real, Path("unused.wav"))
                 for prefer in ("elevenlabs", "azure") for eleven in ELEVEN for azure in AZURE]
        voiced = Counter(t["voiced_by"] or "held (no licensed voice)" for t in takes)
        out["real_runs" if real else "dry_runs"] = {
            "cases": len(takes),
            "voiced_by": dict(sorted(voiced.items())),
            "voiced_unlicensed": sum(1 for t in takes if t["voiced_by"] and not t["licensed"]),
            "marked_licensed_but_unlicensed": sum(1 for t in takes if t["marked_licensed"] and not t["licensed"]),
            **({"held_saying_free_plan_for_another_plan": sum(1 for t in takes if t.get("wrong_free_plan"))}
               if real else {}),
        }
    return out


def output_folder(root: Path) -> Path | None:
    def has_runs(folder: Path) -> bool:
        return folder.is_dir() and any(p.is_dir() for p in folder.glob("run_*"))
    if has_runs(root / "output"):
        return root / "output"
    marker = root / ".git"
    if marker.is_file():   # a worktree: "gitdir: <main checkout>/.git/worktrees/<name>"
        gitdir = Path(marker.read_text(encoding="utf-8").split(":", 1)[1].strip())
        main = gitdir.parent.parent.parent
        if gitdir.parent.name == "worktrees" and has_runs(main / "output"):
            return main / "output"
    return None


def dev_runs(root: Path) -> dict:
    folder = output_folder(root)
    if folder is None:
        return {"runs": 0, "note": "no run folder at this root"}
    try:
        record_licensed = importlib.import_module("src.voice_licence").record_licensed
    except ImportError:
        record_licensed = None
    runs = sorted(p for p in folder.glob("run_*") if p.is_dir())
    by_provider: dict[str, Counter] = {}
    recorded = 0
    for run in runs:
        try:
            record = json.loads((run / "voice.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        recorded += 1
        row = by_provider.setdefault(str(record.get("provider") or "unknown"), Counter())
        row["runs"] += 1
        row["marked_licensed"] += record.get("licensed") is True
        if record_licensed is not None:
            row["licensed_now"] += record_licensed(record)
    table = {}
    for provider, row in sorted(by_provider.items()):
        table[provider] = {"runs": row["runs"], "marked_licensed": row["marked_licensed"],
                           "licensed_now": row["licensed_now"] if record_licensed is not None else None}
    total = {key: (sum(v[key] for v in table.values()) if all(v[key] is not None for v in table.values()) else None)
             for key in ("runs", "marked_licensed", "licensed_now")}
    return {"runs": len(runs), "runs_with_a_voice_record": recorded, "by_provider": table, "all_providers": total}


# voice.json as saved (None: no file; a string: the file's text), and whether that voice is licensed, judged here
RECORDS = {
    "elevenlabs, licensed, saved before the tier was written": ({"provider": "elevenlabs", "licensed": True}, True),
    "elevenlabs creator, active": ({"provider": "elevenlabs", "tier": "creator", "plan_status": "active",
                                    "licensed": True}, True),
    "azure s0 declared": ({"provider": "azure", "tier": "s0", "licensed": True}, True),
    "azure, saved licensed, no tier": ({"provider": "azure", "licensed": True}, False),
    "azure f0, saved licensed": ({"provider": "azure", "tier": "f0", "licensed": True}, False),
    "elevenlabs free, saved licensed": ({"provider": "elevenlabs", "tier": "free", "licensed": True}, False),
    "elevenlabs, plan name not on the table": ({"provider": "elevenlabs", "tier": "zzz", "licensed": True}, False),
    "elevenlabs creator, trialing": ({"provider": "elevenlabs", "tier": "creator", "plan_status": "trialing",
                                      "licensed": True}, False),
    "edge-tts, saved licensed": ({"provider": "edge-tts", "licensed": True}, False),
    "no provider, saved licensed": ({"licensed": True}, False),
    "no voice.json": (None, False),
    "voice.json unreadable": ("{", False),
}


class Uploader:
    """A stand-in for a platform uploader: it records what it was asked to send, and ends as `outcome` says
    ("published", or the name of the publisher's exception to raise: the upload did not publish)."""
    name, platforms = "stand-in", ("facebook", "instagram")

    def __init__(self, sent: list[str], outcome: str = "published", errors=None):
        self.sent, self.outcome, self.errors = sent, outcome, errors

    def _end(self, what: str):
        self.sent.append(what)
        if self.outcome != "published":
            raise getattr(self.errors, self.outcome)("stand-in: the upload did not publish")
        return {"post_urls": {}}

    def publish(self, envelope):
        return self._end("reel")

    def publish_short(self, envelope):
        return self._end("short")


class ReelOnlyUploader:
    """No account that can upload Shorts: the Short is skipped."""
    name, platforms = "stand-in", ("facebook", "instagram")

    def __init__(self, sent: list[str]):
        self.sent = sent

    def publish(self, envelope):
        self.sent.append("reel")
        return {"post_urls": {}}


# gate off, an unlicensed master, an upload that does not publish: (what is asked, how the stand-in ends, the status)
OUTCOMES = [("reel", "PublishFailed", "failed"), ("reel", "PublishUnknown", "unknown"),
            ("reel", "NotConfigured", "failed"), ("short", "PublishFailed", "failed"),
            ("short", "PublishUnknown", "unknown"), ("short", "skipped", "skipped")]


def says_posted(db, meta, run_id: str) -> tuple[bool, bool]:
    """(an audit event of this run says "posted", a note kept for this run's receipt says "posted")."""
    events = [str(e.get("payload") or "") for e in db.list_audit_logs(100000)]
    in_audit = any(f'"{run_id}"' in p and "posted" in p.split('"reason"', 1)[-1].replace("not posted", "") for p in events)
    notes = getattr(meta, "voice_notes", {}).get(run_id) or {}
    return in_audit, any("posted" in str(n) for n in notes.values())


def publish_cases(root: Path) -> dict:
    import shutil
    import tempfile
    sys.path.insert(0, str(root))
    config = importlib.import_module("src.config")
    publisher = importlib.import_module("src.publisher")
    database = importlib.import_module("src.db")
    publisher._assert_recovery_unlocked = lambda: None
    work = Path(tempfile.mkdtemp(prefix="qf127-measure-"))
    out = {}
    try:
        bank = work / "topic_bank.json"
        bank.write_text(json.dumps({"topics": [{"id": "01_topic", "title": "Topic 1", "hook": "h",
                                                "location": {"name": "X", "country": "Y"}}]}), encoding="utf-8")
        for gate in (True, False):
            config.VOICE_REQUIRE_LICENSED = gate
            config.OUTPUT_DIR = work / f"output-gate-{gate}"
            db = database.PipelineDatabase(db_path=work / f"gate-{gate}.db", topic_bank_file=bank)
            row = Counter()
            for n, (record, is_licensed) in enumerate(RECORDS.values()):
                for what in ("reel", "short"):
                    run_id = f"run_{n}_{what}"
                    (config.OUTPUT_DIR / run_id).mkdir(parents=True)
                    if record is not None:
                        (config.OUTPUT_DIR / run_id / "voice.json").write_text(
                            record if isinstance(record, str) else json.dumps(record), encoding="utf-8")
                    db.create_run(run_id, "01_topic")
                    sent: list[str] = []
                    envelope = {"run_id": run_id, "topic_id": "01_topic", "platforms": ["facebook", "instagram"]}
                    meta = publisher.MetaPublisher(db, [Uploader(sent)])
                    res = (meta.publish_reel if what == "reel" else meta.publish_short)(envelope, dry_run=False)
                    if is_licensed:
                        row["licensed_uploaded"] += bool(sent)
                    else:
                        row["unlicensed_uploaded"] += bool(sent)
                        row["unlicensed_on_record"] += bool(sent and res.get("status") == "published"
                                                            and all(says_posted(db, meta, run_id)))
                if gate or is_licensed:
                    continue
                for m, (what, ending, status) in enumerate(OUTCOMES):   # the switch off, an upload that does not publish
                    run_id = f"run_{n}_{what}_not_published_{m}"
                    (config.OUTPUT_DIR / run_id).mkdir(parents=True)
                    if record is not None:
                        (config.OUTPUT_DIR / run_id / "voice.json").write_text(
                            record if isinstance(record, str) else json.dumps(record), encoding="utf-8")
                    db.create_run(run_id, "01_topic")
                    sent = []
                    uploader = ReelOnlyUploader(sent) if ending == "skipped" else Uploader(sent, ending, publisher)
                    envelope = {"run_id": run_id, "topic_id": "01_topic", "platforms": ["facebook", "instagram"]}
                    meta = publisher.MetaPublisher(db, [uploader])
                    res = (meta.publish_reel if what == "reel" else meta.publish_short)(envelope, dry_run=False)
                    assert res.get("status") == status, (run_id, res)      # the stand-in really did not publish
                    row["not_published_asked"] += 1
                    row["not_published_said_posted"] += any(says_posted(db, meta, run_id))
            out["gate_on" if gate else "gate_off"] = {
                "uploads_asked": 2 * len(RECORDS), "licensed_uploaded": row["licensed_uploaded"],
                "unlicensed_uploaded": row["unlicensed_uploaded"],
                **({} if gate else {"unlicensed_on_record": row["unlicensed_on_record"],
                                    "not_published_asked": row["not_published_asked"],
                                    "not_published_said_posted": row["not_published_said_posted"]})}
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return out


if __name__ == "__main__":
    repo = Path(sys.argv[1]).resolve()
    result = engine_cases(repo)
    result["publish"] = publish_cases(repo)
    result["dev_runs"] = dev_runs(repo)
    print(json.dumps(result, indent=1, sort_keys=True))
