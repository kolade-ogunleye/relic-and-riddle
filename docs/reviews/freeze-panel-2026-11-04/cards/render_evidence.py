"""Freeze-panel evidence (2026-11-04): the hook frame, chapter card and myth-vs-fact card (src/design_cards.py)
rendered from one code tree on REAL run inputs only (operator rule), through the catalogue in ../fixtures.

    python render_evidence.py --code-root <tree> --out <dir> [--only hook,chapter,myth] [--match <text>]

<tree> goes first on sys.path, so `src` and the test helpers come from it (run once on the committed HEAD worktree
and once on the working tree). Photos, fact sheets, titles and narration are read from the main checkout through
fixtures.py (by path), never invented:

- grounds: each run's own scene photos (provenance.json source photos; generated beat images left out), measured
  here as the catalogue measures them (512 px long side, Rec. 601 luma, PIL FIND_EDGES energy), and the darkest,
  brightest and busiest taken per run; plus the catalogue's verified hero where the topic has one; plus no photo.
- hook claims: the catalogue's hook_claim_candidates that fit a claim (run titles), on their own run's photos,
  with the first narration burst from that run's words.json.
- chapter titles: the Richat deep dive's own act titles and act count (verified_deep_dive.json), every act on
  every ground (operator decision 2026-11-05, "chapter cards: draw all of them": the catalogue's fits_chapter, the
  retired 32-character limit, no longer limits an act to one ground; a tree that still refuses shows its refusal).
- myths: the catalogue lists no myth field anywhere (gap). The three facts that state a refuted belief
  (damascus-13, kailasa-legend-kokasa, Richat fact-9) seed myth fields built only from those facts' own words
  (each field's derivation is in the manifest).

Card fields: no checked card field exists in any run (catalogue gap), so every card here draws a field that is
NEW: a real fact's words, put on a temporary copy of that topic's real fact sheet through the real
card_fields.check_field path, with the checkers' verdicts supplied by the test suite's mocked checker
(tests/test_design_cards_2026_11_03.py: install / gate; no model is called) and signed under a throwaway test key,
never the .env key. Every image carries the label "new: real fact, test-signed field" (and any other "new" reason)
in a band below the frame; the frame above the band is the card's pixel-exact frame. Quote cards stay held.

Writes <out>/png/<name>.png (frame + label band), <out>/jpg/<name>.jpg (half size) and <out>/manifest[-...].json
(per card: run, topic, inputs, labels, the plan's anchors, px, line counts, source_omitted, drawn contrast on the
measured frames, or the refusal reason).
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import io
import json
import re
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parent / "fixtures" / "fixtures.py"
MAX_BYTES = 2 * 1024 * 1024
NEW_FIELD = "new: real fact, test-signed field"
THROWAWAY_KEY = "freeze-panel-evidence-throwaway-key-0123456789abcdef"   # never the deployment's FACT_RECORD_KEY


def parse() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--code-root", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--only", default="", help="comma list of card kinds: hook,chapter,myth")
    ap.add_argument("--match", default="", help="only the cards whose name contains this")
    return ap.parse_args()


ARGS = parse()
CODE = ARGS.code_root.resolve()
sys.path.insert(0, str(CODE))

_spec = importlib.util.spec_from_file_location("fixtures", FIXTURES)
fx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fx)
REPO = fx.REPO

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFilter, ImageFont  # noqa: E402

from src import config  # noqa: E402

config.FACT_RECORD_KEY = THROWAWAY_KEY
config.QUOTE_CARDS_ENABLED = False           # quote cards stay held; none is rendered here
for _name in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GEMINI_FREE_API_KEY"):
    if hasattr(config, _name):
        setattr(config, _name, "")

from src import design_cards as dc  # noqa: E402
import tests.test_design_cards_2026_11_03 as T  # noqa: E402   (the code tree's own install / gate helpers)

assert Path(dc.__file__).resolve().is_relative_to(CODE), dc.__file__
SIZES = {"916": (1080, 1920), "169": (1920, 1080)}
CAT = fx.section("cards")


# ---------------------------------------------------------------------------------------------------- real inputs
def measure(p: Path) -> dict:
    im = Image.open(p).convert("RGB")
    im.thumbnail((512, 512))
    a = np.asarray(im, np.float32)
    lum = a[..., 0] * .299 + a[..., 1] * .587 + a[..., 2] * .114
    e = np.asarray(Image.fromarray(lum.astype(np.uint8)).filter(ImageFilter.FIND_EDGES), np.float32)
    s = np.asarray(im.convert("HSV"), np.float32)[..., 1]
    return {"mean_luma": round(float(lum.mean()), 1), "edge_energy": round(float(e.mean()), 1),
            "mean_saturation": round(float(s.mean()), 1)}


def run_photos(run: str) -> list[tuple[Path, dict]]:
    """The run's own scene photos (the first source photo of each scene in provenance.json), measured."""
    prov = fx.json_of(f"output/{run}/provenance.json")
    out, seen = [], set()
    for a in prov.get("assets") or []:
        cands = [Path(q) for q in (a.get("source_paths") or [])] + [Path(a.get("path") or "")]
        for q in cands:
            if q.suffix.lower() in (".jpg", ".jpeg", ".png") and q.is_file() and "beat_images" not in q.parts \
                    and "anchor" not in q.stem:
                if q not in seen:
                    seen.add(q)
                    out.append((q, measure(q)))
                break
    return out


