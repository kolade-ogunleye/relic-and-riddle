import pytest
from pathlib import Path
from PIL import Image, ImageDraw
import numpy as np

from src.substrate_compositor import (
    generate_procedural_substrate,
    extract_ink_mask,
    composite_linework_onto_substrate,
    extract_parallax_layers,
    ERA_INK_COLORS,
    ERA_BASE_TONES
)


def test_procedural_substrate_generation():
    for era in ["classical_antiquity", "medieval_renaissance", "industrial_victorian", "modern_cold_war"]:
        sub = generate_procedural_substrate(era, size=(270, 480))
        assert sub.size == (270, 480)
        assert sub.mode == "RGB"
        # Check that average color is close to base tone
        arr = np.array(sub)
        expected_base = ERA_BASE_TONES[era]
        mean_rgb = arr.mean(axis=(0, 1))
        # Vignette darkens slightly, so mean will be within 30 units of base
        for c, exp in zip(mean_rgb, expected_base):
            assert abs(c - exp) < 35


def test_extract_ink_mask():
    # Create white canvas with a black line
    img = Image.new("RGB", (100, 100), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.line([(50, 0), (50, 100)], fill=(0, 0, 0), width=4)
    
    mask = extract_ink_mask(img)
    assert mask.size == (100, 100)
    assert mask.mode == "L"
    arr = np.array(mask)
    # White background should have 0 alpha
    assert arr[50, 10] == 0
    # Black line should have high alpha (>200)
    assert arr[50, 50] > 200


def test_composite_linework_onto_substrate(tmp_path):
    img = Image.new("RGB", (270, 480), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([50, 50, 220, 220], outline=(0, 0, 0), width=3)
    
    out_file = tmp_path / "test_comp.jpg"
    res = composite_linework_onto_substrate(img, era="classical_antiquity", output_path=out_file, target_size=(270, 480))
    assert res.size == (270, 480)
    assert out_file.exists()
    
    # Verify tinted ink color on the drawn line
    arr = np.array(res)
    # Background should be parchment tone
    assert arr[10, 10, 0] > 200
    # Drawn line should be dark sepia ink
    assert arr[50, 50, 0] < 80


def test_extract_parallax_layers():
    img = Image.new("RGB", (270, 480), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.line([(0, 0), (270, 480)], fill=(0, 0, 0), width=5)
    
    ink_rgba, substrate_rgb = extract_parallax_layers(img, era="medieval_renaissance", target_size=(270, 480))
    assert ink_rgba.mode == "RGBA"
    assert substrate_rgb.mode == "RGB"
    assert ink_rgba.size == (270, 480)
    assert substrate_rgb.size == (270, 480)
