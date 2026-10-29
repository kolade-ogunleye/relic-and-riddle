"""Audit gaps of 2026-10-28 (C:/tmp/audit2/a4): the Story body's word budget is the frame's measured one and a
Story body over three lines is shortened instead of the draft rewritten; the last-draft trim takes a blocked claim
out of the captions, slides and graphics too, and everything is checked again; an AAC overshoot is limited once
more before the run fails; a Telegram edit refused by flood control is held for the time given, and the latest
content is sent once."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from src import script_engine
from src.models import ScriptPackage
from src.multi_format import STORY_BODY_LINES, story_body_lines
from src.script_engine import ScriptEngine, story_body_word_limit, without_blocked_sentences

SHEET = {"topic_id": "t1", "facts": [], "sources": [{"title": "X", "url": "https://en.wikipedia.org/wiki/X", "tier": 2}]}
# 18 words that read as one plain sentence; at the Story's size they take four lines (the rejected drafts' count).
LONG_BODY = ("Archaeologists counted hundreds of rooms carved by hand across several underground levels "
             "beneath ordinary village houses over centuries.")
SHORT_BODY = "Hundreds of rooms were carved by hand beneath village houses."


# ---------------------------------------------------------------- 1. the Story body's budget and the slide edit
def test_the_prompts_story_budget_is_inside_what_the_frame_measures():
    limit, poll = story_body_word_limit(), story_body_word_limit(asks_poll=True)
    assert story_body_lines(LONG_BODY) > STORY_BODY_LINES and len(LONG_BODY.split()) == 18  # the old "18 words"
    ruler = script_engine._STORY_RULER * 3
    # The ruler's long words still fit with one word more than the limit, wherever the sentence starts.
    assert all(story_body_lines(" ".join(ruler[r:r + limit + 1]) + ".") <= STORY_BODY_LINES
               for r in range(len(script_engine._STORY_RULER)))
    assert poll < limit <= 14


def test_the_writer_is_told_the_measured_budget(monkeypatch):
    seen = []

    def stop(system, prompt, schema, **kw):
        seen.append(prompt)
        raise script_engine.LLMError("stop")
    engine = ScriptEngine()
    monkeypatch.setattr(script_engine, "_structured", stop)
    monkeypatch.setattr(engine, "_sheet", lambda topic: SHEET)
    with pytest.raises(script_engine.LLMError):
        engine._generate_with_claude({"id": "t1", "title": "T"}, "")
    assert (f"at most {story_body_word_limit()} words ({story_body_word_limit(asks_poll=True)} on slide 3)" in seen[0]
            and "18 words" not in seen[0])


def _surgical(monkeypatch, curated_payload, shortened_texts):
    engine = ScriptEngine()
    monkeypatch.setattr(script_engine, "_ready", lambda: True)
    monkeypatch.setattr(engine, "_sheet", lambda topic: SHEET)
    monkeypatch.setattr(engine, "_finalize", lambda pkg, topic, **k: {"story_slides": [s.model_dump() for s in pkg.story_slides], **k})
    monkeypatch.setattr(script_engine, "spoken_length_problems", lambda pkg: [])
    monkeypatch.setattr(script_engine, "hook_problems", lambda narration: [])
    monkeypatch.setattr(script_engine.improve, "note_problems", lambda problems, source: None)
    slides = [dict(s) for s in curated_payload["story_slides"]]
    slides[2]["body"] = LONG_BODY
    revised = ScriptPackage.model_validate(dict(curated_payload, story_slides=slides))
    assert [p for p in script_engine.validate_structure(revised)] == [
        p for p in script_engine.validate_structure(revised) if p.startswith("Story slide 3's body takes 4 lines")]
    monkeypatch.setattr(engine, "_revise_surgically", lambda previous, findings, sheet: revised)
    calls = {"writer": 0, "shorten": []}

    def write(topic, feedback):
        calls["writer"] += 1
        return ScriptPackage.model_validate(dict(curated_payload))
    monkeypatch.setattr(engine, "_generate_with_claude", write)
    monkeypatch.setattr(script_engine.free_llm, "enabled", lambda: True)
    monkeypatch.setattr(script_engine.free_llm, "structured", lambda system, prompt, schema, **kw: (
        calls["shorten"].append(prompt) or schema(texts=shortened_texts)))
    previous = dict(curated_payload, fact_sheet=True)
    out = engine.generate_script({"id": "t1", "title": "T"}, revision_notes="'a claim' (unsupported)", previous=previous)
    return out, calls


def test_a_surgical_revision_long_only_on_a_story_slide_is_shortened_not_rewritten(monkeypatch, curated_payload):
    out, calls = _surgical(monkeypatch, curated_payload, [SHORT_BODY])
    assert calls["writer"] == 0                                          # no full rewrite
    assert len(calls["shorten"]) == 1 and calls["shorten"][0].startswith("Slide 3 (at most 10 words): ")
    assert out["story_slides"][2]["body"] == SHORT_BODY and out["fact_sheet_used"] is True
    assert out["story_slides"][:2] == curated_payload["story_slides"][:2]  # nothing else touched


@pytest.mark.parametrize("texts", [[LONG_BODY], ["Was it carved by hand beneath the houses?"], [""], ["A. B."], []])
def test_a_shortening_that_still_misses_falls_back_to_the_rewrite(monkeypatch, curated_payload, texts):
    # still four lines / a question on the poll frame / empty / two sentences / the wrong count
    out, calls = _surgical(monkeypatch, curated_payload, texts)
    assert calls["writer"] == 1 and out["story_slides"] == curated_payload["story_slides"]


def test_a_fresh_draft_long_only_on_a_story_slide_is_shortened_in_place(monkeypatch, curated_payload):
    engine = ScriptEngine()
    monkeypatch.setattr(script_engine, "_ready", lambda: True)
    monkeypatch.setattr(engine, "_sheet", lambda topic: SHEET)
    monkeypatch.setattr(engine, "_finalize", lambda pkg, topic, **k: {"story_slides": [s.model_dump() for s in pkg.story_slides]})
    monkeypatch.setattr(script_engine, "spoken_length_problems", lambda pkg: [])
    monkeypatch.setattr(script_engine, "hook_problems", lambda narration: [])
    monkeypatch.setattr(script_engine.improve, "note_problems", lambda problems, source: None)
    slides = [dict(s) for s in curated_payload["story_slides"]]
    slides[0]["body"] = LONG_BODY
    written = []
    monkeypatch.setattr(engine, "_generate_with_claude", lambda topic, feedback: written.append(1) or
                        ScriptPackage.model_validate(dict(curated_payload, story_slides=slides)))
    monkeypatch.setattr(script_engine.free_llm, "enabled", lambda: True)
    monkeypatch.setattr(script_engine.free_llm, "structured",
                        lambda system, prompt, schema, **kw: schema(texts=[SHORT_BODY]))
    out = engine.generate_script({"id": "t1", "title": "T"})
    assert len(written) == 1 and out["story_slides"][0]["body"] == SHORT_BODY


# ---------------------------------------------------------------- 2. the trim, beyond the narration
CAPTION_CLAIM = "The settlement was sealed by rolling stone doors weighing several tons."
NOTES = ("REJECTED by second fact checker: [evidence re-check (gemini-3.7-flash)] 1 blocking claim(s): "
         f"'{CAPTION_CLAIM}' (unsupported: no quote gives the doors' weight.)")


def _with_caption_claim(curated_payload, caption=None):
    first, rest = curated_payload["caption"].split("\n\n", 1)
    return dict(curated_payload, origin="gemini-free",
                caption=caption or f"{first}\n\n{CAPTION_CLAIM} {rest}")


def test_a_blocked_claim_only_in_the_caption_is_taken_out_and_nothing_else(curated_payload):
    payload = _with_caption_claim(curated_payload)
    out = without_blocked_sentences(payload, NOTES)
    assert CAPTION_CLAIM not in out["caption"] and out["caption"] == curated_payload["caption"]
    assert out["narration_script"] == payload["narration_script"] and out["story_slides"] == payload["story_slides"]
    assert out["trimmed_sentences"] == [CAPTION_CLAIM] and out["trimmed_from"] == ["caption"]
    assert CAPTION_CLAIM in payload["caption"]                                       # the draft itself untouched


def test_a_claim_in_a_caption_line_of_its_own_takes_the_line_with_it(curated_payload):
    first, rest = curated_payload["caption"].split("\n\n", 1)
    out = without_blocked_sentences(_with_caption_claim(curated_payload, f"{first}\n{CAPTION_CLAIM}\n\n{rest}"), NOTES)
    assert out["caption"] == curated_payload["caption"]


def test_a_trim_that_would_leave_a_back_reference_dangling_is_refused(curated_payload, caplog):
    claim = "The wreck sank off the island in 60 BC."
    notes = f"1 blocking claim(s): '{claim}' (unsupported)"
    hook, closing = curated_payload["hook_sentence"], curated_payload["closing_sentence"]
    for follow in ("That date makes it the oldest wreck of its kind.", "It was found by sponge divers in 1900.",
                   "Their cargo held bronze statues and coins.", "The same storm sank two other ships nearby."):
        payload = dict(curated_payload, narration_script=f"{hook} {claim} {follow} {closing}")
        with caplog.at_level("INFO", logger="src.script_engine"):
            assert without_blocked_sentences(payload, notes) is None, follow
        assert "points back at it" in caplog.text
    # in a caption, and in a slide body, too
    first, rest = curated_payload["caption"].split("\n\n", 1)
    assert without_blocked_sentences(dict(curated_payload, caption=f"{first}\n\n{claim} It held gears. {rest}"),
                                      notes) is None
    slides = [dict(c) for c in curated_payload["carousel_slides"]]
    slides[2]["body"] = f"{claim} Its cargo held gears."
    assert without_blocked_sentences(dict(curated_payload, carousel_slides=slides), notes) is None
    # a sentence that stands on its own is no reason to refuse ("Herculaneum" is not "Her")
    payload = dict(curated_payload, narration_script=f"{hook} {claim} Herculaneum lies on the bay. {closing}")
    out = without_blocked_sentences(payload, notes)
    assert out["narration_script"] == f"{hook} Herculaneum lies on the bay. {closing}"
    assert out["word_count"] == len(out["narration_script"].split())                 # counted again


def test_a_blocked_story_heading_is_refused_as_the_checker_reads_it(curated_payload):
    slides = [dict(s) for s in curated_payload["story_slides"]]
    slides[1].update(kicker="WHAT HE FOUND", line1="AN UNDERGROUND CITY", line2="~85 METRES DEEP")
    notes = "1 blocking claim(s): 'AN UNDERGROUND CITY 85 METRES DEEP' (unsupported)"   # "line1 line2" (fact_checker)
    assert without_blocked_sentences(dict(curated_payload, story_slides=slides), notes) is None


def test_what_cannot_be_trimmed_is_left_to_the_rejection(curated_payload):
    first, rest = curated_payload["caption"].split("\n\n", 1)
    # the caption's first line
    assert without_blocked_sentences(_with_caption_claim(curated_payload, f"{CAPTION_CLAIM}\n\n{rest}"), NOTES) is None
    # a story body that is that one sentence
    slides = [dict(s) for s in curated_payload["story_slides"]]
    slides[1]["body"] = CAPTION_CLAIM
    assert without_blocked_sentences(dict(curated_payload, story_slides=slides), NOTES) is None
    # the poll frame's body, whose only other sentence is a question the frame drops
    slides = [dict(s) for s in curated_payload["story_slides"]]
    slides[2]["body"] = f"{CAPTION_CLAIM} Would you hide there?"
    assert without_blocked_sentences(dict(curated_payload, story_slides=slides), NOTES) is None
    # a headline
    assert without_blocked_sentences(dict(curated_payload, title=CAPTION_CLAIM), NOTES) is None


def test_a_slide_body_and_a_graphic_entry_are_trimmed_and_the_graphic_dropped_when_too_thin(curated_payload):
    slides = [dict(c) for c in curated_payload["carousel_slides"]]
    slides[2]["body"] = f"{slides[2]['body']} {CAPTION_CLAIM}"
    cards = [{"value": "8 levels", "label": "carved into soft volcanic tuff"}]
    notes = NOTES + " | 'Number card: 8 levels carved into soft volcanic tuff' (unsupported)"
    out = without_blocked_sentences(dict(curated_payload, carousel_slides=slides, stat_cards=cards), notes)
    assert out["carousel_slides"][2]["body"] == curated_payload["carousel_slides"][2]["body"]
    assert out["stat_cards"] is None and len(out["trimmed_sentences"]) == 2
    assert out["trimmed_from"] == ["carousel slide 3", "stat cards"]


def _public(payload):
    """What the package publishes (not the run's record of its rejections or of what a trim removed)."""
    return json.dumps({k: payload.get(k) for k in ("title", "narration_script", "caption", "carousel_caption",
                                                   "first_comment", "story_slides", "carousel_slides", "stat_cards")})


def _coordinator(db, payload, trimmed_passes):
    import main
    seen = []

    class FakeEngine:
        progress = None

        def generate_script(self, topic, revision_notes="", previous=None):
            return dict(payload)

        def verify_claims(self, p, topic=None, allow_unreviewed_curated=False):
            seen.append(_public(p))
            if CAPTION_CLAIM in _public(p):
                return False, p, NOTES
            return trimmed_passes, p, "PASSED" if trimmed_passes else "REJECTED: the caption's first line repeats the hook."

        def _load_curated(self, topic_id):
            return None
    db.create_run("run_x", db.list_all_topics()[0]["id"])
    c = main.PipelineCoordinator.__new__(main.PipelineCoordinator)
    c.db, c.script_engine = db, FakeEngine()
    c._check_cancel = lambda run_id: None
    c.telegram_bot = SimpleNamespace(update_progress_card=lambda *a, **k: None)
    return c, seen


def test_the_trimmed_caption_is_checked_again_in_full(db, tmp_path, curated_payload):
    import main
    c, seen = _coordinator(db, _with_caption_claim(curated_payload), True)
    out = c._write_and_check_script({"id": "t", "title": "T"}, "run_x", tmp_path, dry_run=True)
    assert out["guardrail_passed"] and CAPTION_CLAIM not in out["caption"]
    assert len(seen) == main.MAX_FACT_REVISIONS + 2 and CAPTION_CLAIM not in seen[-1]  # the re-check


def test_a_trimmed_caption_that_fails_its_re_check_still_rejects(db, tmp_path, curated_payload):
    c, _ = _coordinator(db, _with_caption_claim(curated_payload), False)
    with pytest.raises(RuntimeError, match="repeats the hook"):
        c._write_and_check_script({"id": "t", "title": "T"}, "run_x", tmp_path, dry_run=True)


def test_an_untrimmable_claim_still_rejects_and_says_why(db, tmp_path, curated_payload, caplog):
    rest = curated_payload["caption"].split("\n\n", 1)[1]
    c, seen = _coordinator(db, _with_caption_claim(curated_payload, f"{CAPTION_CLAIM}\n\n{rest}"), True)
    with caplog.at_level("INFO", logger="src.script_engine"):
        with pytest.raises(RuntimeError, match="doors' weight"):
            c._write_and_check_script({"id": "t", "title": "T"}, "run_x", tmp_path, dry_run=True)
    assert all(CAPTION_CLAIM in p for p in seen)              # no trimmed draft was passed off
    assert "the caption's first line carries one" in caplog.text


def test_the_review_card_says_where_a_trimmed_claim_was():
    from src.telegram_bot import TelegramApprovalBot
    card = TelegramApprovalBot.format_card_text({"title": "T", "narration_script": "x", "caption": "c",
                                                 "trimmed_sentences": ["A claim.", "Another claim."],
                                                 "trimmed_from": ["narration", "caption, story slide 2"]}, "run_x")
    assert "“A claim.” “Another claim.” (caption, story slide 2)" in card


# ---------------------------------------------------------------- 3. AAC overshoot
def test_the_master_chain_low_passes_before_its_limiter():
    from src.video_composer import _master_chain
    chain = _master_chain(0.0)
    assert "lowpass=f=16000:poles=2" in chain and chain.index("lowpass=") < chain.index("alimiter=")


def _master(tmp_path):
    master = tmp_path / "master.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:r=10:d=2",
                    "-f", "lavfi", "-i", "sine=frequency=997:sample_rate=48000:duration=2",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-b:a", "192k", "-ac", "2",
                    "-shortest", str(master)], check=True, capture_output=True, timeout=30)
    return master


