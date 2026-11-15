"""UI-01h: hold advice names only actions the surface offers; the item page states its opening once; no two credits
in one hold reason read alike; a hold reason names what is missing (QF-65e item 1).

Every hold reason the release gate, QF-126/126b's credit holds and QF-127's voice holds write is read on each surface
it reaches, drawn by the real code: the review panel (UI-01e), a booking held at its slot (its notifications item and
its Details, QF-110), and a run the release gate failed (auto-approve or the CLI: its item and its Details). Each
remedy a text names must be a button that surface offers (or of the review, when the text names the button that
brings it). The vocabulary and the checker are this file's own, not the code's. A fake Bot API keeps the messages;
nothing calls Telegram."""
import copy
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import main
from src import config, credits, notifications, publishable, release
from tests import test_ui01e_review_panel as panel

ROOT = Path(__file__).resolve().parent.parent
DEV_RUNS = ROOT / "tests" / "fixtures" / "qf126b" / "dev_runs.json"

# The remedies a text may name, by the words that name them (matched whatever their case: "re-render" names the
# Re-render button as much as "Re-render" does; UI-01h review 1), and the button labels (without their emoji) that
# offer them.
REMEDIES = {
    "Edit": (r"\bEdit\b", {"Edit text"}),
    "Regenerate": (r"\bRegenerate\b", {"Regenerate"}),
    "re-send": (r"\b(?:[Rr]e-send|Resend)\b", {"Resend for review", "Resend review card"}),
    "Reject": (r"\bReject\b", {"Reject"}),
    "Re-render": (r"\bRe-render\b", {"Re-render"}),
    "Make again": (r"\bMake again\b", {"Make again"}),
    "Make a new version": (r"\b[Mm]ake a new version\b", {"Make a new version"}),
    "cancel the booking": (r"\b[Cc]ancel (?:the )?booking\b", {"Cancel booking"}),
    "redraw": (r"\b[Rr]edraw\b", {"Redraw and post next slot"}),
    "Retry": (r"\bRetry\b", {"Retry", "Retry Reel"}),
    "Back to the queue": (r"\bBack to the queue\b", {"Back to the queue"}),
}
REVIEW_BUTTONS = {"Edit text", "Regenerate", "Reject"}            # the review panel's remedies (its Summary face)
BRINGS_THE_REVIEW = {"Resend review card", "Resend for review", "Make a new version"}


def bare(label):
    return re.sub(r"^[^\w]+", "", " ".join(str(label).split())).strip()


def unoffered(text, labels):
    """The remedies `text` names that no button in `labels` offers. The review's own count only after the text has
    named a button of this surface that brings the review ("Tap Resend review card; then ... Regenerate"): an "Edit"
    said before "Make a new version" is not one the surface offers (UI-01h defect 2)."""
    labels = {bare(x) for x in labels}
    offered = {name for name, (_, carriers) in REMEDIES.items() if labels & carriers}
    via = [text.index(b) for b in BRINGS_THE_REVIEW if b in labels and b in text]
    later = {name for name, (_, carriers) in REMEDIES.items() if REVIEW_BUTTONS & carriers}
    return {name for name, (pattern, _) in REMEDIES.items()
            for m in re.finditer(pattern, text, re.IGNORECASE)
            if name not in offered and not (via and name in later and m.start() > min(via))}


def flat(text):
    return " ".join(notifications.plain(text).split())


def repeated_lines(text):
    lines = [ln.strip(" •") for ln in notifications.plain(text).splitlines() if len(ln.strip(" •")) > 20]
    sentences = [s for ln in lines for s in re.split(r"(?<=[.:])\s+", ln) if len(s) > 30]
    return sorted({s for s in sentences if sentences.count(s) > 1})


# ---------------------------------------------------------------- every hold reason, from its producer

def _staged(tmp_path, run):
    folder = tmp_path / "ledgers" / run["run"]
    folder.mkdir(parents=True)
    ledger = {"assets": run["assets"]}
    if run["music"]:
        ledger["ambient_bed"] = {"path": "ambient_bed.wav"}
        (folder / "ambient_bed.wav.rights.json").write_text(json.dumps({"sources": run["music"]}), encoding="utf-8")
    (folder / "provenance.json").write_text(json.dumps(ledger), encoding="utf-8")
    return folder / "provenance.json"


