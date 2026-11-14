"""Operator decisions A, B and C (2026-11-03): signed fact-check records (src/record_signing.py), held quote cards,
and the round-3 visual fixes of the design cards. The checkers are mocked; every record is signed under the test
FACT_RECORD_KEY that tests/conftest.py sets, unless a test sets another."""
import copy
import json
import logging
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src import card_fields as cf
from src import config, record_signing
from src import design_cards as dc
from src.fact_evidence import (
    check_record,
    checked_fact_review,
    checker_run,
    current_fact_review,
    fact_review_problem,
    public_fingerprint,
    record_fact_review,
    require_signing_key,
)
from src.models import CheckRun

MODELS = {"trace_check": "mock-trace", "evidence_check": "mock-evidence"}


def ev(q, t=1, url="https://www.nasa.gov/x"):
    return {"url": url, "tier": t, "quote": q}


def fact(fid, st, evs=None, **kw):
    return {"id": fid, "statement": st, "kind": "other", "disputed": kw.pop("disputed", False),
            "hedge": kw.pop("hedge", ""), "evidence": evs or [ev(st)], **kw}


def sheet0():
    return {"topic": "The Richat Structure", "facts": [
        fact("len", "The ring is about 40 km across"),
        fact("ero", "The ring formed through erosion, not by aliens",
             [ev("The ring formed through erosion; aliens did not build it", 1, "https://www.britannica.com/r")]),
        fact("orb", "Astronauts see it from orbit"),
        fact("w", "Paid crews built the pyramid", [ev("Paid crews built the pyramid", 1, "https://www.britannica.com/p")]),
    ]}


def checkers(claims):
    """Mocked trace_check.check / evidence_check: claims maps a text to [(claim, trace verdict, fact id, evidence
    verdict)]."""
    def trace(payload, sheet):
        cl = claims[payload["card_copy"][0]]
        audits = [{"claim": c, "where": "graphic", "fact_ids": [fid], "claim_type": "domain_fact", "verdict": v,
                   "note": ""} for c, v, fid, _ in cl]
        return all(x[1] == "traced" for x in cl), 0.0, audits, "mock"

    def evidence(payload, sheet):
        cl = claims[payload["card_copy"][0]]
        audits = [{"claim": c, "where": "graphic", "claim_type": "domain_fact", "verdict": e,
                   "quote_ids": sorted(str(n) for n in cf.evidence_numbers(sheet, fid)), "note": ""}
                  for c, _, fid, e in cl]
        return ("pass" if all(x[3] in ("supported", "hedged_ok") for x in cl) else "fail"), 0.0, audits, "mock"
    return trace, evidence


def put(sheet, kind, field, claims):
    sheet.setdefault("cards", {"statements": [], "myths": [], "quotes": [], "dropped": []})
    tr, evc = checkers(claims)
    field = dict(field)
    field["check"] = cf.check_field(kind, field, sheet, trace=tr, evidence=evc, models=MODELS)
    sheet["cards"][kind] = [x for x in sheet["cards"][kind] if x["id"] != field["id"]] + [field]
    return field


def statement(sheet, text, fid, sid="S"):
    return put(sheet, "statements", {"id": sid, "text": text, "fact_id": fid},
               {text: [(text, "traced", fid, "supported")]})


def myth_field(sheet, myth, claims, corr="The ring formed through erosion", verdict="false", fid="ero",
               url="https://www.britannica.com/r"):
    return put(sheet, "myths", {"id": "M", "myth": myth, "verdict": verdict, "refuting_fact_id": fid,
                                "correction": corr, "source": {"title": "b", "url": url}},
               {myth: claims, corr: [(corr, "traced", fid, "supported")]})


def forge_statement(sheet, text, fid):
    """The review's forgery: made-up checker runs, every hash recomputed, no checker ever run (cf._seal alone)."""
    fld = {"id": "S", "text": text, "fact_id": fid}
    runs = [CheckRun(checker="trace_check", text=text, model="claude-x", passed=True, summary="", audits=[
                {"claim": text, "where": "graphic", "fact_ids": [fid], "claim_type": "domain_fact",
                 "verdict": "traced", "note": ""}]),
            CheckRun(checker="evidence_check", text=text, model="gem-x", passed=True, summary="", audits=[
                {"claim": text, "where": "graphic", "claim_type": "domain_fact", "verdict": "supported",
                 "quote_ids": [str(n) for n in cf.evidence_numbers(sheet, fid)], "note": ""}])]
    v, parts = cf.derive("statements", fld, runs, sheet)
    fld["check"] = cf._seal("statements", fld, sheet, v, parts, runs, [])
    sheet["cards"] = {"statements": [fld], "myths": [], "quotes": [], "dropped": []}