def _measured(monkeypatch, values):
    from src import encoded_audio
    seq = iter(values)
    monkeypatch.setattr(encoded_audio, "measure_program_loudness", lambda path: next(seq))
    filters = []
    real = encoded_audio.subprocess.run

    def run(cmd, **kw):
        filters.append(cmd[cmd.index("-af") + 1])
        return real(cmd, **kw)
    monkeypatch.setattr(encoded_audio.subprocess, "run", run)
    return filters


def test_an_overshoot_a_volume_cut_cannot_fix_is_limited_once_more(monkeypatch, tmp_path):
    from src.encoded_audio import finalize_encoded_audio
    master = _master(tmp_path)
    before = master.read_bytes()
    # +1.0 dB over a -1.5 dBTP limit: turning it down 1.3 dB would leave -15.3 LUFS, outside the 1 LU tolerance.
    filters = _measured(monkeypatch, [{"integrated_lufs": -14.0, "true_peak_dbtp": -0.5},
                                      {"integrated_lufs": -14.2, "true_peak_dbtp": -1.7}])
    report = finalize_encoded_audio(master, -14.0, -1.5)
    assert len(filters) == 1 and "lowpass=f=16000" in filters[0] and "alimiter=limit=0.6888" in filters[0]
    assert report["limiter_ceiling_db"] == -3.24 and report["attenuation_db"] == 0.0  # -1.94 dBFS less 1.3 dB
    assert master.read_bytes() != before and sorted(p.name for p in tmp_path.iterdir()) == ["master.mp4"]


