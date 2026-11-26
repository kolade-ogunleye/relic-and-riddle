"""TEXT stream corpus collector (freeze cycle 5): every piece of drawn text of every dev run, as ONE code tree sets it.

    python text_collect.py <tree> <dev checkout holding output/run_*> <out.json> [--only captions|lines]

Cheap measures only: layout calls, no render. It runs on the base tree and on the tip (it reads only entry points both
have), and the class scripts (text_lines.py, text_captions.py) judge both outputs with the tip's ONE policy
(src/line_policy.py), so base and tip are counted by the same rule.

Per run (its own verified_script.json / words.json, read-only):
- captions, 9:16 and 16:9: the caption file as the composer builds it (VideoComposer.build_subtitles), parsed: each
  burst's lines, show / hide, drawn size, plate width, the first word's alpha on the show frame; the burst's own words
  with their punctuation (caption_phrases); the composer's record (reading_rates);
- every other drawn text through its renderer's own call path (text_surfaces.py: carousel body and heading, Story
  frame, graphic labels, end card and end screen, YouTube headline; the chapter, myth and hook cards on the committed
  card fields, since no dev run holds one), each with the surface's own persisted record;
- two rows still set by an engine call with assumed arguments (marked approx): the Reel cover lockup and the
  carousel share line.

    --surfaces carousel,story,graphics,endcard,youtube,cards   (default all)     --runs <substring of the run name>
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import time
from pathlib import Path

tree, dev, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve()
only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else ""
sys.path.insert(0, str(tree))
sys.path.insert(1, str(Path(__file__).resolve().parent))

import text_surfaces  # noqa: E402

# --surfaces carousel,story,graphics,endcard,youtube,cards (default: all); --runs <substring> limits the runs
WANT = tuple((sys.argv[sys.argv.index("--surfaces") + 1].split(",")) if "--surfaces" in sys.argv
             else text_surfaces.SURFACES)
RUNS = sys.argv[sys.argv.index("--runs") + 1] if "--runs" in sys.argv else ""

from PIL import Image, ImageDraw  # noqa: E402

from src import brand, brand_system, heading, typography  # noqa: E402
from src import multi_format as mf  # noqa: E402
from src import video_composer as vc  # noqa: E402

BS = chr(92)
TS = re.compile(r"(\d+):(\d\d):(\d\d)\.(\d\d)")
POS = re.compile(BS * 2 + r"pos\(([-\d.]+),([-\d.]+)\)")
FS = re.compile(BS * 2 + r"fs(\d+)")
ALPHA = re.compile(BS * 2 + r"alpha&H([0-9A-Fa-f]{2})&" + BS * 2 + r"t\(0,(\d+),")
HIDDEN = "{" + BS + "alpha&HFF&}"


def _ts(s: str) -> float:
    h, m, sec, cs = map(int, TS.match(s.strip()).groups())
    return h * 3600 + m * 60 + sec + cs / 100


def parse_ass(text: str) -> list[dict]:
    boxes, texts = [], []
    for line in text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        p = line[len("Dialogue:"):].split(",", 9)
        s, e, style, body = _ts(p[1]), _ts(p[2]), p[3].strip(), p[9]
        if style == "CapBox":
            nums = [float(v) for v in re.findall(r"[-\d.]+", body.split(BS + "p1}")[-1])]
            boxes.append((s, e, round(max(nums[0::2]) - min(nums[0::2])) if nums else 0))
        elif style == "CapText":
            m, f = POS.search(body), FS.search(body)
            shown = body.split(HIDDEN)[0]
            texts.append((s, e, float(m.group(2)) if m else 0.0, " ".join(re.sub(r"\{[^}]*\}", "", body).split()),
                          int(f.group(1)) if f else None, shown))
    res = []
    for s, e, w in boxes:
        at, fs, first = {}, None, None
        for x in texts:
            if s - 1e-3 <= x[0] < e - 1e-3:
                at[x[2]] = x[3]
                fs = x[4]
                if first is None or x[0] < first[0] - 1e-6:
                    first = x
        alpha = None
        if first is not None and abs(first[0] - s) < 1e-3:
            m = ALPHA.search(first[5])
            alpha = int(m.group(1), 16) if m else 0   # the first word's alpha on the plate's first frame (255: empty)
        res.append({"show": s, "hide": e, "plate_w": w, "fs": fs, "lines": [at[y] for y in sorted(at) if at[y]],
                    "first_alpha": alpha})
    return res


def captions(run: Path) -> dict:
    words = json.loads((run / "words.json").read_text(encoding="utf-8"))
    vj = run / "verified_script.json"
    emph = (json.loads(vj.read_text(encoding="utf-8")).get("emphasis_words") or None) if vj.is_file() else None
    got = {}
    for fmt, kw in (("916", {}), ("169", {"width": 1920, "height": 1080})):
        with tempfile.TemporaryDirectory() as td:
            ass = Path(td) / "c.ass"
            comp = vc.VideoComposer()
            try:
                comp.build_subtitles([dict(w) for w in words], ass, emphasis=emph, **kw)
            except Exception as ex:   # noqa: BLE001 - recorded, the class script fails on it
                got[fmt] = {"error": f"{type(ex).__name__}: {ex}"[:300]}
                continue
            bursts = parse_ass(ass.read_text(encoding="utf-8"))
            phrases = vc.caption_phrases([dict(w) for w in words], emph, fmt == "169",
                                         **({"max_width": min(int(1920 * 0.75), vc.CAPTION_WIDE_MAX_PLATE)}
                                            if fmt == "169" else {}))
            if len(phrases) == len(bursts):
                for b, p in zip(bursts, phrases):
                    b["raw"] = [str(w.get("word", "")) for w in p]
            rate = getattr(comp, "last_reading_rate", None) or {}
            got[fmt] = {"bursts": bursts, "rate": {k: v for k, v in rate.items() if k != "bursts"}}
    return got


def _notes() -> list:
    lp = sys.modules.get("src.line_policy")
    return lp.take_notes() if lp is not None and hasattr(lp, "take_notes") else []


def approx_rows(j: dict, d) -> list[dict]:
    """The two rows still set by an engine call with assumed arguments, not by their renderer's layout (marked
    approx; the Reel cover needs the run's hero photo, which only 7 runs kept): the Reel cover lockup and the
    carousel's share line."""
    gen = mf.MultiFormatGenerator
    rows: list[dict] = []
    width = 1080 - 2 * gen.MARGIN

    def anton(text: str, size: float) -> float:
        return d.textlength(text, font=typography.get_font(brand.DISPLAY_FONT, size))

    th = j.get("thumbnail") or {}
    if isinstance(th, dict):
        title = " ".join(" ".join(str(th.get(k) or "") for k in ("line1", "line2")).split()).upper()
        if title:
            form = heading.set_heading(title, anton, 1080 - 2 * 84, (brand_system.type_px("display_xl", "tall"),),
                                       max_lines=2, lockup=True, locked_lines=3)
            if form is None:
                rows.append({"surface": "cover_lockup", "text": title, "refused": True, "approx": True})
            else:
                rows.append({"surface": "cover_lockup", "text": title, "lines": list(form.lines), "size": form.size,
                             "widths": [round(anton(t, form.size), 1) for t in form.lines],
                             "record": dict(form.record), "heading": True, "approx": True})
    share = " ".join(str(j.get("share_line") or "").split())
    if len(share.split()) >= 2:
        _notes()
        lines = None
        for n in range(3, 7):
            try:
                lines, font, _ = typography.fit_text(d, share, brand.TEXT_FONT, 36, width, n, 36, quiet=True)
                break
            except Exception:   # noqa: BLE001 - one more line, as the renderers try
                lines = None
        if not lines:
            rows.append({"surface": "carousel_share_line", "text": share, "refused": True, "approx": True})
        else:
            lines = mf.even_rag(d, lines, font, width, max([gen.BODY_MEASURE] + [len(ln) for ln in lines]))
            rows.append({"surface": "carousel_share_line", "text": share, "lines": lines, "size": 36,
                         "notes": _notes(), "approx": True})
    return rows