def gate_payload(tmp_path, sheet, title, signed_runs=True):
    sp = tmp_path / "sheet.json"
    sp.write_text(json.dumps({"facts": sheet["facts"]}), encoding="utf-8")
    p = {"guardrail_passed": True, "title": title}
    pub = public_fingerprint(p)
    t_audits = [{"claim": title, "where": "title", "fact_ids": [], "claim_type": "rhetorical_framing",
                 "verdict": "traced", "note": ""}]
    e_audits = [{"claim": title, "where": "title", "claim_type": "rhetorical_framing", "verdict": "supported",
                 "quote_ids": [], "note": ""}]
    if signed_runs:
        rec = check_record(checker_run("trace_check", pub, "m1", True, t_audits, ""),
                           checker_run("evidence_check", pub, "m2", True, e_audits, ""), pub)
    else:   # the review's forged gate record: check_record handed plain dicts, no checker run
        rec = check_record({"model": "m1", "passed": True, "audits": t_audits, "summary": ""},
                           {"model": "m2", "passed": True, "audits": e_audits, "summary": ""}, pub)
        # record_fact_review refuses to stamp it (2026-11-04); the forger signs the stamp around it by hand
        with pytest.raises(ValueError, match="not signed"):
            record_fact_review(p, sp, check=rec)
        from src.credits import sha256_file
        from src.fact_evidence import STAMP_UNSIGNED, _lf_sha256, review_rules_fingerprint
        p["fact_review"] = record_signing.signed("fact_review", {
            "public_fingerprint": pub, "sheet_path": str(sp), "sheet_sha256": sha256_file(sp),
            "sheet_lf_sha256": _lf_sha256(sp), "review_rules_fingerprint": review_rules_fingerprint(), "check": rec},
            exclude=STAMP_UNSIGNED)
        return p
    record_fact_review(p, sp, check=rec)
    return p


def title_card(sheet, payload, title):
    return dc.plan_chapter_card(dc.ChapterCard(2, 5, title, None, (),
                                               dc.Gated("title", payload["fact_review"]["public_fingerprint"],
                                                        payload)), sheet, 1080, 1920)


# ------------------------------------------------------------------------------------------------ A: signing
def test_valid_card_records_are_accepted_and_carry_signatures():
    s = sheet0()
    f = statement(s, "Astronauts see it from orbit", "orb")
    assert f["check"]["verdict"] == "supported" and len(f["check"]["signature"]) == 64
    assert all(len(r["signature"]) == 64 for r in f["check"]["runs"])
    assert cf.verify_field(s, "statements", "S")["text"] == "Astronauts see it from orbit"
    plan = dc.plan_chapter_card(dc.ChapterCard(3, 5, "Astronauts see it from orbit", None, ("orb",),
                                               dc.CardRef("statements", "S")), s, 1080, 1920)
    assert plan.kind == "chapter"


def test_forged_card_records_are_refused():
    """The review's probe: 'Slaves carved the ring' on a chapter card from a record with made-up runs and
    recomputed hashes. Every hash holds; no signature does."""
    s = sheet0()
    forge_statement(s, "Slaves carved the ring", "ero")
    with pytest.raises(cf.FieldRefused) as e:
        cf.verify_field(s, "statements", "S")
    assert e.value.code == "unsigned_check" and "not signed" in e.value.reason
    with pytest.raises(dc.CardRefused) as e:
        dc.plan_chapter_card(dc.ChapterCard(2, 5, "Slaves carved the ring", None, ("ero",),
                                            dc.CardRef("statements", "S")), s, 1080, 1920)
    assert e.value.code == "unsigned_check"


def test_tampered_card_records_are_refused():
    s = sheet0()
    statement(s, "Astronauts see it from orbit", "orb")
    for mutate in (lambda c: c.update(checked_at="2020-01-01T00:00:00+00:00"),       # the record edited
                   lambda c: c["runs"][0].update(model="another-model"),            # a run edited
                   lambda c: c.update(signature="0" * 64)):                         # a signature made up
        t = copy.deepcopy(s)
        chk = t["cards"]["statements"][0]["check"]
        mutate(chk)
        chk["sha256"] = cf.check_hash(chk)          # the forger recomputes the hash
        with pytest.raises(cf.FieldRefused) as e:
            cf.verify_field(t, "statements", "S")
        assert e.value.code == "unsigned_check", e.value.reason
    # a run's signature moved to another record does not verify there either (the record's own signature covers
    # its runs, so it is caught there first)
    t = copy.deepcopy(s)
    statement(t, "The ring is about 40 km across", "len", sid="S2")
    a, b = t["cards"]["statements"]
    b["check"]["runs"][0]["signature"] = a["check"]["runs"][0]["signature"]
    b["check"]["sha256"] = cf.check_hash(b["check"])
    with pytest.raises(cf.FieldRefused, match="untrusted") as e:
        cf.verify_field(t, "statements", "S2")
    assert e.value.code == "unsigned_check"
    # and a run whose own signature is wrong is refused even under a valid record signature
    from src.record_signing import sign
    t = copy.deepcopy(s)
    chk = t["cards"]["statements"][0]["check"]
    chk["runs"][0]["signature"] = "0" * 64
    chk["signature"] = sign("card_check", cf._card_content("statements", "S", chk))
    chk["sha256"] = cf.check_hash(chk)
    with pytest.raises(cf.FieldRefused, match="a checker run"):
        cf.verify_field(t, "statements", "S")