def grounds(run: str, topic: str) -> list[tuple[str, Path | None, dict]]:
    """[(tag, photo, measured)]: the run's darkest, brightest and busiest scene photo, the catalogue's verified
    hero of the topic when it is another photo, and no photo."""
    ph = run_photos(run)
    tags: dict[Path, list[str]] = {}
    for tag, key, pick in (("dark", "mean_luma", min), ("bright", "mean_luma", max), ("busy", "edge_energy", max)):
        p = pick(ph, key=lambda x: x[1][key])[0]
        tags.setdefault(p, []).append(tag)
    for h in CAT.get("hero_photos") or []:
        hp = Path(h["photo"])
        if h["topic"] == topic and hp.is_file() and hp not in tags:
            tags[hp] = ["hero"]
    m = dict(ph)
    out = [("+".join(t), p, m.get(p) or measure(p)) for p, t in tags.items()]
    return out + [("plain", None, {})]


def first_burst(run: str, n: int = 6) -> str:
    """The run's first narration burst: its first words up to a pause of 0.25 s or `n` words (words.json)."""
    ws = fx.json_of(f"output/{run}/words.json")
    out = [ws[0]["word"]]
    for a, b in zip(ws, ws[1:]):
        if len(out) >= n or b["start"] - (a["start"] + a.get("dur", 0)) > 0.25:
            break
        out.append(b["word"])
    return " ".join(out)


def run_sheet(topic: str) -> dict:
    """A temporary copy of the topic's real fact sheet (the card fields are put on the copy only)."""
    return copy.deepcopy(fx.sheet(topic))


_STOP = {"the", "a", "an", "of", "in", "on", "at", "to", "by", "for", "and", "or", "from", "with", "lost"}


def _cw(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.casefold()) if w not in _STOP}


def cited_fact(sheet: dict, text: str) -> str:
    """The supported, undisputed fact whose statement shares the most of the title's words (figures first)."""
    facts = [f for f in sheet["facts"] if not f.get("disputed") and str(f.get("verdict") or "") in ("", "supported")]
    nums = set(re.findall(r"\d[\d,]*", text))
    return max(facts, key=lambda f: (len(nums & set(re.findall(r"\d[\d,]*", f["statement"]))),
                                     len(_cw(text) & _cw(f["statement"]))))["id"]


def refuted(fid: str) -> dict:
    for s in CAT["sheets"]:
        for f in s.get("facts_naming_a_refuted_belief") or []:
            if f["fact_id"] == fid:
                return {"topic": s["topic"], "sheet": str(s["sheet"]), **f}
    raise KeyError(fid)