def test_a_lowered_limiter_that_still_misses_fails_and_keeps_the_master(monkeypatch, tmp_path):
    from src.encoded_audio import finalize_encoded_audio
    master = _master(tmp_path)
    before = master.read_bytes()
    _measured(monkeypatch, [{"integrated_lufs": -14.0, "true_peak_dbtp": -0.5},
                            {"integrated_lufs": -14.2, "true_peak_dbtp": -1.2}])
    with pytest.raises(ValueError, match="cannot satisfy loudness and peak limits"):
        finalize_encoded_audio(master, -14.0, -1.5)
    assert master.read_bytes() == before and sorted(p.name for p in tmp_path.iterdir()) == ["master.mp4"]


def test_the_lowered_ceiling_step_is_small_and_bounded(monkeypatch, tmp_path):
    from src.encoded_audio import finalize_encoded_audio
    master = _master(tmp_path)
    # 3 dB over: the step stops at 1.5 dB, and the result is still required to meet the limits.
    filters = _measured(monkeypatch, [{"integrated_lufs": -14.0, "true_peak_dbtp": 1.5},
                                      {"integrated_lufs": -14.3, "true_peak_dbtp": -0.1}])
    with pytest.raises(ValueError, match="cannot satisfy"):
        finalize_encoded_audio(master, -14.0, -1.5)
    assert "alimiter=limit=0.6731" in filters[0]                          # -1.94 - 1.5 = -3.44 dBFS