def test_key_rotation_makes_every_earlier_record_untrusted_until_rechecked(monkeypatch):
    """Rotation, as documented in src/record_signing.py: no earlier key is accepted. A record signed under the old
    key is refused like an unsigned one, and checking the field again under the new key makes it usable."""
    s = sheet0()
    statement(s, "Astronauts see it from orbit", "orb")
    monkeypatch.setattr(config, "FACT_RECORD_KEY", "a-rotated-key-0000000000000000000000")
    with pytest.raises(cf.FieldRefused, match="signed under another key") as e:
        cf.verify_field(s, "statements", "S")
    assert e.value.code == "unsigned_check"
    statement(s, "Astronauts see it from orbit", "orb")          # check_cards / check_field again
    assert cf.verify_field(s, "statements", "S")


def test_no_key_signs_nothing_and_trusts_nothing(monkeypatch, tmp_path, caplog):
    s = sheet0()
    statement(s, "Astronauts see it from orbit", "orb")
    monkeypatch.setattr(config, "FACT_RECORD_KEY", "")
    with caplog.at_level(logging.WARNING):
        with pytest.raises(cf.FieldRefused, match="FACT_RECORD_KEY is not set"):
            cf.verify_field(s, "statements", "S")
        assert record_signing.sign("fact_review", {"a": 1}) == ""
        sheet = tmp_path / "f.json"
        sheet.write_text("{}")
        p = {"guardrail_passed": True, "title": "T"}
        with pytest.raises(ValueError, match="no record of a check run"):   # 2026-11-04: never without one
            record_fact_review(p, sheet)
        assert "fact_review" not in p and not current_fact_review(p)
        assert "FACT_RECORD_KEY is not set" in fact_review_problem(p)
    assert sum("FACT_RECORD_KEY is not set" in r.getMessage() for r in caplog.records) == 1   # logged once


def test_production_without_a_key_refuses_before_any_check(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "FACT_RECORD_KEY", "")
    monkeypatch.setattr(config, "RELIC_ENVIRONMENT", "production")
    with pytest.raises(RuntimeError, match="FACT_RECORD_KEY is not set in production"):
        require_signing_key()
    # the pipeline's fact gate stops there: no checker is called
    import main
    from src import trace_check
    calls = []
    monkeypatch.setattr(trace_check, "check", lambda *a: calls.append(a) or (True, 0, [], ""))
    with pytest.raises(RuntimeError, match="nothing publishes"):
        main.PipelineCoordinator._verify_final_copy(None, {"guardrail_passed": True, "title": "T"}, {"id": "x"})
    assert calls == []
    monkeypatch.setattr(config, "RELIC_ENVIRONMENT", "development")
    require_signing_key()           # development: a warning only (every record stays untrusted)


def test_the_release_gate_refuses_an_unsigned_or_edited_review(tmp_path, checked_stamp):
    from src.release import write_release_report
    sheet = tmp_path / "facts.json"
    sheet.write_text('{"facts": []}')
    p = {"guardrail_passed": True, "title": "T", "narration_script": "Text"}
    checked_stamp(p, sheet)          # after the checkers passed (2026-11-04)
    assert current_fact_review(p) and fact_review_problem(p) == ""

    def facts_check(payload):
        rep = write_release_report({"master_video_path": str(tmp_path / "none.mp4"), "script_payload": payload},
                                   dry_run=True)
        return next(c for c in rep["checks"] if c["name"] == "facts")
    assert facts_check(p)["status"] == "pass"
    old = copy.deepcopy(p)
    old["fact_review"].pop("signature")                       # an old record, written before signing
    assert not current_fact_review(old)
    assert facts_check(old)["status"] == "fail" and "not signed" in facts_check(old)["detail"]
    edited = copy.deepcopy(p)
    edited["fact_review"]["review_rules_fingerprint"] = "79415fa9c90d9a5027f19d13e77f998cf14dfdf848dc59042819e993438c455d"
    assert not current_fact_review(edited)                    # still a valid rules fingerprint, but not signed so
    assert "signature does not match" in facts_check(edited)["detail"]


