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
# the near-white ground's tile note (cycle 4, 2026-11-19 ruling: the label said STAND-IN twice): the pairing class and
# its evidence come first from board_evidence.pairing, so the item says only which ground it is
STAND_IN = "near-white stress ground"
CAPTION_TOPICS = ("amber_room_heist", "richat_structure_sahara_eye")

_cache: dict[str, Any] = {}


def _payload(dev: Path, run: str) -> dict[str, Any]:
    return json.loads((dev / "output" / run / "verified_script.json").read_text(encoding="utf-8"))


def _rgb(p: Path):
    from PIL import Image
    with Image.open(p) as im:
        return im.convert("RGB")


# ---------------------------------------------------------------------------------------------- covers (rows 3, 4)
def covers(dev: Path, work: Path, heroes=()) -> list[dict[str, Any]]:
    """The bright run's Reel cover, grid tile and YouTube thumbnail, and the near-white stand-in: VisualEngine's
    generate_thumbnails / generate_youtube_thumbnail as render_surfaces.py calls them (style "badge": the run kept no
    cover recipe). Each: {name, label, reel, grid, youtube, masthead (the thumbnail's record), refused, standins (a
    refused YouTube thumbnail's probe with stand-in copy, N4)}."""
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
        out.append({"name": name, "label": why, **made,
                    "standins": youtube_standins(dev, heroes, hero, made, f"{name}__youtube", why, d)})
    _cache["covers"] = out
    return out


def youtube_standins(dev: Path, heroes, hero: Path, made: dict[str, Any], name: str, why: str,
                     d: Path) -> list[dict[str, Any]]:
    """N4: when `made`'s YouTube thumbnail was refused, the same ground and surface with the stand-in copy
    (standin_copies' first): [{name, label, image, refused}]. cover_fit's copy-stage check does not see everything
    render does (the hero figure's form, the photo), so a stand-in the render still refuses is kept as its refusal
    tile (evidence that the check and the render disagree) and the next passing hero's copy is drawn after it."""
    if not heroes or "youtube" not in (made.get("refused") or {}):
        return []
    import board_evidence as be
    out = []
    for k, (run, payload) in enumerate(standin_copies(dev, heroes, "youtube", ASWAN_COVER_RUN)):
        n = len(be.REFUSED)
        yt = youtube(hero, payload, d / f"yt{STANDIN_COPY_TAG}{run}.jpg")
        out.append({"name": f"{name}{STANDIN_COPY_TAG}{run[4:]}", "image": yt["image"], "masthead": yt["masthead"],
                    "label": f"{why}; {standin_label(run, 'youtube', k, ASWAN_COVER_RUN)}",
                    "refused": (be.REFUSED[-1][2] if len(be.REFUSED) > n else "refused") if yt["image"] is None
                    else None})
        if yt["image"] is not None:
            return out
    return out + [{"name": f"{name}{STANDIN_COPY_TAG}none", "image": None, "label": why,
                   "refused": "no board hero's copy passes cover_fit and draws on this ground"}]


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


# ---------------------------------------------------------------------------------------------- stand-in copy (N4)
# Cycle 0 round 8i (N4): the bright and near-white grounds' YouTube thumbnail (and the forced-badge one) carry run
# 1792985912's copy, which src/cover_fit.py refuses at the copy stage (production rewrites it before render), so the
# board's refusal tile is real evidence but leaves the worst grounds unjudged. Beside each such refusal the board draws
# the same ground and surface with another real run's verified copy, chosen by rule (never written for the board):
# the first board hero (board.HEROES, in order) whose copy passes cover_fit for that surface, skipping the refused
# run. Each such tile is named <surface>__copy_<run> and labelled STAND-IN (cross-run copy) by board_evidence.pairing.
STANDIN_COPY_TAG = "__copy_"


