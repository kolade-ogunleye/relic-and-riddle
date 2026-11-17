"""QF-42 acceptance metric: sanitised, capped credit lines.

Usage: python measure.py <repo root>. Prints the metric as JSON and writes measurements.json next to this file.

Runs the real code of the repo given, with deterministic inputs written here (no network, no output/ folder):
- credits.asset_attribution on the review's example title "File:x t.ly/y #tag" (as given, and on a CC BY record,
  which is the one that prints a credit) and on a small corpus of hostile Commons fields (an e-mail address as the
  artist, @handles, hashtags, links with and without a scheme, a 200-character title);
- telegram_bot.TelegramApprovalBot._card_text and format_card_text on a payload whose caption is long enough to be
  clipped on the card, carrying one credit line per photo: is every credit line on the card;
- every committed assets/*/credits.json record as a credit line (title length; links, addresses, handles);
- every committed assets/*/credits.json: e-mail addresses in it (the file and a count are printed, never the
  address).
Round 4 (review of round 3): the regression test's near-full cards (both captions show a preview without the
credits list): does each keep its preview, the narration whole and every photographer's 📷 line; and, with
RR_FIXTURE_REPO, each real card's caption previews, narration and findings, compared before/after in README.md.
Round 5 (review of round 4): the regression test's card whose credits list leaves both captions only room for where
to read them: is each caption still on the card; and on each real card, how its shortened findings lists are shown
(first items or count only) and at which findings caps it fits with the narration whole and every caption on it.
Round 2 (review of round 1):
- format_card_text on review cards crowded with credits (3 to 14 CC BY photos, two URLs each, a 2,000-character
  caption, 0 to 12 folded findings): card length against CARD_PAGE_LIMIT and Telegram's 4,096, every photo's 📷 line;
- release.write_release_report on run folders made here whose copy carries credit lines in the form written before
  QF-42 (a 90-character title; a title with a link and a hashtag): image_attribution and credit_lines, before and
  after credits.rewrite_payload_credits (what Edit and Regenerate now do; media checks are not run);
- the regeneration path (hold_attribution, restore_attribution, add_provenance_attributions) on that copy: photo
  credit lines in the caption and description, titles over 60 characters, links or hashtags.
- Optional, with RR_FIXTURE_REPO set to a checkout that has an output/ folder: the card and gate measures on its real
  run_* folders (read only: the gate's report is written to a temporary folder; media and checksum checks are not
  run, so the gate's credit checks read each run's real ledger and copy).
"""

import html
import json
import os
import re
import shutil
import socket
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXAMPLE = "File:x t.ly/y #tag"
CC_BY = {"license": "CC BY 4.0", "attribution_required": True,
         "source_url": "https://commons.wikimedia.org/wiki/File:Example.jpg",
         "license_url": "https://creativecommons.org/licenses/by/4.0/"}
# Independent of the code under test: what counts as a link, an address, a handle or a hashtag in credit text.
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
LINK = re.compile(r"(?:[a-z]+:)?//\S+|\bwww\.\S+|\b(?:[a-z0-9-]+\.)+[a-z]{2,}/\S*|\b[a-z0-9-]+\.(?:com|org|net|ly|io)\b",
                  re.IGNORECASE)
TAG = re.compile(r"(?<!\w)[@#][A-Za-z_]")
CORPUS = [
    {"artist": "@".join(("photographer", "example.com")),  # noqa: FLY002 (no address literal)
     "title": "File:Gate of the lower level.jpg"},
    {"artist": "Jane Roe @janeroe", "title": "File:Ventilation shaft #history #travel.jpg"},
    {"artist": "Jan Novak (see www.jannovak.example.com)", "title": "File:Stair t.ly/abc.jpg"},
    {"artist": "Ann Lee", "title": "File:Rolling door https://bit.ly/x more at example.org.jpg"},
    {"artist": "Ann Lee", "title": "File:" + "A very long description of a corridor and its rooms " * 4 + ".jpg"},
    {"artist": "Ann Lee", "title": EXAMPLE},
]


def _no_network():
    def refuse(*a, **k):
        raise RuntimeError("measure.py makes no network connection")
    socket.socket.connect = refuse
    socket.create_connection = refuse


def _fields(line: str) -> list:
    return line.removeprefix("📷").strip().split(" | ")