# Myth fields seeded from the three facts that state a refuted belief. Every word is the fact's own (the topic name
# "The Richat Structure" is fact-1's subject); only elisions, never added claims. Lengths are the planner's to judge:
# a field past a format's limit is planned anyway, so its refusal is shown.
MYTHS = [
    {"label": "richat_short", "run": {"916": "run_richat_structure_sahara_eye_1792737039_short",
                                      "169": "run_richat_structure_sahara_eye_1792740961_long"},
     "fact": "fact-9", "myth": "The Richat Structure is an impact crater",
     "correction": ["The impact theory was disproved in 1969", "The impact theory was disproved"],
     "derivation": "myth: fact-1's subject + fact-9 'an ... impact crater' ('extraterrestrial' elided); correction "
                   "(M12): fact-9 'the impact theory was disproved' + 'in 1969' (the clause 'when suspected "
                   "shock-metamorphism coesite was identified' elided), kept only if ground_text accepts it, else "
                   "fact-9 'the impact theory was disproved' verbatim (which then carries the quality note "
                   "correction_drops_specifics)",
     "pending": "pending operator approval: elided correction"},
    {"label": "richat_long", "run": {"916": "run_richat_structure_sahara_eye_1792737039_short",
                                     "169": "run_richat_structure_sahara_eye_1792740961_long"},
     "fact": "fact-9", "myth": "The Richat Structure is an impact crater",
     "correction": "Impact theory disproved when suspected coesite was identified in 1969 as barite",
     "derivation": "correction (round 4, draws 1969, meaning kept): fact-9's own words in their own order, elisions "
                   "only: the leading 'the', the main clause's 'was', 'shock-metamorphism' and 'mislabeled' (79 "
                   "chars). 'suspected' is kept, so the suspected coesite is what proved to be barite, as the source "
                   "states ('barite had been misidentified as coesite'). It fits 9:16 with its source line (round 3's "
                   "83-char 'Impact theory was disproved when ...' did not). ground_text accepts any elision: the "
                   "meaning is a human check, and the field awaits the operator's approval",
     "pending": "pending operator approval: elided correction"},
    {"label": "richat_nodate", "run": {"916": "run_richat_structure_sahara_eye_1792737039_short",
                                       "169": "run_richat_structure_sahara_eye_1792740961_long"},
     "fact": "fact-9", "myth": "The Richat Structure is an impact crater",
     "correction": "The impact theory was disproved when suspected coesite was identified as barite",
     "derivation": "round 3's richat_long, kept for M10's 4-line 16:9 measurement: fact-9 with 'shock-metamorphism', "
                   "'in 1969' and 'mislabeled' elided (79 chars), 'suspected' kept; it drops the date, so it carries "
                   "the quality note correction_drops_specifics",
     "pending": "pending operator approval: elided correction"},
    {"label": "richat_longest", "run": {"916": "run_richat_structure_sahara_eye_1792737039_short",
                                        "169": "run_richat_structure_sahara_eye_1792740961_long"},
     "fact": "fact-9", "myth": "The Richat Structure is an extraterrestrial impact crater",
     "correction": "Impact theory disproved when suspected coesite was identified in 1969 as barite",
     "derivation": "the longest fact-9 myth (57 chars: fact-1's subject + fact-9 'an extraterrestrial impact crater') "
                   "with richat_long's 1969 correction (79). 9:16 refuses the myth (limit 56), as it should; 16:9 "
                   "plans it. Awaits the operator's approval",
     "pending": "pending operator approval: elided correction"},
    {"label": "damascus", "run": {"916": "run_damascus_steel_nanotubes_1792732472_short",
                                  "169": "run_damascus_steel_nanotubes_1792732472_short"},
     "fact": "damascus-13", "myth": "Damascus blades were quenched in the body of an enslaved person",
     "correction": "The legend that Damascus blades were quenched originated as a prank article",
     "derivation": "myth: damascus-13 with 'by thrusting them six times into' shortened to 'in'; correction: "
                   "damascus-13 with the quench clause elided",
     "new169": "new: 16:9 myth of a Reel topic (its run is 9:16)"},
    {"label": "kailasa", "run": {"916": "run_kailasa_temple_monolith_1792524470",
                                 "169": "run_kailasa_temple_monolith_1792524470"},
     "fact": "kailasa-legend-kokasa", "myth": "Kokasa carved the structure within a week's time",
     "correction": "A medieval Marathi legend recounts that Kokasa carved the structure",
     "derivation": "myth and correction: kailasa-legend-kokasa's own words, 'an architect named' and 'from the "
                   "summit down so the queen could view the completed shikhara' elided (the fact reports the legend; "
                   "it states no refutation)",
     "new169": "new: 16:9 myth of a Reel topic (its run is 9:16)"},
]