def hero_copies(dev: Path, heroes) -> list[tuple[str, dict[str, Any]]]:
    """(run, verified payload) per board hero, in board order: the covers catalogue's case for that topic (the copy
    its row 3 / 4 tiles carry), else the bright run for the bright hero."""
    import fixtures as fx
    cases = {c["topic"]: c for c in fx.load("covers")}
    out = []
    for topic in heroes:
        if topic in cases:
            out.append((Path(str(cases[topic]["run"])).name, fx.payload(cases[topic])))
        elif ASWAN_COVER_RUN.startswith(f"run_{topic}_"):
            out.append((ASWAN_COVER_RUN, _payload(dev, ASWAN_COVER_RUN)))
    return out


def fits(payload: dict[str, Any], surface: str) -> list[str]:
    """cover_fit's problems for `payload` on `surface` ("youtube", or "reel" / "grid": the Reel cover's lockup), on
    the copy that surface's renderer sets (visual_engine.youtube_cover_copy, the one copy the fit check reads), with the run's payload (figure check, badge)
    and no hero: the stand-in's copy must set at every column a hero can leave (lane B cycle 1, cover_fit one decision)."""
    from src import cover_fit
    from src.visual_engine import youtube_cover_copy
    yt = surface == "youtube"
    return cover_fit.fit_problems(youtube_cover_copy(payload), str(payload.get("youtube_title") or "") if yt else "",
                                  youtube=yt, reel=not yt, payload=payload)


def topic_runs(dev: Path, topic: str) -> list[tuple[str, dict[str, Any]]]:
    """(run, verified payload) for every run of `topic` in the dev checkout, in folder order (cycle 3: the real
    copy of a ground is first its own topic's runs)."""
    import board_evidence as be
    out = []
    for p in sorted((dev / "output").glob(f"run_{topic}_*/verified_script.json")):
        if be.run_topic(p.parent.name) == topic:
            out.append((p.parent.name, json.loads(p.read_text(encoding="utf-8"))))
    return out


def standin_copies(dev: Path, heroes, surface: str, refused_run: str) -> list[tuple[str, dict[str, Any]]]:
    """[(run, payload)] that pass cover_fit on `surface`, other than `refused_run`: first the other runs of the
    refused run's own topic (the ground's real copy, cycle 3), then the board heroes' copies in board order (a
    cross-run STAND-IN). The first is drawn; the next ones only while the render still refuses one."""
    key = ("standin", tuple(heroes), surface, refused_run)
    if key not in _cache:
        import board_evidence as be
        own = topic_runs(dev, be.run_topic(refused_run))
        seen = {refused_run}
        found = []
        for run, p in own + hero_copies(dev, heroes):
            if run in seen:
                continue
            seen.add(run)
            if not fits(p, surface):
                found.append((run, p))
        _cache[key] = found
    return _cache[key]


