"""QF-94 acceptance measure: characters removed from drawn fields, fallbacks recorded, refusals with a reason.

    python docs/reviews/quality-fixes-2026-11/QF-94/measure.py <repository root> [<production sample copy>]

Runs on the base and on the row's commit (it calls only what both trees have, and reads the new API through
getattr). Committed inputs: real_fields.json beside this file (every text field the dev runs drew on a card, their
photo credits and the signed card fields; extract_real_fields.py), the stress list below and fonts/. The production
sample is never committed: give a temporary copy of it (a folder holding output/run_*/...) as the second argument
and its fields are read at run time (extract_real_fields.collect) and reported as counts only (no text, run names or
author names); while it runs, log records are counted and withheld (stderr gets their count by level, never their
text). Prints JSON; writes nothing outside a temporary folder it removes.

1. real fields and the stress list, in each family a card sets text in (Anton, Montserrat 600, Cinzel 700, IBM Plex
   Mono 500): characters typography.drawable removes (emoji aside: the art gate removes those, recorded), and the
   same through typography.fit_text (the lines a card draws, joined), fields set in a fallback face, fields
   refused, and whether each fallback and refusal is in the run's glyph ledger.
2. the faces a fallback is matched to (font_engine.fallback_match) and the cap-height difference at 80 px.
3. Arabic: which bundled faces map the Arabic block, and how the tracked path (one HarfBuzz buffer) orders a mixed
   Latin and Arabic run: the evidence for refusing an Arabic field rather than drawing it.
"""

import json
import logging
import os
import socket
import sys
import tempfile
from collections import Counter
from pathlib import Path

FAMILIES = (("Anton", None), ("Montserrat", 600), ("Cinzel", 700), ("IBM Plex Mono", 500))
STRESS = ["ΑΓΙΑ ΣΟΦΙΑ", "Αγία Σοφία", "ΓΝΩΘΙ ΣΑΥΤΟΝ", "Ἀκρόπολις", "Москва", "КИРИЛЛИЦА", "Янтарная комната",
          "Ħal Saflieni Hypogeum", "ĦAMRUN ħ", "İstanbul, Ağrı ılık şişe", "IĞDIR ŞANLIURFA", "Hà Nội, Việt Nam",
          "THÀNH PHỐ HỒ CHÍ MINH", "Nguyễn Trãi", "Łódź Ő ő ā Tōhoku ß", "Hagia Sophia (Αγία Σοφία)",
          "Photo: Андрей Андреевич Зеест · CC BY-SA 4.0", "ابن الهيثم", "Ibn al-Haytham (ابن الهيثم)",
          "صلاح الدين", "Photo: ولاء · CC BY-SA 4.0"]


def _offline(*_a, **_k):
    raise RuntimeError("measure.py is offline")