# ---------------------------------------------------------------- 4. Telegram flood control on edits
class _Timers:
    def __init__(self):
        self.made = []

    def __call__(self, delay, fn, args=()):
        timer = SimpleNamespace(delay=delay, fire=lambda: fn(*args), daemon=False, started=False, cancelled=False)
        timer.start = lambda: setattr(timer, "started", True)
        timer.cancel = lambda: setattr(timer, "cancelled", True)
        self.made.append(timer)
        return timer


def _flood_bot(db, monkeypatch, refuse=1, gate=None):
    """A bot whose first `refuse` edits Telegram answers with a 429 (retry after 8 s); `gate` (an Event) makes the
    next send wait until it is set. The clock can be moved on (clock[0] seconds)."""
    import time as _time
    from src import telegram_bot
    bot = telegram_bot.TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    timers, sent, clock = _Timers(), [], [0.0]
    bot._edit_timer = timers
    real = _time.monotonic
    monkeypatch.setattr(telegram_bot.time, "monotonic", lambda: real() + clock[0])

    def post(url, **kwargs):
        sent.append((kwargs["data"]["message_id"], kwargs["data"]["text"]))
        if len(sent) <= refuse:
            return SimpleNamespace(headers={"content-type": "application/json"}, status_code=429, ok=False, json=lambda: {
                "ok": False, "description": "Too Many Requests: retry after 8", "parameters": {"retry_after": 8}})
        if gate is not None and len(sent) == refuse + 1:
            assert gate.wait(5)
        return SimpleNamespace(headers={"content-type": "application/json"}, status_code=200, ok=True,
                               json=lambda: {"ok": True, "result": {"message_id": kwargs["data"]["message_id"]}})
    monkeypatch.setattr(telegram_bot, "_post", post)
    monkeypatch.setattr(telegram_bot.time, "sleep", lambda s: pytest.fail("an edit never waits in place"))
    return bot, timers, sent, clock


