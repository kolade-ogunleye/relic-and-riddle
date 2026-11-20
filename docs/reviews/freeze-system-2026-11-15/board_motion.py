"""The board's motion evidence (cycle 0 round 8, lane BOARD; panel MO-1, MO-8, MO-9, consensus 8): production-encoder
clips for rows 7, 9 and 12, the row-12 strips (hook hold and fade, the end card's rise, the act dissolve, the 9:16
end-card reveal, the 16:9 end screen, the loop bridge) each with its alpha / luma curves at real frame times, and the
motion-token table (durations, eases, overshoot) as this tree holds and draws them.

Every sequence is drawn ONCE by src exactly as the pipeline calls it, streamed into ONE encode with the delivery
encoder (encoding.delivery_video_encoder: libx264 at the locked preset and CRF, BT.709 limited range, tagged), and
decoded ONCE: the strips and the curves are read from the decoded clip, i.e. from the delivered pixels (MO5). Nothing
is written outside `work` and the board's out dir; the clips are kept in <out>/clips.
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np

FPS = 30
_cache: dict[str, Any] = {}


# ---------------------------------------------------------------------------------------------- encode / decode
def _graph() -> str:
    from src.encoding import to_bt709
    return f"scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,{to_bt709('bt709')}"


def encode(frames: Iterable, path: Path, size: tuple[int, int], fps: int = FPS) -> dict[str, Any]:
    """RGB frames -> one H.264 clip by the delivery encoder (the master's colour tags). {path, frames, sha256, args}."""
    from src.encoding import delivery_video_encoder
    w, h = size
    args = ["-vf", _graph(), *delivery_video_encoder(), "-pix_fmt", "yuv420p", "-color_range", "tv",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-r", str(fps)]
    path.parent.mkdir(parents=True, exist_ok=True)
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                          "-r", str(fps), "-i", "-", *args, str(path)], stdin=subprocess.PIPE)
    n = 0
    try:
        for im in frames:
            if im.size != (w, h):
                raise ValueError(f"{path.name}: frame {n} is {im.size}, not {size}")
            p.stdin.write(im.convert("RGB").tobytes())
            n += 1
    finally:
        p.stdin.close()
        p.wait(timeout=600)
    if p.returncode:
        raise RuntimeError(f"ffmpeg failed on {path.name}")
    return {"path": path, "frames": n, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "encoder": " ".join(args)}


def decode(path: Path, size: tuple[int, int]):
    """The clip's frames as delivered (decoded as BT.709, limited range), one at a time."""
    from PIL import Image

    from src.encoding import BT709_TO_RGB
    w, h = size
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"{BT709_TO_RGB},format=rgb24",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    step = w * h * 3
    try:
        while True:
            buf = p.stdout.read(step)
            if len(buf) < step:
                break
            yield Image.frombytes("RGB", (w, h), buf)
    finally:
        p.stdout.close()
        p.wait(timeout=120)


def luma(im, box=None) -> float:
    a = np.asarray(im.crop(box) if box else im, np.float32)
    return float((a[..., 0] * 0.2126 + a[..., 1] * 0.7152 + a[..., 2] * 0.0722).mean())


