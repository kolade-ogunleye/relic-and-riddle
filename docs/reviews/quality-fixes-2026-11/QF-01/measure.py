"""QF-01 colour matrix: measure what a pure #FF0000 picture becomes on the real pipeline code path.

Usage: python measure.py <repo root>

Imports the pipeline from <repo root> (src/graphics.py, src/parallax.py, src/footage.py, src/motion.py,
src/video_composer.py) and runs its own encoders and its own compose_video graph on deterministic inputs made
here (solid #FF0000 frames, a solid red source video, a sine narration): nothing is read from output/ or the
gitignored photos, and no network is touched. Prints JSON and writes measurements.json beside this file.

Measured:
- clips: each intermediate clip's ffprobe colour tags and its decoded Y/U/V (native yuv420p, no conversion)
  at the centre: graphics._encode, parallax.render_clip, footage.make_segment, motion._finish.
- master_conversion: compose_video's delivered masters (9:16 and 16:9) with the film grade held at identity
  (video_composer.film_grade_filter -> "null", so the colour conversions are measured alone): the master's tags
  and the centre Y/U/V of a red still (16:9: the portrait tilt branch, which has no tone curve) and of the red
  graphics clip. Target: BT.709 limited red, Y63 U102 V240 (+-2).
- master_graded: the 9:16 master with the real film grade, against the grade applied in RGB alone and converted
  with BT.709 (the reference): the master's red graphics segment must decode to the grade's own RGB.
- caption_gold: the caption's gold key word (brand.GOLD) over a grey graphic in the 9:16 and 16:9 masters
  (grade and lens vignette at identity): the ASS header's YCbCr Matrix and the median Y/U/V of the gold glyph
  cores, against BT.709 limited brand gold (+-2).
- conversions: every swscale conversion compose_video's graph made (ffmpeg -v verbose), with the matrix on
  each side; the count of RGB->YUV conversions that did not use BT.709.
Stand-ins (no paid or model inputs): parallax.depth_map (the depth model is gitignored; the Ken Burns encode
does not use the map), motion._gate (the clip's content QA, not a colour step) and the narration audio.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
RED = (255, 0, 0)
TARGET = {"Y": 63, "U": 102, "V": 240}
TOL = 2


def bt709_limited(rgb):
    """(Y, U, V) of an 8-bit RGB colour, BT.709 limited range (ITU-R BT.709-6, 8-bit quantisation)."""
    r, g, b = (c / 255.0 for c in rgb)
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return {"Y": round(16 + 219 * y, 1), "U": round(128 + 224 * (b - y) / 1.8556, 1),
            "V": round(128 + 224 * (r - y) / 1.5748, 1)}


def probe_tags(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=pix_fmt,color_space,color_primaries,color_transfer,color_range", "-of", "json",
                          str(path)], capture_output=True, text=True, check=True, timeout=60)
    s = json.loads(out.stdout)["streams"][0]
    return {k: s.get(k, "unknown") for k in ("pix_fmt", "color_space", "color_primaries", "color_transfer",
                                             "color_range")}


def yuv_at(path: Path, t: float = 0.0, box: int = 48) -> dict:
    """Mean Y, U, V of a centred box, decoded as the file's own yuv420p planes (no conversion)."""
    import numpy as np
    info = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                           "stream=width,height", "-of", "csv=s=x:p=0", str(path)],
                          capture_output=True, text=True, check=True, timeout=60).stdout.strip().split("x")
    w, h = int(info[0]), int(info[1])
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"], capture_output=True, check=True,
                         timeout=120).stdout
    a = np.frombuffer(raw, np.uint8)
    y = a[:w * h].reshape(h, w)
    u = a[w * h:w * h * 5 // 4].reshape(h // 2, w // 2)
    v = a[w * h * 5 // 4:w * h * 3 // 2].reshape(h // 2, w // 2)
    cy, cx = h // 2, w // 2
    return {"Y": round(float(y[cy - box:cy + box, cx - box:cx + box].mean()), 1),
            "U": round(float(u[cy // 2 - box // 2:cy // 2 + box // 2, cx // 2 - box // 2:cx // 2 + box // 2].mean()), 1),
            "V": round(float(v[cy // 2 - box // 2:cy // 2 + box // 2, cx // 2 - box // 2:cx // 2 + box // 2].mean()), 1)}


def within(m: dict, target: dict, tol: float = TOL) -> bool:
    return all(abs(m[k] - target[k]) <= tol for k in ("Y", "U", "V"))


def tagged_bt709(tags: dict) -> bool:
    return all(tags.get(k) == "bt709" for k in ("color_space", "color_primaries", "color_transfer"))


def sine(path: Path, seconds: float) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000",
                    "-af", "volume=0.3", "-ac", "1", "-t", f"{seconds:.2f}", str(path)], check=True, timeout=60)
    return path


def red_source(path: Path, w: int, h: int, seconds: float, sd_untagged: bool = False) -> Path:
    """A solid red source video: encoded BT.709 limited and tagged, as a stock HD clip is; or (sd_untagged)
    BT.601 with no colour tags, as an archive SD film often is."""
    if sd_untagged:
        vf, tags = "format=rgb24,scale=out_color_matrix=bt601:out_range=tv,format=yuv420p", []
    else:
        vf = ("format=rgb24,scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,"
              "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709")
        tags = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0xFF0000:s={w}x{h}:r=30",
                    "-t", f"{seconds:.2f}", "-vf", vf, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "1",
                    *tags, str(path)], check=True, timeout=120)
    return path


# swscale's per-link line ("w:.. h:.. fmt:.. csp:.. range:.. -> w:.. ..."). ffmpeg's threads can glue it onto
# another filter's message, so the name is the line's own prefix (the logging filter, not always the scaler).
SCALE_LINE = re.compile(r"\[(?P<name>[A-Za-z0-9_]+) @ [0-9a-fA-Fx]+\][^\n]*?w:\d+ h:\d+ fmt:(?P<f0>\S+) "
                        r"(?:csp:(?P<c0>\S+) )?range:(?P<r0>\S+)[^\n]*?-> w:\d+ h:\d+ fmt:(?P<f1>\S+) "
                        r"(?:csp:(?P<c1>\S+) )?range:(?P<r1>\S+)")


def is_rgb(fmt: str) -> bool:
    return fmt.startswith(("rgb", "bgr", "gbr", "argb", "abgr", "0rgb", "rgba", "bgra"))


def conversions(stderr: str) -> list[dict]:
    seen, out = set(), []
    for m in SCALE_LINE.finditer(stderr):
        d = m.groupdict()
        key = (d["name"], d["f0"], d["c0"], d["f1"], d["c1"], d["r0"], d["r1"])
        if key in seen:
            continue
        seen.add(key)
        kind = ("rgb->yuv" if is_rgb(d["f0"]) and not is_rgb(d["f1"]) else
                "yuv->rgb" if not is_rgb(d["f0"]) and is_rgb(d["f1"]) else
                "rgb->rgb" if is_rgb(d["f0"]) else "yuv->yuv")
        out.append({"filter": d["name"], "kind": kind, "from": f"{d['f0']} csp:{d['c0']} range:{d['r0']}",
                    "to": f"{d['f1']} csp:{d['c1']} range:{d['r1']}", "to_csp": d["c1"], "from_csp": d["c0"]})
    return out


def main(root: Path) -> dict:
    sys.path.insert(0, str(root))
    import numpy as np
    from PIL import Image

    from src import footage, graphics, motion, parallax
    from src import video_composer as vc
    from src.delivery_profiles import get_delivery_profile

    result: dict = {"repo": "<repo root argument>", "target_bt709_red": TARGET, "tolerance": TOL,
                    "bt709_red_formula": bt709_limited(RED), "bt601_red_formula_note": "BT.601 limited red is Y81 U90 V240",
                    "clips": {}, "master_conversion": {}, "master_graded": {}}
    try:
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], capture_output=True, check=False,
                              text=True, timeout=30).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--", "src"], capture_output=True, check=False,
                               text=True, timeout=30).stdout.strip()
        result["commit"] = head + (" (src modified)" if dirty else "")
    except OSError:
        pass
    work = Path(tempfile.mkdtemp(prefix="qf01_"))

    # ---- intermediate clips -----------------------------------------------------------------------------
    gfx_v = graphics._encode((Image.new("RGB", (1080, 1920), RED) for _ in range(60)), work / "gfx_916.mp4", 60,
                             1080, 1920)
    gfx_w = graphics._encode((Image.new("RGB", (1920, 1080), RED) for _ in range(60)), work / "gfx_169.mp4", 60,
                             1920, 1080)
    still_png = work / "red_still.png"
    Image.new("RGB", (1200, 1600), RED).save(still_png)   # portrait: the 16:9 tilt branch (no tone curve)
    clips = {"graphics._encode": gfx_v}

    parallax.depth_map = lambda rgb: np.zeros(rgb.shape[:2], np.float32)   # stand-in: the model is gitignored
    par = work / "parallax_01.mp4"
    try:
        parallax.render_clip(still_png, par, 1.0, size=(1080, 1920))
        clips["parallax.render_clip"] = par
    except Exception as e:  # noqa: BLE001
        result["clips"]["parallax.render_clip"] = {"error": str(e)[:300]}

    src_hd = red_source(work / "source_hd.mp4", 1080, 1920, footage.SEGMENT_SECONDS + 1.0)
    seg = work / "footage_01.mp4"
    footage.make_segment(src_hd, footage.probe(src_hd), seg)
    clips["footage.make_segment"] = seg
    src_sd = red_source(work / "source_sd.mp4", 640, 480, footage.SEGMENT_SECONDS + 1.0, sd_untagged=True)
    seg_sd = work / "footage_02.mp4"
    footage.make_segment(src_sd, footage.probe(src_sd), seg_sd)
    clips["footage.make_segment (untagged SD BT.601 source)"] = seg_sd

    motion._gate = lambda out, archetype="megastructure", keep=True: {}   # stand-in: content QA, not colour
    raw = red_source(work / "motion_raw.mp4", 1080, 1920, motion.SECONDS_OUT + 0.5)
    mot = work / "motion_01.mp4"
    motion._finish(raw, mot)
    clips["motion._finish"] = mot

    for name, path in clips.items():
        tags, yuv = probe_tags(path), yuv_at(path, 0.2)
        result["clips"][name] = {"tags": tags, "yuv": yuv, "bt709_tagged": tagged_bt709(tags),
                                 "yuv_ok": within(yuv, TARGET)}

    # ---- the composer's graph ---------------------------------------------------------------------------
    vo_dur = 3.0
    vo = sine(work / "vo.wav", vo_dur + 0.5)
    words = [{"word": "Red.", "start": 0.2, "dur": 0.4}]
    stderr_log: list[str] = []

    def verbose_ffmpeg(cmd):
        cmd = [str(c) for c in cmd]
        if cmd and cmd[0] == "ffmpeg" and "-filter_complex" in cmd:
            cmd = [cmd[0], "-v", "verbose", *cmd[1:]]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900, check=False)
        if res.returncode != 0:
            raise RuntimeError("ffmpeg failed: " + res.stderr[-1500:])
        if "-filter_complex" in cmd:
            stderr_log.append(res.stderr)
        return res

    vc._run_ffmpeg = verbose_ffmpeg
    real_grade = vc.film_grade_filter

    def compose(name, scenes, profile):
        comp = vc.VideoComposer()
        out = work / f"{name}.mp4"
        comp.compose_video(scenes, vo, vo, words, vo_dur, out, durations=[vo_dur / len(scenes)] * len(scenes),
                           delivery_profile=get_delivery_profile(profile))
        return out

    vc.film_grade_filter = lambda *a, **k: "null"     # identity grade: the conversions alone
    stderr_log.clear()
    m916 = compose("master_916_identity_grade", [gfx_v, gfx_v], "reel")
    conv_916 = conversions("\n".join(stderr_log))
    stderr_log.clear()
    m169 = compose("master_169_identity_grade", [still_png, gfx_w], "deep_dive")
    conv_169 = conversions("\n".join(stderr_log))
    vc.film_grade_filter = real_grade
    for name, path, segs in (("9:16 graphics clip", m916, {"graphics clip": 0.5}),
                             ("16:9 still + graphics clip", m169, {"still (tilt branch)": 0.45,
                                                                    "graphics clip": 2.6})):
        tags = probe_tags(path)
        entry = {"tags": tags, "bt709_tagged": tagged_bt709(tags), "segments": {}}
        for seg_name, t in segs.items():
            yuv = yuv_at(path, t)
            entry["segments"][seg_name] = {"t": t, "yuv": yuv, "yuv_ok": within(yuv, TARGET)}
        result["master_conversion"][name] = entry

    # The real grade: the master's red graphics segment against the grade applied in RGB alone, BT.709-converted.
    stderr_log.clear()
    mg = compose("master_916_graded", [gfx_v, gfx_v], "reel")
    conv_graded = conversions("\n".join(stderr_log))
    (work / "graded_verbose.log").write_text("\n".join(stderr_log), encoding="utf-8")
    ref =subprocess.run(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "64x64", "-i", "-",
                          "-vf", f"format=rgb24,{real_grade(0.0)},format=rgb24", "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         input=bytes(RED) * 64 * 64, capture_output=True, check=True, timeout=60).stdout
    ref_rgb = tuple(int(c) for c in np.frombuffer(ref, np.uint8).reshape(-1, 3)[64 * 32 + 32])
    expected = bt709_limited(ref_rgb)
    got = yuv_at(mg, 0.5)
    result["master_graded"] = {"tags": probe_tags(mg), "graded_red_rgb_reference": ref_rgb,
                               "expected_yuv_bt709": expected, "yuv": got,
                               "yuv_ok": within(got, expected, TOL)}

    # The gold key word of a caption (brand.GOLD) over a grey graphic, grade and lens vignette held at identity:
    # libass writes an ASS file's colours with the matrix its header names (no "YCbCr Matrix" line: TV.601), so
    # a BT.709 master needs the header to say TV.709. Median Y/U/V of the gold glyph cores.
    from src import brand
    grey_v = graphics._encode((Image.new("RGB", (1080, 1920), (128, 128, 128)) for _ in range(75)),
                              work / "grey_916.mp4", 75, 1080, 1920)
    grey_w = graphics._encode((Image.new("RGB", (1920, 1080), (128, 128, 128)) for _ in range(75)),
                              work / "grey_169.mp4", 75, 1920, 1080)
    gold_words = [{"word": "The", "start": 0.2, "dur": 0.3}, {"word": "GOLDEN", "start": 0.5, "dur": 0.6},
                  {"word": "gate.", "start": 1.1, "dur": 0.4}]
    gold_expected = bt709_limited(brand.GOLD)
    real_vignette = vc.picture_vignette
    vc.film_grade_filter = lambda *a, **k: "null"
    vc.picture_vignette = lambda *a, **k: "null"
    result["caption_gold"] = {"brand_gold_rgb": list(brand.GOLD), "expected_yuv_bt709": gold_expected,
                              "bt601_values_note": "BT.601 values for brand gold are about Y175 U60 V164", "masters": {}}
    for name, scene, profile, t in (("9:16", grey_v, "reel", 0.8), ("16:9", grey_w, "deep_dive", 0.8)):
        out = work / f"caption_gold_{profile}.mp4"
        vc.VideoComposer().compose_video([scene], vo, vo, gold_words, 2.0, out, emphasis=["GOLDEN"],
                                         delivery_profile=get_delivery_profile(profile))
        ass_text = next(work.glob(f"caption_gold_{profile}*.ass")).read_text(encoding="utf-8")
        m = re.search(r"^YCbCr Matrix:\s*(.+)$", ass_text, re.MULTILINE)
        w, h = (int(x) for x in subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
             "-of", "csv=s=x:p=0", str(out)], capture_output=True, text=True, check=True, timeout=60).stdout.split("x"))
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(out), "-frames:v", "1",
                              "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"], capture_output=True, check=True,
                             timeout=120).stdout
        a = np.frombuffer(raw, np.uint8)
        y = a[:w * h].reshape(h, w)[::2, ::2].astype(float)
        u = a[w * h:w * h * 5 // 4].reshape(h // 2, w // 2).astype(float)
        v = a[w * h * 5 // 4:w * h * 3 // 2].reshape(h // 2, w // 2).astype(float)
        core = (u < 80) & (v > 150) & (y > 150)    # gold glyph cores (grey is U128 V128, bone and ink are neutral)
        got = ({"Y": float(np.median(y[core])), "U": float(np.median(u[core])), "V": float(np.median(v[core]))}
               if core.sum() > 200 else None)
        result["caption_gold"]["masters"][name] = {
            "t": t, "ass_ycbcr_matrix": m.group(1).strip() if m else "(none: libass reads TV.601)",
            "gold_pixels": int(core.sum()), "yuv_median": got,
            "yuv_ok": bool(got) and within(got, gold_expected)}
    vc.film_grade_filter, vc.picture_vignette = real_grade, real_vignette

    def summary(conv):
        rgb_yuv = [c for c in conv if c["kind"] == "rgb->yuv"]
        return {"all": conv,
                "rgb_to_yuv": len(rgb_yuv),
                "rgb_to_yuv_not_bt709": sum(1 for c in rgb_yuv if c["to_csp"] != "bt709"),
                "yuv_to_rgb_not_bt709": sum(1 for c in conv if c["kind"] == "yuv->rgb" and c["from_csp"] != "bt709"),
                "yuv_to_yuv_matrix_change": sum(1 for c in conv if c["kind"] == "yuv->yuv"
                                                and c["from_csp"] != c["to_csp"])}
    result["conversions"] = {"9:16 identity grade": summary(conv_916), "16:9 identity grade": summary(conv_169),
                             "9:16 real grade": summary(conv_graded)}

    clips_ok = all(c.get("bt709_tagged") and c.get("yuv_ok") for c in result["clips"].values())
    master_ok = all(e["bt709_tagged"] and all(s["yuv_ok"] for s in e["segments"].values())
                    for e in result["master_conversion"].values())
    conv_ok = all(s["rgb_to_yuv_not_bt709"] == 0 and s["yuv_to_rgb_not_bt709"] == 0
                  and s["yuv_to_yuv_matrix_change"] == 0 for s in result["conversions"].values())
    result["acceptance"] = {"clips_bt709_and_red": clips_ok, "master_bt709_and_red": master_ok,
                            "graded_master_matches_bt709_reference": result["master_graded"]["yuv_ok"],
                            "caption_gold_bt709": all(e["yuv_ok"] for e in result["caption_gold"]["masters"].values()),
                            "no_non_bt709_conversion": conv_ok}
    result["acceptance"]["met"] = all(result["acceptance"].values())
    return result


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python measure.py <repo root>")
    res = main(Path(sys.argv[1]).resolve())
    text = json.dumps(res, indent=2)
    (HERE / "measurements.json").write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)