def test_a_429_holds_the_chats_edits_and_the_latest_of_each_is_sent_once(db, monkeypatch):
    bot, timers, sent, clock = _flood_bot(db, monkeypatch)
    assert bot.edit_message_text(1198, "step 1")                         # refused: queued, not failed
    assert bot.edit_message_text(1198, "step 2") and bot.edit_message_text(1198, "step 3")
    assert bot.edit_message_text(77, "another card")                     # the chat waits too (per-chat limit)
    assert [m for m, _ in sent] == [1198]
    assert len(timers.made) == 2 and all(8 <= t.delay <= 9 and t.started for t in timers.made)
    clock[0] = 9                                                         # the wait is over
    for t in timers.made:
        t.fire()
    assert sorted((m, "step 3" in t or "another card" in t) for m, t in sent[1:]) == [(77, True), (1198, True)]
    assert bot.edit_message_text(1198, "step 4") and len(sent) == 4 and "step 4" in sent[-1][1]  # edits flow again
    assert len(timers.made) == 2


def test_an_edit_made_while_a_held_one_is_sent_goes_after_it(db, monkeypatch):
    import threading
    gate = threading.Event()
    bot, timers, sent, clock = _flood_bot(db, monkeypatch, gate=gate)
    assert bot.edit_message_text(1198, "older")
    clock[0] = 9
    flush = threading.Thread(target=timers.made[0].fire)
    flush.start()
    for _ in range(200):                                                 # the held edit is on its way (slow)
        if len(sent) == 2:
            break
        threading.Event().wait(0.01)
    assert bot.edit_message_text(1198, "newer") and len(sent) == 2       # queued behind it, not sent alongside
    gate.set()
    flush.join(5)
    assert ["older" in t for _, t in sent[1:]] == [True, False] and "newer" in sent[-1][1]
    assert not bot._edit_sending and not bot._edit_pending


