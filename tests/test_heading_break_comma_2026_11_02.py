"""A two-line display heading drops the comma its line break ends on (it collided with the next line's caps)."""
from PIL import Image, ImageDraw

from src.multi_format import MultiFormatGenerator, drop_break_comma, split_balanced


def test_split_drops_the_comma_at_the_break() -> None:
    assert split_balanced("CARVED, NOT BUILT") == ("CARVED", "NOT BUILT")


def test_only_a_trailing_comma_is_dropped() -> None:
    assert drop_break_comma("WHY?") == "WHY?"
    assert drop_break_comma("1,200 TONS") == "1,200 TONS"
    assert drop_break_comma("CARVED,") == "CARVED"


def test_a_two_line_content_heading_has_no_comma_at_its_break() -> None:
    g = MultiFormatGenerator()
    d = ImageDraw.Draw(Image.new("RGB", (1080, 1350)))
    px = g._px(1080)
    rows, _ = g._rows(d, px, "CARVED, NOT BUILT", "", 520, two_tone="wrap",
                      facebook_heading=True)
    heads = [r["text"] for r in rows if r["name"] == "heading" and r.get("text")]
    assert heads == ["CARVED", "NOT BUILT"]