def test_a_review_re_pointed_at_this_checkouts_copy_stays_signed(tmp_path, checked_stamp):
    """script_library re-points the stamp's sheet_path and sheet_sha256 at this checkout's copy of the same sheet;
    those location fields are not signed, the sheet's LF content is (sheet_lf_sha256)."""
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_bytes(b'{\n "facts": []\n}\n')
    b.write_bytes(b'{\r\n "facts": []\r\n}\r\n')
    p = {"guardrail_passed": True, "title": "T"}
    checked_stamp(p, a)
    from src.credits import sha256_file
    p["fact_review"].update(sheet_path=str(b), sheet_sha256=sha256_file(b))
    assert current_fact_review(p)
    b.write_bytes(b'{"facts": ["other"]}')
    p["fact_review"]["sheet_sha256"] = sha256_file(b)
    assert not current_fact_review(p)


def test_an_unsigned_old_review_is_checked_again_not_a_crash(monkeypatch, tmp_path, checked_stamp):
    """Old unsigned records on disk are untrusted: the fact gate re-runs the checkers and signs the new record."""
    import main
    from src import trace_check
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    (tmp_path / "fact_sheets").mkdir()
    sp = tmp_path / "fact_sheets" / "x.json"
    sp.write_text('{"facts": []}')
    p = {"guardrail_passed": True, "title": "T"}
    checked_stamp(p, sp)
    p["fact_review"].pop("signature")                 # as written before records were signed
    assert not current_fact_review(p)
    calls = []
    audit = [{"claim": "T", "where": "title", "fact_ids": [], "claim_type": "rhetorical_framing", "verdict": "traced",
              "note": ""}]
    monkeypatch.setattr(trace_check, "check", lambda payload, sheet: calls.append("t") or (True, 0, audit, "ok"))
    monkeypatch.setattr(trace_check, "evidence_check",
                        lambda payload, sheet: calls.append("e") or ("pass", 0, [dict(audit[0], verdict="supported",
                                                                                     quote_ids=[])], "ok"))
    main.PipelineCoordinator._verify_final_copy(None, p, {"id": "x"})
    assert calls == ["t", "e"] and current_fact_review(p)
    ok, why = checked_fact_review(p)
    assert ok, why


def test_forged_gate_records_are_refused(tmp_path):
    """The review's probe: a gate record made with check_record and no checker run put 'A METEOR MADE THE RING' on a
    chapter card (the title has no fact ids, so no word check)."""
    s = sheet0()
    good = gate_payload(tmp_path, s, "The Eye of the Sahara")
    assert checked_fact_review(good) == (True, "")
    assert title_card(s, good, "The Eye of the Sahara").kind == "chapter"
    forged = gate_payload(tmp_path, s, "A Meteor Made the Ring", signed_runs=False)
    ok, why = checked_fact_review(forged)
    assert not ok and "not signed" in why
    with pytest.raises(dc.CardRefused) as e:
        title_card(s, forged, "A Meteor Made the Ring")
    assert e.value.code == "unchecked_gate"
    # a signed gate record whose title is edited and every hash recomputed: the stamp no longer verifies
    from src.checkpoints import fingerprint
    t = copy.deepcopy(good)
    t["title"] = "A Meteor Made the Ring"
    t["fact_review"]["public_fingerprint"] = t["fact_review"]["check"]["public_fingerprint"] = public_fingerprint(t)
    rec = t["fact_review"]["check"]
    rec["sha256"] = fingerprint({k: v for k, v in rec.items() if k != "sha256"})
    ok, why = checked_fact_review(t)
    assert not ok and "untrusted" in why


def test_the_key_is_never_logged_or_shown(monkeypatch, caplog):
    from src.ops import SecretMasker
    key = config.FACT_RECORD_KEY
    assert SecretMasker().mask(f"leaked {key} here") == "leaked *** here"
    monkeypatch.setattr(config, "FACT_RECORD_KEY", "x" * 40)
    s = sheet0()
    with caplog.at_level(logging.DEBUG):
        statement(s, "Astronauts see it from orbit", "orb")
        monkeypatch.setattr(config, "FACT_RECORD_KEY", "y" * 40)
        with pytest.raises(cf.FieldRefused) as e:
            cf.verify_field(s, "statements", "S")
    assert "x" * 40 not in e.value.reason and "y" * 40 not in e.value.reason
    assert not any(("x" * 40) in r.getMessage() or ("y" * 40) in r.getMessage() for r in caplog.records)


def test_settings_are_documented():
    example = (Path(__file__).resolve().parent.parent / ".env.example").read_text(encoding="utf-8")
    assert "FACT_RECORD_KEY=\n" in example and "QUOTE_CARDS_ENABLED=false" in example
    assert config.QUOTE_CARDS_ENABLED is False or isinstance(config.QUOTE_CARDS_ENABLED, bool)


# ------------------------------------------------------------------------------------------------ B: quotes held
def test_quote_cards_are_held_by_default(monkeypatch):
    monkeypatch.setattr(config, "QUOTE_CARDS_ENABLED", False)
    for size in ((1080, 1920), (1920, 1080)):
        with pytest.raises(dc.CardRefused) as e:
            dc.plan_quote_card(dc.QuoteCard(quote_src=dc.CardRef("quotes", "Q")), sheet0(), *size)
        assert e.value.reason == "held" and e.value.code == "held"


