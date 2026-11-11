"""QF-86 evidence: photo-only curves, graphic layers ungraded. Repository root is the ONLY argument.

    python docs/reviews/quality-fixes-2026-11/QF-86/measure.py <repo root>

Runs unchanged on the base (1ab9aded) and on the QF-86 commit: it imports `src` from <repo root> and reads only
committed inputs (inputs/, real run frames listed with their origin in inputs/inputs.json; the brand tokens from
src/brand.py). Every picture goes through that tree's real composer (VideoComposer.compose_video, native 1080x1920
or 1920x1080, its own encoder) in a temporary folder that is deleted; nothing in the repository is written, and no
network is used. Prints one JSON document.

Sections:
- native_graphics: the nine brand tokens as a graphic clip through the master (acceptance: CIE76 <= 2).
- preconversion_ramps: 256 neutral 16-bit codes through the photo grade before any YUV conversion (>= 250 levels,
  black 0, white 65535), neutral and at either topic-bias bound.
- designs: each design of the freeze scope that passes through the master, on real run frames: as-drawn against
  delivered (CIE76 median / p95 over every 4th pixel, mean L* shift) for the graphic clips (stat card, map,
  then-and-now, hook frame) and for a photo parallax clip (geometry identical, so pixel-wise); for a photo still on
  the flat camera move only the mean L*/chroma shift (its crop moves). The "_grade_only" rows draw the lens
  vignette white in both trees (picture_vignette -> null), so they show the grade alone.
- contrast_path: graphics.graded_frame, the frame every graphic's <clip>.contrast.json "graded" ratio is read on.
- cut_gap: the same real photo bare (parallax clip) and as the photo layer of a graphic, at each run's real
  grade bias, lens off in both (the grade alone): the cut between them inside one Reel.
- stat_card_richat: the newest Richat run's real stat card drawn by this tree (bias +0.05): its contrast sidecar
  minimums and its delivered top band against the bare photo's.
- end_screen: the 16:9 end screen (end_card.frames) over the three real last-scene photos the parked freeze round
  measured raw: raw (the park's stand-in), on the frame the documentary master delivers (bias 0, as
  compose_deep_dive grades) and at the topic's own bias.
- still_surfaces: a real Story frame, carousel slide and cover drawn again from their run recipes; sha256 of the
  pixels (identical on both trees = the still surfaces carry no grade before or after).
- name_policy: the review's literal grep and the single operator-approved historical credit.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
INPUTS = HERE / "inputs"
TOKENS = ("INK", "INK2", "BONE", "STONE", "STONE_LT", "GOLD", "GOLD_DIM", "TEAL", "CORAL")
GREP = r"kodak|national geographic|\bBBC\b|economist"


def lab(rgb):
    """CIE Lab (D65) of sRGB values 0-255, any shape (..., 3)."""
    x = np.asarray(rgb, dtype=float) / 255
    x = np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
    xyz = x @ np.array([[.4124564, .3575761, .1804375], [.2126729, .7151522, .0721750],
                        [.0193339, .1191920, .9503041]]).T
    xyz /= np.array([.95047, 1, 1.08883])
    f = np.where(xyz > (6 / 29) ** 3, np.cbrt(xyz), xyz / (3 * (6 / 29) ** 2) + 4 / 29)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], axis=-1)


def compare(drawn: np.ndarray, delivered: np.ndarray) -> dict:
    a, b = lab(drawn[::4, ::4]), lab(delivered[::4, ::4])
    de = np.linalg.norm(a - b, axis=-1)
    ca, cb = np.hypot(a[..., 1], a[..., 2]), np.hypot(b[..., 1], b[..., 2])
    return {"delta_e76_median": round(float(np.median(de)), 3), "delta_e76_p95": round(float(np.percentile(de, 95)), 3),
            "mean_L_shift": round(float((b[..., 0] - a[..., 0]).mean()), 3),
            "mean_chroma_shift": round(float((cb - ca).mean()), 3)}


def means(drawn: np.ndarray, delivered: np.ndarray) -> dict:
    a, b = lab(drawn[::4, ::4]).reshape(-1, 3), lab(delivered[::4, ::4]).reshape(-1, 3)
    return {"mean_L_shift": round(float(b[:, 0].mean() - a[:, 0].mean()), 3),
            "mean_chroma_shift": round(float(np.hypot(b[:, 1], b[:, 2]).mean() - np.hypot(a[:, 1], a[:, 2]).mean()), 3),
            "L_p5_p50_p95_drawn": [round(float(v), 2) for v in np.percentile(a[:, 0], [5, 50, 95])],
            "L_p5_p50_p95_delivered": [round(float(v), 2) for v in np.percentile(b[:, 0], [5, 50, 95])]}


def relative_luminance(rgb) -> float:
    x = np.asarray(rgb, dtype=float) / 255
    x = np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
    return float((x @ np.array([0.2126, 0.7152, 0.0722])).mean())


def audit_names(root: Path, matches: list[str]) -> dict:
    """Exact approved domain contexts and the one approved historical credit; anything else is unexpected."""
    policy = json.loads((HERE / "approved-name-exceptions.json").read_text(encoding="utf-8"))
    domains = {(x["path"], x["content_sha256"]) for x in policy["domain_contexts"]}
    exception = policy["historical_metadata"]
    data = json.loads((root / exception["path"]).read_text(encoding="utf-8"))
    records = [x for x in data["images"] if x.get("title") == exception["title"]
               and x.get("source_url") == exception["source_url"]]
    value = records[0].get(exception["field"], "") if len(records) == 1 else ""
    intact = hashlib.sha256(value.encode("utf-8")).hexdigest() == exception["value_sha256"]
    unexpected = []
    for line in matches:
        path, _, content = line.split(":", 2)
        digest = hashlib.sha256(content.strip().encode("utf-8")).hexdigest()
        if (path, digest) in domains:
            continue
        if path == exception["path"] and intact:
            try:
                field = json.loads("{" + content.strip().rstrip(",") + "}")
            except ValueError:
                field = {}
            if field == {exception["field"]: value}:
                continue
        unexpected.append(line)
    return {"matches": len(matches), "unexpected": unexpected, "exact_metadata_hash_intact": intact,
            "passed_with_approved_exact_exception": intact and not unexpected}


class Rig:
    def __init__(self, root: Path, work: Path):
        from src import graphics
        from src import video_composer as vc
        from src.delivery_profiles import get_delivery_profile
        from src.encoding import BT709_TO_RGB
        self.graphics, self.vc, self.profile, self.to_rgb = graphics, vc, get_delivery_profile, BT709_TO_RGB
        self.work = work
        self.voice = work / "voice.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000",
                        "-t", "1.5", str(self.voice)], check=True, timeout=60)
        self.n = 0

    def frame(self, path: Path, w: int, h: int, t: float) -> np.ndarray:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-vf",
                              f"{self.to_rgb},format=rgb24", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True, check=True, timeout=120).stdout
        return np.frombuffer(raw, np.uint8).reshape(h, w, 3)

    def master(self, scene: Path, profile: str, bias: float = 0.0, t: float = 0.6) -> np.ndarray:
        p = self.profile(profile)
        self.n += 1
        out = self.work / f"master_{self.n}.mp4"
        # no words: no caption plate over the design, so the whole frame compares with the frame as drawn
        self.vc.VideoComposer().compose_video([scene], self.voice, self.voice, [], 1, out, durations=[1],
                                              grade_bias=bias, delivery_profile=p)
        return self.frame(out, p.width, p.height, t)

    def clip(self, im: Image.Image, name: str) -> Path:
        """A static 1-second clip of `im` encoded the way graphics encode (graphics._encode)."""
        d = self.work / f"clip_{self.n}_{name}"
        d.mkdir()
        self.n += 1
        return self.graphics._encode((im for _ in range(30)), d / name, 30, im.width, im.height)


def fitted(path: Path, size: tuple[int, int]) -> Image.Image:
    from PIL import ImageOps
    with Image.open(path) as im:
        return ImageOps.fit(im.convert("RGB"), size, Image.Resampling.LANCZOS)


def note(stage: str) -> None:
    print(f"qf86 measure: {stage}", file=sys.stderr, flush=True)


# end_card.frames draws END_SCREEN_SECONDS * FPS frames (300) and measures only the last. Sampled at this rate it
# draws 6 frames on the same clock: the last at t = 9.09 s, where every element is at full alpha and the push is at
# 99.99 % of the 9.97 s rest frame's (0.00016 zoom, under 0.2 px). The 300-frame path takes about 25 min per card.
END_SCREEN_SAMPLE_FPS = 0.55


def end_screen_stats(end_card, ground: Image.Image, share: str) -> dict:
    stats: dict = {}
    fps, end_card.FPS = end_card.FPS, END_SCREEN_SAMPLE_FPS
    try:
        last = None
        for last in end_card.frames(ground, share, stats):
            pass
    finally:
        end_card.FPS = fps
    stats["_last"] = last
    return stats


def main(root: Path) -> dict:
    sys.path.insert(0, str(root))

    def offline(*args, **kwargs):
        raise RuntimeError("QF-86 evidence must not use the network")

    socket.socket.connect = offline
    # One encoder on every machine and run (review r3, non-blocking 1): encoding.get_video_encoder's 3 s nvenc probe
    # can time out on a cold start and silently switch encoder, so the probe's own fallback, libx264, is pinned.
    from src import encoding
    encoding._DETECTED_ENCODER = ["-c:v", "libx264", "-preset", encoding.VIDEO_PRESET, "-crf", str(encoding.VIDEO_CRF)]
    from src import brand
    manifest = json.loads((INPUTS / "inputs.json").read_text(encoding="utf-8"))
    runs = manifest["runs"]
    results: dict = {"inputs": manifest["files"], "run_grade_bias": {k: v["grade_bias"] for k, v in runs.items()},
                     "delta_e_definition": "CIE76, D65; designs: every 4th pixel; tokens: 24x24 median per token",
                     "network_calls": 0}
    for name, rec in results["inputs"].items():
        assert hashlib.sha256((INPUTS / name).read_bytes()).hexdigest() == rec["sha256"], name
    with tempfile.TemporaryDirectory(prefix="qf86-measure-") as directory:
        work = Path(directory)
        rig = Rig(root, work)
        from src.lineage import record_derived

        # ---- brand tokens (acceptance)
        results["native_graphics"] = {}
        token_cases = [("reel", 0.0), ("deep_dive", .05), ("reel", -.05)]
        token_cases += [("reel", b) for b in sorted({v["grade_bias"] for v in runs.values()}) if b]   # real run biases
        for profile, bias in token_cases:
            p = rig.profile(profile)
            a = np.empty((p.height, p.width, 3), np.uint8)
            for i, token in enumerate(TOKENS):
                a[:, i * p.width // len(TOKENS):(i + 1) * p.width // len(TOKENS)] = getattr(brand, token)
            got = rig.master(rig.clip(Image.fromarray(a), "graphic_tokens.mp4"), profile, bias)
            cells = [np.median(got[p.height // 2 - 12:p.height // 2 + 12,
                                   int((i + .5) * p.width / len(TOKENS)) - 12:int((i + .5) * p.width / len(TOKENS)) + 12],
                               axis=(0, 1)) for i in range(len(TOKENS))]
            de = np.linalg.norm(lab(cells) - lab([getattr(brand, t) for t in TOKENS]), axis=1)
            results["native_graphics"][f"{profile}@{bias:+.3f}"] = {"grade_bias": bias,
                                                   "delta_e76": {t: round(float(v), 3) for t, v in zip(TOKENS, de)},
                                                   "maximum_delta_e76": round(float(de.max()), 3)}

        note("ramps")
        # ---- 16-bit neutral ramps before any YUV conversion
        results["preconversion_ramps"] = {}
        ramp = np.tile((np.arange(256, dtype=np.uint16) * 257)[None, None, :], (3, 16, 1))
        for bias in (0.0, -.05, .05):
            grade = rig.vc.film_grade_filter(bias, out_dir=work / "luts")   # base: its table goes to the temp dir
            out = subprocess.run(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gbrp16le", "-s", "256x16",
                                  "-i", "-", "-vf", f"format=gbrp16le,{grade},format=gbrp16le", "-frames:v", "1",
                                  "-f", "rawvideo", "-pix_fmt", "gbrp16le", "-"],
                                 input=ramp.tobytes(), capture_output=True, check=True, timeout=60).stdout
            planes = np.frombuffer(out, dtype="<u2").reshape(3, 16, 256)
            results["preconversion_ramps"][str(bias)] = {
                "levels": min(len(np.unique(pl[8])) for pl in planes), "black": planes[:, 8, 0].tolist(),
                "white": planes[:, 8, -1].tolist(),
                "monotone": bool(np.all(np.diff(planes[:, 8].astype(int), axis=1) > 0))}

        note("designs")
        # ---- designs on real run frames, 9:16 master
        designs = {}
        size = (1080, 1920)
        for key, name, clip_name in (("stat_card", "graphic_numbers_gol_gumbaz.jpg", "graphic_numbers.mp4"),
                                     ("map", "graphic_map_gol_gumbaz.jpg", "graphic_map.mp4"),
                                     ("then_and_now", "graphic_thennow_roman.jpg", "graphic_thennow.mp4"),
                                     ("hook_frame_dark", "hook_frame_nazca_dark.jpg", "hook_frame.mp4"),
                                     ("hook_frame_bright", "hook_frame_nazca_bright.jpg", "hook_frame.mp4")):
            drawn = fitted(INPUTS / name, size)
            got = rig.master(rig.clip(drawn, clip_name), "reel")
            designs[key] = {"input": name, "path": "graphic clip", **compare(np.asarray(drawn), got)}
        # a photo as the parallax clip real Reels carry (src/parallax.py: lineage operation "parallax")
        photo = work / "photo_gol_gumbaz_04.jpg"
        shutil.copy(INPUTS / "photo_gol_gumbaz_04.jpg", photo)
        drawn = fitted(photo, size)
        clip = rig.clip(drawn, "scene_01.mp4")
        record_derived(clip, [photo], "parallax", {"preset": "hook", "duration": 1.0, "fps": 30, "variant": 0})
        designs["photo_parallax_clip"] = {"input": "photo_gol_gumbaz_04.jpg", "path": "parallax clip",
                                          **compare(np.asarray(drawn), rig.master(clip, "reel"))}
        designs["photo_parallax_clip_warm_bias"] = {"input": "photo_gol_gumbaz_04.jpg", "path": "parallax clip, bias +0.05",
                                                    **compare(np.asarray(drawn), rig.master(clip, "reel", .05))}
        # a photo on the flat camera move (parallax unavailable): its crop moves, so means only
        designs["photo_still_flat_move"] = {"input": "photo_gol_gumbaz_04.jpg", "path": "still (camera)",
                                            **means(np.asarray(drawn), rig.master(photo, "reel"))}
        # The same with the lens vignette drawn white in both trees (picture_vignette -> null): the grade alone.
        # The vignette is common to both trees and is lifted only under graphic_* clips, so it dominates the
        # photo and hook-frame rows above.
        lens = rig.vc.picture_vignette
        rig.vc.picture_vignette = lambda width, height: "null"
        try:
            designs["photo_parallax_clip_grade_only"] = {"input": "photo_gol_gumbaz_04.jpg", "path": "parallax clip, lens off",
                                                         **compare(np.asarray(drawn), rig.master(clip, "reel"))}
            designs["photo_parallax_clip_warm_bias_grade_only"] = {
                "input": "photo_gol_gumbaz_04.jpg", "path": "parallax clip, bias +0.05, lens off",
                **compare(np.asarray(drawn), rig.master(clip, "reel", .05))}
            designs["photo_still_flat_move_grade_only"] = {"input": "photo_gol_gumbaz_04.jpg", "path": "still (camera), lens off",
                                                           **means(np.asarray(drawn), rig.master(photo, "reel"))}
            hook = fitted(INPUTS / "hook_frame_nazca_bright.jpg", size)
            designs["hook_frame_bright_grade_only"] = {"input": "hook_frame_nazca_bright.jpg", "path": "graphic clip, lens off",
                                                       **compare(np.asarray(hook), rig.master(rig.clip(hook, "hook_frame.mp4"), "reel"))}
        finally:
            rig.vc.picture_vignette = lens
        results["designs"] = designs

        note("cut gap")
        # ---- the same photo bare and as the photo layer of a graphic, at each run's real bias (lens off in both,
        # as the vignette is lifted under graphic clips only: this is the grade alone). Base: graphics draw the photo
        # ungraded and the master grades the whole picture; QF-86: graphics draw photo_grade.cover(photo, bias) and
        # the master leaves graphic clips alone. "ungraded_layer" is a graphic that draws its photo ungraded (the
        # state before this fix, ec53d089), measured on this tree only.
        import importlib.util
        has_layer_grade = importlib.util.find_spec("src.photo_grade") is not None
        from src.typography import cover_crop
        gaps = {}
        lens = rig.vc.picture_vignette
        rig.vc.picture_vignette = lambda width, height: "null"
        try:
            for key, name, run in (("gol_gumbaz", "photo_gol_gumbaz_04.jpg", "gol_gumbaz"),
                                   ("roman", "end_ground_roman.jpg", "roman"),
                                   ("aswan", "end_ground_aswan.jpg", "aswan"),
                                   ("wieliczka", "end_ground_wieliczka.jpg", "wieliczka"),
                                   ("richat", "photo_richat_01.jpg", "richat"),
                                   ("aswan_cool_bound", "end_ground_aswan.jpg", None)):
                bias = runs[run]["grade_bias"] if run else -0.05
                src = work / f"gap_{key}" / name
                src.parent.mkdir()
                shutil.copy(INPUTS / name, src)
                raw = cover_crop(src, size).convert("RGB")
                bare = rig.clip(raw, "scene_01.mp4")
                record_derived(bare, [src], "parallax", {"preset": "hook", "duration": 1.0, "fps": 30, "variant": 0})
                a = rig.master(bare, "reel", bias)
                if has_layer_grade:
                    from src import photo_grade
                    layer = photo_grade.cover(src, size, bias)
                else:
                    layer = raw
                b = rig.master(rig.clip(layer, "graphic_photo_layer.mp4"), "reel", bias)
                row = {"input": name, "grade_bias": bias, "bare_vs_graphic_layer": compare(a, b)}
                if has_layer_grade:
                    c = rig.master(rig.clip(raw, "graphic_photo_layer.mp4"), "reel", bias)
                    row["bare_vs_ungraded_layer"] = compare(a, c)
                gaps[key] = row
        finally:
            rig.vc.picture_vignette = lens
        results["cut_gap"] = gaps

        note("stat card")
        # ---- the real stat card of the newest Richat run (bias +0.05) drawn by this tree's renderer: its contrast
        # sidecar, and the delivered frame against the same photo shown bare (top band, where no text is drawn)
        rc = runs["richat"]
        photo_r = work / "stat" / "photo_richat_01.jpg"
        photo_r.parent.mkdir()
        shutil.copy(INPUTS / "photo_richat_01.jpg", photo_r)
        out = work / "stat" / "graphic_numbers.mp4"
        render = lambda: rig.graphics.render_stat_cards(rc["stat_cards"], out, photo_r, width=1080, height=1920,
                                                        narration=rc["narration_script"])
        if has_layer_grade:
            from src import photo_grade
            with photo_grade.using(rc["grade_bias"]):
                render()
        else:
            render()
        side = json.loads(Path(str(out) + ".contrast.json").read_text(encoding="utf-8"))
        rows = side.get("elements") or []
        bare = rig.clip(cover_crop(photo_r, size).convert("RGB"), "scene_01.mp4")
        record_derived(bare, [photo_r], "parallax", {"preset": "hook", "duration": 1.0, "fps": 30, "variant": 0})
        rig.vc.picture_vignette = lambda width, height: "null"
        try:
            got = rig.master(out, "reel", rc["grade_bias"], t=0.0)
            ref = rig.master(bare, "reel", rc["grade_bias"], t=0.0)
        finally:
            rig.vc.picture_vignette = lens
        results["stat_card_richat"] = {
            "grade_bias": rc["grade_bias"], "contrast_min": side.get("min"),
            "drawn_min": min((r["drawn"] for r in rows if r.get("drawn") is not None), default=None),
            "graded_min": min((r["graded"] for r in rows if r.get("graded") is not None), default=None),
            "elements": len(rows),
            "top_band_vs_bare_photo_frame0": compare(ref[:240], got[:240])}

        note("contrast path")
        # ---- the contrast-measurement path of every graphic
        results["contrast_path"] = {}
        for key, name in (("stat_card", "graphic_numbers_gol_gumbaz.jpg"), ("hook_frame_dark", "hook_frame_nazca_dark.jpg"),
                          ("hook_frame_bright", "hook_frame_nazca_bright.jpg")):
            drawn = fitted(INPUTS / name, size)
            graded = rig.graphics.graded_frame(drawn, work / "contrast")
            results["contrast_path"][key] = compare(np.asarray(drawn), np.asarray(graded.convert("RGB")))

        note("end screens")
        # ---- 16:9 end screen over the park's three real last-scene photos
        from src import end_card
        ends = {}
        # each card shows its own dev run's share line (inputs.json, verbatim from the run's script_metadata.json)
        # as the pipeline draws it: end_card.card_text sets the follow clause aside (the card says FOLLOW itself)
        for key, name in (("wieliczka", "end_ground_wieliczka.jpg"), ("aswan", "end_ground_aswan.jpg"),
                          ("roman", "end_ground_roman.jpg")):
            share = end_card.card_text(runs[key]["share_line"])
            raw = fitted(INPUTS / name, (1920, 1080))
            still = work / f"end_{name}"
            shutil.copy(INPUTS / name, still)
            delivered = Image.fromarray(rig.master(still, "deep_dive", t=0.95))
            lens = rig.vc.picture_vignette
            rig.vc.picture_vignette = lambda width, height: "null"
            try:
                grade_only = rig.master(still, "deep_dive", t=0.95)
            finally:
                rig.vc.picture_vignette = lens
            row = {"input": name, "ground": means(np.asarray(raw), np.asarray(delivered)),
                   "ground_grade_only": means(np.asarray(raw), grade_only)}
            # the documentary master grades at bias 0 (compose_deep_dive); the topic's own bias is shown too
            topic_bias = runs[key]["grade_bias"]
            at_bias = Image.fromarray(rig.master(still, "deep_dive", topic_bias, t=0.95))
            row["topic_grade_bias"] = topic_bias
            for tag, ground in (("raw_photo", raw), ("delivered_frame", delivered), ("delivered_at_topic_bias", at_bias)):
                stats = end_screen_stats(end_card, ground, share)
                row[tag] = {"contrast_kicker": stats.get("contrast_kicker"), "contrast": stats.get("contrast"),
                            "ground_relative_luminance": round(relative_luminance(np.asarray(ground)), 4)}
            ends[key] = row
        results["end_screen"] = ends

        note("still surfaces")
        # ---- still surfaces from their run recipes (real copy, real photos)
        from src import visual_engine
        from src.multi_format import MultiFormatGenerator
        for n in ("photo_gol_gumbaz_03.jpg", "photo_gol_gumbaz_22.jpg"):
            shutil.copy(INPUTS / n, work / n)
        gen = MultiFormatGenerator()

        def digest(im):
            return hashlib.sha256(np.asarray(im.convert("RGB")).tobytes()).hexdigest()

        story, _ = gen._render_story_frame(photo, {"kicker": "ANCIENT MARVEL", "line1": "THE ACOUSTIC DOME",
                                                   "line2": "OF GOL GUMBAZ",
                                                   "body": "Sound circles this vast chamber along its curved boundary."},
                                           0, 3, {}, tight=True)
        slide, _ = gen._render_carousel_slide("cover", photo, work / "photo_gol_gumbaz_03.jpg",
                                              {"heading": "THE WHISPERING VAULT",
                                               "body": "How did seventeenth-century builders create an acoustic wonder in Bijapur, India?"},
                                              0, 7, {}, "", False, tight=True)
        cover = work / "covers" / "thumbnail_cover.jpg"
        cover.parent.mkdir()
        visual_engine.redraw_cover(cover, {"hero": str(work / "photo_gol_gumbaz_22.jpg"),
                                           "copy": {"eyebrow": "ANCIENT ACOUSTICS", "line1": "THE 144-FOOT",
                                                    "line2": "WHISPERING DOME", "tagline": "BUILT WITHOUT RINGS",
                                                    "badge": "EST. 144 FT (44 M)"},
                                           "style": "depth_sandwich", "text_cy": 760})
        with Image.open(cover) as im:
            cover_digest = digest(im)
        results["still_surfaces"] = {"story_frame_1_sha256": digest(story), "carousel_slide_1_sha256": digest(slide),
                                     "cover_sha256": cover_digest,
                                     "recipes": "run_gol_gumbaz_whispering_gallery_1793278143 stories/carousel/still_layout.json"}

    names = subprocess.run(["git", "grep", "-n", "-i", "-E", GREP, "--", "src", "scripts", "assets", "main.py"],
                           cwd=root, capture_output=True, text=True, encoding="utf-8", check=False)
    if names.returncode not in (0, 1):
        raise RuntimeError(names.stderr)
    results["name_policy"] = audit_names(root, names.stdout.splitlines())
    ramps = results["preconversion_ramps"].values()
    results["acceptance"] = {
        "graphic_tokens_delta_e_le_2": all(x["maximum_delta_e76"] <= 2 for x in results["native_graphics"].values()),
        "at_least_250_levels_before_conversion": all(x["levels"] >= 250 for x in ramps),
        "black_zero": all(x["black"] == [0, 0, 0] for x in ramps),
        "formula_lut_removed": not (root / "assets/luts/kodak_2383.cube").exists(),
        "formula_generator_removed": not (root / "scripts/generate_kodak_lut.py").exists(),
        "name_policy_with_exact_operator_exception": results["name_policy"]["passed_with_approved_exact_exception"],
    }
    results["acceptance"]["met"] = all(results["acceptance"].values())
    # every encoded figure depends on the encoder (pinned above): the clips' and the master's lossy encodes
    from src.encoding import get_video_encoder
    results["encoder"] = get_video_encoder()
    return results


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python measure.py <repository root>")
    sys.stdout.reconfigure(newline="\n")   # LF output on Windows too
    print(json.dumps(main(Path(sys.argv[1]).resolve()), indent=1))
