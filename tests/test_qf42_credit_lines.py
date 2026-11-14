"""QF-42 (project quality review 2026-11-05, section 9.2): sanitised, capped credit lines.

A Commons file title or Artist field was published verbatim in the caption and description: a title
"File:x t.ly/y #tag" put a link and a hashtag in the post, an Artist field that is an e-mail address published the
address, the credit lines were exempt from the phrase checks, and the review card hid them when the caption was
long. Deterministic inputs only (no network, no output/ folder).
"""

import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CC_BY = {"license": "CC BY 4.0", "attribution_required": True,
         "source_url": "https://commons.wikimedia.org/wiki/File:X.jpg",
         "license_url": "https://creativecommons.org/licenses/by/4.0/"}
LINK = re.compile(r"t\.ly|https?://(?!commons\.wikimedia\.org/wiki/File:X\.jpg|creativecommons\.org/licenses/by/4\.0/)")


def _title_field(line: str) -> str:
    return line.removeprefix("📷 ").split(" | ")[1]


def test_the_review_example_title_publishes_no_link_or_hashtag_and_at_most_60_characters():
    from src.credits import asset_attribution
    assert asset_attribution({"title": "File:x t.ly/y #tag"}) == ""   # no licence asks for a credit: no line
    line = asset_attribution({**CC_BY, "artist": "Ann Lee", "title": "File:x t.ly/y #tag"})
    title = _title_field(line)
    assert "t.ly" not in line and "#" not in line and not LINK.search(line), line
    assert title == "File:x" and len(title) <= 60
    assert line.endswith(" | " + CC_BY["source_url"] + " | " + CC_BY["license_url"])


def test_a_long_title_is_capped_at_60_characters_at_a_word():
    from src.credits import TITLE_MAX, asset_attribution
    long = "File:" + "Underground corridor of the old city seen from the third level " * 3 + ".jpg"
    title = _title_field(asset_attribution({**CC_BY, "artist": "Ann Lee", "title": long}))
    assert TITLE_MAX == 60 and len(title) <= 60 and title.endswith("…") and title.startswith("File:Underground")


def test_no_address_handle_or_link_from_the_artist_field_reaches_the_credit():
    from src.credits import asset_attribution, author_name
    assert author_name({"artist": "@".join(("someone", "example.com"))}) == "Unknown"  # noqa: FLY002
    line = asset_attribution({**CC_BY, "artist": "Jane Roe @janeroe www.janeroe.example.com #photo",
                              "title": "File:Vault.jpg"})
    assert line.startswith("📷 Jane Roe | File:Vault.jpg | CC BY 4.0 | https://")
    assert "@" not in line and "#" not in line and "www." not in line


def test_commons_ingest_keeps_no_address_in_the_artist():
    from src import commons
    page = {"title": "File:Vault.jpg", "categories": [], "imageinfo": [{
        "mime": "image/jpeg", "width": 4000, "height": 3000, "url": "u", "size": 1000,
        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Vault.jpg",
        "extmetadata": {"Artist": {"value": "Jan Novak " + "@".join(("jan.novak", "example.org"))},  # noqa: FLY002
                        "LicenseShortName": {"value": "CC BY 4.0"}}}]}
    cand = next(iter(commons.candidates([page], [], allow_sa=True)))
    assert cand["artist"] == "Jan Novak"


def test_credit_lines_are_checked_for_links_hashtags_and_banned_phrases():
    from src.credits import asset_attribution, credit_line_problems
    raw = "📷 Ann | File:x t.ly/y #tag | CC BY 4.0 | https://a.example/x | https://b.example/y"
    found = credit_line_problems(raw)
    assert any("t.ly/y" in p for p in found) and any("#tag" in p for p in found)
    assert any("banned phrase" in p for p in credit_line_problems(
        "📷 Ann | File:Ancient aliens built it.jpg | CC BY 4.0 | https://a.example/x | https://b.example/y"))
    assert credit_line_problems(asset_attribution({**CC_BY, "artist": "Ann", "title": "File:x t.ly/y #tag"})) == []
    assert credit_line_problems("📷 Images via Wikimedia Commons and partner archives: Ann (CC BY 4.0)") == []


def test_the_review_card_always_shows_every_credit_line():
    from src.credits import asset_attribution
    from src.telegram_bot import TelegramApprovalBot
    lines = [asset_attribution({**CC_BY, "artist": f"Author {i}", "title": f"File:Photo {i}.jpg"}) for i in range(4)]
    caption = "Long caption. " * 140 + "\n\n" + "\n".join(lines)
    # A documentary-length narration is kept whole, so the caption is clipped to the room left on the card.
    payload = {"title": "T", "narration_script": "Words " * 300, "caption": caption, "hashtags": ["#A"]}
    for card in (TelegramApprovalBot._card_text(payload, "run_qf42", None, None),
                 TelegramApprovalBot.format_card_text(payload, "run_qf42")):
        for line in lines:
            assert html.escape(line) in card, line
        assert card.count("📷") >= len(lines)