def lines_of_run(run: Path, d, want) -> list[dict]:
    j = json.loads((run / "verified_script.json").read_text(encoding="utf-8"))
    rows = text_surfaces.rows_of_run(run, j, want)
    if "carousel" in want:
        rows += approx_rows(j, d)
    return rows


def main() -> None:
    d = ImageDraw.Draw(Image.new("RGB", (16, 16)))
    data: dict = {"tree": str(tree), "runs": {}}
    t0 = time.time()
    for run in sorted(dev.glob("output/run_*")):
        if RUNS and RUNS not in run.name:
            continue
        row: dict = {}
        if (run / "words.json").is_file() and only in ("", "captions"):
            row["captions"] = captions(run)
        if (run / "verified_script.json").is_file() and only in ("", "lines"):
            try:
                row["lines"] = lines_of_run(run, d, WANT)
            except Exception as ex:   # noqa: BLE001
                row["lines_error"] = f"{type(ex).__name__}: {ex}"[:300]
        if row:
            data["runs"][run.name] = row
        print(run.name, round(time.time() - t0, 1), flush=True)
    if only in ("", "lines") and "cards" in WANT:
        try:
            data["runs"][text_surfaces.CARD_RUN] = {"lines": text_surfaces.card_rows()}
        except Exception as ex:   # noqa: BLE001
            data["runs"][text_surfaces.CARD_RUN] = {"lines_error": f"{type(ex).__name__}: {ex}"[:300]}
        print(text_surfaces.CARD_RUN, round(time.time() - t0, 1), flush=True)
    out.write_text(json.dumps(data, indent=0, ensure_ascii=True), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
