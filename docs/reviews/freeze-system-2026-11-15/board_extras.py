"""The board's evidence beyond QF-35a's surfaces (cycle 0 round 7, preview panel 1 finding F22): every surface the
panel could not judge, drawn in this process by the renderers in src/ exactly as the pipeline calls them, on real run
inputs read-only from the dev checkout (renders use real run data). board.py imports this module after it has put
the tree under review first on sys.path; nothing here changes what src draws, and nothing is written outside `work`
(a temporary folder) and the board's out dir.

Cases (each tile's label says which it is):
- bright hero: run_unfinished_obelisk_aswan_1792985912 (covers: the covers catalogue's survey matched its delivered
  cover to assets/unfinished_obelisk_aswan/01_commons.jpg at 0.99) and _1792993374 (carousel: its own copy routes a
  colossal_stat slide and a scale diagram). Its run kept no per-slide recipe, so each slide's photo is the run's
  scene still that best matches the delivered slide (luma correlation), recorded in the manifest.
- near-white hero: no antikythera_mechanism run exists, so its brightest-topped photo (assets/antikythera_mechanism/
  01_commons.jpg, the one row 2 measures) is drawn with the Aswan run's own copy, through the same renderer, and every
  such tile is labelled STAND-IN.
- the hook frame: the tests' committed hook card on the real heroes (as render_surfaces.py), at the frame the claim is
  held (frame 0, the Reel's first frame), not the clip's last frame (after the type has faded).
- captions: the catalogue's caption cases on their own run's caption-free ground at that narration time
  (fixtures.background_at; round 8: a stored graphic frame there is re-drawn by this tree, board_evidence.
  caption_ground), through the master's picture chain (video_composer.picture_chain, the subtitles filter in it) and
  the delivery encoder (encoding.delivery_video_encoder), one clip per case from 0 to the end of its third burst,
  decoded as BT.709 once (the still, the burst-change frames and the clip itself).
- the disclosure badge on the cover and the YouTube thumbnail: no run has had an illustrated hero, so the bright run's
  cover and thumbnail are drawn with scene_planner.is_illustration answering yes for its hero (labelled FORCED).
"""
from __future__ import annotations

import contextlib
import copy as _copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ASWAN_COVER_RUN = "run_unfinished_obelisk_aswan_1792985912"
ASWAN_DECK_RUN = "run_unfinished_obelisk_aswan_1792993374"
BRIGHT_HERO = Path("assets") / "unfinished_obelisk_aswan" / "01_commons.jpg"
NEAR_WHITE_HERO = Path("assets") / "antikythera_mechanism" / "01_commons.jpg"
STAND_IN = "STAND-IN: antikythera photo, Aswan run copy (no antikythera run)"
CAPTION_TOPICS = ("amber_room_heist", "richat_structure_sahara_eye")

_cache: dict[str, Any] = {}


def _payload(dev: Path, run: str) -> dict[str, Any]:
    return json.loads((dev / "output" / run / "verified_script.json").read_text(encoding="utf-8"))


def _rgb(p: Path):
    from PIL import Image
    with Image.open(p) as im:
        return im.convert("RGB")


# ---------------------------------------------------------------------------------------------- covers (rows 3, 4)
def covers(dev: Path, work: Path) -> list[dict[str, Any]]:
    """The bright run's Reel cover, grid tile and YouTube thumbnail, and the near-white stand-in: VisualEngine's
    generate_thumbnails / generate_youtube_thumbnail as render_surfaces.py calls them (style "badge": the run kept no
    cover recipe). Each: {name, label, reel, grid, youtube, masthead (the thumbnail's record)}."""
    if "covers" in _cache:
        return _cache["covers"]
    cases = [("cover__unfinished_obelisk_aswan__bright", dev / BRIGHT_HERO, f"bright hero, run {ASWAN_COVER_RUN[4:]}"),
             ("cover__antikythera_mechanism__near_white", dev / NEAR_WHITE_HERO, STAND_IN)]
    out = []
    for name, hero, why in cases:
        if not hero.is_file():
            continue
        d = work / name
        made = _thumbnails(hero, _payload(dev, ASWAN_COVER_RUN), d)
        out.append({"name": name, "label": why, **made})
    _cache["covers"] = out
    return out


