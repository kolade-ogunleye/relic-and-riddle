"""The realism layer: is a stress input one production could ever hand a renderer, and inputs drawn like production's.

Three modes (run.py --mode):
- edge: the hostile generator (surfaces.build): every class, every extreme;
- realistic: inputs sampled from the real corpus's measured distributions (tools/stress/corpus_stats.json, made by
  corpus.py from the dev runs and the read-only production sample: counts and statistics only), on real hero photos;
- mutation: a real run's own copy and photos (read at run time, read-only, never committed) with ONE controlled change
  (lengthen a field, swap a figure, swap the photo, add an accent) or none (the control).

Reachability (tag): before rendering, every case's inputs (surfaces.inputs) go through the upstream gates a real
run's copy passes, the production functions themselves, read-only:
- the writer's structure lint, script_engine.validate_structure, on a real ScriptPackage with the case's field laid
  over it (only the problems the overlay adds count; a harness artefact of a partial narration is never counted:
  its word count, its closing sentence, its last punctuation);
- the schema, models.ScriptPackage / ScaleFact / CrossSection / CardStatement / CardMyth (pydantic);
- the cover headline fit, script_engine.cover_fit_problems (Reel) and cover_fit.fit_problems (YouTube);
- the card field gates, card_fields.findings (statements, myths) with the myth limit;
- the photo pool's admission, commons.MIN_PIXELS / MIN_HEIGHT / MIN_VERTICAL_CROP (a synthetic ground that passes
  them is still no photograph: it is tagged SYNTHETIC, its content only the vision review would judge).
REACHABLE: no gate stops it (data and ground). GUARDED: one does; the gate is named.
"""
from __future__ import annotations

import copy as _copy
import json
import random
import re
from functools import lru_cache
from pathlib import Path

import corpus
import generators as gen
import grounds as gr

HERE = Path(__file__).resolve().parent
ARTEFACT = re.compile(r"^(Narration is \d+ words|Narration does not end with terminal punctuation|closing_sentence is "
                      r"not the final sentence)")
SYNTHETIC_KINDS = set(gr.GROUND_KINDS) - {"hero_crop"}


