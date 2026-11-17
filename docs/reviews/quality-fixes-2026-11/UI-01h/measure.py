"""UI-01h measurements: what each hold reason tells the operator to do, on each surface it is read on.

    python measure.py <repo root>

Runs the code of the repo given (on the parent and on the commit) with TELEGRAM_CLEAN_CHAT on, against a fake Bot API
(no network; a throwaway database and output folder in the system temp folder, no .env). The checker and the drivers
of the surfaces are this row's regression test (tests/test_ui01h_hold_advice.py, loaded from the tree this file is
in), so the remedy vocabulary is not the code's own:

- every hold reason the release gate writes that names a remedy (QF-126/126b's licence credits as the committed
  development runs give them, and the forms they do not: copy written before the credits were asked for, a text over
  its limit, a text cut at it; QF-42's credit lines; a Story frame drawn before Story credits; QF-88's caption rate),
  each drawn by the real code on the review panel (Summary and Checks), a booking held at its slot (its item and its
  Details) and a run the release gate failed (its item and its Details); counted: the surfaces whose text names a
  remedy no button there offers, and those that lose what was found;
- QF-127's voice hold, with the run resumable and not: its item's text against its buttons;
- the blocked booking's item page: sentences said twice;
- credit names in one reason that read alike, on the committed development runs (tests/fixtures/qf126b/dev_runs.json);
- QF-65e item 1: an AI motion clip of a run with no provenance.json said to be "in provenance.json".

With RR_REAL_OUTPUT set to one or more folders of real run_* folders (separated by ";"; read only, each run's ledger
copied to a temporary folder), the same "read alike" count on them, and RR_ASSETS (";"-separated, one per folder)
names each folder's topic photos, so a photo on its topic's sheet is named by its number there. Counts only; each
held kind's longest real hold (the committed development runs', and each folder's) is also drawn on every surface and
counted, and with RR_RENDERS set to a file, the texts drawn are written there (Markdown).
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import shutil
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
HERE = Path(__file__).resolve()
TMP = Path(tempfile.mkdtemp(prefix="ui01h-measure-"))
os.environ["RELIC_APP_ROOT"] = str(TMP)
for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "UPLOAD_POST_API_KEY", "HEALTHCHECK_PING_URL"):
    os.environ[name] = ""
socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(OSError("no network in measure.py"))
sys.path.insert(0, str(ROOT))
(TMP / "data").mkdir()
for name in ("reference_facts.json", "stage_budget.json", "cover_art_direction.json"):   # read at import
    if (ROOT / "data" / name).is_file():
        shutil.copy2(ROOT / "data" / name, TMP / "data" / name)
shutil.copytree(ROOT / "fonts", TMP / "fonts")   # main.py's imports measure type at import

from _pytest.monkeypatch import MonkeyPatch

from src import config, credits, publishable
from src.db import PipelineDatabase

config.TELEGRAM_CARDS_V2 = False
config.YOUTUBE_SHORTS = False
config.IMAGES_ALLOW_SHARE_ALIKE = False

spec = importlib.util.spec_from_file_location("ui01h_checker", HERE.parents[4] / "tests" / "test_ui01h_hold_advice.py")
T = importlib.util.module_from_spec(spec)
spec.loader.exec_module(T)
DEV_RUNS = ROOT / "tests" / "fixtures" / "qf126b" / "dev_runs.json"


def a_world(name: str, mp: MonkeyPatch):
    bank = TMP / f"{name}.json"
    bank.write_text(json.dumps({"topics": [{"id": "01_topic", "title": "Topic", "hook": "h",
                                            "location": {"name": "X", "country": "Y"}}]}), encoding="utf-8")
    db = PipelineDatabase(db_path=TMP / f"{name}.db", topic_bank_file=bank)
    folder = TMP / name
    folder.mkdir()
    mp.setattr(config, "OUTPUT_DIR", folder / "output")
    T.panel.clean_chat.__wrapped__(mp)
    bot, fake = T.panel.make_bot(db, mp)
    import main
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.db, c.telegram_bot = db, bot
    c.topic_engine = type("Topics", (), {"record_status": staticmethod(lambda *a, **k: None)})()
    return type("World", (), {"db": db, "bot": bot, "fake": fake, "c": c, "tmp": folder})()


def surfaces_of_every_hold() -> dict:
    out = {"holds": 0, "surfaces_drawn": 0, "remedy_not_offered": 0, "what_was_found_lost": 0, "by_surface": {},
           "examples": []}
    holds = None
    for kind in T.HOLDS:
        mp = MonkeyPatch()
        try:
            w = a_world(f"hold_{kind}", mp)
            holds = holds or T.gate_holds(TMP / "holds")
            hold = holds[kind]
            out["holds"] += 1
            for surface, (text, labels) in T._surfaces(w, mp, 0, [hold]).items():
                out["surfaces_drawn"] += 1
                missing = sorted(T.unoffered(T.flat(text), labels))
                row = out["by_surface"].setdefault(surface, {"drawn": 0, "remedy_not_offered": 0})
                row["drawn"] += 1
                if missing:
                    out["remedy_not_offered"] += 1
                    row["remedy_not_offered"] += 1
                    if len(out["examples"]) < 6:
                        out["examples"].append({"hold": kind, "surface": surface, "names": missing,
                                                "buttons": [T.bare(x) for x in labels]})
                if surface != "review Summary" and T.fact_of(hold[1])[:40] not in T.flat(text):
                    out["what_was_found_lost"] += 1
        finally:
            mp.undo()
    return out


def voice_hold() -> dict:
    out = {}
    for resumable in (True, False):
        mp = MonkeyPatch()
        try:
            w = a_world(f"voice_{resumable}", mp)
            import main
            from src.audio_engine import NoLicensedVoice
            mp.setattr("src.resume_points.worth_resuming", lambda *a, r=resumable, **k: r)
            mp.setattr(main, "ping_healthcheck", lambda *a, **k: None)
            T.a_run(w, "run_voice", T.report_of([]))
            w.c._hold_for_voice("run_voice", w.db.get_run("run_voice")["topic_id"], "Fixture",
                                NoLicensedVoice("No commercially licensed voice is available: set up ElevenLabs."))
            text, labels = T.item_page(w.db, "fail:run_voice")
            out["resumable" if resumable else "not_resumable"] = {
                "buttons": [T.bare(x) for x in labels], "names_not_offered": sorted(T.unoffered(T.flat(text), labels))}
        finally:
            mp.undo()
    return out


def opening_said_twice() -> dict:
    mp = MonkeyPatch()
    try:
        w = a_world("opening", mp)
        holds = [("licence_credits", T.credit_holds(TMP / "opening_holds")["fit"])]
        surfaces = {**T.booked_surfaces(w, "run_once", holds), **T.failed_surfaces(w, "run_once_f", holds)}
        return {surface: len(T.repeated_lines(text)) for surface, (text, _) in surfaces.items()}
    finally:
        mp.undo()


SUBJECT = re.compile(r"(?:photo|music) [^“;]*“[^”]*”[^;]*? by [^;]*?(?=;| and more|\. They|\. Shorten|\. The text| lacks)")


def alike(runs: list[dict], folder: Path) -> dict:
    out = {"runs": len(runs), "reasons": 0, "credits_named": 0, "reasons_with_two_alike": 0, "named_by_sheet_number": 0}
    for n, run in enumerate(runs):
        ledger = T._staged(folder / str(n), run)
        payload = copy.deepcopy(run["payload"])
        texts, limits = publishable.payload_credit_texts(payload, publishable.is_documentary(payload))
        required = credits.required_credits(json.loads(ledger.read_text(encoding="utf-8")), ledger.parent)
        # as stored, and (every credit named) with none of them in the caption yet
        for problems in (credits.credit_reach_problems(texts, required, limits),
                         credits.credit_reach_problems({"Reel caption": ""}, required, {"Reel caption": 99999})):
            for p in problems:
                named = SUBJECT.findall(p)
                out["reasons"] += 1
                out["credits_named"] += len(named)
                out["named_by_sheet_number"] += sum(bool(re.match(r"photo \d+, ", s)) for s in named)
                out["reasons_with_two_alike"] += len(set(named)) < len(named)
    return out


def real_runs(folder: Path) -> list[dict]:
    out = []
    for run in sorted(folder.glob("run_*")):
        try:
            ledger = json.loads((run / "provenance.json").read_text(encoding="utf-8"))
            payload = json.loads((run / "script_metadata.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        music = []
        bed = (ledger.get("ambient_bed") or {}).get("path")
        if bed:
            try:
                name = str(bed).replace("\\", "/").rsplit("/", 1)[-1] + ".rights.json"
                music = [s for s in json.loads((run / name).read_text(encoding="utf-8")).get("sources") or []
                         if isinstance(s, dict) and s.get("title")]
            except (OSError, ValueError):
                music = []
        out.append({"run": run.name, "payload": payload, "assets": ledger.get("assets") or [], "music": music})
    return out


def real_holds(runs: list[dict], folder: Path, label: str, renders: list[str]) -> dict:
    """The real holds of `runs` (their stored copy through the release gate's credit check), each kind's longest,
    drawn whole (every reason of the run) on every surface; counted, and their texts added to `renders`."""
    kinds: dict[str, tuple[str, list[str]]] = {}
    for n, run in enumerate(runs):
        ledger = T._staged(folder / str(n), run)
        payload = copy.deepcopy(run["payload"])
        texts, limits = publishable.payload_credit_texts(payload, publishable.is_documentary(payload))
        problems = credits.ledger_credit_problems(ledger, texts, limits)
        if not problems:
            continue
        joined = " ".join(problems)
        kind = ("over" if re.search(r"characters over|cut at the platform|the platform takes", joined) else
                "lacks" if " lacks " in joined else "fit" if "They fit, and nothing" in joined else
                "written_before" if "written before" in joined else "other")
        if kind not in kinds or len(joined) > len(" ".join(kinds[kind][1])):
            kinds[kind] = (run["run"], problems)
        if run["run"] == T.panel.FIXTURE.name:   # the run whose review panel can be drawn from its own record
            kinds["review fixture run"] = (run["run"], problems)
    out = {"held_runs_by_kind": {}, "surfaces_drawn": 0, "remedy_not_offered": 0, "sentences_said_twice": 0,
           "longest_item_page": 0}
    titles = {r["run"]: str(r["payload"].get("title") or r["run"]) for r in runs}
    for kind, (run, problems) in sorted(kinds.items()):
        out["held_runs_by_kind"][kind] = 1
        mp = MonkeyPatch()
        try:
            w = a_world(f"real_{label}_{kind}", mp)
            hold = [("licence_credits", problems)]
            # each surface drawn for the run the hold is of (its title); the review panel only for the run whose
            # whole record is committed (tests/fixtures/ui02_review_v2), never another run's media under its reason
            drawn = {**T.booked_surfaces(w, run, hold, titles[run]), **T.failed_surfaces(w, run + "_gate", hold, titles[run])}
            if run == T.panel.FIXTURE.name:
                drawn.update(T.review_surfaces(w, mp, run + "_review", hold))
            for surface, (text, labels) in drawn.items():
                out["surfaces_drawn"] += 1
                out["remedy_not_offered"] += bool(T.unoffered(T.flat(text), labels))
                out["sentences_said_twice"] += bool(T.repeated_lines(text)) and "item" in surface
                if "item" in surface:
                    out["longest_item_page"] = max(out["longest_item_page"], len(T.notifications.plain(text)))
                fence = "`" * 3
                renders.append(f"### {label} · {kind} · {surface}\n\nButtons: {' / '.join(T.bare(x) for x in labels)}"
                               f"\n\n{fence}\n{T.notifications.plain(text).strip()}\n{fence}\n")
        finally:
            mp.undo()
    return out


def motion_clip_without_ledger() -> dict:
    from src import ai_label
    run = TMP / "run_motion"
    run.mkdir()
    scene = str(run / "motion" / "motion_01.mp4")
    why = ai_label._ledger_scene(run, scene, "abc", {"problem": "the run has none", "data": {}, "hashes": {},
                                                      "root": None})[1]
    return {"reason": why, "says_in_provenance_json": "in provenance.json" in why,
            "names_what_is_missing": "has none" in why}


def main() -> None:
    result = {
        "every_hold_on_every_surface": surfaces_of_every_hold(),
        "voice_hold_item": voice_hold(),
        "sentences_said_twice": opening_said_twice(),
        "credits_alike_dev_fixture": alike(json.loads(DEV_RUNS.read_text(encoding="utf-8"))["runs"], TMP / "dev"),
        "motion_clip_without_ledger": motion_clip_without_ledger(),
    }
    renders: list[str] = []
    dev = json.loads(DEV_RUNS.read_text(encoding="utf-8"))["runs"]
    result["dev_fixture_real_holds"] = real_holds(dev, TMP / "dev_holds", "development", renders)
    folders = [Path(f) for f in os.environ.get("RR_REAL_OUTPUT", "").split(";") if f]
    assets = [Path(f) for f in os.environ.get("RR_ASSETS", "").split(";") if f]
    for n, folder in enumerate(folders):
        if n < len(assets):
            config.ASSETS_DIR = assets[n]
        runs = real_runs(folder)
        result[f"credits_alike_real_{n + 1}"] = alike(runs, TMP / f"real{n}")
        result[f"real_holds_{n + 1}"] = real_holds(runs, TMP / f"real_holds{n}", f"real {n + 1}", renders)
    if os.environ.get("RR_RENDERS"):
        Path(os.environ["RR_RENDERS"]).write_text("\n".join(renders), encoding="utf-8", newline="\n")
    print(json.dumps(result, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