def standin_label(run: str, surface: str, k: int = 0, refused_run: str = "") -> str:
    import board_evidence as be
    what = "YouTube thumbnail" if surface == "youtube" else "Reel cover"
    if refused_run and be.run_topic(run) == be.run_topic(refused_run):
        return (f"copy: run {run[4:]}'s verified copy, the first other run of this topic whose copy passes cover_fit "
                f"for the {what}" + (f" and draws ({k} before it refused by the render)" if k else "")
                + "; beside the refused tile")
    return (f"copy: run {run[4:]}'s verified copy, the first board hero whose copy passes cover_fit for the {what}"
            + (f" and draws (the {k} before it pass cover_fit but the render refuses them)" if k else "")
            + "; beside the refused tile")


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
    # cycle 5 (panel 6: no masthead record for this deck): each slide's own layout record, taken as the generator
    # draws it (the last draw of a slide index is the delivered one); the call and its result are untouched
    mastheads: dict[int, Any] = {}
    real = MultiFormatGenerator._render_carousel_slide

    def spy(self, archetype, photo, photo_next, card, index, *a, **k):
        got = real(self, archetype, photo, photo_next, card, index, *a, **k)
        try:
            mastheads[int(index)] = (getattr(got[1], "meta", None) or {}).get("masthead")
        except (TypeError, IndexError, ValueError):
            pass
        return got
    import board_decks
    MultiFormatGenerator._render_carousel_slide = spy
    try:
        with board_decks.no_vision():   # cycle 5: the kept match above is the deck's; the paid matcher never runs
            slides = be.guarded(f"carousel deck {ASWAN_DECK_RUN[4:]}",
                                MultiFormatGenerator().generate_carousel_sequence,
                                stills, payload, d, facebook=False, graphics=graphics, default=None)
    finally:
        MultiFormatGenerator._render_carousel_slide = real
    if slides is None:
        _cache["deck"] = []
        return []
    rec = json.loads((d / "carousel" / "still_layout.json").read_text(encoding="utf-8"))
    for row in payload.get("layout_holds") or []:   # cycle 5 (SYS-plates-c5-1): the deck's drawn-without rows
        be.REFUSED.append((f"social__{ASWAN_DECK_RUN[4:]}__carousel ({row.get('surface', 'deck')})",
                           "drawn without a part (recorded omission)",
                           f"{row.get('omitted', '?')}: {str(row.get('reason') or row.get('rule') or '')[:300]}"))
    out = []
    for p in slides:
        r = (rec.get(p.name) or {}).get("recipe") or {}
        drawn = r.get("archetype") == "diagram"
        # the tree's own record of the frame's masthead where it keeps one (still_layout.json "masthead", stream
        # PLATES cycle 5), else the layout the draw returned (the spy above)
        kept = (rec.get(p.name) or {}).get("masthead")
        out.append({"name": f"social__{ASWAN_DECK_RUN[4:]}__carousel_{p.stem}", "archetype": r.get("archetype"),
                    "photo": Path(str(r.get("photo") or "")).name, "image": _rgb(p),
                    "masthead": kept if kept is not None else (mastheads.get(int(r["index"])) if "index" in r
                                                               else None),
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
                    "archetype": arch[i], "image": im.convert("RGB"),
                    "label": f"{STAND_IN}; a single slide through _render_carousel_slide, not a deck draw",
                    "masthead": (getattr(lay, "meta", None) or {}).get("masthead")})
    return out


def recipe_archetype(dev: Path, stem: str, surfaces: dict[str, Any] | None = None) -> str | None:
    """A recipe surface's archetype (social__<run>__carousel_carousel_slide_NN / __fb_...): the one the tree drew
    where render_surfaces.py recorded it (`surfaces`: surfaces.json; cycle 5: photo_choice "archetype" of a deck
    drawn by production's entry, which draws a diagram slide where an older run recorded a photo slide; else
    "rerouted", today's routing of a replayed slide), else the run's record."""
    s = surfaces or {}
    now = ((s.get("photo_choice") or {}).get(stem) or {}).get("archetype") or (
        (s.get("rerouted") or {}).get(stem) or {}).get("current")
    if now:
        return str(now)
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
                        clip: Path, timeout: float = 600) -> dict[str, Any]:
    """Round 8 (MO-1): one production-encoder clip of narration time t0 .. t0 + seconds: `ground` held, `ass` burned in
    by the master's picture chain (video_composer.picture_chain: BT.709 in, 16-bit RGB, lens vignette, the
    subtitles filter, BT.709 out) and the delivery encoder. {path, sha256, encoder}."""
    import hashlib

    from src.encoding import delivery_video_encoder, to_bt709
    from src.video_composer import VIDEO_FPS, picture_chain
    w, h = size
    still = clip.with_suffix(".ground.png")
    clip.parent.mkdir(parents=True, exist_ok=True)   # the caption row runs before the motion row makes <out>/clips
    ground.save(still)
    chain = picture_chain("null", w, h, captions=f"subtitles='{_esc(ass)}':fontsdir='{_esc(fonts)}'")
    graph = (f"[0:v]scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,setpts=PTS+{t0:.3f}/TB,"
             f"format=yuv420p,{chain},format=yuv420p,{to_bt709('bt709')}[v]")
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", str(VIDEO_FPS), "-t",
                        f"{seconds:.3f}", "-i", str(still), "-filter_complex", graph, "-map", "[v]",
                        *delivery_video_encoder(), "-pix_fmt", "yuv420p", "-color_range", "tv", "-color_primaries",
                        "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-r", str(VIDEO_FPS), str(clip)],
                       check=True, capture_output=True, timeout=timeout)
    except BaseException:   # cycle 5: a failed or timed-out encode leaves no half clip and no ground in <out>/clips
        clip.unlink(missing_ok=True)
        raise
    finally:
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