# ---------------------------------------------------------------------------------------------------- output
OUT = ARGS.out
(OUT / "png").mkdir(parents=True, exist_ok=True)
(OUT / "jpg").mkdir(parents=True, exist_ok=True)
MANIFEST: dict[str, dict] = {}


def _font(px: int):
    for name in ("arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    return ImageFont.load_default()


def labelled(im: Image.Image, lines: list[str]) -> Image.Image:
    """The frame, untouched, over a band that names the run / topic and every "new" reason."""
    f = _font(max(22, im.width // 46))
    band = 16 + len(lines) * int(f.size * 1.35) + 8
    out = Image.new("RGB", (im.width, im.height + band), (20, 20, 22))
    out.paste(im.convert("RGB"), (0, 0))
    d = ImageDraw.Draw(out)
    y = im.height + 12
    for ln in lines:
        d.text((16, y), ln, font=f, fill=(250, 196, 90) if ln.startswith("new:") else (225, 225, 225))
        y += int(f.size * 1.35)
    return out


def save(name: str, im: Image.Image, lines: list[str]) -> dict:
    """PNG master (palette-quantised only if a full-colour PNG would pass 2 MB) and a half-size JPEG."""
    im = labelled(im, lines)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    master = "rgb"
    if buf.tell() > MAX_BYTES:
        buf = io.BytesIO()
        im.quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(buf, "PNG", optimize=True)
        master = "quantised-256 (full-colour PNG over 2 MB)"
    (OUT / "png" / f"{name}.png").write_bytes(buf.getvalue())
    half = im.resize((im.width // 2, im.height // 2), Image.Resampling.LANCZOS)
    half.save(OUT / "jpg" / f"{name}.jpg", quality=88, optimize=True)
    return {"png": f"{name}.png", "jpg": f"{name}.jpg", "png_master": master}


def write_manifest(seconds: float | None = None) -> None:
    """Written after every card, so a run cut short still leaves its record."""
    meta = {"code_root": str(CODE), "design_cards": dc.__file__, "version": dc.VERSION, "seconds": seconds,
            "inputs": "real run media and fact-sheet facts via docs/reviews/freeze-panel-2026-11-04/fixtures",
            "card_fields": NEW_FIELD + ": checker verdicts from the test suite's mocked checker, signed under "
                                       "THROWAWAY_KEY (this rig's own constant, assigned over config's key before "
                                       "any card module loads; the deployment key is never read) in a "
                                       "temporary sheet copy",
            "throwaway_key": "THROWAWAY_KEY (render_evidence.py)",
            "catalogue_gaps": CAT.get("verdict")}
    stem = "manifest" + ("-" + "-".join(sorted(k for k in ARGS.only.split(",") if k)) if ARGS.only else "") + (
        "-" + ARGS.match if ARGS.match else "")
    (OUT / f"{stem}.json").write_text(json.dumps({"meta": meta, "renders": MANIFEST}, indent=1, default=str,
                                                 ensure_ascii=False), encoding="utf-8", newline="\n")


def drawn_contrast(plan, frame) -> float | None:
    vals = []
    for _name, p in plan.meta.get("_contrast") or []:
        try:
            v = p.measure(frame)
        except Exception:
            v = None
        if v is not None:
            vals.append(float(v))
    return round(min(vals), 2) if vals else None


STOPS = getattr(dc, "W1_STOPS", frozenset())


def groups(plan) -> dict[str, list]:
    """The planned display lines by group (title_k, myth_k, fact_k), each [(text, element)] in order."""
    out: dict[str, list] = {}
    for e in plan.elements:
        head, _, k = e.name.rpartition("_")
        if head in ("title", "myth", "fact") and k.isdigit() and e.kind == "text":
            out.setdefault(head, []).append((int(k), e.text, e))
    return {g: [(t, e) for _k, t, e in sorted(v)] for g, v in out.items()}


def summary(plan) -> dict:
    m = plan.meta
    anchors = {k: v for k, v in (m.get("anchors") or {}).items() if not k.startswith("_")}
    k = plan.width / 1080 if plan.width < plan.height else min(plan.width / 1920, plan.height / 1080)
    lines, stop_end, gaps = {}, {}, {}
    for g, ls in groups(plan).items():
        lines[g] = [t for t, _e in ls]
        stop_end[g] = any(t.split() and t.split()[-1].lower().strip(".,;:") in STOPS for t, _e in ls[:-1])
        # A1: each line's ink top below the previous line's baseline (design px; >= 16 wanted where accented)
        gaps[g] = [round((b.box[1] - a.xy[1]) / k, 1) for (_ta, a), (_tb, b) in zip(ls, ls[1:])]
    els = {e.name: e for e in plan.elements}
    total = None
    if "total" in els:
        t = els["total"]
        ref = els.get("kicker_label") if t.face == "Montserrat" else els.get("numeral")
        total = {"text": t.text, "px": t.px, "face": t.face, "fill": "#%02X%02X%02X" % tuple(t.fill),
                 "placement": anchors.get("total_placement"), "box": list(t.box),
                 "gap_to_ref_ink": round((t.box[0] - ref.box[2]) / k, 1) if ref else None,
                 "baseline_vs_ref": round(t.xy[1] - ref.xy[1], 1) if ref else None}
    return {"size": [plan.width, plan.height], "seconds": plan.seconds, "frames": plan.frames,
            "anchors": json.loads(json.dumps(anchors, default=str)),
            "drawn_text": dict(plan.drawn_text), "lines": lines, "stop_end": stop_end, "accent_gaps": gaps,
            "total_mark": total, "quality_notes": m.get("quality_notes"), "source_host_raw": m.get("source_host_raw"),
            "t_s": m.get("t_s"), "t_f": m.get("t_f"), "pen_total": m.get("pen_total"),
            "source_omitted": m.get("source_omitted"),
            "planned_drawn_contrast_min": m.get("drawn_contrast_min"),
            "elements": [{"name": e.name, "box": list(e.box), "px": e.px} for e in plan.elements]}


def refusal_image(size, title: str, reason: str) -> Image.Image:
    im = Image.new("RGB", size, (40, 12, 12))
    d = ImageDraw.Draw(im)
    f = _font(max(24, size[0] // 30))
    y = size[1] // 4
    width = 40 if size[0] < size[1] else 64
    for line in ["REFUSED", title] + [reason[i:i + width] for i in range(0, min(len(reason), width * 10), width)]:
        d.text((size[0] // 12, y), line, font=f, fill=(240, 220, 200))
        y += int(f.size * 1.5)
    return im


def run(name: str, size, build, frames_of, info: dict, measure_at=None) -> None:
    """build() -> plan; frames_of(plan) -> [(suffix, frame index)]. A refusal is recorded and drawn as a slate."""
    if ARGS.match and ARGS.match not in name:
        return
    lines = [f"{info['run']}  |  {info['topic']}", f"{info.get('ground', '')}  {info.get('photo_name', '')}".strip()]
    lines += [lb for lb in info.get("labels", [])]
    t0 = time.time()
    rec: dict = {"name": name, **info}
    try:
        plan = build()
    except dc.CardRefused as e:
        rec.update(refused=e.reason, code=e.code)
        rec.update(save(name + "__last", refusal_image(size, name, e.reason), lines))
        MANIFEST[name] = rec
        write_manifest()
        print(f"REFUSED {name}: {e.reason}", flush=True)
        return
    except Exception as e:  # an error in the code under test is evidence too
        rec.update(error=f"{type(e).__name__}: {e}", trace=traceback.format_exc()[-1500:])
        rec.update(save(name + "__last", refusal_image(size, name, f"{type(e).__name__}: {e}"), lines))
        MANIFEST[name] = rec
        write_manifest()
        print(f"ERROR {name}: {type(e).__name__}: {e}", flush=True)
        return
    rec.update(summary(plan))
    rec["renders"] = {}
    for suffix, i in frames_of(plan):
        i = max(0, min(plan.frames - 1, int(i)))
        fr = dc.frame_at(plan, i)
        r = save(f"{name}__{suffix}", fr, lines + [f"frame {i}  t {i / dc.VIDEO_FPS:.2f} s"])
        r.update(frame=i, t=round(i / dc.VIDEO_FPS, 3))
        if (measure_at or (lambda p, k: k == p.frames - 1))(plan, i):
            r["drawn_contrast_min"] = drawn_contrast(plan, fr)
        rec["renders"][suffix] = r
    rec["seconds_to_render"] = round(time.time() - t0, 1)
    MANIFEST[name] = rec
    write_manifest()
    print(f"ok {name} ({rec['seconds_to_render']} s)", flush=True)


def ground_info(tag, photo, m) -> dict:
    return {"ground": tag, "photo": str(photo) if photo else None,
            "photo_name": photo.name if photo else "(no photo)", "photo_measured": m}


# ---------------------------------------------------------------------------------------------------- cards
# Hook claims as statement fields from real facts (no run title passes the claim guard: see the refusals below).
# Each is the fact's own words, at most 6 words and 30 characters; the run is the topic's hook-candidate run.
HOOK_FIELDS = [
    {"run": "run_amber_room_heist_1792801257", "topic": "amber_room_heist", "fact": "amber-room-nazi-looting-1941",
     "claim": "Disassembled in 36 hours"},
    {"run": "run_amber_room_heist_1792801257", "topic": "amber_room_heist", "fact": "amber-room-nazi-looting-1941",
     "claim": "Shipped to Königsberg Castle"},
    {"run": "run_nazca_lines_runway_paradox_1793247464", "topic": "nazca_lines_runway_paradox",
     "fact": "nazca-location", "claim": "Etched into the Nazca Desert"},
    {"run": "run_nazca_lines_runway_paradox_1793247464", "topic": "nazca_lines_runway_paradox",
     "fact": "nazca-unesco-listing", "claim": "Inscribed in 1994"},
    {"run": "run_tunguska_event_1908_1792930600", "topic": "tunguska_event_1908", "fact": "tunguska-atmos-07",
     "claim": "Rippled across the globe"},
]


def _hook(name, run_name, topic, claim, fid, src, gs, labels, extra):
    burst = first_burst(run_name)
    for tag, photo, m in gs:
        info = {"run": run_name, "topic": topic, "card": "hook", "format": "916", "claim": claim,
                "claim_chars": len(claim), "claim_words": len(claim.split()), "cited_fact": fid,
                "fact_statement": fx.sheet(topic) and next(f["statement"] for f in fx.sheet(topic)["facts"]
                                                           if f["id"] == fid),
                "first_burst": burst, **extra, **ground_info(tag, photo, m), "labels": labels}

        def build(claim=claim, fid=fid, photo=photo, burst=burst):
            sh = run_sheet(topic)
            spec = dc.HookFrame(claim=claim, fact_ids=(fid,), photo=photo, first_burst=burst,
                                claim_src=src(claim, fid, sh))
            return dc.plan_hook_frame(spec, sh)
        # frame 0 (the poster), 32 (last full-alpha frame), 37 (mid dissolve, H4)
        run(f"{name}_{tag}", (1080, 1920), build, lambda p: [("f00", 0), ("f32", 32), ("f37", 37)], info,
            measure_at=lambda p, k: k in (0, 32))


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.casefold()).strip("_")[:28]


def do_hooks():
    for h in HOOK_FIELDS:
        gs = [g for g in grounds(h["run"], h["topic"]) if g[1] is not None]   # the hook is always on its photo
        _hook(f"hook_916_{_slug(h['claim'])}", h["run"], h["topic"], h["claim"], h["fact"],
              lambda c, f, sh: T.statement(c, f, sh), gs, [NEW_FIELD + f" (statement from {h['fact']})"],
              {"claim_source": "statement field"})
    # the catalogue's real hook candidates (run titles), gated with a test record: each shown once, on its run's
    # darkest photo, so the claim guard's verdict on real copy is on record
    for cand in CAT.get("hook_claim_candidates") or []:
        run_name, topic = Path(str(cand["run"])).name, cand["topic"]
        fid = cited_fact(run_sheet(topic), cand["text"])
        gs = [g for g in grounds(run_name, topic) if g[1] is not None][:1]
        _hook(f"hooktitle_916_{_slug(cand['text'])}", run_name, topic, cand["text"], fid,
              lambda c, f, sh: T.gate(c, sh), gs, [NEW_FIELD + " (real run title, test-gated)"],
              {"claim_source": "gated title", "fits_claim": cand.get("fits_claim")})


def do_chapters():
    dd_run = None
    for c in CAT.get("chapter_title_candidates") or []:
        dd_run = Path(str(c["run"])).name
    acts = fx.json_of(f"output/{dd_run}/verified_deep_dive.json")["acts"]
    total = len(acts)
    topic = CAT["chapter_title_candidates"][0]["topic"]
    gs = grounds(dd_run, topic)
    for c in CAT["chapter_title_candidates"]:
        n, title = int(c["act"]), c["title"]
        for fmt, size in SIZES.items():
            for tag, photo, m in gs:
                labels = [NEW_FIELD + " (real act title, test-gated)"]
                if fmt == "916":
                    labels.append("new: 9:16 chapter card of the 16:9 deep dive's act")
                info = {"run": dd_run, "topic": topic, "card": "chapter", "format": fmt, "number": n, "total": total,
                        "title": title, "title_chars": len(title), "fits_chapter": c.get("fits_chapter"),
                        **ground_info(tag, photo, m), "labels": labels}

                def build(n=n, title=title, photo=photo, size=size):
                    sh = run_sheet(topic)
                    spec = dc.ChapterCard(number=n, total=total, title=title, photo=photo,
                                          title_src=T.gate(title, sh))
                    return dc.plan_chapter_card(spec, sh, *size)
                run(f"chapter_{fmt}_act{n}_{tag}", size, build,
                    lambda p: [("mid", p.frames // 3), ("last", p.frames - 1)], info)


def myth_frames(plan):
    fps = dc.VIDEO_FPS
    last = plan.frames - 1
    t_s, t_f = plan.meta["t_s"], plan.meta["t_f"]
    return [("a_before_strike", round((t_s - 0.10) * fps)),
            ("b_mid_strike", round((t_s + dc.STRIKE_PEN / 2) * fps)),
            ("c_after_strike", round((t_f + 0.05) * fps)),
            ("d_fact_revealed", last)]


def pick_correction(mf: dict, topic: str) -> tuple[str, list[dict]]:
    """The first of the field's candidate corrections the code tree's own ground_text accepts (M12 (4))."""
    cands = mf["correction"] if isinstance(mf["correction"], list) else [mf["correction"]]
    tried = []
    for c in cands:
        try:
            dc.ground_text(c, run_sheet(topic), [mf["fact"]], role="fact")
        except dc.CardRefused as e:
            tried.append({"correction": c, "ground_text": "refused: " + e.reason})
            continue
        tried.append({"correction": c, "ground_text": "accepted"})
        return c, tried
    return cands[-1], tried


def do_myths():
    for mf in MYTHS:
        base = refuted(mf["fact"])
        topic = base["topic"]
        mf = dict(mf)
        mf["correction"], mf["correction_tried"] = pick_correction(mf, topic)
        for fmt, size in SIZES.items():
            run_name = mf["run"][fmt]
            labels = ([NEW_FIELD + f" (myth field from {mf['fact']})"] + ([mf["new169"]] if fmt == "169" and
                                                                           mf.get("new169") else [])
                      + ([mf["pending"]] if mf.get("pending") else []))
            for tag, photo, m in grounds(run_name, topic):
                info = {"run": run_name, "topic": topic, "card": "myth", "format": fmt, "refuting_fact": mf["fact"],
                        "fact_statement": base["statement"], "myth": mf["myth"], "myth_chars": len(mf["myth"]),
                        "correction": mf["correction"], "correction_chars": len(mf["correction"]),
                        "correction_tried": mf["correction_tried"],
                        "derivation": mf["derivation"], **ground_info(tag, photo, m), "labels": labels}

                def build(mf=mf, photo=photo, size=size):
                    sh = run_sheet(topic)
                    spec = T.myth(sheet=sh, myth_text=mf["myth"], fact_text=mf["correction"], fact_id=mf["fact"],
                                  photo=photo, verdict="false")
                    return dc.plan_myth_fact(spec, sh, *size)
                run(f"myth_{fmt}_{mf['label']}_{tag}", size, build, myth_frames, info)


if __name__ == "__main__":
    only = {k for k in ARGS.only.split(",") if k}
    t0 = time.time()
    if not only or "hook" in only:
        do_hooks()
    if not only or "chapter" in only:
        do_chapters()
    if not only or "myth" in only:
        do_myths()
    secs = round(time.time() - t0, 1)
    write_manifest(secs)
    n_ref = sum(1 for r in MANIFEST.values() if r.get("refused"))
    n_err = sum(1 for r in MANIFEST.values() if r.get("error"))
    print(f"done: {len(MANIFEST)} cards, {n_ref} refused, {n_err} errors, {secs} s")