# ------------------------------------------------------------------------------------------------ the base package
@lru_cache(maxsize=1)
def _base() -> tuple[dict, frozenset]:
    """A real run's verified script that parses as a ScriptPackage, and its own structure problems (subtracted)."""
    from src.models import ScriptPackage
    from src.script_engine import validate_structure
    for _src, _run, p in corpus.payloads():
        try:
            pkg = ScriptPackage.model_validate(p)
        except Exception:  # noqa: BLE001, S112  an older run's script that no longer parses is not the base
            continue
        return p, frozenset(validate_structure(pkg))
    raise RuntimeError("no real verified script parses as a ScriptPackage")


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def data_gates(case) -> list[str]:
    """The upstream gates the case's inputs fail (each "gate: problem"); [] when it would reach the renderer."""
    import surfaces

    from src.models import ScriptPackage
    from src.script_engine import validate_structure
    fam, inp = surfaces.family(case["surface"]), surfaces.inputs(case)
    base, base_problems = _base()
    d = _copy.deepcopy(base)
    out: list[str] = []
    if fam in ("cover", "youtube"):
        d["thumbnail"] = dict(inp["thumbnail"])
    elif fam == "carousel":
        d["carousel_slides"] = [dict(s) for s in inp["carousel_slides"]]
    elif fam == "story":
        d["story_slides"] = [dict(s) for s in inp["story_slides"]]
    elif fam in ("captions", "badge"):
        text = " ".join(str(w["word"]) for w in inp["words"])
        d["narration_script"] = text
        sents = _sentences(text)
        d["closing_sentence"] = sents[-1] if sents else text
        d["emphasis_words"] = None
    elif fam == "timeline":
        d["timeline"] = [{"year": int(e["year"]), "approximate": bool(e.get("approximate")), "label": e["label"]}
                         for e in inp["events"]]
    elif fam == "scale":
        d["scale_fact"] = dict(inp["fact"])
    elif fam == "section":
        d["cross_section"] = _copy.deepcopy(inp["cross_section"])
    elif fam == "numbers":
        d["stat_cards"] = [dict(c) for c in inp["stat_cards"]]
    elif fam == "route":
        d["route"] = [dict(s) for s in inp["stops"]]
    elif fam == "callout":
        d["photo_labels"] = list(inp["labels"])
    elif fam in ("endcard", "bridge"):
        d["share_line"] = inp["share_line"]
    try:
        pkg = ScriptPackage.model_validate(d)
    except Exception as e:  # noqa: BLE001  the schema itself refuses the field
        first = str(e).splitlines()
        return [f"schema (models.ScriptPackage): {' '.join(first[:3])[:200]}"]
    for p in validate_structure(pkg):
        if p in base_problems or ARTEFACT.match(p):
            continue
        out.append(f"writer lint (script_engine.validate_structure): {p[:200]}")
    if fam == "cover":
        from src.script_engine import cover_fit_problems
        out += [f"cover fit (script_engine.cover_fit_problems): {p[:200]}" for p in cover_fit_problems(pkg)]
    if fam == "youtube":
        from src import cover_fit
        from src.visual_engine import youtube_cover_copy
        cp = youtube_cover_copy({"thumbnail": inp["thumbnail"], "title": inp.get("title") or "",
                                 "narration_script": inp.get("narration") or ""})
        out += [f"cover fit (cover_fit.fit_problems, YouTube): {p[:200]}"
                for p in cover_fit.fit_problems(cp, str(inp.get("youtube_title") or ""), youtube=True, reel=False)]
    if fam in ("hook", "myth"):
        from src import card_fields
        from src.models import CardMyth, CardStatement
        if fam == "hook":
            claim = inp["claim"]
            sheet = {"facts": [{"id": "s_hook", "statement": claim, "evidence": [
                {"url": "https://www.britannica.com/s", "tier": 2, "quote": claim}]}]}
            try:
                CardStatement(id="S_x", text=" ".join(claim.split()), fact_id="s_hook")
            except Exception as e:  # noqa: BLE001
                out.append(f"schema (models.CardStatement): {str(e).splitlines()[0][:160]}")
            out += [f"card field gate (card_fields.findings statements): {p}"
                    for p in card_fields.findings("statements", {"text": claim, "fact_id": "s_hook"}, sheet)]
        else:
            m, c = inp["myth"], inp["fact"]
            url = "https://www.britannica.com/s_myth"
            sheet = {"facts": [{"id": "s_myth", "statement": c, "misconception": m, "evidence": [
                {"url": url, "tier": 2, "quote": f"It is a popular belief that {m}. {c}."}]}]}
            field = {"myth": m, "correction": c, "refuting_fact_id": "s_myth", "verdict": "false",
                     "source": {"title": url, "url": url}}
            try:
                CardMyth(id="M_x", **field)
            except Exception as e:  # noqa: BLE001
                out.append(f"schema (models.CardMyth): {str(e).splitlines()[0][:160]}")
            out += [f"card field gate (card_fields.findings myths): {p}"
                    for p in card_fields.findings("myths", field, sheet)]
    return out


def ground_tag(spec: dict, root: Path) -> tuple[str, str]:
    """(REAL | SYNTHETIC | GUARDED, why) for one ground: a real run's photo; a synthetic ground the photo pool's
    size gates would admit (no photograph: the vision review decides, not provable here); one they refuse."""
    from src import commons
    if spec["kind"] == "real":
        return "REAL", "a real run's photo"
    p = gr.make(spec, root)
    from PIL import Image
    with Image.open(p) as im:
        w, h = im.size
    why = []
    if w * h < commons.MIN_PIXELS:
        why.append(f"commons.MIN_PIXELS ({w}x{h} < {commons.MIN_PIXELS:,} px)")
    if h < commons.MIN_HEIGHT and w < commons.MIN_HEIGHT:
        why.append(f"commons.MIN_HEIGHT (both edges < {commons.MIN_HEIGHT})")
    if commons.vertical_crop_width(w, h) < commons.MIN_VERTICAL_CROP:
        why.append(f"commons.MIN_VERTICAL_CROP ({commons.vertical_crop_width(w, h)} < {commons.MIN_VERTICAL_CROP})")
    if why:
        return "GUARDED", "photo pool admission: " + "; ".join(why)
    if spec["kind"] == "hero_crop":
        return "REAL", "a crop of a real hero"
    return "SYNTHETIC", f"synthetic {spec['kind']} ground (admissible by size; no real photograph)"


GROUNDS_USED = {"cover": 1, "youtube": 1, "carousel": 4, "story": 3, "hook": 1, "chapter": 1, "myth": 1,
                "callout": 1, "endcard": 1, "bridge": 2, "endscreen": 1, "timeline": 1, "numbers": 1, "thennow": 2}


