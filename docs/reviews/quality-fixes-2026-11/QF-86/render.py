"""QF-86 evidence renders: delivered master frames of the real run inputs, for one tree, half size.

    python docs/reviews/quality-fixes-2026-11/QF-86/render.py <repo root> <out dir> <before|after>

The same inputs and composer calls as measure.py (inputs/, real run frames). Writes, each a half-size JPEG under
300 KB, <out dir>/<tag>_<panel>.jpg:
- stat_card_richat: the Richat run's real stat card drawn by this tree at the run's bias (+0.05) as the master
  delivers it (left), and the same photo delivered bare (right), frame 0, lens on as in a Reel;
- cut_aswan: one frame split down the middle at Aswan's bias (+0.036), lens off: left half the photo delivered
  bare, right half the same photo as the photo layer of a graphic (this tree's renderers' layer);
- then_and_now, hook_frame: as drawn (left) and as delivered (right);
- photo_parallax: the Gol Gumbaz photo as drawn and delivered as a parallax clip;
- end_screen_aswan: the 16:9 end screen's last frame over the delivered Aswan ground (documentary bias 0).
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
LIMIT = 300 * 1024


def pair(left: Image.Image, right: Image.Image, labels: tuple[str, str]) -> Image.Image:
    w, h = left.width // 2, left.height // 2
    sheet = Image.new("RGB", (2 * w + 10, h + 24), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    for k, (im, label) in enumerate(((left, labels[0]), (right, labels[1]))):
        sheet.paste(im.resize((w, h), Image.LANCZOS), (k * (w + 10), 24))
        d.text((k * (w + 10) + 4, 5), label, fill=(0, 0, 0))
    return sheet


def save(im: Image.Image, path: Path) -> None:
    for q in (85, 80, 75, 70, 65):
        im.save(path, quality=q, optimize=True)
        if path.stat().st_size <= LIMIT:
            return
    raise RuntimeError(f"{path.name} over 300 KB")


def main(root: Path, out_dir: Path, tag: str) -> None:
    sys.path.insert(0, str(root))
    # the encoder measure.py and floor.py pin (libx264, encoding.get_video_encoder's own fallback; review r3)
    from src import encoding
    encoding._DETECTED_ENCODER = ["-c:v", "libx264", "-preset", encoding.VIDEO_PRESET, "-crf", str(encoding.VIDEO_CRF)]
    spec = importlib.util.spec_from_file_location("qf86_measure", HERE / "measure.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    import json
    runs = json.loads((m.INPUTS / "inputs.json").read_text(encoding="utf-8"))["runs"]
    has_layer_grade = importlib.util.find_spec("src.photo_grade") is not None
    size = (1080, 1920)
    panels: dict[str, Image.Image] = {}
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="qf86-render-") as directory:
        work = Path(directory)
        rig = m.Rig(root, work)
        from src.lineage import record_derived
        from src.typography import cover_crop

        def parallax_of(src: Path) -> Path:
            clip = rig.clip(cover_crop(src, size).convert("RGB"), "scene_01.mp4")
            record_derived(clip, [src], "parallax", {"preset": "hook", "duration": 1.0, "fps": 30, "variant": 0})
            return clip

        # the real Richat stat card at its bias, and the same photo bare
        rc = runs["richat"]
        photo_r = work / "stat" / "photo_richat_01.jpg"
        photo_r.parent.mkdir()
        shutil.copy(m.INPUTS / "photo_richat_01.jpg", photo_r)
        card = work / "stat" / "graphic_numbers.mp4"
        draw = lambda: rig.graphics.render_stat_cards(rc["stat_cards"], card, photo_r, width=1080, height=1920,
                                                      narration=rc["narration_script"])
        if has_layer_grade:
            from src import photo_grade
            with photo_grade.using(rc["grade_bias"]):
                draw()
        else:
            draw()
        panels["stat_card_richat"] = pair(Image.fromarray(rig.master(card, "reel", rc["grade_bias"], t=0.0)),
                                          Image.fromarray(rig.master(parallax_of(photo_r), "reel", rc["grade_bias"],
                                                                     t=0.0)),
                                          (f"{tag}: stat card, bias {rc['grade_bias']:+.3f}", "same photo, bare"))
        # the cut at Aswan's bias, lens off
        bias = runs["aswan"]["grade_bias"]
        src = work / "cut" / "end_ground_aswan.jpg"
        src.parent.mkdir()
        shutil.copy(m.INPUTS / "end_ground_aswan.jpg", src)
        if has_layer_grade:
            from src import photo_grade
            layer = photo_grade.cover(src, size, bias)
        else:
            layer = cover_crop(src, size).convert("RGB")
        lens = rig.vc.picture_vignette
        rig.vc.picture_vignette = lambda width, height: "null"
        try:
            a = rig.master(parallax_of(src), "reel", bias)
            b = rig.master(rig.clip(layer, "graphic_photo_layer.mp4"), "reel", bias)
        finally:
            rig.vc.picture_vignette = lens
        split = Image.fromarray(a.copy())
        split.paste(Image.fromarray(b[:, 540:]), (540, 0))
        ImageDraw.Draw(split).line([(540, 0), (540, 1919)], fill=(255, 255, 255), width=1)
        panels["cut_aswan"] = pair(Image.fromarray(a), split, (f"{tag}: bare photo, bias {bias:+.3f}",
                                                               "left bare | right inside a graphic"))
        for name, inp, clip_name in (("then_and_now", "graphic_thennow_roman.jpg", "graphic_thennow.mp4"),
                                     ("hook_frame", "hook_frame_nazca_bright.jpg", "hook_frame.mp4")):
            drawn = m.fitted(m.INPUTS / inp, size)
            panels[name] = pair(drawn, Image.fromarray(rig.master(rig.clip(drawn, clip_name), "reel")),
                                (f"{tag}: {name} drawn", "delivered"))
        photo = work / "ph" / "photo_gol_gumbaz_04.jpg"
        photo.parent.mkdir()
        shutil.copy(m.INPUTS / "photo_gol_gumbaz_04.jpg", photo)
        panels["photo_parallax"] = pair(cover_crop(photo, size).convert("RGB"),
                                        Image.fromarray(rig.master(parallax_of(photo), "reel")),
                                        (f"{tag}: photo drawn", "delivered (parallax clip)"))
        from src import end_card
        still = work / "end" / "end_ground_aswan.jpg"
        still.parent.mkdir()
        shutil.copy(m.INPUTS / "end_ground_aswan.jpg", still)
        ground = Image.fromarray(rig.master(still, "deep_dive", t=0.95))
        last = m.end_screen_stats(end_card, ground, end_card.card_text(runs["aswan"]["share_line"]))["_last"]
        panels["end_screen_aswan"] = pair(ground, last, (f"{tag}: 16:9 delivered ground", "end screen, last frame"))
    for name, im in panels.items():
        save(im, out_dir / f"{tag}_{name}.jpg")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("usage: python render.py <repository root> <out dir> <before|after>")
    main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]), sys.argv[3])