def _crowded_payload(n_photos=10, findings=6):
    """A review card at Telegram's limit: a narration near 1,000 characters, a 2,000-character caption carrying
    `n_photos` CC BY credits (two URLs each, long titles) and folded findings."""
    from src.credits import asset_attribution
    lines = [asset_attribution({**CC_BY, "artist": f"Photographer Number {i}",
                                "source_url": f"https://commons.wikimedia.org/wiki/File:Corridor_{i}_of_the_lower_level.jpg",
                                "title": f"File:Corridor {i} of the lower level seen from the upper gallery at dusk.jpg"})
             for i in range(n_photos)]
    issues = [{"severity": "info", "card": "cover_look", "issue": "a long note about the cover " * 8}
              for _ in range(findings)]
    return {"title": "T", "narration_script": "Spoken words here. " * 55, "hashtags": ["#A"],
            "caption": "A caption sentence of ordinary length. " * 50 + "\n\n" + "\n".join(lines),
            "carousel_caption": "Swipe through the rooms.", "self_healing": {"issues": issues}}, lines


def test_a_card_full_of_credits_stays_inside_the_page_limit_and_lists_every_credit():
    """Review of QF-42 (round 1): whole credit lines pushed real cards past Telegram's 4,096 and the bot fell back
    to plain text cut at 4,000. The credits now have a length budget: shorter forms, each credit on its own line,
    the narration whole, and the reply under the card has every line whole."""
    from src.telegram_bot import (
        CARD_LIST_MORE,
        CARD_PAGE_LIMIT,
        TelegramApprovalBot,
        _tg_len,
        detail_messages,
    )
    for n in (3, 6, 10, 14):
        payload, lines = _crowded_payload(n)
        card = TelegramApprovalBot.format_card_text(payload, "run_qf42")
        assert _tg_len(card) <= CARD_PAGE_LIMIT, (n, _tg_len(card))
        assert "[shortened:" not in card and "<b>Caption:</b>" in card
        for i in range(n):  # every photo keeps its own 📷 line with its author
            assert f"📷 Photographer Number {i}" in card, (n, i)
        assert card.count("📷") >= n
        if any(html.escape(x) not in card for x in lines):  # a shortened credit says where the whole line is
            assert CARD_LIST_MORE in card
            full = TelegramApprovalBot._card_text(payload, "run_qf42", None, None, 10 ** 6)
            details = "\n".join(detail_messages(full))
            assert all(html.escape(x) in details for x in lines)
    # And with a card the findings fill, the reserve badge included, the credits never push it over.
    payload, _ = _crowded_payload(10, findings=12)
    assert _tg_len(TelegramApprovalBot.format_card_text(payload, "run_qf42", reserve=60)) <= CARD_PAGE_LIMIT - 60


def _old_line(rec):
    """The credit line asset_attribution wrote before QF-42 (start commit e1d8466): every field verbatim."""
    return "📷 " + " | ".join(" ".join(str(rec.get(k) or "Unknown").split())
                              for k in ("artist", "title", "license", "source_url", "license_url"))


def _gate_run(tmp_path, records, caption, description):
    """A run folder with a recorded provenance ledger for `records` and the payload's copy (no video: the media
    checks fail on their own; the credit checks are the ones read)."""
    from src.credits import sha256_file
    master = tmp_path / "master.mp4"
    master.write_bytes(b"not a video")
    assets = []
    for i, rec in enumerate(records):
        photo = tmp_path / f"photo_{i}.jpg"
        photo.write_bytes(bytes([i]) * 16)
        assets.append({**rec, "path": photo.name, "sha256": sha256_file(photo), "rights_status": "verified"})
    (tmp_path / "provenance.json").write_text(json.dumps({
        "status": "recorded", "issues": [], "master_video": {"path": master.name, "sha256": sha256_file(master)},
        "assets_count": len(assets), "assets": assets}), encoding="utf-8")
    return {"master_video_path": str(master),
            "script_payload": {"caption": caption, "description": description, "delivery_profile": "reel"}}


def _checks(report):
    return {c["name"]: c for c in report["checks"]}


LONG_TITLE = "File:Underground corridor of the old city seen from the third level at dusk, looking north.jpg"
LINKED_TITLE = "File:Stair t.ly/abc #travel.jpg"