CAPTION_RUN_PAD = 120    # px above and below the caption lines' own y range in a band crop
CAPTION_RUN_LEAD = 0.08  # s before a burst leaves the screen: the frame its reveal is complete on


def caption_run_cases() -> list[tuple[str, str]]:
    """(topic, tag) of every caption fixture of the catalogue (cycle 5: the whole-run evidence, one per fixture)."""
    import fixtures as fx
    return [(c["topic"], "169" if c.get("format") == "16:9" else "916") for c in fx.load("captions")]


def caption_run(tree: Path, work: Path, clips: Path, topic: str, tag: str) -> dict[str, Any]:
    """Cycle 5 (panel 6: the caption row showed each fixture's first three bursts; the lander built the rest by
    hand): one caption fixture's WHOLE narration as the composer captions it, burned by the master's picture chain
    and the delivery encoder into <clips>/captions_run__<topic>__<tag>.mp4 on the run's ground at the first burst
    (held), decoded once. {name, clip, frames, tiles: the caption band (its lines' own y range) at 1:1 on the frame
    CAPTION_RUN_LEAD before each burst leaves, labelled with the burst's seconds on screen, characters and
    characters per second, rows: the burst table, record: caption_rates'}. A worker process can run it."""
    import board_evidence as be
    import board_motion as bm
    import fixtures as fx

    from src import video_composer as vc
    work, clips = Path(work), Path(clips)
    case = next(c for c in fx.load("captions")
                if c["topic"] == topic and ("169" if c.get("format") == "16:9" else "916") == tag)
    words = json.loads(Path(fx.path(case["words"])).read_text(encoding="utf-8"))
    wide = tag == "169"
    size = (1920, 1080) if wide else (1080, 1920)
    name = f"captions_run__{topic}__{tag}"
    ass = work / f"run_{topic}_{tag}.ass"
    comp = vc.VideoComposer()
    comp.build_subtitles(words, ass, emphasis=case.get("emphasis_words") or None,
                         **({"width": 1920, "height": 1080} if wide else {}))
    text = ass.read_text(encoding="utf-8")
    bursts = be.ass_bursts(text)
    rates = be.caption_rates(words, text, getattr(comp, "last_reading_rate", None))
    if not bursts:
        return {"name": name, "clip": None, "tiles": [], "rows": [], "record": rates, "topic": topic, "tag": tag}
    ground, note = be.caption_ground(case, bursts[0]["show"] + 0.05, size, work)
    seconds = bursts[-1]["hide"] + 0.2
    clip = encode_caption_clip(ground, ass, 0.0, seconds, size, Path(tree) / "fonts", clips / f"{name}.mp4",
                               timeout=max(600.0, seconds * 40))   # a whole narration: minutes of video
    want = {max(0, round((b["hide"] - CAPTION_RUN_LEAD) * bm.FPS)): i for i, b in enumerate(bursts)}
    ys = [ln["y"] for b in bursts for ln in b["lines"] if ln["y"]]
    top = max(0, int(min(ys, default=size[1] * 0.55)) - CAPTION_RUN_PAD)
    bottom = min(size[1], int(max(ys, default=size[1] * 0.85)) + CAPTION_RUN_PAD)
    side = (size[0] - min(size[0], 1280)) // 2   # 16:9: the middle 1280 px (the caption column), still 1:1
    box = (side, top, size[0] - side, bottom)
    bands: dict[int, Any] = {}   # decoded once; only the band of each wanted frame is kept (a run is minutes long)
    frames = 0
    for k, im in enumerate(bm.decode(clip["path"], size)):
        frames += 1
        if k in want:
            bands[k] = im.crop(box)
    clip["frames"] = frames
    per = rates["bursts"]
    tiles, rows = [], [("burst", "shows at (s)", "on screen (s)", "chars", "chars per second", "frame", "text")]
    for f, i in sorted(want.items(), key=lambda x: x[1]):
        b = bursts[i]
        r = per[i] if i < len(per) else {}
        cps = r.get("shown_cps")
        flag = " OVER 17" if (cps or 0) > 17 else ""
        rows.append((i + 1, f"{b['show']:.2f}", f"{b['hide'] - b['show']:.2f}", r.get("chars", "-"),
                     f"{cps}{flag}", f"f{f} t={f / bm.FPS:.2f}s", " / ".join(ln["text"] for ln in b["lines"])))
        im = bands.get(f)
        if im is None:
            continue
        tiles.append((f"burst {i + 1} | shows {b['show']:.2f}s | {b['hide'] - b['show']:.2f}s on screen | "
                      f"{r.get('chars', '-')} chars | {cps} cps{flag} | frame {f}", im))
    return {"name": name, "clip": clip, "frames": frames, "tiles": tiles, "rows": rows, "record": rates,
            "topic": topic, "tag": tag, "box": list(box), "ground": note, "seconds": round(seconds, 2)}


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
CALLOUT_REVEAL = [0.6, 2.2]   # render_surfaces.py's callout job: each label's cue