def _text_problems(line: str) -> list:
    """Links, addresses, handles or hashtags in the author, title and licence fields of a credit line."""
    text = " ".join(_fields(line)[:3])
    return [m.group(0) for p in (EMAIL, LINK, TAG) for m in p.finditer(text)]


TELEGRAM_HARD_LIMIT = 4096
LONG_TITLE = "File:Underground corridor of the old city seen from the third level at dusk, looking north.jpg"
LINKED_TITLE = "File:Stair t.ly/abc #travel.jpg"
SHORTENED = re.compile(r"…and \d+ more|<i>\d+ items?\b")   # a folded list cut to its first items (or to a count)


def _old_line(rec: dict) -> str:
    """The credit line asset_attribution wrote before QF-42 (start commit): every field verbatim."""
    return "📷 " + " | ".join(" ".join(str(rec.get(k) or "Unknown").split())
                              for k in ("artist", "title", "license", "source_url", "license_url"))


def _crowded(n: int, findings: int = 6) -> tuple:
    lines = [{**CC_BY, "artist": f"Photographer Number {i}",
              "source_url": f"https://commons.wikimedia.org/wiki/File:Corridor_{i}_of_the_lower_level.jpg",
              "title": f"File:Corridor {i} of the lower level seen from the upper gallery at dusk.jpg"} for i in range(n)]
    issues = [{"severity": "info", "card": "cover_look", "issue": "a long note about the cover " * 8}
              for _ in range(findings)]
    return {"title": "T", "narration_script": "Spoken words here. " * 55, "hashtags": ["#A"],
            "caption": "A caption sentence of ordinary length. " * 50 + "\n\n" + "\n".join(_old_line(r) for r in lines),
            "carousel_caption": "Swipe through the rooms.", "self_healing": {"issues": issues}}, lines


# Round 4 (review of round 3: the credits list cost six real cards their caption preview and five their narration).
CAPTION_END = r"(?=\n<b>Carousel caption:</b>|\n?<b>Hashtags:</b>|\n?<b>Tags:</b>|</blockquote>)"


def _caption(card: str, label: str) -> list:
    """[how the card shows the caption, characters of its text shown]: full, clipped, too_long (only where to read
    it) or absent. Credit lines are not counted (the card before the list showed them inside the caption)."""
    m = re.search(rf"<b>{label}:</b>\n(.*?)\n{CAPTION_END}", card, re.DOTALL)
    if not m:
        return ["absent", 0]
    text = m.group(1)
    if "too long for this card" in text:
        return ["too_long", 0]
    prose = "\n".join(ln for ln in html.unescape(text).splitlines() if not ln.startswith("📷")).strip()
    return ["clipped" if "(full text: Edit)" in text else "full", len(prose.replace(" (full text: Edit)", ""))]