def youtube(hero: Path, payload: dict[str, Any], path: Path) -> dict[str, Any]:
    """VisualEngine.generate_youtube_thumbnail on `hero` with the run's payload: {image, masthead (the thumbnail's own
    record, read from the meta the renderer fills)}."""
    from src import youtube_thumbnail
    from src.visual_engine import VisualEngine
    meta: dict[str, Any] = {}
    real_render = youtube_thumbnail.render

    def spy(*a, **k):
        got = real_render(*a, **k)
        meta.update(k.get("meta") or {})
        return got
    youtube_thumbnail.render = spy
    import board_evidence as be
    try:   # round 8: a refusal is recorded (board_evidence.guarded) and the image is None
        yt = be.guarded(f"youtube {Path(hero).parent.name}/{Path(hero).name}", VisualEngine().generate_youtube_thumbnail,
                        hero, _copy.deepcopy(payload), path)
    finally:
        youtube_thumbnail.render = real_render
    return {"image": _rgb(yt) if yt else None, "masthead": meta.get("masthead")}


def _thumbnails(hero: Path, payload: dict[str, Any], d: Path) -> dict[str, Any]:
    import board_evidence as be
    from src.visual_engine import VisualEngine
    got = be.guarded(f"reel cover + grid {Path(hero).parent.name}/{Path(hero).name}", VisualEngine().generate_thumbnails,
                     hero, _copy.deepcopy(payload), d, style="badge", variants=False)
    reel, grid = got if got else (None, None)
    refused = {"reel": be.REFUSED[-1][2]} if got is None and be.REFUSED else {}
    yt = youtube(hero, payload, d / "yt.jpg")   # on its own: a refused thumbnail never drops the cover, nor the reverse
    if yt["image"] is None and be.REFUSED:
        refused["youtube"] = be.REFUSED[-1][2]
    refused["grid"] = refused.get("reel")
    return {"reel": _rgb(reel) if reel else None, "grid": _rgb(grid) if grid else None, "youtube": yt["image"],
            "masthead": yt["masthead"], "refused": {k: v for k, v in refused.items() if v}}


def grid_crops(reel) -> list[tuple[str, Any]]:
    """A 9:16 cover as the profile grid shows it (src/safe_zones.py: the centre 3:4 since January 2025, the centre 1:1
    before it, visual_engine.grid_crop): both crops at 1x, and the 124 px wide 3:4 tile at 1x and at 4x (nearest)."""
    from PIL import Image

    from src.visual_engine import grid_crop
    w, h = reel.size
    h34 = round(w * 4 / 3)
    top = (h - h34) // 2
    c34 = reel.crop((0, top, w, top + h34))
    tile = c34.resize((124, round(124 * h34 / w)), Image.LANCZOS)
    return [("3:4 grid crop", c34), ("1:1 grid crop", grid_crop(reel)), ("124 px tile 1x", tile),
            ("124 px tile shown 4x", tile.resize((tile.width * 4, tile.height * 4), Image.NEAREST))]


# ---------------------------------------------------------------------------------------------- carousel (row 5)
def _scene_stills(run: Path) -> list[Path]:
    """The run's scene stills in order: each provenance asset's first source that is a photograph."""
    prov = json.loads((run / "provenance.json").read_text(encoding="utf-8"))
    out: list[Path] = []
    for a in prov.get("assets") or []:
        if Path(str(a.get("path") or "")).name.startswith("graphic_"):
            continue
        for s in a.get("source_paths") or []:
            p = Path(s)
            if p.suffix.lower() in (".jpg", ".jpeg", ".png") and p.is_file():
                if p not in out:
                    out.append(p)
                break
    return out