def tag(case, root: Path) -> dict:
    """{"tag", "data_gates", "ground", "ground_why"} for the case."""
    import surfaces
    try:
        gates = data_gates(case)
    except Exception as e:  # noqa: BLE001  a gate that cannot run is said, never taken for a pass
        gates = [f"HARNESS: the gates could not run ({type(e).__name__}: {e})"]
    n = GROUNDS_USED.get(surfaces.family(case["surface"]), 0)
    gtags = [ground_tag(case["ground"][k % len(case["ground"])], root) for k in range(n)]
    worst = next((t for t in gtags if t[0] == "GUARDED"), next((t for t in gtags if t[0] == "SYNTHETIC"),
                                                                gtags[0] if gtags else ("NONE", "no photo")))
    return {"tag": "GUARDED" if gates or worst[0] == "GUARDED" else "REACHABLE", "data_gates": gates,
            "data_tag": "GUARDED" if gates else "REACHABLE", "ground": worst[0], "ground_why": worst[1]}


# ------------------------------------------------------------------------------------------------ realistic mode
@lru_cache(maxsize=1)
def stats() -> dict:
    return corpus.load()


@lru_cache(maxsize=1)
def real_heroes() -> tuple[str, ...]:
    return tuple(str(p) for p in corpus.heroes())


def _pick(rnd, xs, default):
    return rnd.choice(xs) if xs else default


def _text(rnd, field: str, default_words: int = 3, caps: bool | None = None, figure: str = "") -> str:
    """Neutral words at a real field's measured word count, casing and accent rate (no corpus text is used)."""
    st = stats()["fields"].get(field) or {}
    n = int(_pick(rnd, st.get("words") or [], default_words))
    words = [rnd.choice(gen.WORDS) for _ in range(max(1, n - (len(figure.split()) if figure else 0)))]
    if rnd.random() < float(st.get("non_ascii_share") or 0):
        words[rnd.randrange(len(words))] = rnd.choice(gen.ACCENTED)
    text = " ".join(words)
    if figure:
        text = f"{figure} {text}" if rnd.random() < 0.5 else f"{text} {figure}"
    up = caps if caps is not None else rnd.random() < float(st.get("caps_share") or 0)
    return text.upper() if up else text[:1].upper() + text[1:]


def _figure(rnd) -> str:
    """A figure as the corpus's figures are: magnitude, decimals, spans, hedges and units at their measured rates."""
    f = stats()["figures"]
    n = max(1, int(f.get("n") or 1))
    mag = 10 ** float(_pick(rnd, f.get("magnitude_log10") or [], 2.0))
    dec = rnd.random() < f.get("decimals", 0) / n
    v = round(mag, 1) if dec else round(mag)
    num = f"{v:,}" if not dec else f"{v:,.1f}"
    if rnd.random() < f.get("spans", 0) / n:
        hi = v * rnd.uniform(1.05, 1.6)
        num = f"{num}–{(round(hi, 1) if dec else round(hi)):,}"
    units = f.get("units") or {"m": 1}
    unit = rnd.choices(list(units), weights=list(units.values()))[0]
    text = f"{num} {unit}"
    if rnd.random() < f.get("hedged", 0) / n:
        hedges = f.get("hedges") or {"about": 1}
        text = f"{rnd.choices(list(hedges), weights=list(hedges.values()))[0]} {text}"
    return text


def _real_ground(rnd) -> dict:
    hs = real_heroes()
    return {"kind": "real", "path": _pick(rnd, list(hs), ""), "seed": 0, "variant": 0} if hs else \
        {"kind": "noise", "seed": rnd.randrange(1 << 30), "variant": 0}