# ------------------------------------------------------------------------------------------------ the review's myth probes
@pytest.mark.parametrize("myth, claims", [
    # a compound myth the checker treats as one claim: "wide" paraphrases the fact's "across"
    ("The ring is 40 km wide and aliens built it", [("The ring is 40 km wide and aliens built it", "contradicts",
                                                     "ero", "contradicted")]),
    ("Aliens built the 40 km ring", [("Aliens built the 40 km ring", "contradicts", "ero", "contradicted")]),
    # a true statement the checker wrongly calls a myth: the refuting fact's own evidence says it
    ("Aliens did not build the ring", [("x", "contradicts", "ero", "contradicted")]),
])
def test_myths_a_fact_or_its_evidence_states_are_refused(myth, claims):
    s = sheet0()
    f = myth_field(s, myth, claims)
    assert f["check"]["verdict"] == "supported"        # the mocked checker let it through
    with pytest.raises(dc.CardRefused) as e:
        dc.plan_myth_fact(dc.MythFact(myth_src=dc.CardRef("myths", "M")), s, 1080, 1920)
    assert e.value.code == "affirmed_myth"


def test_a_myth_the_evidence_reports_as_a_belief_still_draws():
    s = sheet0()
    s["facts"][1]["evidence"].append(ev("A popular myth says aliens built the ring", 1, "https://www.britannica.com/r"))
    myth_field(s, "Aliens built the ring", [("Aliens built the ring", "contradicts", "ero", "contradicted")])
    assert dc.plan_myth_fact(dc.MythFact(myth_src=dc.CardRef("myths", "M")), s, 1080, 1920).kind == "mythfact"


# ------------------------------------------------------------------------------------------------ C: visuals
def _photo(path, rgb, size=(1300, 2100)):
    arr = np.zeros((size[1], size[0], 3), np.uint8) + np.array(rgb, np.uint8)
    arr = np.clip(arr.astype(int) + np.random.default_rng(3).integers(-6, 6, arr.shape), 0, 255).astype(np.uint8)
    Image.fromarray(arr).save(path, quality=92)
    return path


def test_misleading_myth_has_its_own_label_and_strike():
    s = sheet0()
    myth_field(s, "Aliens built the ring", [("Aliens built the ring", "overstated", "ero", "contradicted")],
               verdict="misleading")
    for size in ((1080, 1920), (1920, 1080)):
        plan = dc.plan_myth_fact(dc.MythFact(myth_src=dc.CardRef("myths", "M")), s, *size)
        assert plan.drawn_text["myth_label"] == "MISLEADING" and plan.meta["anchors"]["strike"] == "dashed"
        strike = next(it for it in plan.meta["_r"]["items"] if it["type"] == "strike")
        assert strike["dash"] and len(dc._dashes(strike["segs"][0][0], strike["segs"][0][1], strike["dash"])) >= 3
    s2 = sheet0()
    myth_field(s2, "Aliens built the ring", [("Aliens built the ring", "contradicts", "ero", "contradicted")])
    plan = dc.plan_myth_fact(dc.MythFact(myth_src=dc.CardRef("myths", "M")), s2, 1080, 1920)
    assert plan.drawn_text["myth_label"] == "MYTH" and plan.meta["anchors"]["strike"] == "solid"


def _long_myth_sheet():
    s = {"topic": "The Richat Structure", "facts": [
        fact("m3", "Water wore down a dome of layered rock into rings over 100 million years")]}
    corr = "Water wore down a dome of layered rock into rings over 100 million years"
    myth_field(s, "A giant meteorite blasted out these desert rings",
               [("A giant meteorite blasted out these desert rings", "contradicts", "m3", "contradicted")],
               corr=corr, fid="m3", url="https://www.nasa.gov/x")
    return s


def test_the_long_9x16_myth_sets_at_84_px_or_more_and_the_ground_below_is_the_photo(tmp_path):
    s = _long_myth_sheet()
    photo = _photo(tmp_path / "cyan.jpg", (190, 232, 240))
    plan = dc.plan_myth_fact(dc.MythFact(photo=photo, myth_src=dc.CardRef("myths", "M")), s, 1080, 1920)
    # design handover 2026-11-04 (deliberate change): the source line is never dropped; the type may go down to the
    # MYTH_FLOOR_SOURCE floor (76 px) to keep it. Was: >= 84 px, met by dropping the source ("no room")
    assert plan.meta["anchors"]["px"] >= dc.MYTH_FLOOR_SOURCE and plan.meta["source_omitted"] is None
    rest = np.asarray(plan.meta["_r"]["rest"]).astype(float)
    low = rest[1500:1900].mean(axis=(0, 1))
    assert low[2] > low[0] + 15 and low.mean() > 90       # the cyan photo's own colour comes back, not slate
    # review 2026-11-03 (round 4; changed deliberately from "one rising curve" under the block, which read as a
    # muddy grey field): the photo below the panel is the photo itself under a light veil, as bright as it is
    veiled = (1 - dc.MYTH_OUT_SHADE) * np.array([190, 232, 240]) + dc.MYTH_OUT_SHADE * np.array(dc.INK)
    assert np.abs(low - veiled).max() < 8, (low, veiled)