def _match_delivered(run: Path, stills: list[Path], n: int) -> list[Path]:
    """Per slide, the still that best matches the delivered slide (luma correlation at 36x45): the run recorded no
    recipe, and the pipeline's own matcher is a paid vision call."""
    import numpy as np
    from PIL import Image, ImageOps

    def vec(im):
        a = np.asarray(ImageOps.fit(im.convert("L"), (36, 45), Image.LANCZOS), np.float32).ravel()
        return (a - a.mean()) / (a.std() + 1e-6)
    cand = [vec(_rgb(s)) for s in stills]
    chosen = []
    for i in range(n):
        slide = run / "carousel" / f"carousel_slide_{i + 1:02d}.jpg"
        if not slide.is_file():
            chosen.append(stills[i % len(stills)])
            continue
        v = vec(_rgb(slide))
        chosen.append(stills[int(np.argmax([float(v @ c) / len(v) for c in cand]))])
    return chosen


def aswan_deck(dev: Path, work: Path) -> list[dict[str, Any]]:
    """The bright run's Instagram carousel through MultiFormatGenerator.generate_carousel_sequence (its archetype
    routing, diagram slide, heading frame and swipe strip), with the matched photos given as the run's photo match
    (carousel_photos.json in the work folder, so no vision call is made). Each: {name, archetype, image, photo}."""
    if "deck" in _cache:
        return _cache["deck"]
    from src.ai_label import real_stills
    from src.multi_format import CAROUSEL_PHOTOS, MultiFormatGenerator
    run = dev / "output" / ASWAN_DECK_RUN
    payload = _payload(dev, ASWAN_DECK_RUN)
    cards = payload["carousel_slides"]
    stills, _ = real_stills(_scene_stills(run))
    photos = _match_delivered(run, stills, len(cards))
    d = work / "deck"
    d.mkdir(parents=True, exist_ok=True)
    key = {"cards": [f"{c.get('heading', '')}|{c.get('body', '')}" for c in cards], "stills": [str(Path(p)) for p in stills]}
    (d / CAROUSEL_PHOTOS).write_text(json.dumps({"key": key, "photos": [str(p) for p in photos]}, indent=1),
                                     encoding="utf-8", newline="\n")
    graphics = sorted((run / "graphics").glob("graphic_*.mp4"))
    import board_evidence as be
    slides = be.guarded(f"carousel deck {ASWAN_DECK_RUN[4:]}", MultiFormatGenerator().generate_carousel_sequence,
                        stills, payload, d, facebook=False, graphics=graphics, default=None)
    if slides is None:
        _cache["deck"] = []
        return []
    rec = json.loads((d / "carousel" / "still_layout.json").read_text(encoding="utf-8"))
    out = []
    for p in slides:
        r = (rec.get(p.name) or {}).get("recipe") or {}
        drawn = r.get("archetype") == "diagram"
        out.append({"name": f"social__{ASWAN_DECK_RUN[4:]}__carousel_{p.stem}", "archetype": r.get("archetype"),
                    "photo": Path(str(r.get("photo") or "")).name, "image": _rgb(p),
                    "label": f"bright hero, run {ASWAN_DECK_RUN[4:]}, " + (
                        "the run's own graphic poster (carousel_diagram)" if drawn else
                        "photo matched to the delivered slide")})
    _cache["deck"] = out
    return out


def near_white_slides(dev: Path) -> list[dict[str, Any]]:
    """The cover and the first interior slide on the near-white photo, the Aswan deck's own copy and archetypes
    (STAND-IN), through _render_carousel_slide as render_surfaces.py calls it."""
    from src.multi_format import MultiFormatGenerator, standalone_subject
    from src.script_engine import share_prompt
    hero = dev / NEAR_WHITE_HERO
    if not hero.is_file():
        return []
    payload = _payload(dev, ASWAN_DECK_RUN)
    cards = payload["carousel_slides"]
    arch = MultiFormatGenerator.sequence_archetypes(cards)
    gen = MultiFormatGenerator()
    import board_evidence as be
    out = []
    for i in (0, 1):
        got = be.guarded(f"social__antikythera_mechanism_near_white__carousel_slide_{i + 1:02d}",
                         gen._render_carousel_slide, arch[i], hero, hero, cards[i], i, len(cards),
                         payload.get("poll") or {}, share_prompt(payload), False, subject=standalone_subject(payload))
        if got is None:
            continue
        im, lay = got
        out.append({"name": f"social__antikythera_mechanism_near_white__carousel_slide_{i + 1:02d}",
                    "archetype": arch[i], "image": im.convert("RGB"), "label": STAND_IN,
                    "masthead": (getattr(lay, "meta", None) or {}).get("masthead")})
    return out


