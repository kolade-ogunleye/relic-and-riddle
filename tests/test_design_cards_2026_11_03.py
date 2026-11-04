"""Design cards build spec (2026-11-03): the hook frame, chapter card, myth-vs-fact card and quote card
(src/design_cards.py). Layout inside the safe zones, measured contrast, motion timing, fail-closed refusals and
the fact rules. Most tests are pure (plan_*, alpha_at, strike_progress, frame_at); encode tests need ffmpeg."""
import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src import brand
from src import card_fields as cf
from src import design_cards as dc
from src.cover_design import CROP_34
from src.fact_evidence import check_record, public_fingerprint, record_fact_review
from src.graphics import _WideGrid, nontext_contrast
from src.safe_zones import cover_box, graphics_box

ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / "src" / "design_cards.py"
HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")


@pytest.fixture(autouse=True)
def quote_cards_on(monkeypatch):
    """Quote cards are held by default (operator decision B: config.QUOTE_CARDS_ENABLED=false refuses "held", tested
    in tests/test_signed_records_2026_11_03.py); these tests exercise the quote guard behind the hold."""
    from src import config
    monkeypatch.setattr(config, "QUOTE_CARDS_ENABLED", True)


def ev(url, tier, quote, **structured):
    """An evidence entry; `structured` adds the fact sheet's quotation fields (speaker, role, work, year,
    quotations) that the quote card reads instead of the prose (round 4, rule B)."""
    return {"url": url, "tier": tier, "quote": quote, **structured}


def fact(fid, statement, disputed=False, hedge="", evidence=None, kind="number", **extra):
    return {"id": fid, "statement": statement, "kind": kind, "disputed": disputed, "hedge": hedge,
            "evidence": evidence or [ev("https://www.britannica.com/place/ring", 2, statement)], **extra}


def said(text, speaker, **kw):
    return {"text": text, "speaker": speaker, **kw}


QUOTE_EV = 'Theodore Monod wrote: "We have found the ring." Later he said "It is not a crater."'
# review 2026-11-03, blocking 2: a quote's role, work and date are tied to the clause that attributes it, so the
# cited quotations' evidence carries them there (QUOTE_EV's "Later he said" names no role, work or date)
CITED_EV = ('In his Sahara Notebook in 1940, the geologist Theodore Monod wrote: "We have found the ring." '
            'In the same Sahara Notebook in 1940, the geologist Theodore Monod said "It is not a crater."')
WALK_EV = ('Theodore Monod wrote: "We walked for many days across the open sand and at last we saw the great rings '
           'of stone rise out of the desert floor around us."')
LONG_EV = ('Monod said "' + " ".join(["the ring"] * 15 + ["now"]) + '."')
SHEET = {"topic": "The Richat Structure ring", "facts": [
    fact("f_len", "The ring is about 40 km across"),
    fact("f_disp", "The outer wall is 12 metres high", True, "between 10 and 15 metres"),
    fact("f_disp1", "The basin is 45 km wide", True, "almost 50 km"),
    fact("f_myth", "The ring was not built by aliens; it formed through erosion",
         evidence=[ev("https://www.britannica.com/x", 2, "It is a popular belief that aliens built the ring")],
         misconception="Aliens built the ring"),
    fact("f_cons", "The ring formed through erosion",
         evidence=[ev("https://www.britannica.com/y", 2, "Aliens are considered to have built the ring")]),
    fact("f_myth3", "The ring formed through erosion",
         evidence=[ev("https://someblog.example/z", 3, "It is a popular belief that aliens built the ring")]),
    fact("f_et", "Wind and water slowly eroded the dome over millions of years into concentric rings",
         evidence=[ev("https://www.nasa.gov/a", 1, "A common myth holds that extraterrestrials constructed the "
                                                   "circular rings")],
         misconception="Extraterrestrials constructed the circular rings"),
    fact("f_quote", "Geologist Theodore Monod described the ring in his Sahara Notebook in 1940",
         evidence=[ev("https://www.nasa.gov/monod", 1, CITED_EV, quotations=[
             said("We have found the ring.", "Theodore Monod", role="Geologist", work="Sahara Notebook", year=1940),
             said("It is not a crater.", "Theodore Monod", role="Geologist", work="Sahara Notebook", year=1940)])]),
    fact("f_quote_pron", "Theodore Monod described the ring", evidence=[ev("https://www.nasa.gov/pron", 1, QUOTE_EV)]),
    fact("f_quote3", "Theodore Monod described the ring", evidence=[ev("https://blog.example/m", 3, QUOTE_EV, quotations=[
        said("It is not a crater.", "Theodore Monod")])]),
    fact("f_quote_disp", "Theodore Monod described the ring", True, "perhaps",
         evidence=[ev("https://www.nasa.gov/m", 1, QUOTE_EV, quotations=[said("It is not a crater.", "Theodore Monod")])]),
    fact("f_walk", "Theodore Monod crossed the desert to reach the rings",
         evidence=[ev("https://www.nasa.gov/walk", 1, WALK_EV, speaker="Theodore Monod")]),
    fact("f_long_q", "Monod repeated himself", evidence=[ev("https://www.nasa.gov/l", 1, LONG_EV, speaker="Monod")]),
    fact("f_glyph", "Monod wrote a sign", evidence=[ev("https://www.nasa.gov/g", 1, 'Monod wrote: "We found ༀ here."',
                                                       speaker="Monod")]),
    fact("f_neg", "The walls were built without mortar"),
    fact("f_2000", "The stair has about 2000 steps"),
    fact("f_4000", "The stair has 4,000 steps"),
    fact("f_geo", "Concentric sandstone quartzite ridges form the ring; erosion formed the ring basins"),
    fact("f_long", "Incomprehensibilities of the ring"),
    # round 1 review: a marker that does not govern the myth's words, magnitudes, counts, attribution
    fact("f_inv", "Contrary to legend, the ring formed through erosion",
         evidence=[ev("https://www.britannica.com/i", 2, "Contrary to legend, the ring formed through erosion")]),
    fact("f_contr", "The ring formed through erosion",
         evidence=[ev("https://www.britannica.com/c", 2, "Contrary to the legend that aliens built the ring, it formed "
                                                         "through erosion")], misconception="Aliens built the ring"),
    fact("f_negmyth", "The ring formed through erosion",
         evidence=[ev("https://www.britannica.com/n", 2, "It is a myth that aliens did not build the ring")]),
    fact("f_post", "The ring formed through erosion",
         evidence=[ev("https://www.britannica.com/p", 2, "The claim that aliens built the ring has been debunked")]),
    fact("f_t", "The ring has 12 concentric ridges and the oldest rock is 600 million years old"),
    fact("f_twelve", "Twelve concentric ridges make up the ring"),
    fact("f_stone", "Theodore Monod described the ring",
         evidence=[ev("https://www.nasa.gov/s", 1, 'Monod wrote: "We crossed the stone desert at dawn."',
                      speaker="Monod")]),
]}


def _photo(path, kind, size=(1400, 1800)):
    w, h = size
    rng = np.random.default_rng(7)
    if kind == "warm":
        y = np.linspace(0, 1, h)[:, None, None]
        arr = np.concatenate([np.full((h, w, 1), 235.0), 170 + 40 * y + np.zeros((h, w, 1)),
                              80 + 30 * y + np.zeros((h, w, 1))], axis=2)
        arr += rng.normal(0, 12, (h, w, 3))
    elif kind == "sky":
        arr = np.full((h, w, 3), 252.0)
        arr[int(h * 0.7):] = 150.0
    elif kind == "noise":
        arr = rng.normal(128, 45, (h, w, 3))
    elif kind == "busy":     # hard-edged high-contrast texture (a checker of bright and dark)
        yy, xx = np.mgrid[0:h, 0:w]
        arr = 128 + 110 * np.sign(np.sin(xx / 9.0) * np.sin(yy / 13.0))[..., None] + rng.normal(0, 20, (h, w, 3))
    elif kind == "smooth":   # a dark, grain-free gradient (JPEG keeps it within a level or two)
        y = np.linspace(0, 1, h)[:, None, None]
        arr = 40 + 60 * y + np.zeros((h, w, 3))
    else:   # dark
        arr = rng.normal(30, 10, (h, w, 3))
    Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB").save(path, quality=92)
    return path


@pytest.fixture(scope="module")
def photos(tmp_path_factory):
    d = tmp_path_factory.mktemp("photos")
    return {k: _photo(d / f"{k}.jpg", k) for k in ("warm", "sky", "noise", "dark", "smooth", "busy")} | {
        "small": _photo(d / "small.jpg", "noise", (400, 600))}


# ---- line provenance (operator decision 2026-11-03): cards draw only the fact sheet's checked card fields
# (src/card_fields.py) and, for the hook and chapter cards, the gate-passed title. The spec helpers below put each
# text on the sheet as the card field it would be, and check it through card_fields.check_field with the fact
# checker's model mocked (fake_checkers: audits in the shape trace_check.check and evidence_check return), exactly
# as fact_sheet.build records a verdict; a title gets a payload whose fact review carries the checkers' own record
# (fact_evidence.check_record), as main._verify_final_copy makes it. ground_text still runs on every line as the
# second guard, so the fact-rule tests below keep testing it through the cards.
_GATE_DIR = Path(tempfile.gettempdir()) / "design_cards_gate_2026_11_03"
FAKE_MODELS = {"trace_check": "mock-trace-checker", "evidence_check": "mock-evidence-checker"}


def _sheet_for(ids, sheet=None):
    if sheet is not None:
        return sheet
    for cand in (SHEET, globals().get("PYR"), globals().get("R3")):
        if cand is not None and all(any(f["id"] == i for f in cand["facts"]) for i in ids):
            return cand
    return SHEET


def fake_checkers(verdicts, fid_for):
    """Mocked trace_check.check and evidence_check: `verdicts` maps a text to (trace verdict, evidence verdict)."""
    def trace(payload, sh):
        t = payload["card_copy"][0]
        v = verdicts[t][0]
        a = [{"claim": t, "where": "graphic", "fact_ids": [fid_for[t]], "claim_type": "domain_fact", "verdict": v,
              "note": ""}]
        return v == "traced", 100.0 if v == "traced" else 0.0, a, "mock"

    def evidence(payload, sh):
        t = payload["card_copy"][0]
        v = verdicts[t][1]
        a = [{"claim": t, "where": "graphic", "claim_type": "domain_fact", "verdict": v,
              "quote_ids": sorted(cf.evidence_numbers(sh, fid_for[t])), "note": ""}]
        return ("pass" if v in ("supported", "hedged_ok") else "fail"), 0.0, a, "mock"
    return trace, evidence


def install(sheet, kind, field, good=True, myth_good=True):
    """`field` put on sheet["cards"][kind] with its check record (mocked checker); returns a CardRef to it."""
    cards = sheet.setdefault("cards", {})
    for k in ("statements", "myths", "quotes", "dropped"):
        cards.setdefault(k, [])
    field = {"id": f"{kind[0].upper()}_{hashlib.sha256(json.dumps([field, good, myth_good], sort_keys=True).encode()).hexdigest()[:10]}",
             **field}
    texts = cf.texts(kind, field)
    fid = field.get("refuting_fact_id") or field["fact_id"]
    verdicts = {}
    for part, t in texts.items():
        if kind == "myths" and part == "myth":
            verdicts[t] = ("contradicts", "contradicted") if myth_good else ("traced", "supported")
        else:
            verdicts[t] = ("traced", "supported") if good else ("untraced", "unsupported")
    tr, evc = fake_checkers(verdicts, {t: fid for t in texts.values()})
    field["check"] = cf.check_field(kind, field, sheet, trace=tr, evidence=evc, models=FAKE_MODELS)
    cards[kind] = [x for x in cards[kind] if x["id"] != field["id"]] + [field]
    return dc.CardRef(kind, field["id"])


def statement(text, fact_id, sheet=None, good=True):
    sh = _sheet_for((fact_id,), sheet)
    return install(sh, "statements", {"text": " ".join(text.split()), "fact_id": fact_id}, good=good)


def _gate_payload(sheet, path, text, checked=True):
    blob = json.dumps({"facts": sheet["facts"]}, sort_keys=True)
    _GATE_DIR.mkdir(parents=True, exist_ok=True)
    sp = _GATE_DIR / (hashlib.sha256(blob.encode()).hexdigest()[:20] + ".json")
    if not sp.exists():
        tmp = sp.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(blob, encoding="utf-8")
        os.replace(tmp, sp)
    payload = {"guardrail_passed": True, path: text}
    rec = None
    if checked:
        # the mocked checkers' runs, signed as main._verify_final_copy signs them when the checkers return
        # (fact_evidence.checker_run, under the test FACT_RECORD_KEY)
        from src.fact_evidence import checker_run
        pub = public_fingerprint(payload)
        rec = check_record(
            checker_run("trace_check", pub, "mock-trace-checker", True, [
                {"claim": text, "where": "title", "fact_ids": [], "claim_type": "rhetorical_framing",
                 "verdict": "traced", "note": ""}], "mock"),
            checker_run("evidence_check", pub, "mock-evidence-checker", True, [
                {"claim": text, "where": "title", "claim_type": "rhetorical_framing", "verdict": "supported",
                 "quote_ids": [], "note": ""}], "mock"),
            pub)
        record_fact_review(payload, sp, check=rec)
        return payload
    # unchecked: record_fact_review refuses it (operator decision 2026-11-04), so the stamp is signed by hand, as
    # an old one or an insider's would be, to show the card refusing it too
    with pytest.raises(ValueError, match="no record of a check run"):
        record_fact_review(payload, sp, check=rec)
    from src.credits import sha256_file
    from src.fact_evidence import STAMP_UNSIGNED, _lf_sha256, review_rules_fingerprint
    from src.record_signing import signed
    payload["fact_review"] = signed("fact_review", {
        "public_fingerprint": public_fingerprint(payload), "sheet_path": str(sp), "sheet_sha256": sha256_file(sp),
        "sheet_lf_sha256": _lf_sha256(sp), "review_rules_fingerprint": review_rules_fingerprint()},
        exclude=STAMP_UNSIGNED)
    return payload


def gate(text, sheet=None, path="title", checked=True):
    """A Gated source for `text`: the gate's recorded (and checked) pass over a payload whose `path` holds it."""
    sheet = SHEET if sheet is None else sheet
    payload = _gate_payload(sheet, path, text, checked)
    return dc.Gated(path, payload["fact_review"]["public_fingerprint"], payload)


def span(sheet, fid, text, field="statement", evidence=None):
    """The retired Verbatim source of `text` inside fact `fid`'s `field` (cards now refuse it)."""
    f = dc.fact_by_id(sheet, fid)
    p = f["evidence"][evidence]["quote"] if field == "evidence" else f[field]
    i = p.lower().index(text.lower())
    return dc.Verbatim(fid, i, i + len(text), field, evidence)


def chapter(**kw):
    sheet = kw.pop("sheet", None)
    base = {"number": 2, "total": 5, "title": "The Eye of the Great Sahara"}
    spec = {**base, **kw}
    spec.setdefault("title_src", gate(spec["title"], _sheet_for(spec.get("fact_ids", ()), sheet)))
    return dc.ChapterCard(**spec)


def _source(sh, fid):
    f = dc.fact_by_id(sh, fid)
    e = min(f["evidence"], key=lambda e: e.get("tier", 9))
    return {"title": e["url"], "url": e["url"]}


def myth(**kw):
    """A myth card on a checked myth field: myth_text the myth, fact_text its correction, fact_id the refuting
    fact (myth_fact_id, the retired misconception fact, is only a default for it)."""
    sheet = kw.pop("sheet", None)
    good, myth_good = kw.pop("good", True), kw.pop("myth_good", True)
    verdict = kw.pop("verdict", "false")
    base = {"myth_text": "Aliens built the ring", "fact_text": "The ring formed through erosion", "fact_id": "f_myth"}
    spec = {**base, **kw}
    spec.pop("myth_fact_id", None)
    sh = _sheet_for((spec["fact_id"],), sheet)
    if "myth_src" not in spec:
        spec["myth_src"] = install(sh, "myths", {
            "myth": " ".join(spec["myth_text"].split()), "verdict": verdict, "refuting_fact_id": spec["fact_id"],
            "correction": " ".join(spec["fact_text"].split()), "source": _source(sh, spec["fact_id"])},
            good=good, myth_good=myth_good)
    return dc.MythFact(**spec)


def quote(**kw):
    """A quote card on a checked quote field built from these arguments (the evidence is the fact's evidence
    `evidence_index`, its URL the source)."""
    sheet = kw.pop("sheet", None)
    good = kw.pop("good", True)
    base = {"text": "It is not a crater.", "speaker": "Theodore Monod", "fact_id": "f_quote", "evidence_index": 0,
            "role": "Geologist", "work": "Sahara Notebook", "year": 1940}
    spec = {**base, **kw}
    sh = _sheet_for((spec["fact_id"],), sheet)
    e = dc.fact_by_id(sh, spec["fact_id"])["evidence"][spec["evidence_index"]]
    year = spec.pop("year")
    approx = spec.pop("approximate", False)
    ref = install(sh, "quotes", {
        "quotation": " ".join(spec["text"].split()), "speaker": {"name": spec["speaker"], "role": spec["role"]},
        "work": spec["work"], "date": (f"c. {year}" if approx else str(year)) if year is not None else None,
        "source_url": e["url"], "evidence": e["quote"], "fact_id": spec["fact_id"]}, good=good)
    return dc.QuoteCard(photo=spec.get("photo"), quote_src=ref)