def callout_held(work: Path, clips: Path | None = None) -> list[dict[str, Any]]:
    """The photo callout (render_surfaces.py's case: the operator's reference pair on the Amber Room hero, 9:16) at
    the frame its labels are fully on: the clip ends after its exit fade, so its last frame (the surface QF-35a keeps)
    shows the bare photo. The held frame is the one farthest from frame 0 (the photo before the first marker).
    Cycle 4 (panel 5 MO, carry-in 2): the same draw is also encoded by the renderer's own clip encode (callouts._encode,
    graphics._encode: the delivery encoder) into <clips>/callouts__reference_pair__916.mp4, with the renderer's own
    per-frame record (callouts.render record=: each label's marker, plate and text alpha) as its timing record; the
    held frame is the drawn frame, as before. Cached per process."""
    if "callout" in _cache:
        return _cache["callout"]
    import hashlib
    import shutil
    import time

    import fixtures as fx
    import numpy as np

    from src import callouts
    from tests.test_callouts_v4_2026_11_03 import REF_DOOR, REF_WALL
    t0 = time.perf_counter()
    hero = next(fx.path(hh["photo"]) for hh in fx.section("cards")["hero_photos"] if hh["topic"] == "amber_room_heist")
    w, h = 1080, 1920
    kept = callouts.declutter(callouts.clear_of_captions([REF_DOOR, REF_WALL], w, h), w, h)
    frames: list = []
    real = callouts._encode

    def tee(fr):
        for im in fr:
            frames.append(im.convert("RGB"))
            yield im

    def keep(fr, out, *a, **k):
        if clips is None:   # the held frame only (as before cycle 4)
            frames.extend(im.convert("RGB") for im in fr)
            return Path(out)
        return real(tee(fr), out, *a, **k)
    callouts._encode = keep
    import board_evidence as be
    rec: dict[str, Any] = {}
    out = work / "callouts__reference_pair__916.mp4"
    try:
        got = be.guarded("callouts__reference_pair__916 (held frame)", callouts.render, hero, kept, out,
                         width=w, height=h, reveal=list(CALLOUT_REVEAL), **({"record": rec} if clips is not None else {}))
    finally:
        callouts._encode = real
    if not frames:
        _cache["callout"] = []
        return []
    f0 = np.asarray(frames[0].resize((108, 192)), np.float32)
    k = int(np.argmax([np.abs(np.asarray(f.resize((108, 192)), np.float32) - f0).mean() for f in frames]))
    item = {"name": f"callouts__reference_pair__916__f{k:03d}", "image": frames[k],
            "label": f"labels held: frame {k} of {len(frames)} (the reference pair, as render_surfaces.py)"}
    if clips is not None and got is not None and Path(out).is_file():
        path = Path(clips) / Path(out).name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(out, path)
        item["clip"] = {"path": path, "frames": len(frames), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "encoder": "callouts._encode (graphics._encode: the delivery encoder, as production writes the "
                                   "callout clip)"}
        item["record"] = callout_record(rec, kept)
        item["seconds"] = round(time.perf_counter() - t0, 1)
    _cache["callout"] = [item]
    return [item]