def recipe_archetype(dev: Path, stem: str) -> str | None:
    """A recipe surface's archetype (social__<run>__carousel_carousel_slide_NN / __fb_...), from its run's record."""
    try:
        run, rest = stem[len("social__"):].split("__", 1)
    except ValueError:
        return None
    folder = "carousel_fb" if rest.startswith("fb_") else "carousel"
    frame = rest.split("_", 1)[1] + ".jpg"
    p = dev / "output" / f"run_{run}" / folder / "still_layout.json"
    if not p.is_file():
        return None
    return ((json.loads(p.read_text(encoding="utf-8")).get(frame) or {}).get("recipe") or {}).get("archetype")


def feed_views(slide) -> list[tuple[str, Any]]:
    """A 4:5 slide as the feeds show it: the feed at 1080 wide (1080x1350), and the profile grid's centre 3:4
    (src/safe_zones.py: 1013x1350 at 1080, here at the slide's own width)."""
    from PIL import Image
    w, h = slide.size
    feed = slide.resize((1080, round(1080 * h / w)), Image.LANCZOS)
    gw = round(h * 3 / 4)
    x0 = (w - gw) // 2
    return [("feed 1080 wide", feed), ("grid 3:4 crop", slide.crop((x0, 0, x0 + gw, h)))]


# ---------------------------------------------------------------------------------------------- Story (row 6)
def story_overlay(im):
    """The Story frame with src/safe_zones.py's Story box drawn on it: outside tinted, the box outlined."""
    from PIL import Image, ImageDraw

    from src.safe_zones import safe_box
    x0, y0, x1, y1 = safe_box("story", im.width, im.height)
    base = im.convert("RGBA")
    shade = Image.new("RGBA", im.size, (255, 0, 64, 90))
    ImageDraw.Draw(shade).rectangle([x0, y0, x1 - 1, y1 - 1], fill=(0, 0, 0, 0))
    out = Image.alpha_composite(base, shade)
    ImageDraw.Draw(out).rectangle([x0, y0, x1 - 1, y1 - 1], outline=(0, 255, 255, 255), width=3)
    return out.convert("RGB"), (x0, y0, x1, y1)


# ---------------------------------------------------------------------------------------------- captions (rows 7, 11)
def _esc(p: Path) -> str:
    return str(p.resolve()).replace("\\", "/").replace(":", "\\:")


CAPTION_CLIP_BURSTS = 3     # round 8: each caption clip runs from 0 to the end of its third burst (at most 6 s)


def encode_caption_clip(ground, ass: Path, t0: float, seconds: float, size: tuple[int, int], fonts: Path,
                        clip: Path) -> dict[str, Any]:
    """Round 8 (MO-1): one production-encoder clip of narration time t0 .. t0 + seconds: `ground` held, `ass` burned in
    by the master's picture chain (video_composer.picture_chain: BT.709 in, 16-bit RGB, lens vignette, the
    subtitles filter, BT.709 out) and the delivery encoder. {path, sha256, encoder}."""
    import hashlib

    from src.encoding import delivery_video_encoder, to_bt709
    from src.video_composer import VIDEO_FPS, picture_chain
    w, h = size
    still = clip.with_suffix(".ground.png")
    ground.save(still)
    chain = picture_chain("null", w, h, captions=f"subtitles='{_esc(ass)}':fontsdir='{_esc(fonts)}'")
    graph = (f"[0:v]scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,setpts=PTS+{t0:.3f}/TB,"
             f"format=yuv420p,{chain},format=yuv420p,{to_bt709('bt709')}[v]")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", str(VIDEO_FPS), "-t", f"{seconds:.3f}",
                    "-i", str(still), "-filter_complex", graph, "-map", "[v]", *delivery_video_encoder(),
                    "-pix_fmt", "yuv420p", "-color_range", "tv", "-color_primaries", "bt709", "-color_trc", "bt709",
                    "-colorspace", "bt709", "-r", str(VIDEO_FPS), str(clip)],
                   check=True, capture_output=True, timeout=600)
    still.unlink(missing_ok=True)
    return {"path": clip, "sha256": hashlib.sha256(clip.read_bytes()).hexdigest(),
            "encoder": "picture_chain + subtitles, " + " ".join(delivery_video_encoder())}


