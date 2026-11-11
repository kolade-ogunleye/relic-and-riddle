"""QF-83: Shorts on by default, no thumbnail request after a Short's upload, and the loop bridge's FOLLOW (operator
2026-11-10: on in production). The source defaults are read in a fresh interpreter with no .env and the two settings
unset (tests/conftest.py pins both for every other test)."""
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageDraw

from src import config, end_card, safe_zones

ROOT = Path(__file__).resolve().parent.parent


def _source_default(name: str, tmp_path: Path, value: str | None = None):
    env = {k: v for k, v in os.environ.items() if k not in ("YOUTUBE_SHORTS", "LOOP_FOLLOW_OVERLAY")}
    env["RELIC_APP_ROOT"] = str(tmp_path)          # an app root without a .env
    if value is not None:
        env[name] = value
    out = subprocess.run([sys.executable, "-c", f"from src import config; print(repr(config.{name}))"], cwd=ROOT,
                         env=env, capture_output=True, text=True, timeout=120, check=False)
    assert out.returncode == 0, out.stderr[-800:]
    return eval(out.stdout.strip().splitlines()[-1])


def test_shorts_and_the_follow_are_on_unless_set_false(tmp_path):
    assert _source_default("YOUTUBE_SHORTS", tmp_path) is True
    assert _source_default("LOOP_FOLLOW_OVERLAY", tmp_path) is True
    assert _source_default("YOUTUBE_SHORTS", tmp_path, "false") is False       # an explicit setting still wins
    assert _source_default("LOOP_FOLLOW_OVERLAY", tmp_path, "false") is False
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "\nYOUTUBE_SHORTS=true\n" in example and "\nLOOP_FOLLOW_OVERLAY=true\n" in example


def test_a_default_dry_run_posts_a_short(db, monkeypatch, tmp_path):
    import main
    from src import publisher, release
    monkeypatch.setattr(config, "YOUTUBE_SHORTS", _source_default("YOUTUBE_SHORTS", tmp_path))
    monkeypatch.setattr(config, "TELEGRAM_CARDS_V2", False, raising=False)
    monkeypatch.setattr(release, "require_release", lambda run, dry_run=False: None)
    for name in ("master.mp4", "cover.jpg"):
        (tmp_path / name).write_bytes(b"offline fixture")
    topic = db.list_all_topics()[0]["id"]
    db.create_run("qf83", topic)
    db.update_run("qf83", "pending_approval", master_video_path=tmp_path / "master.mp4",
                  thumbnail_cover_path=tmp_path / "cover.jpg", thumbnail_grid_path=tmp_path / "cover.jpg",
                  script_payload={"title": "Fixture", "caption": "Checked fixture", "topic_id": topic})
    coord = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    coord.db, coord.publisher = db, publisher.MetaPublisher(db=db, publishers=[])
    coord.topic_engine = SimpleNamespace(record_status=lambda *a, **k: None)
    result = coord.publish_run("qf83", dry_run=True)
    assert result["publish_result"]["youtube_short"]["status"] == "simulated"
    assert [p["status"] for p in db.list_publications("qf83") if p["channel"] == "youtube-shorts"] == ["simulated"]


def test_a_published_short_makes_no_thumbnail_request(db, tmp_path):
    from src.publisher import MetaPublisher
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"\xff\xd8" + b"0" * 1000)
    calls = []
    uploader = SimpleNamespace(
        publish_short=lambda envelope: {"status": "published", "post_urls": {"youtube": "https://youtube.com/shorts/y6_GqrOprXE"}},
        set_youtube_thumbnail=lambda video_id, image: calls.append(video_id) or {"set": True, "error": None})
    topic = db.list_all_topics()[0]["id"]
    db.create_run("run_s", topic)
    out = MetaPublisher(db=db, publishers=[uploader]).publish_short(
        {"topic_id": topic, "run_id": "run_s", "media": {"thumbnail_path": str(cover)}, "metadata": {}})
    assert calls == [] and out == {"status": "published", "post_urls": {"youtube": "https://youtube.com/shorts/y6_GqrOprXE"}}
    row = db.list_publications("run_s")[-1]
    assert row["channel"] == "youtube-shorts" and row["status"] == "published"
    assert "thumbnail" not in json.loads(row["response_payload_json"])


def test_the_posting_view_names_the_frame_a_new_short_shows():
    from src.posting_view import _short_thumbnail
    short = [{"channel": "youtube-shorts", "status": "published", "response_payload_json": '{"post_urls": {}}'}]
    assert _short_thumbnail(short, short) == "frame"
    old = [{"channel": "youtube-shorts", "status": "published",
            "response_payload_json": '{"thumbnail": {"set": false, "error": "HTTP 403"}}'}]
    assert _short_thumbnail(old, old).startswith("not set")       # a Short posted before QF-83 keeps its line