class _Withhold(logging.Handler):
    """Counts log records by level and emits none (the production pass prints counts only)."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.counts: Counter = Counter()

    def emit(self, record):
        self.counts[record.levelname] += 1


def main(root: Path, production: Path | None = None) -> None:
    root = root.resolve()
    os.environ.setdefault("FONT_SIZE", "80")
    socket.create_connection = _offline
    socket.socket.connect = _offline
    sys.path.insert(0, str(root))
    import uharfbuzz as hb
    from fontTools.ttLib import TTFont
    from PIL import Image, ImageDraw, features

    from src import font_engine as FE
    from src import typography as T
    assert features.check("raqm"), "raqm is required (FriBiDi on PATH)"
    here = Path(__file__).resolve().parent
    real = json.loads((here / "real_fields.json").read_text(encoding="utf-8"))
    refused_type = getattr(FE, "GlyphRefused", None)
    has_ledger = hasattr(FE, "set_glyph_ledger")
    probe = ImageDraw.Draw(Image.new("L", (8, 8)))
    tmp = Path(tempfile.mkdtemp(prefix="qf94-measure-"))
    ledger = tmp / "glyph_faces.jsonl"
    if has_ledger:
        FE.set_glyph_ledger(ledger)

    def letters(text):
        return Counter(ch for ch in T.ART_EMOJI.sub("", text) if not ch.isspace())

    def removed(text, out):
        return sum((letters(text) - Counter(ch for ch in out if not ch.isspace())).values())

    def run(texts):
        out = {"fields": len(texts), "non_ascii_fields": 0, "family_checks": 0, "chars_removed": 0,
               "fields_changed": 0, "fit_chars_removed": 0, "fallback": 0, "refused": 0, "by_family": {},
               "examples": [], "refused_reasons": [], "fallbacks": []}
        for text in texts:
            if text.isascii():
                out["family_checks"] += len(FAMILIES)       # ASCII is drawn as it is by every tree
                continue
            out["non_ascii_fields"] += 1
            for family, weight in FAMILIES:
                out["family_checks"] += 1
                fam = out["by_family"].setdefault(family, {"chars_removed": 0, "fallback": 0, "refused": 0})
                font = T.get_font(family, 60, weight=weight)
                try:
                    drawn = T.drawable(text, font)
                except Exception as e:
                    if refused_type is None or not isinstance(e, refused_type):
                        raise
                    out["refused"] += 1
                    fam["refused"] += 1
                    out["refused_reasons"].append({"family": family, "text": text, "reason": e.reason})
                    continue
                cut = removed(text, drawn)
                out["chars_removed"] += cut
                fam["chars_removed"] += cut
                if cut:
                    out["fields_changed"] += 1
                    if len(out["examples"]) < 12:
                        out["examples"].append({"family": family, "field": text, "drawn": drawn})
                lines, fit_font, _short = T.fit_text(probe, text, family, 60, 100000, 1, 60, weight=weight,
                                                     quiet=True)
                out["fit_chars_removed"] += removed(text, " ".join(lines))
                face = Path(getattr(fit_font, "path", "")).name
                if getattr(fit_font, "_rr_fallback_for", None):
                    out["fallback"] += 1
                    fam["fallback"] += 1
                    out["fallbacks"].append({"family": family, "text": text, "face": face})
        return out

    result = {"real": run(sorted({r["text"] for r in real})), "stress": run(STRESS)}
    result["real"]["by_source"] = dict(Counter(r["source"] for r in real))
    if production is not None:
        sys.path.insert(0, str(here))
        from extract_real_fields import collect
        # Counts only (decisions PROD-SAMPLE-counts-only): the pipeline's own log records (a refusal is logged at
        # WARNING with its text) and any error message would carry production text, so while the production pass
        # runs every log record is counted and withheld, and a failure is reported by its type alone.
        withheld = _Withhold()
        root_logger, last_resort = logging.getLogger(), logging.lastResort
        saved = root_logger.handlers[:]
        root_logger.handlers[:] = [withheld]
        logging.lastResort = None
        try:
            prod_rows = collect(production, "prod")
            prod_texts = sorted({r["text"] for r in prod_rows})
            result["production"] = run(prod_texts)
        except Exception as e:      # noqa: BLE001 - its message may quote production text
            raise SystemExit(f"production pass failed: {type(e).__name__} (message withheld: counts only)") from None
        finally:
            root_logger.handlers[:] = saved
            logging.lastResort = last_resort
        result["production"]["field_rows"] = len(prod_rows)
        result["production"]["runs"] = len({r["run"] for r in prod_rows})
        print(f"production pass: {sum(withheld.counts.values())} log record(s) withheld "
              f"{dict(sorted(withheld.counts.items()))}", file=sys.stderr)
    events = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()] if ledger.is_file() else []
    if has_ledger:
        FE.set_glyph_ledger(None)
    recorded = {(e["kind"], e["text"]) for e in events}
    for part in [k for k in ("real", "stress", "production") if k in result]:
        r = result[part]
        r["fallbacks_recorded"] = sum(("fallback", f["text"]) in recorded for f in r["fallbacks"])
        r["refusals_recorded"] = sum(("refused", f["text"]) in recorded for f in r["refused_reasons"])
        r["silent_removals"] = r["fields_changed"]        # a field drawn shorter is never recorded anywhere
        r["fallback_faces"] = dict(Counter(f["face"] for f in r["fallbacks"]))
        r["fallbacks"] = r["fallbacks"][:8]
        r["refused_reasons"] = r["refused_reasons"][:6]
    if "production" in result:      # counts only: no production text leaves this process
        for key in ("examples", "fallbacks", "refused_reasons"):
            result["production"].pop(key, None)
    result["ledger_events"] = dict(Counter(e["kind"] for e in events))

    # 2. matched faces
    match = {}
    if hasattr(FE, "fallback_match"):
        for family, weight in FAMILIES:
            brand = T.get_font(family, 80, weight=weight)
            fb = brand._rr_fallbacks[0]
            values, factor = FE.fallback_match(str(brand.path), getattr(brand, "_rr_variations", ()), fb)
            face = FE.field_face("ΑΓΙΑ ΣΟΦΙΑ", brand)
            match[family] = {"fallback": Path(fb).name, "axes": dict(values), "size_factor": factor,
                             "cap_height_px": [T.cap_height(brand), T.cap_height(face)]}
    result["fallback_match"] = match

    # 3. Arabic
    arabic = {}
    for path in sorted((root / "fonts").glob("*.ttf")):
        cmap = TTFont(path, lazy=True).getBestCmap() or {}
        arabic[path.name] = sum(0x0600 <= cp <= 0x06FF for cp in cmap)
    mixed = "Ibn al-Haytham ابن الهيثم"
    buf = hb.Buffer()
    buf.add_str(mixed)
    buf.guess_segment_properties()
    sans = root / "fonts" / "Montserrat-Variable.ttf"
    hb.shape(hb.Font(hb.Face(sans.read_bytes())), buf, {})
    clusters = [g.cluster for g in buf.glyph_infos]
    start = mixed.index("ا")
    tail = [c for c in clusters if c >= start]
    result["arabic"] = {"bundled_faces_mapping_arabic": {k: v for k, v in arabic.items() if v},
                        "bundled_faces": len(arabic),
                        "tracked_run_direction": buf.direction,
                        "arabic_clusters_in_visual_order": tail,
                        "arabic_drawn_left_to_right": tail == sorted(tail)}
    fonts = {}
    if hasattr(T, "FALLBACK_FILES"):
        from src.font_evidence import font_rights
        for name in T.FALLBACK_FILES.values():
            ok, rec = font_rights(root / "fonts" / name)
            fonts[name] = {"bytes": (root / "fonts" / name).stat().st_size, "licence_verified": ok,
                           "version": rec.get("version")}
    result["fallback_files"] = fonts
    for p in tmp.glob("*"):
        p.unlink()
    tmp.rmdir()
    print(json.dumps(result, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2 else None)
