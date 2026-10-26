"""One presentation style for HTML panels, media captions and notifications.

Use a short bold sentence-case heading, a blank line, and concise body text.
Quoted evidence/code is preserved verbatim; formatting never changes buttons.
"""
import html
import re

_PROTECTED = re.compile(r"<(?P<tag>pre|code|blockquote)\b[^>]*>.*?</(?P=tag)>", re.S | re.I)
_RULE = re.compile(r"^\s*[━─═]{3,}\s*$", re.M)


def _balanced(fragment):
    """Whether every tag opened in `fragment` is closed in it, in order."""
    stack = []
    for closing, name in re.findall(r"<(/?)([a-zA-Z0-9-]+)[^>]*>", fragment):
        if not closing:
            stack.append(name.lower())
        elif not stack or stack.pop() != name.lower():
            return False
    return not stack


def format_html(text):
    def normalize(part):
        part = re.sub(r"</?u>", "", part)
        part = _RULE.sub("", part)
        return re.sub(r"\n{3,}", "\n\n", part)
    parts, cursor = [], 0
    for match in _PROTECTED.finditer(str(text)):
        parts.extend([normalize(str(text)[cursor:match.start()]), match[0]])
        cursor = match.end()
    parts.append(normalize(str(text)[cursor:]))
    text = "".join(parts).strip()
    if re.match(r"^<(?:pre|code|blockquote)\b", text, re.I):
        return text
    heading, separator, body = text.partition("\n")
    def sentence_case(match):
        title = match[1]
        plain = html.unescape(title)
        if plain == "RELIC & RIDDLE":
            return "<b>Relic &amp; Riddle</b>"
        if plain.isupper() and "<" not in title:
            words = title.lower().split(" ")
            acronyms = {"ai", "api", "qa", "ig", "fb", "utc", "nvenc", "gpu"}
            words = [word.upper() if word in acronyms else word for word in words]
            title = " ".join(words)
            title = title[:1].upper() + title[1:]
        return "<b>" + title + "</b>"
    # Preserve exact source code/quotes, even when they occupy the first line.
    if not re.search(r"<(?:pre|code|blockquote)\b", heading, re.I):
        heading = re.sub(r"<b>(.*?)</b>", sentence_case, heading)
        # Bolded only when its own tags close within it: "<i>a" with "</i>" on a later line crossed the tags
        if "<b>" not in heading and _balanced(heading) and len(re.sub(r"<[^>]+>", "", heading)) <= 160:
            heading = "<b>" + heading + "</b>"
        # A leading emoji stays outside the bold, as on every other line ("<b>ℹ️ Nothing to undo.</b>"
        # read differently from "🎯 <b>Next:</b>": 2026-10-23 UI review).
        heading = re.sub(r"^<b>([^A-Za-z0-9<&\s\"'(]+)\s+", r"\1 <b>", heading)
    return heading + ("\n\n" + body.lstrip("\n") if separator and body.strip() else "")


_COMMAND_CODE = re.compile(r"<code>(/[a-z_]+(?: [^<]*)?)</code>", re.I)


def copy_buttons(text, markup):
    """A 📋 Copy button (Telegram copy_text: one tap puts it on the clipboard) for each ready-to-use
    command shown in a code box, e.g. <code>/add The Lost Labyrinth</code>. Templates with
    placeholders (/bump &lt;number&gt;) are left to the code box: copying a placeholder helps no one."""
    import json
    commands = []
    for match in _COMMAND_CODE.finditer(str(text or "")):
        command = html.unescape(match[1]).strip()
        if " " in command and "<" not in command and ">" not in command and command not in commands and len(command) <= 256:
            commands.append(command)
    if not commands:
        return markup
    value = json.loads(markup) if isinstance(markup, str) else dict(markup or {"inline_keyboard": []})
    rows = value.get("inline_keyboard", [])
    if any("copy_text" in button for row in rows for button in row):
        return markup  # already offered (an edit of the same screen)
    labels = [f"📎 Copy {c}" if len(c) <= 30 else f"📎 Copy {c[:28]}…" for c in commands[:3]]  # 📋 is the Queue
    value["inline_keyboard"] = [[{"text": label, "copy_text": {"text": command}}] for label, command in zip(labels, commands)] + rows
    return json.dumps(value)


def format_payload(method, payload):
    """Copy only presentation fields; retain original evidence/action metadata."""
    import json
    from .telegram_bot import safe_html_truncate, _MASKER
    result = dict(payload)
    if result.get("parse_mode") == "HTML":
        for key in ("text", "caption"):
            if key in result:
                result[key] = safe_html_truncate(format_html(_MASKER.mask(result[key])), 1024 if key == "caption" else 4000)
        if method in {"sendMessage", "editMessageText"}:
            result_markup = copy_buttons(result.get("text"), result.get("reply_markup"))
            if result_markup:
                result["reply_markup"] = result_markup
    if method in {"editMessageMedia", "sendMediaGroup"} and isinstance(result.get("media"), str):
        value = json.loads(result["media"])
        for media in value if isinstance(value, list) else [value]:
            if media.get("parse_mode") == "HTML" and "caption" in media:
                media["caption"] = safe_html_truncate(format_html(_MASKER.mask(media["caption"])), 1024)
        result["media"] = json.dumps(value)
    return result
