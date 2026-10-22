"""One presentation style for HTML panels, media captions and notifications.

Use a short bold sentence-case heading, a blank line, and concise body text.
Quoted evidence/code is preserved verbatim; formatting never changes buttons.
"""
import html
import re

_PROTECTED = re.compile(r"<(?P<tag>pre|code|blockquote)\b[^>]*>.*?</(?P=tag)>", re.S | re.I)
_RULE = re.compile(r"^\s*[━─═]{3,}\s*$", re.M)


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
        if "<b>" not in heading and len(re.sub(r"<[^>]+>", "", heading)) <= 160:
            heading = "<b>" + heading + "</b>"
    return heading + ("\n\n" + body.lstrip("\n") if separator and body.strip() else "")


def format_payload(method, payload):
    """Copy only presentation fields; retain original evidence/action metadata."""
    import json
    from .telegram_bot import safe_html_truncate
    result = dict(payload)
    if result.get("parse_mode") == "HTML":
        for key in ("text", "caption"):
            if key in result:
                result[key] = safe_html_truncate(format_html(result[key]), 1024 if key == "caption" else 4000)
    if method == "editMessageMedia" and isinstance(result.get("media"), str):
        media = json.loads(result["media"])
        if media.get("parse_mode") == "HTML" and "caption" in media:
            media["caption"] = safe_html_truncate(format_html(media["caption"]), 1024)
        result["media"] = json.dumps(media)
    return result
