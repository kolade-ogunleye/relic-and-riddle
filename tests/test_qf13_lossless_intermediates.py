"""Prepared render images must retain the computed pixels exactly."""

import json

import numpy as np
import pytest
from PIL import Image

from src import beat_images, colour_balance, commons, privacy, upscale


def pattern(path, size=(96, 96)):
    rng = np.random.default_rng(13)
    rgb = rng.integers(20, 220, (*size[::-1], 3), dtype=np.uint8)
    Image.fromarray(rgb).save(path)
    return path


def pixels(path):
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))


def test_downloaded_png_preserves_every_decoded_source_pixel(tmp_path, monkeypatch):
    original = pattern(tmp_path / "original.png")
    data = original.read_bytes()
    monkeypatch.setattr(commons, "_get", lambda *a, **k: type("Response", (), {"content": data})())
    target = tmp_path / "pool.png"
    commons.download_jpeg("https://upload.wikimedia.org/example.png", target)
    assert Image.open(target).format == "PNG"
    assert np.array_equal(pixels(target), pixels(original))


def test_colour_balance_serializes_computed_pixels_without_a_jpeg_generation(tmp_path, monkeypatch):
    source = pattern(tmp_path / "source.jpg")
    monkeypatch.setattr(colour_balance, "CACHE_DIR", tmp_path / "balance")
    plan = {"gains": [1.05, 1.0, 0.95], "gamma": 1, "cast": 0.1}
    monkeypatch.setattr(colour_balance, "plan", lambda image: plan)
    with Image.open(source) as image:
        expected = np.asarray(colour_balance.apply(image, plan))
    result = colour_balance.balanced(source)
    assert result.suffix == ".png" and Image.open(result).format == "PNG"
    assert np.array_equal(pixels(result), expected)


def test_band_crop_preserves_retained_pixels_exactly(tmp_path, monkeypatch):
    source = pattern(tmp_path / "source.jpg")
    monkeypatch.setattr(beat_images, "TEXT_CROP_DIR", tmp_path / "crop")
    result = beat_images.crop_text_band(source, "bottom", [900, 0, 1000, 1000])
    assert result is not None and result.suffix == ".png"
    crop = pixels(result)
    assert np.array_equal(crop, pixels(source)[:crop.shape[0]])


def test_upscale_output_is_lossless_and_is_not_deleted_as_backend_temporary(tmp_path, monkeypatch):
    source = pattern(tmp_path / "source.jpg")
    monkeypatch.setattr(upscale, "CACHE_DIR", tmp_path / "upscale")
    expected = pixels(source)
    monkeypatch.setattr(upscale, "_upscale_cpu_polish", lambda path: Image.fromarray(expected))
    output = upscale._cache_path(source, "cpu_lanczos_polish")
    result = upscale._try_backend("cpu_lanczos_polish", source, output)
    assert result == output and output.is_file() and output.suffix == ".png"
    assert np.array_equal(pixels(output), expected)


def test_privacy_keeps_nonface_pixels_exactly(tmp_path, monkeypatch):
    source = pattern(tmp_path / "source.jpg")
    monkeypatch.setattr(privacy, "FACE_PRIVACY_ENABLED", True)
    monkeypatch.setattr(privacy, "CACHE_DIR", tmp_path / "privacy")
    monkeypatch.setattr(privacy, "is_artwork", lambda path: False)
    monkeypatch.setattr(privacy, "looks_like_art", lambda path: False)

    def detect(image, **kwargs):
        kwargs["status"]["complete"] = True
        return [(30, 30, 20, 20)]

    monkeypatch.setattr(privacy, "detect_human_faces", detect)
    expected = privacy.blur_faces_on_image(privacy.cv2.imread(str(source)), [(30, 30, 20, 20)])
    output = privacy.anonymize_media(source)
    assert output.suffix == ".png"
    assert np.array_equal(privacy.cv2.imread(str(output)), expected)