def caption_frames(tree: Path, work: Path, clips: Path | None = None) -> list[dict[str, Any]]:
    """The catalogue's caption cases of the board's heroes, as render_surfaces.py builds their ASS, each without and
    with the reconstruction badge, burned into the run's ground AS THIS TREE DRAWS IT (board_evidence.caption_ground:
    a stored graphic is re-drawn) in ONE production-encoder clip per case (0 to the end of its third burst), decoded
    once: the still at the first burst (words[3], as before), the burst-change frames and the clip itself. Each item
    also carries the ASS text, the words and the composer's reading-rate record (the CPS table)."""
    if "captions" in _cache:
        return _cache["captions"]
    import board_evidence as be
    import board_motion as bm
    import fixtures as fx

    from src import video_composer as vc
    clips = clips or work
    out = []
    for case in fx.load("captions"):
        if case["topic"] not in CAPTION_TOPICS:
            continue
        words = json.loads(Path(fx.path(case["words"])).read_text(encoding="utf-8"))
        wide = case.get("format") == "16:9"
        size = (1920, 1080) if wide else (1080, 1920)
        t = float(words[min(3, len(words) - 1)]["start"]) + 0.05
        end = float(words[-1]["start"]) + float(words[-1].get("dur", 1.0))
        got = be.guarded(f"caption ground {case['topic']} t={t:.2f}s", be.caption_ground, case, t, size, work)
        if got is None:
            continue
        ground, note = got
        for suffix, recon in (("", None), ("__badge", [(0.0, end)])):
            ass = work / f"enc_{case['topic']}{suffix}.ass"
            kw = {"width": 1920, "height": 1080} if wide else {}
            comp = vc.VideoComposer()
            comp.build_subtitles(words, ass, emphasis=case.get("emphasis_words") or None,
                                 reconstruction_intervals=recon, **kw)
            text = ass.read_text(encoding="utf-8")
            bursts = be.ass_bursts(text)
            seconds = min(6.0, max(t + 0.5, bursts[min(CAPTION_CLIP_BURSTS, len(bursts)) - 1]["hide"] if bursts else 3.0))
            name = f"captions__{case['topic']}{suffix}__encoded"
            clip = encode_caption_clip(ground, ass, 0.0, seconds, size, tree / "fonts", clips / f"{name}.mp4")
            k_t = round(t * bm.FPS)
            changes = {round(b["show"] * bm.FPS) for b in bursts[1:CAPTION_CLIP_BURSTS]}
            keep = {k_t} | {c + d for c in changes for d in range(-1, 5)}
            seq = bm.Seq(name, clip, size, None, keep=lambda i, keep=keep: i in keep)
            clip["frames"] = seq.n
            out.append({"name": name, "badge": bool(suffix), "image": seq.kept.get(k_t), "seq": seq, "clip": clip,
                        "ass": text, "words": words, "record": getattr(comp, "last_reading_rate", None),
                        "case": case["topic"], "format": case.get("format"), "changes": sorted(changes),
                        "label": f"production encode t={t:.2f}s (frame {k_t} of the clip) on {note}"})
    _cache["captions"] = out
    return out


