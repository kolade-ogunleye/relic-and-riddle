"""A last draft blocked on a sentence or two the checker quoted is checked again without them, before the
run fails closed (production 2026-10-25: three Antikythera drafts, the last blocked only on "The bronze
lump was pulled specifically from the Aegean Sea."). Nothing is rewritten; every check runs again."""
from types import SimpleNamespace

import main
from src.script_engine import without_blocked_sentences

NARRATION = ("Divers found a lump of bronze in 1901. The bronze lump was pulled specifically from the Aegean Sea. "
             "Inside were gears no one expected. It is the oldest known geared computer.")
PAYLOAD = {"narration_script": NARRATION, "hook_sentence": "Divers found a lump of bronze in 1901.",
           "closing_sentence": "It is the oldest known geared computer.", "origin": "gemini-free"}
NOTES = ("REJECTED by second fact checker: [evidence re-check (gemini-3.7-flash)] 1 blocking claim(s): "
         "'The bronze lump was pulled specifically from the Aegean Sea.'")


def test_the_quoted_sentence_is_taken_out_and_nothing_else():
    out = without_blocked_sentences(PAYLOAD, NOTES)
    assert out["narration_script"] == ("Divers found a lump of bronze in 1901. Inside were gears no one expected. "
                                       "It is the oldest known geared computer.")
    assert out["trimmed_sentences"] == ["The bronze lump was pulled specifically from the Aegean Sea."]
    assert PAYLOAD["narration_script"] == NARRATION                                   # the draft itself untouched


def test_the_hook_and_closing_are_never_removed_and_paraphrases_are_not_guessed():
    assert without_blocked_sentences(PAYLOAD, "1 blocking claim(s): 'It is the oldest known geared computer.'") is None
    assert without_blocked_sentences(PAYLOAD, "1 blocking claim(s): 'Divers found a lump of bronze in 1901.'") is None
    assert without_blocked_sentences(PAYLOAD, "1 blocking claim(s): 'The lump came from the Aegean.'") is None
    assert without_blocked_sentences(PAYLOAD, "blocking: 'the bronze lump was pulled specifically from the aegean seas'") is None


def test_more_than_two_blocked_sentences_is_a_rewrite_not_a_trim():
    many = {"narration_script": "Hook one here now. A b c d. E f g h. I j k l. Closing line here now.",
            "hook_sentence": "Hook one here now.", "closing_sentence": "Closing line here now."}
    assert without_blocked_sentences(many, "'A b c d.' 'E f g h.' 'I j k l.'") is None


def _run(db, tmp_path, trimmed_passes):
    seen = []

    class FakeEngine:
        progress = None

        def generate_script(self, topic, revision_notes="", previous=None):
            return dict(PAYLOAD)

        def verify_claims(self, payload, topic=None, allow_unreviewed_curated=False):
            seen.append(payload["narration_script"])
            if "Aegean" in payload["narration_script"]:
                return False, payload, NOTES
            return (trimmed_passes, payload, "PASSED" if trimmed_passes else "Narration runs 38.0s when spoken (minimum 42s).")

        def _load_curated(self, topic_id):
            return None
    db.create_run("run_x", db.list_all_topics()[0]["id"])
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.db = db
    c.script_engine = FakeEngine()
    c._check_cancel = lambda run_id: None
    c.telegram_bot = SimpleNamespace(update_progress_card=lambda *a, **k: None)
    return c, seen


def test_a_trimmed_draft_that_passes_every_check_is_used(db, tmp_path):
    c, seen = _run(db, tmp_path, True)
    payload = c._write_and_check_script({"id": "t", "title": "T"}, "run_x", tmp_path, dry_run=True)
    assert payload["guardrail_passed"] and "Aegean" not in payload["narration_script"]
    assert payload["trimmed_sentences"] and len(seen) == main.MAX_FACT_REVISIONS + 2     # three drafts, one re-check


def test_a_trimmed_draft_that_fails_a_check_still_fails_closed(db, tmp_path):
    import pytest
    c, _ = _run(db, tmp_path, False)
    with pytest.raises(RuntimeError, match="minimum 42s"):
        c._write_and_check_script({"id": "t", "title": "T"}, "run_x", tmp_path, dry_run=True)


def test_the_review_card_says_what_was_removed():
    from src.telegram_bot import TelegramApprovalBot
    card = TelegramApprovalBot.format_card_text({"title": "T", "narration_script": "x", "caption": "c",
                                                 "trimmed_sentences": ["The lump was from the Aegean."]}, "run_x")
    assert "Removed after the fact check blocked them" in card and "The lump was from the Aegean." in card


def test_an_early_failure_card_offers_the_queue_and_says_the_topic_is_set_aside(db, monkeypatch):
    from src.telegram_bot import TelegramApprovalBot
    db.create_run("run_f", db.list_all_topics()[0]["id"])
    db.update_run("run_f", "failed", error="Script rejected by guardrail")
    bot = TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    shown = {}
    monkeypatch.setattr(bot, "edit_message_text", lambda mid, text, buttons=None: shown.update(text=text, buttons=buttons) or True)
    monkeypatch.setattr(bot, "_live_progress_card", lambda run_id: 9)
    bot.finish_progress_card(9, "T", "run_f", success=False, error="Script rejected by guardrail")
    assert "set aside, not retried on its own" in shown["text"] and "rendered again on a later run" not in shown["text"]
    assert shown["buttons"][0][0]["callback_data"] == f"requeue:{db.list_all_topics()[0]['id']}"
