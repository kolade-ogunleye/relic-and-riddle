# Telegram: few live cards (design v2 for approval, 29 Oct 2026)

The operator's idea: one reusable **navigator**, one **notifications** card that pages through everything waiting,
and the **progress** card while a run is live, so the chat never fills with cards. v1 of this document was written
against an older picture of Telegram; v2 is built on the current Bot API (10.3, 24 Aug 2026), read in full
(notes and quotes: `C:\tmp\tg_research\brief.md`). "Confirmed" below is quoted from the reference; "to prove" is
something the reference does not state and is tested on the dev bot first (§8, step 0).

## 1. What changes from v1

| v1 said | Current Telegram | v2 |
|---|---|---|
| A text card can't show media | `editMessageMedia` can "replace a text or a rich message with a media"; media → text still can't | A card can gain media; designed so cards never need to lose it |
| Review = text card + separate video message | **Rich Messages** (10.1–10.3) hold a Video block, tables, folding sections and button rows in **one** message, editable in place | Review is one rich card (to prove: video playback inside a rich block on phone and desktop) |
| One pinned notifications card, pings to make up for silent edits | **Topics in private chats** (9.3/9.4): the bot can create threads; `message_thread_id` works on every send method | One topic per purpose, one live card in each (to prove: per-topic unread counts and notifications) |
| "Telegram has no disabled button" workaround | `style`: "success" (green), "danger" (red), "primary" (blue); `disabled` (10.3); `copy_text`; `force_reply` | Coloured, disabled and copy buttons everywhere they help |
| Pinning adds a service line | Pins are silent in private chats, and the service line can be deleted (< 48 h) | Pin and tidy |
| Times written in one zone | `date_time` entities (9.5) show dates in the viewer's format (to prove: local time zone) | "Next run", "expires in", "posted" as live times |

## 2. The chat, in topics

Topics are a per-bot @BotFather switch (`has_topics_enabled`). The bot creates them once (`createForumTopic`, fixed
names and colours) and keeps their ids in the database.

| Topic | Its live card | New messages (these notify) |
|---|---|---|
| 🏛 **Studio** | The navigator: dashboard and every screen, confirmations, replies to commands | None: everything edits the one card |
| 🔔 **Needs you** | The paged notifications card (§3) | One short line per new item ("🎬 Review waiting: Nazca"), deleted when the item is handled |
| 🎬 **Reviews** | One rich review card per waiting review (§4) | The card itself |
| ⚡ **Runs** | The progress card while a run is live, then its one-line outcome | The outcome line |
| 🗞 **Info** | Weekly policy watch, digest, settings and update checks: one card each, replaced by the next | Muted by the operator if wanted |

**Fallback, if topics don't behave as needed in testing** (no per-topic unread count, or clients that don't show
them): v1's layout in one chat — navigator, pinned notifications card, short pings — with everything else in v2.

## 3. Needs you: one paged card

Groups, in order (the first non-empty one shows; "Next group ›" moves on; an empty group moves on by itself):
🎬 reviews waiting · ❌ failures needing a decision · ⌛ timed-out reviews · 🔑 accounts and providers ·
🧭 improvement proposals. Each page: what it is, why it matters, its actions (coloured), `‹ 2 of 5 ›`. Acting
removes the item and shows the next; "✅ Nothing needs you right now." when empty. The dashboard's "Needs you"
lines come from the same query, so the two never disagree.

## 4. Rich cards

- **Review card** (one rich message): Video block (the proxy, with `cover` and `supports_streaming`), the title and
  hook, a compact Table (length, release gate, pictures, rights, costs), Details blocks that fold the script, the
  findings and the repeats; then 🟢 Approve & Post · ✏️ Edit · ♻️ Regenerate · 🔴 Reject. `protect_content` keeps an
  unreleased proxy from being forwarded or saved.
- **Dashboard and analytics** (rich, in Studio): Tables (platform × views/reach/followers/watch time), Unicode bars
  for trends (▁▂▃▅▇), and a Photo or Collage block of rendered charts (§5), all edited in place.
- **Progress card** (rich, in Runs): a List of stages with ✅ / ⏳ / ⬜, the stage's live `date_time` ETA, the slow-step
  note, and a disabled "⏳ Rendering…" or a red "⏹ Cancel run".
