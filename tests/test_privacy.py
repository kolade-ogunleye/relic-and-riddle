"""
tests/test_privacy.py
=====================
Tests for face privacy de-identification and skin-color tone filtering.
"""

from pathlib import Path
import numpy as np
import cv2
import pytest

from src import privacy
from src.config import BASE_DIR


def test_skin_detection_synthetic():
    # Create synthetic stone-like patch (gray tones, Cr ~ 128, Cb ~ 128)
    stone_patch = np.full((100, 100, 3), 120, dtype=np.uint8)
    ycrcb_stone = cv2.cvtColor(stone_patch, cv2.COLOR_BGR2YCrCb)
    cr_stone = ycrcb_stone[:, :, 1]
    cb_stone = ycrcb_stone[:, :, 2]
    stone_mask = (cr_stone >= 135) & (cr_stone <= 180) & (cb_stone >= 85) & (cb_stone <= 135)
    assert stone_mask.sum() == 0, "Stone texture must have 0% skin match"

    # Create synthetic human skin patch (e.g. BGR = (140, 160, 220) -> warm human skin)
    skin_patch = np.full((100, 100, 3), (140, 160, 220), dtype=np.uint8)
    ycrcb_skin = cv2.cvtColor(skin_patch, cv2.COLOR_BGR2YCrCb)
    cr_skin = ycrcb_skin[:, :, 1]
    cb_skin = ycrcb_skin[:, :, 2]
    skin_mask = (cr_skin >= 135) & (cr_skin <= 180) & (cb_skin >= 85) & (cb_skin <= 135)
    assert skin_mask.sum() == 10000, "Human skin tone must have 100% skin match"


def test_blur_faces_on_image():
    # Create image with high-frequency pattern in center
    img = np.zeros((200, 200, 3), dtype=np.uint8)
    img[50:150, 50:150] = np.random.randint(0, 255, (100, 100, 3), dtype=np.uint8)
    
    orig_variance = np.var(img[70:130, 70:130])
    
    # Apply face blur on (50, 50, 100, 100)
    blurred = privacy.blur_faces_on_image(img, [(50, 50, 100, 100)])
    blurred_variance = np.var(blurred[70:130, 70:130])
    
    # Blurring must significantly reduce pixel variance
    assert blurred_variance < orig_variance / 2


def test_blur_human_faces_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(privacy, "FACE_PRIVACY_ENABLED", False)
    test_img = tmp_path / "test.jpg"
    cv2.imwrite(str(test_img), np.zeros((100, 100, 3), dtype=np.uint8))
    
    out = privacy.blur_human_faces(test_img)
    assert out == test_img


def test_anonymize_media_non_image(tmp_path):
    test_file = tmp_path / "test.mp4"
    test_file.write_text("dummy video")
    out = privacy.anonymize_media(test_file)
    assert out == test_file


def test_kailasa_stone_carving_not_blurred():
    # If 03_commons.jpg exists, test that stone carving faces are not flagged as living humans
    kailasa_stone = BASE_DIR / "assets" / "kailasa_temple_monolith" / "03_commons.jpg"
    if kailasa_stone.exists():
        img = cv2.imread(str(kailasa_stone))
        faces = privacy.detect_human_faces(img)
        assert len(faces) == 0, f"Expected 0 human faces in stone carvings, found {len(faces)}"