def credit_holds(tmp_path):
    """QF-126/126b's hold reasons: those the committed development runs give as they are stored (credits that fit
    once written again, credits over the platform's limit, an author the credit leaves out), then the forms they do
    not: copy written before the credits were asked for, a text over its limit, a text cut at its limit."""
    runs = json.loads(DEV_RUNS.read_text(encoding="utf-8"))["runs"]
    out = {}
    for run in runs:
        ledger = _staged(tmp_path, run)
        payload = copy.deepcopy(run["payload"])
        texts, limits = publishable.payload_credit_texts(payload, publishable.is_documentary(payload))
        for p in credits.ledger_credit_problems(ledger, texts, limits):
            kind = ("lacks" if " lacks " in p else "over" if "characters over" in p else
                    "fit" if "They fit, and nothing" in p else "written_before" if "written before" in p else "other")
            out.setdefault(kind, p)
    gumbaz = next(r for r in runs if r["run"].startswith("run_gol_gumbaz"))
    required = credits.required_credits({"assets": gumbaz["assets"]}, None)
    name = "Reel caption (Instagram and Facebook)"
    out.setdefault("written_before", next(p for p in credits.credit_reach_problems({name: "A caption."}, required, {name: 99999})
                                 if "written before" in p))
    long = credits.credit_reach_problems({name: "x " * 1200}, required, {name: 2200})
    out["over_its_limit"] = next(p for p in long if "the platform takes" in p)
    out["cut"] = next(p for p in long if "cut at the platform" in p)
    assert {"lacks", "over", "fit", "written_before"} <= set(out), sorted(out)   # the development runs, as stored
    return out


def gate_holds(tmp_path):
    """Every blocking check of the release gate whose words name a remedy: QF-126's licence credits (each form), the
    credit lines a link or handle breaks (QF-42), a Story frame drawn before Story credits (2026-10-29), captions
    shown faster than they are read (QF-88), and the picture, sound and colour holds a new render puts right."""
    holds = {kind: ("licence_credits", text) for kind, text in credit_holds(tmp_path).items()}
    holds["credit_lines"] = ("credit_lines", ["Credit line has a link: https://example.org",
                                              "Edit the text or Regenerate: either rewrites the credit lines."])
    holds["story_credits"] = ("story_credits", release._plain_story_problem(
        "story_02_reveal.jpg: no layout record of its photo and credit (drawn before 2026-10-29?)"))
    holds["caption_rate"] = ("caption_rate", release._reading_rate_checks({"reading_rate": {
        "max_cps": 17, "timing_defects": [{"at": 12.0, "text": "Fifteen thousand", "shown_cps": 24.0,
                                           "spoken_cps": 18.0}]}})[0]["detail"])
    holds.update(render_holds(tmp_path))
    return holds


def _gate(cls, **state):
    """A picture check of picture_qa as its decode leaves it, with what it found (`state`)."""
    from src import picture_qa
    gate = getattr(picture_qa, cls)(False)
    for key, value in state.items():
        setattr(gate, key, value)
    return gate


