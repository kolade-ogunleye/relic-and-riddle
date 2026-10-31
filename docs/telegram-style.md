# Telegram style

How every bot card and notification is written, so screens stay consistent. Telegram does not let a
bot choose a font family or size: messages render in each reader's app font. The tools are Telegram's
HTML (bold, italic, underline, strikethrough, spoiler, inline code, code blocks, quotes, expandable
quotes, links) and emoji, so consistency comes from how those are used.

`src/telegram_presentation.format_html` applies the first four rules to every message automatically.

1. **Heading:** the first line, bold, sentence case (an uppercase source heading becomes sentence case;
   underlines and divider lines are removed). One emoji may lead it, outside the bold:
   `🎯 <b>Next:</b> …`, never `<b>🎯 Next:</b>`.
2. **A blank line after the heading**, and never more than one blank line in a row.
3. **Labels:** `emoji <b>Label:</b> value`, sentence case, colon inside the bold.
4. **Copyable text** (a command or template) goes in `<code>` or a code block: Telegram offers Copy,
   and ready-to-run commands also get a 📋 Copy button.
5. **Line breaks over long sentences:** a card that explains several things uses a short lead line
   and `•` bullets, not one paragraph.
6. **Long content** (narration, a description, evidence) goes in `<blockquote expandable>`, so the card
   stays short until opened (`control.collapsible` does this past 280 characters).
7. **Never cut mid-word:** shorten with `control.clip_words`, which ends at a word with "…".
8. **One name per action, everywhere:** the button that starts the next topic is "⚡ Render #N now" on
   the dashboard and the queue alike.
9. **One panel:** a screen replaces the one it came from; a decided review leaves the chat and its
   receipt becomes the dashboard's "Last review" line; every screen keeps "← Dashboard" and, for a
   job, a way back to the screen that started it.
10. **Say what happens, as it is:** a card never promises an effect the code does not perform (see the
    downstream-effects rule in `docs/reviews/2026-10-23-downstream-effects-audit.md`).
