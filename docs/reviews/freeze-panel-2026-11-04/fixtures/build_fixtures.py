"""Builds fixtures.json: a catalogue of REAL run inputs for the freeze-panel evidence renders (2026-11-04).

    .venv/Scripts/python.exe docs/reviews/freeze-panel-2026-11-04/fixtures/build_fixtures.py

Read-only over output/run_*/, data/script_library/, data/fact_sheets/ and assets/: it never imports src (no
config, no .env) and writes only fixtures.json (and a frame cache in a temp folder, for measuring the last frames
of the masters). Every case names the run it comes from and the exact files and fields to feed; every pick says
which ground or length it covers, measured here (mean luma, mean saturation, edge energy / density of the photo;
character counts of the copy). Paths in the catalogue are relative to the repository root.
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

Image.MAX_IMAGE_PIXELS = None     # the runs keep 100+ MP museum scans; they are local, trusted files

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
OUT = REPO / "output"
ASSETS = REPO / "assets"
LIB = REPO / "data" / "script_library"
SHEETS = REPO / "data" / "fact_sheets"
FRAMES = Path(tempfile.gettempdir()) / "rr_fixture_frames"
TRANS_DUR = 0.40           # src/video_composer.py TRANS_DUR (read, not imported)
MYTH_WORDS = re.compile(r"\b(myths?|misconceptions?|popular(?:ly)? belie(?:f|ved)|legends?|often (?:said|claimed)|"
                        r"disproved|debunked|mislabel(?:l)?ed|once (?:thought|believed|suspected)|"
                        r"initially (?:considered|thought)|contrary to)\b", re.IGNORECASE)


# ------------------------------------------------------------------------------------------------ helpers
def rel(p) -> str:
    """A repo-relative POSIX path for an absolute path inside the checkout (the runs record Windows paths)."""
    s = str(p).replace("\\", "/")
    root = str(REPO).replace("\\", "/")
    if s.lower().startswith(root.lower() + "/"):
        return s[len(root) + 1:]
    return s


def absolute(p: str) -> Path:
    q = Path(str(p).replace("\\", "/"))
    return q if q.is_absolute() else REPO / q


def jload(p: Path):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


_MEASURED: dict[str, dict] = {}


def measure(path) -> dict | None:
    """Mean luma (Rec. 601, 0-255), mean saturation (HSV S, 0-255), edge energy (mean of PIL FIND_EDGES on luma)
    and edge density (share of pixels whose edge response exceeds 40), at 512 px on the long side."""
    p = absolute(path)
    key = str(p)
    if key in _MEASURED:
        return _MEASURED[key]
    if not p.is_file():
        _MEASURED[key] = None
        return None
    try:
        with Image.open(p) as im:
            im.draft("RGB", (1024, 1024))
            im = im.convert("RGB")
            size = im.size
            im.thumbnail((512, 512))
            luma = np.asarray(im.convert("L"), dtype=np.float32)
            sat = np.asarray(im.convert("HSV"), dtype=np.float32)[..., 1]
            edges = np.asarray(im.convert("L").filter(ImageFilter.FIND_EDGES), dtype=np.float32)[1:-1, 1:-1]
        r = {"size": list(size), "mean_luma": round(float(luma.mean()), 1),
             "p90_luma": round(float(np.percentile(luma, 90)), 1),
             "mean_saturation": round(float(sat.mean()), 1), "edge_energy": round(float(edges.mean()), 1),
             "edge_density": round(float((edges > 40).mean()), 3)}
    except OSError:
        r = None
    _MEASURED[key] = r
    return r


def grade(m: dict | None) -> str:
    if not m:
        return "unmeasured"
    tags = []
    if m["mean_luma"] < 70:
        tags.append("dark")
    if m["mean_luma"] > 170:
        tags.append("near-white" if m["mean_saturation"] < 40 else "bright")
    if m["edge_energy"] > 14:
        tags.append("busy")
    if m["mean_saturation"] > 110:
        tags.append("colourful")
    return ", ".join(tags) or "mid"


def ffmpeg_frame(mp4: Path, t: float | None, dst: Path, last: bool = False) -> Path | None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return dst
    cmd = ["ffmpeg", "-loglevel", "error", "-y"]
    cmd += ["-sseof", "-0.2"] if last else ["-ss", f"{t or 0:.3f}"]
    cmd += ["-i", str(mp4), "-frames:v", "1", "-update", "1", str(dst)]
    try:
        subprocess.run(cmd, check=True, timeout=120, capture_output=True)
    except (subprocess.SubprocessError, OSError):
        return None
    return dst if dst.exists() else None


def payload_of(run: Path) -> tuple[dict | None, str]:
    for name in ("verified_script.json", "verified_deep_dive.json"):
        d = jload(run / name)
        if isinstance(d, dict):
            return d, name
    return None, ""


def topic_of(run: Path, payload: dict | None) -> str:
    m = re.match(r"run_(?:\d+_)?(.+?)_\d{9,}", run.name)
    return m.group(1) if m else (payload or {}).get("topic_id", "")


def strip_emoji(s: str) -> str:
    return " ".join(re.sub(r"[^\w\s'’:,.\-–—?&()/%]", " ", s or "").split())


def copy_chars(th: dict) -> dict:
    keys = ("eyebrow", "line1", "line2", "tagline")
    out = {k: len((th or {}).get(k) or "") for k in keys}
    out["total"] = sum(out.values())
    return out


# ------------------------------------------------------------------------------------------------ the runs
def scene_timeline(run: Path) -> list[dict]:
    """The composition's scenes in order, each with its source photo (lineage) and its clip length where recorded.
    Starts are estimated: the clips' lengths minus one TRANS_DUR crossfade per cut; a still without a lineage
    record has no length here (null)."""
    comp = next(iter(sorted(run.glob("*.composition.json"))), None)
    d = jload(comp) if comp else None
    if not d:
        return []
    out, t = [], 0.0
    known = True
    prov = {}
    for a in (jload(run / "provenance.json") or {}).get("assets") or []:
        ims = [x if isinstance(x, str) else (x or {}).get("path", "") for x in a.get("source_paths") or []]
        ims = [x for x in ims if re.search(r"\.(jpe?g|png)$", x or "", re.I) and absolute(x).is_file()]
        if a.get("file") and ims:
            prov[a["file"]] = rel(ims[-1])
    for path in (d.get("scenes") or {}):
        p = absolute(path)
        lin = jload(Path(str(p) + ".lineage.json"))
        dur = ((lin or {}).get("settings") or {}).get("duration")
        if not dur and p.suffix.lower() == ".mp4" and p.is_file():
            dur = probe_seconds(p)
        srcs = [rel(s.get("path")) for s in (lin or {}).get("sources") or [] if isinstance(s, dict)]
        photo = next((s for s in srcs if re.search(r"\.(jpe?g|png|tiff?)$", s, re.I) and absolute(s).is_file()),
                     None) or prov.get(p.name)
        if p.suffix.lower() in (".jpg", ".jpeg", ".png"):
            photo = rel(p)
        item = {"clip": rel(p), "clip_exists": p.is_file(), "photo": photo,
                "duration_s": round(dur, 3) if dur else None, "start_s_est": round(t, 3) if known else None}
        out.append(item)
        if dur and known:
            t += dur - TRANS_DUR
        else:
            known = False
    return out


def probe_seconds(p: Path) -> float | None:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                           check=True, timeout=60, capture_output=True, text=True)
        return float(r.stdout.strip())
    except (subprocess.SubprocessError, OSError, ValueError):
        return None


def provenance_stills(run: Path) -> list[str]:
    d = jload(run / "provenance.json") or {}
    out = []
    for a in d.get("assets") or []:
        for s in a.get("source_paths") or []:
            s = s if isinstance(s, str) else (s or {}).get("path", "")
            if re.search(r"\.(jpe?g|png)$", s or "", re.I) and absolute(s).is_file():
                out.append(rel(s))
    return out


def cover_similarity(photo: str, cover: Path) -> float | None:
    """Pearson correlation of a 18x32 luma thumbnail of the photo (centre cover-crop to 9:16) with the run's
    delivered cover: a check that the named hero is the photo the cover shows (text perturbs it; rank matters)."""
    try:
        with Image.open(absolute(photo)) as a, Image.open(cover) as b:
            a.draft("RGB", (512, 512))
            a = a.convert("L")
            w, h = a.size
            tw = min(w, h * 9 / 16)
            th = tw * 16 / 9
            a = a.crop(((w - tw) / 2, (h - th) / 2, (w + tw) / 2, (h + th) / 2)).resize((18, 32))
            b = b.convert("L").resize((18, 32))
        x = np.asarray(a, np.float32).ravel()
        y = np.asarray(b, np.float32).ravel()
        return round(float(np.corrcoef(x, y)[0, 1]), 3)
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def hero_of(run: Path) -> dict | None:
    sl = jload(run / "still_layout.json") or {}
    rec = (sl.get("thumbnail_cover.jpg") or {}).get("recipe") or next(
        ((v or {}).get("recipe") for v in sl.values() if isinstance(v, dict) and (v.get("recipe") or {}).get("hero")),
        None)
    if rec and rec.get("hero") and absolute(rec["hero"]).is_file():
        return {"photo": rel(rec["hero"]), "how": "still_layout.json thumbnail_cover.jpg recipe.hero (the photo "
                "the cover was drawn from)", "recorded_copy": rec.get("copy"), "style": rec.get("style"),
                "text_cy": rec.get("text_cy")}
    tl = scene_timeline(run)
    photo_ok = lambda s: bool(s) and not s.startswith("tools/") and "/graphics/" not in s and \
        re.search(r"\.(jpe?g|png)$", s, re.I) and absolute(s).is_file()  # noqa: E731
    first = next((s["photo"] for s in tl if photo_ok(s["photo"])), None)
    cover = run / "thumbnail_cover.jpg"
    if first and cover.is_file():
        cands = sorted({c for c in provenance_stills(run) if photo_ok(c)} | {first})
        scores = {c: cover_similarity(c, cover) for c in cands}
        best = max((c for c in scores if scores[c] is not None), key=lambda c: scores[c], default=None)
        if not best or scores[best] < HERO_MATCH:
            return {"photo": None, "how": f"unverified: no still of the run matches its delivered cover "
                    f"(best {best} at {scores.get(best)}); not used", "first_still": first,
                    "cover_similarity": scores.get(first)}
        return {"photo": best, "how": "the run's still that best matches its delivered thumbnail_cover.jpg "
                f"(luma correlation {scores[best]}; the run kept no cover recipe)"
                + ("; it is also the sequence's first still (visual_engine.hero_image)" if best == first else
                   f"; the first still {first} matches at {scores.get(first)}"),
                "cover_similarity": scores[best], "first_still": first}
    return None


HERO_MATCH = 0.6   # the lowest cover-to-still correlation accepted as "this is the photo the cover shows"


def proper_names(words: list[dict]) -> list[dict]:
    """Runs of two or more consecutive capitalised narration words (punctuation stripped), the first word of a
    sentence dropped when it is an ordinary word that the narration also uses in lower case."""
    toks = [str(w.get("word", "")) for w in words]
    lower = {re.sub(r"\W", "", t) for t in toks if t[:1].islower()}
    out, i = [], 0
    while i < len(toks):
        j = i
        while j < len(toks) and re.sub(r"^[\W_]+", "", toks[j])[:1].isupper() and not (
                j > i and re.search(r"[.!?,;:]$", toks[j - 1])):
            j += 1
        if j - i >= 2:
            seg = list(range(i, j))
            initial = i == 0 or re.search(r"[.!?]$", toks[i - 1])
            if initial and re.sub(r"\W", "", toks[i]).lower() in lower | {"a", "an", "the", "in", "on", "near", "by",
                                                                          "at", "from", "during", "after", "before"}:
                seg = seg[1:]
            if len(seg) >= 2:
                out.append({"name": " ".join(re.sub(r"[^\w'’\-Ħħ]", "", toks[k]) for k in seg),
                            "word_index": seg[0], "start_s": round(float(words[seg[0]]["start"]), 3)})
        i = max(j, i + 1)
    return out


def scan_runs() -> list[dict]:
    runs = []
    for run in sorted(OUT.glob("run_*")):
        if not run.is_dir():
            continue
        payload, pname = payload_of(run)
        meta = jload(run / "script_metadata.json") or {}
        r = {"run": rel(run), "name": run.name, "topic": topic_of(run, payload), "payload_file": pname,
             "payload": payload, "meta": meta, "has_master": bool(list(run.glob("*_master.mp4")) or
                                                                  list(run.glob("*_16x9.mp4")))}
        runs.append(r)
    return runs


# ------------------------------------------------------------------------------------------------ designs
def pick(picks: list, label: str, pool: list, ident) -> None:
    """The pool's first entry is the extreme: it becomes a case, or, when it is already one, that case also
    covers `label` (an extreme is never swapped for a runner-up)."""
    if not pool:
        return
    x = pool[0]
    for p in picks:
        if ident(p[1]) == ident(x):
            p[0] += "; " + label
            return
    picks.append([label, x])


def covers(runs: list[dict]) -> dict:
    rows, unverified = [], []
    for r in runs:
        run = REPO / r["run"]
        th = (r["payload"] or {}).get("thumbnail")
        if not th or not (run / "thumbnail_cover.jpg").is_file():
            continue
        h = hero_of(run)
        if not h or not h["photo"]:
            if h:
                unverified.append({"run": r["run"], **h})
            continue
        m = measure(h["photo"])
        rows.append({"run": r["run"], "topic": r["topic"], "hero": h, "measured": m, "ground": grade(m),
                     "copy": {k: th.get(k) for k in ("eyebrow", "line1", "line2", "tagline")},
                     "copy_chars": copy_chars(th), "youtube_title": (r["payload"] or {}).get("youtube_title"),
                     "payload": f"{r['run']}/{r['payload_file']}",
                     "delivered": {k: f"{r['run']}/{n}" for k, n in (
                         ("reel_cover", "thumbnail_cover.jpg"), ("grid_tile", "thumbnail_profile_grid.jpg"),
                         ("youtube", "thumbnail_youtube_16x9.jpg")) if (run / n).is_file()}})
    rows = [x for x in rows if x["measured"]]
    picks: list[tuple[str, dict]] = []

    def take(label, key, reverse=True, flt=lambda x: True):
        pick(picks, label, sorted([x for x in rows if flt(x)], key=key, reverse=reverse), lambda x: x["hero"]["photo"])

    take("darkest hero across all runs", lambda x: x["measured"]["mean_luma"], reverse=False)
    take("brightest (near-white) hero across all runs", lambda x: x["measured"]["mean_luma"])
    take("busiest hero (edge energy) across all runs", lambda x: x["measured"]["edge_energy"])
    take("most colourful hero (saturation) across all runs", lambda x: x["measured"]["mean_saturation"])
    take("shortest real thumbnail copy across all runs", lambda x: x["copy_chars"]["total"], reverse=False)
    take("longest real thumbnail copy across all runs", lambda x: x["copy_chars"]["total"])
    take("longest single title line (line1/line2) across all runs",
         lambda x: max(x["copy_chars"]["line1"], x["copy_chars"]["line2"]))
    take("longest tagline across all runs", lambda x: x["copy_chars"]["tagline"])
    take("the only 16:9 documentary run (its own YouTube thumbnail and youtube_title)",
         lambda x: 1, flt=lambda x: "youtube" in x["delivered"])
    cases = []
    for why, x in picks:
        c = dict(x)
        c["why"] = why
        c["feed"] = {"hero": x["hero"]["photo"], "payload": x["payload"],
                     "call": "VisualEngine().generate_thumbnails(hero, payload, out_dir, style=hero.style or "
                             "'badge') for the Reel cover + grid tile; generate_youtube_thumbnail(hero, payload, "
                             "out) for the YouTube thumbnail (copy from documentary_cover_copy(payload))"}
        cases.append(c)
    extremes = {"luma": [min(x["measured"]["mean_luma"] for x in rows), max(x["measured"]["mean_luma"] for x in rows)],
                "copy_chars_total": [min(x["copy_chars"]["total"] for x in rows),
                                     max(x["copy_chars"]["total"] for x in rows)],
                "runs_with_hero_and_copy": len(rows)}
    survey = [{"run": x["run"], "hero": x["hero"]["photo"], "how": x["hero"]["how"][:40],
               "cover_similarity": x["hero"].get("cover_similarity"), "ground": x["ground"],
               "luma": x["measured"]["mean_luma"], "sat": x["measured"]["mean_saturation"],
               "edge": x["measured"]["edge_energy"], "copy_total": x["copy_chars"]["total"]} for x in rows]
    return {"cases": cases, "range": extremes, "survey": survey, "heroes_not_verified": unverified}


def captions(runs: list[dict]) -> dict:
    rows = []
    for r in runs:
        run = REPO / r["run"]
        words = jload(run / "words.json")
        if not isinstance(words, list) or not words or not r["payload"]:
            continue
        names = proper_names(words)
        clean = [re.sub(r"[^\w'’\-]", "", str(w.get("word", ""))) for w in words]
        longest = sorted(set(clean), key=len, reverse=True)[:5]
        ass = next(iter(sorted(run.glob("*_captions.ass"))), None)
        wide = r["payload_file"] == "verified_deep_dive.json" or bool(list(run.glob("*_16x9.mp4")))
        tl = scene_timeline(run)
        rows.append({"run": r["run"], "topic": r["topic"], "format": "16:9" if wide else "9:16",
                     "words": f"{r['run']}/words.json", "n_words": len(words),
                     "duration_s": round(float(words[-1]["start"]) + float(words[-1].get("dur", 0)), 2),
                     "emphasis_words": (r["payload"] or {}).get("emphasis_words") or [],
                     "proper_names": names, "multiword_names": sorted({n["name"] for n in names}),
                     "longest_words": [{"word": w, "chars": len(w)} for w in longest],
                     "delivered_ass": rel(ass) if ass else None,
                     "master": next((rel(p) for p in sorted(run.glob("*_master.mp4")) + sorted(run.glob("*_16x9.mp4"))),
                                    None),
                     "timeline": tl, "beat_images": sorted(rel(p) for p in (run / "beat_images").glob("*.jpg")),
                     "has_backgrounds": bool(tl) and all(x["clip_exists"] or x["photo"] for x in tl),
                     "timeline_complete": bool(tl) and all(x["start_s_est"] is not None and x["clip_exists"]
                                                           for x in tl)})
    rows_bg = [x for x in rows if x["has_backgrounds"]]
    picks = []

    def take(label, key, flt=lambda x: True):
        pick(picks, label, sorted([x for x in rows_bg if flt(x)], key=lambda x: (key(x), x["timeline_complete"]),
                                  reverse=True), lambda x: x["run"])

    take("the only 16:9 narration (documentary words.json)", lambda x: x["n_words"], flt=lambda x: x["format"] == "16:9")
    take("most multi-word proper names in one 9:16 narration", lambda x: len(x["multiword_names"]),
         flt=lambda x: x["format"] == "9:16")
    take("longest multi-word proper name (characters)",
         lambda x: max([len(n) for n in x["multiword_names"]] or [0]), flt=lambda x: x["format"] == "9:16")
    take("longest single word across all narrations", lambda x: x["longest_words"][0]["chars"],
         flt=lambda x: x["format"] == "9:16")
    take("non-ASCII letters in a proper name (Königsberg)", lambda x: 1,
         flt=lambda x: any(any(ord(ch) > 127 and ch.isalpha() for ch in n) for n in x["multiword_names"]))
    take("most words in a 9:16 narration (densest bursts)", lambda x: x["n_words"] / max(x["duration_s"], 1),
         flt=lambda x: x["format"] == "9:16")
    cases = []
    for why, x in picks:
        c = dict(x)
        c["why"] = why
        c["feed"] = {"words": x["words"], "emphasis": "payload.emphasis_words",
                     "call": "composer.build_subtitles(words, ass_path, emphasis=emphasis_words, width, height); "
                             "background at a burst time t = the scene clip in `timeline` covering t (caption-free "
                             "parallax/graphic clip or the still itself), see fixtures.background_at"}
        cases.append(c)
    allnames = sorted({n for x in rows for n in x["multiword_names"]}, key=len, reverse=True)
    allwords = sorted({w["word"] for x in rows for w in x["longest_words"]}, key=len, reverse=True)[:12]
    return {"cases": cases, "all_multiword_names": allnames[:60], "longest_words_all_runs": allwords,
            "runs_with_words": len(rows)}


def social(runs: list[dict]) -> dict:
    rows = []
    for r in runs:
        run = REPO / r["run"]
        p = r["payload"] or {}
        cs, ss = p.get("carousel_slides") or [], p.get("story_slides") or []
        if not (cs or ss) or not (run / "carousel").is_dir():
            continue
        car = jload(run / "carousel" / "still_layout.json") or {}
        fb = jload(run / "carousel_fb" / "still_layout.json") or {}
        st = jload(run / "stories" / "still_layout.json") or {}
        cp = jload(run / "carousel_photos.json") or {}

        def recipes(d):
            out = []
            for k in sorted(d):
                rc = (d[k] or {}).get("recipe") or {}
                if rc:
                    out.append({"frame": k, "renderer": rc.get("renderer"), "archetype": rc.get("archetype"),
                                "photo": rel(rc.get("photo")) if rc.get("photo") else None,
                                "photo_next": rel(rc.get("photo_next")) if rc.get("photo_next") else None,
                                "card": rc.get("card"), "slide": rc.get("slide"), "index": rc.get("index"),
                                "total": rc.get("total") or rc.get("count"), "poll": rc.get("poll")})
            return out
        car_r, fb_r, st_r = recipes(car), recipes(fb), recipes(st)
        photos = [rel(x) for x in (cp.get("photos") or [])]
        stills = [rel(x) for x in ((cp.get("key") or {}).get("stills") or [])]
        exact = bool(car_r and st_r)
        ms = [measure(x["photo"]) for x in car_r + st_r if x.get("photo")]
        ms = [m for m in ms if m]
        rows.append({
            "run": r["run"], "topic": r["topic"], "payload": f"{r['run']}/{r['payload_file']}",
            "exact_recipes": exact,
            "carousel_slides": len(cs), "story_slides": len(ss),
            "carousel_body_chars": [len(c.get("body") or "") for c in cs],
            "carousel_heading_chars": [len(c.get("heading") or "") for c in cs],
            "story_chars": [{k: len(s.get(k) or "") for k in ("kicker", "line1", "line2", "body")} for s in ss],
            "poll": p.get("poll"),
            "carousel_recipes": car_r, "carousel_fb_recipes": fb_r, "story_recipes": st_r,
            "carousel_photos": photos, "scene_stills": stills or provenance_stills(run),
            "photo_luma_range": [min(m["mean_luma"] for m in ms), max(m["mean_luma"] for m in ms)] if ms else None,
            "delivered": {"carousel": rel(run / "carousel"), "carousel_fb": rel(run / "carousel_fb")
                          if (run / "carousel_fb").is_dir() else None, "stories": rel(run / "stories")}})
    picks = []

    def take(label, key, flt=lambda x: True, reverse=True):
        pick(picks, label, sorted([x for x in rows if flt(x)], key=key, reverse=reverse), lambda x: x["run"])
    ex = lambda x: x["exact_recipes"]  # noqa: E731
    for t in sorted({x["topic"] for x in rows if x["exact_recipes"]}):
        take(f"{t}: per-slide recipes recorded (exact photo + card per slide, IG, FB and Stories)",
             lambda x: max(x["carousel_body_chars"] or [0]), flt=lambda x, t=t: ex(x) and x["topic"] == t)
    take("longest carousel body across all runs", lambda x: max(x["carousel_body_chars"] or [0]))
    take("shortest carousel body across all runs", lambda x: min(x["carousel_body_chars"] or [999]), reverse=False)
    take("longest Story body across all runs", lambda x: max([s["body"] for s in x["story_chars"]] or [0]))
    take("longest Story title line across all runs",
         lambda x: max([max(s["line1"], s["line2"]) for s in x["story_chars"]] or [0]))
    take("darkest carousel/story photos", lambda x: (x["photo_luma_range"] or [999])[0], flt=ex, reverse=False)
    take("brightest carousel/story photos", lambda x: (x["photo_luma_range"] or [0, 0])[1], flt=ex)
    cases = []
    for why, x in picks:
        c = dict(x)
        c["why"] = why
        if x["exact_recipes"]:
            c["feed"] = {"how": "per frame: the recorded recipe (multi_format.redraw_carousel_slide / "
                                "redraw_story_frame take exactly these fields); or the whole set: "
                                "generate_carousel_sequence(scene_stills, payload, out[, facebook=True]) and "
                                "generate_story_sequence(hero, payload, out, scene_images=scene_stills)"}
        else:
            c["feed"] = {"how": "the run kept no per-slide recipe: payload copy is the run's own; the photos are the "
                                "run's provenance scene stills in order (the strong-stem filter it applied is not "
                                "recorded), so the photo per slide may differ from the delivered set",
                         "label": "new: photo-to-slide order reconstructed; this run recorded no carousel recipe"}
        cases.append(c)
    return {"cases": cases, "runs_with_social_copy": len(rows),
            "longest_carousel_body": max((max(x["carousel_body_chars"] or [0]) for x in rows), default=0),
            "longest_story_body": max((max([s["body"] for s in x["story_chars"]] or [0]) for x in rows), default=0)}


def endcard(runs: list[dict]) -> dict:
    rows = []
    for r in runs:
        run = REPO / r["run"]
        master = next(iter(sorted(run.glob("*_master.mp4")) + sorted(run.glob("*_16x9.mp4"))), None)
        tl = scene_timeline(run)
        if not master or not tl:
            continue
        p = r["payload"] or {}
        ec = r["meta"].get("end_card") or {}
        wide = master.name.endswith("_16x9.mp4")
        last_clip = absolute(tl[-1]["clip"])
        last_png = FRAMES / r["name"] / "last_scene_last_frame.png"
        clip_frame = last_clip.suffix.lower() == ".mp4" and last_clip.is_file()
        if clip_frame:
            got = ffmpeg_frame(last_clip, None, last_png, last=True)
        elif last_clip.suffix.lower() != ".mp4" and last_clip.is_file():
            got = last_clip
        else:
            got = absolute(tl[-1]["photo"]) if tl[-1]["photo"] else None
        first_png = ffmpeg_frame(master, 0.0, FRAMES / r["name"] / "master_frame0.png")
        share = p.get("share_line") or ""
        rows.append({
            "run": r["run"], "topic": r["topic"], "format": "16:9" if wide else "9:16",
            "master": rel(master), "first_frame": {"from": rel(master), "t_s": 0.0,
                                                   "note": "the Reel's delivered frame 0 (graded, captioned), as "
                                                           "video_composer reads it back for loop_bridge"},
            "last_frame": {"from": tl[-1]["clip"] if clip_frame or last_clip.suffix.lower() != ".mp4" else tl[-1]["photo"],
                           "which": "last frame of the last scene clip (caption-free, ungraded)" if clip_frame else
                           ("the last scene's still" if last_clip.suffix.lower() != ".mp4" else
                            "the last scene's source photo (its clip is no longer on disk)"),
                           "photo": tl[-1]["photo"],
                           "master_alt": {"from": rel(master), "t_s": round(float(ec["start_s"]) - 1 / 30, 3)}
                           if ec.get("start_s") else None},
            "share_line": share, "share_chars": len(share), "recorded_end_card": ec or None,
            "share_source": "payload.share_line (the composer passes script_engine.end_card_share(payload), which "
                            "is card_text of it when it passes its checks)" if share else
                            "none: this payload has no share_line, so end_card_share(payload) returns the code's own "
                            "SHARE_FALLBACK ('Share this with someone who loves a good mystery, and follow for the "
                            "next one.'), the line a real 16:9 run of it gets",
            "last_measured": measure(got) if got else None, "first_measured": measure(first_png) if first_png else None})
    picks = []

    def take(label, key, flt=lambda x: True, reverse=True):
        pick(picks, label, sorted([x for x in rows if flt(x) and x["last_measured"]], key=key, reverse=reverse),
             lambda x: x["run"])
    reel = lambda x: x["format"] == "9:16" and x["share_line"]  # noqa: E731
    take("16:9 end screen: the only 16:9 run (its own last scene)", lambda x: 1, flt=lambda x: x["format"] == "16:9")
    take("darkest last frame (9:16 loop bridge)", lambda x: x["last_measured"]["mean_luma"], flt=reel, reverse=False)
    take("brightest last frame (9:16 loop bridge)", lambda x: x["last_measured"]["mean_luma"], flt=reel)
    take("busiest last frame (9:16 loop bridge)", lambda x: x["last_measured"]["edge_energy"], flt=reel)
    take("most colourful last frame (9:16 loop bridge)", lambda x: x["last_measured"]["mean_saturation"], flt=reel)
    take("longest real share line", lambda x: x["share_chars"], flt=reel)
    take("shortest real share line", lambda x: x["share_chars"], flt=reel, reverse=False)
    take("largest luma gap between last frame and frame 0 (hardest seam)",
         lambda x: abs(x["last_measured"]["mean_luma"] - (x["first_measured"] or x["last_measured"])["mean_luma"]),
         flt=reel)
    cases = []
    for why, x in picks:
        c = dict(x)
        c["why"] = why
        c["feed"] = {"call": "end_card.loop_bridge(last, first, share_line, stats) for the Reel; end_card.frames(last, "
                             "share_line, stats) with a 16:9 `last` for the YouTube end screen"}
        cases.append(c)
    # a 16:9 end screen on Reel material: only one 16:9 run exists
    wide_pool = []
    for x in rows:
        ph = x["last_frame"]["photo"]
        m = measure(ph) if reel(x) and ph else None
        if m and m["size"][0] > m["size"][1] and x["topic"] not in {w[0]["topic"] for w in wide_pool}:
            wide_pool.append((x, ph, m))
    wide_pool.sort(key=lambda w: w[2]["mean_luma"])
    chosen = [wide_pool[0], wide_pool[-1], max(wide_pool, key=lambda w: w[2]["edge_energy"])] if wide_pool else []
    for x, ph, m in {id(w[0]): w for w in chosen}.values():
        if True:
            c = dict(x)
            c["format"] = "16:9"
            c["last_measured"], c["first_measured"] = m, None   # the photo itself; no 16:9 frame 0 exists
            c["why"] = f"16:9 end screen on a Reel run's last-scene photo ({grade(m)}, luma {m['mean_luma']})"
            c["label"] = "new: 16:9 end screen of a Reel topic; only one 16:9 run exists, so the run's own " \
                         "landscape last-scene photo stands in for a 16:9 narration's last frame"
            c["feed"] = {"last": ph, "share_line": x["share_line"], "call": "end_card.frames(last, share_line, stats)"}
            cases.append(c)
    return {"cases": cases, "runs_with_master_and_timeline": len(rows)}


def thennow(runs: list[dict]) -> dict:
    """then_now.make(topic, run/graphics) -> source() picks (then_K.jpg, now_K_J.jpg) and render(old, year, new,
    year). Only runs whose graphics/ holds graphic_thennow.mp4 had an accepted pair; the years are the ones the
    run drew (read off its own clip), the recent photo is the now_0_J.jpg the poster matches."""
    cases, candidates = [], []
    years = {"run_singing_colossi_of_memnon_1792952878": (1876, 2011),
             "run_roman_self_healing_concrete_1793163918": (1934, 2018)}
    for r in runs:
        g = REPO / r["run"] / "graphics"
        thens = sorted(g.glob("then_*.jpg"))
        if not thens:
            continue
        clip = g / "graphic_thennow.mp4"
        if clip.is_file():
            poster = g / "graphic_thennow.jpg"
            nows = sorted(g.glob("now_0_*.jpg"))
            sims = {}
            for n in nows:
                try:
                    with Image.open(poster) as a, Image.open(n) as b:
                        b.draft("RGB", (512, 512))
                        a = a.convert("L").resize((18, 32))
                        bb = b.convert("L")
                        w, h = bb.size
                        tw = min(w, h * 9 / 16)
                        th = tw * 16 / 9
                        bb = bb.crop(((w - tw) / 2, (h - th) / 2, (w + tw) / 2, (h + th) / 2)).resize((18, 32))
                    sims[n.name] = round(float(np.corrcoef(np.asarray(a, np.float32).ravel(),
                                                           np.asarray(bb, np.float32).ravel())[0, 1]), 3)
                except OSError:
                    pass
            best = max(sims, key=sims.get)
            old, new = g / "then_0.jpg", g / best
            yo, yn = years.get(r["name"], (None, None))
            mo, mn = measure(old), measure(new)
            cases.append({
                "run": r["run"], "topic": r["topic"], "why": "an accepted then-and-now pair the run rendered "
                f"(old {grade(mo)}, luma {mo['mean_luma']}; recent {grade(mn)}, luma {mn['mean_luma']})",
                "old": rel(old), "old_year": yo, "new": rel(new), "new_year": yn,
                "old_measured": mo, "new_measured": mn, "recent_match": sims,
                "delivered": {"clip": rel(clip), "poster": rel(poster)},
                "years_source": "read off the run's own graphic_thennow.mp4 (t=0.5 s shows the old year, the poster "
                                "the recent year); the run kept no credit record for the pair",
                "registration": "none recorded: the pair predates then_now.register (no graphics/registered/); "
                                "render() itself takes the photos as given",
                "feed": {"call": "then_now.render(old, old_year, new, new_year, out, width, height) at 1080x1920 "
                                 "and 1920x1080"}})
        else:
            for t in thens:
                m = measure(t)
                if m:
                    candidates.append({"run": r["run"], "topic": r["topic"], "photo": rel(t), "measured": m,
                                       "ground": grade(m)})
    candidates.sort(key=lambda x: -x["measured"]["mean_luma"])
    return {"cases": cases, "rejected_old_photo_candidates_brightest": candidates[:6],
            "note": "Two accepted pairs exist in all runs. Every other then_K.jpg / now_K_J.jpg was a candidate the "
                    "vision check or viewpoint check rejected (run.log: 'no honest match'); a rejected candidate is a "
                    "real photo of the topic but NOT a verified pair, so pairing it is new and must be labelled."}


def cards(runs: list[dict]) -> dict:
    sheets = []
    paths = sorted(SHEETS.glob("*.json")) + sorted(LIB.glob("*/fact_sheet.json"))
    for p in paths:
        d = jload(p) or {}
        facts = d.get("facts") or []
        c = d.get("cards")
        fields = {k: len(v) for k, v in (c or {}).items()} if c else None
        st = [{"id": s.get("id"), "text": s.get("text"), "chars": len(s.get("text") or ""), "fact_id": s.get("fact_id"),
               "check_verdict": (s.get("check") or {}).get("verdict"),
               "signed": bool((s.get("check") or {}).get("signature"))} for s in (c or {}).get("statements") or []]
        my = [{"id": m.get("id"), "myth": m.get("myth"), "myth_chars": len(m.get("myth") or ""),
               "correction": m.get("correction"), "correction_chars": len(m.get("correction") or ""),
               "check_verdict": (m.get("check") or {}).get("verdict")} for m in (c or {}).get("myths") or []]
        refuted = [{"fact_id": f.get("id"), "statement": f.get("statement"), "chars": len(f.get("statement") or ""),
                    "disputed": f.get("disputed"), "verdict": f.get("verdict")}
                   for f in facts if MYTH_WORDS.search(f.get("statement") or "")]
        sheets.append({"sheet": rel(p), "topic": d.get("topic_id") or p.stem, "facts": len(facts),
                       "disputed": sum(1 for f in facts if f.get("disputed")), "card_fields": fields,
                       "statements": st, "myths": my, "facts_naming_a_refuted_belief": refuted})
    hooks, chapters = [], []
    for r in runs:
        p = r["payload"] or {}
        fr = p.get("fact_review") or r["meta"].get("fact_review") or {}
        for key in ("title", "youtube_title"):
            t = strip_emoji(p.get(key) or "")
            if t:
                hooks.append({"run": r["run"], "topic": r["topic"], "path": key, "text": t, "chars": len(t),
                              "words": len(t.split()), "fits_claim": len(t) <= 30 and len(t.split()) <= 6,
                              "fact_review_has_check_record": "check" in fr})
        for a in p.get("acts") or []:
            t = a.get("act_title") or ""
            chapters.append({"run": r["run"], "topic": r["topic"], "act": a.get("act_number"), "title": t,
                             "chars": len(t), "fits_chapter": 1 <= len(t) <= 32})
    seen, uniq = set(), []
    for h in sorted(hooks, key=lambda h: h["chars"]):
        if (h["topic"], h["text"]) not in seen:
            seen.add((h["topic"], h["text"]))
            uniq.append(h)
    # heroes for the hook frame: the run's own cover photo
    photo_cases = []
    for r in runs:
        run = REPO / r["run"]
        if r["topic"] in {c["topic"] for c in photo_cases} or not (run / "thumbnail_cover.jpg").is_file():
            continue
        h = hero_of(run)
        sheet = SHEETS / f"{r['topic']}.json"
        if h and h.get("photo") and sheet.is_file():
            m = measure(h["photo"])
            photo_cases.append({"run": r["run"], "topic": r["topic"], "photo": h["photo"], "measured": m,
                                "ground": grade(m), "sheet": rel(sheet), "payload": f"{r['run']}/{r['payload_file']}"})
    myths_all = [m for s in sheets for m in s["myths"]]
    return {
        "verdict": {
            "myth_fields": "none: no fact sheet in data/fact_sheets or data/script_library carries a cards.myths "
                           "entry (refuting_fact_id appears in no JSON file in the repository). A real myth-vs-fact "
                           "card cannot be rendered from existing inputs.",
            "statement_fields": "one: data/fact_sheets/hal_saflieni_hypogeum.json cards.statements S1, check verdict "
                                "'needs_check' with no runs and no signature, so card_fields.verify_field refuses it.",
            "quote_fields": "none.",
            "gated_titles": "every run's fact_review holds only public_fingerprint / sheet_path / sheet_sha256 / "
                            "review_rules_fingerprint, with no check record, so design_cards.gated_text refuses "
                            "Gated('title'|'youtube_title') on every run (fact_evidence.checked_fact_review: no "
                            "current check record).",
            "consequence": "Every hook, chapter and myth card needs a checked, signed card field. None exists. The real "
                           "inputs are the fact sheets' facts, the runs' titles, act titles and hero photos below; the "
                           "card fields themselves must be produced by fact_sheet.build + card_fields.check_cards "
                           "(with a checker key) before a card can be rendered honestly. Anything rendered before that "
                           "is new and must be labelled."},
        "longest_real_myth": max(myths_all, key=lambda m: m["myth_chars"]) if myths_all else None,
        "longest_real_correction": max(myths_all, key=lambda m: m["correction_chars"]) if myths_all else None,
        "sheets": sheets,
        "hook_claim_candidates": [h for h in uniq if h["fits_claim"]] + [h for h in uniq if not h["fits_claim"]][:4],
        "chapter_title_candidates": chapters,
        "hero_photos": photo_cases}


def main() -> None:
    runs = scan_runs()
    out = {
        "built": "2026-11-04", "builder": rel(Path(__file__)),
        "rule": "Evidence renders use existing run facts and media only: no invented copy, fake fact sheets, synthetic "
                "grounds or a photo paired with another topic's text. Anything new is labelled 'new: <why>'.",
        "paths": "relative to the repository root (fixtures.py resolves them)",
        "measures": "mean_luma (Rec.601, 0-255), p90_luma, mean_saturation (HSV S, 0-255), edge_energy (mean PIL "
                    "FIND_EDGES on luma), edge_density (share of edge response > 40); all at 512 px long side",
        "runs_scanned": len(runs),
        "designs": {"covers": covers(runs), "captions": captions(runs), "social": social(runs),
                    "endcard": endcard(runs), "thennow": thennow(runs), "cards": cards(runs)}}
    dst = HERE / "fixtures.json"
    with open(dst, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    for k, v in out["designs"].items():
        print(k, len(v.get("cases") or []), "cases")
    print("wrote", dst)


if __name__ == "__main__":
    sys.exit(main())