def test_the_9x16_myth_is_one_panel_the_size_of_the_graphics_box(tmp_path):
    """Review 2026-11-03 (round 4, blocking): the lower 40% was a dead gradient and the block sat in a dark area
    that faded out. The 9:16 myth is one designed panel, the graphics box plus its air, dark edge to edge with crisp
    top and foot; the block centred in it; the photograph itself above and below."""
    from src.safe_zones import graphics_box
    gb = graphics_box()
    assert dc.MYTH_PANEL == (gb[1] - dc.MYTH_PANEL_AIR, gb[3] + dc.MYTH_PANEL_AIR)
    s = _long_myth_sheet()
    for rgb in ((250, 250, 246), (240, 215, 40), (190, 232, 240), (20, 20, 22)):
        photo = _photo(tmp_path / f"{rgb[0]}_{rgb[1]}.jpg", rgb)
        plan = dc.plan_myth_fact(dc.MythFact(photo=photo, myth_src=dc.CardRef("myths", "M")), s, 1080, 1920)
        rest = np.asarray(plan.meta["_r"]["rest"]).astype(float)
        rows = rest.mean(axis=(1, 2))
        y0, y1 = dc.MYTH_PANEL
        inside, below, above = rows[y0 + 2:y1 - 2], rows[y1 + 2:], rows[:y0 - 2]
        assert inside.max() < 60, (rgb, inside.max())                 # dark from edge to edge, no fade-out
        if sum(rgb) > 300:                                            # a bright photo: crisp edges, itself outside
            assert rows[y1 + 2] - rows[y1 - 2] > 80 and rows[y0 - 2] - rows[y0 + 2] > 80, rgb
            assert np.ptp(below) < 4 and np.ptp(above) < 4, rgb      # an even photo, no shade curve over it
        ink = [e.box for e in plan.elements if e.name.split("_")[0] in ("myth", "fact", "source") and e.box]
        top, foot = min(b[1] for b in ink), max(b[3] for b in ink)
        assert y0 < top and foot < y1                                 # the block inside the panel
        for n, p_ in plan.meta["_contrast"]:
            assert p_.contrast >= dc.PLACE_TARGET, (rgb, n, p_.contrast)


def test_the_16x9_myth_column_is_balanced_against_the_fact():
    s = sheet0()
    corr = "Wind and water carved the rings over millions of years"
    s["facts"].append(fact("m", corr))
    myth_field(s, "A meteor strike made the rings",
               [("A meteor strike made the rings", "contradicts", "m", "contradicted")], corr=corr, fid="m",
               url="https://www.nasa.gov/x")
    plan = dc.plan_myth_fact(dc.MythFact(myth_src=dc.CardRef("myths", "M")), s, 1920, 1080)
    els = {e.name: e for e in plan.elements}
    myth = [e.box for n, e in els.items() if n.startswith("myth_") and n[5:].isdigit()]
    fact_ = [e.box for n, e in els.items() if n.startswith("fact_") and n[5:].isdigit()]
    m_top, m_bot = min(b[1] for b in myth), max(b[3] for b in myth)
    f_top, f_bot = min(b[1] for b in fact_), max(b[3] for b in fact_)
    # the myth column is no small lump at the top: its centre sits near the fact column's centre
    assert abs((m_top + m_bot) / 2 - (f_top + f_bot) / 2) < 0.15 * (f_bot - f_top)
    assert plan.meta["anchors"]["myth_px"] > dc.myth_px(plan.meta["anchors"]["px"], True)   # grown to fill


def test_the_16x9_chapter_title_fills_the_right_third(tmp_path):
    s = sheet0()
    p = gate_payload(tmp_path, s, "The Eye of the Sahara")
    plan = dc.plan_chapter_card(dc.ChapterCard(2, 5, "The Eye of the Sahara", None, (),
                                               dc.Gated("title", p["fact_review"]["public_fingerprint"], p)),
                                s, 1920, 1080)
    right = max(e.box[2] for e in plan.elements if e.name.startswith("title_"))
    assert right >= 1920 * 2 / 3 + 200 and plan.meta["anchors"]["title_px"] >= 120