def test_motion_anchor_is_png_even_when_legacy_caller_requests_jpeg(tmp_path):
    from src.motion import pre_condition_anchor_frame
    source = pattern(tmp_path / "source.png", (192, 108))
    output = pre_condition_anchor_frame(source, tmp_path / "anchor.jpg")
    assert output.suffix == ".png" and Image.open(output).format == "PNG"


def test_illustration_composite_retains_computed_pixels(tmp_path):
    from src.substrate_compositor import composite_engraving_on_ink
    source = pattern(tmp_path / "raw.png")
    output = tmp_path / "scene.png"
    expected = composite_engraving_on_ink(source, output, (96, 96))
    assert Image.open(output).format == "PNG"
    assert np.array_equal(pixels(output), np.asarray(expected))


def test_located_detail_crop_retains_pixels(tmp_path):
    from src.picture_coverage import _save_crop
    source = pattern(tmp_path / "photo.jpg")
    with Image.open(source) as image:
        output = _save_crop(source, image, (0.25, 0.25, 0.75, 0.75), tmp_path / "run", {})
    assert output.suffix == ".png" and Image.open(output).format == "PNG"
    assert np.array_equal(pixels(output), pixels(source)[24:72, 24:72])
    credit = {"file": str(source), "artist": "Fixture"}
    assert beat_images.used_credits([output], [credit]) == [credit]
    from src.prevoice_lines import pool_descriptions
    (tmp_path / "credits.json").write_text(json.dumps({"images": [
        {"file": source.name, "title": "Fixture detail", "description": "A stone door"}]}))
    descriptions = pool_descriptions(tmp_path, [output])
    assert len(descriptions) == 1 and descriptions[0]["file"] == source.name


def test_operator_upload_preserves_decoded_pixels_and_review_hold(db, tmp_path, monkeypatch):
    from src import telegram_images
    original = pattern(tmp_path / "original.png")
    monkeypatch.setattr(telegram_images, "write_contact_sheet", lambda *a: None)
    rights = {"author": "Fixture", "licence": "CC BY 4.0", "source": "https://example.org/photo",
              "licence_url": "https://creativecommons.org/licenses/by/4.0/"}
    topic = telegram_images.add_image(db, tmp_path / "assets", "1", rights, original.read_bytes(), "fixture")
    directory = tmp_path / "assets" / topic
    credits = json.loads((directory / "credits.json").read_text())
    output = directory / credits["images"][0]["file"]
    assert not credits["reviewed"] and output.suffix == ".png"
    assert np.array_equal(pixels(output), pixels(original))


def test_refetched_orientation_updates_credit_identity_and_preserves_original(tmp_path, monkeypatch):
    from src import image_presentation, photo_orientation
    source = pattern(tmp_path / "photo.jpg", (96, 72))
    original = source.read_bytes()
    image_presentation.designate(source, "Essential object")
    credits = {"reviewed": True, "images": [{"file": source.name, "width": 72, "height": 96,
                "download_url": "https://upload.wikimedia.org/example.jpg", "operator_keep": True}]}
    (tmp_path / "credits.json").write_text(json.dumps(credits))

    def download(url, target, **kwargs):
        Image.new("RGB", (72, 96), "gray").save(target, "PNG")

    monkeypatch.setattr(commons, "download_jpeg", download)
    _, still = photo_orientation.put_upright(tmp_path)
    assert not still
    target = tmp_path / "photo.png"
    assert target.is_file() and not source.exists()
    assert (tmp_path / photo_orientation.BACKUP_DIR / source.name).read_bytes() == original
    saved = json.loads((tmp_path / "credits.json").read_text())
    assert saved["images"][0]["file"] == target.name and saved["images"][0]["operator_keep"]
    assert not saved["reviewed"] and image_presentation.full_image(target)


