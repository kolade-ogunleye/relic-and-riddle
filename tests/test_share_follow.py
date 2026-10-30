"""Meta's creator tips (2026-10-25): ask viewers to share, invite them to follow. One line, the writer's when it
passes its checks or a plain one, closes both captions (before the
hashtags) and the carousel's last slide."""
from types import SimpleNamespace

from PIL import Image, ImageDraw

from src import script_engine
from src.script_engine import SHARE_FALLBACK, is_stock_share, share_line_problems, share_prompt, with_share_line

LINE = "Send this to a friend who loves ancient engineering, and follow for the next relic."


def test_a_share_line_must_ask_for_a_share_and_a_follow_and_stay_short():
    assert share_line_problems(LINE) == []
    assert share_line_problems("Follow for more.")                                  # asks for no share
    assert share_line_problems("Share this with a friend.")                        # asks for no follow
    assert share_line_problems("Share this #history and follow")                   # a hashtag
    assert share_line_problems("x" * 91 + " share, follow")                        # too long
    assert share_prompt({"share_line": "Follow for more."}) == SHARE_FALLBACK
    assert share_prompt({}) == SHARE_FALLBACK and is_stock_share(SHARE_FALLBACK)   # last resort, noted on the card
    assert share_prompt({"share_line": " Pass this on to a map lover,  and follow for the next one. "}) == \
        "Pass this on to a map lover, and follow for the next one."


def test_the_line_goes_before_the_hashtags_once():
    caption = "A hook line.\nMore context here.\n\nWhat would you do?\n\n#history #malta"
    out = with_share_line(caption, "Share it and follow.")
    assert out == "A hook line.\nMore context here.\n\nWhat would you do?\n\nShare it and follow.\n\n#history #malta"
    assert with_share_line(out, "Share it and follow.") == out                     # never twice
    assert with_share_line("No tags here.", "Share it and follow.") == "No tags here.\n\nShare it and follow."


def test_validate_structure_rejects_a_bad_share_line_and_accepts_none(curated_payload):
    from src.models import ScriptPackage
    pkg = ScriptPackage.model_validate(dict(curated_payload, share_line="Follow us #now"))
    problems = " ".join(script_engine.validate_structure(pkg))
    assert "share_line:" in problems and "hashtags" in problems
    pkg = ScriptPackage.model_validate(curated_payload)
    assert pkg.share_line is None and not any("share_line" in p for p in script_engine.validate_structure(pkg))


def test_the_script_stage_puts_the_line_in_both_captions(db, tmp_path, curated_payload):
    import main

    class FakeEngine:
        progress = None

        def generate_script(self, topic, revision_notes="", previous=None):
            return dict(curated_payload, origin="gemini-free", caption="Hook.\n\nQuestion?\n\n#one #two",
                        carousel_caption="Swipe.\n\n#one #two", share_line=LINE)

        def verify_claims(self, payload, topic=None, allow_unreviewed_curated=False):
            return True, payload, "PASSED"

        def _load_curated(self, topic_id):
            return None
    db.create_run("run_s", db.list_all_topics()[0]["id"])
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.db = db
    c.script_engine = FakeEngine()
    c._check_cancel = lambda run_id: None
    c.telegram_bot = SimpleNamespace(update_progress_card=lambda *a, **k: None)
    payload = c._write_and_check_script({"id": "t", "title": "T"}, "run_s", tmp_path, dry_run=True)
    assert payload["caption"] == f"Hook.\n\nQuestion?\n\n{LINE}\n\n#one #two"
    assert payload["carousel_caption"] == f"Swipe.\n\n{LINE}\n\n#one #two"


def test_the_closing_carousel_slide_carries_the_line(tmp_path):
    from src.multi_format import MultiFormatGenerator
    from src.typography import get_font
    photo = tmp_path / "p.jpg"
    Image.new("RGB", (1200, 900), (40, 60, 80)).save(photo)
    drawn = []
    real = ImageDraw.ImageDraw.text

    def spy(self, xy, text, *a, **k):
        drawn.append((xy[1], str(text)))
        return real(self, xy, text, *a, **k)
    ImageDraw.ImageDraw.text = spy
    try:
        MultiFormatGenerator()._render_debate(photo, {"heading": "Why?", "body": "One body line."}, 6, 7, 1080, 1350,
                                             {"question": "Q?", "options": ["A", "B"]}, get_font("Montserrat", 28), share=LINE)
    finally:
        ImageDraw.ImageDraw.text = real
    # Each line's first draw is at its canvas position (its glyph mask; the later draws are into local crops).
    firsts = {}
    for y, t in drawn:
        firsts.setdefault(t, y)
    share_rows = [y for t, y in firsts.items() if t.split() and t.split()[0] in LINE.split()[:3] and t in LINE]
    body_y = next(y for y, t in drawn if t == "One body line.")
    # No "TELL US IN THE COMMENTS" footer since 2026-10-29 (operator decision: one ask, the share line), so the
    # share line is the last text on the slide, below the body.
    assert share_rows and all(body_y < y for y in share_rows)
    assert not any(t.startswith("TELL US") for _, t in drawn)


