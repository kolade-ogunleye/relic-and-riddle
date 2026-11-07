"""Gold on a Story frame (2026-11-01): the title's second line is the one gold read; the pointer is bone."""

from PIL import Image

from src import brand
from src.multi_format import MultiFormatGenerator


def test_the_reel_pointer_is_bone_and_the_title_keeps_its_gold(tmp_path) -> None:
    photo = tmp_path / "p.jpg"
    Image.new("RGB", (1080, 1920), (120, 110, 95)).save(photo)
    slide = {"kicker": "Orbital mystery", "line1": "The desert eye", "line2": "seen from space",
             "body": "A colossal concentric formation marks the vast Mauritanian desert."}
    _, lay = MultiFormatGenerator()._render_story_frame(photo, slide, 0, 3, {})
    fills = {e["name"]: e.get("fill") for e in lay.to_dict()["elements"]}
    assert fills["reel_pointer"] == list(brand.BONE)
    assert fills["title_2"] == list(brand.GOLD)