def render_holds(tmp_path):
    """The gate's picture, sound and colour holds whose remedy is a new render (QF-10, QF-11, QF-88), each written
    by its producer from what a decode found (UI-01h review 1)."""
    from src import audio_qa, colour_vision, picture_qa
    span = SimpleNamespace(finish=lambda duration: [{"start": 4.0, "end": 5.5}])
    out = {name: (gate.name, gate.check(30.0)[1]) for name, gate in (
        ("black", _gate("Black", runs=span)), ("freeze", _gate("Freeze", runs=span)),
        ("bars", _gate("Bars", runs=span)), ("range", _gate("Range", runs=span)),
        ("flash", _gate("Flash", found={"general": [[4.0, 5.5]], "red": []}, worst={"general": 9, "red": 0})),
        ("flash_held", _gate("Flash", holds={"general": [[4.0, 5.5]], "red": []}, held_worst={"general": 9, "red": 0})))}
    out["dropout"] = ("audio_dropout", audio_qa._dropout([{"start": 10.0, "end": 11.0}], 30.0)[1])
    master = tmp_path / "tags" / "master.mp4"
    master.parent.mkdir(parents=True)
    master.write_bytes(b"offline")
    tags = {"color_space": "bt470bg", "color_primaries": "bt709", "color_transfer": "bt709", "color_range": "tv",
            "pix_fmt": "yuv420p"}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(picture_qa, "_probe", lambda path: (tags, 8, 8, 1.0, [0.0]))
        mp.setattr(picture_qa, "_decode", lambda *a, **k: (1, (8, 8)))
        measured = picture_qa.measure(master)
        colour = next(c for c in measured["checks"] if c["name"] == "picture_colour")
        out["colour_tags"] = ("picture_colour", colour["detail"])
        mp.setattr(colour_vision, "run_check", lambda run_dir, accent: [{"graphic": "graphic_map.mp4", "pairs": [
            {"pass": False, "pair": ["gold", "accent"], "de00": {"deutan": 6.2}, "worst_vision": "deutan"}]}])
        out["graphic_colours"] = ("graphic_colour_vision", release._colour_vision_checks(tmp_path / "tags")[0]["detail"])
    return out


HOLDS = ("fit", "written_before", "lacks", "over", "over_its_limit", "cut", "credit_lines", "story_credits",
         "caption_rate", "black", "freeze", "bars", "range", "flash", "flash_held", "dropout", "colour_tags",
         "graphic_colours")


def report_of(holds):
    return {"schema_version": "1.1", "status": "fail", "dry_run": False,
            "checks": [{"name": "duration", "status": "pass", "detail": "Measured 40.000s; expected 15-90s."}]
            + [{"name": name, "status": "fail", "detail": detail} for name, detail in holds]}


def fact_of(detail):
    """What a reason says was found: its words before the advice (kept on every surface)."""
    text = " ".join(detail) if isinstance(detail, list) else str(detail)
    return " ".join(text.split())[:60]


# ---------------------------------------------------------------- the surfaces, drawn by the real code

@pytest.fixture(autouse=True)
def clean_chat(monkeypatch):
    panel.clean_chat.__wrapped__(monkeypatch)   # the clean chat on, as UI-01e's tests have it


@pytest.fixture
def world(db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "output")
    bot, fake = panel.make_bot(db, monkeypatch)
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.db, c.telegram_bot = db, bot
    c.topic_engine = SimpleNamespace(record_status=lambda *a, **k: None)
    return SimpleNamespace(db=db, bot=bot, fake=fake, c=c, tmp=tmp_path)


def a_run(w, run_id, report, title="Fixture"):
    root = config.OUTPUT_DIR / run_id
    root.mkdir(parents=True)
    (root / "master.mp4").write_bytes(b"offline")
    (root / "release_report.json").write_text(json.dumps(report), encoding="utf-8")
    topic = w.db.list_all_topics()[0]["id"]
    w.db.create_run(run_id, topic)
    w.db.update_run(run_id, "approved", master_video_path=root / "master.mp4",
                    script_payload={"title": title, "caption": "c", "topic_id": topic})
    return root


def item_page(db, key):
    item = max((i for i in notifications.recent(db, limit=200) if i["dedupe_key"] == key), key=lambda i: i["id"])
    notifications.set_view(db, 1, item["id"], 1)
    text, rows = notifications.render(db)
    notifications.set_view(db, 1, None, 1)
    return text, [b["text"] for row in rows for b in row]


def booked_surfaces(w, run_id, holds, title="Fixture"):
    a_run(w, run_id, report_of(holds), title)
    due = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    w.db.add_scheduled_post(run_id, "reel", due, [], status="pending")
    post = w.db.list_scheduled_posts(("pending",), run_id=run_id)[0]
    w.c._block_booking(post, release.ReleaseBlocked("Release blocked: " + "; ".join(n for n, _ in holds)),
                       datetime.now(UTC))
    item = item_page(w.db, f"post-held:{post['id']}")
    from src.posting_view import posting_episode
    text, rows = posting_episode(w.db, run_id)
    return {"booked item": item, "booked Details": (text, [b["text"] for row in rows for b in row])}