def test_drain_sends_held_edits_now_and_cancels_their_timers(db, monkeypatch):
    bot, timers, sent, clock = _flood_bot(db, monkeypatch)
    buttons = [[{"text": "Done", "callback_data": "cdash:"}]]
    assert bot.edit_message_text(1198, "final card", buttons=buttons)
    buttons[0][0]["text"] = "changed by the caller"                      # the queued edit kept its own copy
    bot.drain()
    assert timers.made[0].cancelled and not bot._edit_timers and not bot._edit_pending
    assert len(sent) == 2 and "final card" in sent[-1][1] and bot._edit_chat_held_until == 0.0


def test_an_older_edit_never_replaces_a_newer_one_held(db):
    from src import telegram_bot
    bot = telegram_bot.TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    bot._edit_timer = _Timers()
    bot._hold_edits(5, 3, 10, ("newer", None))
    bot._hold_edits(5, 3, 9, ("older", None))                            # its 429 came back later
    assert bot._edit_pending[5] == (10, ("newer", None))


def test_a_queued_edit_keeps_a_copy_of_its_buttons(db, monkeypatch):
    bot, timers, sent, clock = _flood_bot(db, monkeypatch, refuse=1)
    assert bot.edit_message_text(1198, "first")
    buttons = [[{"text": "Keep", "callback_data": "cdash:"}]]
    assert bot.edit_message_text(1198, "second", buttons=buttons)
    buttons[0][0]["text"] = "Mutated"
    assert bot._edit_pending[1198][1][1][0][0]["text"] == "Keep"


def test_a_held_send_refused_again_waits_out_the_new_hold_on_a_timer(db, monkeypatch):
    bot, timers, sent, clock = _flood_bot(db, monkeypatch, refuse=2)
    assert bot.edit_message_text(1198, "older")
    clock[0] = 9
    timers.made[0].fire()                                                # refused again: no loop of requests
    assert len(sent) == 2 and len(timers.made) == 2 and timers.made[1].started and not bot._edit_sending
    assert bot.edit_message_text(1198, "latest") and len(sent) == 2      # still held: queued
    clock[0] = 18
    timers.made[1].fire()
    assert len(sent) == 3 and "latest" in sent[-1][1] and not bot._edit_pending