def hook(photo, **kw):
    sheet = kw.pop("sheet", None)
    good = kw.pop("good", True)
    base = {"claim": "About 40 km across", "fact_ids": ("f_len",), "photo": photo, "first_burst": "In the Sahara sits an eye"}
    spec = {**base, **kw}
    sh = _sheet_for(spec["fact_ids"], sheet)
    spec.setdefault("claim_src", statement(spec["claim"], spec["fact_ids"][0], sh, good=good))
    if spec.get("eyebrow"):
        spec.setdefault("eyebrow_src", gate(spec["eyebrow"], sh))
    return dc.HookFrame(**spec)


def graphic_plans(w, h, photo=None):
    return {"chapter": dc.plan_chapter_card(chapter(photo=photo), SHEET, w, h),
            "mythfact": dc.plan_myth_fact(myth(photo=photo), SHEET, w, h),
            "quote": dc.plan_quote_card(quote(photo=photo), SHEET, w, h)}


def _read_json(p):
    """A card's .contrast.json / .design.json record: UTF-8 by contract (typographic quotes), read as UTF-8 on every
    platform (a bare read_text() takes the locale codec: cp1252 on Windows)."""
    return json.loads(Path(p).read_text(encoding="utf-8"))


def inside(b, z):
    return b[0] >= z[0] and b[1] >= z[1] and b[2] <= z[2] and b[3] <= z[3]


def mad(a, b):
    return float(np.abs(np.asarray(a.convert("RGB"), np.int16) - np.asarray(b.convert("RGB"), np.int16)).mean())


# ------------------------------------------------------------------------------------------------ geometry
@pytest.mark.parametrize("size", [(1080, 1920), (720, 1280)])
def test_boxes_9x16(size, photos):
    w, h = size
    zone = graphics_box(w, h)
    plans = graphic_plans(w, h, photos["noise"])
    if size == (1080, 1920):
        plans["hook"] = dc.plan_hook_frame(hook(photos["dark"]), SHEET)
    for kind, plan in plans.items():
        for e in plan.elements:
            assert inside(e.box, zone), (kind, e.name, e.box, zone)
            assert e.box[3] <= round(1058 * w / 1080), (kind, e.name)
            assert e.box[0] >= round(100 * w / 1080) - 1 or e.kind == "mark" or kind == "hook", (kind, e.name, e.box)


@pytest.mark.parametrize("size", [(1920, 1080), (1280, 720)])
def test_boxes_16x9(size, photos):
    w, h = size
    g = _WideGrid(w, h)
    safe = (g.x(153) - 0.5, g.y(129) - 0.5, g.x(1766) + 0.5, g.y(820) + 0.5)
    plans = graphic_plans(w, h, photos["noise"])
    for kind, plan in plans.items():
        for e in plan.elements:
            assert inside(e.box, safe), (kind, e.name, e.box, safe)
    m = {e.name: e for e in plans["mythfact"].elements}
    div = m["divider"].box
    for e in plans["mythfact"].elements:
        if e.name.startswith(("myth", "strike")):
            assert e.box[2] < div[0], e.name
        if e.name.startswith(("fact", "source")):
            assert e.box[0] > div[2], e.name


@pytest.mark.parametrize("claim,ids,n", [("The ring", ("f_len",), 1), ("About 40 km across", ("f_len",), 2),
                                         ("Concentric sandstone quartzite", ("f_geo",), 3)])
def test_hook_in_crops(claim, ids, n, photos):
    plan = dc.plan_hook_frame(hook(photos["dark"], claim=claim, fact_ids=ids, eyebrow="The Richat ring"), SHEET)
    assert plan.meta["anchors"]["lines"] == n
    for e in plan.elements:
        for zone in (cover_box(), CROP_34, graphics_box()):
            assert inside(e.box, zone), (claim, e.name, e.box, zone)
        assert e.box[3] < 1098


def test_hook_focus_fallback(photos):
    plan = dc.plan_hook_frame(hook(photos["dark"], focus=(0, 800, 1080, 1100)), SHEET)
    assert plan.meta["anchors"]["block_top"] == 547 and plan.meta["anchors"]["position"] == "top"
    with pytest.raises(dc.CardRefused):
        dc.plan_hook_frame(hook(photos["dark"], focus=(0, 500, 1080, 1050)), SHEET)
    with pytest.raises(dc.CardRefused):
        dc.plan_hook_frame(hook(photos["dark"], focus=(0, 800, 1080, 1100), photo_kind="generated"), SHEET)


def test_exact_anchors():
    p = dc.plan_chapter_card(chapter(), SHEET)
    a = p.meta["anchors"]
    # review 2026-11-03 (second round, 3): the 9:16 chapter title runs to 136 px (it was 104, R 400, Bn 740,
    # baselines [885, 987], kicker rule at y 400)
    assert (a["title_px"], a["R"], a["Bn"], a["title_baselines"]) == (136, 371, 711, [884, 1017])
    els = {e.name: e for e in p.elements}
    assert els["kicker_rule"].box == (100, 371, 144, 375)
    p = dc.plan_chapter_card(chapter(), SHEET, 1920, 1080)
    els = {e.name: e for e in p.elements}
    last = max(n for n in els if n.startswith("title_"))
    assert abs(els[last].xy[1] - els["numeral"].xy[1]) <= 1
    # operator decision C (2026-11-03, deliberate change): the 16:9 title fills its measure ("THE EYE OF THE /
    # GREAT SAHARA" at 176 px, no empty right third), so the taller block centres lower (Bn was 630)
    assert p.meta["anchors"]["Bn"] == 676 and p.meta["anchors"]["title_px"] == 176
    p = dc.plan_myth_fact(myth(myth_text="Extraterrestrials constructed the circular rings", myth_fact_id="f_et",
                               fact_text="Wind and water slowly eroded the dome over millions of years into "
                                         "concentric rings", fact_id="f_et"), SHEET)
    # operator decision C (2026-11-03, deliberate change): the 9:16 myth is set at MYTH_RATIO 0.86 of the fact, so it
    # takes 2 lines (it was 3) and the source line fits again
    assert len(p.meta["anchors"]["myth_baselines"]) == 2 and len(p.meta["anchors"]["fact_baselines"]) == 4
    # round 4 (F): Anton display type for both statements, one size, block centred on 694 (round 4 review 2: the
    # zone opens to y 300 and the measure to 788, so this length sets at 68, not 64)
    # review 2026-11-03 (5): the block is composed for the whole graphics box (its top 288, the FACT group set down
    # by up to MYTH_SPREAD) and no smaller size is taken to save a line, so this length sets at 72, not 68
    assert p.meta["anchors"]["R"] == 288 and p.meta["anchors"]["fact_baselines"][-1] == 974   # was 1046
    assert p.meta["anchors"]["px"] == 72
    assert p.meta["source_omitted"] is None                                                   # was "no room"
    p = dc.plan_quote_card(quote(text=WALK_EV.split('"')[1], fact_id="f_walk", role="", work="", year=None),
                           SHEET, 1920, 1080)
    # review 2026-11-03 (4): no 25 % unwrap shrink and a fifth 16:9 line, so this quote sets at 72 px in 5 lines
    # (it was 56 px in 4)
    # review 2026-11-03 (second round, 3): the 16:9 column runs to title-safe's right, so the same 72 px sets in 4
    # lines (it was [259, 354, 449, 544, 639], M 163)
    assert p.meta["anchors"]["quote_baselines"] == [306, 401, 496, 591]
    assert p.meta["anchors"]["M"] == 210 and p.meta["anchors"]["quote_px"] == 72


# ------------------------------------------------------------------------------------------------ type floors
def test_type_floors(photos):
    floors = {False: {"running": 46, "display": 46, "kicker": 40, "data": 30, "masthead": 40},
              True: {"running": 48, "display": 48, "kicker": 28, "data": 22}}
    sets = [(graphic_plans(w, h, photos["dark"]), w, h) for w, h in ((1080, 1920), (720, 1280), (1920, 1080),
                                                                     (1280, 720))]
    sets.append(({"hook": dc.plan_hook_frame(hook(photos["dark"], eyebrow="The Richat ring",
                                                  photo_kind="generated"), SHEET)}, 1080, 1920))
    for plans, w, h in sets:
        wide = w > h
        k = min(w / 1920, h / 1080) if wide else w / 1080
        for kind, plan in plans.items():
            roles = plan.meta["roles"]
            for e in plan.elements:
                role = roles[e.name]
                if e.kind != "text":
                    continue
                assert e.px >= round(floors[wide][role] * k), (kind, w, e.name, role, e.px)


def test_hook_grid_floor(photos):
    claim = "Erosion formed the ring basins"
    assert len(claim) == 30
    plan = dc.plan_hook_frame(hook(photos["noise"], claim=claim, fact_ids=("f_geo",)), SHEET)
    proof = plan.meta["grid_proof"]
    assert proof["hook_fails"] == []
    assert proof["elements"]["title_1"]["cap_124"] >= 9


# ------------------------------------------------------------------------------------------------ contrast
@pytest.mark.parametrize("kind", ["warm", "sky"])
def test_planned_contrast(kind, photos):
    attempts = [lambda: dc.plan_hook_frame(hook(photos[kind]), SHEET)]
    for size in ((1080, 1920), (1920, 1080)):
        attempts += [lambda s=size: dc.plan_chapter_card(chapter(photo=photos[kind]), SHEET, *s),
                     lambda s=size: dc.plan_myth_fact(myth(photo=photos[kind]), SHEET, *s),
                     lambda s=size: dc.plan_quote_card(quote(photo=photos[kind]), SHEET, *s)]
    for make in attempts:
        try:
            plan = make()
        except dc.CardRefused:
            continue
        target = 5.1 if plan.kind == "hook" else brand.PLACE_TARGET
        for name, p in plan.meta["_contrast"]:
            floor = brand.PLACE_TARGET if name == "brand" else target
            assert p.contrast is not None and p.contrast >= floor, (plan.kind, name, p.contrast)


def test_nontext_marks(photos):
    plans, refused = [], 0
    for kind in ("warm", "noise", "dark"):
        for size in ((1080, 1920), (1920, 1080)):
            for planner, spec in ((dc.plan_chapter_card, chapter), (dc.plan_myth_fact, myth),
                                  (dc.plan_quote_card, quote)):
                try:
                    plans.append(planner(spec(photo=photos[kind]), SHEET, *size))
                except dc.CardRefused as e:      # a mark under 3:1 refuses the card; it never passes below it
                    assert "non-text" in e.reason or "reaches only" in e.reason
                    refused += 1
    assert len(plans) >= 12
    names = set()
    for plan in plans:
        last = dc.frame_at(plan, plan.frames - 1)
        for name, mask in plan.meta["_marks"]:
            v = nontext_contrast(last, mask, ground=plan.meta["_r"]["rest"])
            assert v is not None and v >= 3.0, (plan.kind, name, v)
            names.add(name.split("_")[0])
    assert {"strike", "kicker", "myth", "fact", "divider", "quote", "rule"} <= names