def realistic_inputs(fam: str, rnd) -> dict:
    s = stats()
    if fam in ("cover", "youtube"):
        fig = _figure(rnd).upper() if rnd.random() < 0.5 else ""
        th = {"eyebrow": _text(rnd, "thumbnail.eyebrow", 2), "line1": _text(rnd, "thumbnail.line1", 2),
              "line2": _text(rnd, "thumbnail.line2", 2),
              "tagline": fig or _text(rnd, "thumbnail.tagline", 3)}
        narration = " ".join(f"{_text(rnd, 'carousel.body', 12, caps=False)}." for _ in range(4)) + \
            f" It reaches {_figure(rnd) if not fig else fig.lower()}."
        return {"thumbnail": th, "title": _text(rnd, "title", 8, caps=False), "narration": narration,
                "hook_sentence": "", "closing_sentence": ""}
    if fam == "carousel":
        n = int(_pick(rnd, s["fields"].get("carousel.count", {}).get("words") or [], 7))
        slides = [{"heading": _text(rnd, "carousel.heading", 3),
                   "body": _text(rnd, "carousel.body", 12, caps=False, figure=_figure(rnd) if k == 2 else "") + "."}
                  for k in range(n)]
        return {"carousel_slides": slides, "title": _text(rnd, "title", 8, caps=False),
                "narration": " ".join(f"{x['body']}" for x in slides), "share_line": _text(rnd, "share_line", 14,
                                                                                           caps=False) + ".",
                "closing_sentence": slides[-1]["body"]}
    if fam == "story":
        slides = [{"kicker": _text(rnd, "story.kicker", 2), "line1": _text(rnd, "story.line1", 2),
                   "line2": _text(rnd, "story.line2", 2),
                   "body": _text(rnd, "story.body", 12, caps=False, figure=_figure(rnd) if k == 1 else "") + "."}
                  for k in range(3)]
        return {"story_slides": slides, "title": _text(rnd, "title", 8, caps=False),
                "narration": " ".join(x["body"] for x in slides)}
    if fam in ("captions", "badge"):
        import surfaces
        sents = []
        for k in range(rnd.randint(4, 8)):
            body = _text(rnd, "carousel.body", 12, caps=False, figure=_figure(rnd) if k % 3 == 1 else "")
            sents.append(body + rnd.choice(".....?"))
        return {"words": surfaces._words(" ".join(sents))}
    if fam == "hook":
        return {"claim": _text(rnd, "stat.label", 3, caps=False, figure=_figure(rnd)), "eyebrow": "",
                "first_burst": _text(rnd, "carousel.body", 8, caps=False)}
    if fam == "chapter":
        num = rnd.randint(1, 5)
        return {"title": " ".join(w.capitalize() for w in _text(rnd, "title", 5, caps=False).split()[:6]),
                "number": num, "total": max(num, 5)}
    if fam == "myth":
        # the corpus holds no myth field (0 myths on the sheets): a myth and its correction at the lengths a card
        # field is written to (the cards' own text limits), neutral words
        m = " ".join(rnd.choice(gen.WORDS) for _ in range(rnd.randint(3, 6))).capitalize()
        lead = max(m.split(), key=len)
        fact = f"{lead.capitalize()} {' '.join(rnd.choice(gen.WORDS) for _ in range(rnd.randint(3, 6)))}"
        return {"myth": m, "fact": fact}
    if fam == "callout":
        return {"labels": [_text(rnd, "photo_labels", 2, caps=False).lower() for _ in range(3)]}
    if fam in ("endcard", "bridge", "endscreen"):
        # the writer's share line as its gate shapes it (a share ask naming who, a follow), at the corpus's length
        st = stats()["fields"].get("share_line") or {}
        n = int(_pick(rnd, st.get("words") or [], 15))
        who = " ".join(rnd.choice(gen.WORDS) for _ in range(max(1, n - 12)))
        if rnd.random() < float(st.get("non_ascii_share") or 0):
            who += " of " + rnd.choice(gen.ACCENTED)
        limit = int(_pick(rnd, st.get("chars") or [], 89))
        line = f"Send this to the friend who loves {who} {rnd.choice(gen.WORDS)}s, and follow for the next one."
        while len(line) > limit and len(who.split()) > 1:
            who = " ".join(who.split()[:-1])
            line = f"Send this to the friend who loves {who} {rnd.choice(gen.WORDS)}s, and follow for the next one."
        return {"share_line": line}
    if fam == "map":
        bank = json.loads((corpus.TREE / "data" / "topics" / "topic_bank.json").read_text(encoding="utf-8"))
        locs = [t["location"] for t in bank.get("topics") or [] if (t.get("location") or {}).get("latitude")]
        loc = rnd.choice(locs)
        return {"place": {"lat": float(loc["latitude"]), "lon": float(loc["longitude"]),
                          "country": loc.get("country") or "", "label": loc.get("name") or "",
                          "approximate": False}}
    if fam == "route":
        import surfaces
        places = surfaces.ROUTE_PLACES[:]
        rnd.shuffle(places)
        return {"stops": [{"place": p, "country": c, "label": _text(rnd, "route.label", 4, caps=False)}
                          for p, c in places[:rnd.randint(2, 3)]]}
    if fam == "timeline":
        tl = s["timeline"]
        n = int(_pick(rnd, tl.get("events") or [], 3))
        years = sorted(rnd.sample(list(dict.fromkeys(tl.get("years") or list(range(1000, 2020)))), min(n, 3)))
        while len(years) < n:
            years.append(years[-1] + rnd.randint(1, 80))
        appr = tl.get("approximate", 0) / max(1, sum(tl.get("events") or [1]))
        return {"events": [{"year": y, "approximate": rnd.random() < appr,
                            "label": _text(rnd, "timeline.label", 4, caps=False)} for y in years]}
    if fam == "scale":
        sc = s["scale"]
        m = float(_pick(rnd, sc.get("metres") or [], 50.0)) * rnd.uniform(0.8, 1.25)
        units = sc.get("units") or {"m": 1}
        unit = rnd.choices(list(units), weights=list(units.values()))[0]
        dims = sc.get("dimensions") or {"height": 1}
        return {"fact": {"value": round(m / 0.3048 if unit == "ft" else m, 1), "unit": unit,
                         "dimension": rnd.choices(list(dims), weights=list(dims.values()))[0],
                         "subject": _text(rnd, "scale.subject", 3, caps=False)[:28],
                         "approximate": rnd.random() < sc.get("approximate", 1) / max(1, sc.get("n", 1))}}
    if fam == "section":
        # the corpus has no cross-section (0 of 68 runs): the schema's own limits (2-5 levels, ordered, within the
        # total), depths whole at the fact sheets' decimal rate
        fsd = s["fact_sheets"]
        dec = fsd.get("statement_decimals", 0) / max(1, fsd.get("facts", 1))
        total = float(rnd.choice((60, 120, 280, 600, 1200)))
        n = rnd.randint(2, 5)
        depths = sorted(rnd.uniform(0.05, 1.0) * total for _ in range(n))
        depths = [round(d, 1) if rnd.random() < dec else float(round(d)) for d in depths]
        return {"cross_section": {"subject": _text(rnd, "scale.subject", 2, caps=False)[:28],
                                  "total_depth_ft": total, "approximate": True,
                                  "levels": [{"depth_ft": d, "label": _text(rnd, "timeline.label", 4, caps=False)[:32]}
                                             for d in depths]}}
    if fam == "numbers":
        n = int(_pick(rnd, s["stat_cards"].get("cards") or [], 2))
        return {"stat_cards": [{"value": _figure(rnd)[:22], "label": _text(rnd, "stat.label", 3, caps=False)[:40]}
                               for _ in range(n)]}
    if fam == "thennow":
        return {"years": [rnd.randint(1850, 1960), rnd.randint(2005, 2024)]}
    raise ValueError(fam)