def test_a_late_timer_does_not_skip_a_longer_hold_of_the_chat(db, monkeypatch):
    bot, timers, sent, clock = _flood_bot(db, monkeypatch)
    assert bot.edit_message_text(1198, "card")
    bot._hold_edits(77, 60, 99, ("another card", None))                  # a longer wait came later, for the chat
    clock[0] = 9
    timers.made[0].fire()
    assert len(sent) == 1 and bot._edit_pending[1198][1][0] == "card"   # not sent into the longer hold
    assert len(timers.made) == 3 and 50 <= timers.made[-1].delay <= 52


def test_drain_waits_for_a_held_send_on_its_way_and_then_sends_what_queued_behind_it(db, monkeypatch):
    import threading
    gate = threading.Event()
    bot, timers, sent, clock = _flood_bot(db, monkeypatch, gate=gate)
    assert bot.edit_message_text(1198, "older")
    clock[0] = 9
    flush = threading.Thread(target=timers.made[0].fire)
    flush.start()
    for _ in range(200):
        if len(sent) == 2:
            break
        threading.Event().wait(0.01)
    assert bot.edit_message_text(1198, "newer")
    threading.Timer(0.2, gate.set).start()
    bot.drain()                                                          # returns only once both are sent
    flush.join(5)
    assert len(sent) == 3 and "newer" in sent[-1][1] and not bot._edit_sending and not bot._edit_pending


def test_a_long_flood_wait_is_a_warning(db, monkeypatch, caplog):
    from src import telegram_bot
    bot = telegram_bot.TelegramApprovalBot(db=db, bot_token="123:abc", chat_id="555", approver_ids={"555"})
    bot._edit_timer = _Timers()
    with caplog.at_level("INFO", logger="src.telegram_bot"):
        bot._hold_edits(5, 8, 1, ("a", None))
        bot._hold_edits(6, 300, 2, ("b", None))
    levels = [(r.levelname, "message 5" in r.getMessage()) for r in caplog.records if "asked the bot to wait" in r.getMessage()]
    assert levels == [("INFO", True), ("WARNING", False)]


@pytest.mark.parametrize("follow", ["But divers kept looking for more.", "So the find was dated late.",
                                    "There the divers found a second hull.", "Later the gears were X-rayed."])
def test_a_connective_after_a_removed_sentence_is_a_dangling_reference(curated_payload, follow):
    claim = "The wreck sank off the island in 60 BC."
    hook, closing = curated_payload["hook_sentence"], curated_payload["closing_sentence"]
    payload = dict(curated_payload, narration_script=f"{hook} {claim} {follow} {closing}")
    assert without_blocked_sentences(payload, f"1 blocking claim(s): '{claim}' (unsupported)") is None


def test_one_claim_in_the_narration_and_a_graphic_counts_once(curated_payload):
    hook, closing = curated_payload["hook_sentence"], curated_payload["closing_sentence"]
    a, b = "Divers found 8 levels carved into soft volcanic tuff.", "The deepest room sat far below the plaza."
    cards = [{"value": "8 levels", "label": "carved into soft volcanic tuff"}]
    notes = f"2 blocking claim(s): '{a}' (unsupported); '{b}' (unsupported)"
    payload = dict(curated_payload, narration_script=f"{hook} {a} {b} Stone doors sealed it. {closing}", stat_cards=cards)
    out = without_blocked_sentences(payload, notes)              # three places, two claims: within the limit of 2
    assert out is not None and out["stat_cards"] is None
    assert out["trimmed_sentences"] == [a, b, "Number card: 8 levels carved into soft volcanic tuff"]  # each one said
    assert out["trimmed_from"] == ["narration", "narration", "stat cards"]