def callout_record(rec: dict[str, Any], points: list) -> dict[str, Any]:
    """The timing part of callouts.render's record (each frame's per-label marker, plate and text alpha; the masks
    left out) and the renderer's timing constants, picklable for a worker."""
    from src import callouts
    keys = ("plate_a", "text_a", "plate", "lead", "g")
    return {"frames": [{"i": f["i"], "t": f["t"], "labels": {k: {x: float(d[x]) for x in keys if x in d}
                                                          for k, d in (f.get("labels") or {}).items()}}
                       for f in rec.get("frames") or []],
            "exit_frame": rec.get("exit_frame"), "reveal": list(CALLOUT_REVEAL),
            "texts": {str(k): str(p[0]) for k, p in enumerate(points or [])},   # each point's label, in render order
            "clip_seconds": getattr(callouts, "CLIP_SECONDS", None), "dot_lead": getattr(callouts, "DOT_LEAD", None),
            "exit_lead": getattr(callouts, "EXIT_LEAD", None), "exit_time": getattr(callouts, "EXIT_TIME", None)}


@contextlib.contextmanager
def _illustrated():
    from src import scene_planner
    real = scene_planner.is_illustration
    scene_planner.is_illustration = lambda _p: True
    try:
        yield
    finally:
        scene_planner.is_illustration = real


def cover_badges(dev: Path, work: Path, heroes=()) -> list[dict[str, Any]]:
    """The disclosure badge as the cover and the YouTube thumbnail draw it over an illustrated hero: the bright run's
    cover and thumbnail with is_illustration answering yes (FORCED: no run has had an illustrated hero). A refused
    surface is kept as its refusal (round 8i, N4) and a refused thumbnail gets its stand-in-copy probe beside it."""
    hero = dev / BRIGHT_HERO
    name = "thumbnail_badge__unfinished_obelisk_aswan__youtube"
    why = f"FORCED illustration flag: run {ASWAN_COVER_RUN[4:]}'s hero and copy (no run had an illustrated hero)"
    with _illustrated():
        made = _thumbnails(hero, _payload(dev, ASWAN_COVER_RUN), work / "badge")
        extra = youtube_standins(dev, heroes, hero, made, name, "FORCED illustration flag", work / "badge")
    refused = made.get("refused") or {}
    return [x for x in ({"name": "cover_badge__unfinished_obelisk_aswan__reel", "image": made["reel"], "label": why,
                         "refused": refused.get("reel")},
                        {"name": name, "image": made["youtube"], "label": why, "refused": refused.get("youtube")},
                        *extra) if x["image"] is not None or x.get("refused")]


if __name__ == "__main__":   # pragma: no cover
    sys.exit("imported by board.py")