def test_the_bright_hook_panel_is_evenly_padded_centred_and_has_no_foot_rule(tmp_path):
    """Review 2026-11-03 (round 4, blocking): 20 px above the masthead against 57 px under the claim, a GOLD foot
    rule lost on yellow, and a centred masthead over a flush-left claim. The panel now has HOOK_PLATE_AIR on both
    sides of its ink, the claim is centred under the masthead, and nothing GOLD runs along its foot."""
    s = sheet0()
    statement(s, "Astronauts see it from orbit", "orb")
    for rgb in ((250, 225, 60), (240, 240, 240), (190, 232, 240)):
        photo = _photo(tmp_path / f"{rgb[0]}.jpg", rgb)
        plan = dc.plan_hook_frame(dc.HookFrame("Astronauts see it from orbit", ("orb",), photo, "",
                                               claim_src=dc.CardRef("statements", "S")), s)
        y0, y1 = plan.meta["scrim"]["plate"]
        els = {e.name: e for e in plan.elements}
        titles = [e.box for n, e in els.items() if n.startswith("title_")]
        assert els["brand"].box[1] - y0 == dc.HOOK_PLATE_AIR, rgb
        assert abs((y1 - max(b[3] for b in titles)) - dc.HOOK_PLATE_AIR) <= 1, rgb
        for b in titles:                                              # centred on the masthead's axis
            assert abs((b[0] + b[2]) / 2 - 540) <= 3, (rgb, b)
        assert "rule" not in plan.meta["scrim"]
        f0 = np.asarray(plan.meta["_ground0"]).astype(int)
        foot = f0[y1 - 8:y1 - 1].reshape(-1, 3)
        assert (foot.max(axis=1) < 60).all(), rgb                      # the panel's foot is INK, no GOLD band


def test_a_bright_hook_has_one_plate_from_the_masthead_to_the_claim(tmp_path):
    s = sheet0()
    statement(s, "Astronauts see it from orbit", "orb")
    for rgb in ((250, 225, 60), (240, 240, 240), (190, 232, 240)):
        photo = _photo(tmp_path / f"{rgb[0]}.jpg", rgb)
        plan = dc.plan_hook_frame(dc.HookFrame("Astronauts see it from orbit", ("orb",), photo, "",
                                               claim_src=dc.CardRef("statements", "S")), s)
        sc, band = plan.meta["scrim"], plan.meta["mast_band"]
        assert sc["mode"] == "plate" and band.get("on_plate") and plan.meta["anchors"]["position"] == "plate"
        y0, y1 = sc["plate"]
        assert y0 <= band["band"][0] and y1 >= plan.meta["anchors"]["L"]       # one plate holds both
        a = np.asarray(plan.meta["_r"]["under"].getchannel("A")).astype(int)[:, 540]
        assert (a[y0 + 2:y1 - 2] > 230).all()                                   # no bright gap inside it
        g0 = np.asarray(plan.meta["_ground0"]).astype(float)[y0 + 20:y1 - 20].mean(axis=(0, 1))
        assert max(g0) - min(g0) < 12, (rgb, g0)                                # neutral INK: never olive


# ------------------------------------------------------------------------------------------------ review round 4
def test_a_dry_run_pass_with_no_checker_is_never_stamped(db, tmp_path, curated_payload, monkeypatch):
    """Review 2026-11-03 (round 4, 1): an unreviewed curated script passes a dry run with no checker at all; that
    pass got a stamp signed under the real key, and the release gate then treated it as checked. It is no longer
    stamped, so the final copy goes through the real checkers; since 2026-11-04 (operator decision) a human-reviewed
    script is not stamped either."""
    import main
    from types import SimpleNamespace
    from src.fact_evidence import unchecked_pass

    class Engine:
        progress = None

        def __init__(self, reviewed):
            self.reviewed = reviewed

        def generate_script(self, topic, revision_notes="", previous=None):
            return dict(curated_payload, human_reviewed=self.reviewed)

        def verify_claims(self, p, topic=None, allow_unreviewed_curated=False):
            return True, p, "Curated script (UNREVIEWED; dry run only)."

        def _load_curated(self, topic_id):
            return None
    data = tmp_path / "data"
    (data / "fact_sheets").mkdir(parents=True)
    tid = db.list_all_topics()[0]["id"]
    (data / "fact_sheets" / f"{tid}.json").write_text('{"facts": []}', encoding="utf-8")
    monkeypatch.setattr(config, "DATA_DIR", data)
    for reviewed in (False, True):
        run_id = f"run_{int(reviewed)}"
        db.create_run(run_id, tid)
        c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
        c.db, c.script_engine = db, Engine(reviewed)
        c._check_cancel = lambda run_id: None
        c.telegram_bot = SimpleNamespace(update_progress_card=lambda *a, **k: None)
        out = c._write_and_check_script({"id": tid, "title": "T"}, run_id, tmp_path, dry_run=True)
        assert out["guardrail_passed"]
        assert unchecked_pass(out) is (not reviewed)
        # operator decision 2026-11-04: a human review is no fact check either, so neither pass is stamped
        assert "fact_review" not in out and not current_fact_review(out)


@pytest.mark.parametrize("weak", ["abcd", "short-key-0123456789", "your_fact_record_key_here_0123456789abcdef",
                                  "x" * 31])
