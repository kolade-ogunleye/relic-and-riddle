"""Meta's creator tips (2026-10-25): ask viewers to share, invite them to follow. One line, the writer's when it
passes its checks or a plain one, closes the video (its last three seconds), both captions (before the
hashtags) and the carousel's last slide."""
from types import SimpleNamespace

from PIL import Image, ImageDraw

from src import script_engine
from src.script_engine import SHARE_FALLBACK, share_line_problems, share_prompt, with_share_line

LINE = "Send this to a friend who loves ancient engineering, and follow for the next relic."


def test_a_share_line_must_ask_for_a_share_and_a_follow_and_stay_short():
    assert share_line_problems(LINE) == []
    assert share_line_problems("Follow for more.")                                  # asks for no share
    assert share_line_problems("Share this with a friend.")                        # asks for no follow
    assert share_line_problems("Share this #history and follow")                   # a hashtag
    assert share_line_problems("x" * 91 + " share, follow")                        # too long
    assert share_prompt({"share_line": "Follow for more."}) == SHARE_FALLBACK
    assert share_prompt({}) == SHARE_FALLBACK and share_line_problems(SHARE_FALLBACK) == []
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
    share_rows = [y for y, t in drawn if t.split()[0] in LINE.split()[:3] and t in LINE]
    body_y = next(y for y, t in drawn if t == "One body line.")
    footer_y = next(y for y, t in drawn if t.startswith("TELL US"))
    assert share_rows and all(body_y < y < footer_y for y in share_rows)         # between the body and the footer


def test_the_video_ends_on_the_line_inside_the_safe_area(tmp_path):
    from src import safe_zones
    from src.video_composer import END_PROMPT_SECONDS, VideoComposer
    from src.video_qa import caption_pills
    words = [{"word": "The", "start": 0.0, "dur": 0.3}, {"word": "end.", "start": 0.4, "dur": 0.3},
             {"word": "Truly", "start": 44.0, "dur": 0.3}, {"word": "done.", "start": 44.4, "dur": 0.3}]
    ass = tmp_path / "c.ass"
    VideoComposer().build_subtitles(words, ass, end_prompt=LINE, end_window=(47.0 - END_PROMPT_SECONDS, 47.0))
    lines = ass.read_text(encoding="utf-8").splitlines()
    prompt = [ln for ln in lines if "\\fad(300,0)" in ln]
    # One to three pills (each a box and its text), balanced, over exactly the last three seconds.
    assert len(prompt) in (2, 4, 6) and all(ln.split(",")[1] == "0:00:44.00" and ln.split(",")[2] == "0:00:47.00" for ln in prompt)
    reel = safe_zones.safe_box("reel", 1080, 1920)
    assert all(safe_zones.inside(r, reel) for r in caption_pills(ass))
    band = safe_zones.caption_band_top(1080, 1920)
    prompt_pills = [r for r in caption_pills(ass)][-len(prompt) // 2:]
    assert all(r[3] <= band for r in prompt_pills)                                 # above the captions' band
    # A documentary (16:9) gets no prompt.
    wide = tmp_path / "w.ass"
    VideoComposer().build_subtitles(words, wide, width=1920, height=1080, end_prompt=LINE, end_window=(44.0, 47.0))
    assert "\\fad(300,0)" not in wide.read_text(encoding="utf-8")
