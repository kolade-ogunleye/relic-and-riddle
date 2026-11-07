"""QF-42 (project quality review 2026-11-05, section 9.2), review of round 3: the credits list cost captions their
preview.

Every caption credit is listed on the review card, but the list took the captions' room: on real runs the main
caption read "(too long for this card: see Edit)" on 8 cards instead of 2, because a whole carousel caption, filled
first, took the room the credits left. The main caption now keeps the room of a clipped preview before a credit
form is chosen, the carousel caption is accounted for separately, the shorter credit forms come before a dropped
caption, and the findings are shortened before a caption loses the preview the card without the list showed.
Deterministic inputs only (no network, no output/ folder).
"""

import html
import re

CC_BY = {"license": "CC BY 4.0", "attribution_required": True,
         "license_url": "https://creativecommons.org/licenses/by/4.0/"}
AUTHORS = ("Ann Lee", "Jan Novak")


def _payload(fill: int) -> tuple:
    """A card near the page limit: a whole narration, a 330-character carousel caption, a long caption carrying six
    CC BY credits by two photographers, and a source note of `fill` sentences."""
    from src.credits import asset_attribution
    lines = [asset_attribution({**CC_BY, "artist": AUTHORS[i % 2],
                                "source_url": f"https://commons.wikimedia.org/wiki/File:Hall_{i}_of_the_old_mine.jpg",
                                "title": f"File:Hall {i} of the old mine seen from the gallery.jpg"}) for i in range(6)]
    return {"title": "T", "narration_script": "Spoken words of the narration here. " * 20, "hashtags": ["#A"],
            "caption": "A caption sentence of ordinary length. " * 30 + "\n\n" + "\n".join(lines),
            "carousel_caption": "Swipe through the halls of the old mine, one by one. " * 6,
            "source_note": "📎 Sources: " + "a source note of the run that fills the card. " * fill}, lines


def _preview(card: str, label: str) -> bool:
    """The caption is on the card with some of its text (not only where to read it)."""
    m = re.search(rf"<b>{label}:</b>\n(.*?)\n(?=\n<b>Carousel caption:</b>|<b>Hashtags:</b>)", card, re.DOTALL)
    return bool(m) and "too long for this card" not in m.group(1) and bool(m.group(1).strip())


def test_the_credits_list_leaves_both_captions_their_preview_and_the_narration_whole():
    from src.telegram_bot import CARD_PAGE_LIMIT, TelegramApprovalBot, _tg_len
    for fill in (42, 45):  # the card without the list shows both captions (the long one clipped) here
        payload, _ = _payload(fill)
        card = TelegramApprovalBot.format_card_text(payload, "run_qf42")
        assert _tg_len(card) <= CARD_PAGE_LIMIT
        assert "[shortened:" not in card, fill
        assert _preview(card, "Caption"), fill            # round 3: "(too long for this card: see Edit)"
        assert _preview(card, "Carousel caption"), fill
        for author in AUTHORS:                            # e1d8466: the credits were cut with the caption
            assert f"📷 {author}" in card, (fill, author)


def test_a_short_credit_form_names_each_photographer_once_with_the_count():
    """Review of round 3: '📷 Galvm' five times on one card. A shortened form lists a repeated line once."""
    from src.telegram_bot import TelegramApprovalBot
    payload, lines = _payload(42)
    card = TelegramApprovalBot.format_card_text(payload, "run_qf42")
    assert not any(html.escape(x) in card for x in lines)   # this card shows the short form
    listed = re.findall(r"• (📷 [^\n<]+)", card)
    assert len(listed) == len(set(listed)) == len(AUTHORS)
    counts = [re.search(r"\(×(\d+)\)$", x) for x in listed]
    assert sum(int(m.group(1)) if m else 1 for m in counts) == len(lines)  # every photo's credit is counted


def test_the_main_caption_keeps_a_preview_when_a_whole_carousel_caption_would_take_the_room():
    """_card_text alone (one findings cap): with credits the main caption's preview room is kept before the
    carousel caption is filled, when both previews fit."""
    from src.telegram_bot import TelegramApprovalBot
    payload, _ = _payload(48)
    card = TelegramApprovalBot._card_text(payload, "run_qf42", None, None)
    assert _preview(card, "Caption") and _preview(card, "Carousel caption")
    assert all(f"📷 {a}" in card for a in AUTHORS)


def test_a_caption_with_room_only_for_see_edit_keeps_that_line_rather_than_being_dropped():
    """Review of round 4 (Wieliczka): one clipped preview and a dropped caption outranked two "see Edit" lines, so the
    reviewer was no longer told the carousel caption existed. A caption whose "see Edit" line fits stays on the card
    (format_card_text: every caption shows "at least its first lines, or where to read it")."""
    from src.telegram_bot import (
        CAPTION_TOO_LONG,
        CARD_PAGE_LIMIT,
        TelegramApprovalBot,
        _tg_len,
    )
    payload, _ = _payload(52)  # the credits list leaves both captions only room for where to read them
    for card in (TelegramApprovalBot._card_text(payload, "run_qf42", None, None),
                 TelegramApprovalBot.format_card_text(payload, "run_qf42")):
        assert _tg_len(card) <= CARD_PAGE_LIMIT
        assert "[shortened:" not in card
        assert "<b>Caption:</b>" in card                 # e1d8466: left out
        assert "<b>Carousel caption:</b>" in card        # round 4: left out for the main caption's clipped preview
        assert card.count(CAPTION_TOO_LONG) == 2
        for author in AUTHORS:
            assert f"📷 {author}" in card, author