def test_a_run_credited_before_the_change_keeps_its_gate_and_a_linked_old_line_is_fixed_by_a_rewrite(tmp_path):
    """Review of QF-42 (round 1): image_attribution looked only for the new line, so 16 of 31 real runs credited
    before the change failed it, and a run waiting for approval at deploy lost Approve & Post. An old line that
    rewrites to the asset's line counts; an old line with a link still fails credit_lines, and Edit (which rewrites
    the credit lines) clears it."""
    from src.credits import asset_attribution, rewrite_payload_credits
    from src.release import write_release_report
    safe = {**CC_BY, "artist": "Ann Lee", "title": LONG_TITLE}
    linked = {**CC_BY, "artist": "Jan Novak", "title": LINKED_TITLE}
    old = "\n".join(_old_line(r) for r in (safe, linked))
    assert len(_old_line(safe).split(" | ")[1]) > 60 and asset_attribution(safe) not in old
    run = _gate_run(tmp_path, [safe, linked], "Copy.\n\n" + old, "Description.\n\n" + old)
    checks = _checks(write_release_report(run))
    assert checks["image_attribution"]["status"] == "pass", checks["image_attribution"]
    assert checks["credit_lines"]["status"] == "fail" and "t.ly/abc" in str(checks["credit_lines"]["detail"])
    rewrite_payload_credits(run["script_payload"])          # what Edit and Regenerate do to the copy
    for field in ("caption", "description"):
        text = run["script_payload"][field]
        assert asset_attribution(safe) in text and asset_attribution(linked) in text and "t.ly" not in text
        assert text.count("📷") == 2
    checks = _checks(write_release_report(run))
    assert checks["image_attribution"]["status"] == "pass" and checks["credit_lines"]["status"] == "pass"


def test_an_edit_rewrites_the_old_credit_lines_of_every_public_field(db, tmp_path, monkeypatch):
    """Review of QF-42 (round 1): Edit kept the old lines (keep_attribution) and left the description alone, so an
    old line with a link held the run for good. Any edit now rewrites the credit lines of the copy."""
    from types import SimpleNamespace

    from main import PipelineCoordinator
    from src.credits import asset_attribution
    from tests.test_telegram_release_safety import ready_review
    ready_review(db, tmp_path)
    linked = {**CC_BY, "artist": "Jan Novak", "title": LINKED_TITLE}
    old = _old_line(linked)
    run = db.get_run("review")
    db.update_run("review", run["status"], script_payload={**run["script_payload"], "caption": "Old wording.\n\n" + old,
                                                           "description": "About it.\n\n" + old})
    coordinator = PipelineCoordinator.__new__(PipelineCoordinator)
    coordinator.db = db
    coordinator.topic_engine = SimpleNamespace(get_topic_by_id=db.get_topic_by_id)
    coordinator._verify_final_copy = lambda payload, topic: None
    coordinator.telegram_bot = SimpleNamespace(_strip_buttons=lambda mid: None, send_approval_request=lambda *a: None)
    monkeypatch.setattr("src.release.write_release_report", lambda run: {"checks": []})
    coordinator.edit_copy("review", {"caption": "New wording."})
    payload = db.get_run("review")["script_payload"]
    for field in ("caption", "description"):
        assert asset_attribution(linked) in payload[field] and old not in payload[field] and "t.ly" not in payload[field]
    assert payload["caption"].startswith("New wording.") and payload["caption"].count("📷") == 1


def test_regenerating_a_run_credited_before_the_change_leaves_one_new_line_per_photo(tmp_path):
    """Review of QF-42 (round 1): hold_attribution/restore_attribution put the old lines back and
    add_provenance_attributions added the new ones, so a regenerated description carried both copies."""
    from src.credits import (
        add_provenance_attributions,
        asset_attribution,
        hold_attribution,
        restore_attribution,
    )
    records = [{**CC_BY, "artist": "Ann Lee", "title": LONG_TITLE}, {**CC_BY, "artist": "Jan Novak", "title": LINKED_TITLE}]
    old = "\n".join(_old_line(r) for r in records)
    previous = {"caption": "Old copy.\n\n" + old, "description": "Old description.\n\n" + old}
    _, held = hold_attribution(previous)
    regenerated = restore_attribution({"caption": "New copy.", "description": previous["description"]}, held)
    ledger = tmp_path / "provenance.json"
    ledger.write_text(json.dumps({"assets": records}), encoding="utf-8")
    add_provenance_attributions(regenerated, ledger)
    for field in ("caption", "description"):
        credit_lines = [ln for ln in regenerated[field].splitlines() if ln.startswith("📷")]
        assert credit_lines == [asset_attribution(r) for r in records], (field, credit_lines)
        assert all(len(ln.split(" | ")[1]) <= 60 and "t.ly" not in ln and "#" not in ln for ln in credit_lines)


def test_no_committed_credits_file_holds_an_email_address():
    email = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
    found = []
    for path in sorted((ROOT / "assets").glob("*/credits.json")):
        text = path.read_text(encoding="utf-8")
        json.loads(text)
        found += [f"{path.relative_to(ROOT).as_posix()}: {len(email.findall(text))}"] if email.search(text) else []
    assert found == []