def build_realistic(surface: str, count: int, seed: int) -> list[dict]:
    import surfaces
    out = []
    for i in range(count):
        rnd = random.Random(f"realistic:{surface}:{seed}:{i}")
        fam = surfaces.family(surface)
        inp = realistic_inputs(fam, rnd)
        case = {"surface": surface, "id": f"{surface}-R{seed}-{i:04d}", "fmt": surfaces._fmt(surface), "mode":
                "realistic", "inputs": inp, "copy": [{"cls": "realistic", "text": ""}],
                "figure": {"cls": "realistic", "text": "", "signed": ""},
                "ground": [_real_ground(rnd) for _ in range(4)], "rnd": rnd.randrange(1 << 30)}
        if fam in ("cover", "youtube"):
            case["style"] = ("badge", "monument", "minimal", "depth_sandwich", "lower_third")[i % 5]
        if fam == "scale":
            case["scale"] = {"cls": "realistic", "fact": inp["fact"]}
        if fam == "map":
            case["map"] = {"cls": "realistic", "place": inp["place"]}
        _signed_narration(case)
        out.append(case)
    return out


def _signed_narration(case) -> None:
    """Every figure the realistic copy draws stated in its narration, as a real run's narration states them."""
    inp = case["inputs"]
    if "narration" in inp:
        texts = []
        for k in ("thumbnail", "carousel_slides", "story_slides"):
            v = inp.get(k)
            if isinstance(v, dict):
                texts += [str(x) for x in v.values()]
            elif isinstance(v, list):
                texts += [str(x) for s in v for x in s.values()]
        inp["narration"] = inp["narration"] + " " + " ".join(f"{t.strip().rstrip('.')}." for t in texts if t.strip())


