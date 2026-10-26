"""Persistent pages for long text panels; evidence is never silently truncated."""
import hashlib
import html
from html.parser import HTMLParser

from .telegram_state import get, put


class Plain(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag == "br":
            self.parts.append("\n")


def panel(db, text, buttons):
    if len(text.encode("utf-16-le")) // 2 <= 3700:
        return text, buttons
    parser = Plain()
    parser.feed(text)
    plain = "".join(parser.parts)
    pages, current, cost = [], [], 0
    for char in plain:
        escaped = html.escape(char)
        size = max(len(escaped), len(char.encode("utf-16-le")) // 2)
        if cost + size > 3100:
            pages.append("".join(current))
            current, cost = [], 0
        current.append(escaped)
        cost += size
    if current:
        pages.append("".join(current))
    key = hashlib.sha256((text + repr(buttons)).encode()).hexdigest()[:32]
    put(db, "pages", key, {"pages": pages, "buttons": buttons})
    return render(db, key, 0)


def render(db, key, number):
    value = get(db, "pages", key)
    if not value or not 0 <= number < len(value["pages"]):
        from .telegram_ui import HOME
        return "This page has expired. Reopen the command.", [[HOME]]
    pages = value["pages"]
    from .control import pager
    from .telegram_bot import format_responsive_keyboard
    return (f"<b>Page {number+1} of {len(pages)}</b>\n\n" + pages[number],
            pager(f"page:{key}:", number, len(pages)) + format_responsive_keyboard(value.get("buttons")))