def _frame_count(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                          "stream=nb_read_frames", "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
    return int(out.stdout.strip())


def _check_render(out, plan):
    assert out is not None and out.exists()
    assert out.with_suffix(".jpg").exists()
    c = _read_json(str(out) + ".contrast.json")
    d = _read_json(str(out) + ".design.json")
    assert c["min"] >= 4.5
    assert c["nontext_min"] is None or c["nontext_min"] >= 3.0
    assert d["refused"] is None and d["version"] == "design-cards-1" and d["frames"] == plan.frames
    assert _frame_count(out) == plan.frames
    return c, d


@needs_ffmpeg
def test_render_smoke_chapter(tmp_path, photos):
    out = dc.render_chapter_card(chapter(photo=photos["warm"]), SHEET, tmp_path / "graphic_chapter_01.mp4")
    c, d = _check_render(out, dc.plan_chapter_card(chapter(photo=photos["warm"]), SHEET))
    assert c["nontext_min"] >= 3.0 and d["poster"] == "last"


@needs_ffmpeg
def test_render_smoke_mythfact(tmp_path, photos):
    spec = myth(photo=photos["noise"])
    out = dc.render_myth_fact(spec, SHEET, tmp_path / "graphic_mythfact_01.mp4", 1920, 1080)
    c, d = _check_render(out, dc.plan_myth_fact(spec, SHEET, 1920, 1080))
    assert d["never_first"] is True and d["min_seconds"] == d["seconds"]
    assert c["nontext_min"] >= 3.0


@needs_ffmpeg
def test_render_smoke_quote(tmp_path, photos):
    spec = quote(photo=photos["warm"])
    out = dc.render_quote_card(spec, SHEET, tmp_path / "graphic_quote_01.mp4")
    _check_render(out, dc.plan_quote_card(spec, SHEET))


@needs_ffmpeg
def test_render_smoke_hook(tmp_path, photos):
    spec = hook(photos["noise"], eyebrow="The Richat ring")
    out = dc.render_hook_frame(spec, SHEET, tmp_path)
    assert out == tmp_path / "hook_frame.mp4"
    c, d = _check_render(out, dc.plan_hook_frame(spec, SHEET))
    assert c["frames"] == [0, 32] and c["graded"] is True
    assert d["poster"] == "first" and d["caption_clear_until_s"] == 1.40 and d["grid_proof"]["hook_fails"] == []


# ------------------------------------------------------------------------------------------------ fact rules
def g(text, ids, role="fact"):
    return dc.ground_text(text, SHEET, ids, role=role)


def test_ground_numbers():
    assert "40" in g("About 40 km across", ["f_len"])
    with pytest.raises(dc.CardRefused):
        g("About 40.5 km across", ["f_len"])
    assert g("About 2,000 steps", ["f_2000"]) == "About 2,000 steps"
    with pytest.raises(dc.CardRefused):
        g("About 40 mi across", ["f_len"])
    with pytest.raises(dc.CardRefused):
        g("About 40+ km across", ["f_len"])
    with pytest.raises(dc.CardRefused):
        g("About 40.0 km across", ["f_len"])


def test_ground_limiters():
    with pytest.raises(dc.CardRefused, match="about"):
        g("The ring is 40 km across", ["f_len"])
    with pytest.raises(dc.CardRefused):
        g("The walls were built with mortar", ["f_neg"])
    assert g("The walls were built without mortar", ["f_neg"])


def test_ground_superlative():
    with pytest.raises(dc.CardRefused, match="largest"):
        g("The largest ring", ["f_len"], role="title")
    with pytest.raises(dc.CardRefused):
        g("The only ring about 40 km across", ["f_len"])


def test_disputed(photos):
    assert "10–15" in g("The wall is 10-15 metres high", ["f_disp"])
    assert "10–15" in g("The wall is 10–15 metres high", ["f_disp"])
    with pytest.raises(dc.CardRefused):
        g("The wall is 12 metres high", ["f_disp"])
    assert dc.disputed_span(dc.fact_by_id(SHEET, "f_disp")) == "10–15"
    assert dc.disputed_span(dc.fact_by_id(SHEET, "f_disp1")) is None
    # round 4 (provenance): a disputed figure with no span refuses the hook too; it is no longer withheld into a
    # new line ("Basin wide"), which would be words neither the sheet nor the fact gate ever passed
    for claim in ("Basin 45 km wide", "45 km wide"):
        with pytest.raises(dc.CardRefused) as e:
            dc.plan_hook_frame(hook(photos["dark"], claim=claim, fact_ids=("f_disp1",)), SHEET)
        assert e.value.code == "disputed_no_span", e.value.reason
    with pytest.raises(dc.CardRefused):   # every other card refuses a disputed figure with no span
        dc.plan_myth_fact(myth(fact_text="The basin is 45 km wide", fact_id="f_disp1"), SHEET)


def test_myth_attestation():
    """Operator decision 2026-11-03: the myth is the checked myth field, its verdict the fact checker's (mocked
    here); the old NLP checks only refuse."""
    assert dc.plan_myth_fact(myth(), SHEET).kind == "mythfact"
    for spec in (myth(myth_text="Giants built the ring", myth_good=False),   # the checker did not refute it
                 myth(good=False),                                          # the correction is unsupported
                 myth(fact_id="f_myth3"),                                   # only a tier-3 source refutes it
                 myth(myth_text="Aliens built 2 rings", myth_good=False)):
        with pytest.raises(dc.CardRefused):
            dc.plan_myth_fact(spec, SHEET)
    with pytest.raises(dc.CardRefused):
        dc.plan_myth_fact(myth(fact_text="Aliens built the ring"), SHEET)   # the myth restated


def test_quote_verbatim():
    drawn = dc.plan_quote_card(quote(), SHEET).drawn_text
    assert drawn[max(k for k in drawn if k.startswith("quote_"))].endswith("”")
    assert dc.plan_quote_card(quote(text="We have found the ring."), SHEET)
    bad = [quote(text="It is not a volcano."), quote(text="it is not a crater."), quote(text="It isn't a crater."),
           quote(text="It is … a crater."), quote(text="It is [sic] not a crater."),
           quote(text="Later he said"), quote(text="We have found"), quote(speaker="Gertrude Bell"),
           quote(fact_id="f_quote3", role="", work="", year=None), quote(fact_id="f_quote_disp", role="", work="",
                                                                         year=None),
           quote(work="Daily Telegraph"), quote(year=1925), quote(role="Astronaut")]
    for spec in bad:
        with pytest.raises(dc.CardRefused):
            dc.plan_quote_card(spec, SHEET)


def test_drawable_refuses(photos):
    with pytest.raises(dc.CardRefused):
        dc.plan_hook_frame(hook(photos["dark"], claim="About 40 km across \U0001F30D"), SHEET)
    with pytest.raises(dc.CardRefused):
        dc.plan_myth_fact(myth(fact_text="The ring formed through erosion \U0001F30D"), SHEET)
    with pytest.raises(dc.CardRefused):
        dc.plan_quote_card(quote(text="We found ༀ here.", fact_id="f_glyph", role="", work="", year=None), SHEET)
    plans = list(graphic_plans(1080, 1920).values()) + [dc.plan_hook_frame(hook(photos["dark"]), SHEET)]
    for plan in plans:
        for s in plan.drawn_text.values():
            assert "…" not in s


# ------------------------------------------------------------------------------------------------ timing
def test_hook_frame0(photos):
    plan = dc.plan_hook_frame(hook(photos["noise"], eyebrow="The Richat ring"), SHEET)
    assert plan.frames == 45 and plan.meta["caption_clear_until_s"] == 1.40
    for e in plan.elements:
        assert dc.alpha_at(plan, e.name, 0.0) == 1.0
        assert dc.alpha_at(plan, e.name, 1.09) == 1.0
        assert dc.alpha_at(plan, e.name, 1.40) == 0.0
    bg = plan.meta["_r"]["bg"]
    assert mad(dc.frame_at(plan, 44), dc._pushed_in(bg, 1 + 0.0016 * 44)) <= 1
    assert mad(dc.frame_at(plan, 0), dc.frame_at(plan, 32)) > 0     # the push moves the photo


@needs_ffmpeg
def test_hook_poster_is_frame0(tmp_path, photos):
    spec = hook(photos["smooth"])
    out = dc.render_hook_frame(spec, SHEET, tmp_path)
    assert out is not None
    with Image.open(out.with_suffix(".jpg")) as poster:
        assert mad(poster, dc.frame_at(dc.plan_hook_frame(spec, SHEET), 0)) <= 2


def test_myth_timing():
    plan = dc.plan_myth_fact(myth(), SHEET)
    t_s, t_f, rest_f = plan.meta["t_s"], plan.meta["t_f"], plan.meta["rest_f"]
    assert dc.strike_progress(plan, t_s - 1 / 30) == 0.0
    assert dc.strike_progress(plan, t_s + 0.45) == 1.0
    names = [e.name for e in plan.elements]
    for i in range(plan.frames):
        t = i / 30
        lab = dc.alpha_at(plan, "myth_label", t)
        for n in names:
            if n.startswith("myth_") and n[5:].isdigit():
                assert dc.alpha_at(plan, n, t) <= lab + 1e-9
            if n.startswith(("fact", "source")) and t < t_f:
                assert dc.alpha_at(plan, n, t) == 0.0
        if t >= rest_f:
            assert all(dc.alpha_at(plan, n, t) == 1.0 for n in names)
    assert plan.seconds >= rest_f + 2.0 + 0.40 - 1e-9
    rest_m = plan.meta["rest_m"]
    late = dc.plan_myth_fact(myth(fact_at=4.0), SHEET)
    assert late.meta["t_s"] == pytest.approx(4.0 - 0.45)
    early = dc.plan_myth_fact(myth(fact_at=0.5), SHEET)
    assert early.meta["t_s"] == pytest.approx(rest_m + 1.2)


def test_quote_alpha_invariants():
    plan = dc.plan_quote_card(quote(text=WALK_EV.split('"')[1], fact_id="f_walk", role="", work="", year=None), SHEET,
                              1920, 1080)
    lines = [e.name for e in plan.elements if e.name.startswith("quote_") and e.name[6:].isdigit()]
    assert len(lines) >= 3
    for i in range(plan.frames):
        t = i / 30
        vals = {dc.alpha_at(plan, n, t) for n in lines}
        assert len(vals) == 1
        q = vals.pop()
        assert dc.alpha_at(plan, "speaker", t) >= q
        assert dc.alpha_at(plan, "speaker", t) == dc.alpha_at(plan, "citation", t)
    assert plan.seconds >= 6.0


def test_chapter_timing():
    for size in ((1080, 1920), (1920, 1080)):
        plan = dc.plan_chapter_card(chapter(), SHEET, *size)
        names = [e.name for e in plan.elements]
        assert all(dc.alpha_at(plan, n, 0.09) == 0.0 for n in names)
        assert dc.alpha_at(plan, "kicker_label", 0.55) > 0 and dc.alpha_at(plan, "title_0", 0.55) == 0.0
        assert all(dc.alpha_at(plan, n, 1.20) == 1.0 for n in names)
        assert all(dc.alpha_at(plan, n, plan.seconds) == 1.0 for n in names)
        assert 0 < 44 * dc.alpha_at(plan, "kicker_rule", 0.25) < 44
        assert plan.seconds == (2.5 if size[1] > size[0] else 3.5)
    plan = dc.plan_chapter_card(chapter(), SHEET)
    rule = next(e for e in plan.elements if e.name == "kicker_rule").box
    arr = np.asarray(dc.frame_at(plan, 7))[rule[1] + 1, rule[0]:rule[2] + 4]
    drawn = int((np.abs(arr.astype(int) - np.array(brand.GOLD)).sum(axis=1) < 6).sum())
    assert 0 < drawn < 44


def test_rest_tail(photos):
    for plan in graphic_plans(1080, 1920, photos["noise"]).values():
        last = dc.frame_at(plan, plan.frames - 1).tobytes()
        assert (plan.frames - 12) / 30 >= plan.meta["rest_s"]
        for i in range(plan.frames - 12, plan.frames):
            assert dc.frame_at(plan, i, freeze_drift=True).tobytes() == last, (plan.kind, i)


# ------------------------------------------------------------------------------------------------ refusals
def _refused(path):
    d = json.loads(Path(str(path) + ".design.json").read_text())
    assert d["refused"] and d["version"] == "design-cards-1"
    assert not Path(path).exists() and not Path(path).with_suffix(".jpg").exists()
    return d["refused"]


def test_refusals_write_reason(tmp_path, photos):
    hooks = {
        "no photo": hook(tmp_path / "missing.jpg"),
        "upscale": hook(photos["small"]),
        "fit": hook(photos["dark"], claim="Incomprehensibilities", fact_ids=("f_long",)),
        "words": hook(photos["dark"], claim="The ring is about 40 km across"),
        "burst": hook(photos["dark"], first_burst="This ring is about 40 km across, wide"),
        "run myth": hook(photos["dark"], run_myths=("about 40 KM across",)),
    }
    for name, spec in hooks.items():
        d = tmp_path / name.replace(" ", "_")
        assert dc.render_hook_frame(spec, SHEET, d) is None, name
        reason = _refused(d / "hook_frame.mp4")
        if name == "upscale":
            assert "enlarged" in reason
    with pytest.raises(dc.CardRefused):
        dc.validate_chapters([chapter(number=1, total=2), chapter(number=3, total=2)])
    dc.validate_chapters([chapter(number=1, total=2), chapter(number=2, total=2)])
    for k, spec in enumerate([chapter(number=3, total=2), chapter(number=0), chapter(number=13, total=13),
                              chapter(title="Act 2: The ring"), chapter(title="The 4,000 steps")]):
        out = tmp_path / f"graphic_chapter_{k:02d}.mp4"
        assert dc.render_chapter_card(spec, SHEET, out) is None
        _refused(out)
    assert dc.plan_chapter_card(chapter(title="The 4,000 steps", fact_ids=("f_4000",)), SHEET)
    out = tmp_path / "graphic_mythfact_01.mp4"
    assert dc.render_myth_fact(myth(fact_at=9.0), SHEET, out) is None
    assert "10" in _refused(out)
    out = tmp_path / "graphic_quote_01.mp4"
    text = LONG_EV.split('"')[1]
    assert len(text.split()) == 31
    assert dc.render_quote_card(quote(text=text, speaker="Monod", fact_id="f_long_q", role="", work="", year=None),
                                SHEET, out) is None
    assert "31 words" in _refused(out)


# ------------------------------------------------------------------------------------------------ determinism, safety
def test_determinism(photos):
    for plan in graphic_plans(1080, 1920, photos["warm"]).values():
        idx = [0, plan.frames // 3, plan.frames // 2, plan.frames - 1]
        h1 = [hashlib.sha256(dc.frame_at(plan, i).tobytes()).hexdigest() for i in idx]
        h2 = [hashlib.sha256(dc.frame_at(plan, i).tobytes()).hexdigest() for i in reversed(idx)][::-1]
        assert h1 == h2


def test_output_names(tmp_path, photos):
    with pytest.raises(ValueError):
        dc.render_chapter_card(chapter(), SHEET, tmp_path / "chapter_01.mp4")
    with pytest.raises(ValueError):
        dc.render_quote_card(quote(), SHEET, tmp_path / "graphic_chapter_01.mp4")
    with pytest.raises(ValueError):
        dc.plan_hook_frame(hook(photos["dark"]), SHEET, 1920, 1080)
    with pytest.raises(ValueError):
        dc.plan_chapter_card(chapter(), SHEET, 1000, 1000)
    assert dc.render_hook_frame(hook(tmp_path / "none.jpg"), SHEET, tmp_path) is None
    assert (tmp_path / "hook_frame.mp4.design.json").exists()


def test_no_forbidden_colours():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            assert node.id not in ("TEAL", "CORAL")
        if isinstance(node, ast.Attribute):
            assert node.attr not in ("TEAL", "CORAL")
        if isinstance(node, ast.alias):
            assert node.name not in ("TEAL", "CORAL")
        if isinstance(node, ast.Tuple) and len(node.elts) == 3:
            assert not all(isinstance(e, ast.Constant) and isinstance(e.value, int) for e in node.elts), \
                ast.unparse(node)
    for size in ((1080, 1920), (1920, 1080)):
        for plan in graphic_plans(*size).values():     # flat INK ground: every pixel is the card's own
            for i in (plan.frames // 2, plan.frames - 1):
                arr = np.asarray(dc.frame_at(plan, i)).reshape(-1, 3)
                for c in (brand.TEAL, brand.CORAL):
                    assert not (arr == np.array(c)).all(axis=1).any()


def test_imports_house_only():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    allowed = set(sys.stdlib_module_names) | {"PIL", "numpy"}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                assert a.name.split(".")[0] in allowed, a.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue          # the src package itself
            root = (node.module or "").split(".")[0]
            assert root in allowed or root == "src" or node.module == "__future__", node.module


def test_private_helpers_pinned(tmp_path):
    from src.cover_design import _brand, _provenance, _scrim
    from src.graphics import _ease, _ease_io, _encode, _pushed_in, _twin
    from src.still_layout import Layout
    from src.typography import _FIGURE, _unit, get_font, plan_text
    assert _ease(0.5) == pytest.approx(0.875) and _ease_io(0.5) == pytest.approx(0.5)
    im = Image.new("RGB", (1080, 1920), brand.INK)
    assert _pushed_in(im, 1.05).size == im.size
    p = plan_text(im, (100, 500), "RING", get_font("Anton", 96), brand.BONE, "ls")
    tw = _twin(im, p, brand.STONE)
    assert tw.text == "RING" and tw.fill == brand.STONE
    g = _WideGrid(1280, 720)
    assert g.x(153) == pytest.approx(102) and g.k == pytest.approx(2 / 3)
    lay = Layout("hook_frame", (1080, 1920), graphics_box())
    out = _brand(_scrim(im, (76, 600, 908, 1040), strength=0.6, feather=200), lay)
    assert lay.meta["plates"]["brand"][0].contrast >= brand.PLACE_TARGET and out.size == im.size
    lay.meta["composition"] = {"image_box": [84, 0, 996, 700]}
    _provenance(out, lay)
    assert lay.meta["provenance"]["plate"][3] == 700 - 24   # image_box block_top + 8 -> block_top - 16
    m = _FIGURE.search("about 40 km across")
    assert m and _unit(m.group("unit"))[3] == "km"
    if HAS_FFMPEG:
        path = _encode((Image.new("RGB", (64, 64), brand.INK) for _ in range(2)), tmp_path / "pin.mp4", 2, 64, 64)
        assert path.exists() and path.with_suffix(".jpg").exists()


# ------------------------------------------------------------------------------------------------ round 1 review fixes
def test_myth_is_never_a_fact_the_sheet_states():
    """Review 2026-11-03, blocking 1: a myth any supported fact on the sheet states as true is never struck out
    (the guard asks every fact, not only the myth's and the fact line's), whatever the field's verdict says."""
    sheet = {"topic": SHEET["topic"], "facts": SHEET["facts"] + [
        fact("f_affirm", "Aliens built the ring",
             evidence=[ev("https://www.britannica.com/a", 2, "It is a popular belief that aliens built the ring")])]}
    with pytest.raises(dc.CardRefused, match="states the myth") as e:
        dc.plan_myth_fact(myth(sheet=sheet), sheet)
    assert e.value.code == "affirmed_myth"
    for size in ((1080, 1920), (1920, 1080)):      # "Contrary to legend, <true statement>": a true myth refuses
        with pytest.raises(dc.CardRefused, match="states the myth"):
            dc.plan_myth_fact(myth(myth_text="The ring formed through erosion",
                                   fact_text="The ring was not built by aliens"), SHEET, *size)
    assert dc._affirmed("Aliens built the ring", SHEET) is None     # f_myth negates it: no fact states it


def test_ground_magnitude_words():
    """Review 2 (blocking): "600 years" against "600 million years" changes the figure."""
    for role in ("claim", "fact", "title"):
        with pytest.raises(dc.CardRefused, match="magnitude"):
            g("The rock is 600 years old", ["f_t"], role=role)
        with pytest.raises(dc.CardRefused):
            g("The rock is 600 billion years old", ["f_t"], role=role)
    assert g("The rock is 600 million years old", ["f_t"]) == "The rock is 600 million years old"
    assert g("It has 12 concentric ridges", ["f_t"])
    assert dc._num_mags("10-15 million years, 40 km, between 3 and 4 billion") == [
        ("10", "million"), ("15", "million"), ("40", ""), ("3", "billion"), ("4", "billion")]
    with pytest.raises(dc.CardRefused):
        g("About 40 million km across", ["f_len"])


def test_title_counts_cite_a_fact():
    """Review 12: a spelled-out count is a figure: a title carrying one cites a fact that spells it."""
    with pytest.raises(dc.CardRefused, match="cite a fact"):
        dc.plan_chapter_card(chapter(title="Twelve ridges"), SHEET)
    assert dc.plan_chapter_card(chapter(title="Twelve ridges", fact_ids=("f_twelve",)), SHEET)
    with pytest.raises(dc.CardRefused, match="count"):
        dc.plan_chapter_card(chapter(title="Twelve ridges", fact_ids=("f_t",)), SHEET)   # the fact says "12"


def test_quote_attribution_outside_the_marks():
    """Review 3 (blocking) and 8: the speaker, role and work come from the attribution, as whole words."""
    with pytest.raises(dc.CardRefused, match="attribution"):
        dc.plan_quote_card(quote(text="We crossed the stone desert at dawn.", speaker="Stone Desert", fact_id="f_stone",
                                 role="", work="", year=None), SHEET)
    assert dc.plan_quote_card(quote(text="We crossed the stone desert at dawn.", speaker="Monod", fact_id="f_stone",
                                    role="", work="", year=None), SHEET)
    with pytest.raises(dc.CardRefused):
        dc.plan_quote_card(quote(speaker="Crater"), SHEET)              # a word only inside the quotation
    for bad in (quote(role="Nat"), quote(work="the"), quote(work="Sahara Note"), quote(role="Geo")):
        with pytest.raises(dc.CardRefused):
            dc.plan_quote_card(bad, SHEET)
    for size in ((1080, 1920), (1920, 1080)):         # review 9: the GOLD rule is the dash; the speaker is bare
        plan = dc.plan_quote_card(quote(), SHEET, *size)
        assert plan.drawn_text["speaker"] == "THEODORE MONOD"
        assert not any("—" in v for v in plan.drawn_text.values())


def _gold_cols(arr, y0, y1, x1):
    band = arr[y0:y1, :x1].astype(int)
    hit = (np.abs(band - np.array(brand.GOLD)).sum(axis=2) < 60).any(axis=0)
    return np.nonzero(hit)[0]


@pytest.mark.parametrize("size", [(1920, 1080), (1280, 720)])
def test_chapter_169_numeral_keeps_the_divider_close(size):
    """Review 4 (blocking): the divider sits a fixed gap after the numeral's ink, the title the same gap after it."""
    w, h = size
    k = _WideGrid(w, h).k
    gaps = []
    for n in (1, 4, 7, 12):
        plan = dc.plan_chapter_card(chapter(number=n, total=12), SHEET, w, h)
        els = {e.name: e for e in plan.elements}
        div = els["divider"].box
        arr = np.asarray(dc.frame_at(plan, plan.frames - 1))
        cols = _gold_cols(arr, div[1], div[3], div[0])
        gaps.append(div[0] - (cols.max() + 1))
        assert abs(els["title_0"].box[0] - div[2] - round(dc.CHAPTER_GAP * k)) <= 3, (n, els["title_0"].box, div)
        assert abs(els["kicker_rule"].box[0] - els["title_0"].xy[0]) <= 2 * k + 1
    assert max(gaps) - min(gaps) <= 3, gaps
    assert all(abs(gp - round(dc.CHAPTER_GAP * k)) <= 3 for gp in gaps), gaps


@pytest.mark.parametrize("kind", ["warm", "sky", "busy"])
def test_169_cards_survive_bright_photos(kind, photos):
    """Review 5 (blocking): a GOLD_DIM divider under 3:1 steps up to GOLD (graphics.spine_colour), not a refusal."""
    for planner, spec in ((dc.plan_chapter_card, chapter), (dc.plan_myth_fact, myth)):
        for size in ((1920, 1080), (1280, 720)):
            plan = planner(spec(photo=photos[kind]), SHEET, *size)
            fill = plan.meta["anchors"]["divider_fill"]
            assert fill in (brand.hex_colour(brand.GOLD_DIM), brand.hex_colour(brand.GOLD))
            last = dc.frame_at(plan, plan.frames - 1)
            for name, mask in plan.meta["_marks"]:
                assert nontext_contrast(last, mask, ground=plan.meta["_r"]["rest"]) >= 3.0, (kind, name)
    flat = dc.plan_chapter_card(chapter(), SHEET, 1920, 1080)          # on flat INK the house's GOLD_DIM is kept
    assert flat.meta["anchors"]["divider_fill"] == brand.hex_colour(brand.GOLD_DIM)


@pytest.mark.parametrize("kind", ["warm", "sky", "busy", "noise"])
def test_hook_eyebrow_rule_on_bright_photos(kind, photos):
    """Review 6 (blocking): the eyebrow rule reaches 3:1 on bright, white and busy photos, drawn above the plates
    so the eyebrow's halo never dims it, its scrim starting 56 px above it."""
    plan = dc.plan_hook_frame(hook(photos[kind], claim="Ring wall 10–15 metres high", fact_ids=("f_disp",),
                                   eyebrow="The Richat ring"), SHEET)
    f0 = dc.frame_at(plan, 0)
    (name, mask), = plan.meta["_marks"]
    assert name == "eyebrow_rule"
    assert nontext_contrast(f0, mask, ground=plan.meta["_ground0"]) >= 3.0
    rule = next(e for e in plan.elements if e.name == "eyebrow_rule").box
    px = np.asarray(f0)[rule[1]:rule[3], rule[0]:rule[2]].reshape(-1, 3)
    assert (px == np.array(brand.GOLD)).all()


@pytest.mark.parametrize("size", [(1920, 1080), (1280, 720)])
def test_myth_169_block_is_centred(size, photos):
    """Review 7 (blocking): the two columns are one block centred on y 475, not pinned to y 250."""
    w, h = size
    gr = _WideGrid(w, h)
    for spec in (myth(), myth(myth_text="Extraterrestrials constructed the circular rings", myth_fact_id="f_et",
                              fact_text="Wind and water slowly eroded the dome over millions of years into "
                                        "concentric rings", fact_id="f_et")):
        plan = dc.plan_myth_fact(spec, SHEET, w, h)
        a = plan.meta["anchors"]
        low = max(a["myth_baselines"][-1], a["fact_baselines"][-1] + (56 if not plan.meta["source_omitted"] else 0))
        assert abs((a["R"] + low + 12) / 2 - 475) <= 1, a
        els = {e.name: e for e in plan.elements}
        assert abs(els["divider"].box[1] - gr.y(a["R"])) <= 1 and abs(els["divider"].box[3] - gr.y(low + 16)) <= 1
        ink = [e.box for e in plan.elements]
        top, bottom = min(b[1] for b in ink), max(b[3] for b in ink)
        mid = (top + bottom) / 2
        assert abs(mid - gr.y(475)) <= 30 * gr.k, (top, bottom)


def test_myth_169_fact_limit_is_reachable():
    """Review 10, now the card field's rule: a correction is at most 84 characters (models.CARD_TEXT_MAX), and that
    length sets in the 16:9 column within its five lines; one character more is refused by the field."""
    assert dc.FACT_CHARS_WIDE == 96 and cf.CARD_TEXT_MAX == 84
    words = "The rings formed through erosion of a dome of rocks over a very long time and no aliens built it"
    sheet = {"topic": SHEET["topic"], "facts": SHEET["facts"] + [fact("f_96", words)]}
    t84 = "The rings formed through erosion of a dome of rocks over a very long time, no aliens"
    assert len(t84) == 84, len(t84)
    plan = dc.plan_myth_fact(myth(fact_text=t84, fact_id="f_96", sheet=sheet), sheet, 1920, 1080)
    assert len(plan.meta["anchors"]["fact_baselines"]) <= 5
    with pytest.raises(dc.CardRefused) as e:
        dc.plan_myth_fact(myth(fact_text=t84 + "t", fact_id="f_96", sheet=sheet), sheet, 1920, 1080)
    assert e.value.code == "bad_field", e.value.reason


def test_disclosure_has_air(photos):
    """Review 11: the disclosure plate ends 32 px above the claim block, so it never reads as its eyebrow."""
    for claim, ids in (("About 40 km across", ("f_len",)), ("Ring wall 10–15 metres high", ("f_disp",))):
        plan = dc.plan_hook_frame(hook(photos["sky"], claim=claim, fact_ids=ids, photo_kind="generated"), SHEET)
        prov = next(e for e in plan.elements if e.name == "provenance_plate").box
        assert plan.meta["anchors"]["block_top"] - prov[3] == dc.DISCLOSURE_AIR == 32
        assert prov[1] >= 523


def test_small_glyph_path_is_narrow(monkeypatch):
    """Review 13: the measured fallback runs only for glyphs too thin for plan_text; any other plan_text error
    propagates instead of being swallowed."""
    for size in ((1080, 1920), (1920, 1080)):
        for plan in graphic_plans(*size).values():
            for it in plan.meta["_r"]["items"]:
                assert not any(getattr(p, "_thin", False) for p in it.get("parts", [])), (plan.kind, it["name"])

    def boom(*a, **k):
        raise TypeError("a real bug")
    monkeypatch.setattr(dc, "plan_text", boom)
    with pytest.raises(TypeError, match="a real bug"):
        dc.plan_chapter_card(chapter(), SHEET)


# ------------------------------------------------------------------------------------------------ round 2 review fixes
PYR = {"topic": "The Great Pyramid", "facts": [
    fact("p_steps", "There are 2000 steps and 40 chambers"),
    fact("p_h", "It is 146 metres tall"),
    fact("p_w", "The base is 230 metres on each side"),
    fact("p_blocks", "It contains an estimated 2.3 million stone blocks"),
    fact("p_disp", "Construction took about 20 years", True, "between 10 and 20 years"),
    fact("p_slaves", "The pyramid was not built by slaves; paid labourers built it"),
    fact("p_long", "The pyramid was built with roughly 2.3 million blocks hauled from nearby quarries along a long "
                   "ramp system by large crews of skilled paid workers"),
    fact("p_q", "Petrie surveyed it", evidence=[ev("https://www.nasa.gov/p", 1,
                                                   'Petrie wrote: "The joints are fine. They are very fine." '
                                                   'Smith said "Nothing."', quotations=[
                                                       said("The joints are fine. They are very fine.", "Petrie"),
                                                       said("Nothing.", "Smith")])]),
    fact("p_q2", "A guide saw it", evidence=[ev("https://www.nasa.gov/g", 1, '"We saw it," said the guide, not Petrie.')]),
    # p_q3 carries a (wrong) structured credit on purpose: the prose's negation must still refuse it (second guard)
    fact("p_q3", "Petrie saw it", evidence=[ev("https://www.nasa.gov/n", 1, 'The guide, not Petrie, said: "We saw it."',
                                               speaker="Petrie")]),
    fact("p_q4", "Petrie saw it", evidence=[ev("https://www.nasa.gov/a", 1, '"We saw it," said Petrie.', speaker="Petrie")]),
    fact("p_cite", "Flinders Petrie surveyed the pyramid in 1880", evidence=[ev("https://www.nasa.gov/x", 1,
        'Petrie noted in his survey: "The joints are so fine that a knife blade cannot be inserted between them." '
        'Petrie, the Egyptologist, wrote this in The Pyramids and Temples of Gizeh in 1883.', speaker="Petrie",
        role="the Egyptologist", work="The Pyramids and Temples of Gizeh", year=1883)]),
]}


def gp(text, ids, role="fact"):
    return dc.ground_text(text, PYR, ids, role=role)


@pytest.mark.parametrize("role", ["claim", "fact", "title"])
def test_figure_keeps_its_noun(role):
    """Round 2 review 1 (blocking): a figure may not move onto another noun or dimension."""
    for text, ids in (("There are 2000 chambers", ["p_steps"]), ("There are 40 steps", ["p_steps"]),
                      ("146 metres on each side", ["p_h", "p_w"]), ("230 metres tall", ["p_h", "p_w"])):
        with pytest.raises(dc.CardRefused) as e:
            gp(text, ids, role)
        assert e.value.code == "figure_noun", (text, e.value.reason)
    assert gp("There are 2000 steps and 40 chambers", ["p_steps"], role)
    assert gp("146 metres tall", ["p_h", "p_w"], role)
    assert gp("230 metres on each side", ["p_h", "p_w"], role)
    assert gp("An estimated 2.3 million blocks", ["p_blocks"], role)     # a noun inside the facts' phrase


def test_figure_phrases():
    assert dc._figure_phrases("2000 steps and 40 chambers", 5) == [("2000", ["step"]), ("40", ["chamber"])]
    assert dc._figure_phrases("between 10 and 20 years", 5) == [("10", ["year"]), ("20", ["year"])]
    assert dc._figure_phrases("146 metres tall, the base", 5) == [("146", ["m", "tall"])]


def test_title_adds_no_limiter():
    """Round 2 review 2 (blocking): a title never adds a limiter, comparative or negation the facts do not use."""
    for title in ("Over 230 metres", "Under 230 metres", "Nearly 230 metres", "Almost 230 metres",
                  "More than 230 metres", "Not 146 metres tall"):
        with pytest.raises(dc.CardRefused) as e:
            gp(title, ["p_w", "p_h"], "title")
        assert e.value.code == "added_limiter", title
        with pytest.raises(dc.CardRefused):
            dc.plan_chapter_card(chapter(title=title, fact_ids=("p_w", "p_h")), PYR)
    with pytest.raises(dc.CardRefused, match="adds 'not'"):
        gp("Not built", [], "title")
    assert gp("Over the Nile", [], "title")                  # a preposition that governs no figure is a place
    assert gp("230 metres", ["p_w"], "title")
    with pytest.raises(dc.CardRefused, match="tomb"):        # a title with a figure takes the content-word check
        gp("230 metres of tomb", ["p_w"], "title")


def test_quote_speaker_bound_to_its_marks():
    """Round 2 review 3 (blocking): the speaker comes from the attribution next to the matched quotation."""
    ok = lambda **kw: dc.plan_quote_card(quote(**{"role": "", "work": "", "year": None, **kw}), PYR)
    # round 4 (rule B): a quote is the whole text between one pair of marks, so one sentence of a two-sentence
    # quotation now refuses; the whole quotation passes
    assert ok(text="The joints are fine. They are very fine.", speaker="Petrie", fact_id="p_q")
    for part in ("The joints are fine.", "They are very fine."):
        with pytest.raises(dc.CardRefused, match="whole text between one pair"):
            ok(text=part, speaker="Petrie", fact_id="p_q")
    assert ok(text="Nothing.", speaker="Smith", fact_id="p_q")
    assert ok(text="We saw it,", speaker="Petrie", fact_id="p_q4")            # '"...," said X'
    for text, speaker, fid in (("The joints are fine. They are very fine.", "Smith", "p_q"), ("Nothing.", "Petrie", "p_q"),
                               ("We saw it,", "Petrie", "p_q2"), ("We saw it.", "Petrie", "p_q3"),
                               ("The joints are fine.", "Wrote", "p_q")):
        with pytest.raises(dc.CardRefused):
            ok(text=text, speaker=speaker, fact_id=fid)
    with pytest.raises(dc.CardRefused, match="negates"):
        ok(text="We saw it.", speaker="Petrie", fact_id="p_q3")
    with pytest.raises(dc.CardRefused, match="not a name"):
        ok(text="The joints are fine. They are very fine.", speaker="Wrote", fact_id="p_q")
    # a pronoun ("Later he said") refers back to the previous quotation's attribution
    assert dc.plan_quote_card(quote(fact_id="f_quote_pron", role="", work="", year=None), SHEET)
    with pytest.raises(dc.CardRefused, match="not tied"):     # but carries no role, work or date to the words
        dc.plan_quote_card(quote(fact_id="f_quote_pron"), SHEET)
    with pytest.raises(dc.CardRefused):
        dc.plan_quote_card(quote(speaker="Later"), SHEET)


def test_limiter_window_stops_at_clause():
    """Round 2 review 5: the "not" window stops at ; , . and contrast words; a hedge's span takes no limiter
    from the statement."""
    assert gp("Paid labourers built it", ["p_slaves"], "claim")
    with pytest.raises(dc.CardRefused, match="not"):
        gp("Slaves built the pyramid", ["p_slaves"], "claim")
    assert gp("Took 10–20 years", ["p_disp"], "claim") == "Took 10–20 years"
    with pytest.raises(dc.CardRefused):
        gp("Took 20 years", ["p_disp"], "claim")
    assert dc._clauses(dc._toks("not built by slaves; paid labourers built it")) == [
        ["not", "built", "by", "slaves"], ["paid", "labourers", "built", "it"]]


def test_hook_masthead_planned_at_hook_target(photos):
    """Round 2 review 6: the masthead plans 5.1 like every other hook line."""
    for kind in ("warm", "sky", "noise", "dark"):
        plan = dc.plan_hook_frame(hook(photos[kind], eyebrow="The Richat ring"), SHEET)
        brand_p = dict(plan.meta["_contrast"])["brand"]
        assert brand_p.contrast >= dc.HOOK_TARGET == 5.1, (kind, brand_p.contrast)


def test_qualifier_rewraps_instead_of_refusing():
    """Round 2 review 7: a qualifier landing on another line than its figure re-wraps."""
    sheet = {"topic": PYR["topic"], "facts": PYR["facts"] + [
        fact("f_myth_p", "The pyramid was not built by slaves",
             evidence=[ev("https://www.britannica.com/s", 2, "It is a popular belief that slaves built the pyramids")],
             misconception="Slaves built the pyramids", refuted_by=["p_long"])]}
    spec = myth(myth_text="Slaves built the pyramids", myth_fact_id="f_myth_p", fact_id="p_long",
                fact_text="Built with roughly 2.3 million blocks by large crews of skilled paid workers", sheet=sheet)
    for size in ((1080, 1920), (720, 1280), (1920, 1080), (1280, 720)):
        plan = dc.plan_myth_fact(spec, sheet, *size)
        lines = [plan.drawn_text[k] for k in sorted(plan.drawn_text) if k.startswith("fact_") and k[5:].isdigit()]
        assert not dc._qualifier_split(lines), lines
        assert any("roughly 2.3 million" in ln.lower() for ln in lines), lines   # round 4: drawn in caps
    assert dc._qualifier_split(["Built with roughly", "2.3 million blocks"])
    assert dc._qualifier_split(["Built with 2.3", "million blocks"])
    assert dc._qualifier_split(["It is 146", "metres tall"])
    assert not dc._qualifier_split(["Built with", "roughly 2.3 million"])
    f = dc.get_font("Montserrat", 60, weight=700)
    lines = dc._wrap("The ring is about 40 km across", f, 420, 3)
    assert lines and not dc._qualifier_split(lines) and any("about 40 km" in ln for ln in lines), lines


def test_long_work_title_wraps_the_citation():
    """Round 2 review 8: a long work title wraps the citation to two lines (at a " · ") instead of refusing."""
    spec = quote(text="The joints are so fine that a knife blade cannot be inserted between them.", speaker="Petrie",
                 fact_id="p_cite", role="the Egyptologist", work="The Pyramids and Temples of Gizeh", year=1883)
    for size in ((1080, 1920), (720, 1280)):
        plan = dc.plan_quote_card(spec, PYR, *size)
        assert plan.meta["anchors"]["citation_lines"] == 2
        text = plan.drawn_text["citation"] + " · " + plan.drawn_text["citation_1"]
        assert text.startswith("THE EGYPTOLOGIST · THE PYRAMIDS AND TEMPLES OF GIZEH · 1883"), text
        assert "…" not in text
        bottom = max(e.box[3] for e in plan.elements)
        assert bottom <= round(1058 * size[0] / 1080)
    wide = dc.plan_quote_card(spec, PYR, 1920, 1080)
    assert wide.meta["anchors"]["citation_lines"] == 1
    f = dc.get_font(brand.DATA_FONT, 30, weight=500)
    assert dc._citation_lines(["A" * 80], None, f, 760) is None          # one unbreakable part still refuses
    assert dc._citation_lines([], None, f, 760) == []


def test_breaks_decided_at_design_size():
    """Round 2 review 9: the same quote breaks the same way at 1080x1920 and 720x1280 (and 16:9 at both sizes)."""
    spec = quote(text="The joints are so fine that a knife blade cannot be inserted between them.", speaker="Petrie",
                 fact_id="p_cite", role="", work="", year=None)
    for big, small in (((1080, 1920), (720, 1280)), ((1920, 1080), (1280, 720))):
        a, b = dc.plan_quote_card(spec, PYR, *big), dc.plan_quote_card(spec, PYR, *small)
        qa = [v for k, v in sorted(a.drawn_text.items()) if k.startswith("quote_")]
        qb = [v for k, v in sorted(b.drawn_text.items()) if k.startswith("quote_")]
        assert qa == qb


def test_myth_source_has_air():
    """Round 2 review 10: on 9:16 the source line sits SOURCE_DROP (72) under the last fact baseline."""
    plan = dc.plan_myth_fact(myth(), SHEET)
    els = {e.name: e for e in plan.elements}
    last = plan.meta["anchors"]["fact_baselines"][-1]
    assert els["source"].xy[1] - last == dc.SOURCE_DROP[False] == 72
    assert max(e.box[3] for e in plan.elements) <= 1058


def test_generated_hook_drops_the_eyebrow(photos):
    """Round 2 review 11: masthead, disclosure and eyebrow no longer stack: the disclosure takes the eyebrow's
    place on a generated photo."""
    plan = dc.plan_hook_frame(hook(photos["dark"], eyebrow="The Richat ring", photo_kind="generated"), SHEET)
    names = {e.name for e in plan.elements}
    assert "provenance" in names and "eyebrow" not in names and "eyebrow_rule" not in names
    assert plan.meta["eyebrow_dropped"]
    plan = dc.plan_hook_frame(hook(photos["dark"], eyebrow="The Richat ring"), SHEET)
    assert "eyebrow" in {e.name for e in plan.elements} and plan.meta["eyebrow_dropped"] is None


def _sky_and_yellow(tmp_path):
    h, w = 2100, 1300
    yy = np.mgrid[0:h, 0:w][0]
    sky = np.dstack([200 + 55 * yy / h, 225 + 30 * yy / h, np.full((h, w), 255.0)])
    yellow = np.dstack([np.full((h, w), 250.0), np.full((h, w), 225.0), 60 + 30 * np.cos(yy / 40)])
    out = {}
    for name, arr in (("sky", sky), ("yellow", yellow)):
        out[name] = tmp_path / f"{name}.jpg"
        Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).save(out[name], quality=92)
    return out


def test_hook_scrim_sized_to_the_ground(tmp_path, photos):
    """Review 2026-11-03 (3): on a flat bright ground (white sky, yellow wall) the claim sits on a designed plate,
    one full-bleed INK band with crisp edges, not a soft wash: no glyph glow, no eyebrow patch, every line >= 5.1,
    the photograph untouched above and below the band, and the masthead on a plate of its own."""
    grounds = _sky_and_yellow(tmp_path)
    for name, path in grounds.items():
        for spec in (hook(path, claim="About 40 km across", eyebrow="The Richat ring"),
                     hook(path, claim="Not built by aliens", fact_ids=("f_myth",), photo_kind="generated")):
            plan = dc.plan_hook_frame(spec, SHEET)
            sc = plan.meta["scrim"]
            assert sc["mode"] == "plate" and sc["strength"] in dc.HOOK_PLATE_STEPS, (name, sc)
            assert sc["halo"] <= dc.HOOK_GLOW_MAX and sc.get("eyebrow_patch", 0.0) == 0.0, (name, sc)
            assert plan.meta["mast_band"]["strength"] in dc.HOOK_PLATE_STEPS, (name, plan.meta["mast_band"])
            for n, p in plan.meta["_contrast"]:
                assert p.contrast >= 5.1, (name, n, p.contrast)
            g0 = np.asarray(plan.meta["_ground0"]).astype(float)
            bg = np.asarray(plan.meta["_r"]["bg"]).astype(float)
            y0, y1 = sc["plate"]
            inside_ = g0[y0 + 2:y1 - 2]
            ratio = inside_.mean(axis=(0, 2)) / np.maximum(1, bg[y0 + 2:y1 - 2].mean(axis=(0, 2)))
            assert ratio.max() - ratio.min() <= 0.03, name            # one even plate, edge to edge
            below = slice(y1 + 3, min(1920, y1 + 300))
            assert np.abs(g0[below] - bg[below]).max() <= 2, name    # the photograph below it untouched
            a = np.asarray(plan.meta["_r"]["under"].getchannel("A")).astype(int)[:, 540]
            assert a[y0 - 3] < 30 < a[y0 + 3] and a[y1 + 3] < 30 < a[y1 - 3], name   # crisp edges
    dark = dc.plan_hook_frame(hook(photos["dark"]), SHEET)
    assert dark.meta["scrim"]["mode"] == "wash" and dark.meta["scrim"]["strength"] == 0.0   # no scrim at all


@needs_ffmpeg
def test_hook_bright_grounds_grade(tmp_path):
    """Round 2 review 4: with the lighter scrim the graded hook (vignette kept) still measures >= 4.5."""
    grounds = _sky_and_yellow(tmp_path)
    for name, path in grounds.items():
        out = dc.render_hook_frame(hook(path, claim="About 40 km across", eyebrow="The Richat ring"), SHEET,
                                   tmp_path / name)
        assert out is not None, name
        c = _read_json(str(out) + ".contrast.json")
        assert c["graded"] and c["min"] >= 4.5, (name, c["min"])


def test_hook_diamond_stays_visible(tmp_path, photos):
    """Round 2 review 4 follow-up: with the lighter masthead band the GOLD diamond keeps 3:1 on a yellow wall."""
    grounds = _sky_and_yellow(tmp_path)
    for path in (grounds["yellow"], grounds["sky"], photos["warm"], photos["dark"]):
        plan = dc.plan_hook_frame(hook(path), SHEET)
        assert plan.meta["diamond_contrast"] >= 3.0, (path, plan.meta["diamond_contrast"])


# ------------------------------------------------------------------------------------------------ round 3 review fixes
def _pet(fid, quote_text, *speakers):
    """A probe quotation; `speakers` are the fact sheet's structured credits, one per quotation in order (none:
    the sheet names no speaker, so the card refuses whatever the prose says)."""
    if not speakers:
        return fact(fid, "Petrie surveyed it", evidence=[ev("https://www.nasa.gov/r3", 1, quote_text)])
    inner = [m for m in __import__("re").findall(r'"([^"]*)"', quote_text)]
    return fact(fid, "Petrie surveyed it", evidence=[ev("https://www.nasa.gov/r3", 1, quote_text, quotations=[
        said(t, sp) for t, sp in zip(inner, speakers)])])


R3 = {"topic": "The Great Pyramid", "facts": PYR["facts"] + [
    fact("r_cmp", "The pyramid is 146 metres tall, taller than the 60 metre Djoser pyramid"),
    fact("r_ppl", "10000 workers and 2000 slaves"),
    fact("r_wk", "About 20000 workers built it over 20 years"),
    fact("r_s1", "Slaves did not build the pyramid; the pyramid was built by workers"),
    fact("r_s2", "Slaves did not build it. Workers built it in 20 years"),
    fact("r_s3", "The core is not hollow, but the chambers are hollow"),
    fact("r_s4", "Khufu never saw it finished"),
    fact("r_s5", "No slaves built the pyramid"),
    fact("r_pct", "About 40% of the casing survives"),
    _pet("a1", 'Smith said that Petrie wrote: "The joints are fine."', "Petrie"),
    _pet("a2", '"The joints are fine," Smith told Petrie.', "Smith"),
    _pet("a3", 'Petrie wrote "The joints are fine." Smith disagreed. Later he said "They are rough."', "Petrie", "Smith"),
    _pet("a5", 'Petrie denied ever writing "The joints are fine."'),
    _pet("a6", 'According to Smith, Petrie never said "The joints are fine."'),
    _pet("a7", 'Smith, mocking Petrie, wrote: "The joints are fine."'),
    _pet("a8", '"The joints are fine," Petrie\'s rival Smith said.', "Smith"),
    _pet("a9", 'Neither Petrie nor Smith wrote "The joints are fine."'),
    _pet("a10", 'Smith quoted a letter to Petrie: "The joints are fine."'),
    _pet("a12", '"The joints are fine," said Smith, Petrie\'s critic.', "Smith"),
    _pet("a14", 'Unlike Petrie, Smith wrote: "The joints are fine."', "Smith"),
    _pet("a15", '"The joints are fine," Smith said, quoting Petrie.'),
    _pet("a16", 'Smith asked Petrie: "The joints are fine?"', "Smith"),
    _pet("a17", 'Petrie wrote "Good." Smith later wrote: "Bad." He said "The joints are fine."', "Petrie", "Smith", "Smith"),
    _pet("a18", 'According to Smith, "The joints are fine."', "Smith"),
    fact("r_gct", "Gertrude Caton-Thompson excavated the site", evidence=[ev(
        "https://www.jstor.org/x", 1, 'In a letter to the Royal Society, the archaeologist Gertrude Caton-Thompson '
                                      'wrote: "The walls are African work."', speaker="Gertrude Caton-Thompson")]),
    fact("r_long_name", "Bartholomew Fetherstonhaugh wrote it", evidence=[ev(
        "https://www.jstor.org/y", 1, 'Bartholomew Fetherstonhaugh wrote: "The walls are African work."',
        speaker="Bartholomew Fetherstonhaugh")]),
]}


def g3(text, ids, role="fact"):
    return dc.ground_text(text, R3, ids, role=role)


@pytest.mark.parametrize("role", ["claim", "fact", "title"])
def test_figure_bound_to_the_noun_before_it(role):
    """Round 3 review 2 (blocking): the content words leading up to a figure are bound like those after it."""
    for text, ids in (("Each side is 146 metres", ["p_w", "p_h"]), ("Tall at 230 metres", ["p_w", "p_h"]),
                      ("Chambers 2000", ["p_steps"]), ("Chambers: 2000", ["p_steps"]), ("Slaves: 10000", ["r_ppl"]),
                      ("Djoser pyramid is 146 metres tall", ["r_cmp"])):
        with pytest.raises(dc.CardRefused) as e:
            g3(text, ids, role)
        assert e.value.code == "figure_noun", (text, e.value.reason)
    assert g3("Steps: 2000", ["p_steps"], role)
    assert g3("The pyramid is 146 metres tall", ["r_cmp"], role)
    assert g3("About 20000 workers built it over 20 years", ["r_wk"], role)
    assert dc._figure_spans("Height: 146 metres tall", 5, 3) == [("146", ["m", "tall"], ["height"])]
    assert dc._figure_spans("The 40 chambers sit on 2000 steps", 3, 3) == [
        ("40", ["chamber", "sit"], []), ("2000", ["step"], [])]


def test_comparative_binds_its_figure():
    """Round 3: "than" (and any limiter) before a figure must stand before that same figure in the facts."""
    with pytest.raises(dc.CardRefused) as e:
        g3("Taller than 146 metres", ["r_cmp", "p_h"], "title")
    assert e.value.code == "added_limiter" and "before 146" in e.value.reason, e.value.reason
    with pytest.raises(dc.CardRefused):
        g3("Over 20000 workers", ["r_wk"], "title")
    assert g3("Over 20 years", ["r_wk"], "title")
    assert g3("Taller than the 60 metre Djoser pyramid", ["r_cmp"], "fact")


def test_negation_governs_its_subject():
    """Round 3 review 1 (blocking): a fact's negated clause is never drawn as its affirmative, even when its
    predicate words also stand in a free clause."""
    for text, fid in (("Slaves built the pyramid", "r_s1"), ("Slaves built it", "r_s2"),
                      ("The core is hollow", "r_s3"), ("Khufu saw it finished", "r_s4"),
                      ("Slaves built the pyramid", "r_s5"), ("Slaves built the pyramid", "p_slaves")):
        for role in ("claim", "fact"):
            with pytest.raises(dc.CardRefused, match="drops the facts'"):
                g3(text, [fid], role)
    assert g3("Workers built it in 20 years", ["r_s2"], "claim")
    assert g3("Workers built the pyramid", ["r_s1"], "claim")
    assert g3("The chambers are hollow", ["r_s3"], "claim")
    assert g3("Paid labourers built it", ["p_slaves"], "claim")
    assert g3("Took 10–20 years", ["p_disp"], "claim") == "Took 10–20 years"
    assert dc._stem("built") == dc._stem("build") == "build"


def test_contractions_are_negations():
    """Round 3 review 5 (blocking): "isn't" and "wasn't" are read as "not"."""
    for title in ("The core isn't hollow", "The core wasn't solid", "Khufu can't have seen it"):
        with pytest.raises(dc.CardRefused) as e:
            g3(title, [], "title")
        assert e.value.code == "added_limiter", title
    with pytest.raises(dc.CardRefused):
        g3("It isn't 146 metres tall", ["p_h"], "title")
    assert g3("The core isn't hollow", ["r_s3"], "title")          # the facts use the negation
    assert g3("The core isn’t hollow", ["r_s3"], "claim")
    assert dc._toks("isn't") == ["is", "not"] and dc._toks("won’t") == ["will", "not"]


def test_units_and_percent_fold():
    """Round 3 review 8, 9: "m" and "metres", "%" and "percent" are one unit (they over-refused true lines)."""
    assert g3("146 m tall", ["p_h"], "claim")
    assert g3("About 40 percent of the casing survives", ["r_pct"], "fact")
    assert g3("About 40 per cent survives", ["r_pct"], "fact")
    with pytest.raises(dc.CardRefused):
        g3("About 40 percent of the core survives", ["r_pct"], "fact")


R3_CREDITS_OK = (("a1", "The joints are fine.", "Petrie"), ("a2", "The joints are fine,", "Smith"),
                 ("a3", "The joints are fine.", "Petrie"), ("a3", "They are rough.", "Smith"),
                 ("a8", "The joints are fine,", "Smith"), ("a12", "The joints are fine,", "Smith"),
                 ("a14", "The joints are fine.", "Smith"), ("a16", "The joints are fine?", "Smith"),
                 ("a17", "The joints are fine.", "Smith"), ("a18", "The joints are fine.", "Smith"))
R3_CREDITS_BAD = (("a1", "The joints are fine.", "Smith"), ("a2", "The joints are fine,", "Petrie"),
                  ("a3", "They are rough.", "Petrie"), ("a5", "The joints are fine.", "Petrie"),
                  ("a6", "The joints are fine.", "Petrie"), ("a6", "The joints are fine.", "Smith"),
                  ("a7", "The joints are fine.", "Petrie"), ("a8", "The joints are fine,", "Petrie"),
                  ("a9", "The joints are fine.", "Petrie"), ("a9", "The joints are fine.", "Smith"),
                  ("a10", "The joints are fine.", "Petrie"), ("a10", "The joints are fine.", "Smith"),
                  ("a12", "The joints are fine,", "Petrie"), ("a14", "The joints are fine.", "Petrie"),
                  ("a15", "The joints are fine,", "Petrie"), ("a15", "The joints are fine,", "Smith"),
                  ("a16", "The joints are fine?", "Petrie"), ("a17", "The joints are fine.", "Petrie"),
                  ("a18", "The joints are fine.", "Petrie"))


def _q3(fid, text, speaker, **kw):
    return dc.plan_quote_card(quote(**{"text": text, "speaker": speaker, "fact_id": fid, "role": "", "work": "",
                                       "year": None, **kw}), R3)


@pytest.mark.parametrize("fid,text,speaker", R3_CREDITS_BAD)
def test_quote_never_credits_the_wrong_person(fid, text, speaker):
    """Round 3 review 3 (blocking): the speaker is the subject of the speech verb next to the matched marks; a
    negator or a second-hand credit anywhere in that clause refuses; a pronoun is the nearest name before it."""
    with pytest.raises(dc.CardRefused) as e:
        _q3(fid, text, speaker)
    assert e.value.reason.startswith(f"speaker {speaker!r}") or "attributes it" in e.value.reason, e.value.reason


@pytest.mark.parametrize("fid,text,speaker", R3_CREDITS_OK)
def test_quote_credits_the_subject(fid, text, speaker):
    assert _q3(fid, text, speaker)


def test_quote_refusal_quotes_the_clause():
    """Round 3 review 7: the refusal shows the attribution clause it checked, not the speaker again."""
    with pytest.raises(dc.CardRefused) as e:
        _q3("a2", "The joints are fine,", "Petrie")
    assert "'Smith told Petrie'" in e.value.reason and "credits 'Smith'" in e.value.reason


@pytest.mark.parametrize("size", [(1080, 1920), (720, 1280), (1920, 1080)])
def test_two_part_name_fits_the_quote_card(size):
    """Round 3 review 6: "Gertrude Caton-Thompson" (23) no longer refuses at 9:16: the tracking steps down, then the
    name wraps onto two lines, at the kicker floor; never past the measure."""
    plan = dc.plan_quote_card(quote(text="The walls are African work.", speaker="Gertrude Caton-Thompson",
                                    fact_id="r_gct", role="", work="", year=None), R3, *size)
    sp = [e for e in plan.elements if e.name.startswith("speaker")]
    assert sp and all(e.px >= (40 if size[0] < size[1] else 28) * (size[0] / 1080 if size[0] < size[1] else
                                                                     min(size[0] / 1920, size[1] / 1080)) - 1 for e in sp)
    assert " ".join(e.text for e in sp) == "GERTRUDE CATON-THOMPSON"
    font = dc.get_font("Montserrat", 40, weight=800)
    lines, track = dc._speaker_lines("BARTHOLOMEW FETHERSTONHAUGH", font, 6, 1.0, 760)
    assert lines == ["BARTHOLOMEW", "FETHERSTONHAUGH"] and track == 6
    long_plan = _q3("r_long_name", "The walls are African work.", "Bartholomew Fetherstonhaugh")
    names = [e for e in long_plan.elements if e.name.startswith("speaker")]
    assert [e.text for e in names] == ["BARTHOLOMEW", "FETHERSTONHAUGH"]
    assert names[1].xy[1] > names[0].xy[1]
    cite = [e for e in long_plan.elements if e.name.startswith("citation")]
    assert all(c.box[1] > names[1].box[3] for c in cite)


def test_hook_halos_drawn_under_every_line(tmp_path, photos):
    """Round 3 review 4 (blocking): the hook's glyph halos are one whole-frame field drawn under all the lines (no
    plate halo, so no crop-edge box and no later line's halo over an earlier line's glyphs), and the first BONE
    line keeps its colour on bright grounds."""
    grounds = _sky_and_yellow(tmp_path)
    for path in (grounds["yellow"], grounds["sky"], photos["warm"], photos["noise"]):
        plan = dc.plan_hook_frame(hook(path, claim="It is 146 metres tall", fact_ids=("p_h",),
                                       eyebrow="The Great Pyramid", sheet=R3), R3)
        r = plan.meta["_r"]
        assert all(p.halo == 0 for p in r["plates"]), [p.halo for p in r["plates"]]
        a = np.asarray(r["under"].getchannel("A")).astype(int)
        # a feathered field: no straight cut where a plate's crop would have ended (review 2026-11-03: the only
        # hard edges are the designed plates' own top and bottom, full bleed)
        assert np.abs(np.diff(a, axis=1)).max() <= 24, path
        jumps = set(np.nonzero(np.abs(np.diff(a, axis=0)).max(axis=1) > 24)[0])
        if plan.meta["scrim"]["mode"] == "plate":
            y0, y1 = plan.meta["scrim"]["plate"]
            mb = plan.meta["mast_band"]["band"]
            edges = {y for e in (y0, y1, *mb) for y in range(e - 2, e + 2)}
            jumps -= edges
        assert not jumps, (path, sorted(jumps)[:10])
        frame0 = np.asarray(dc._hook_compose(r, 0)).astype(float)
        first = next(p for p in r["plates"] if p.text == plan.drawn_text["title_0"])
        m = np.zeros(frame0.shape[:2], bool)
        h, w = first.glyphs.shape
        ox, oy = first.origin
        m[oy:oy + h, ox:ox + w] = dc._erode3(dc._erode3(first.glyphs > 200))
        lum = frame0[m].mean(axis=1)
        assert np.percentile(lum, 5) >= 220, (path, np.percentile(lum, 5))
        assert plan.meta["scrim"]["halo"] <= dc.HOOK_GLOW_MAX, (path, plan.meta["scrim"])   # round 4: no heavy glow
        for n, p in plan.meta["_contrast"]:
            assert p.contrast >= 5.1, (path, n, p.contrast)


def test_diamond_halo_is_round(tmp_path):
    """Round 3 review 4: the masthead diamond's halo is the diamond grown and feathered, never a box."""
    from PIL import Image as _I
    bg = _I.new("RGB", (1080, 1920), (250, 225, 70))
    under = _I.new("RGBA", bg.size, (0, 0, 0, 0))
    cx, cy, r = 300, 400, 10
    shape = [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]
    layer = dc._rule_halo(bg, under, [cx - r, cy - r, cx + r, cy + r], shape=shape)
    a = np.asarray(layer.getchannel("A")).astype(float)
    assert a.max() > 0          # a yellow wall needs the halo
    d = 22
    # the corners of the box around the diamond's patch are lighter than its axes at the same distance
    assert a[cy - d, cx - d] < 0.6 * a[cy - d, cx], (a[cy - d, cx - d], a[cy - d, cx])


# ------------------------------------------------------------------------------------------------ round 4: provenance
PROV = {"topic": "The Great Pyramid", "facts": [
    fact("v_sl", "The pyramid was not built by slaves; paid labourers built it"),
    fact("v_her", "According to Herodotus, 100000 slaves built it"),
    fact("v_steps", "There are 2000 steps and 40 chambers"),
    fact("v_disp", "Construction took 20 years", True, "between 10 and 20 years"),
    fact("v_weak", "Workers built it", evidence=[ev("https://blog.example/w", 3, "Workers built it, every one paid")]),
    fact("v_ev", "Workers built it", evidence=[ev("https://www.britannica.com/w", 2,
                                                   'Smith said: "slaves built it"; paid workers built it.')]),
    fact("v_false", "Slaves built the pyramid", verdict="false", refuted_by=["v_label"],
         evidence=[ev("https://www.britannica.com/f", 2, "Slaves built the pyramid")]),
    fact("v_label", "Paid workers built the pyramid", misconception="Slaves built the pyramid",
         evidence=[ev("https://www.britannica.com/l", 2, "Paid workers built the pyramid")]),
    fact("v_q", "Petrie surveyed it", evidence=[ev("https://www.nasa.gov/q", 1, 'Petrie wrote: "The joints are fine."')]),
    fact("v_q2", "Petrie surveyed it", evidence=[ev("https://www.nasa.gov/q", 1, 'Petrie wrote: "A." Smith said "B."',
                                                    speaker="Petrie")]),
]}


def _title(title, src, ids=(), payload=None):
    return dc.plan_chapter_card(dc.ChapterCard(1, 2, title, fact_ids=ids, title_src=src), PROV, payload=payload)


def test_every_line_needs_provenance(photos):
    """Round 4 (A): a line with no source refuses, however true its words."""
    for planner in (lambda: dc.plan_chapter_card(dc.ChapterCard(1, 2, "Paid labourers built it"), PROV),
                    lambda: dc.plan_hook_frame(dc.HookFrame("Paid labourers built it", ("v_sl",), photos["dark"], ""),
                                               PROV),
                    lambda: dc.plan_myth_fact(dc.MythFact("Slaves built the pyramid", "v_label", "Paid workers built "
                                                          "the pyramid", "v_label",
                                                          myth_src=span(PROV, "v_label", "Slaves built the pyramid",
                                                                        "misconception")), PROV)):
        with pytest.raises(dc.CardRefused) as e:
            planner()
        assert e.value.code == "no_provenance", e.value.reason


def test_retired_sources_refuse(photos):
    """Review 2026-11-03, blocking 1 and 2: a span of a fact passage, a cover or slide headline and the myth card's
    separate fact line are no longer card sources."""
    for src in (span(PROV, "v_label", "Paid workers built the pyramid"),
                span(PROV, "v_label", "Paid workers built the pyramid", "evidence", 0)):
        with pytest.raises(dc.CardRefused) as e:
            _title("Paid workers built the pyramid", src)
        assert e.value.code == "no_provenance", e.value.reason
    for path in ("thumbnail.headline", "thumbnail.eyebrow", "story_slides.0.headline", "carousel_slides.0.heading",
                 "narration_script", "description"):
        p = _gate_payload(PROV, "title", "Paid workers built the pyramid")
        with pytest.raises(dc.CardRefused) as e:
            _title("Paid workers built the pyramid", dc.Gated(path, p["fact_review"]["public_fingerprint"], p))
        assert e.value.code == "ungated", (path, e.value.reason)
    good = myth()
    with pytest.raises(dc.CardRefused) as e:
        dc.plan_myth_fact(dc.MythFact(myth_src=good.myth_src, fact_src=gate("The ring formed through erosion")), SHEET)
    assert e.value.code == "no_provenance"
    with pytest.raises(dc.CardRefused) as e:       # a statement field is not a myth field, nor the reverse
        dc.plan_myth_fact(dc.MythFact(myth_src=statement("About 40 km across", "f_len")), SHEET)
    assert e.value.code == "bad_field"
    with pytest.raises(dc.CardRefused) as e:
        dc.plan_hook_frame(hook(photos["dark"], claim_src=good.myth_src), SHEET)
    assert e.value.code == "bad_field"


def test_gated_line_needs_the_gate_record(tmp_path):
    """The gated title is accepted only with a current fact review of the same payload and sheet that carries the
    checkers' own record (fact_evidence.checked_fact_review): a payload stamped without a check run is refused."""
    src = gate("Paid labourers built it", PROV)
    assert _title("Paid labourers built it", src).meta["provenance"]["title"]["source"] == "gated"
    with pytest.raises(dc.CardRefused, match="is not the gated"):
        _title("Slaves built it", src)
    tampered = dict(src.payload, title="Slaves built it")          # edited after the review: no longer current
    with pytest.raises(dc.CardRefused, match="no current fact review"):
        _title("Slaves built it", dc.Gated("title", src.review, tampered))
    with pytest.raises(dc.CardRefused, match="recorded review"):
        _title("Paid labourers built it", dc.Gated("title", "0" * 64, src.payload))
    with pytest.raises(dc.CardRefused, match="different fact sheet"):
        dc.plan_chapter_card(dc.ChapterCard(1, 2, "Paid labourers built it", title_src=src), SHEET)
    with pytest.raises(dc.CardRefused, match="only the title and youtube_title"):
        _title("Paid labourers built it", dc.Gated("caption", src.review, src.payload))
    with pytest.raises(dc.CardRefused, match="no payload"):
        _title("Paid labourers built it", dc.Gated("title", src.review))
    failed = dict(src.payload, guardrail_passed=False)
    with pytest.raises(dc.CardRefused, match="no current fact review"):
        _title("Paid labourers built it", dc.Gated("title", src.review, failed))
    # the plan's own payload is used when the source carries none
    assert _title("Paid labourers built it", dc.Gated("title", src.review), payload=src.payload)
    yt = gate("Paid labourers built it", PROV, path="youtube_title")
    assert _title("Paid labourers built it", yt).meta["provenance"]["title"]["path"] == "youtube_title"
    # gated text still meets the second guard: a negation it drops refuses
    neg = gate("Slaves built the pyramid", PROV)
    with pytest.raises(dc.CardRefused):
        dc.plan_chapter_card(dc.ChapterCard(1, 2, "Slaves built the pyramid", fact_ids=("v_sl",), title_src=neg), PROV)


def test_forged_gate_record_is_refused():
    """Review 2026-11-03 (non-blocking, now required): record_fact_review alone, with no checker run, made
    "Aliens built the pyramid" a chapter title. A gate record must carry the checkers' audits, models and hash."""
    stamped = gate("Aliens built the pyramid", PROV, checked=False)
    with pytest.raises(dc.CardRefused, match="no record of a check run") as e:
        _title("Aliens built the pyramid", stamped)
    assert e.value.code == "unchecked_gate"
    good = gate("Paid labourers built it", PROV)

    def forged(mutate, rehash=True):
        p = json.loads(json.dumps(good.payload))
        rec = p["fact_review"]["check"]
        mutate(rec)
        if rehash:
            rec["sha256"] = __import__("src.checkpoints", fromlist=["fingerprint"]).fingerprint(
                {k: v for k, v in rec.items() if k != "sha256"})
        return dc.Gated("title", good.review, p)
    cases = ((lambda r: r["trace"].update(passed=False), True, "no passing audits"),
             (lambda r: r["trace"].update(audits=[]), True, "no passing audits"),
             (lambda r: r["evidence"].update(model=""), True, "names no model"),
             (lambda r: r["trace"]["audits"][0].update(verdict="contradicts"), True, "blocking claim"),
             (lambda r: r["evidence"]["audits"][0].update(verdict="contradicted"), True, "blocking claim"),
             (lambda r: r.update(public_fingerprint="0" * 64), True, "other copy"),
             (lambda r: r["trace"].update(summary="edited"), False, "does not match its hash"))
    for mutate, rehash, why in cases:
        with pytest.raises(dc.CardRefused, match=why) as e:
            _title("Paid labourers built it", forged(mutate, rehash))
        assert e.value.code == "unchecked_gate", e.value.reason


def test_myth_needs_a_checked_myth_field():
    """Operator decision 2026-11-03: the myth card draws one checked myth field. Its check must hold now: a
    supported verdict re-derived from the stored runs, the field and the sheet's facts unchanged since."""
    with pytest.raises(dc.CardRefused) as e:
        dc.plan_myth_fact(dc.MythFact("Slaves built the pyramid", "v_label", "Paid workers built the pyramid",
                                      "v_label"), PROV)
    assert e.value.code == "no_provenance"
    sh = json.loads(json.dumps(PROV))
    ref = myth(myth_text="Slaves built the pyramid", fact_text="Paid workers built the pyramid", fact_id="v_label",
               sheet=sh).myth_src
    plan = dc.plan_myth_fact(dc.MythFact(myth_src=ref), sh)
    assert plan.meta["provenance"]["myth"] == {"source": "card_field", "kind": "myths", "id": ref.id,
                                               "part": "myth", "refuting_fact_id": "v_label"}
    assert plan.drawn_text["myth_0"].startswith("SLAVES")
    bad = myth(myth_text="Slaves built the pyramid", fact_text="Paid workers built the pyramid", fact_id="v_label",
               sheet=sh, myth_good=False).myth_src
    with pytest.raises(dc.CardRefused) as e:
        dc.plan_myth_fact(dc.MythFact(myth_src=bad), sh)
    assert e.value.code == "unsupported_field"

    def field(s):
        return next(f for f in s["cards"]["myths"] if f["id"] == ref.id)
    edited = json.loads(json.dumps(sh))
    field(edited)["myth"] = "Aliens built the pyramid"
    refacted = json.loads(json.dumps(sh))
    refacted["facts"][0]["statement"] = "The pyramid was built by slaves"
    stamped = json.loads(json.dumps(sh))               # a "supported" verdict stamped with no runs behind it
    chk = field(stamped)["check"]
    chk["runs"] = []
    chk["sha256"] = cf.check_hash(chk)
    unhashed = json.loads(json.dumps(sh))
    field(unhashed)["check"]["parts"]["myth"] = "supported"
    for s, code in ((edited, "stale_check"), (refacted, "stale_check"), (stamped, "forged_check"),
                    (unhashed, "forged_check")):
        with pytest.raises(dc.CardRefused) as e:
            dc.plan_myth_fact(dc.MythFact(myth_src=ref), s)
        assert e.value.code == code, (code, e.value.reason)
    for spec, code in ((dc.MythFact("Slaves built it", myth_src=ref), "not_verbatim"),
                       (dc.MythFact(fact_id="v_sl", myth_src=ref), "bad_field")):
        with pytest.raises(dc.CardRefused) as e:
            dc.plan_myth_fact(spec, sh)
        assert e.value.code == code, e.value.reason


def test_myth_line_is_the_whole_field():
    """Review 2026-11-03, blocking 1: the myth is the whole myth field, never clause-trimmed (a true clause of a
    compound misconception is never struck out on its own)."""
    sh = {"topic": "The Great Pyramid", "facts": [fact("w", "Paid crews built the pyramid"),
                                                 fact("t", "The pyramid is 146 metres tall")]}
    # review 2026-11-03 (second round), blocking 1: a compound myth with a TRUE clause is refused whole (the guard
    # tests every clause); the whole-field rule is shown on a compound myth whose clauses are all false
    true_clause = "The pyramid is 146 metres tall, and slaves dragged every block"
    with pytest.raises(dc.CardRefused) as e:
        dc.plan_myth_fact(myth(myth_text=true_clause, fact_text="Paid crews built the pyramid", fact_id="w", sheet=sh),
                          sh, 1920, 1080)
    assert e.value.code == "affirmed_myth"
    whole = "Slaves dragged every block of the pyramid, and aliens cut the stone"
    plan = dc.plan_myth_fact(myth(myth_text=whole, fact_text="Paid crews built the pyramid", fact_id="w", sheet=sh),
                             sh, 1920, 1080)
    drawn = " ".join(plan.drawn_text[k] for k in sorted(plan.drawn_text) if k.startswith("myth_") and k[5:].isdigit())
    assert drawn == dc.typeset(whole).upper()
    with pytest.raises(dc.CardRefused, match="not the checked field"):
        dc.plan_myth_fact(dc.MythFact("Slaves dragged every block of the pyramid",
                                      myth_src=myth(myth_text=whole, fact_text="Paid crews built the pyramid",
                                                    fact_id="w", sheet=sh).myth_src), sh)


def test_quote_needs_a_checked_quote_field():
    """Operator decision 2026-11-03: a quote card draws only a checked quote field; a supported verdict, its
    quotation whole between one pair of the cited evidence's marks, the speaker in that evidence's attribution."""
    with pytest.raises(dc.CardRefused) as e:
        dc.plan_quote_card(dc.QuoteCard("The joints are fine.", "Petrie", "v_q", 0), PROV)
    assert e.value.code == "no_provenance"
    for spec in (quote(role="Astronomer"), quote(year=1925), quote(good=False), quote(work="Daily Telegraph")):
        with pytest.raises(dc.CardRefused) as e:
            dc.plan_quote_card(spec, SHEET)
        assert e.value.code == "unsupported_field", e.value.reason
    with pytest.raises(dc.CardRefused) as e:     # the second quotation is Smith's, by the evidence's attribution
        dc.plan_quote_card(quote(text="B.", speaker="Petrie", fact_id="v_q2", role="", work="", year=None,
                                 sheet=PROV), PROV)
    assert e.value.code == "unsupported_field"
    plan = dc.plan_quote_card(quote(), SHEET)
    assert plan.meta["provenance"]["quote"]["source"] == "card_field"
    assert plan.drawn_text["citation"].startswith("GEOLOGIST · SAHARA NOTEBOOK · 1940")


def test_myth_card_is_display_type(photos):
    """Round 4 (F): MYTH and FACT in one Anton size, large on short texts, the strike a bold GOLD bar; a bright
    photo is dimmed further instead of refusing the 9:16 card."""
    for size in ((1080, 1920), (1920, 1080)):
        plan = dc.plan_myth_fact(myth(), SHEET, *size)
        els = {e.name: e for e in plan.elements}
        texts = [e for n, e in els.items() if n[:5] in ("myth_", "fact_") and n[5:].isdigit()]
        assert {e.face for e in texts} == {"Anton"}
        # review 2026-11-03 (second round, 3): one size on 9:16; on 16:9 the myth is set smaller (MYTH_RATIO) so the
        # fact is the hero
        m_px = {e.px for e in texts if e.name.startswith("myth_")}
        f_px = {e.px for e in texts if e.name.startswith("fact_")}
        assert len(m_px) == 1 and len(f_px) == 1
        # operator decision C (2026-11-03, deliberate change): the 9:16 myth is a step under the fact too
        # (MYTH_RATIO 0.86; it was one size), so a long myth keeps the card at 84 px or more
        assert max(m_px) < min(f_px), (size, m_px, f_px)
        k = size[0] / 1080 if size[0] < size[1] else 1.0
        assert max(f_px) >= 96 * k, f_px
        bar = els["strike_0"].box
        assert bar[3] - bar[1] >= 0.1 * texts[0].px
        assert all(t.text == t.text.upper() for t in texts)
    for kind in ("sky", "warm", "busy"):
        plan = dc.plan_myth_fact(myth(photo=photos[kind]), SHEET)
        for n, p in plan.meta["_contrast"]:
            assert p.contrast >= dc.PLACE_TARGET, (kind, n, p.contrast)
    bright = tmp_white = Path(tempfile.gettempdir()) / "design_cards_white_2026_11_03.jpg"
    if not tmp_white.exists():
        Image.new("RGB", (1400, 1800), (254, 254, 250)).save(tmp_white, quality=92)
    plan = dc.plan_myth_fact(myth(photo=bright), SHEET)       # was "myth_0's STONE twin reaches only 4.79"
    assert plan.meta["anchors"]["ground_dim"] >= 0.0


def test_hook_wash_has_no_box_and_no_heavy_glow(tmp_path, photos):
    """Round 4 (G): one full-width wash from the frame's top (no eyebrow patch, no edge), the halo a light shadow,
    planned contrast still >= 5.1."""
    grounds = _sky_and_yellow(tmp_path)
    for path in (grounds["sky"], grounds["yellow"], photos["warm"], photos["busy"]):
        plan = dc.plan_hook_frame(hook(path, eyebrow="The Richat ring"), SHEET)
        sc = plan.meta["scrim"]
        assert sc["halo"] <= dc.HOOK_GLOW_MAX and sc["eyebrow_patch"] == 0.0, sc
        for n, p in plan.meta["_contrast"]:
            assert p.contrast >= dc.HOOK_TARGET, (path, n, p.contrast)
        wash = dc._wash_layer((1080, 1920), 0, plan.meta["anchors"]["L"] + 30, sc["strength"])
        a = np.asarray(wash.getchannel("A")).astype(int)
        assert (a == a[:, :1]).all()                                     # no left or right edge
        assert np.abs(np.diff(a[:, 0])).max() <= 3                       # no hard top or bottom edge


def test_quote_916_scales_up():
    """Round 4 (H): the 9:16 quote takes the largest size that fits the zone; short quotes read large."""
    short = dc.plan_quote_card(quote(), SHEET)
    assert short.meta["anchors"]["quote_px"] >= 72
    long_ = dc.plan_quote_card(quote(text="The joints are so fine that a knife blade cannot be inserted between "
                                          "them.", speaker="Petrie", fact_id="p_cite", role="", work="", year=None), PYR)
    assert long_.meta["anchors"]["quote_px"] >= 64, long_.meta["anchors"]
    for plan in (short, long_):
        assert max(e.box[3] for e in plan.elements) <= 1058
        assert all(e.box[2] <= 100 + 760 + dc.DESIGN_WRAP_SLACK for e in plan.elements if e.name.startswith("quote_"))


# earlier reviewers' probes (round 2 review 2, round 3 reviews 1-3): free text that the NLP check let through, or
# nearly did. At card level each now refuses for want of provenance; none is a clause-bounded span of its passage.
EARLIER_PROBES = (
    ("Chambers – 2000", "v_steps"), ("Chambers 2000", "v_steps"), ("There are 40 steps", "v_steps"),
    ("Forty steps", "v_steps"), ("Slaves built it", "v_sl"), ("Slaves built the pyramid", "v_sl"),
    ("100000 slaves built it", "v_her"), ("It took 20 years", "v_disp"), ("Took 15 years", "v_disp"))


@pytest.mark.parametrize("text,fid", EARLIER_PROBES)
def test_earlier_probes_refuse_without_provenance(text, fid):
    with pytest.raises(dc.CardRefused) as e:
        dc.plan_chapter_card(dc.ChapterCard(1, 2, text, fact_ids=(fid,)), PROV)
    assert e.value.code == "no_provenance"
    f = dc.fact_by_id(PROV, fid)
    i = f["statement"].lower().find(text.lower())
    if i >= 0:          # where the words do stand in the passage, the span rules refuse them
        with pytest.raises(dc.CardRefused):
            _title(text, dc.Verbatim(fid, i, i + len(text)), (fid,))


# ---- round 4 review 2: provenance, not word lists (blocking 1-5) and the visual fixes (6-7)
R2 = {"topic": "The Great Pyramid", "facts": [
    fact("r_q", "Paid workers built the pyramid", evidence=[ev("https://www.britannica.com/a", 2,
                                                                "Slaves built the pyramid? No: paid workers did.")]),
    fact("r_amber", "The Amber Room was lost in 1945", evidence=[ev("https://www.britannica.com/b", 2,
         "The Amber Room was destroyed in 1945 \u2014 a story Soviet officials spread to hide the loss.")]),
    fact("r_two", "Paid workers built the pyramid", evidence=[ev("https://www.britannica.com/c", 2,
         "The old tale is told everywhere. Guides repeat it to tourists. Slaves built the pyramid.")]),
    fact("r_circa", "Built c. 2560 BC"),
    fact("r_approx", "It was approx. 146 m tall"),
    fact("r_novel", "In the novel, Petrie found the hidden tomb"),
    fact("r_her", "Herodotus reported: 100000 slaves built it"),
    fact("r_until", "The pyramid was the tallest structure, until 1311"),
    fact("r_uns", "Workers used iron tools", verdict="unsupported"),
    fact("r_disp", "Workers used copper saws", verdict="disputed"),
    fact("r_sup", "Workers used copper saws", verdict="supported"),
    fact("r_ask", "Did slaves build the pyramid?"),
    fact("r_myth", "Paid workers built the pyramid", misconception="Slaves built the pyramid"),
    fact("r_tall", "The pyramid is 138 metres tall today"),
    fact("r_linked", "Paid crews built the pyramid", refutes="r_myth"),
]}


def _r2(title, src, ids=()):
    return dc.plan_chapter_card(dc.ChapterCard(1, 2, title, fact_ids=ids, title_src=src), R2)


@pytest.mark.parametrize("fid,text,field,code", (
    ("r_q", "Slaves built the pyramid", "evidence", "no_provenance"),
    ("r_amber", "The Amber Room was destroyed in 1945", "evidence", "no_provenance"),
    ("r_two", "Slaves built the pyramid", "evidence", "no_provenance"),
    ("r_circa", "2560 BC", "statement", "no_provenance"),
    ("r_approx", "146 m tall", "statement", "no_provenance"),
    ("r_novel", "Petrie found the hidden tomb", "statement", "no_provenance"),
    ("r_her", "100000 slaves built it", "statement", "no_provenance"),
    ("r_until", "The pyramid was the tallest structure", "statement", "no_provenance"),
    ("r_uns", "Workers used iron tools", "statement", "unverified_fact"),
    ("r_disp", "Workers used copper saws", "statement", "unverified_fact"),
    ("r_ask", "Did slaves build the pyramid", "statement", "no_provenance")))
def test_fact_lines_are_whole_statements(fid, text, field, code):
    """Blocking 1-3 (round 4 review 2): fact-passage spans are no longer sources at all; a fact whose verdict is not
    "supported" still refuses first."""
    with pytest.raises(dc.CardRefused) as e:
        dc.ground_line(text, span(R2, fid, text, field, 0 if field == "evidence" else None), R2, (fid,), role="fact")
    assert e.value.code == code, e.value.reason


def test_whole_statements_still_draw():
    assert _r2("Built c. 2560 BC", statement("Built c. 2560 BC", "r_circa", R2), ("r_circa",))
    assert _r2("Workers used copper saws", statement("Workers used copper saws", "r_sup", R2))
    with pytest.raises(dc.CardRefused) as e:           # a statement field is never a question
        _r2("Did slaves build the pyramid?", statement("Did slaves build the pyramid?", "r_ask", R2))
    assert e.value.code == "unsupported_field"
    with pytest.raises(dc.CardRefused) as e:           # nor longer than 84 characters
        _r2("x", statement("Workers used copper saws " * 4, "r_sup", R2))
    assert e.value.code == "bad_field"


def test_myth_fact_comes_from_the_myth_fact_or_a_link():
    """Blocking 4 (round 4 review 2): the fact line is the myth field's correction, drawn only where its refuting
    fact states it (the earlier NLP check, kept as a guard); the refuting fact is the field's, never the spec's."""
    with pytest.raises(dc.CardRefused):
        dc.plan_myth_fact(myth(myth_text="Slaves built the pyramid", fact_text="Paid crews built the pyramid",
                               fact_id="r_tall", sheet=R2), R2)
    ok = dc.plan_myth_fact(myth(myth_text="Slaves built the pyramid", fact_text="Paid crews built the pyramid",
                                fact_id="r_linked", sheet=R2), R2)
    assert ok.meta["provenance"]["fact"]["refuting_fact_id"] == "r_linked"
    assert ok.fact_ids == ("r_linked", "r_linked")


def _payload(sheet, **kw):
    path, text = next(iter(kw.items()))
    return _gate_payload(sheet, path, text)


def test_gated_lines_are_stand_alone_display_copy():
    """Blocking 5 (round 4 review 2), and review 2026-11-03 blocking 2: only the title and youtube_title are gated
    card lines; every other surface, a cover or slide headline included, refuses whatever its siblings say; a gated
    question mark is never dropped."""
    p = _payload(R2, title="Paid workers built the pyramid")
    rv = p["fact_review"]["public_fingerprint"]
    for path in ("sentence_trace.0", "description.1", "story_slides.0.body", "carousel_slides.0.body",
                 "thumbnail.headline", "story_slides.0.headline", "carousel_slides.0.heading"):
        with pytest.raises(dc.CardRefused) as e:
            _r2("Paid workers built the pyramid", dc.Gated(path, rv, p))
        assert e.value.code == "ungated", (path, e.value.reason)
    p = _payload(R2, title="Paid workers built the pyramid?")
    with pytest.raises(dc.CardRefused, match="not the gated"):
        _r2("Paid workers built the pyramid", dc.Gated("title", p["fact_review"]["public_fingerprint"], p))
    p = _payload(R2, title="Workers used iron tools")
    with pytest.raises(dc.CardRefused) as e:
        _r2("Workers used iron tools", dc.Gated("title", p["fact_review"]["public_fingerprint"], p), ("r_uns",))
    assert e.value.code == "unverified_fact"


@pytest.mark.parametrize("q", ('A forger signing as Petrie wrote: "The tomb is empty."',
                               'The line "The tomb is empty." is often attributed to Petrie.',
                               'The line "The tomb is empty." is wrongly attributed to Petrie; Smith wrote it.'))
def test_quote_prose_doubt_refuses(q):
    """Non-blocking (review 2): prose that relays, doubts or forges the credit refuses the quote field."""
    sheet = {"facts": [fact("f", "Petrie described the tomb", kind="other",
                            evidence=[ev("https://www.nasa.gov/x", 1, q)])]}
    with pytest.raises(dc.CardRefused) as e:
        dc.plan_quote_card(quote(text="The tomb is empty.", speaker="Petrie", fact_id="f", role="", work="",
                                 year=None, sheet=sheet), sheet)
    assert e.value.code == "unsupported_field" and "speaker 'Petrie'" in e.value.reason, e.value.reason


def test_myth_card_ground_and_strike(tmp_path):
    """Blocking 7: struck lines step 1.16 x P, the GOLD bar has no INK casing, the long case sets above 64, and the
    ground is the photo opening toward the top (not one flat charcoal)."""
    white = tmp_path / "white.jpg"
    Image.new("RGB", (1400, 2400), (250, 250, 246)).save(white, quality=92)
    plan = dc.plan_myth_fact(myth(myth_text="Extraterrestrials constructed the circular rings", myth_fact_id="f_et",
                                  fact_text="Wind and water slowly eroded the dome over millions of years into "
                                            "concentric rings", fact_id="f_et", photo=white), SHEET)
    a = plan.meta["anchors"]
    assert a["px"] > 64
    steps = np.diff(a["myth_baselines"])
    # operator decision C (2026-11-03, deliberate change): the step is 1.16 x the MYTH's own size (it was the fact's
    # size, the same number while both were one size)
    assert (steps >= round(1.16 * a["myth_px"])).all(), steps
    strike = next(it for it in plan.meta["_r"]["items"] if it.get("type") == "strike")
    assert strike["casing"] == 0
    g = np.asarray(plan.meta["_r"]["rest"]).astype(float).mean(axis=(1, 2))
    assert g[:60].mean() > g[a["R"] + 40:a["R"] + 300].mean() + 40     # the photo shows above the block
    # review 2026-11-03 (round 4, deliberate change): with no step but the 9:16 panel's own two crisp edges
    assert np.abs(_off_panel_edges(np.diff(g))).max() <= 3
    for n, p in plan.meta["_contrast"]:
        assert p.contrast >= dc.PLACE_TARGET, (n, p.contrast)


def test_myth_169_short_myth_on_one_line():
    """Review 2026-11-03: never a smaller size to save a line (the 15 % unwrap is gone): a short myth takes the
    largest size, and display lines break evenly, with no word alone on a line once a text has four words."""
    sh = {"topic": "x", "facts": [fact("ms", "Workers built it")]}
    plan = dc.plan_myth_fact(myth(myth_text="Aliens built it", fact_text="Workers built it", fact_id="ms", sheet=sh),
                             sh, 1920, 1080)
    assert plan.meta["anchors"]["px"] == dc.MYTH_SIZES[0]
    sh2 = {"topic": "x", "facts": [fact("pw", "Paid crews built the pyramid")]}
    for size in ((1920, 1080), (1280, 720), (1080, 1920)):
        plan = dc.plan_myth_fact(myth(myth_text="Slaves dragged every block of the great pyramid",
                                      fact_text="Paid crews built the pyramid", fact_id="pw", sheet=sh2), sh2, *size)
        lines = [plan.drawn_text[k] for k in sorted(plan.drawn_text) if k[:5] in ("myth_", "fact_") and k[5:].isdigit()]
        assert all(len(ln.split()) > 1 for ln in lines), (size, lines)
    f = dc.get_font("Anton", 112)
    assert dc._wrap("SLAVES DRAGGED EVERY BLOCK OF THE GREAT PYRAMID", f, 700, 5, even=True)[0] != "SLAVES"


def test_hook_has_no_glow_and_opens_right(tmp_path, photos):
    """Blocking 6 (round 4 review 2): no glyph halo at all on bright grounds; on a mid ground the wash still eases off
    right of the block. Review 2026-11-03 (3): a bright ground takes the full-bleed plate instead of the wash."""
    grounds = _sky_and_yellow(tmp_path)
    for path in grounds.values():
        plan = dc.plan_hook_frame(hook(path), SHEET)
        sc = plan.meta["scrim"]
        assert not sc["halo"] and sc["mode"] == "plate", sc
        for n, p in plan.meta["_contrast"]:
            assert p.contrast >= dc.HOOK_TARGET, (n, p.contrast)
        layer = dc._plate_layer((1080, 1920), *sc["plate"], sc["strength"])
        al = np.asarray(layer.getchannel("A")).astype(int)
        assert (al == al[:, :1]).all()                                    # full bleed: no left or right edge
    mid = tmp_path / "mid.jpg"
    Image.new("RGB", (1300, 2100), (120, 110, 100)).save(mid, quality=92)
    plan = dc.plan_hook_frame(hook(mid), SHEET)
    sc = plan.meta["scrim"]
    assert sc["mode"] == "wash" and not sc["halo"], sc
    if sc["strength"]:
        w = dc._wash_layer((1080, 1920), 0, plan.meta["anchors"]["L"] + 30, sc["strength"], sc["wash_right"])
        row = np.asarray(w.getchannel("A")).astype(int)[plan.meta["anchors"]["L"]]
        assert row[-1] < row[0] and np.abs(np.diff(row)).max() <= 2


def test_quote_sizes_fill_the_frame():
    """Review 2: a one-word quote reads large at both sizes, and the 16:9 quote is no longer fixed at 56 px."""
    sh = {"topic": "x", "facts": [
        fact("qs", "Petrie entered the chamber", kind="other",
             evidence=[ev("https://www.nasa.gov/q", 1, 'Petrie wrote: "Empty."')])]}
    for size, floor in (((1080, 1920), 96), ((1920, 1080), 88)):
        plan = dc.plan_quote_card(quote(text="Empty.", speaker="Petrie", fact_id="qs", role="", work="", year=None,
                                        sheet=sh), sh, *size)
        assert plan.meta["anchors"]["quote_px"] >= floor, (size, plan.meta["anchors"])
    long_ = dc.plan_quote_card(quote(text=WALK_EV.split('"')[1], fact_id="f_walk", role="", work="", year=None),
                               SHEET, 1920, 1080)
    assert long_.meta["anchors"]["quote_px"] >= 56
    short = dc.plan_quote_card(quote(), SHEET, 1920, 1080)
    assert short.meta["anchors"]["quote_px"] > 56
    assert max(e.box[3] for e in short.elements) <= 816


def test_quote_stays_large():
    """Review 2026-11-03 (4): no 25 % unwrap shrink. A quote takes the largest size that fits, preferring an extra
    line; on 9:16 never under 64 px (the reviewer's 75-character quote had dropped to 58 px)."""
    assert not hasattr(dc, "QUOTE_UNWRAP") and min(dc.QUOTE_SIZES_TALL) == 64
    text = "The joints are so fine that a knife blade cannot be inserted between them."
    assert len(text) == 74
    spec = quote(text=text, speaker="Petrie", fact_id="p_cite", role="", work="", year=None)
    for size in ((1080, 1920), (720, 1280)):
        plan = dc.plan_quote_card(spec, PYR, *size)
        px = plan.meta["anchors"]["quote_px"]
        assert px >= 64 * size[0] / 1080 - 1 and px >= 72, plan.meta["anchors"]
        sizes = [P for P in dc.QUOTE_SIZES_TALL if P > px]
        # every larger size fails for room or lines, not for a rule that trades size for lines
        for P in sizes:
            lines = dc._design_wrap(dc.typeset(text) + "”", "Montserrat", 600, P, 760,
                                    dc.QUOTE_SHORT_LINES if P > dc.QUOTE_SHORT_PX[False] else dc.QUOTE_LINES_LARGE,
                                    dc._Geo(*size))
            assert not lines or dc._quote_tall_height(P, len(lines), 1, 0) > dc.QUOTE_TALL_ROOM, (P, lines)





# ---- review 2026-11-03, second round ---------------------------------------------------------------------------
def _runs(claims):
    """Mocked checkers over several audited claims: text -> [(claim, trace verdict, fact id, evidence verdict)]."""
    def trace(payload, sh):
        cl = claims[payload["card_copy"][0]]
        a = [{"claim": c, "where": "graphic", "fact_ids": [f], "claim_type": "domain_fact", "verdict": v, "note": ""}
             for c, v, f, _ in cl]
        return all(x[1] == "traced" for x in cl), 0.0, a, "x"

    def evidence(payload, sh):
        cl = claims[payload["card_copy"][0]]
        a = [{"claim": c, "where": "graphic", "claim_type": "domain_fact", "verdict": e,
              "quote_ids": sorted(str(n) for n in cf.evidence_numbers(sh, f)), "note": ""} for c, _, f, e in cl]
        return ("pass" if all(x[3] in ("supported", "hedged_ok") for x in cl) else "fail"), 0.0, a, "x"
    return trace, evidence


RING = {"topic": "The Richat Structure", "facts": [
    fact("len", "The ring is about 40 km across"),
    fact("ero", "The ring formed through erosion, not by aliens",
         evidence=[ev("https://www.britannica.com/r", 1, "The ring formed through erosion; aliens did not build it")])]}


def _myth_field(m, claims, sheet, verdict="false"):
    c = "The ring formed through erosion"
    f = {"id": "M", "myth": m, "verdict": verdict, "refuting_fact_id": "ero", "correction": c,
         "source": {"title": "b", "url": "https://www.britannica.com/r"}}
    tr, evc = _runs({m: claims, c: [(c, "traced", "ero", "supported")]})
    f["check"] = cf.check_field("myths", f, sheet, trace=tr, evidence=evc, models={"trace_check": "m1",
                                                                                    "evidence_check": "m2"})
    sheet["cards"] = {"statements": [], "myths": [f], "quotes": [], "dropped": []}
    return f


@pytest.mark.parametrize("m", ["The ring is about 40 km across and aliens built it",
                               "Aliens built the ring, which is about 40 km across"])
def test_compound_myth_with_a_true_clause_is_unusable(m):
    """Blocking 1: a myth whose checker audit traces one clause and contradicts another is no verified-false myth;
    and the NLP guard catches the true clause on its own even when the record calls the whole myth false."""
    sh = json.loads(json.dumps(RING))
    f = _myth_field(m, [("The ring is about 40 km across", "traced", "len", "supported"),
                        ("aliens built it", "contradicts", "ero", "contradicted")], sh)
    assert f["check"]["verdict"] != "supported" and f["check"]["parts"]["myth"] != "contradicted"
    for size in ((1080, 1920), (1920, 1080)):
        with pytest.raises(dc.CardRefused) as e:
            dc.plan_myth_fact(dc.MythFact(myth_src=dc.CardRef("myths", "M")), sh, *size)
        assert e.value.code == "unsupported_field"
    sh2 = json.loads(json.dumps(RING))         # a record that says every claim is false: the guard still refuses
    _myth_field(m, [(m, "contradicts", "ero", "contradicted")], sh2)
    with pytest.raises(dc.CardRefused) as e:
        dc.plan_myth_fact(dc.MythFact(myth_src=dc.CardRef("myths", "M")), sh2)
    assert e.value.code == "affirmed_myth"
    assert "The ring is about 40 km across" in dc.myth_clauses("The ring is about 40 km across and aliens built it")


def test_myth_verdict_needs_every_empirical_claim_false():
    sh = json.loads(json.dumps(RING))
    m = "Aliens carved the ring"
    f = _myth_field(m, [(m, "contradicts", "ero", "contradicted")], sh)
    assert f["check"]["verdict"] == "supported" and f["check"]["parts"]["myth"] == "contradicted"
    f = _myth_field(m, [(m, "contradicts", "ero", "contradicted"), ("it is old", "untraced", "ero", "unsupported")], sh)
    assert f["check"]["parts"]["myth"] == "unsupported"
    f = _myth_field("The ring is 40 km across", [("The ring is 40 km across", "overstated", "ero", "contradicted")],
                    sh, verdict="misleading")
    with pytest.raises(dc.CardRefused) as e:     # the fact with its hedge dropped is no myth to strike
        dc.plan_myth_fact(dc.MythFact(myth_src=dc.CardRef("myths", "M")), sh)
    assert e.value.code == "affirmed_myth"


@pytest.mark.parametrize("evidence,kw,ok", [
    ('Theodore Monod wrote: "It is not a crater." The geologist Jean Smith agreed.', {"role": "geologist"}, False),
    ('Theodore Monod wrote: "It is not a crater." Smith\'s Desert Atlas disagreed.', {"work": "Desert Atlas"}, False),
    ('Theodore Monod, born in 1902, wrote: "It is not a crater."', {"date": "1902"}, False),
    ('In his Desert Atlas, Smith\'s rival Theodore Monod wrote: "It is not a crater."', {"work": "Desert Atlas"}, True),
    ('In his Sahara Notebook in 1940, the geologist Theodore Monod wrote: "It is not a crater."',
     {"role": "geologist", "work": "Sahara Notebook", "date": "1940"}, True),
    ('Theodore Monod, a geologist, wrote: "It is not a crater."', {"role": "geologist"}, True),
    ('"It is not a crater," said Theodore Monod in 1940.', {"date": "1940"}, True),
])
def test_quote_citation_is_tied_to_the_attribution(evidence, kw, ok):
    """Blocking 2: role, work and date belong to the clause that attributes THIS quotation to its speaker."""
    q = "It is not a crater." if not evidence.startswith('"') else "It is not a crater,"
    why = dc.quote_citation_problem(q, "Theodore Monod", kw.get("role"), kw.get("work"), kw.get("date"), evidence)
    assert (why is None) == ok, why
    sh = {"topic": "x", "facts": [fact("q", "Theodore Monod described the ring",
                                       evidence=[ev("https://www.nasa.gov/q", 1, evidence)])]}
    field = {"id": "Q", "quotation": q, "speaker": {"name": "Theodore Monod", "role": kw.get("role", "")},
             "work": kw.get("work", ""), "date": kw.get("date"), "source_url": "https://www.nasa.gov/q",
             "evidence": evidence, "fact_id": "q"}
    assert (cf.findings("quotes", field, sh) == []) == ok


def test_check_field_records_the_model_that_answered():
    """Non-blocking: the run records the model that served each checker call, not the configured one."""
    from src.llm import note_served
    sh = json.loads(json.dumps(RING))

    def tr(p, s):
        note_served("served-trace-model")
        return True, 100.0, [{"claim": "x", "where": "graphic", "fact_ids": ["len"], "claim_type": "domain_fact",
                              "verdict": "traced", "note": ""}], "ok"

    def evc(p, s):
        note_served("served-evidence-model")
        return "pass", 100.0, [{"claim": "x", "where": "graphic", "claim_type": "domain_fact", "verdict": "supported",
                                "quote_ids": ["1"], "note": ""}], "ok"
    chk = cf.check_field("statements", {"id": "S", "text": "About 40 km across", "fact_id": "len"}, sh,
                         trace=tr, evidence=evc, models={"trace_check": "configured", "evidence_check": "configured"})
    assert [r["model"] for r in chk["runs"]] == ["served-trace-model", "served-evidence-model"]


def test_balanced_wrap_never_hangs_an_article():
    f = dc.get_font("Anton", 104)
    for t in ("ALIENS BUILT THE RING AT NIGHT", "SLAVES DRAGGED EVERY BLOCK OF THE GREAT PYRAMID"):
        lines = dc._wrap(t, f, 700, 5, even=True)
        assert lines and all(ln.split()[-1].strip(",") not in ("THE", "A", "AN", "OF", "AND") for ln in lines[:-1]), lines


def _flat(tmp_path, name, rgb, size=(1400, 2400)):
    p = tmp_path / f"{name}.jpg"
    Image.new("RGB", size, rgb).save(p, quality=92)
    return p


def _off_panel_edges(d):
    """Row-to-row steps with the 9:16 myth panel's two edges (dc.MYTH_PANEL, +-2 rows) taken out."""
    d = np.array(d, float)
    for y in dc.MYTH_PANEL:
        d[max(0, y - 3):y + 2] = 0.0
    return d


def _rows(im):
    return np.asarray(im.convert("RGB")).astype(float).mean(axis=(1, 2))


def test_toned_grounds_on_flat_bright_photos(tmp_path):
    """Review item 3: no flat grey slab over a light band (no step down the whole frame), the lower half keeps a
    tone, a yellow photo turns amber (never olive), and the quote's ground is the photo, not one dark grey."""
    white, yellow = _flat(tmp_path, "w", (250, 250, 246)), _flat(tmp_path, "y", (240, 215, 40))
    for photo in (white, yellow):
        for size in ((1080, 1920), (1920, 1080)):
            for plan in (dc.plan_myth_fact(myth(photo=photo), SHEET, *size),
                         dc.plan_quote_card(quote(photo=photo), SHEET, *size),
                         dc.plan_chapter_card(chapter(photo=photo), SHEET, *size)):
                rest = plan.meta["_r"]["rest"]
                g = _rows(rest)
                d = np.diff(g)
                if plan.kind == "mythfact" and size == (1080, 1920):
                    # review 2026-11-03 (round 4, deliberate change): the 9:16 myth is one panel with crisp top and
                    # foot (dc.MYTH_PANEL); no other step anywhere
                    d = _off_panel_edges(d)
                assert np.abs(d).max() <= 3, (plan.kind, size, np.abs(d).max())
                assert g.max() - g.min() >= 25, (plan.kind, size)      # a shaped ground, not one flat grey
                for n, p in plan.meta["_contrast"]:
                    assert p.contrast >= dc.PLACE_TARGET, (plan.kind, n, p.contrast)
    rest = dc.plan_myth_fact(myth(photo=yellow), SHEET).meta["_r"]["rest"]
    low = np.asarray(rest).astype(float)[1400:1900].reshape(-1, 3).mean(axis=0)
    assert low[0] > low[1] * 1.12 > low[2] * 1.12, low          # amber: red over green over blue, not olive
    w_low = np.asarray(dc.plan_myth_fact(myth(photo=white), SHEET).meta["_r"]["rest"]).astype(float)[1400:1900]
    assert w_low.mean() > 45                                    # the lower half is no dead dark field
    qa = dc.plan_quote_card(quote(photo=white), SHEET).meta["_r"]["rest"]
    qb = dc.plan_quote_card(quote(photo=yellow), SHEET).meta["_r"]["rest"]
    assert np.abs(np.asarray(qa, float) - np.asarray(qb, float)).mean() > 8   # the photo shows through


def test_myth_ground_blur_hides_burned_in_text():
    assert dc.MYTH_BLUR >= 18 and dc.QUOTE_BLUR >= 18 and dc.CHAPTER_BLUR >= 18


def test_quote_marks_match_and_16x9_short_quote_is_large():
    for size in ((1080, 1920), (1920, 1080)):
        plan = dc.plan_quote_card(quote(), SHEET, *size)
        els = {e.name: e for e in plan.elements}
        assert els["quote_mark"].face == "Montserrat" and tuple(els["quote_mark"].fill) == brand.GOLD
        last = max(k for k in plan.drawn_text if k.startswith("quote_"))
        item = next(it for it in plan.meta["_r"]["items"] if it.get("name") == last)
        assert item["parts"][-1].text == "”" and tuple(item["parts"][-1].fill) == brand.GOLD
    short = dc.plan_quote_card(quote(text="It is not a crater.", fact_id="f_quote_pron", role="", work="",
                                     year=None), SHEET, 1920, 1080)
    assert short.meta["anchors"]["quote_px"] >= 128


def test_myth_169_fact_is_the_hero_and_uses_the_width():
    plan = dc.plan_myth_fact(myth(), SHEET, 1920, 1080)
    a = plan.meta["anchors"]
    assert a["myth_px"] < a["px"]
    els = [e for e in plan.elements if e.name.startswith("fact_")]
    assert min(e.box[0] for e in els) < 900


def test_chapter_916_title_is_large():
    plan = dc.plan_chapter_card(chapter(), SHEET, 1080, 1920)
    assert plan.meta["anchors"]["title_px"] >= 120


def test_a_line_a_fact_denies_is_refused():
    """Non-blocking item (forged records): even under a forged record, a chapter or hook line that states an agent a
    supported fact denies ("Aliens built the ring" over "not by aliens") is refused by the guard."""
    assert dc.denied_agents("The ring formed through erosion, not by aliens") == {"alien"}
    assert dc.denied_agents("The walls were built without mortar") == set()
    sh = {"facts": [{"id": "e", "statement": "The ring formed through erosion, not by aliens"},
                    {"id": "p", "statement": "The pyramid was not built by slaves; paid crews built it"}]}
    assert dc.denied_by_fact("Aliens built the ring", sh) == ("e", "alien")
    assert dc.denied_by_fact("Slaves built it", sh)[0] == "p"
    for ok in ("The Eye of the Sahara", "Built by paid crews", "Not built by aliens"):
        assert dc.denied_by_fact(ok, sh) is None
