import json

from src import control
from src.telegram_bot import TelegramApprovalBot
from src.telegram_presentation import format_html, format_payload


def test_one_style_preserves_evidence_and_is_idempotent():
    quote = '<blockquote expandable>EXACT <u>evidence</u>\n\n\n━ ━\n<code>ID</code></blockquote>'
    original = '📋 <b><u>TOPIC QUEUE</u></b>\nDetails\n━━━━━━━━━━\n\n' + quote
    rendered = format_html(original)
    assert rendered.startswith('📋 <b>Topic queue</b>\n\nDetails')
    assert quote in rendered and '━━━━━━━━━━' not in rendered
    assert format_html(rendered) == rendered
    assert format_html('<pre><code>EXACT\n\n\n  spacing</code></pre>') == '<pre><code>EXACT\n\n\n  spacing</code></pre>'


def test_text_photo_and_audio_share_style_without_touching_actions():
    original = {'parse_mode': 'HTML', 'caption': '🎵 <b><u>MUSIC REVIEW</u></b>\nListen first', 'reply_markup': 'unchanged'}
    formatted = format_payload('sendAudio', original)
    assert formatted['caption'] == '🎵 <b>Music review</b>\n\nListen first'
    assert formatted['reply_markup'] == original['reply_markup'] and '<u>' in original['caption']
    edited = format_payload('editMessageMedia', {'media': json.dumps(dict(original, type='photo', media='file-id'))})
    assert json.loads(edited['media'])['caption'] == formatted['caption']
    assert format_payload('answerCallbackQuery', {'text': 'raw'}) == {'text': 'raw'}
    assert len(format_payload('sendPhoto', dict(original, caption='x' * 1024))['caption']) <= 1024


def test_clear_then_start_leaves_one_clean_dashboard_even_after_restart(db, monkeypatch):
    calls = []
    def api(method, **kwargs):
        calls.append((method, kwargs.get('data', {})))
        if method == 'getUpdates':
            return [{'update_id': 1, 'message': {'message_id': 600, 'text': '/clear', 'chat': {'id': 555}, 'from': {'id': 555}}}]
        return {'message_id': 900}
    bot = TelegramApprovalBot(db=db, bot_token='123:abc', chat_id='555', approver_ids={'555'})
    monkeypatch.setattr(bot, '_api', api)
    handler = control.make_command_handler(db, lambda: None, lambda: '', send_message=bot.notify)
    bot.poll_once(handler)
    bot.drain()
    sends = [d for method, d in calls if method == 'sendMessage']
    assert len(sends) == 1 and 'CHAT CLEARED' not in sends[0]['text']
    assert 'RELIC' in sends[0]['text']
    # After a restart, Telegram's /start still leaves exactly one panel: a new one at the bottom (the old
    # one may be gone from the operator's view after "Clear history"), with the persisted one removed.
    fresh = TelegramApprovalBot(db=db, bot_token='123:abc', chat_id='555', approver_ids={'555'})
    def next_api(method, **kwargs):
        calls.append((method, kwargs.get('data', {})))
        if method == 'getUpdates':
            return [{'update_id': 2, 'message': {'message_id': 901, 'text': '/start', 'chat': {'id': 555}, 'from': {'id': 555}}}]
        return {'message_id': 902}
    monkeypatch.setattr(fresh, '_api', next_api)
    fresh.poll_once(handler)
    fresh.drain()
    assert len([1 for method, _ in calls if method == 'sendMessage']) == 2
    assert any(method == 'deleteMessage' and data.get('message_id') == 900 for method, data in calls)
    assert db.get_setting('telegram_dashboard_id') == '902'


def test_ready_commands_get_a_copy_button_and_placeholders_do_not():
    import json
    from src.telegram_presentation import format_payload
    text = ('<b>Add topic</b>\n\n<code>/add The Lost Labyrinth of Hawara</code>\n<code>/add &lt;topic title&gt;</code>')
    markup = json.dumps({"inline_keyboard": [[{"text": "← Dashboard", "callback_data": "cdash:"}]]})
    rows = json.loads(format_payload("sendMessage", {"parse_mode": "HTML", "text": text, "reply_markup": markup})["reply_markup"])["inline_keyboard"]
    assert rows[0] == [{"text": "📋 Copy /add The Lost Labyrinth of H…", "copy_text": {"text": "/add The Lost Labyrinth of Hawara"}}]
    assert len(rows) == 2 and rows[1][0]["callback_data"] == "cdash:"      # the placeholder template gets none
    again = format_payload("editMessageText", {"parse_mode": "HTML", "text": text, "reply_markup": json.dumps({"inline_keyboard": rows})})
    assert len(json.loads(again["reply_markup"])["inline_keyboard"]) == 2  # never doubled on an edit


def test_two_dashboard_refreshes_at_once_leave_one_panel(db, monkeypatch):
    # Production 2026-10-23: at startup the dashboard card and a queued "Dashboard" tap both found the stored
    # panel gone, and each posted a new one.
    import threading
    import time
    db.set_setting('telegram_dashboard_id', '768')
    sent, lock = [], threading.Lock()

    def api(method, **kwargs):
        data = kwargs.get('data', {})
        if method == 'editMessageText':
            time.sleep(0.05)  # both callers are in flight together
            if str(data.get('message_id')) == '768':
                raise RuntimeError('Telegram editMessageText failed: HTTP 400 Bad Request: message to edit not found')
            return {'message_id': int(data['message_id'])}
        if method == 'sendMessage':
            with lock:
                sent.append(900 + len(sent))
                return {'message_id': sent[-1]}
        return True
    bot = TelegramApprovalBot(db=db, bot_token='123:abc', chat_id='555', approver_ids={'555'})
    monkeypatch.setattr(bot, '_api', api)
    threads = [threading.Thread(target=bot.show_dashboard, args=('🏛 Panel', [[{'text': 'x', 'callback_data': 'cdash:'}]]))
               for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sent == [900] and db.get_setting('telegram_dashboard_id') == '900'