- **Confirmations**: a page in the card that asked, "🟢 Yes, do it · ← Keep it", expiring after 5 minutes.
- **Reasons**: `force_reply` opens the reply box under the card for a typed reason.
- **Copy**: run ids and commands as `copy_text` buttons, not text to select.

Limits (confirmed): 32,768 characters, 500 blocks, 50 media, 20 table columns; Buttons blocks of 1–8; a 50 MB video
cap (the proxy is ~5 MB).

## 5. Graphics and charts

Telegram has no drawing primitives in messages or rich blocks (confirmed by absence). Three tiers:
1. **Now:** rich Tables and Unicode bars for numbers and trends.
2. **Now:** charts rendered by us (matplotlib, our palette, dark and light) as PNGs inside the rich card, re-rendered
   and swapped when the numbers change (hourly with the analytics collector).
3. **Later, if wanted:** a **Mini App**: a full-screen web page inside Telegram with interactive charts and a proper
   review screen. It needs a public HTTPS address, and production sits on the lab network, so hosting and a tunnel
   are a lab decision (`docs/deployment-reconciliation.md` ownership), plus `initData` HMAC validation. Not in this
   build.

## 6. Mechanics

- **One owner per card:** card ids, page, items and a revision number live in the database; every edit is
  compare-and-set, so the worker process and the bot cannot overwrite each other. The rc69 429 hold applies.
- **48 hours:** bots delete only messages younger than 48 h (confirmed). Temporary messages are swept well inside
  that; a live card older than ~40 h is replaced so it stays removable. Editing has no stated limit.
- **Media rule:** a card may gain media, never needs to lose it; media cards are edited with
  `editMessageCaption` / `editMessageMedia` (fixes "there is no text in the message to edit", dev restart, 28 Oct).
- **Persistent shortcut:** a `ReplyKeyboardMarkup` with `is_persistent` showing 🏠 Studio · 🔔 Needs you, always one
  tap away.
- **Not used:** drafts (a 30-second preview that disappears), checklists (business accounts only), ephemeral messages
  (groups only, delivery not guaranteed), custom-emoji button icons (need Premium or Fragment).

## 7. Folded in (built once, inside this)

Confirmations (Approve & Post, Reject, Regenerate, long or paid Cancel job); ♻️ Restorable and 📮 Posted queue
screens; dashboard limits with "+N more"; "Last run failed" clearing when handled; per-release counting for
proposals and the self-audit (a proposal whose findings all predate the release closes itself); privacy at photo
selection (prominent living faces left out, never an identifiable child, light blur only on small background faces,
historical subjects never blurred).

## 8. Plan

0. **Prove it on the dev bot (half a day):** topics on the dev bot via @BotFather; a rich review card with a video
   block, a table, folding sections and coloured buttons, edited in place; per-topic unread and notification
   behaviour; a text card edited into a rich one; `date_time` in local time; on your phone and the desktop app.
   Decide topics vs the single-chat fallback from what you see.
1. Card store with compare-and-set; the topic layout behind `TELEGRAM_CARDS_V2` (on for development, off on
   production until you accept it).
2. All 63 `notify()` call sites routed by group (a call with no group fails a test).
3. Rich review, dashboard, analytics (tables, bars, rendered charts) and progress cards; confirmations; the queue
   screens; dashboard limits.
4. Per-release counting; privacy at selection.
5. Tests for every page, group, ping, pin, 48-h replacement, two writers racing, 429 mid-edit, every confirmation and
   every screen registered with one 🏠; then acceptance on development with a full run, a failure, a proposal night
   and a release notice.

Estimate: step 0 half a day; steps 1–5 about a day and a half with review cycles; acceptance half a day.

## 9. Decisions wanted

1. Run step 0 on the dev bot (needs you to enable topics for **@relicandriddle_dev_bot** in @BotFather, and to look at
   it on your phone)?
2. The topic set and order in §2?
3. When the "new item" lines in Needs you are removed: when handled (recommended), or after 30 minutes?
4. The Mini App for analytics: later, as a separate lab decision (recommended), or plan it now?
