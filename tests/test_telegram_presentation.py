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
    # A fresh process must reuse the persisted panel, including Telegram's /start.
    fresh = TelegramApprovalBot(db=db, bot_token='123:abc', chat_id='555', approver_ids={'555'})
    def next_api(method, **kwargs):
        calls.append((method, kwargs.get('data', {})))
        if method == 'getUpdates':
            return [{'update_id': 2, 'message': {'message_id': 901, 'text': '/start', 'chat': {'id': 555}, 'from': {'id': 555}}}]
        return {'message_id': 902}
    monkeypatch.setattr(fresh, '_api', next_api)
    fresh.poll_once(handler)
    fresh.drain()
    assert len([1 for method, _ in calls if method == 'sendMessage']) == 1
    assert any(method == 'editMessageText' and data['message_id'] == 900 for method, data in calls)
