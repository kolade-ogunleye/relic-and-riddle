from datetime import datetime, timezone

from src import policy_watch as pw
from src.models import PolicyChange, PolicyDigest, Source


def _change(url, summary="Reels over 90s now eligible", action=False):
    return PolicyChange(date="2026-10-01", area="Content monetization", summary=summary,
                        impact="More reach possible.", action_needed=action, action="Do X" if action else "",
                        source_url=url)


def _wire(monkeypatch, changes, retrieved=("https://about.fb.com/news/x",)):
    monkeypatch.setattr(pw, "claude_available", lambda: True)
    monkeypatch.setattr(pw, "research", lambda *a, **k: ("notes", [Source(title="t", url=u) for u in retrieved]))
    monkeypatch.setattr(pw, "structured_call", lambda *a, **k: PolicyDigest(changes=changes))


def test_only_sourced_new_changes_are_reported_once(db, monkeypatch):
    good, fake = _change("https://about.fb.com/news/x"), _change("https://made-up.example/y", "Fake change")
    _wire(monkeypatch, [good, fake])
    first = pw.check(db, datetime(2026, 10, 12, tzinfo=timezone.utc))
    assert [c.source_url for c in first] == ["https://about.fb.com/news/x"]
    assert pw.check(db, datetime(2026, 10, 19, tzinfo=timezone.utc)) == []  # already reported
    assert db.get_setting(pw.LAST_CHECK_KEY).startswith("2026-10-19")


def test_digest_text_puts_actions_first_and_escapes():
    text = pw.digest_text([_change("https://a", "<b>minor</b>"), _change("https://b", "Big one", action=True)])
    assert text.index("ACTION") < text.index("minor") and "<b>minor</b>" not in text
    assert "no new Meta policy changes" in pw.digest_text([])
