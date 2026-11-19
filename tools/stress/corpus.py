"""The real corpus, measured: counts and statistics only (never copy text), for the stress generator's realism layer.

    python tools/stress/corpus.py --out tools/stress/corpus_stats.json

Read-only sources (none is ever written):
- the dev checkout's output/run_*/verified_script.json and words.json (RR_STRESS_DEV, default the dev checkout);
- the production sample C:\\tmp\\prod-export-data\\output\\run_*/ (RR_STRESS_PROD), hash-verified, read-only;
- the tree's data/fact_sheets, data/script_library, data/topics/topic_bank.json and data/geo_cache.json;
- hero photos (01-03) of every topic under both assets folders, for luma / texture statistics.

What it keeps (corpus_stats.json): per copy field the word and character counts, the share set in capitals, the share
carrying a non-ASCII letter and the scripts seen; figure magnitudes (log10), decimals, spans, hedges and units as
figures.parse reads them; timeline event counts and years; scale facts in metres and whether a reference landmark
exists for them (graphics.pick_reference); cross-section level counts and decimal depths; stat-card counts and value
lengths; narration casing (all-caps words, capitalised hedges); place-name character sets (topic bank, place cache);
photo luma mean / spread and edge density. Numbers and category names only.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
TREE = HERE.parents[1]
DEV = Path(os.environ.get("RR_STRESS_DEV") or r"C:\Users\omokolade\GitHub\relic-and-riddle")
PROD = Path(os.environ.get("RR_STRESS_PROD") or r"C:\tmp\prod-export-data")
HEDGE_WORDS = ("about", "around", "roughly", "nearly", "almost", "more than", "over", "up to", "at least", "some",
               "estimated", "between", "approximately", "as many as")


def runs() -> list[tuple[str, Path]]:
    """(source, run dir) of every run with a verified script: dev first, then the production sample."""
    out = []
    for src, root in (("dev", DEV / "output"), ("prod", PROD / "output")):
        if root.is_dir():
            out += [(src, p) for p in sorted(root.glob("run_*")) if (p / "verified_script.json").is_file()]
    return out


def payloads():
    for src, run in runs():
        try:
            yield src, run, json.loads((run / "verified_script.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue


def heroes(limit: int = 3) -> list[Path]:
    out = []
    for root in (DEV / "assets", PROD / "assets"):
        if root.is_dir():
            for topic in sorted(p for p in root.iterdir() if p.is_dir()):
                out += sorted(topic.glob("0[1-9]_commons.jpg"))[:limit]
    return out


def scripts_of(text: str) -> set[str]:
    """The writing systems of the letters in `text` (Latin, Latin-ext, Greek, Cyrillic, Arabic, CJK, other)."""
    got = set()
    for ch in str(text or ""):
        if not ch.isalpha():
            continue
        if ch.isascii():
            got.add("latin")
            continue
        name = unicodedata.name(ch, "")
        for key, tag in (("LATIN", "latin_ext"), ("GREEK", "greek"), ("CYRILLIC", "cyrillic"), ("ARABIC", "arabic"),
                         ("HEBREW", "hebrew"), ("CJK", "cjk"), ("HIRAGANA", "cjk"), ("KATAKANA", "cjk"),
                         ("HANGUL", "cjk")):
            if name.startswith(key):
                got.add(tag)
                break
        else:
            got.add("other")
    return got


def is_caps(text: str) -> bool:
    letters = [c for c in str(text or "") if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)


class Field:
    """One copy field's measurements over the corpus."""

    def __init__(self):
        self.words, self.chars, self.caps, self.non_ascii, self.empty = [], [], 0, 0, 0
        self.scripts: Counter = Counter()

    def add(self, text) -> None:
        t = str(text or "")
        if not t.strip():
            self.empty += 1
            return
        self.words.append(len(t.split()))
        self.chars.append(len(t))
        self.caps += is_caps(t)
        self.non_ascii += any(c.isalpha() and not c.isascii() for c in t)
        self.scripts.update(scripts_of(t))

    def stats(self) -> dict:
        n = len(self.words)
        return {"n": n, "empty": self.empty, "words": sorted(self.words), "chars": sorted(self.chars),
                "caps_share": round(self.caps / n, 3) if n else None,
                "non_ascii_share": round(self.non_ascii / n, 3) if n else None, "scripts": dict(self.scripts)}