def test_sentences_are_one_claim_only_in_the_same_words(curated_payload):
    # The review's repro: one sentence holding another's words is still a claim of its own (three, not two).
    p = {"title": "T", "hook_sentence": "Nobody expected this wreck.", "closing_sentence": "The sea keeps its secrets.",
         "narration_script": "Nobody expected this wreck. The ship sank in 60 BC. Divers found coins on the seabed. Romans "
                             "say the ship sank in 60 BC off Crete with gold aboard. Sponge divers saw it first. The crew "
                             "numbered two hundred men. Bronze gears turned inside. The sea keeps its secrets."}
    notes = ('Blocked: "The ship sank in 60 BC." and "Romans say the ship sank in 60 BC off Crete with gold aboard." '
             'and "The crew numbered two hundred men."')
    assert without_blocked_sentences(p, notes) is None
    # a label-like colon in a sentence is not a graphic's label: "One fact remains: ..." is its own claim
    p2 = dict(p, narration_script="Nobody expected this wreck. One fact remains: the ship sank in 60 BC. Divers found "
                                  "coins. The crew numbered two hundred men. Sponge divers saw it first. The sea keeps its secrets.")
    notes2 = 'Blocked: "One fact remains: the ship sank in 60 BC." "The crew numbered two hundred men." "the ship sank in 60 BC"'
    out = without_blocked_sentences(p2, notes2)
    assert out is not None and len(out["trimmed_sentences"]) == 2


def test_an_older_edit_refused_after_a_newer_one_was_shown_is_never_sent(db, monkeypatch):
    bot, timers, sent, clock = _flood_bot(db, monkeypatch, refuse=0)
    assert bot._deliver_edit(1198, 11, ("newer", None))                  # thread A: shown
    bot._hold_edits(1198, 8, 10, ("older", None))                        # thread B: its 429 came back after
    clock[0] = 9
    for t in timers.made:
        t.fire()
    assert len(sent) == 1 and "newer" in sent[0][1] and not bot._edit_pending
    assert bot._deliver_edit(1198, 10, ("older", None)) and len(sent) == 1  # nor directly


def test_drain_waits_out_a_short_chat_hold_before_its_final_sends(db, monkeypatch):
    import time as _time
    bot, timers, sent, clock = _flood_bot(db, monkeypatch)
    assert bot.edit_message_text(1198, "final card")
    clock[0] = bot._edit_chat_held_until - _time.monotonic() - 0.4      # 0.4 s of the hold left
    t0 = _time.perf_counter()
    bot.drain()
    assert _time.perf_counter() - t0 >= 0.3 and len(sent) == 2 and "final card" in sent[-1][1]


def test_after_drain_an_edit_is_never_held_or_timed(db, monkeypatch, caplog):
    bot, timers, sent, clock = _flood_bot(db, monkeypatch, refuse=0)
    bot.drain()
    monkeypatch.setattr(bot, "_api", lambda method, **kw: (_ for _ in ()).throw(
        __import__("src.telegram_bot", fromlist=["x"]).TelegramRateLimited(method, 8)))
    with caplog.at_level("WARNING", logger="src.telegram_bot"):
        assert bot.edit_message_text(1198, "late") is False
    assert not timers.made and not bot._edit_pending and "before shutting down" in caplog.text


def test_a_graphic_never_links_two_sentences_into_one_claim():
    # Review repro: two sentences each holding a card's words ("60 BC the ship sank") are still two claims, so
    # with a third the trim is a rewrite's.
    p = {"title": "T", "hook_sentence": "Nobody expected this wreck.", "closing_sentence": "The sea keeps its secrets.",
         "stat_cards": [{"value": "60 BC", "label": "the ship sank"}, {"value": "3", "label": "divers"}],
         "narration_script": "Nobody expected this wreck. In 60 BC the ship sank. Divers found coins on the seabed. "
                             "Romans say in 60 BC the ship sank off Crete with gold aboard. Sponge divers saw it first. "
                             "The crew numbered two hundred men. Bronze gears turned inside. The sea keeps its secrets."}
    notes = ('Blocked: "In 60 BC the ship sank." and "Romans say in 60 BC the ship sank off Crete with gold aboard." '
             'and "The crew numbered two hundred men."')
    assert without_blocked_sentences(p, notes) is None


def test_drain_has_one_overall_deadline_and_says_what_was_left(db, monkeypatch, caplog):
    import threading
    import time as _time
    bot, timers, sent, clock = _flood_bot(db, monkeypatch, refuse=0)
    bot.DRAIN_SECONDS = 0.5
    release = threading.Event()
    bot._queue_navigation(lambda: release.wait(5))                       # a navigation job that does not finish
    t0 = _time.perf_counter()
    with caplog.at_level("WARNING", logger="src.telegram_bot"):
        bot.drain()
    release.set()
    assert _time.perf_counter() - t0 < 2 and "1 pool(s) still busy" in caplog.text