def failed_surfaces(w, run_id, holds, title="Fixture"):
    a_run(w, run_id, report_of(holds), title)
    assert w.c._blocked_at_gate(run_id, report_of(holds), False, "auto-approve")
    item = item_page(w.db, f"gate:{run_id}")
    from src.control import history_details
    text, rows = history_details(w.db, w.db.run_key(run_id))
    return {"failed item": item, "failed Details": (text, [b["text"] for row in rows for b in row])}


def review_surfaces(w, monkeypatch, run_id, holds):
    root, payload = panel.a_review(w.db, w.tmp, monkeypatch, run_id, passing=False)
    report = report_of(holds)
    (root / "release_report.json").write_text(json.dumps(report), encoding="utf-8")
    release._ui01e_reports[str(root)] = report
    mid = panel.deliver(w.bot, w.db, root, payload, run_id)
    summary, labels = panel.caption(w.fake, mid), [b for row in panel.labels(w.fake, mid) for b in row]
    key = w.db.run_key(run_id)
    panel.tap(w.bot, f"rvt:{key}:chk", mid)
    pages = [panel.caption(w.fake, mid)]
    for n in range(2, 10):
        if not any(b["callback_data"] == f"rvi:{key}:{n}" for row in panel.rows(w.fake, mid) for b in row):
            break
        panel.tap(w.bot, f"rvi:{key}:{n}", mid)
        pages.append(panel.caption(w.fake, mid))
    return {"review Summary": (summary, labels), "review Checks": ("\n".join(pages), labels)}


def _surfaces(w, monkeypatch, n, holds):
    return {**booked_surfaces(w, f"run_b{n}", holds), **failed_surfaces(w, f"run_f{n}", holds),
            **review_surfaces(w, monkeypatch, f"rv{n}", holds)}


# ---------------------------------------------------------------- acceptance

@pytest.mark.parametrize("kind", HOLDS)
def test_every_hold_reason_names_only_remedies_its_surface_offers_and_says_what_was_found(world, monkeypatch, kind):
    holds = gate_holds(world.tmp)
    assert set(holds) == set(HOLDS)
    hold = holds[kind]
    wrong = []
    for surface, (text, labels) in _surfaces(world, monkeypatch, 0, [hold]).items():
        missing = unoffered(flat(text), labels)
        if missing:
            wrong.append((surface, sorted(missing), labels, flat(text)[-400:]))
        # nothing the operator needs is lost: what was found is said on every surface (the review's Summary lists
        # it among its notes when there is room, Checks in full)
        assert fact_of(hold[1])[:40] in flat(text) or surface == "review Summary", (surface, fact_of(hold[1]))
    assert not wrong, wrong


def test_a_held_booking_says_its_opening_once_and_the_failed_run_too(world):
    holds = [("licence_credits", credit_holds(world.tmp)["fit"])]
    surfaces = {**booked_surfaces(world, "run_once", holds), **failed_surfaces(world, "run_once_f", holds)}
    for surface, (text, _) in surfaces.items():
        assert not repeated_lines(text), (surface, repeated_lines(text))
    item = flat(surfaces["booked item"][0])
    assert item.count("Its Reel was held at its slot because") == 1 and item.count("Not posted") == 1, item
    assert "Make a new version" in item and "required credit(s) are not in it yet" in item


def test_a_long_item_quotes_what_its_opening_does_not_already_say():
    body = "First line of it\nSecond line of it\n" + "• " + "the reason, word after word " * 20
    page = notifications._item_body(body)
    assert flat(page).count("First line of it") == 1 and flat(page).count("Second line of it") == 1
    assert "the reason, word after word" in page and "<blockquote expandable>" in page
    one = "A first line far longer than the two lines a list shows " * 4 + "\nthen more " * 30
    assert "A first line far longer" in page_quote(notifications._item_body(one))   # cut above, whole below


def page_quote(page):
    return page.split("<blockquote expandable>", 1)[1]