def test_orientation_credit_failure_restores_original_and_essential_policy(tmp_path, monkeypatch):
    from pathlib import Path

    from src import image_presentation, photo_orientation
    source = pattern(tmp_path / "photo.jpg", (96, 72))
    original = source.read_bytes()
    image_presentation.designate(source, "Essential object")
    credit = tmp_path / "credits.json"
    credit.write_text(json.dumps({"images": [{"file": source.name, "width": 72, "height": 96,
                       "download_url": "https://upload.wikimedia.org/example.jpg"}]}))
    original_credit = credit.read_bytes()
    monkeypatch.setattr(commons, "download_jpeg", lambda url, target, **kwargs: Image.new("RGB", (72, 96)).save(target))
    replace = Path.replace

    def fail_credit(path, target):
        if path.name == "credits.upright.tmp":
            raise OSError("credit install failed")
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_credit)
    _, still = photo_orientation.put_upright(tmp_path)
    assert still == {source.name}
    assert source.read_bytes() == original and credit.read_bytes() == original_credit
    assert image_presentation.full_image(source)
    assert not (tmp_path / "photo.png").exists()


def test_then_now_registration_keeps_both_layers_lossless(tmp_path):
    from src import then_now
    old = pattern(tmp_path / "then_0.png", (240, 320))
    new = pattern(tmp_path / "now_0_0.png", (240, 320))
    pair = then_now.register(old, new, np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]), tmp_path / "registered")
    assert pair is not None
    x0, y0, x1, y1 = json.loads((tmp_path / "registered" / then_now.REGISTRATION_SIDECAR).read_text(
        encoding="utf-8"))["shared"]
    for path in pair:
        with Image.open(path) as image:
            assert image.format == "PNG"
    assert np.array_equal(pixels(pair[0]), pixels(old)[y0:y1, x0:x1])
    expected, _ = then_now.match_exposure(pixels(old)[y0:y1, x0:x1], pixels(new)[y0:y1, x0:x1])
    assert np.array_equal(pixels(pair[1]), expected)


def _jpeg_with_exif(path, orientation=None, mode="RGB"):
    rgb = np.random.default_rng(7).integers(20, 220, (64, 96, 3), dtype=np.uint8)
    exif = Image.Exif()
    exif[0x013B] = "Fixture Artist"                        # metadata the pool never keeps
    if orientation:
        exif[0x0112] = orientation
    Image.fromarray(rgb).convert(mode).save(path, "JPEG", quality=90, exif=exif.tobytes(), comment=b"fixture")
    return path


def _download(tmp_path, monkeypatch, source):
    data = source.read_bytes()
    monkeypatch.setattr(commons, "_get", lambda *a, **k: type("Response", (), {"content": data})())
    requested = tmp_path / source.stem / "01_commons.png"
    requested.parent.mkdir()
    commons.download_jpeg("https://upload.wikimedia.org/example.jpg", requested)
    return commons.stored(requested)


def test_upright_jpeg_download_keeps_its_coded_bytes_without_metadata(tmp_path, monkeypatch):
    source = _jpeg_with_exif(tmp_path / "served.jpg")
    kept = _download(tmp_path, monkeypatch, source)
    data, original = kept.read_bytes(), source.read_bytes()
    assert kept.name == "01_commons.jpg" and not kept.with_suffix(".png").exists()
    assert b"Exif" not in data and b"Fixture Artist" not in data and b"fixture" not in data
    assert data[data.index(b"\xff\xda"):] == original[original.index(b"\xff\xda"):]   # the scan, byte for byte
    assert np.array_equal(pixels(kept), pixels(source))     # no decode, no new JPEG generation
    assert json.loads(kept.with_name(kept.name + ".quality.json").read_text())["pool_sha256"]


