# The operator chat, overhauled (2026-10-25)

The operator asked for one reusable panel wherever feasible, a seamless way back to it where not, only the
buttons each screen needs, subcommands instead of more commands, no truncated labels, one level up and
down at a time, and consistent words, emoji and formatting. This records what was found and what changed.
`screens.md` beside this file is the main screens as rendered from development data on the day.

## What the inventory found

- 42 public commands in 4 groups and about 40 handler-only actions; five commands (`/images`, `/image`,
  `/review`, `/drop`, `/keep`, plus `/swap`) reached the same photo flow; `/drop` removed photos while
  `/drop_topic` removed topics; `/proposals` meant topic ideas while the dashboard's "Proposals" meant
  improvements; `/queue all` did nothing.
- Back buttons that skipped a level (the topic picker, Cancel run, the covers photo), and four cards with
  no way home at all (the review card, the progress card, an improvement proposal, the reason fallback).
- Eleven button labels over 28 characters, which Telegram cuts; seven lines packing four or more facts.
- Titles in capitals and underline on nine cards; the same emoji for different things (📋 for Queue and
  Copy, 🔄 for Refresh, Regenerate, Swap and Check accounts) and different emoji for the same thing.

## The conventions now (`src/telegram_ui.py`)

- **One home button**, `🏠 Dashboard`, on every screen and card. `←` always means one level up and names
  the parent: `← Posting`, `← Pictures`. Every screen names its parent in one table (`SCREENS`); the bot
  applies the navigation row itself, so no handler can forget it.
- **A title is one emoji and one name**, in sentence case; a summary goes on the line under it, never
  packed into the title. The dashboard keeps its masthead.
- **A fact a line.** The dashboard reads Needs you, Production, This month, Last 7 days as headed blocks;
  reasons and errors sit in expandable blockquotes.
- **Buttons show whole**: labels are cut at a word with an ellipsis (`telegram_ui.label`), and the run a
  button acts on is named in the text above it, so the button is the verb (`🔁 Make again`, `🔁 Retry Reel`).
- **Hubs, not a wall of buttons.** The dashboard offers the day's work: Render or Progress, Queue, Reviews,
  Posting, Pictures, Music, Status, Help, Pause. Ideas live under Queue; Covers, uploads and footage under
  Pictures; Audience, Accounts, Alerts, Activity, Improvements, Digest and Jobs under Status.
- **Commands with subcommands** (`src/telegram_commands.py`): `/queue bump 4`, `/photos 4 review`,
  `/posts now 5`, `/ideas unesco`, `/music find`, `/render 4`. The "/" menu is 29 commands in four groups;
  Help shows each group with its subcommands. Every older name is an alias (`/run`, `/images`, `/bump`,
  `/drop_topic`, `/history`, `/audit`...), so nothing typed before is refused; `/swap` is retired and says so.
- **One emoji per concept**: 📋 Queue, 📎 Copy, 🔄 Refresh, ♻️ Regenerate, 🔗 Accounts, ⏹ stop a run,
  ✖ cancel a choice, 🏠 home.
- **Minimal motion, native only**: Telegram's "typing…" while a screen is built, "sending a video…" while a
  review album goes, and the progress card's hourglass turning on each of its minute redraws.
- **Speed**: the dashboard's photo-readiness count is cached per set until its credits file changes, so a
  tap no longer parses every set.

## Verified by tests

`tests/test_telegram_ux.py`: every screen's parent exists; a handler's own back and home buttons are
replaced by the standard row; labels never cut mid-word; the subcommand table; the menu has no duplicates
and keeps every older name; the main screens keep their buttons under 28 characters and end with a way
home; the Status and Pictures hubs open their records; `/queue all` lists everything.