# ------------------------------------------------------------------------------------------------ mutation mode
MUTATIONS = ("control", "lengthen", "swap_figure", "swap_photo", "add_accent")
_ACCENT = {"A": "Á", "E": "É", "I": "Í", "O": "Ó", "U": "Ú", "a": "ã", "e": "é", "i": "í", "o": "ó", "u": "ü",
           "N": "Ñ", "n": "ñ", "C": "Ç", "c": "ç"}


@lru_cache(maxsize=1)
def _real_runs() -> tuple:
    """(source, run dir, payload, photos) of every real run whose topic has its photo pool here (read-only)."""
    out = []
    for src, run, p in corpus.payloads():
        topic = str(p.get("topic_id") or run.name[4:].rsplit("_", 1)[0])
        root = (corpus.DEV if src == "dev" else corpus.PROD) / "assets" / topic
        photos = sorted(str(x) for x in root.glob("[0-9][0-9]_commons.jpg"))[:6] if root.is_dir() else []
        out.append((src, str(run), p, tuple(photos)))
    return tuple(out)


def _lengthen(text: str, rnd) -> str:
    words = text.split()
    add = max(1, len(words) // 2)
    extra = [rnd.choice(gen.WORDS) for _ in range(add)]
    if text.isupper():
        extra = [w.upper() for w in extra]
    return " ".join(words + extra)


def _accent(text: str, rnd) -> str:
    idx = [i for i, ch in enumerate(text) if ch in _ACCENT]
    if not idx:
        return text
    i = rnd.choice(idx)
    return text[:i] + _ACCENT[text[i]] + text[i + 1:]


_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _swap_number(text: str, rnd) -> tuple[str, str, str]:
    """`text` with its first number replaced by another magnitude: (new text, old number, new number)."""
    m = _NUM.search(text)
    if not m:
        return text, "", ""
    old = m.group(0)
    try:
        v = float(old.replace(",", ""))
    except ValueError:
        return text, "", ""
    nv = v * rnd.choice((0.01, 0.1, 7.3, 13, 1000))
    new = f"{nv:,.1f}" if ("." in old or nv < 1) else f"{round(nv):,}"
    return text[:m.start()] + new + text[m.end():], old, new


def mutation_inputs(fam: str, real: tuple, kind: str, rnd) -> tuple[dict | None, str]:
    """(inputs, what changed) from one real run, or (None, why not) when the run has nothing for this surface."""
    _src, run, p, _photos = real
    narration = str(p.get("narration_script") or "")
    inp: dict
    if fam in ("cover", "youtube"):
        th = dict(p.get("thumbnail") or {})
        if not th:
            return None, "no thumbnail copy"
        inp = {"thumbnail": th, "title": p.get("title") or "", "narration": narration,
               "hook_sentence": p.get("hook_sentence") or "", "closing_sentence": p.get("closing_sentence") or "",
               "youtube_title": p.get("youtube_title") or ""}
        targets = [("thumbnail", k) for k in ("eyebrow", "line1", "line2", "tagline")]
    elif fam == "carousel":
        sl = [dict(s) for s in p.get("carousel_slides") or []]
        if not sl:
            return None, "no carousel slides"
        inp = {"carousel_slides": sl, "title": p.get("title") or "", "narration": narration,
               "share_line": p.get("share_line") or "", "closing_sentence": p.get("closing_sentence") or ""}
        targets = [("carousel_slides", i, k) for i in range(len(sl)) for k in ("heading", "body")]
    elif fam == "story":
        sl = [dict(s) for s in p.get("story_slides") or []][:3]
        if len(sl) < 2:
            return None, "no story slides"
        inp = {"story_slides": sl, "title": p.get("title") or "", "narration": narration}
        targets = [("story_slides", i, k) for i in range(min(2, len(sl))) for k in ("kicker", "line1", "line2", "body")]
    elif fam in ("captions", "badge"):
        wj = Path(run) / "words.json"
        if not wj.is_file():
            return None, "no words.json"
        words = [dict(w) for w in json.loads(wj.read_text(encoding="utf-8"))]
        inp = {"words": words, "narration": narration}
        targets = [("words", i) for i in range(len(words))]
    elif fam == "timeline":
        evs = [dict(e) for e in p.get("timeline") or []]
        if not evs:
            return None, "no timeline"
        inp = {"events": evs}
        targets = [("events", i, "label") for i in range(len(evs))]
    elif fam == "scale":
        if not p.get("scale_fact"):
            return None, "no scale fact"
        inp = {"fact": dict(p["scale_fact"])}
        targets = [("fact", "subject")]
    elif fam == "numbers":
        cards = [dict(c) for c in p.get("stat_cards") or []]
        if not cards:
            return None, "no stat cards"
        inp = {"stat_cards": cards}
        targets = [("stat_cards", i, k) for i in range(len(cards)) for k in ("value", "label")]
    elif fam == "callout":
        labels = list(p.get("photo_labels") or [])
        if len(labels) < 2:
            return None, "no photo labels"
        inp = {"labels": labels[:3]}
        targets = [("labels", i) for i in range(len(inp["labels"]))]
    elif fam in ("endcard", "bridge", "endscreen"):
        if not p.get("share_line"):
            return None, "no share line"
        inp = {"share_line": p["share_line"]}
        targets = [("share_line",)]
    elif fam == "route":
        stops = [dict(s) for s in p.get("route") or []]
        if len(stops) < 2:
            return None, "no route"
        inp = {"stops": stops}
        targets = [("stops", i, "label") for i in range(len(stops))]
    elif fam == "map":
        bank = json.loads((corpus.TREE / "data" / "topics" / "topic_bank.json").read_text(encoding="utf-8"))
        loc = next((t["location"] for t in bank.get("topics") or [] if t.get("id") == p.get("topic_id")), None)
        if not loc or not loc.get("latitude"):
            return None, "topic not in the bank with coordinates"
        inp = {"place": {"lat": float(loc["latitude"]), "lon": float(loc["longitude"]),
                         "country": loc.get("country") or "", "label": loc.get("name") or "", "approximate": False}}
        targets = [("place", "label")]
    else:
        return None, (f"the corpus carries no real {fam} fields (cards: 1 statement, 0 myths, 0 act titles; "\
                     "sections: 0 of 68 runs; then-and-now: no run fields)")
    if kind in ("control", "swap_photo"):
        return inp, kind
    if fam in ("captions", "badge"):     # one spoken word changed, its time kept (never a word squeezed in)
        words = inp["words"]
        if kind == "swap_figure":
            hits = [i for i, w in enumerate(words) if _NUM.search(str(w["word"]))]
            if not hits:
                return inp, "swap_figure: no figure in the narration (control)"
            i = rnd.choice(hits)
            new, old, nv = _swap_number(str(words[i]["word"]), rnd)
            words[i]["word"] = new
            if old and inp.get("narration"):
                inp["narration"] = re.sub(r"(?<![\d,.])" + re.escape(old) + r"(?![\d,]|\.\d)", nv,
                                          inp["narration"], count=1)
            return inp, f"swap the figure {old} -> {nv} in word {i}"
        i = rnd.randrange(len(words))
        w = str(words[i]["word"])
        if kind == "lengthen":
            core = w.rstrip(".,;:!?")
            words[i]["word"] = f"{core}-{rnd.choice(gen.LONG_WORDS).lower()}{w[len(core):]}"
            what = f"lengthen word {i} into a long compound"
        else:
            words[i]["word"] = _accent(w, rnd)
            what = f"add an accent to word {i}"
        if inp.get("narration"):
            inp["narration"] = inp["narration"].replace(w, words[i]["word"], 1)
        return inp, what
    t = rnd.choice(targets)
    cur = inp
    for k in t[:-1]:
        cur = cur[k]
    key = t[-1]
    if fam in ("captions", "badge"):
        w = inp["words"][key]
        text = str(w["word"])
    else:
        text = str(cur[key])
    if kind == "lengthen":
        new = _lengthen(text, rnd)
        what = f"lengthen {'/'.join(map(str, t))} ({len(text.split())} -> {len(new.split())} words)"
    elif kind == "add_accent":
        new = _accent(text, rnd)
        what = f"add an accent to {'/'.join(map(str, t))}"
    else:   # swap_figure: the first number anywhere in the surface's fields, the narration kept signing it
        flat = json.dumps(inp, ensure_ascii=False)
        m = _NUM.search(text) or None
        if m is None:
            hits = [tt for tt in targets if _NUM.search(str(_get(inp, tt)))]
            if not hits:
                return inp, "swap_figure: no figure on this surface (control)"
            t = rnd.choice(hits)
            text = str(_get(inp, t))
        new, old, nv = _swap_number(text, rnd)
        what = f"swap the figure {old} -> {nv} in {'/'.join(map(str, t))}"
        if "narration" in inp and old:
            # the same figure where the narration states it, matched whole (never inside a longer number)
            inp["narration"] = re.sub(r"(?<![\d,.])" + re.escape(old) + r"(?![\d,]|\.\d)", nv, inp["narration"],
                                      count=1)
        _ = flat
    if fam in ("captions", "badge"):
        inp["words"][t[-1]]["word"] = new if kind != "lengthen" else new.split()[0]
        if kind == "lengthen":   # spoken words inserted after it, each with its own time
            w = inp["words"][t[-1]]
            extra = [{"word": x, "start": round(float(w["start"]) + 0.01 * (j + 1), 3), "dur": 0.2}
                     for j, x in enumerate(new.split()[1:])]
            inp["words"][t[-1] + 1:t[-1] + 1] = extra
    else:
        _set(inp, t, new)
    return inp, what


def _get(inp, t):
    cur = inp
    for k in t:
        cur = cur[k]
    return cur


def _set(inp, t, v):
    cur = inp
    for k in t[:-1]:
        cur = cur[k]
    cur[t[-1]] = v


def build_mutation(surface: str, count: int, seed: int) -> tuple[list[dict], list[str]]:
    """Mutation cases and, for a surface the corpus cannot feed, why not."""
    import surfaces
    fam = surfaces.family(surface)
    reals = list(_real_runs())
    out, skipped = [], []
    tries = 0
    i = 0
    while len(out) < count and tries < count * 6:
        tries += 1
        rnd = random.Random(f"mutation:{surface}:{seed}:{tries}")
        real = reals[(seed + tries * 7) % len(reals)]
        kind = MUTATIONS[i % len(MUTATIONS)]
        inp, what = mutation_inputs(fam, real, kind, rnd)
        if inp is None:
            skipped.append(what)
            if fam in ("hook", "chapter", "myth", "section", "thennow"):
                break
            continue
        photos = list(real[3]) or list(real_heroes())
        if kind == "swap_photo":
            other = reals[(seed + tries * 13 + 5) % len(reals)][3] or real_heroes()
            photos = list(other)
            what = f"swap the photos for another run's ({Path(other[0]).parent.name if other else '?'})"
        case = {"surface": surface, "id": f"{surface}-M{seed}-{len(out):04d}", "fmt": surfaces._fmt(surface),
                "mode": "mutation", "inputs": inp, "mutation": kind, "mutation_what": what,
                "real_run": f"{real[0]}:{Path(real[1]).name}", "copy": [{"cls": f"mutation:{kind}", "text": ""}],
                "figure": {"cls": f"mutation:{kind}", "text": "", "signed": ""},
                "ground": [{"kind": "real", "path": photos[k % len(photos)], "seed": 0, "variant": 0}
                           for k in range(4)] if photos else [{"kind": "noise", "seed": 1, "variant": 0}],
                "rnd": rnd.randrange(1 << 30)}
        if fam in ("cover", "youtube"):
            case["style"] = ("badge", "monument", "minimal", "depth_sandwich", "lower_third")[len(out) % 5]
        if fam == "scale":
            case["scale"] = {"cls": "mutation", "fact": inp["fact"]}
        if fam == "map":
            case["map"] = {"cls": "mutation", "place": inp["place"]}
        out.append(case)
        i += 1
    return out, sorted(set(skipped))


# ------------------------------------------------------------------------------------------------ re-tag a run
def retag(parts: list[Path], seed: int, root: Path) -> dict:
    """Every result of an edge run (its report.partial.jsonl files) with its case rebuilt (surfaces.build at the
    run's own count per surface, deterministic) and tagged: {id: {"status", "failures", "reach"}}."""
    import surfaces
    results: dict[str, dict] = {}
    for part in parts:
        for line in part.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                results[r["id"]] = r
    per: dict[str, int] = {}
    for r in results.values():
        per[r["surface"]] = per.get(r["surface"], 0) + 1
    out = {}
    for surface, n in sorted(per.items()):
        for case in surfaces.build(surface, n, seed):
            r = results.get(case["id"])
            if r is None:
                continue
            out[case["id"]] = {"surface": surface, "status": r["status"], "failures": r["failures"],
                               "refusal": r.get("refusal"), "reach": tag(case, root)}
    return out


if __name__ == "__main__":
    import argparse
    import sys
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("retag",))
    ap.add_argument("parts", type=Path, nargs="+")
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--tree", type=Path, default=HERE.parents[1])
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    sys.path.insert(0, str(a.tree))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    got = retag(a.parts, a.seed, a.out.parent / "_grounds")
    a.out.write_text(json.dumps(got, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(f"{len(got)} results tagged -> {a.out}")