def measure() -> dict:
    sys.path.insert(0, str(TREE))
    from src import figures, graphics
    fields: dict[str, Field] = {}

    def f(name: str) -> Field:
        return fields.setdefault(name, Field())
    fig = {"magnitude_log10": [], "decimals": 0, "spans": 0, "spans_descending": 0, "hedged": 0, "n": 0,
           "units": Counter(), "hedges": Counter(), "eras": Counter()}
    tl = {"events": [], "years": [], "approximate": 0, "n_events": 0, "descending_sets": 0, "bc_events": 0}
    sc = {"metres": [], "units": Counter(), "dimensions": Counter(), "approximate": 0, "n": 0, "no_reference": 0,
          "no_reference_metres": []}
    cs = {"levels": [], "total_ft": [], "decimal_depths": 0, "depths": 0, "n": 0, "negative": 0}
    stat = {"cards": [], "value_chars": []}
    narr = {"words": [], "caps_words": 0, "caps_words_len3plus": 0, "tokens": 0, "caps_hedges": 0, "runs": 0,
            "caps_hedge_examples_count_by_hedge": Counter()}
    words_json = {"files": 0, "tokens": 0, "caps_tokens_len3plus": 0, "caps_hedges": 0}
    heads = {"carousel_two_word_plus_accent": 0, "accented_headings": 0, "headings": 0}
    yt = {"thumbnail_tagline_spans": 0, "thumbnail_figures_ge_1000": 0, "thumbnails": 0}
    per_source = Counter()
    for src, run, p in payloads():
        per_source[src] += 1
        th = p.get("thumbnail") or {}
        yt["thumbnails"] += bool(th)
        for k in ("eyebrow", "line1", "line2", "tagline"):
            f(f"thumbnail.{k}").add(th.get(k))
        for figure in figures.parse(" ".join(str(th.get(k) or "") for k in ("line1", "line2", "tagline"))):
            yt["thumbnail_tagline_spans"] += bool(figure.hi)
            try:
                yt["thumbnail_figures_ge_1000"] += float(str(figure.lo).replace(",", "")) >= 1000
            except ValueError:
                pass
        f("title").add(p.get("title"))
        f("share_line").add(p.get("share_line"))
        for s in p.get("carousel_slides") or []:
            f("carousel.heading").add(s.get("heading"))
            f("carousel.body").add(s.get("body"))
            h = str(s.get("heading") or "")
            heads["headings"] += 1
            acc = any(c.isalpha() and not c.isascii() for c in h)
            heads["accented_headings"] += acc
            heads["carousel_two_word_plus_accent"] += acc and len(h.split()) >= 2
        f("carousel.count").add(" ".join("x" for _ in p.get("carousel_slides") or []))
        for s in p.get("story_slides") or []:
            for k in ("kicker", "line1", "line2", "body"):
                f(f"story.{k}").add(s.get(k))
        for x in p.get("photo_labels") or []:
            f("photo_labels").add(x)
        text = str(p.get("narration_script") or "") or " ".join(str(a.get("narration_text") or "")
                                                                  for a in p.get("acts") or [])
        if text:
            narr["runs"] += 1
            toks = text.split()
            narr["words"].append(len(toks))
            narr["tokens"] += len(toks)
            for t in toks:
                w = t.strip(".,;:!?\"'()\u2019\u2018\u201c\u201d")
                if len(w) >= 2 and is_caps(w):
                    narr["caps_words"] += 1
                    narr["caps_words_len3plus"] += len(w) >= 3
            for h in HEDGE_WORDS:
                n = len(re.findall(r"\b" + re.escape(h.upper()) + r"\b", text))
                narr["caps_hedges"] += n
                if n:
                    narr["caps_hedge_examples_count_by_hedge"][h] += n
        for a in p.get("acts") or []:
            f("act_title").add(a.get("act_title"))
        sources = [text] + [str(c.get("value") or "") for c in p.get("stat_cards") or []]
        for figure in figures.parse(" ".join(sources)):
            fig["n"] += 1
            try:
                v = float(str(figure.lo).replace(",", ""))
                if v > 0:
                    fig["magnitude_log10"].append(round(math.log10(v), 2))
            except ValueError:
                pass
            fig["decimals"] += "." in str(figure.lo)
            fig["spans"] += bool(figure.hi)
            if figure.hi:
                try:
                    fig["spans_descending"] += float(str(figure.hi).replace(",", "")) < float(
                        str(figure.lo).replace(",", ""))
                except ValueError:
                    pass
            fig["hedged"] += bool(figure.hedge)
            if figure.hedge:
                fig["hedges"][str(figure.hedge).lower()] += 1
            if figure.unit:
                fig["units"][str(figure.unit).lower()] += 1
        for e in re.findall(r"\b(BCE|BC|AD|CE)\b", text):
            fig["eras"][e] += 1
        evs = p.get("timeline") or []
        if evs:
            tl["n_events"] += 1
            tl["events"].append(len(evs))
            years = [int(e.get("year") or 0) for e in evs]
            tl["years"] += years
            tl["descending_sets"] += years != sorted(years)
            tl["bc_events"] += sum(y < 0 for y in years)
            tl["approximate"] += sum(bool(e.get("approximate")) for e in evs)
            for e in evs:
                f("timeline.label").add(e.get("label"))
        s = p.get("scale_fact") or {}
        if s.get("value") is not None:
            sc["n"] += 1
            m = float(s["value"]) * (0.3048 if s.get("unit") == "ft" else 1.0)
            sc["metres"].append(round(m, 2))
            sc["units"][s.get("unit")] += 1
            sc["dimensions"][s.get("dimension")] += 1
            sc["approximate"] += bool(s.get("approximate", True))
            f("scale.subject").add(s.get("subject"))
            if graphics.pick_reference(m / 0.3048) is None:
                sc["no_reference"] += 1
                sc["no_reference_metres"].append(round(m, 2))
        c = p.get("cross_section") or {}
        if c.get("levels") is not None:
            cs["n"] += 1
            cs["levels"].append(len(c["levels"]))
            cs["total_ft"].append(float(c.get("total_depth_ft") or 0))
            f("section.subject").add(c.get("subject"))
            for lv in c["levels"]:
                d = float(lv.get("depth_ft") or 0)
                cs["depths"] += 1
                cs["decimal_depths"] += d != int(d)
                cs["negative"] += d < 0
                f("section.label").add(lv.get("label"))
        cards = p.get("stat_cards") or []
        if cards:
            stat["cards"].append(len(cards))
            for k in cards:
                stat["value_chars"].append(len(str(k.get("value") or "")))
                f("stat.label").add(k.get("label"))
                f("stat.value").add(k.get("value"))
        for stop in p.get("route") or []:
            f("route.label").add(stop.get("label"))
        wj = run / "words.json"
        if wj.is_file():
            try:
                ws = json.loads(wj.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                ws = []
            words_json["files"] += 1
            joined = " ".join(str(w.get("word") or "") for w in ws)
            for w in ws:
                t = str(w.get("word") or "").strip(".,;:!?\"'()")
                words_json["tokens"] += 1
                words_json["caps_tokens_len3plus"] += len(t) >= 3 and is_caps(t)
            words_json["caps_hedges"] += sum(len(re.findall(r"\b" + re.escape(h.upper()) + r"\b", joined))
                                             for h in HEDGE_WORDS)
    # fact sheets (the tree's, plus the library's): figure decimals in signed statements, card field lengths
    sheets = {"facts": 0, "statement_decimals": 0, "myths": 0, "statements": 0}
    for sp in list((TREE / "data" / "fact_sheets").glob("*.json")) + list((TREE / "data" / "script_library").glob(
            "*/fact_sheet.json")):
        try:
            sh = json.loads(sp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for fact in sh.get("facts") or []:
            sheets["facts"] += 1
            sheets["statement_decimals"] += bool(re.search(r"\d\.\d", str(fact.get("statement") or "")))
        cards = sh.get("cards") or {}
        for st in cards.get("statements") or []:
            sheets["statements"] += 1
            f("card.statement").add(st.get("text"))
        for m in cards.get("myths") or []:
            sheets["myths"] += 1
            f("card.myth").add(m.get("myth"))
            f("card.correction").add(m.get("correction"))
    places = Field()
    bank = json.loads((TREE / "data" / "topics" / "topic_bank.json").read_text(encoding="utf-8"))
    for t in bank.get("topics") or []:
        loc = t.get("location") or {}
        for k in ("name", "city", "region", "country"):
            places.add(loc.get(k))
            f(f"place.{k}").add(loc.get(k))
        f("place.region_missing").add("" if loc.get("region") else "x")
        f("place.name_equals_country").add("x" if str(loc.get("name") or "").strip().lower() ==
                                           str(loc.get("country") or "").strip().lower() else "")
    try:
        cache = json.loads((TREE / "data" / "geo_cache.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    for v in cache.values():
        if isinstance(v, dict):
            f("place.cache_label").add(v.get("label"))
    photos = photo_stats(heroes())
    return {"sources": dict(per_source), "fields": {k: v.stats() for k, v in sorted(fields.items())},
            "figures": {**fig, "units": dict(fig["units"]), "hedges": dict(fig["hedges"]), "eras": dict(fig["eras"]),
                        "magnitude_log10": sorted(fig["magnitude_log10"])},
            "timeline": {**tl, "events": sorted(tl["events"]), "years": sorted(tl["years"])},
            "scale": {**sc, "units": dict(sc["units"]), "dimensions": dict(sc["dimensions"]),
                      "metres": sorted(sc["metres"])},
            "section": {**cs, "levels": sorted(cs["levels"]), "total_ft": sorted(cs["total_ft"])},
            "stat_cards": {"cards": sorted(stat["cards"]), "value_chars": sorted(stat["value_chars"])},
            "narration": {**narr, "words": sorted(narr["words"]),
                          "caps_hedge_examples_count_by_hedge": dict(narr["caps_hedge_examples_count_by_hedge"])},
            "words_json": words_json, "headings": heads, "thumbnail_figures": yt, "fact_sheets": sheets,
            "places": places.stats(), "photos": photos}


def photo_stats(paths: list[Path]) -> dict:
    import numpy as np
    from PIL import Image
    lum, spread, edges, aspect = [], [], [], []
    for p in paths:
        try:
            with Image.open(p) as im:
                aspect.append(round(im.width / max(1, im.height), 3))
                g = np.asarray(im.convert("L").resize((256, 256)), np.float32)
        except OSError:
            continue
        lum.append(round(float(g.mean()), 1))
        spread.append(round(float(g.std()), 1))
        gx, gy = np.abs(np.diff(g, axis=1)), np.abs(np.diff(g, axis=0))
        edges.append(round(float(((gx[:-1] > 24) | (gy[:, :-1] > 24)).mean()), 3))
    return {"n": len(lum), "luma_mean": sorted(lum), "luma_std": sorted(spread), "edge_density": sorted(edges),
            "aspect": sorted(aspect)}


def load(path: Path | None = None) -> dict:
    return json.loads((path or HERE / "corpus_stats.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=HERE / "corpus_stats.json")
    a = ap.parse_args()
    stats = measure()
    a.out.write_text(json.dumps(stats, indent=1, sort_keys=True), encoding="utf-8", newline="\n")
    print(f"{sum(stats['sources'].values())} runs ({stats['sources']}), {stats['photos']['n']} photos -> {a.out}")