# ---------------------------------------------------------------------------------------------- hook (row 8)
def hook_frames() -> list[dict[str, Any]]:
    """The tests' hook card (render_surfaces.py's cards job, same grounds and key) at the frames the claim is held:
    frame 0 (the Reel's first frame) and the last frame before the type starts to fade."""
    import board_evidence as be
    import fixtures as fx

    from src import config
    from src import design_cards as dc
    config.FACT_RECORD_KEY = "test-only-fact-record-key-0123456789abcdef"   # the tests' key, this process only
    from tests import test_freeze_cards_2026_11_04 as fc
    heroes = {h["topic"]: fx.path(h["photo"]) for h in fx.section("cards")["hero_photos"]}
    grounds = {"richat": fx.still("richat_structure_sahara_eye_01_commons_960.jpg"),
               "amber": heroes["amber_room_heist"], "obelisk": heroes["unfinished_obelisk_aswan"]}
    out = []
    for g, ph in grounds.items():
        try:
            plan = fc.hook_plan(ph)
        except be._refusal_type() as e:   # round 8: the one refusal type (CardRefused is one), recorded
            out.append({"name": f"card__hook__{g}__916", "refused": str(e)[:200]})
            continue
        held = [k for k in range(plan.frames) if dc._hook_alpha(k / dc.VIDEO_FPS) >= 0.999]   # the type's own curve
        last_held = held[-1] if held else 0
        claim = " / ".join(fc.claim_lines(plan))
        for k in dict.fromkeys((0, last_held)):
            out.append({"name": f"card__hook__{g}__916__f{k:02d}", "image": dc.frame_at(plan, k).convert("RGB"),
                        "label": f"hook frame {k} ({k / dc.VIDEO_FPS:.2f}s), claim held: {claim}"})
    return out


# ---------------------------------------------------------------------------------------------- badges (row 11)
def callout_held(work: Path) -> list[dict[str, Any]]:
    """The photo callout (render_surfaces.py's case: the operator's reference pair on the Amber Room hero, 9:16) at
    the frame its labels are fully on: the clip ends after its exit fade, so its last frame (the surface QF-35a keeps)
    shows the bare photo. The held frame is the one farthest from frame 0 (the photo before the first marker)."""
    import fixtures as fx
    import numpy as np

    from src import callouts
    from tests.test_callouts_v4_2026_11_03 import REF_DOOR, REF_WALL
    hero = next(fx.path(hh["photo"]) for hh in fx.section("cards")["hero_photos"] if hh["topic"] == "amber_room_heist")
    w, h = 1080, 1920
    kept = callouts.declutter(callouts.clear_of_captions([REF_DOOR, REF_WALL], w, h), w, h)
    frames: list = []
    real = callouts._encode

    def keep(fr, out, *_a, **_k):
        frames.extend(im.convert("RGB") for im in fr)
        return Path(out)
    callouts._encode = keep
    import board_evidence as be
    try:
        be.guarded("callouts__reference_pair__916 (held frame)", callouts.render, hero, kept,
                   work / "callout_916.mp4", width=w, height=h, reveal=[0.6, 2.2])
    finally:
        callouts._encode = real
    if not frames:
        return []
    f0 = np.asarray(frames[0].resize((108, 192)), np.float32)
    k = int(np.argmax([np.abs(np.asarray(f.resize((108, 192)), np.float32) - f0).mean() for f in frames]))
    return [{"name": f"callouts__reference_pair__916__f{k:03d}", "image": frames[k],
             "label": f"labels held: frame {k} of {len(frames)} (the reference pair, as render_surfaces.py)"}]


@contextlib.contextmanager
def _illustrated():
    from src import scene_planner
    real = scene_planner.is_illustration
    scene_planner.is_illustration = lambda _p: True
    try:
        yield
    finally:
        scene_planner.is_illustration = real


def cover_badges(dev: Path, work: Path) -> list[dict[str, Any]]:
    """The disclosure badge as the cover and the YouTube thumbnail draw it over an illustrated hero: the bright run's
    cover and thumbnail with is_illustration answering yes (FORCED: no run has had an illustrated hero)."""
    hero = dev / BRIGHT_HERO
    with _illustrated():
        made = _thumbnails(hero, _payload(dev, ASWAN_COVER_RUN), work / "badge")
    why = f"FORCED illustration flag: run {ASWAN_COVER_RUN[4:]}'s hero and copy (no run had an illustrated hero)"
    return [x for x in ({"name": "cover_badge__unfinished_obelisk_aswan__reel", "image": made["reel"], "label": why},
                        {"name": "thumbnail_badge__unfinished_obelisk_aswan__youtube", "image": made["youtube"],
                         "label": why}) if x["image"] is not None]


if __name__ == "__main__":   # pragma: no cover
    sys.exit("imported by board.py")