def _photo(seed: int, light: bool) -> Image.Image:
    rng = np.random.default_rng(seed)
    base = 200 if light else 40
    arr = np.clip(base + rng.normal(0, 30, (1920 // 8, 1080 // 8, 3)), 0, 255).astype(np.uint8)
    im = Image.fromarray(arr).resize((1080, 1920), Image.BICUBIC)
    ImageDraw.Draw(im).rectangle([300, 650, 780, 900], fill=(250, 248, 240) if light else (20, 18, 16))
    return im


CASES = {"dark_to_light": (_photo(1, False), _photo(2, True)), "light_to_dark": (_photo(3, True), _photo(4, False)),
         "white": (Image.new("RGB", (1080, 1920), "white"), Image.new("RGB", (1080, 1920), "white")),
         "black": (Image.new("RGB", (1080, 1920), "black"), Image.new("RGB", (1080, 1920), "black"))}


@pytest.mark.parametrize("case", CASES)
def test_the_follow_sits_in_the_reel_box_reads_and_leaves_the_seam_alone(case):
    last, first = CASES[case]
    plain_stats, follow_stats = {}, {}
    plain = list(end_card.loop_bridge(last, first, "Send this on.", plain_stats, line=False))
    follow = list(end_card.loop_bridge(last, first, "Send this on.", follow_stats, line=False, follow=True))
    assert len(follow) == len(plain) == round(end_card.LOOP_SECONDS * end_card.FPS)     # no added time
    assert follow_stats["text"] == "FOLLOW" and follow_stats["follow"] is True
    assert follow_stats["contrast"] >= end_card.LOOP_FOLLOW_MIN and follow_stats["follow_frames"] >= 10
    assert end_card.seam_problems(follow_stats) == end_card.seam_problems(plain_stats)
    # its first and last frames are the purely visual bridge's: the loop still lands on frame 0
    for i in (0, -1):
        assert np.array_equal(np.asarray(follow[i]), np.asarray(plain[i]))
    x0, y0, x1, y1 = follow_stats["follow_box"]
    bx0, by0, bx1, by1 = safe_zones.union(*safe_zones.DELIVERABLES["reel"]).box()
    halo = end_card.LOOP_FOLLOW_HALO
    assert bx0 <= x0 - halo and x1 + halo <= bx1 and by0 <= y0 - halo and y1 + halo + end_card.LOOP_FOLLOW_RISE <= by1
    assert y1 + halo + end_card.LOOP_FOLLOW_RISE < safe_zones.caption_band_top()      # clear of frame 0's caption
    assert abs((x0 + x1) / 2 - 540) <= 2


def test_a_follow_that_cannot_read_is_left_off_and_the_review_card_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(end_card, "LOOP_FOLLOW_HALO", 1)          # no halo to speak of: bone on white cannot read
    white = Image.new("RGB", (1080, 1920), "white")
    stats = {}
    frames = list(end_card.loop_bridge(white, white, "Send this on.", stats, line=False, follow=True))
    assert len(frames) == 30 and stats["follow"] is False and stats["text"] is None and stats["follow_requested"]
    assert "needs 4.5:1" in stats["follow_skipped"]
    master = tmp_path / "m_master.mp4"
    master.with_suffix(".composition.json").write_text(json.dumps(
        {"end_card": {**stats, "variant": end_card.LOOP_VARIANT, "start_s": 3.0, "duration_s": 1.0}}), encoding="utf-8")
    record = end_card.end_card_record(master)
    assert record["follow"] is False and record["follow_skipped"] == stats["follow_skipped"]
    note = end_card.end_card_note(record)
    assert note["severity"] == "info" and "without its FOLLOW" in note["issue"]


def test_a_measured_follow_under_the_floor_is_a_gate_problem():
    assert end_card.seam_problems({"follow": True, "contrast": 3.9, "max_step_L": 1.0, "mad_last": 1.0,
                                   "mix_last": 29 / 30})
    assert not end_card.seam_problems({"follow": True, "contrast": 4.6, "max_step_L": 1.0, "mad_last": 1.0,
                                       "mix_last": 29 / 30})


def test_the_composer_asks_for_the_follow_and_the_master_keys_on_it():
    import inspect

    import main
    from src.video_composer import VideoComposer
    src = inspect.getsource(VideoComposer._render_with_loop_bridge)
    assert '"follow": bool(config.LOOP_FOLLOW_OVERLAY)' in src
    assert '"loop_follow_overlay": bool(config.LOOP_FOLLOW_OVERLAY)' in inspect.getsource(main.PipelineCoordinator)


def test_a_composed_reel_ends_on_the_bridge_with_its_follow(tmp_path, monkeypatch):
    import shutil

    import soundfile as sf
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg is required to render a master")
    from src.audio_engine import AudioEngine
    from src.video_composer import VideoComposer
    monkeypatch.setattr(config, "LOOP_FOLLOW_OVERLAY", True)
    sr, vo_dur = 48000, 2.0
    t = np.arange(int(sr * vo_dur)) / sr
    voice = tmp_path / "voiceover.wav"
    sf.write(voice, 0.3 * np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t)), sr)
    music = tmp_path / "music.wav"
    tm = np.arange(int(sr * 6)) / sr
    sf.write(music, np.column_stack([0.2 * np.sin(2 * np.pi * 110 * tm)] * 2), sr)
    bed = AudioEngine().generate_ambient_bed(vo_dur + end_card.LOOP_SECONDS + 0.05, tmp_path / "ambient_bed.wav",
                                             music_path=music, end_card_at=vo_dur)
    scenes = []
    for i, (last, _) in enumerate((CASES["dark_to_light"], CASES["light_to_dark"])):
        p = tmp_path / f"s{i}.jpg"
        last.save(p)
        scenes.append(p)
    words = [{"word": "Old", "start": 0.2, "dur": 0.4}, {"word": "maps.", "start": 0.7, "dur": 0.5}]
    master = tmp_path / "f_master.mp4"
    VideoComposer().compose_video(scenes, voice, bed, words, vo_dur, master, durations=[1.0, 1.0],
                                  end_card="Send this to the codebreaker you know.")
    card = json.loads(master.with_suffix(".composition.json").read_text(encoding="utf-8"))["end_card"]
    assert card["variant"] == end_card.LOOP_VARIANT and card["text"] == "FOLLOW" and card["follow"] is True
    assert card["contrast"] >= end_card.LOOP_FOLLOW_MIN
    record = end_card.end_card_record(master)
    assert record["text"] == "FOLLOW" and record["follow"] is True and end_card.end_card_note(record) is None