def test_rotated_or_cmyk_jpeg_download_is_decoded_once_to_png(tmp_path, monkeypatch):
    from PIL import ImageOps
    rotated = _jpeg_with_exif(tmp_path / "rotated.jpg", orientation=6)
    out = _download(tmp_path, monkeypatch, rotated)
    assert out.suffix == ".png" and Image.open(out).format == "PNG"
    with Image.open(rotated) as im:
        assert np.array_equal(pixels(out), np.asarray(ImageOps.exif_transpose(im).convert("RGB")))
    cmyk = _jpeg_with_exif(tmp_path / "cmyk.jpg", mode="CMYK")
    out = _download(tmp_path, monkeypatch, cmyk)
    assert out.suffix == ".png"
    with Image.open(cmyk) as im:
        assert np.array_equal(pixels(out), np.asarray(im.convert("RGB")))


def test_approved_storyboard_still_finds_a_cropped_photo_saved_as_png(tmp_path, monkeypatch):
    from src import scene_planner
    photo = pattern(tmp_path / "03_commons.jpg", (400, 300))
    other = pattern(tmp_path / "04_commons.jpg", (400, 300))
    monkeypatch.setattr(beat_images, "TEXT_CROP_DIR", tmp_path / "crop")
    crop = beat_images.crop_text_band(photo, "bottom", [900, 0, 1000, 1000])
    assert crop is not None and crop.name == "03_commons.png"
    storyboard = [{"file": "04_commons.jpg", "text": "The rings rise out of the desert"},
                  {"file": "03_commons.jpg", "text": "Seen from orbit the eye stares back"}]
    beats = [{"text": "The rings rise out of the desert"}, {"text": "Seen from orbit the eye stares back"}]
    assert scene_planner.storyboard_order(storyboard, beats, [other, crop]) == [0, 1]
    assert "03_commons.jpg" in scene_planner.photo_names(crop)


TRAILER = b"MotionPhoto_Data ftypmp42 GPS +51.5000-000.1200 SEFT"   # what phones append after the end of image


def _with_trailer(path, progressive=False):
    rgb = np.random.default_rng(11).integers(20, 220, (64, 96, 3), dtype=np.uint8)
    exif = Image.Exif()
    exif[0x013B] = "Fixture Artist"
    Image.fromarray(rgb).save(path, "JPEG", quality=90, exif=exif.tobytes(), progressive=progressive)
    path.write_bytes(path.read_bytes() + TRAILER)
    return path


@pytest.mark.parametrize("progressive", [False, True])
def test_kept_download_ends_at_the_end_of_image_marker(tmp_path, monkeypatch, progressive):
    source = _with_trailer(tmp_path / "served.jpg", progressive)
    kept = _download(tmp_path, monkeypatch, source)
    data = kept.read_bytes()
    assert kept.suffix == ".jpg" and data.endswith(bytes([0xFF, 0xD9]))
    assert b"GPS" not in data and b"MotionPhoto" not in data and b"Fixture Artist" not in data
    assert np.array_equal(pixels(kept), pixels(source))


def test_operator_upload_drops_metadata_and_appended_trailer(db, tmp_path, monkeypatch):
    from src import telegram_images
    source = _with_trailer(tmp_path / "phone.jpg")
    monkeypatch.setattr(telegram_images, "write_contact_sheet", lambda *a: None)
    rights = {"author": "Fixture", "licence": "CC BY 4.0", "source": "https://example.org/photo",
              "licence_url": "https://creativecommons.org/licenses/by/4.0/"}
    topic = telegram_images.add_image(db, tmp_path / "assets", "1", rights, source.read_bytes(), "fixture")
    credits = json.loads((tmp_path / "assets" / topic / "credits.json").read_text())
    output = tmp_path / "assets" / topic / credits["images"][0]["file"]
    data = output.read_bytes()
    assert output.suffix == ".jpg" and data.endswith(bytes([0xFF, 0xD9]))
    assert b"GPS" not in data and b"MotionPhoto" not in data and b"Fixture Artist" not in data
    assert np.array_equal(pixels(output), pixels(source))