def test_a_weak_or_placeholder_key_counts_as_unset_and_is_masked(monkeypatch, weak, caplog):
    """Review 2026-11-03 (round 4, 2): a key under 32 bytes or an .env.example placeholder was used to sign, and
    one of 4 characters or a "your_" key was printed in clear. Such a key is now never used and always masked."""
    from src.ops import SecretMasker
    monkeypatch.setattr(config, "FACT_RECORD_KEY", weak)
    assert record_signing.sign("fact_review", {"a": 1}) == ""
    why = record_signing.key_problem()
    assert "not set" in why and weak not in why
    assert record_signing.problem("fact_review", {"a": 1, "signature": "00"}) == why
    assert weak not in SecretMasker().mask(f"key={weak} end")
    monkeypatch.setattr(config, "RELIC_ENVIRONMENT", "production")
    with pytest.raises(RuntimeError, match="FACT_RECORD_KEY is not set in production") as e:
        require_signing_key()
    assert weak not in str(e.value)
    monkeypatch.setattr(config, "FACT_RECORD_KEY", "k" * record_signing.MIN_KEY_BYTES)
    assert record_signing.key_problem() == "" and record_signing.sign("fact_review", {"a": 1})


def test_production_without_a_key_stops_before_the_writer(monkeypatch):
    """Review 2026-11-03 (round 4, 3): in production with no key the writer and the checkers were still called
    before the key was asked for. The writer's first call now stops first."""
    from src.script_engine import ScriptEngine
    monkeypatch.setattr(config, "FACT_RECORD_KEY", "")
    monkeypatch.setattr(config, "RELIC_ENVIRONMENT", "production")
    engine = ScriptEngine.__new__(ScriptEngine)
    monkeypatch.setattr(ScriptEngine, "_generate_script", lambda *a, **k: pytest.fail("the writer was called"),
                        raising=False)
    with pytest.raises(RuntimeError, match="FACT_RECORD_KEY is not set in production"):
        engine.generate_script({"id": "t", "title": "T"})


def test_old_unsigned_card_fields_are_checked_again_when_the_sheet_is_used(monkeypatch, tmp_path):
    """Review 2026-11-03 (round 4, 4): only fact_sheet.build checked card fields, so an old unsigned record (or one
    signed under an earlier key) was never checked again. fact_sheet.ensure now re-checks such fields on a cached
    sheet and saves it; a field refused for another reason is left as it is; with no key nothing is re-checked."""
    from src import card_fields, fact_sheet
    s = sheet0()
    s["facts"] += [fact(f"x{i}", f"Extra fact number {i}") for i in range(fact_sheet.MIN_VERIFIED_FACTS)]
    s.update(topic_id="t_recheck", built_at="2099-01-01T00:00:00+00:00")
    statement(s, "Astronauts see it from orbit", "orb")
    statement(s, "The ring is about 40 km across", "len", sid="S2")
    old = s["cards"]["statements"][0]["check"]
    old["signature"] = ""                                            # written before records were signed
    old["sha256"] = cf.check_hash(old)
    with pytest.raises(cf.FieldRefused) as e:
        cf.verify_field(s, "statements", "S")
    assert e.value.code == "unsigned_check"
    tr, evc = checkers({"Astronauts see it from orbit": [("Astronauts see it from orbit", "traced", "orb",
                                                         "supported")]})
    real, calls = cf.check_field, []

    def check_field(kind, field, sheet, **kw):
        calls.append(field["id"])
        return real(kind, field, sheet, trace=tr, evidence=evc, models=MODELS)
    monkeypatch.setattr(card_fields, "check_field", check_field)
    key = config.FACT_RECORD_KEY
    monkeypatch.setattr(config, "FACT_RECORD_KEY", "")
    assert card_fields.recheck_untrusted(copy.deepcopy(s)) == 0 and calls == []   # no key: nothing re-checked
    monkeypatch.setattr(config, "FACT_RECORD_KEY", key)
    # through fact_sheet.ensure, on a cached sheet on disk
    monkeypatch.setattr(fact_sheet, "path_for", lambda tid: tmp_path / f"{tid}.json")
    monkeypatch.setattr(fact_sheet, "SHEETS_DIR", tmp_path)
    monkeypatch.setattr("src.script_library.reuses", lambda stage: True)
    fact_sheet.save(s)
    out = fact_sheet.ensure({"id": "t_recheck", "title": "T"})
    assert calls == ["S"]                                            # only the untrusted field
    assert cf.verify_field(out, "statements", "S") and cf.verify_field(out, "statements", "S2")
    on_disk = json.loads((tmp_path / "t_recheck.json").read_text(encoding="utf-8"))
    assert on_disk["cards"]["statements"][0]["check"]["signature"]
    calls.clear()
    fact_sheet.ensure({"id": "t_recheck", "title": "T"})
    assert calls == []                                               # checked once, then trusted