def _card_row(card: str) -> dict:
    m = re.search(r"Narration and timings:</b>\n<i>(.*?)</i>\n\n<b>First comment", card, re.DOTALL)
    narration = html.unescape(m.group(1)) if m else ""
    return {"caption": _caption(card, "Caption"), "carousel_caption": _caption(card, "Carousel caption"),
            "narration_chars": len(narration), "narration_shortened": "[shortened:" in narration,
            "findings_shortened": bool(SHORTENED.search(card)),
            # Round 5: how each shortened folded list is shown: its first items then "…and N more", or its count only.
            "findings_lists_first_items": len(re.findall(r"…and \d+ more", card)),
            "findings_lists_count_only": len(re.findall(r"<i>\d+ items?\b", card)),
            "length": len(card.encode("utf-16-le")) // 2}


def _every_cap(payload: dict, run_id: str, release: dict) -> dict:
    """The card at every findings cap format_card_text tries (None, 4, 3, 2, 1, 0), with the credits list and
    without it: the most captions with a preview, the longest narration, and whether the list is in its shortest
    form (author alone) on every card it is on. A preview or narration length no cap gives with the list, the list
    in its shortest form, is lost to that form: every credit line shown forces it."""
    from src.telegram_bot import CARD_PAGE_LIMIT, TelegramApprovalBot

    def narration(card: str) -> int:
        m = re.search(r"Narration and timings:</b>\n<i>(.*?)</i>\n\n<b>First comment", card, re.DOTALL)
        return len(html.unescape(m.group(1))) if m else 0

    out = {}
    for name, credits in (("with_list", True), ("without_list", False)):
        try:
            cards = [TelegramApprovalBot._card_text(payload, run_id, release, cap, credits=credits)
                     for cap in (None, 4, 3, 2, 1, 0)]
        except TypeError:      # a tree before round 4: no card without the list to compare with
            return {}
        cards = [c for c in cards if len(c.encode("utf-16-le")) // 2 <= CARD_PAGE_LIMIT] or cards
        out[name] = {"most_caption_previews": max(
            sum(_caption(c, label)[0] in ("full", "clipped") for label in ("Caption", "Carousel caption"))
            for c in cards), "longest_narration": max(narration(c) for c in cards),
            "narration_chars_at_caps_none_4_3_2_1_0": [
                narration(TelegramApprovalBot._card_text(payload, run_id, release, cap, credits=credits))
                for cap in (None, 4, 3, 2, 1, 0)],
            # Round 5: the findings caps (None = every item) whose card fits with the narration whole and every
            # caption on it (a preview or where to read it): format_card_text takes the first.
            "caps_fitting_with_whole_narration_and_every_caption": [
                cap for cap in (None, 4, 3, 2, 1, 0)
                for c in [TelegramApprovalBot._card_text(payload, run_id, release, cap, credits=credits)]
                if len(c.encode("utf-16-le")) // 2 <= CARD_PAGE_LIMIT and "[shortened:" not in c
                and all(_caption(c, label)[0] != "absent" for key, label in (("caption", "Caption"),
                        ("carousel_caption", "Carousel caption")) if payload.get(key))]}
        if credits:
            lists = [re.search(r"Credits posted with the captions:</b> \d+\n<blockquote expandable>(.*?)</blockquote>",
                               c, re.DOTALL) for c in cards]
            out[name]["shortest_credit_form_on_every_card"] = all(m is None or "·" not in m.group(1) for m in lists)
    return out


def _room_payload(fill: int) -> dict:
    """The regression test's card (tests/test_qf42_caption_room.py): a whole narration, a 330-character carousel
    caption, a long caption carrying six CC BY credits by two photographers, a source note of `fill` sentences."""
    from src.credits import asset_attribution
    lines = [asset_attribution({**CC_BY, "artist": ("Ann Lee", "Jan Novak")[i % 2],
                                "source_url": f"https://commons.wikimedia.org/wiki/File:Hall_{i}_of_the_old_mine.jpg",
                                "title": f"File:Hall {i} of the old mine seen from the gallery.jpg"}) for i in range(6)]
    return {"title": "T", "narration_script": "Spoken words of the narration here. " * 20, "hashtags": ["#A"],
            "caption": "A caption sentence of ordinary length. " * 30 + "\n\n" + "\n".join(lines),
            "carousel_caption": "Swipe through the halls of the old mine, one by one. " * 6,
            "source_note": "📎 Sources: " + "a source note of the run that fills the card. " * fill}


def _room_cards() -> dict:
    """Near-full cards whose captions both show a preview without the credits list (at e1d8466): do they keep it
    with every photographer's 📷 line?"""
    from src.telegram_bot import TelegramApprovalBot
    out = {}
    for fill in (42, 45):
        card = TelegramApprovalBot.format_card_text(_room_payload(fill), "run_qf42")
        row = _card_row(card)
        out[f"source_note_{fill}"] = {**row, "every_author": all(f"📷 {a}" in card for a in ("Ann Lee", "Jan Novak"))}
    return out


def _see_edit_card() -> dict:
    """Round 5 (review of round 4, Wieliczka): the regression test's card whose credits list leaves both captions
    only the room of where to read them (source note of 52 sentences). Is each caption still on the card?"""
    from src.telegram_bot import TelegramApprovalBot
    card = TelegramApprovalBot.format_card_text(_room_payload(52), "run_qf42")
    row = _card_row(card)
    return {**row, "every_author": all(f"📷 {a}" in card for a in ("Ann Lee", "Jan Novak")),
            "every_caption_on_the_card": row["caption"][0] != "absent" and row["carousel_caption"][0] != "absent"}


def _card_measures(cards: dict, limit: int) -> dict:
    """{name: (card, [author names whose 📷 line it must show])} -> counts."""
    lens = {k: len(c.encode("utf-16-le")) // 2 for k, (c, _) in cards.items()}
    missing = {k: [a for a in authors if f"📷 {a}" not in c] for k, (c, authors) in cards.items()}
    return {"cards": len(cards), "over_card_page_limit": sum(n > limit for n in lens.values()),
            "over_telegram_4096": sum(n > TELEGRAM_HARD_LIMIT for n in lens.values()),
            "longest": max(lens.values(), default=0),
            "findings_shortened": sum(bool(SHORTENED.search(c)) for c, _ in cards.values()),
            "cards_missing_a_photo_credit": sum(bool(m) for m in missing.values()),
            "over_4096": sorted(f"{k}: {n}" for k, n in lens.items() if n > TELEGRAM_HARD_LIMIT)}


def _crowded_cards() -> dict:
    from src.telegram_bot import CARD_PAGE_LIMIT, TelegramApprovalBot
    cards = {}
    for n in (3, 6, 10, 14):
        for findings in (0, 6, 12):
            payload, recs = _crowded(n, findings)
            # The caption carries the lines asset_attribution writes in this tree (as a fresh render does).
            from src.credits import asset_attribution
            payload["caption"] = payload["caption"].split("\n\n📷")[0] + "\n\n" + "\n".join(
                asset_attribution(r) for r in recs)
            cards[f"{n}_photos_{findings}_findings"] = (TelegramApprovalBot.format_card_text(payload, "run_qf42"),
                                                        [r["artist"] for r in recs])
    return _card_measures(cards, CARD_PAGE_LIMIT)


def _no_media(module) -> None:
    """write_release_report without ffprobe: the media checks fail on their own; the credit checks are read."""
    import subprocess

    class _Sub:
        SubprocessError = subprocess.SubprocessError

        @staticmethod
        def run(*a, **k):
            raise subprocess.SubprocessError("measure.py runs no media tools")
    module.subprocess = _Sub


def _status(report: dict, name: str) -> str:
    return next((c["status"] for c in report["checks"] if c["name"] == name), "absent")


def _gate(tmp: Path, records: list, payload: dict) -> dict:
    from src import release
    from src.credits import sha256_file
    _no_media(release)
    master = tmp / "master.mp4"
    master.write_bytes(b"not a video")
    assets = []
    for i, rec in enumerate(records):
        photo = tmp / f"photo_{i}.jpg"
        photo.write_bytes(bytes([i]) * 16)
        assets.append({**rec, "path": photo.name, "sha256": sha256_file(photo), "rights_status": "verified"})
    (tmp / "provenance.json").write_text(json.dumps({
        "status": "recorded", "issues": [], "master_video": {"path": master.name, "sha256": sha256_file(master)},
        "assets_count": len(assets), "assets": assets}), encoding="utf-8")
    report = release.write_release_report({"master_video_path": str(master), "script_payload": payload})
    return {k: _status(report, k) for k in ("image_attribution", "credit_lines")}


def _old_runs() -> dict:
    """The gate on copy credited before QF-42, then after the rewrite Edit and Regenerate apply."""
    safe = {**CC_BY, "artist": "Ann Lee", "title": LONG_TITLE}
    linked = {**CC_BY, "artist": "Jan Novak", "title": LINKED_TITLE}
    out = {}
    with tempfile.TemporaryDirectory() as t:
        for name, recs in (("old_safe_lines", [safe]), ("old_line_with_a_link", [safe, linked])):
            old = "\n".join(_old_line(r) for r in recs)
            payload = {"caption": "Copy.\n\n" + old, "description": "Description.\n\n" + old, "delivery_profile": "reel"}
            d = Path(t) / name
            d.mkdir()
            out[name] = _gate(d, recs, payload)
            try:
                from src.credits import rewrite_payload_credits
            except ImportError:
                out[name + "_after_edit"] = "no rewrite in this tree (Edit keeps the old lines)"
                continue
            rewrite_payload_credits(payload)
            d = Path(t) / (name + "_edited")
            d.mkdir()
            out[name + "_after_edit"] = _gate(d, recs, payload)
        # Regeneration: the held lines are restored, then the render's ledger adds its lines.
        from src.credits import (
            add_provenance_attributions,
            hold_attribution,
            restore_attribution,
        )
        old = "\n".join(_old_line(r) for r in (safe, linked))
        previous = {"caption": "Old copy.\n\n" + old, "description": "Old description.\n\n" + old}
        _, held = hold_attribution(previous)
        regen = restore_attribution({"caption": "New copy.", "description": previous["description"]}, held)
        ledger = Path(t) / "ledger.json"
        ledger.write_text(json.dumps({"assets": [safe, linked]}), encoding="utf-8")
        add_provenance_attributions(regen, ledger)
        for field in ("caption", "description"):
            lines = [ln for ln in regen[field].splitlines() if ln.startswith("📷")]
            out[f"regenerated_{field}"] = {
                "photo_credit_lines": len(lines), "photos": 2,
                "titles_over_60": sum(len(_fields(ln)[1]) > 60 for ln in lines),
                "lines_with_link_or_hashtag": sum(bool(_text_problems(ln)) for ln in lines)}
    return out


def _real_runs() -> dict:
    """The card and gate measures on a checkout's real output/run_* folders (RR_FIXTURE_REPO), read only."""
    fixture = os.environ.get("RR_FIXTURE_REPO")
    out_dir = Path(fixture) / "output" if fixture else None
    if not out_dir or not out_dir.is_dir():
        return {"measured": False, "why": "RR_FIXTURE_REPO with an output/ folder is not set"}
    from src import release
    from src.credits import asset_attribution
    from src.telegram_bot import CARD_PAGE_LIMIT, TelegramApprovalBot
    _no_media(release)
    # The run folders' media were moved or re-rendered since their ledgers were written, so the checksum check is
    # taken as passed here: the gate's credit checks then read the run's real ledger and copy.
    release.verify_provenance_integrity = lambda ledger: True
    cards, gate_runs, gate_fail, credit_lines_fail, at_cap_0 = {}, 0, [], [], {}
    for d in sorted(out_dir.glob("run_*")):
        meta = d / "script_metadata.json"
        if not meta.is_file():
            continue
        payload = json.loads(meta.read_text(encoding="utf-8"))
        rep = d / "release_report.json"
        if rep.is_file():
            card = TelegramApprovalBot.format_card_text(payload, d.name, json.loads(rep.read_text(encoding="utf-8")))
            from src.credits import split_attribution
            authors = [_fields(ln)[0] for k in ("caption", "carousel_caption")
                       for ln in split_attribution(payload.get(k) or "")[1] if len(_fields(ln)) == 5]
            cards[d.name] = (card, authors)
            at_cap_0[d.name] = _every_cap(payload, d.name, json.loads(rep.read_text(encoding="utf-8")))
        ledger = d / "provenance.json"
        if ledger.is_file():
            data = json.loads(ledger.read_text(encoding="utf-8"))
            if any(asset_attribution(a) for a in data.get("assets", [])):
                gate_runs += 1
                with tempfile.TemporaryDirectory() as t:
                    shutil.copy(ledger, Path(t) / "provenance.json")
                    report = release.write_release_report({"master_video_path": str(Path(t) / "master.mp4"),
                                                           "script_payload": payload})
                gate_fail += [d.name] if _status(report, "image_attribution") != "pass" else []
                credit_lines_fail += [d.name] if _status(report, "credit_lines") == "fail" else []
    m = _card_measures(cards, CARD_PAGE_LIMIT)
    rows = {k: {**_card_row(c), "photo_authors_missing": sum(f"📷 {a}" not in c for a in set(authors)),
                **({"every_findings_cap": at_cap_0[k]} if at_cap_0.get(k) else {})}
            for k, (c, authors) in cards.items()}
    return {"measured": True, "cards": m, "per_card": rows, "runs_with_photo_credits": gate_runs,
            "image_attribution_not_pass": len(gate_fail), "image_attribution_not_pass_runs": gate_fail,
            "credit_lines_fail": len(credit_lines_fail), "credit_lines_fail_runs": credit_lines_fail}


def measure(root: Path) -> dict:
    sys.path.insert(0, str(root))
    _no_network()
    from src.credits import asset_attribution
    from src.telegram_bot import TelegramApprovalBot

    as_given = asset_attribution({"title": EXAMPLE})
    example = asset_attribution({**CC_BY, "artist": "Ann Lee", "title": EXAMPLE})
    lines = [asset_attribution({**CC_BY, **rec}) for rec in CORPUS]
    titles = [_fields(x)[1] for x in lines]

    credits = [asset_attribution({**CC_BY, "artist": f"Author {i}", "title": f"File:Photo {i}.jpg"}) for i in range(6)]
    # A documentary-length narration (300 words) and a caption near Instagram's 2,200 characters: the card
    # keeps the narration whole and clips the caption to the room left.
    payload = {"title": "T", "narration_script": "Words " * 300, "hashtags": ["#A"],
               "caption": "A long caption sentence. " * 60 + "\n\n" + "\n".join(credits)}
    cards = {"_card_text": TelegramApprovalBot._card_text(payload, "run_qf42", None, None),
             "format_card_text": TelegramApprovalBot.format_card_text(payload, "run_qf42")}
    shown = {name: sum(html.escape(c) in card for c in credits) for name, card in cards.items()}

    # Every committed credits record as a credit line (each treated as needing one, as a CC BY file does).
    records = []
    for path in sorted((root / "assets").glob("*/credits.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        records += [r for r in (data if isinstance(data, list) else data.get("images", [])) if isinstance(r, dict)]
    committed = [asset_attribution({**r, "attribution_required": True}) for r in records]

    emails = []
    for path in sorted((root / "assets").glob("*/credits.json")):
        found = EMAIL.findall(path.read_text(encoding="utf-8"))   # where, never the address itself
        emails += [f"{path.relative_to(root).as_posix()}: {len(found)} address(es)"] if found else []

    crowded = _crowded_cards()
    room = _room_cards()
    room_ok = all(r["caption"][0] in ("full", "clipped") and r["carousel_caption"][0] in ("full", "clipped")
                  and not r["narration_shortened"] and r["every_author"] for r in room.values())
    see_edit = _see_edit_card()
    see_edit_ok = (see_edit["every_caption_on_the_card"] and see_edit["every_author"]
                   and not see_edit["narration_shortened"])
    old_runs = _old_runs()
    real = _real_runs()
    old_ok = (old_runs["old_safe_lines"]["image_attribution"] == "pass"
              and isinstance(old_runs["old_line_with_a_link_after_edit"], dict)
              and set(old_runs["old_line_with_a_link_after_edit"].values()) == {"pass"}
              and all(old_runs[f"regenerated_{f}"]["photo_credit_lines"] == 2
                      and old_runs[f"regenerated_{f}"]["titles_over_60"] == 0
                      and old_runs[f"regenerated_{f}"]["lines_with_link_or_hashtag"] == 0
                      for f in ("caption", "description")))
    cards_ok = crowded["over_card_page_limit"] == 0 and crowded["cards_missing_a_photo_credit"] == 0

    return {
        "example_as_given": as_given,
        "example_cc_by_line": example,
        "example_title_field": _fields(example)[1],
        "example_title_chars": len(_fields(example)[1]),
        "example_has_link_or_hashtag": bool(_text_problems(example)),
        "corpus_lines": len(lines),
        "corpus_lines_with_link_address_handle_or_hashtag": sum(bool(_text_problems(x)) for x in lines),
        "corpus_findings": [p for x in lines for p in _text_problems(x)],
        "corpus_max_title_chars": max(len(t) for t in titles),
        "card_credit_lines": len(credits),
        "card_credit_lines_shown": shown,
        "card_every_asset_has_its_credit": all(n == len(credits) for n in shown.values()),
        "committed_credits_files": len(list((root / "assets").glob("*/credits.json"))),
        "committed_records": len(committed),
        "committed_lines_with_link_address_handle_or_hashtag": sum(bool(_text_problems(x)) for x in committed),
        "committed_max_title_chars": max((len(_fields(x)[1]) for x in committed), default=0),
        "committed_titles_over_60": sum(len(_fields(x)[1]) > 60 for x in committed),
        "emails_in_committed_credits": emails,
        "crowded_review_cards": crowded,
        "crowded_cards_within_limit_with_every_credit": cards_ok,
        "near_full_cards": room,
        "near_full_cards_keep_both_caption_previews_and_every_credit": room_ok,
        "see_edit_card": see_edit,
        "see_edit_card_keeps_every_caption_and_credit": see_edit_ok,
        "copy_credited_before_qf42": old_runs,
        "copy_credited_before_qf42_keeps_gate_and_rewrites": old_ok,
        "real_runs": real,
        "acceptance_met": (not _text_problems(example) and len(_fields(example)[1]) <= 60
                           and all(n == len(credits) for n in shown.values()) and not emails
                           and cards_ok and old_ok and room_ok and see_edit_ok),
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python measure.py <repo root>")
    result = measure(Path(sys.argv[1]).resolve())
    text = json.dumps(result, indent=2, ensure_ascii=False)
    (HERE / "measurements.json").write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)