def test_no_two_credits_in_one_reason_read_alike_on_the_development_runs(tmp_path):
    runs = json.loads(DEV_RUNS.read_text(encoding="utf-8"))["runs"]
    alike, subjects = [], 0
    for run in runs:
        ledger = _staged(tmp_path, run)
        payload = copy.deepcopy(run["payload"])
        texts, limits = publishable.payload_credit_texts(payload, publishable.is_documentary(payload))
        for p in credits.ledger_credit_problems(ledger, texts, limits):
            named = re.findall(r"(?:photo|music) [^“;]*“[^”]*”[^;]*? by [^;]*?(?=;| and more|\. They|\. Shorten| lacks)", p)
            subjects += len(named)
            if len(set(named)) < len(named):
                alike.append((run["run"], named))
        required = credits.required_credits(json.loads(ledger.read_text(encoding="utf-8")), ledger.parent)
        every = credits.credit_reach_problems({"Reel caption": ""}, required, {"Reel caption": 99999})
        named = re.findall(r"photo [^“;]*“[^”]*”[^;]*? by [^;]*?(?=;| and more|\. They)", " ".join(every))
        if len(set(named)) < len(named):
            alike.append((run["run"], named))
    assert subjects >= 20 and not alike, alike


def test_a_photo_on_its_topics_sheet_is_named_by_its_number_and_titles_cut_alike_are_told_apart(tmp_path, monkeypatch):
    topic = tmp_path / "assets" / "a_topic"
    topic.mkdir(parents=True)
    images = [{"file": f"{n:02d}_commons.jpg", "title": f"File:Interior of a long hall seen from the north gallery {n:02d}.jpg",
               "artist": "Ann Lee", "license": "CC BY 4.0", "tier": "core", "relevance": 10 - n} for n in (1, 2, 3)]
    for im in images:
        (topic / im["file"]).write_bytes(b"jpg")
    (topic / "credits.json").write_text(json.dumps({"reviewed": True, "images": images}), encoding="utf-8")
    monkeypatch.setattr(config, "ASSETS_DIR", tmp_path / "assets")
    records = [{"path": f"/elsewhere/output/run_x/parallax/scene_0{n}.mp4", "title": im["title"], "artist": "Ann Lee",
                "license": "CC BY 4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
                "source_url": f"https://commons.wikimedia.org/wiki/File:Hall_{n}.jpg",
                # photo 1 as a Windows ledger records it (backslashes), photo 2 as a POSIX one
                "source_paths": [chr(92).join(["", "other", "machine", "assets", "a_topic", im["file"]]) if n == 1
                                 else f"/other/machine/assets/a_topic/{im['file']}"] if n < 3 else
                [f"/elsewhere/output/run_x/beat_images/beat0{n}_1.jpg"]}
               for n, im in zip((1, 2, 3), images)]
    required = credits.required_credits({"assets": records}, None)
    why = " ".join(credits.credit_reach_problems({"Reel caption": "A caption."}, required, {"Reel caption": 9999}))
    sheet = [i["file"] for i in credits.sheet_order(topic, images)]
    assert f"photo {sheet.index('01_commons.jpg') + 1}, “" in why and f"photo {sheet.index('02_commons.jpg') + 1}, “" in why
    named = re.findall(r"photo [^“;]*“[^”]*”", why)
    assert len(named) == 3 and len(set(named)) == 3 and "03.jpg”" in why, named   # the beat image: its own words


def test_a_motion_clip_named_without_a_ledger_says_what_is_missing(tmp_path):
    from src import ai_label
    run = tmp_path / "run_x"
    run.mkdir()
    scene = str(run / "motion" / "motion_01.mp4")
    for problem, data in (("the run has none", {}), ("", {"assets": []})):
        verdict, why = ai_label._ledger_scene(run, scene, "abc", {"problem": problem, "data": data, "hashes": {},
                                                                   "root": None})
        assert verdict == "ai" and "motion_01.mp4" in why and "in provenance.json" not in why, why
    assert "the run has none" in ai_label._ledger_scene(run, scene, "abc", {"problem": "the run has none", "data": {},
                                                                             "hashes": {}, "root": None})[1]
    held = {"problem": "", "data": {"assets": [{"path": scene}]}, "hashes": {}, "root": None}
    assert ai_label._ledger_scene(run, scene, "abc", held)[1] == "AI motion clip (motion_01.mp4, in provenance.json)"


