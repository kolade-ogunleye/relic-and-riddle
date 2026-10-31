"""The 2026-10-25 overhaul of the operator chat: one home button, ← one level up on every screen, buttons that
show whole, titles in sentence case, subcommands instead of more commands, and older names still answered."""
import pytest

from src import control, telegram_ui
from src.control import make_command_handler
from src.telegram_commands import ALIASES, GROUPS, menu, normalise_command
from src.telegram_ui import HOME, SCREENS, label, long_labels, nav, parent_of, with_nav


def test_every_screen_names_a_parent_that_exists_and_home_is_one_button():
    for key, (emoji, name, parent) in SCREENS.items():
        assert emoji and name
        assert parent is None or parent.partition(":")[0] in SCREENS, key
    assert HOME == {"text": "🏠 Dashboard", "callback_data": "cdash:"}
    assert nav(None) == [[HOME]]
    assert nav("cposts:") == [[{"text": "← Posting", "callback_data": "cposts:"}, HOME]]


def test_with_nav_replaces_a_handlers_own_back_and_home_buttons():
    rows = [[{"text": "x", "callback_data": "postnow:1"}], [{"text": "← Posting", "callback_data": "cposts:"}], [{"text": "← Dashboard", "callback_data": "cdash:"}]]
    assert with_nav(rows, "cposts:") == [[{"text": "x", "callback_data": "postnow:1"}],
                                         [{"text": "← Posting", "callback_data": "cposts:"}, HOME]]


def test_labels_are_cut_at_a_word_never_mid_word():
    assert label("Short") == "Short"
    cut = label("🔁 Retry the failed platform: Carousel slides", 28)
    assert len(cut) <= 28 and cut.endswith("…") and not cut.endswith(" …") and "platfo…" not in cut


@pytest.mark.parametrize("typed, expected", [
    (("queue", "bump 4"), ("bump", "4")), (("bump", "4"), ("bump", "4")), (("drop_topic", "4"), ("drop_topic", "4")),
    (("render", ""), ("run", "")), (("render", "4"), ("qrender", "4")), (("run", ""), ("run", "")),
    (("reviews", "history"), ("history", "")), (("posts", "now 5"), ("postsdo", "now 5")), (("posts", ""), ("posts", "")),
    (("photos", ""), ("images", "")), (("photos", "4"), ("image", "4")), (("photos", "4 review"), ("review", "4")),
    (("photos", "4 keep 2 7"), ("keep", "4 2 7")), (("photos", "upload 4"), ("upload", "4")),
    (("photos", "4 unkeep 2"), ("unkeep", "4 2")), (("ideas", "unesco"), ("discover", "")),
    (("ideas", "research"), ("propose", "")), (("activity", "10"), ("audit", "10")),
    (("music", "find mysterious"), ("music", "fetch mysterious")), (("start", ""), ("dashboard", "")),
])
def test_subcommands_become_the_action(typed, expected):
    assert normalise_command(*typed) == expected


def test_the_menu_is_short_and_retired_names_are_gone_everywhere():
    # Operator, 2026-10-25: a retired command is removed, not listed, mentioned or half-working.
    import pathlib
    import re
    names = [m["command"] for m in menu()]
    assert len(names) == len(set(names)) and len(names) <= 30
    assert not any(" · " in m["description"] for m in menu())            # the menu shows the plain purpose
    assert ALIASES == {"start": "dashboard"} and "swap" not in names      # only Telegram's own /start
    assert {"Everyday", "Topics", "Pictures and media", "Diagnostics"} == set(GROUPS)
    retired = re.compile(r"""[">( ]/(run|next|bump|drop_topic|requeue|undo|history|propose|proposals|discover|images|"""
                         r"""image|fetchimages|review|drop|keep|unkeep|audit|critique|bug|commands|swap)\b""")
    for path in [*pathlib.Path("src").glob("*.py"), pathlib.Path("main.py")]:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            assert not retired.search(code) or '"""' in line or "data/" in code, f"{path}:{n}: {line.strip()}"


def test_the_main_screens_keep_their_buttons_whole_and_end_with_a_way_home(db, tmp_path):
    h = make_command_handler(db, lambda: "Mon 13 Oct 07:00", lambda: "", assets_dir=tmp_path)
    for action, args in (("qrefresh", ""), ("cstat", ""), ("cposts", ""), ("creviews", ""), ("cimages", ""),
                         ("chelp", "Topics"), ("cmusic", ""), ("cideas", "")):
        reply = h(action, args)
        text, buttons = reply if isinstance(reply, tuple) else (reply, [])
        rows = with_nav(buttons, parent_of(action))
        assert long_labels(rows) == [], (action, long_labels(rows))
        assert rows[-1][-1] == HOME, action
        assert "<b><u>" not in text or "RELIC" in text, action                   # one masthead, no shouting titles
    text, buttons = control.dashboard_text_and_buttons(db, "Mon 13 Oct 07:00", assets_dir=tmp_path)
    assert long_labels(buttons) == []
    assert "<b>Production</b>" in text and "<b>This month</b>" in text and "<b>Last 7 days</b>" in text
    flat = [b["text"] for row in buttons for b in row]
    assert any(t.startswith("🎬 Reviews") for t in flat) and "🖼 Pictures" in flat and "📊 Status" in flat
    assert not any(t in ("🎨 Covers", "💡 Topic ideas", "🔄 Check accounts", "🧰 Jobs") for t in flat)   # moved into their hubs


def test_the_status_hub_opens_its_records_and_the_pictures_hub_its_sets(db, tmp_path):
    h = make_command_handler(db, lambda: None, lambda: "", assets_dir=tmp_path)
    _, rows = h("cstat", "")
    callbacks = {b["callback_data"] for row in rows for b in row}
    assert {"canalytics:", "caccounts:", "calerts:", "caudit:", "cimprove:", "cdigest:", "jobs:"} <= callbacks
    text, rows = h("cimages", "")
    assert text.startswith("🖼 <b>Pictures</b>") and {"imagepick:0", "ccov:", "cfootage:"} <= {b["callback_data"] for row in rows for b in row}
    text, _ = h("calerts", "")
    assert isinstance(text, str)          # its own (text, buttons), never a pair wrapped in a pair (2026-10-25)


def test_the_queue_lists_everything_on_request_and_offers_ideas(db, tmp_path):
    h = make_command_handler(db, lambda: None, lambda: "", assets_dir=tmp_path)
    text = h("queue", "all")                                                      # typed: the text (sent to the panel)
    assert "use the page buttons" not in text and "Topic queue" not in text and "<b>Queue</b>" in text
    _, rows = h("qrefresh", "")
    assert any(b["callback_data"] == "cideas:" for row in rows for b in row)