class Seq:
    """One sequence as delivered: per-frame mean luma, per-region luma, the frame-to-frame MAD, the kept frames."""

    def __init__(self, name: str, clip: dict[str, Any], size, regions: dict[str, tuple] | None, keep: Callable[[int], bool]):
        self.name, self.clip, self.size = name, clip, size
        self.luma: list[float] = []
        self.mad: list[float] = []
        self.regions = {k: [] for k in (regions or {})}
        self.kept: dict[int, Any] = {}
        prev = None
        for i, im in enumerate(decode(clip["path"], size)):
            small = np.asarray(im.resize((size[0] // 4, size[1] // 4)), np.float32)
            self.luma.append(luma(im))
            self.mad.append(float(np.abs(small - prev).mean()) if prev is not None else 0.0)
            prev = small
            for k, box in (regions or {}).items():
                self.regions[k].append(luma(im, box))
            if keep(i):
                self.kept[i] = im
        self.n = len(self.luma)

    def t(self, i: int) -> float:
        return i / FPS


# ---------------------------------------------------------------------------------------------- plots
def plot(series: dict[str, tuple[list[float], list[float]]], title: str, y_label: str, y_range: tuple[float, float],
         marks: dict[str, float] | None = None, size=(1500, 620), x_label: str = "time (s)"):
    """A line plot with real axes: x in seconds (ticks every 0.1 s, labelled every 0.5 s), y in `y_label`."""
    from PIL import Image, ImageDraw, ImageFont
    W, H = size
    L, R, T, B = 110, 330, 60, 80
    im = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(im)
    f = ImageFont.load_default(size=18)
    xs = [x for v in series.values() for x in v[0]] or [0.0, 1.0]
    x0, x1 = 0.0, max(xs) if max(xs) > 0 else 1.0
    y0, y1 = y_range

    def px(x, y):
        return L + (W - L - R) * (x - x0) / (x1 - x0), H - B - (H - T - B) * (y - y0) / ((y1 - y0) or 1)
    d.text((L + 160, 12), title, fill=(0, 0, 0), font=ImageFont.load_default(size=22))
    d.rectangle([L, T, W - R, H - B], outline=(0, 0, 0))
    k = 0
    while k * 0.1 <= x1 + 1e-9:
        x = k * 0.1
        X, _ = px(x, y0)
        d.line([(X, H - B), (X, H - B + (8 if k % 5 == 0 else 4))], fill=(0, 0, 0))
        if k % 5 == 0:
            d.line([(X, T), (X, H - B)], fill=(230, 230, 230))
            d.text((X - 14, H - B + 10), f"{x:.1f}", fill=(0, 0, 0), font=f)
        k += 1
    d.text(((W - R + L) / 2 - 60, H - 30), x_label, fill=(0, 0, 0), font=f)
    for j in range(5):
        y = y0 + (y1 - y0) * j / 4
        _, Y = px(x0, y)
        d.line([(L - 6, Y), (L, Y)], fill=(0, 0, 0))
        d.line([(L, Y), (W - R, Y)], fill=(235, 235, 235))
        d.text((10, Y - 9), f"{y:g}", fill=(0, 0, 0), font=f)
    d.text((10, T - 30), y_label, fill=(0, 0, 0), font=f)
    colours = [(200, 40, 40), (40, 110, 200), (30, 150, 70), (150, 70, 160), (210, 130, 0), (80, 80, 80),
               (0, 150, 160), (120, 60, 20)]
    for n, (name, (tx, vy)) in enumerate(series.items()):
        c = colours[n % len(colours)]
        pts = [px(x, max(y0, min(y1, y))) for x, y in zip(tx, vy)]
        if len(pts) > 1:
            d.line(pts, fill=c, width=3)
        for p in pts:
            d.ellipse([p[0] - 2, p[1] - 2, p[0] + 2, p[1] + 2], fill=c)
        d.text((W - R + 14, T + 26 * n), name, fill=c, font=f)
    for n, (name, x) in enumerate((marks or {}).items()):
        X, _ = px(x, y0)
        d.line([(X, T), (X, H - B)], fill=(120, 120, 120), width=1)
        d.text((X + 3, T + 4 + 20 * (n % 4)), name, fill=(90, 90, 90), font=ImageFont.load_default(size=15))
    return im


def strip(seq: Seq, crop=None, scale_h: int = 360, label: str = "") -> list[tuple[str, Any]]:
    """The kept frames as tiles, each labelled with its frame number and real time."""
    from PIL import Image
    out = []
    for i, im in sorted(seq.kept.items()):
        tile = im.crop(crop) if crop else im
        if scale_h and tile.height != scale_h:
            tile = tile.resize((max(1, round(tile.width * scale_h / tile.height)), scale_h), Image.LANCZOS)
        out.append((f"{label}f{i:03d} t={i / FPS:.3f}s", tile))
    return out


# ---------------------------------------------------------------------------------------------- sequences
def _fx():
    import fixtures as fx
    return fx


def _still(fx, src):
    from PIL import Image
    return (Image.open(fx.frame(src, last=True)) if str(src).endswith(".mp4") else Image.open(fx.path(src))).convert("RGB")


def _card_share(fx, case) -> str:
    """The 9:16 end card's line as the composer makes it (QF-35a render_surfaces.card_share)."""
    import json

    from src.end_card import card_text
    from src.script_engine import end_card_share
    p = fx.run_file(case["run"], "verified_script.json")
    if p.is_file():
        return end_card_share(json.loads(p.read_text(encoding="utf-8")))[0]
    return card_text(case["share_line"])


def _case(fx, topic: str, fmt: str):
    """(index, case) of the catalogue's end-card case for `topic` in `fmt`, or (None, None) when it has none."""
    cases = fx.load("endcard")
    return next(((i, c) for i, c in enumerate(cases) if c["topic"] == topic and c.get("format") == fmt), (None, None))


END_TOPIC = "unfinished_obelisk_aswan"   # the first board ground: row 12's unsuffixed sheets (as before cycle 3)
# cycle 3 (MO r1/r2, p3 MO-5): every ground gets each end sequence its catalogue case allows, and the loop bridge both
# ways it can ship: with FOLLOW (config.LOOP_FOLLOW_OVERLAY) and without (the composer's QF-83 fallback ladder)
END_NAMES = ("endscreen_169", "endcard_916", "bridge_follow_916", "bridge_916")
PREFETCH: dict[tuple[str, str], Any] = {}   # (topic, name) -> Future of board_evidence.captured(draw_end, ...)


def end_topics(heroes) -> list[str]:
    """The board grounds (`heroes`, in board order, END_TOPIC first) that have an end-card case in the catalogue."""
    fx = _fx()
    topics = {c["topic"] for c in fx.load("endcard")}
    order = [END_TOPIC] + [h for h in heroes if h != END_TOPIC]
    return [h for h in order if h in topics]


def end_names(topic: str) -> list[str]:
    """The end sequences `topic`'s catalogue cases allow (END_NAMES order)."""
    fx = _fx()
    have = {c.get("format") for c in fx.load("endcard") if c["topic"] == topic}
    return [n for n in END_NAMES if ("16:9" if n.endswith("_169") else "9:16") in have]


def _end_jobs(topic: str) -> dict[str, Any]:
    """name -> (size, make(stats) -> the frames, case index, case): the end sequences of `topic` as end_sequences
    draws them (end_names: the formats its catalogue cases have)."""
    from PIL import Image, ImageOps

    from src import end_card
    fx = _fx()
    iw, wide = _case(fx, topic, "16:9")
    it, tall = _case(fx, topic, "9:16")
    jobs: dict[str, Any] = {}
    if wide is not None:
        last_w = _still(fx, wide.get("last_frame", {}).get("from") or wide.get("last"))
        if last_w.width < last_w.height:
            last_w = ImageOps.fit(last_w, (1920, 1080), Image.LANCZOS)
        jobs["endscreen_169"] = ((1920, 1080), lambda st: end_card.frames(last_w, wide["share_line"], st), iw, wide)
    if tall is not None:
        last_t = _still(fx, tall.get("last_frame", {}).get("from") or tall.get("last"))
        first = Image.open(fx.frame(tall["first_frame"]["from"], 0.0)).convert("RGB")
        # the line the composer passes (render_surfaces.card_share: end_card_share of the run), not the raw one
        jobs["endcard_916"] = ((1080, 1920), lambda st: end_card.frames(last_t, _card_share(fx, tall), st), it, tall)
        jobs["bridge_follow_916"] = ((1080, 1920), lambda st: _then(end_card.loop_bridge(
            last_t, first, tall["share_line"], st, line=False, follow=True), first), it, tall)
        # without FOLLOW, as the composer ships it when FOLLOW is off or dropped (line=end_card.LOOP_LINE)
        jobs["bridge_916"] = ((1080, 1920), lambda st: _then(end_card.loop_bridge(
            last_t, first, tall["share_line"], st, line=end_card.LOOP_LINE, follow=False), first), it, tall)
    return {n: jobs[n] for n in END_NAMES if n in jobs}


def draw_end(clips: Path, topic: str, name: str) -> dict[str, Any]:
    """One end sequence drawn and encoded (the clip, or None when refused, with the renderer's stats), as
    end_sequences does it inline: board.py --workers runs it in a worker process."""
    import board_evidence as be
    t0 = time.perf_counter()
    size, make, idx, _case_ = _end_jobs(topic)[name]
    stats: dict[str, Any] = {}
    path = clips / f"{name}__{idx}_{topic}.mp4"
    clip = be.guarded(f"{name} ({topic}, case {idx})", encode, make(stats), path, size)
    if clip is None:
        path.unlink(missing_ok=True)
    return {"clip": clip, "stats": stats, "seconds": time.perf_counter() - t0}


def end_sequences(clips: Path, topic: str = END_TOPIC) -> dict[str, Any]:
    """The 16:9 end screen, the 9:16 end card and the 9:16 loop bridge with and without FOLLOW (into the Reel's frame
    0) of the catalogue's `topic` cases, each drawn once, encoded once, decoded once (drawn and encoded by a worker
    when board.py --workers prefetched it)."""
    done = _cache.setdefault("end", {})
    if topic in done:
        return done[topic]
    out: dict[str, Any] = {}
    jobs = _end_jobs(topic)
    for name, (size, make, idx, case) in jobs.items():
        t0 = time.perf_counter()
        import board_evidence as be   # round 8: a refusal is recorded, the other sequences go on
        if (topic, name) in PREFETCH:
            got = be.replay(PREFETCH.pop((topic, name)))
            clip, stats, drawn = got["clip"], got["stats"], got["seconds"]
        else:
            stats: dict[str, Any] = {}
            path = clips / f"{name}__{idx}_{topic}.mp4"
            clip = be.guarded(f"{name} ({topic}, case {idx})", encode, make(stats), path, size)
            drawn = 0.0
            if clip is None:
                path.unlink(missing_ok=True)
        if clip is None:
            continue
        regions = _end_regions(name, stats, size)
        every = 3 if clip["frames"] > 60 else 1
        seq = Seq(name, clip, size, regions, keep=lambda i, e=every, n=clip["frames"]: i % e == 0 or i == n - 1)
        seq.stats, seq.case, seq.index, seq.topic = stats, case, idx, topic
        seq.seconds = round(drawn + time.perf_counter() - t0, 1)
        out[name] = seq
    done[topic] = out
    return out


def all_end() -> list[tuple[str, str, Any]]:
    """(topic, name, seq) of every end sequence drawn so far in this process."""
    return [(t, n, s) for t, seqs in (_cache.get("end") or {}).items() for n, s in seqs.items()]


def _then(it, first):
    yield from it
    yield first   # the Reel's frame 0, the frame the loop lands on


def _end_regions(name: str, stats: dict[str, Any], size) -> dict[str, tuple]:
    """Element boxes from the renderer's own stats: the masthead, the share block, the FOLLOW; and the field."""
    w, h = size
    reg: dict[str, tuple] = {}
    m = (stats.get("masthead") or {}).get("box")
    if m:
        reg["masthead box"] = tuple(int(round(v)) for v in m)
    if stats.get("box") and not name.startswith("bridge"):
        # end_card's layout box is the FOLLOW line's when the card has one (end_card.held_layout), else the share
        # block's (cycle 3: it was labelled "share block" either way)
        reg["FOLLOW line" if stats.get("follow") else "share block"] = tuple(int(round(v)) for v in stats["box"])
    if stats.get("follow_box"):
        reg["FOLLOW box"] = tuple(int(round(v)) for v in stats["follow_box"])
    if name == "endscreen_169":   # the field: the text column (left) and under the video slot (right)
        reg["field x 300-500"] = (300, round(h * 0.22), 500, h)
        reg["field x 1500-1700"] = (1500, round(h * 0.22), 1700, h)
    return {k: (max(0, b[0]), max(0, b[1]), min(w, max(b[0] + 1, b[2])), min(h, max(b[1] + 1, b[3])))
            for k, b in reg.items()}


def hook_sequence(clips: Path) -> dict[str, Any]:
    """The hook card (the tests' card, render_surfaces.py's grounds) on the first ground it is not refused on, every
    frame, encoded once. {seq, ground, refused: {ground: reason}}."""
    if "hook" in _cache:
        return _cache["hook"]
    fx = _fx()
    from src import config
    from src import design_cards as dc
    config.FACT_RECORD_KEY = "test-only-fact-record-key-0123456789abcdef"   # the tests' key, this process only
    from tests import test_freeze_cards_2026_11_04 as fc
    heroes = {h["topic"]: fx.path(h["photo"]) for h in fx.section("cards")["hero_photos"]}
    grounds = {"richat": fx.still("richat_structure_sahara_eye_01_commons_960.jpg"),
               "obelisk": heroes["unfinished_obelisk_aswan"], "amber": heroes["amber_room_heist"]}
    import board_evidence as be
    refused, res = {}, {}
    for g, ph in grounds.items():
        try:
            plan = fc.hook_plan(ph)
        except be._refusal_type() as e:   # the one refusal type (CardRefused is one): recorded, next ground
            refused[g] = str(e)[:200]
            continue
        t0 = time.perf_counter()
        path = clips / f"hook__{g}__916.mp4"
        clip = be.guarded(f"card__hook__{g}__916 (clip)", lambda: encode(
            (dc.frame_at(plan, k).convert("RGB") for k in range(plan.frames)), path, dc.frame_at(plan, 0).size))
        if clip is None:
            path.unlink(missing_ok=True)
            refused[g] = "a frame refused while drawing (see the refusal table)"
            continue
        seq = Seq("hook", clip, dc.frame_at(plan, 0).size, None, keep=lambda i: True)
        seq.seconds = round(time.perf_counter() - t0, 1)
        res = {"seq": seq, "ground": g, "claim": " / ".join(fc.claim_lines(plan)), "plan_frames": plan.frames}
        break
    res["refused"] = refused
    _cache["hook"] = res
    return res


def act_dissolve_spec() -> dict[str, Any]:
    """The act dissolve as this tree's composer drives it, read from its code, never assumed: the length from
    video_composer.act_dissolve_s() (round 8: tok("motion.act_dissolve") in whole frames), else the older literal
    `fades = [N / VIDEO_FPS`; the transition from its xfade line. {seconds, frames, transition, source}; a value
    that cannot be found is None and `source` says so (an evidence table never raises)."""
    out: dict[str, Any] = {"seconds": None, "frames": None, "transition": None, "source": "not found"}
    try:
        from src import video_composer as vc
        text = Path(inspect.getsourcefile(vc)).read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        out["source"] = f"not found (video_composer: {type(e).__name__})"
        return out
    fn = getattr(vc, "act_dissolve_s", None)
    if callable(fn):
        try:
            out.update(seconds=float(fn()), source="video_composer.act_dissolve_s() (tok motion.act_dissolve)")
        except Exception as e:  # noqa: BLE001
            out["source"] = f"not found (act_dissolve_s raised {type(e).__name__}: {e})"
    else:
        m = re.search(r"fades\s*=\s*\[\s*(\d+)\s*/\s*VIDEO_FPS", text)
        if m:
            out.update(seconds=int(m.group(1)) / FPS, source="video_composer.py literal `fades = [N / VIDEO_FPS`")
    if out["seconds"] is not None:
        out["frames"] = round(out["seconds"] * FPS)
    m = re.search(r"xfade=transition=(\w+)", text)
    out["transition"] = m.group(1) if m else None
    return out


def act_dissolve(dev: Path, clips: Path, run: str) -> dict[str, Any] | None:
    """The act-change dissolve as video_composer builds it (ffmpeg xfade with the composer's own transition and
    length, act_dissolve_spec) between two of the run's real scene stills at 9:16, held still (the camera push is left
    out, so only the dissolve moves), through the delivery encoder. None (with the reason in "missing") when the
    tree's dissolve cannot be read."""
    if "dissolve" in _cache:
        return _cache["dissolve"]
    from PIL import Image, ImageOps

    from src.encoding import delivery_video_encoder
    import board_extras as bx
    spec = act_dissolve_spec()
    if spec["seconds"] is None or spec["transition"] is None:
        _cache["dissolve"] = {"missing": f"act dissolve not drawn: {spec}"}
        return _cache["dissolve"]
    t0 = time.perf_counter()
    stills = bx._scene_stills(dev / "output" / run)[:2]
    size = (1080, 1920)
    paths = []
    for k, s in enumerate(stills):
        p = clips / f"_dissolve_{k}.png"
        ImageOps.fit(Image.open(s).convert("RGB"), size, Image.LANCZOS).save(p)
        paths.append(p)
    fade, tr = spec["seconds"], spec["transition"]
    hold = 0.5
    path = clips / f"act_dissolve__{run[4:]}__916.mp4"
    graph = (f"[0:v]format=rgb24[a];[1:v]format=rgb24[b];[a][b]xfade=transition={tr}:duration={fade:.9f}:"
             f"offset={hold:.9f},{_graph()}[v]")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", str(FPS), "-t", f"{hold + fade:.4f}",
                    "-i", str(paths[0]), "-loop", "1", "-framerate", str(FPS), "-t", f"{hold + fade + hold:.4f}",
                    "-i", str(paths[1]), "-filter_complex", graph, "-map", "[v]", *delivery_video_encoder(),
                    "-pix_fmt", "yuv420p", "-color_range", "tv", "-color_primaries", "bt709", "-color_trc", "bt709",
                    "-colorspace", "bt709", "-r", str(FPS), str(path)], check=True, capture_output=True, timeout=300)
    clip = {"path": path, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "encoder": f"xfade=transition={tr}:duration={fade:.4f} then " + " ".join(delivery_video_encoder())}
    a = np.asarray(Image.open(paths[0]).resize((270, 480)), np.float32)
    b = np.asarray(Image.open(paths[1]).resize((270, 480)), np.float32)
    seq = Seq("act_dissolve", clip, size, None,
              keep=lambda i: round(hold * FPS) - 2 <= i <= round((hold + fade) * FPS) + 1)
    clip["frames"] = seq.n
    mix, nearer_b = [], []
    for im in decode(path, size):   # the mix per frame: least squares of f = (1-m) a + m b; and the share of
        f = np.asarray(im.resize((270, 480)), np.float32)   # pixels nearer b (a dither dissolve jumps per pixel)
        dab = (b - a).ravel()
        mix.append(float(np.clip(((f - a).ravel() @ dab) / max(1e-6, dab @ dab), 0, 1)))
        nearer_b.append(float((np.abs(f - b).sum(-1) < np.abs(f - a).sum(-1)).mean()))
    for p in paths:
        p.unlink(missing_ok=True)
    seq.seconds = round(time.perf_counter() - t0, 1)
    out = {"seq": seq, "mix": mix, "nearer_b": nearer_b, "fade_s": fade, "hold_s": hold, "spec": spec,
           "stills": [Path(s).name for s in stills]}
    _cache["dissolve"] = out
    return out


INK_OVER_GROUND = 60   # luma steps over the row's ground: the card's light type on its dimmed ground
GROUND_PCT = 20       # the row's ground: its 20th luma percentile (type covers well under 80 % of a row)


def share_block_box(seq: Seq, rest_i: int) -> tuple[tuple[int, int, int, int] | None, str]:
    """(box, how) of the share block's type on the rest frame, measured (cycle 3, MO r1 MO-evid-rise-B, MO r2 MO-EV2:
    the layout box is the FOLLOW line's when the card has one, so a crop from it missed the share block). The
    renderer's records bound the search: below the masthead's box, above the FOLLOW line's box (or to the share
    box's foot plus one line), across the safe width; inside it the type's rows are those with pixels
    INK_OVER_GROUND above the row's ground (GROUND_PCT), kept in runs at least 0.35 of the share size tall (a rule's few rows
    are not type)."""
    st = getattr(seq, "stats", {}) or {}
    im = seq.kept.get(rest_i)
    w, h = seq.size
    box, size = st.get("box"), float(st.get("share_size") or 0)
    if im is None or not box or not size:
        return None, "no rest frame or layout record"
    mast = (st.get("masthead") or {}).get("box")
    top = int(mast[3]) + 4 if mast else 0
    bottom = int(box[1]) - 2 if st.get("follow") else min(h, int(box[3] + size))
    half = float(st.get("safe_width") or w) / 2
    x0, x1 = max(0, int(w / 2 - half)), min(w, int(w / 2 + half))
    if bottom - top < size * 0.5:
        return None, f"no room between the masthead and the FOLLOW line ({top}-{bottom})"
    a = np.asarray(im.crop((x0, top, x1, bottom)).convert("L"), np.float32)
    ink = a > (np.percentile(a, GROUND_PCT, axis=1, keepdims=True) + INK_OVER_GROUND)
    rows = ink.sum(1) >= max(2, 0.004 * a.shape[1])
    runs, start = [], None
    for y, on in enumerate(list(rows) + [False]):
        if on and start is None:
            start = y
        elif not on and start is not None:
            runs.append((start, y))
            start = None
    merged: list[list[int]] = []
    for s, e in runs:   # a line's letters and its accents / descenders: gaps under a fifth of the size join
        if merged and s - merged[-1][1] <= max(2, size * 0.2):
            merged[-1][1] = e
        else:
            merged.append([s, e])
    lines = [(s, e) for s, e in merged if e - s >= 0.35 * size]
    if not lines:
        return None, "no type rows found between the masthead and the FOLLOW line"
    # the share block is the last group of lines (the one over the FOLLOW line / at the box's foot): a gap of a
    # share size or more parts it from anything above (the masthead and its rule, if the record's box is short)
    block = [lines[-1]]
    for s, e in reversed(lines[:-1]):
        if block[0][0] - e >= size:
            break
        block.insert(0, (s, e))
    y0, y1 = block[0][0], block[-1][1]
    cols = np.where(ink[y0:y1].any(0))[0]
    return ((x0 + int(cols[0]), top + y0, x0 + int(cols[-1]) + 1, top + y1),
            f"measured on the rest frame: {len(block)} type line(s) over the "
            f"{'FOLLOW line' if st.get('follow') else 'share box foot'}")


def rise_ink_px(seq: Seq, box: tuple, rest_i: int, pad: int = 26, reach: int = 24) -> dict[int, int]:
    """The share block's vertical offset (px below rest) on each kept frame: the frame's type pixels (INK_OVER_GROUND
    / 2 over its row's ground, so fading type still counts) moved up 0..reach-1 px, and the shift that lands the
    most of them on the rest frame's type pixels (its precision). A partly revealed block is a subset of the rest
    frame's type, so it matches at its own offset; the ground before the dim (texture, not type) lands on type at
    no shift and is left out (precision under RISE_MATCH_MIN), as is a frame with no type and a best shift on the
    search's edge (cycle 3: the luma-profile match of rise_px reads 0 wherever the ground or the masthead
    dominates its box)."""
    if rest_i not in seq.kept:
        return {}
    x0, y0, x1, y1 = box

    def mask(im):   # the ground per row from the whole frame row (the box is the type's own, mostly type)
        a = np.asarray(im.crop((0, max(0, y0 - pad), im.width, min(im.height, y1 + pad))).convert("L"), np.float32)
        ground = np.percentile(a, GROUND_PCT, axis=1, keepdims=True)
        return a[:, x0:x1] > ground + INK_OVER_GROUND / 2
    ref = mask(seq.kept[rest_i])
    out = {}
    for i, im in seq.kept.items():
        m = mask(im)
        n = int(m.sum())
        if n < 0.02 * ref.sum():
            continue
        score = [float((m[s:] & ref[:len(ref) - s]).sum()) / n if s else float((m & ref).sum()) / n
                 for s in range(reach)]
        best = int(np.argmax(score))
        if best < reach - 1 and score[best] >= RISE_MATCH_MIN:
            out[i] = best
    return out


RISE_MATCH_MIN = 0.6   # the least share of a frame's type pixels that must land on the rest frame's type


def rise_px(seq: Seq, box: tuple, rest_i: int) -> dict[int, int]:
    """The vertical offset (px) of the content in `box` on each kept frame against the rest frame, by the best
    correlation of the box's row-luma profiles over shifts 0-24 px (the rise moves the type up into place)."""
    if rest_i not in seq.kept:
        return {}
    x0, y0, x1, y1 = box
    pad = 26

    def prof(im):
        a = np.asarray(im.crop((x0, max(0, y0 - pad), x1, min(im.height, y1 + pad))).convert("L"), np.float32)
        p = a.mean(1)
        return p - p.mean()
    ref = prof(seq.kept[rest_i])
    out = {}
    for i, im in seq.kept.items():
        p = prof(im)
        if p.std() < 0.15 * ref.std():   # no type in the box yet (only the ground dimming): nothing to measure
            continue
        score = [float(np.dot(p[s:], ref[:len(ref) - s])) if s else float(np.dot(p, ref)) for s in range(0, 25)]
        best = int(np.argmax(score))
        if best < 24:                    # a peak on the search's edge is no match, never a 24 px rise
            out[i] = best
    return out


# ---------------------------------------------------------------------------------------------- the motion tokens
EASE_FUNCS = {"ease", "ease_io", "ease_out", "ease_in_out", "ease_out_back", "ease_out_expo", "ease_in", "_smoother",
              "_smoothstep", "_hook_alpha", "follow_alpha", "mix_at", "kicker_rise"}


def ease_calls(module, functions: tuple[str, ...]) -> list[str]:
    """Every timing call in `functions` of `module` as the source writes it: 'name:line  ease((t - 0.15) / 0.4)'."""
    src = Path(inspect.getsourcefile(module)).read_text(encoding="utf-8")
    tree = ast.parse(src)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in functions:
            for c in ast.walk(node):
                if isinstance(c, ast.Call):
                    fn = c.func.id if isinstance(c.func, ast.Name) else getattr(c.func, "attr", "")
                    if fn in EASE_FUNCS:
                        out.append(f"{node.name}:{c.lineno}  {ast.get_source_segment(src, c)}")
    return sorted(dict.fromkeys(out), key=lambda s: (s.split(":")[0], int(s.split(":")[1].split()[0])))


def ass_motion(ass_text: str) -> dict[str, list]:
    """The caption motion the built ASS carries: every \\t(a,b,...) word ease and \\fad(in,out) plate fade, counted."""
    t = {}
    for a, b in re.findall(r"\\t\((\d+),(\d+),", ass_text):
        t[f"{int(b) - int(a)} ms"] = t.get(f"{int(b) - int(a)} ms", 0) + 1
    fad = {}
    for a, b in re.findall(r"\\fad\((\d+),(\d+)\)", ass_text):
        fad[f"in {a} / out {b} ms"] = fad.get(f"in {a} / out {b} ms", 0) + 1
    return {"word_ease": sorted(t.items()), "plate_fade": sorted(fad.items())}


def motion_rows(ass_texts: dict[str, str]) -> list[tuple]:
    """(name, value, kind, what it times) for every motion value the tree holds: brand_system's motion.* (gate,
    runtime token, written SPEC), its eases (with motion.back's overshoot), the renderers' own timing constants, the
    act dissolve, the caption ASS's eases and fades, and every timing call in the end card, bridge and hook code.
    An evidence table never raises: a part this tree does not have is a "not found" row saying why."""
    rows: list[tuple] = [("name", "value", "kind", "what it times / where")]

    def part(name: str, fn) -> None:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            rows.append((name, "not found", "not on this tree", f"{type(e).__name__}: {e}"[:300]))

    def tokens():
        from src import brand_system as bs
        for kind, table in (("gate", bs.GATES), ("runtime token", getattr(bs, "SYSTEM_TOKENS", {})),
                            ("SPEC (written)", bs.SPEC)):
            for k, v in table.items():
                if k.startswith("motion."):
                    rows.append((k, v, kind, "src/brand_system.py"))

    def eases():
        from src import brand_system as bs
        back = bs.tok("motion.back")
        peak = max(bs.ease_out_back(i / 1000) for i in range(1001))
        for name, fn in bs.EASES.items():
            doc = (fn.__doc__ or "").strip().splitlines()[0] if (fn.__doc__ or "").strip() else "min(1, max(0, t))"
            extra = f"; overshoot {100 * (peak - 1):.1f} % at motion.back {back}" if name == "ease_out_back" else ""
            rows.append((f"ease {name}", f"y(0.25)={fn(0.25):.3f} y(0.5)={fn(0.5):.3f}", "ease", doc + extra))

    def consts():
        from src import design_cards as dc
        from src import end_card as ec
        for mod, names in ((ec, ("FPS", "SECONDS", "END_SCREEN_SECONDS", "LOOP_SECONDS", "LOOP_TIMES", "LOOP_CROSSFADE",
                                 "LOOP_DIM", "LOOP_PUSH", "LOOP_PUSH_CAP", "LANDSCAPE_PUSH", "LOOP_FOLLOW_FRAMES",
                                 "LOOP_FOLLOW_RISE", "BELL_AT", "LOOP_MAX_STEP_L", "LOOP_MAX_MAD")),
                           (dc, ("HOOK_SECONDS", "HOOK_HOLD", "HOOK_FADE", "HOOK_GROUND_HOLD", "HOOK_GROUND_FADE",
                                 "CAPTION_CLEAR", "REVEAL"))):
            short = mod.__name__.split(".")[-1]
            for n in names:
                rows.append((f"{short}.{n}", getattr(mod, n) if hasattr(mod, n) else "not found",
                             "module constant (drives now)" if hasattr(mod, n) else "not on this tree", f"src/{short}.py"))

    def dissolve():
        spec = act_dissolve_spec()
        rows.append(("act dissolve", (f"{spec['frames']} frames = {spec['seconds']:.3f} s, xfade transition="
                                      f"{spec['transition'] or 'not found'}") if spec["seconds"] is not None
                     else "not found", "composer (drives now)", spec["source"]))

    def captions():
        for case, text in ass_texts.items():
            m = ass_motion(text)
            rows.append((f"captions {case}", "; ".join(f"{k} x{v}" for k, v in m["word_ease"]) or "none",
                         "built ASS (drives now)", "word ease \\t(a,b): the word's alpha in"))
            rows.append((f"captions {case}", "; ".join(f"{k} x{v}" for k, v in m["plate_fade"]) or "none",
                         "built ASS (drives now)", "plate fade \\fad(in,out)"))

    def calls():
        from src import design_cards as dc
        from src import end_card as ec
        for mod, fns in ((ec, ("frames", "loop_bridge", "follow_alpha", "kicker_rise", "_gate_lines")),
                         (dc, ("_hook_alpha", "frame_at"))):
            for line in ease_calls(mod, fns):
                rows.append((f"{mod.__name__.split('.')[-1]}.{line.split(':')[0]}", line.split("  ", 1)[1],
                             "timing call (source)", f"line {line.split(':')[1].split()[0]}"))
    for name, fn in (("motion tokens", tokens), ("eases", eases), ("timing constants", consts),
                     ("act dissolve", dissolve), ("caption ASS motion", captions), ("timing calls", calls)):
        part(name, fn)
    return rows