@pytest.mark.parametrize("resumable", [True, False])
def test_the_voice_hold_names_the_way_back_its_item_offers(world, monkeypatch, resumable):
    from src import notifications as n
    from src.audio_engine import NoLicensedVoice
    monkeypatch.setattr("src.resume_points.worth_resuming", lambda *a, **k: resumable)
    monkeypatch.setattr(main, "ping_healthcheck", lambda *a, **k: None)
    a_run(world, "run_voice", report_of([]))
    world.c._hold_for_voice("run_voice", world.db.get_run("run_voice")["topic_id"], "Fixture",
                            NoLicensedVoice("No commercially licensed voice is available: set up ElevenLabs."))
    text, labels = item_page(world.db, "fail:run_voice")
    assert ("Re-render" in labels) is resumable and ("Back to the queue" in labels) is not resumable, labels
    assert not unoffered(flat(text), labels), (flat(text), labels)
    assert n.open_items(world.db)


def test_the_unlicensed_post_names_its_resend_and_then_the_reviews_regenerate(db, tmp_path, monkeypatch):
    from tests.test_qf127_voice_licence_gate import a_master
    publisher, _, envelope = a_master(monkeypatch, db, tmp_path, {"provider": "azure", "licensed": True}, run_id="ui02")
    monkeypatch.setattr(config, "YOUTUBE_SHORTS", True)
    monkeypatch.setattr(release, "require_release", lambda run, dry_run=False: None)
    for name in ("master.mp4", "cover.jpg"):
        (tmp_path / name).write_bytes(b"offline fixture")
    db.update_run("ui02", "approved", master_video_path=tmp_path / "master.mp4", thumbnail_cover_path=tmp_path / "cover.jpg",
                  thumbnail_grid_path=tmp_path / "cover.jpg",
                  script_payload={"title": "Fixture", "caption": "c", "topic_id": "01_topic"})
    monkeypatch.setattr(publisher, "build_envelope", lambda *a, **k: dict(envelope))
    coord = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    coord.db, coord.publisher, sent = db, publisher, []
    coord.telegram_bot = SimpleNamespace(stamp_review_outcome=lambda *a, **k: None)
    coord.topic_engine = SimpleNamespace(record_status=lambda *a, **k: None)
    coord._alert = lambda text, *a, **k: sent.append((text, k))
    assert coord.publish_run("ui02")["status"] == "blocked_unlicensed"
    text, item = sent[0]
    labels = [b["text"] for b in item["buttons"]]
    assert "Regenerate" in text and not unoffered(flat(text), labels), (labels, flat(text))


# Every module whose text becomes a blocking check's detail (release.write_release_report and what it calls).
PRODUCERS = ("src/release.py", "src/credits.py", "src/picture_qa.py", "src/audio_qa.py", "src/video_qa.py",
             "src/colour_vision.py")
# Text in them that is not a check's detail: the answer to "review clear-flash" when the master cannot be cleared.
NOT_A_CHECK = {"so it cannot be cleared: re-render it without that flashing."}


def test_the_advice_table_covers_every_remedy_its_producers_write():
    """Every literal in the gate's producers that names a remedy is one of the advice the table rewrites per
    surface: a new advice written inline would reach a held booking in words it cannot act on."""
    import ast
    try:
        from src import hold_advice
    except ImportError:
        hold_advice = None
    assert hold_advice is not None, "no one table of hold advice"
    found = []
    for name in PRODUCERS:
        tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
        docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                      if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body
                      and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
                    and any(re.search(p, node.value, re.IGNORECASE) for p, _ in REMEDIES.values())
                    and not any(x in node.value for x in NOT_A_CHECK)):
                found.append((name, node.value))
    assert not found, found          # they are hold_advice's constants, rewritten on each surface (for_surface)
    for canonical, phrased in hold_advice.PHRASES.items():
        assert set(phrased) == set(hold_advice.SURFACES), canonical
