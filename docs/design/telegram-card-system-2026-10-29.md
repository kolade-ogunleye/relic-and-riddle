# Telegram card system (design, 29 Oct 2026)

The visual and interaction system for every card the Relic & Riddle studio bot sends, built on design v2
(`telegram-two-card-2026-10-29.md`: topics 🏛️ Studio · 🔔 Needs you · 🎬 Reviews · ⚡ Runs · 🗞️ Info, rich cards,
one owner per card) and on Bot API 10.3 (`C:\tmp\tg_research\brief.md`). The design targets the operator's iPhone
first (topics as top tabs) and the macOS desktop app second (topics in a side bar). Each rule here is a decision.
Where the reference or the prototype has not settled something, the rule says **round 0** and names the test that
settles it (§9).

What the prototype (`C:\tmp\tg_proto\proto.py`) established on device, and what this system takes from it:

| Seen on the operator's iPhone and Mac | Consequence here |
|---|---|
| Rich review card renders well: serif SectionHeading, italic hook, bordered table, Details folds, coloured buttons | Rich messages are the default card format (§2, §7) |
| Video plays inside the rich card; iPhone poster is a dark placeholder (cover ignored), Mac shows a frame | Round 0 poster test, three variants (§9.2); the title sits **under** the video, so a dark poster never leaves the card unnamed |
| `date_time` renders in local time and format; "in 5 hours · Thursday October 29, 2026 at 12:31 PM" | Every time is an entity. The long `wD…` form is banned on card faces because it is 37 characters (§2.4) |
| A 5-column table wrapped mid-word on iPhone ("Instagra/m", "Faceboo/k", "Follower/s") | A width budget for every table, ≤ 3 columns by default, 4 at most (§3.3) |
| Unicode trend bars ▁▂▃▅▇ rendered as blocky white steps | Banned. Trends are drawn in rendered charts and described in words (§3.4, §5.2) |
| The matplotlib PNG looked sharp | Charts are rendered images on a brand plate (§3.4, §5.2) |
| Pin shows an icon, no service line | Pinning is free. Each topic pins its one live card (§8) |
| Per-topic notifications work; Info arrives silently; Mac side bar shows unread badges per topic | The noise model is per topic (§1.4, §8) |
| Disabled "⏳ Rendering…" greys out; the copy button shows a copy glyph | Disabled buttons carry state, and copy buttons carry no emoji of their own (§6) |

## 0. How to read the mocks

Each mock lists its blocks in order, one per line, with exact sample text.

| Code | Block / entity | Code | Block / entity |
|---|---|---|---|
| `VID` | Video block | `T[b s c]` | Table: **b**ordered, **s**triped, **c**ompact; `│` separates cells, `‖h` marks a header row |
| `IMG` | Photo block (size, caption) | `D▸` / `D▾` | Details, closed / open (`is_open`); its children are indented |
| `SLD` / `COL` | Slideshow / Collage | `L` / `OL` / `TL` | List / ordered list / task list (☑ ☐) |
| `H1` `H2` `H3` | SectionHeading size 1 / 2 / 3 | `PQ` | PullQuotation (`— credit`) |
| `P` | Paragraph | `XBQ` | ExpandableBlockQuotation |
| `HR` | Divider | `F` | Footer |
| `BTN` | Buttons block (one row) | `KB` | Inline keyboard (`reply_markup`) |

Inline: `**bold**`, `_italic_`, `` `code` ``, `==marked==`, `~~struck~~`. A `date_time` entity is written
`⟨fmt: expected render⟩`, e.g. `⟨r: in 6 hours⟩`, `⟨wt: Friday 6:00 PM⟩`. Its fallback text is the same moment
formatted by `local_time.show` in the operator's zone.
Button styles: `(S)` success · `(D)` danger · `(P)` primary · `(L)` link · `(·)` default · `(✕)` disabled ·
`(⧉)` copy_text · `(↗)` url.
`[rich]` and `[plain]` say how a card is sent: `sendRichMessage`, or `sendMessage` with HTML.

---

## 1. Principles

1. **One card, one job, one primary action.** Every card answers one question ("is this episode good enough to
   post?", "how is the run going?") and offers at most one success or primary action as its main move. Anything
   else is secondary (default style) or folded away.
2. **Verdict first.** The first line after the title tells you the state: `✅ Passed`, `⛔ Blocked by the release
   gate`, `⏳ Step 3 of 5`. The details come after it, never before it.
3. **Progressive disclosure through Details.** A card face shows what a decision needs. Everything you might want
   (the script, captions, findings, sources, picture credits, logs) sits in closed Details, ordered by how often
   it is opened. A Details opens by default (`is_open`) only when it holds the reason a decision is blocked.
4. **Calm vs urgent is decided by topic, not by volume. Only 🔔 Needs you rings.**
   - Urgent means a new one-line message in 🔔 Needs you (§7.3). It is the **only** kind of message that
     notifies, so one event is one ping, and the operator can leave every other topic on its default without
     being woken twice for the same review.
   - Everything else is sent with `disable_notification`: the review card, run outcome lines, Info cards and the
     reason prompt. Edits are silent anyway. Topics still show unread badges (Mac side bar, iPhone tabs), so
     silent is not invisible.
   - Nothing becomes urgent through capitals, exclamation marks, red emoji stacks or effects. Telegram's
     notification is the alarm, and the card itself stays composed.
5. **Phone-first widths.** Every element is budgeted for an iPhone bubble (§3.1). The Mac gets the same card, and
   it must not look sparse there. The Mac never gets layouts of its own.
6. **Never colour alone.**
   - Every coloured button has a verb label, and every status emoji is followed by a word (`✅ Passed`, not `✅`).
   - Every chart has direct labels, distinct marker shapes, and a caption that states the trend in words.
   - Up and down are shown with a sign (`+12%`, `−8%`) and words, never green and red.
7. **Emoji are icons, not decoration.**
   - One emoji has one meaning across the whole bot (§4).
   - A line has at most one emoji, at its start. Titles of content cards carry none, because the topic already
     says what they are.
8. **No stale screens.** Live cards update themselves. There is no 🔄 Refresh button anywhere. A screen that could
   go stale says when it was last updated (`F Updated ⟨r⟩`), and if it is stale anyway, that is a defect to fix,
   not a button to add.
9. **Feedback is a toast, not a message.** An acknowledgement ("Bumped Derinkuyu to #1", "That card is out of
   date") goes in `answerCallbackQuery` (≤ 200 chars). A message is sent only when there is something to keep.
10. **Honest copy.** Present tense, second person, plain words. There are no exclamation marks, no "successfully",
    no "Oops", and no promises the pipeline does not keep. A dry run says it can still cost money. Production
    says approval decides posting.

---

## 2. Typography and formatting

### 2.1 Fonts

Fonts are the client's. Bots cannot choose a typeface, size or colour of text.
- iOS: SF Pro for text, and an observed serif for rich SectionHeadings.
- macOS: SF for text, and the same serif headings.

The design works with what the client gives:
- **Serif headings are the brand voice.** They read like a museum label, which suits Relic & Riddle (Cinzel in
  the videos). Headings stay short so the serif is shown at its best: card titles ≤ 48 characters (two lines at
  H2 on iPhone), section headings ≤ 24.
- **No fake fonts.** Unicode "bold" 𝐁𝐨𝐥𝐝, small caps ᴀʙᴄ, full-width letters and similar look broken on Mac and
  VoiceOver reads them as noise. They are banned.
- **Monospace means "a value you might copy or type".** It is used for run ids, commands (`/produce 3`), file
  names, and nothing else. Numbers are aligned by tables, never by monospace.
- **Text size is the operator's.** Every card is checked at the default iOS text size and one step larger
  (Telegram › Settings › Appearance › Text Size).

### 2.2 Role → block or entity

| Role | Use | Rules |
|---|---|---|
| Masthead | `H1` | Studio dashboard only: "Relic & Riddle". Title case, never caps (caps in a serif at H1 shouts on a phone) |
| Card title | `H2` | One per card, the object's name: "Nazca Lines: The Runway Paradox", "Queue", "Audience". A navigator screen title starts with its screen emoji. A content card (review, progress) has none |
| Section heading | `H3` | ≤ 3 per card face, sentence case, ≤ 24 characters, may end in a count: "After it · 13" |
| Lede / state line | `P` | The verdict line under the title: state emoji, **bold verdict word**, then at most two `·` facts. No second emoji anywhere on the line, and ≤ 40 characters with the entity rendered, so it stays on one line |
| Hook | `PQ` with credit "Hook, first 3 seconds" | The line viewers hear first, centred. Round 0 compares it with an italic `P` (the proven render). If PQ renders oversized on iPhone, the italic `P` is used |
| Body | `P` | ≤ 2 sentences and ≤ 160 characters on a card face. Longer text goes into Details |
| Label–value facts | `T[b c]`, 2 columns | Labels plain, values plain. A verdict value leads with its state emoji. No bold inside tables except a total row |
| Peer items (runs, topics, posts) | `L` / `OL` | "Name — detail". One emoji at the start only when it is a state or a kind |
| Checkpoints (snapshots, stages that are milestones) | `TL` | ☑ done · ☐ to come, each with its `date_time`. Not tickable, display only |
| Meta (updated, release, run id, mode) | `F` | One footer per card, last block, ≤ 3 `·` facts |
| Footnote / caveat | `P` with `_italic_` | One per card, under what it qualifies: "_Patterns read from 10 videos; 4 so far._" |
| Numbers | Table cells, right-aligned | Formats in §2.4 |
| Ids, commands | `` `code` `` | And a `(⧉)` copy button whenever the operator would copy it |
| Warning | `P`: `⚠️ **Short of photos** · 4 of 6 usable` | The word carries it. Never italic, never all caps |
| The one blocking reason | `==marked==` inside an open `D▾` | One `marked` run per card at most, and only on the thing that blocks a decision |
| Quoted text (an error, operator feedback, a caption) | `XBQ` | Long or third-party text. It is closed by default and opens to read |
| Log excerpt | `pre` inside a Details | Error cards only, ≤ 12 lines |
| Superseded value | `~~struck~~` → new value | Edited states only: "Slot ~~Friday 6:00 PM~~ → Saturday 12:00 PM" |
| Links | Labelled inline URL, "Instagram post ↗" | Never a raw URL on a card face. Sources show the domain as the label |

### 2.3 Emphasis, case and punctuation

- **Bold:** at most one bold run per paragraph. Use it for the verdict word ("**Approved**") or the subject of a
  sentence ("**Derinkuyu** is next"). Bold is never used for labels, and never for a whole sentence.
- **Italic:** for the hook (in fallback), a live detail ("_Making the video: beat 6 of 10_"), and footnotes. Never for
  warnings.
- **Never used:** underline (it reads as a link), spoiler, superscript/subscript, maths, message effects, Unicode
  box drawing, ASCII art.
- **Sentence case** for headings, buttons, labels and table headers. Proper names and episode titles are written
  as the script has them. ALL CAPS is never used.
- **Separators:**
  - ` · ` (middle dot with spaces) separates facts on one line, at most two per line. This tightens
    `too_packed`'s three for rich cards.
  - ` — ` (em dash with spaces) separates an item from its detail in lists.
  - `–` (en dash) is used for ranges: "20–26 Oct", "1–14".
- **Typographic characters:** curly quotes “ ” ‘ ’; ellipsis `…` (one character); `×` for dimensions and
  multiples; a true minus `−` (U+2212) in changes; `›` for breadcrumbs and forward motion.
- **Non-breaking spaces (U+00A0)** go between a number and its unit (`52 s`, `6.1 h`, `$18.40 of $60`) and after
  `#` in `#1`, so a unit never wraps onto a line by itself.

### 2.4 Numbers, dates and durations

| Kind | Format | Examples |
|---|---|---|
| Counts under 10,000 | Exact, comma grouping | `612`, `1,284` |
| Counts of 10,000 and over | Compact, one decimal, `.0` dropped | `10.2k`, `1.3M`, `12k` |
| Percent | Integer; one decimal under 10 | `62%`, `4.5%` |
| Change | Sign and magnitude, true minus; "flat" within ±2% | `+12%`, `−8%`, `flat` (captions use words: "up 12%"). Not ▲ ▼: VoiceOver reads them as "black up-pointing triangle", while "+12%" is read "plus 12 percent", is shorter, and needs no colour |
| Money | `$` with two decimals under $100, whole dollars from $100 | `$0.41`, `$18.40`, `$140` |
| Durations | Units, never clocks | `52 s`, `4 min`, `1 h 12 min`, `3 d` |
| Live counters | Whole minutes + "so far" | `3 min so far` (progress card only). Never `m:ss`: the card edits every 60 s, so seconds would sit frozen and look stale |
| Plurals | Always agree | `1 video`, `3 videos`, `0 photos` |

**`date_time` everywhere a time appears.** Every moment on every card is a `<tg-time unix=… format=…>` entity with
an operator-zone fallback. The format is chosen by distance, so a time never needs more than about 17 characters:

| When | Format | Renders like |
|---|---|---|
| Countdowns, recency ("next run", "updated", "expires", "made") within 12 h ahead or 24 h behind | `r` | "in 6 hours", "3 minutes ago" |
| Today, an exact moment that matters (a slot, a start) | `t` | "6:00 PM" |
| Within the next or last 6 days | `wt` | "Friday 6:00 PM" |
| Further away | `dt` | "11/14/26, 6:00 PM" (client locale) |
| Dated headers on Info cards (week of a digest) | `D` | "October 26, 2026" |
| Never | `wD…`, `T` | Too long for a phone line; seconds are noise |

One entity per moment. The only place a relative and an absolute time appear together is the progress ETA:
"review ⟨r: in 12 minutes⟩ (⟨t: 2:52 PM⟩)". Button labels may hold an entity too ("Post at ⟨t⟩"), but only if it
fits in 20 characters.

### 2.5 Line and length budgets (iPhone, default text size)

| Element | Budget |
|---|---|
| Paragraph line | ~34 characters a line (calibrated in round 0, §9.2); paragraph ≤ 160 characters on a card face |
| H2 title | ≤ 48 characters (two lines) |
| H3 | ≤ 24 characters (one line) |
| Notification line (🔔, outcome) | ≤ 40 characters, so the lock-screen preview shows it whole |
| Button | ≤ 20 characters full-width, ≤ 16 two-up, ≤ 8 three-up (registry hard cap stays `BUTTON_LIMIT = 28`) |
| Details summary | ≤ 28 characters, "Name · count": "Script · 142 words" |
| Table token | ≤ 14 characters per unbreakable word in a cell |
| Footer | ≤ 3 facts |

### 2.6 What not to use

- Unicode bar charts (`▁▂▃▅▇`, `▰▱`, `█░`): ugly on iPhone (observed). The progress card drops its `[▰▰▰▱▱] 58%`
  bar. The stage list is the progress (§7.5).
- Traffic-light circles 🟢🟡🟠🔴🔵 as states, as the current queue uses them. Use the state set in §4.2, with words.
- Emoji used as bullets in lists. VoiceOver reads every one of them.
- Colour words standing in for state ("the red one").
- Separate media messages next to a card (the old albums). Media goes inside the rich card.
- `message_effect_id`, `sendMessageDraft` for progress, checklists, ephemeral messages (brief §E, I, J).
- Raw URLs, raw ISO timestamps, time-zone abbreviations on card faces.

---

## 3. Spacing and rhythm

Telegram sets the spacing between blocks, and the bot cannot add padding. Rhythm therefore comes from **which
blocks follow which**.

### 3.1 Width budget

The iPhone bubble is the design width. The working assumption is ~34 characters of body text and ~30 characters of
table content, calibrated in round 0 with the ruler mock (§9.2). The Mac bubble is wider. The same card must not
look sparse there: tables hold their natural width rather than stretching, and charts are 4:3 so their height
scales with the width.

### 3.2 Canonical block order

Every card follows this skeleton, leaving out what it does not need:

1. **Media.** Video or slideshow, first and edge to edge. **A chart is not hero media:** it is data, and data is
   read after its name, so on a chart screen the `H2` and lede come first and the chart follows the lede.
2. **`H2` title.**
3. **Lede `P`.** State emoji + bold verdict + ≤ 2 facts.
4. **Hook or key quote** (`PQ`). Review card only.
5. **Facts table** (`T[b c]`, 2 columns, ≤ 7 rows).
6. **Sections** (`H3` + one `P`/`L`/`T` each, ≤ 3).
7. **Details** (closed), ordered by how often they are opened.
8. **`HR`.** The one divider separates content from actions.
9. **Action rows** (`BTN`), main move first.
10. **Navigation row** (`BTN`, link style).
11. **`F` footer.** Meta.

Rules:
- **Dividers vs headings.** Headings separate sections of content. The divider appears **once per card**, between
  content and actions. A divider is never placed next to a heading, and never two in a row.
- **No empty paragraphs** for spacing, and no two consecutive `P`s that could be one.
- **Card face budget:** ≤ 16 top-level blocks and ≤ 120 blocks including nested ones (the limit is 500; staying
  far under it keeps editing fast). If a card face scrolls more than about 1.5 iPhone screens with Details closed,
  something belongs in a Details or on another screen.
- **Lists:** ≤ 6 items on a card face, then "_+8 more_" and a pager or screen. This fulfils the v2 "dashboard
  limits".

### 3.3 Tables

| Rule | Value |
|---|---|
| Columns on phone | **≤ 3 by default, 4 at most.** Width check: Σ(widest cell per column, in characters) + 3 × columns ≤ 30. The prototype's 5-column table scored 48 and wrapped |
| Key–value table | 2 columns; label ≤ 10 characters, so the value gets ≥ 60% of the width; values wrap at word boundaries; `is_bordered` + `is_compact`, not striped (≤ 7 rows need no stripes) |
| Data grid | Header row (`is_header`), `is_bordered` + `is_striped` + `is_compact`, ≥ 3 rows |
| Alignment | Text left, numbers right, state cells left. `valign` middle |
| Header labels | ≤ 7 characters: "Views", "Watch", "Reach", "Used", "State". The first header cell is empty when rows are self-evident |
| Inside cells | Inline formatting only (API rule): no emoji except a leading state emoji in a verdict cell, no links except "↗" post links in Posted |
| `caption` | Not used. The `H3` above names the table, because captions render small and get missed |
| Key–value vs list | Key–value when ≥ 3 facts have short labels and one-line values. A list when items are peers (runs, topics, posts) or values are sentences |
| Too wide? | Drop a column into the item's own screen. Never shorten words to make a column fit ("Followers" never becomes "Fllwrs") |

### 3.4 Charts and images

Telegram has no drawing primitives (brief §H), so charts are rendered PNGs inside the rich card.

| Use | Size (px) | Aspect | Notes |
|---|---|---|---|
| Hero chart (analytics, digest) | 1200 × 900 | 4:3 | 16:9 is too short on a phone (330 × 186 pt) |
| Small multiples (one row per platform) | 1200 × 1500 | 4:5 | 3–4 rows. At 4:3 each row would be ~80 pt tall on a phone, too flat to read a trend. This replaces the Unicode trend bars |
| Retention curve (per video) | 1200 × 900 | 4:3 | Marker at the 3 s hook |
| Review proxy video | 720 × 1280 | 9:16 | Reel; the deep-dive proxy is 1280 × 720 |
| Beat stills, covers | Source aspect | 9:16 | In a Slideshow or Collage, never stretched |

Chart craft:
- Flat brand plate (§5.2) with a 48 px safe margin. No title inside the image: the `H2`/`H3` above is the title.
- Montserrat (in the repo): direct labels at **44 px SemiBold**, ticks at **38 px** Medium, in a 1200 px-wide
  image. Shown 330 pt wide on an iPhone, that is 12 pt and 10.5 pt; Apple's floor for legible text is 11 pt, and
  the earlier 34 px (9.4 pt) fell under it. At most 4 ticks per axis, so the larger type never crowds.
- Numbers on the chart use the §2.4 formats (`2,410`, `+12%`), with the true minus.
- Series lines 5 px, grid lines 2 px, markers 14 px in three shapes (circle, square, triangle) so series differ
  without colour.
- Direct labels at line ends with the latest value and its change ("Instagram 2,410 +12%"). There is no legend
  box.
- A file stays under 300 KB. No gradients, no 1 px lines (Telegram re-encodes photos as JPEG).
- Re-rendered and swapped only when the plotted numbers change (hash of the data), not on every collector tick.
- **Alt text:** the Photo block's caption states the trend in words for VoiceOver and for anyone who does not
  read charts: "Views a day, 28 days. Instagram up 12%, Facebook flat, Shorts down 8% against their medians."

### 3.5 Collage, Slideshow, Map

- **Collage:** 2–4 items of the same aspect ratio, all seen at once. It is used for the cover options (3 × 9:16)
  and the Story frames (≤ 4).
- **Slideshow:** ordered sequences of more than 4 items, swiped. It is used for beat stills (10) and carousel
  slides (7). Each slide's caption names it the way /photos does: "Photo 3, Main gallery", with the credit in
  the caption's `credit` field ("Wikimedia Commons · CC BY-SA 4.0").
- Never more than one Collage or Slideshow per card face. Videos stay out of slideshows (they autoplay badly in
  swipe views).
- **Map:** once, inside the review card's "Place & sources" Details: 640 × 320, zoom 6 (country context). Never on
  a card face.

---

## 4. Iconography

### 4.1 Rules

- **One emoji, one meaning,** across topics, cards, buttons and lists. The table below is the whole vocabulary; an
  emoji not in it is not used.
- **VS16 always.** Emoji whose default is text presentation are always sent with U+FE0F, or iOS may draw them as
  black text glyphs: 🏛️ 🗞️ 🗓️ 🖼️ ⚠️ ✏️ ▶️ ⏸️ ⏹️ ⏭️ 🗂️ 🗒️ 🛡️ ⚙️ ↩️ ☑️ ⚖️ 🎞️. A test fails any registered string
  missing it.
- **Where emoji go:**
  - Navigator screen titles, section buttons, list items that are kinds or states, and lede lines.
  - **Not** content-card titles, table labels, Details summaries (unless the summary carries a verdict:
    "⚠️ Findings · 2"), or action buttons (§6.3).
- One emoji per line, at its start. Never two in a row.

### 4.2 The set

**Topics**

| Topic | Emoji | Topic icon colour (fixed at creation) |
|---|---|---|
| Studio | 🏛️ | `0xFFD67E` gold (brand) |
| Needs you | 🔔 | `0xFB6F5F` red |
| Reviews | 🎬 | `0xCB86DB` violet |
| Runs | ⚡ | `0x6FB9F0` blue |
| Info | 🗞️ | `0x8EEE98` green |

The prototype has Studio blue and Runs gold. Colour cannot be changed by `editForumTopic`, so the dev topics are
recreated (dev only; `deleteForumTopic` removes their test messages) and production is created right.

**States** (always followed by a word)

| State | Emoji | Words used with it |
|---|---|---|
| OK / done / passed | ✅ | Passed, Ready, Approved, Posted, Connected |
| Warning / needs attention | ⚠️ | Short of photos, Hedged, Expires soon |
| Failed | ❌ | Failed, Rejected |
| Blocked / unavailable | ⛔ | Blocked by the release gate, Capped until… |
| Running | ⏳ | Making, Installing, Posting |
| Not started | ◽ | (stage name only). Round 0 compares ◽ with ▫️ in dark mode: a white square must not out-shine the ✅ rows above it |
| Timed out | ⏰ | Timed out |
| Skipped | ⏭️ | Skipped |
| Cancelled | ⏹️ | Cancelled |
| Paused | ⏸️ | Paused |
| Active (schedule) | ▶️ | Active |

`hourglass()` currently flips ⏳/⌛ each minute. It stops: ⌛ next to ⏰ reads as "timed out", and the progress
card's sign of life becomes the live "updated ⟨r⟩" footer and the "3 min so far" counter.

**Objects and screens**

| Meaning | Emoji | Meaning | Emoji |
|---|---|---|---|
| Queue | 📋 | Reel (post kind) | 🎞️ |
| Posting / slots | 🗓️ | Story (post kind) | 📱 |
| Pictures / photo sets | 🖼️ | Carousel (post kind) | 🗂️ |
| Covers | 🎨 | Posted | 📮 |
| Music | 🎵 | Restorable | ↩️ |
| Upload | 📎 | Ideas | 💡 |
| Status and health | 🩺 | UNESCO marvels | 🌍 |
| Audience / analytics | 📈 | Proposals / improvements | 🧭 |
| Activity / audit | 🛡️ | Accounts and providers | 🔑 |
| Jobs | 🧰 | Notices (was Alerts 🔔) | 🗒️ |
| Weekly digest | 📊 | Policy watch | ⚖️ |
| History (decisions) | 📜 | | |
| Settings check | ⚙️ | Release / deployment | 📦 |
| Spend | 💳 | Help | ❓ |
| Reason prompt | 📝 | Place | 📍 |
| Post candidate (dev) | 🚀 | Edited | ✏️ |
| Making again (regenerate) | 🔄 | Best / worst | 🏆 / 🔻 |
| Instagram | 📸 | Facebook | 📘 |
| YouTube (Shorts and long) | 📺 | Development / Production | 🧪 / 📡 |

Reassigned from today's bot so each meaning has one emoji:
- 🎬 now means Reviews only. The Reel kind is 🎞️ and YouTube long is 📺.
- 🏛 for UNESCO marvels becomes 🌍.
- 🔔 Alerts becomes 🗒️ Notices.
- ♻️ (Regenerate) becomes 🔄, and ↩️ is Restorable.
- 🟢🟡🟠🔵 readiness becomes ✅/⚠️/⏳ with words.
- 🔴 for "Live production" becomes 📡, because red means fail and danger.
- 💓 heartbeat is dropped; the dashboard footer carries it.
- 📊 Status becomes 🩺: next to 📈 Audience two chart glyphs were indistinguishable at button size. 📊 moves to
  the Weekly digest, whose old 📰 was a second newspaper next to the 🗞️ Info topic.
- ⬆️ and 🗑️ on the Queue's pickers are removed (they were outside the set); those buttons are verbs (§7.8).

**Clashes with Telegram's own glyphs, checked:** 📌 (pins), 🔇/🔕 (mute), ✓✓ (read ticks), ⏰ (scheduled message
clock in the composer) and 📎 (attach). The set avoids 📌 and the mute bells. ⏰ means "timed out" only inside
card text, never on a button, so it is not mistaken for scheduling. 📎 Upload deliberately borrows the attach
meaning, because the upload screen asks the operator to attach a photo.

**VoiceOver names.** iOS reads ✅ as "check mark button" (its CLDR name). The word after it ("Passed") carries the
meaning; round 0's VoiceOver pass (§9.2) confirms the word is read, and if "button" misleads, state cells read
✅ only where no button sits on the same line (already true in tables).

### 4.3 Custom emoji

The reference allows custom emoji, in text and as `icon_custom_emoji_id` on buttons, "in the messages directly
sent by the bot to private… chats if the owner of the bot has a Telegram Premium subscription" (api.txt).

- **Decision (operator, 29 Oct): no Premium now.** The Unicode set above is what ships.
- **Round 1 carries one mock-only comparison** so the idea is judged on the device, not in the abstract: the
  dashboard and the review card drawn twice, once with Unicode and once with a sample of 8 custom icons.
  - Style of the sample: monochrome antique-gold line icons, 2 px stroke at 100 px, static WEBP, alt character
    = the Unicode emoji it replaces, objects and topics only (state emoji stay Unicode, because their shapes are
    universally known and VoiceOver names them).
  - Because custom emoji in bot messages need Premium on the owner's account, the comparison mock is a
    screenshot composite or is sent from a test account that has it; it never needs Premium on the production
    owner, and nothing in the build depends on it.
  - If the operator later takes Premium, the full 32-icon set follows this style, behind a
    `TELEGRAM_CUSTOM_EMOJI` setting with Unicode fallback on any custom-emoji error.
- **Topic icons are free.** `getForumTopicIconStickers` has no Premium gate. Round 0 checks whether the iPhone
  tab shows the topic icon **and** the name's emoji. If it shows both, the name drops its emoji ("Studio",
  "Needs you") and the matching topic icon sticker is set, so the tab shows one icon, not two.

---

## 5. Colour

### 5.1 Button styles (the only colour a bot controls in text)

| Style | Means | Used for | Per card |
|---|---|---|---|
| `success` (green) | Go: publish or accept | Approve & post, Post now, Approve set, Accept proposal, the "yes" of a non-destructive confirmation | ≤ 1 |
| `danger` (red) | Destroy, stop or refuse | Reject, Cancel run, Drop topic, Cancel booking, Remove photo, and the "yes" of their confirmations | ≤ 1 |
| `primary` (blue) | The main move when it is not final | Make it now, Edit text, Redo with a note, Try again, Resend for review, Open Needs you | ≤ 2 |
| `link` | Navigation and escape | ‹ Back, 🏠 Studio, the "keep it" side of a confirmation | any |
| default | Choose or browse | Section grid, pickers, number buttons, pager, copy | any |
| `disabled` | State: "not now, and why" | "Making Nazca…", "Blocked by a check", "Installing rc71…" | ≤ 1 |

Rules:
- A success and a danger button are never in the same row. A danger button never sits directly under a success
  button.
- `link` exists only on callback buttons in Buttons blocks (API). URL buttons keep the default style.
- Colour never replaces the verb. "Reject" is red **and** says Reject.

### 5.2 Chart palette

**Everything follows the device theme.** Text, tables, buttons, Details and footers are drawn by the client, so
they already follow light and dark on both devices. A chart is a static image, and a bot cannot know the
client's theme, so the chart has to read well on **both** chat themes at once. Design round 1 sends two variants
of the same chart side by side and the operator chooses (§9.2). There is no "dark by default".

Chat surfaces the chart must work on (approximate incoming-bubble colours, re-measured from round 1
screenshots): iOS light `#FFFFFF`, iOS dark `#262628`, iOS night `#1C1C1E`, iOS tinted `#182533`, Mac dark
`#2B2B2B`.

**Variant (a), recommended: a self-contained widget panel.** The chart carries its own ink panel, full bleed, with
no border drawn in the image. Telegram rounds media corners in the bubble, so the panel reads as a rounded
widget (round 1 confirms the rounding inside a rich Photo block; if the corners come out square, the image gets
a drawn 28 px corner radius on a 24 px margin of the panel colour). Contrast is fixed by the panel, not by the
chat, so it is identical in every theme:
- On light chats the panel is a deliberate dark card (17.9:1 against white), like an iOS widget.
- On dark chats it sits almost flush (1.05–1.27:1), which reads as native dark content, not a box.
- It matches the videos' museum-dark look.

**Variant (b), for comparison: transparent, with haloed type.** Sent as a PNG with alpha. Lines use mid-luminance
colours (relative luminance 0.20–0.25) that clear 3:1 against both white and every dark bubble; text and labels
are ivory with a dark halo, so their contrast is carried by the halo, not the chat.
- **Technical risk, tested first:** Telegram re-encodes photos as JPEG, which has no alpha. If the transparent
  area arrives flattened (black or white), (b) is impossible and is withdrawn without a design debate.
- Even when alpha survives, haloed text on a white chat looks heavier than (a), and the halo is the price of
  theme-independence.

| Token | (a) Widget panel | (b) Transparent | Contrast target | Measured (a) / (b) worst case |
|---|---|---|---|---|
| Plate | `#15171C` | none (alpha 0) | — | — |
| Text, direct labels | `#EDE6D6` ivory | `#F4EFE6` + **6 px** halo `#15171C` (100%) | ≥ 4.5:1 | 14.4 / 15.1 against its halo |
| Muted (ticks, units) | `#9A9385` | `#F4EFE6` at 38 px + **5 px** halo `#15171C` (90%) | ≥ 4.5:1 | 5.9 / ≥ 12 against its halo |
| Grid | `#2A2C33` | `#857F74` at 35% alpha, 2 px | quiet, < 1.6:1 | 1.29 / ≈ 1.4 |
| Instagram | `#E08BBE` | `#C2559A` | ≥ 3:1 on every surface | 7.35 / 3.41 (Mac dark) |
| Facebook | `#56B4E9` | `#2B86C5` | ≥ 3:1 | 7.77 / 3.59 (Mac dark) |
| YouTube | `#F0754A` | `#CC5A26` | ≥ 3:1 | 6.27 / 3.41 (Mac dark) |
| Brand accent (highlights only: "today", the 7-day band, the hook marker) | `#D4AF37` | `#A88419` | ≥ 3:1 | 8.53 / 3.51 (white) |
| Series line halo | none | **2 px** `#15171C` at 30% each side | separates line from a busy wallpaper | — |

- Measured with WCAG 2.x relative luminance against the surfaces above; the script `C:\tmp\tg_design_contrast.py`
  gains the (b) palette and the five surfaces before round 1, and re-runs the colour-vision ΔE check for (b):
  Instagram `#C2559A` and YouTube `#CC5A26` share a luminance, so they must stay apart by hue under
  deuteranopia (target ΔE ≥ 12, as in (a)); if they do not, (b) fails the comparison, because marker shapes
  (§3.4) are a backup, not the main distinction.
- Halo widths are at the 1200 px render size (6 px ≈ 1.6 pt on an iPhone): enough to hold contrast, thin enough
  not to look like outlined meme text.
- A light parchment plate is retired: a light panel on a dark chat is a glaring white box, the opposite of
  following the theme.
- (a)'s series are the original ink-plate study's colours, Okabe–Ito derived, with these measured results:

  Minimum ΔE between series under simulated colour-vision deficiency (Machado 2009, full severity), ink /
  (retired) parchment:
  - deuteranopia 28.9 / 34.3
  - protanopia 14.3 / 17.1
  - tritanopia 32.0 / 26.9
- Gold is **never a series**. Next to YouTube vermilion it drops to ΔE 14.7 (deuteranopia) on ink and 6.2
  (protanopia) on parchment.
- All text is ≥ 4.5:1 and every series ≥ 3:1. Measurement script: `C:\tmp\tg_design_contrast.py`.
- Platform brand colours (Instagram pink, YouTube red) are not used. Two of them collapse into one for a
  red–green colour-blind viewer, and they clash with the button reds.
- Growth is not drawn in green, nor decline in red. Views going up is not "success".

### 5.3 Status colour

Text colour is the client's. Status colour comes from the state emoji (§4.2), and always with a word. Topic icon
colours (§4.2) identify topics in the tab bar and the side bar; they carry no status.

---

## 6. Buttons

### 6.1 Where buttons live

- **Inside the card, as Buttons blocks.** Actions and navigation are content: they edit with the card, they can
  use `link` style, and they sit above the footer.
- **`reply_markup` inline keyboard** is used only where the API requires it:
  - plain messages (the reason prompt and the rare plain fallbacks)
  - `force_reply` (§7.14)
- **No persistent reply keyboard.** With topics, the tab bar and the side bar *are* the navigation. A 🏠/🔔
  keyboard would take screen height on the iPhone to repeat them. (v2 §6 proposed it. This replaces that, unless
  topics fail and the single-chat fallback is used.)

### 6.2 Order and rows (top to bottom)

1. The main move. Full width when it is success.
2. Secondary actions, two-up.
3. The destructive action, last among actions, on the right.
4. A section grid or pickers (navigator screens).
5. The pager: `‹ Prev` · `2 / 5` (disabled, as a counter) · `Next ›`.
6. The navigation row, link style, centred: `‹ <parent>` · `🏠 Studio`. It is absent on the Studio home. A card
   in another topic (Reviews, Runs, Info) has no 🏠, because the tab bar is home.

Per row on the phone: **2** text buttons, **3** short ones (≤ 8 characters: pager, Yes/No), **5** number buttons
(1–5). Never 4 text buttons in a row. Every button is at least as tall as Telegram's default, and nothing is
squeezed.

### 6.3 Labels

- Verb first, sentence case, ≤ 20 characters (≤ 16 two-up): "Approve & post", "Edit text", "Make it now",
  "Resend for review".
- **Action buttons carry no emoji.** Kept after critique, for four reasons:
  - A colour emoji on a green, blue or red fill loses contrast and turns the button into two competing colours.
  - VoiceOver reads the emoji's name before the verb ("check mark button, Approve and post"), which is slower
    and confusing on a button.
  - Speed comes from position and colour, not icons: the main move is always first and full width, the
    destructive move always last and on the right (§6.2). The eye learns the place, not a glyph.
  - Telegram's own action buttons (Join, Start, Open app) are text-only, so text-only reads as native.
- **Plain verbs for a non-technical operator.** "Make" for a run ("Make it now", "Making Nazca…"), "Try again"
  for a failed run, "Redo with a note" for regenerating; never "render", "re-render", "regenerate" or "pipeline"
  on a button. Internal words may appear only in Run details.
- **Section and navigation buttons carry their screen emoji** (they are nouns in a grid): "📋 Queue",
  "‹ Status", "🏠 Studio".
- A count goes in the label only when it is a call to act: "🎬 Reviews · 2", "🔔 Needs you · 3".
- The object is named in the card, not in the button ("Try again", not "Try Nazca Lines: The Runway… again").
- **Copy buttons:** "Copy run id", "Copy caption", "Copy command". There is no 📋, because the client adds a copy
  glyph (observed). The payload is ≤ 256 characters. A longer caption is copied through Edit.

### 6.4 Disabled

A disabled button shows an action that exists but cannot run now, and **the label says why**: "Making Nazca…",
"Blocked by a check", "Installing rc71…", "Posting…". A disabled button is never decoration and never a heading.
It is at most one per card. The pager counter (`2 / 5`) is the one standing exception.

### 6.5 Confirmations

Confirmations are used for:
- **Destructive actions:** Reject, Cancel run, Drop topic, Cancel booking.
- **Irreversible ones:** Approve & post, Post now.
- **Paid ones:** a long or paid Cancel job, Redo with a note.

The confirmation replaces **only the card's action zone** (everything below the `HR`) (§7.13). The title,
video, table and Details stay exactly where they were. This matters for three reasons:
- The operator keeps the context (the video they just watched, the slot) on screen while answering.
- The card's height barely changes, so the iPhone does not jump the scroll position under the operator's thumb.
- A card with a Video block never has to drop it (the §8 media rule).

The action zone becomes:
- a `P` with the question in bold and the consequence in one sentence, ≤ 160 characters;
- one row: the escape on the left (`link`) and the action on the right (`success` or `danger`), repeating the
  question's verb;
- the footer `Expires ⟨r⟩`.

It expires after 5 minutes and reverts. A tap after that gets a toast. It never becomes a new message.

**Reject asks for its reason in the confirmation itself.** The reason chips *are* the confirming buttons (§7.13),
so a rejection is two taps (Reject, then a reason) instead of four, and a rejection can never be recorded without
a reason. Redo with a note works the same way.

**Undo, after the fact.** A confirmation stops slips; an undo forgives them.
- Approved: the decided card carries `[Undo approval](·)` until 15 minutes before the first post goes out. It
  cancels every booking and puts the card back to Waiting. The footer says how long: `Undo until ⟨t⟩`.
- Rejected: `[Undo](·)` for 10 minutes, restoring the card to Waiting and the topic to its place.
- Post now and anything already posted have no undo, which is why they confirm in words that say so.

### 6.6 Back and home

- `‹ <parent>` follows `telegram_ui.SCREENS` parents and the trail, as today.
- `🏠 Studio` (renamed from "🏠 Dashboard") returns to the dashboard.
- After 15 minutes without a tap, the Studio card returns to the dashboard by itself, so the pinned card you come
  back to is always the dashboard.

---

## 7. The cards

Every mock below is complete for its state. Sample data uses real topics. Character budgets were checked against
§2.5 and §3.3.

### 7.1 Studio dashboard `[rich]` · 🏛️ Studio · pinned · edited in place

**Idle**
```
H1   Relic & Riddle
P    🧪 Development · rc70                          (production: 📡 Production · rc70)
P    ✅ **Nothing needs you** right now.
H3   Coming up
T[b c]  Next run   │ ⟨r: in 6 hours⟩
        Next up    │ Derinkuyu Underground City
        Next post  │ Nazca Lines ⟨wt: Friday 6:00 PM⟩
        Queue      │ 14 topics, 9 ready
D▸   ✅ Health · 6 of 7 runs passed
       T[b c]  Typical run │ 31 min
               Spend       │ $18.40 of $60 this month
               Voice       │ 41% of the month used
HR
BTN  [Start next episode](P)                                (full width)
BTN  [📋 Queue](·)                   [🗓️ Posting · 3](·)
BTN  [🎬 Reviews](·)                 [📈 Audience](·)
BTN  [🖼️ Pictures](·)                [🎵 Music](·)
BTN  [🩺 Status](·)                  [❓ Help](·)
F    Updated ⟨r: just now⟩
```
- Audience moves onto the home grid (it was under Status) because it is the revenue screen (project goal).
- **Health folds into a Details** whose summary carries the verdict. The dashboard's job is "does anything need
  me, and what happens next"; health is a glance at the summary. It opens by itself (`is_open`) when any of its
  rows is ⚠️ or ⛔, and the summary then reads `⚠️ Health · spend at 90%`. "Median" became "Typical run".
- The face drops from 15 to 13 top-level blocks and fits one iPhone screen with the Needs-you list closed.
- **Pause** moves to Status, next to the Schedule row (§7.11). A one-tap stop beside Help on the home grid was
  too easy to hit by accident and was an action with an emoji, against §6.3.
- "Render #1 now" became "Start next episode" (18 characters): no jargon, no "#1". The grid buttons are ordered by use.
- The footer drops "daemon ✅ · digest ready": "daemon" is jargon, the emoji sat mid-line, and a stopped daemon
  is a ⛔ Needs-you item, not a footer tick.

**Run live** (lede and first button change; the rest as idle)
```
P    ⏳ **Making Nazca Lines** · review ⟨r: in 12 minutes⟩
P    _Step 3 of 5. Follow it in ⚡ Runs._
…
BTN  [Making Nazca…](✕)                                     (full width)
```

**Needs you** (replaces the "Nothing needs you" lede; ≤ 3 items, then "+N more")
```
H3   Needs you · 4
L    🎬 Review waiting — Nazca Lines
     ❌ Last run failed — Dyatlov Pass
     🧭 2 improvement proposals
P    _+1 more_
BTN  [🔔 Open Needs you · 4](P)
H3   Coming up
…
```
"Open Needs you" pages through the same card component **inside Studio**. Telegram has no link to a message in a
private chat (by absence in 10.3), so a button cannot switch tabs. The 🔔 topic card and this view come from the
same query, so the two never disagree.

**Dev post candidate** (development only, inside Needs you): `🚀 Post candidate — Nazca Lines (dry run)` and
`[Post for real](S)` → confirmation.

**Installing a release**: see §7.17.

### 7.2 Needs you, paged `[rich]` · 🔔 Needs you · pinned · edited in place

Items are ordered: 🎬 reviews waiting · ❌ failures needing a decision · ⏰ timed-out reviews · 🔑 accounts and
providers · 🧭 proposals, oldest first within each. The card shows **the first item in full and the rest as a
numbered list**, so the whole backlog is read in one glance and any item is one tap away. (Round 0's "one item a
page" with a pager and Next group needed up to 5 taps to see the fourth item and hid the size of the backlog; it
is retired.)

**A review waiting** (first item open, three more listed)
```
H2   🔔 Needs you · 4
H3   🎬 Review waiting
P    **Nazca Lines: The Runway Paradox** · made ⟨r: 2 hours ago⟩
P    _Watch it in 🎬 Reviews and decide there. It times out ⟨wt: Saturday 2:00 PM⟩._
BTN  [Send it again](·)
H3   Also waiting
OL   2. ❌ Dyatlov Pass failed — ⟨r: 40 minutes ago⟩
     3. 🔑 Instagram token — expires ⟨r: in 3 days⟩
     4. 🧭 Picture search proposal
HR
BTN  [2](·) [3](·) [4](·)
F    Updated ⟨r: just now⟩
```
- Approve is deliberately not here. Approving means watching, and the video is in Reviews.
- "Send it again" re-sends the review card to the bottom of 🎬 Reviews (silent), for when it has scrolled away.
- Tapping a number opens that item in the top slot; the one it replaces drops into the list. There is no pager
  until a seventh item, and then only a `Next ›` on the list.
- The 4 consecutive paragraphs of the earlier mock collapse into 2.

**A failure**
```
H3   ❌ Last run failed
P    **Dyatlov Pass Incident** · at quality checks ⟨r: 40 minutes ago⟩
XBQ  Release gate: beat 8 picture judged against narration ("ski tracks") but shows the memorial;
     re-plan the beat or replace the photo.
BTN  [Try again](P)              [Copy run id](⧉)
```

**A timed-out review**
```
H3   ⏰ Review timed out
P    **Derinkuyu Underground City** · nothing posted
P    Its topic waits for this decision instead of being made again.
BTN  [Resend for review](P)      [Reject](D)
```

**Accounts and providers**
```
H3   🔑 Instagram token expires
P    ⟨r: in 3 days⟩ · posting stops when it does
BTN  [Open Accounts](P)
```
```
H3   ⛔ Gemini unavailable
P    Monthly cap reached · back ⟨dt: 11/1/26, 12:00 AM⟩
P    Scripts use the next writer meanwhile.
BTN  [Open Status](·)
```

**A proposal**
```
H3   🧭 Improvement proposal
P    **Widen thin picture searches within the topic's Commons category**
P    From 3 findings since rc68 · closes itself if none recur
BTN  [Open proposal](P)
```

**Empty** (the card stays and is edited to this)
```
H2   🔔 Needs you
P    ✅ **Nothing needs you** right now.
P    _Next run ⟨r: in 6 hours⟩. This topic rings when something arrives._
F    Checked ⟨r: just now⟩
```
Acting on an item removes it, toasts what happened ("Nazca Lines approved"), and moves the next item up. The
empty state says when the next thing can happen, so an empty card reassures instead of looking broken.

### 7.3 New-item line `[plain]` · 🔔 Needs you · notifies · deleted when handled

```
🎬 Review waiting · Nazca Lines
```
Other examples: `❌ Run failed · Dyatlov Pass`, `⏰ Review timed out · Derinkuyu`,
`🔑 Instagram token expires in 3 days`, `🧭 New proposal · picture search`.
- One line, ≤ 40 characters, no period, no buttons (the pinned card above holds the actions).
- **This is the only message in the system that notifies** (§1.4). The episode is named by its short name
  (§7.15), never cut with "…".
- `link_preview_options.is_disabled` on every plain message, so a domain in a line never grows a preview.
- The lock screen reads "Relic & Riddle › Needs you: 🎬 Review waiting · Nazca Lines".
- It is deleted when its item is handled. An item still open at 40 hours keeps its page on the card and loses its
  line, which keeps every deletion inside the 48-hour window.

### 7.4 Review card `[rich]` · 🎬 Reviews · silent on arrival (the 🔔 line rings)

**Waiting**
```
VID  review_proxy.mp4 · 720×1280 · supports_streaming · cover (round 0 variant)
H2   Nazca Lines: The Runway Paradox
P    ⚠️ **Ready to approve** · 2 notes to check
PQ   “From the air they look like runways. From the ground, you'd never know they were there.”
     — Hook, first 3 seconds
T[b c]  Checks   │ ✅ All 4 passed
        Facts    │ 14 checked, 2 hedged
        Posts    │ Reel, Short, 4 Stories, carousel
        Slot     │ ⟨wt: Friday 6:00 PM⟩
        Place    │ Nazca, Peru
D▸   ⚠️ Worth a look · 2
       L  ⚠️ 2 repeated shots — thin photo pool (beats 4 and 9)
          ✅ Beat 8 picture judged against narration — fits
D▸   Script · 52 s, 142 words
       P  (narration, one paragraph per beat, with its timing in _italic_: “_0:00–0:04_ From the air…”)
D▸   Captions & first comment
       H3 Caption           P (as posted, hashtags removed)
       H3 Carousel caption  P
       H3 First comment     P
       P  #nazca #peru #ancientmysteries …
       BTN [Copy caption](⧉)  [Copy hashtags](⧉)
D▸   Pictures · 10 beats
       SLD  10 stills, caption per slide “Photo 3, Hummingbird geoglyph” — credit “Wikimedia Commons · CC BY-SA 4.0”
D▸   Facts & sources · 14
       T[b c]  Release gate │ ✅ Passed
               Facts        │ ✅ 96% supported
               Pictures     │ ✅ 9 distinct, 10 beats
               Rights       │ ✅ All cleared
       L    per claim: ✅/⚠️ + the claim, the source domain ↗
       MAP  −14.74, −75.13 · zoom 6 · 640×320
D▸   Run details
       T[b c]  Written by │ Gemini 2.5 Pro
               Voice      │ ElevenLabs · Archivist
               Music      │ “Ancient Winds” · licensed
               AI label   │ Embedded (Meta)
               Cost       │ $0.41
       BTN [Copy run id](⧉)
HR
BTN  [Approve & post](S)                                    (full width)
BTN  [Edit text](P)                [Redo with a note](P)
BTN  [🎨 Covers](·)                [Reject](D)
F    🧪 Dry run · rc70 · made ⟨r: 2 hours ago⟩
```
- **Verdict first.** The lede under the title is the decision state (principle 2). The earlier mock put the hook
  and a place line there and left the verdict inside the table's first row. The lede is computed:
  - `✅ **Ready to approve** · 52 s` when nothing is worth a look;
  - `⚠️ **Ready to approve** · 2 notes to check` when the "Worth a look" Details has a ⚠️ item;
  - `⛔ **Can't be approved yet** · 1 check failed` when blocked (below).
- **Checks collapse to one row when they all pass.** Four ✅ rows in a column were four identical icons for
  VoiceOver and the eye. Each check gets its own row on the face only when it is not ✅; the full list sits in
  "Facts & sources".
- **Six Details, not seven,** ordered by what a decision needs: what to check, then what you hear, read, see,
  and the proof. "Fact check" and "Place & sources" merge; the cost moves into Run details.
- **Height on the iPhone.** A 9:16 video at bubble width is ~590 pt tall, most of a screen, so the face below it is
  budgeted at one more screen: title, lede, hook, 5-row table, 6 Details summaries and the action rows.
  Round 0 measures how tall the client actually draws a portrait video in a rich block (§9.2); if it is drawn
  at full height, the hook moves into the Script Details and the table loses Place.
- Every note the old text card carried has a home here:
  - Scene plan, picture lines, footage, graphics, motion and the stills/cover notes go under **Worth a look**
    (✅ items included, so a fixed issue shows as fixed).
  - Trimmed sentences go under **Script** as `~~struck~~`.
  - Voice and music notes go under **Run details**.
  - The poll goes under **Captions**.
  - Topic origin and fact sheet notes go under **Facts & sources**.
- Card face: 16 top-level blocks, the §3.2 ceiling. Nothing more may be added to the face; a new note goes into
  a Details.
- The production footer reads `📡 Production · rc70 · made ⟨r⟩`.
- A card made again with a note adds, under the lede, `P 🔄 Made again · your note: “tighten the hook”`.
- **No `protect_content`.** The earlier draft protected the card. In the operator's own private chat it only
  stops them saving or forwarding a cut to ask someone's opinion, and on some clients it also blocks copying
  text. Open question §10.4.
- **Missing video.** If the proxy upload fails, the card is sent without the Video block, the lede reads
  `⛔ **Video didn't upload** · nothing is lost`, Approve is replaced by `[Video missing](✕)` and
  `[Send video again](P)` takes the first action row. A card can never be approved unwatched.

**Blocked by the release gate**
```
P    ⛔ **Can't be approved yet** · 1 check failed
…T  Checks │ ⛔ Rights failed
D▾   ⛔ Why it can't be approved                            (open, first Details)
       P  ==The music's licence record is missing its proof file.==
       P  Fix the record and the card checks itself again, or redo it with a note.
…
BTN  [Blocked by a check](✕)                                (in place of Approve)
BTN  [Edit text](P)                [Redo with a note](P)
BTN  [🎨 Covers](·)                [Reject](D)
```
The reason is written for the operator ("missing its proof file"), not in release terms ("predates rc66"); the
release detail goes in Run details.

**Edited** (after Edit saves; the same card, redrawn)
```
H2   Nazca Lines: The Runway Paradox
P    ✏️ **Edited** ⟨r: just now⟩ · caption and first comment
…    (rest unchanged; the changed Details gains “· edited”: “Captions & first comment · edited”)
```
A new cut after an edit swaps the Video block and stamps `✏️ Edited · new cut ⟨r⟩`.

**Decided** (action rows removed; stamp under the title; Details kept for the record)
```
VID  (kept)
H2   Nazca Lines: The Runway Paradox
P    ✅ **Approved** ⟨t: 2:31 PM⟩ · posts ⟨wt: Friday 6:00 PM⟩
T[b c]  Reel & Short │ ⏳ Booked ⟨wt: Friday 6:00 PM⟩
        Stories      │ ◽ After the Reel
        Carousel     │ ◽ ⟨wt: Sunday 12:00 PM⟩
BTN  [Undo approval](·)
F    Undo until ⟨wt: Friday 5:45 PM⟩ · rc70
```
- As posts go out, the rows edit to `✅ Posted ⟨r⟩ · Instagram ↗`.
- The Undo button and its footer fact disappear 15 minutes before the first post (§6.5); the footer then reads
  `rc70 · decided by you`.

The other decided stamps:
- `❌ **Rejected** ⟨t⟩ · nothing posted`, a second line `P _Reason: Pictures. The topic is in Queue › Restorable._`,
  and `[Undo](·)` for 10 minutes.
- `🔄 **Making it again** ⟨t⟩ · follow it in ⚡ Runs`; the new card replaces this one.
- `⏰ **Timed out** ⟨r⟩ · nothing posted` plus `[Resend for review](P)  [Reject](D)`

The stamp pattern is: state emoji, **past-tense verb**, when, consequence.

### 7.5 Progress card `[rich]` · ⚡ Runs · pinned while live · silent edits

**Running**
```
H2   Nazca Lines
P    ⏳ **Step 3 of 5** · review ⟨r: in 12 minutes⟩ (⟨t: 2:52 PM⟩)
L    ✅ Script, fact check & voice — 6 min
     ✅ Music, photos, Stories & carousel — 9 min
     ⏳ Graphics, scene plan & video — 3 min so far
     ◽ Quality checks
     ◽ Review package
P    _Making the video: beat 6 of 10_
HR
BTN  [Cancel run](D)
F    🧪 Dry run: nothing posts, providers still bill · updated ⟨r: just now⟩
```
- Production footer: `📡 Production · approval decides posting · updated ⟨r⟩`.
- The lede loses "58%": the stage list already is the progress, a percentage beside "step 3 of 5" was a third
  `·` fact and pushed the lede to two lines, and it implied a precision the pipeline does not have. "Started"
  leaves the footer for the same reason (each stage shows its duration). The footer is ≤ 2 lines on the iPhone.

**Slow step** (under the list, in place of the live detail)
```
P    ⚠️ **Taking longer than usual** · this step usually takes 7 min; Commons is answering slowly
```

**Cancel tapped** → confirmation (§7.13): "Cancel this run?" / "14 min of work is lost. Nothing is posted."

**Uncancellable moment** (the final upload): `BTN [Packaging review…](✕)`.

**Done** (edited, unpinned; the outcome line §7.6 is sent; the card is deleted 30 minutes later)
```
H2   Nazca Lines
P    ✅ **Ready for review** ⟨t: 2:52 PM⟩ · 31 min
L    ✅ Script, fact check & voice — 6 min
     ✅ Music, photos, Stories & carousel — 9 min
     ✅ Graphics, scene plan & video — 11 min
     ✅ Quality checks — 3 min
     ✅ Review package — 2 min
P    _Its card is in 🎬 Reviews._
F    🧪 Dry run · rc70
```

**Failed**
```
H2   Dyatlov Pass Incident
P    ❌ **Failed** at quality checks ⟨t: 3:40 PM⟩ · 27 min
L    ✅ … ✅ … ✅ …
     ❌ Quality checks — 2 min
     ◽ Review package
XBQ  Release gate: beat 8 picture judged against narration but shows the memorial.
D▸   Log · last 12 lines
       pre  …
HR
BTN  [Try again](P)                [Copy run id](⧉)
F    📡 Production · rc70
```

**Cancelled**: the lede becomes `⏹️ **Cancelled** by you ⟨t⟩ · nothing posted`, the running stage becomes
`⏹️ … — stopped after 3 min`, and there are no buttons.

The "bring progress down" logic retires: with topics, the progress card cannot be buried under other kinds of
message.

### 7.6 Run outcome line `[plain]` · ⚡ Runs · silent · kept as the run log

```
✅ Nazca Lines ready for review · 31 min
❌ Dyatlov Pass failed at quality checks
⏹️ Derinkuyu cancelled · nothing posted
📮 Nazca Lines posted · Reel & Short
```
- ≤ 40 characters, no buttons, sent silently. A failure also raises a 🔔 Needs you line, which is the ping;
  "posted" is good news that needs nothing, so it never rings.
- These are never deleted: the ⚡ Runs topic **is** the run history, one line per outcome, readable as a log.
- Posting outcomes land here too, one line per episode, not one per platform.

### 7.7 Analytics `[rich]` · 🏛️ Studio (navigator screens)

**Overview**
```
H2   📈 Audience
P    Last 7 days · 3 videos posted
IMG  1200×1500 small multiples, on the chosen §5.2 variant: rows Instagram / Facebook / Shorts, views a day over 28 days, the last
     7 days on a gold band, direct label “2,410 +12%” at each line end
     caption: “Views a day, 28 days. Instagram up 12%, Facebook flat, Shorts down 8% against their medians.”
T[b s c]  ‖h           │ Views │ Watched
          Instagram    │ 10.2k │ 62%
          Facebook     │ 3.5k  │ 48%
          Shorts       │ 6.8k  │ 71%
          Long video   │ 1.1k  │ 38%
          Stories      │ 2.4k  │ —
H3   Best and worst · 28 d
L    🏆 Derinkuyu Underground City — 4.1k views
     🔻 Gol Gumbaz Whispering Gallery — 610 views
P    _Patterns read from 10 videos; 6 so far, around ⟨dt: 11/14/26⟩ at this pace._
D▸   How these are measured
       P  Each post is measured 1, 3, 7 and 28 days after posting; Stories at 20 h, before they expire.
       P  “+12%” and “flat” compare the newest video with the platform's own median at the same snapshot.
HR
BTN  [📸 Instagram](·)              [📘 Facebook](·)
BTN  [📺 YouTube](·)                [🎞️ By video](·)
BTN  [‹ Status](L)                  [🏠 Studio](L)
F    Updated ⟨r: 40 minutes ago⟩ · all 3 sources answered
```
- Title and lede come before the chart (§3.2): a chart is read after its name. The lede's "28 days: 11" fact
  left, because the chart already spans 28 days and the fact was a third `·` item.
- The column is "Watched" (average share of the video watched), not "Watch", which read as a verb.
- Table width check: "YouTube long" 12 + "Views" 5 + "Watched" 7 + 9 = 33, over the 30 budget, so the row is
  "Long video" (10, total 31) from the start, and round 0 confirms the last character.

**Per platform**
```
H2   📸 Instagram
P    28 days · 9 videos · +12% against its median
IMG  1200×900: views a day, 28 days, with new followers a day as a second small panel below (no right axis:
     dual axes are misread at phone size)
     caption: “Instagram views a day, 28 days: up 12% this week; 46 new followers.”
T[b c]  Views      │ 10.2k
        Reach      │ 7.9k
        Watch time │ 6.1 h
        Watched    │ 62% on average
        Followers  │ 1,284 (+46)
H3   Videos · 9
OL   1. Derinkuyu Underground City — 4.1k · 71%
     2. Nazca Lines — 2.4k · 64%
     … (≤ 6, then pager)
BTN  [1](·) [2](·) [3](·) [4](·) [5](·)
BTN  [‹ Prev](✕)  [1 / 2](✕)  [Next ›](·)
BTN  [‹ Audience](L)                [🏠 Studio](L)
F    Updated ⟨r⟩
```

**Per video**
```
H2   🎞️ Derinkuyu Underground City
P    Posted ⟨dt: 10/22/26, 6:00 PM⟩ · 52 s
IMG  1200×900 retention curve (Instagram and Shorts), gold marker at 3 s
     caption: “Still watching: 81% after the 3-second hook, 46% at the end.”
T[b s c]  ‖h         │ Views │ Watched
          Instagram  │ 4.1k  │ 71%
          Facebook   │ 1.2k  │ 52%
          Shorts     │ 2.9k  │ 74%
H3   Snapshots
TL   ☑ Day 1 ⟨dt⟩   ☑ Day 3 ⟨dt⟩   ☑ Day 7 ⟨dt⟩   ☐ Day 28 ⟨dt: 11/19/26⟩
BTN  [Instagram post ↗](↗)         [Short ↗](↗)
BTN  [‹ Videos](L)                  [🏠 Studio](L)
```

**Empty**: `P 📈 **Nothing measured yet.** Each published post is measured 1, 3, 7 and 28 days after it goes
out.` No chart, no table.

**Thin data** (1–3 videos): no chart, because a line through two points reads as a trend that is not there. The
table shows what exists and the footnote says when the chart starts: `_The chart starts at 4 videos; 2 so far._`

**A source did not answer**: its row keeps the last numbers with `_from ⟨r: 6 hours ago⟩_` in the footnote, and
the footer reads `Updated ⟨r⟩ · Facebook didn't answer`. A missing source never shows as zero.

### 7.8 Queue `[rich]` · 🏛️ Studio

**Queued**
```
H2   📋 Queue
P    14 queued · 9 ready to make
H3   Next up
P    **1. Derinkuyu Underground City** — ready, 12 photos
BTN  [Make it now](P)               [Move one up](·)
H3   After it · 13
OL   2. Dyatlov Pass Incident — ✅ ready
     3. Gol Gumbaz Whispering Gallery — ⚠️ short of photos, 4 of 6
     4. Odessa Catacombs — ⚠️ photos to review
     5. Singing Colossi of Memnon — ✅ ready
     6. Great Wall of Gorgan — ⏳ fetching photos
P    _+8 more_
BTN  [‹ Prev](✕)  [1 / 3](✕)  [Next ›](·)
HR
BTN  [Remove a topic](·)            [💡 Ideas](·)
BTN  [↩️ Restorable · 6](·)         [📮 Posted · 11](·)
BTN  [🏠 Studio](L)
F    Updated ⟨r⟩
```
- "Next up" drops its ✅: a mid-line emoji after a bold name broke §4.1, and the word "ready" carries it.
- "Move one up" and "Remove a topic" open number pickers; the removal itself confirms in `danger` (§6.5).
  "Several back to back: `/produce N`" leaves the footer for Help: code in small grey footer text is hard to
  read and impossible to tap.
- While a run is live: `[Making Derinkuyu…](✕)` replaces Make it now, and number 1 reads
  `⏳ in production`.
- Drop is never offered for the topic being made.

**Restorable**
```
H2   ↩️ Restorable
P    6 topics can go back in the queue
OL   7. Camp Century — rejected ⟨dt⟩
     9. Flannan Isle — failed, release gate
     12. Baltic Sea Anomaly — its last run is over
     …
BTN  [7](·) [9](·) [12](·) [15](·) [18](·)                 (tap → “Restore Camp Century?” confirm, success)
BTN  [‹ Queue](L)                   [🏠 Studio](L)
```

**Posted**
```
H2   📮 Posted
P    11 episodes · latest ⟨r: 2 days ago⟩
OL   1. Derinkuyu Underground City — ⟨dt⟩ · 8.2k views
     2. Nazca Lines — ⟨dt⟩ · 4.4k views
     …
BTN  [1](·) [2](·) [3](·) [4](·) [5](·)                    (→ that video's audience, §7.7)
BTN  [‹ Prev](✕)  [1 / 3](✕)  [Next ›](·)
BTN  [‹ Queue](L)                   [🏠 Studio](L)
```

**Empty**: `P The queue is empty. Add one with `/add Title` or find ideas with `/ideas research`.` plus
`[💡 Ideas](P)`.

### 7.9 Posting and slots `[rich]` · 🏛️ Studio

**Posting**
```
H2   🗓️ Posting
P    Next slot ⟨wt: Friday 6:00 PM⟩ · 3 episodes booked
H3   1. Nazca Lines
L    🎞️ Reel & Short — ⟨wt: Friday 6:00 PM⟩
     📱 4 Stories — right after the Reel
     🗂️ Carousel — ⟨wt: Sunday 12:00 PM⟩
H3   2. Derinkuyu Underground City
L    🎞️ Reel & Short — ⟨dt: 11/3/26, 6:00 PM⟩
     🗂️ Carousel — 48 h after its Reel
P    ⚠️ **Won't post:** Instagram token expires before this slot
BTN  [1](·) [2](·) [3](·)
HR
BTN  [🗓️ Slots](·)                  [📮 Posted](·)
BTN  [🏠 Studio](L)
```

**Episode**
```
H2   Nazca Lines
P    ⏳ **Booked** · Reel & Short ⟨wt: Friday 6:00 PM⟩
T[b c]  Reel & Short │ ⏳ ⟨wt: Friday 6:00 PM⟩
        Stories      │ ◽ After the Reel
        Carousel     │ ◽ ⟨wt: Sunday 12:00 PM⟩
        Booked       │ ⟨r: 3 hours ago⟩
HR
BTN  [Post now](S)                                          (→ confirm)
BTN  [Next free slot](·)            [Cancel booking](D)     (→ confirm)
BTN  [‹ Posting](L)                 [🏠 Studio](L)
```
- After a move, the slot reads `~~Friday 6:00 PM~~ → Saturday 12:00 PM` for 10 minutes.
- A failed Story or carousel shows `❌ Failed: 2 of 4 frames` and `[Retry Stories](P)`.

**Slots (the week)**
```
H2   🗓️ Slots
P    Reels at 6:00 PM, carousels at 12:00 PM · your time
T[b s c]  ‖h   │ Reel     │ Carousel
          Fri  │ ✅ Nazca │ —
          Sat  │ ◽ Free   │ —
          Sun  │ ◽ Free   │ ✅ Nazca
          Mon  │ ✅ #2    │ —
```
Cells name an episode by its number in Posting (`#1`, `#2`) when its short name would not fit; names are never
cut with "…" inside a table. Day names come from `⟨w⟩` entities.

### 7.10 Pictures `[rich]` · 🏛️ Studio

**Overview**
```
H2   🖼️ Pictures
P    ⚠️ **7 need you** · 4 to review, 3 short of photos
H3   To review
OL   4. Odessa Catacombs — 14 photos
     7. Hal Saflieni Hypogeum — 11 photos
H3   Short of 6 usable
OL   3. Gol Gumbaz Whispering Gallery — 4 of 6
     11. Lycurgus Cup — 2 of 6
BTN  [4](·) [7](·) [3](·) [11](·)
HR
BTN  [📎 Upload](·)                 [🎨 Covers](·)
BTN  [🏠 Studio](L)
```

**A photo set**
```
SLD  14 photos; caption per slide “Photo 3, Main ventilation shaft” — credit “Wikimedia Commons · CC BY-SA 4.0 · A. Kaya”
H2   Odessa Catacombs
P    ⚠️ **To review** · 14 photos, 9 usable
T[b c]  Topic    │ 5 photos
        Context  │ 9 photos
        Usable   │ 9, needs 6
        Privacy  │ ✅ No identifiable living faces
HR
BTN  [Approve set](S)
BTN  [Fetch more](P)                [Remove a photo](D)     (→ reply “3” via force_reply prompt)
BTN  [‹ Pictures](L)                [🏠 Studio](L)
F    Faces: prominent living faces left out; no identifiable child; historical subjects never blurred
```

### 7.11 Status and health `[rich]` · 🏛️ Studio

```
H2   🩺 Status
P    ✅ **All working** · Production, rc70
T[b c]  Schedule │ ▶️ Active, next ⟨r: in 6 hours⟩
        Worker   │ ✅ Idle
        Bot      │ ✅ @relicandriddle_bot
        Release  │ ✅ rc70 since ⟨dt⟩
        Backup   │ ✅ ⟨r: 5 hours ago⟩
H3   Providers
T[b s c]  ‖h         │ State     │ Used
          ElevenLabs │ ✅ On     │ 41%
          Azure      │ ✅ On     │ 12%
          Gemini     │ ⛔ Capped │ 100%
          Groq       │ ✅ On     │ —
H3   Accounts & issues
T[b c]  Instagram │ ✅ Connected
        Facebook  │ ✅ Connected
        YouTube   │ ⚠️ Token expires ⟨r: in 3 days⟩
        Issues    │ ⚠️ 2 open · 14 fixed
HR
BTN  [🔑 Accounts](·)               [🧭 Improvements](·)
BTN  [🧰 Jobs](·)                   [🛡️ Activity](·)
BTN  [🗒️ Notices](·)                [📊 Digest](·)
BTN  [📜 History](·)                [Pause runs](·)          (Pause → confirm; reads [Resume runs](P) while paused)
BTN  [🏠 Studio](L)
F    Checked ⟨r: just now⟩ · spend $18.40 of $60 this month
```
- The lede's verdict is computed: `⚠️ **2 things need a look**` when any row is ⚠️ or ⛔. Its second emoji
  (📡) left the line (§4.1); the environment is a word here and a 📡/🧪 on the dashboard.
- Pause lives here, not on the home grid (§7.1). While paused, the dashboard lede reads
  `⏸️ **Runs paused** · since ⟨r⟩` so a pause is never forgotten.
- The bot row is the effective identity (`getMe`), never the configured name (AGENTS.md).
- Width: "ElevenLabs" 10 + "⛔ Capped" 9 + "Used" 4 + 9 = 32. Round 0 confirms it; the fallback state word is
  "⛔ Off".

### 7.12 Help `[rich]` · 🏛️ Studio

```
H2   ❓ Help
P    Every screen is a tap away on the dashboard; these are the typed shortcuts.
T[b c]  `/produce 3` │ Make 3 episodes back to back
        `/add Title` │ Add a topic to the queue
        `/photos 4`  │ Open photo set 4
        `/deploy`    │ Check for a signed release now
D▸   All commands · 24
BTN  [🏠 Studio](L)
```

### 7.13 Confirmation `[rich]` · replaces the action zone of the card that asked · expires in 5 minutes

Only the blocks below the card's `HR` change (§6.5). Everything above stays, including the video.

**Irreversible, go** (Posting › episode)
```
…    (title, lede, table unchanged)
HR
P    **Post Nazca Lines now?** It goes out now instead of ⟨wt: Friday 6:00 PM⟩. This can't be undone.
BTN  [Keep the slot](L)             [Post now](S)
F    Expires ⟨r: in 5 minutes⟩
```

**Approve & post** (review card)
```
…    (video, title, lede, hook, table, Details unchanged)
HR
P    **Approve Nazca Lines?** The Reel and Short go out ⟨wt: Friday 6:00 PM⟩, then the Stories. You can undo until 15 minutes before.
BTN  [Not yet](L)                   [Approve & post](S)
F    Expires ⟨r: in 5 minutes⟩
```

**Reject, with its reason in one tap** (review card)
```
…    (unchanged above the HR)
HR
P    **Reject Nazca Lines?** Nothing is posted. Tap the main reason; it rejects it.
BTN  [Facts or wording](D)          [Hook or story](D)
BTN  [Pacing or length](D)          [Voice](D)
BTN  [Pictures](D)                  [Music or sound](D)
BTN  [Cover or text](D)             [Something else](D)
BTN  [Keep it](L)
F    Expires ⟨r: in 5 minutes⟩
```
- The eight reason buttons are each a confirming Reject, so they share the `danger` style; this is the one
  sanctioned exception to "≤ 1 danger per card", because they are one choice split eight ways. Round 0 checks
  that eight red buttons read as one choice and not as alarm; if they do not, the chips become default style and
  the question keeps the red meaning in its words.
- The escape is alone on the last row, link style, so a thumb that lands low never rejects by accident.
- Two taps, not four (Reject, confirm, chip, reply), and a rejection can never lack a reason.

**Make it again** (review card) follows the same shape: "**Make Nazca Lines again?** It rewrites from the script
step and costs about $0.40. Tap what to change." with chips `Tighten the hook`, `Shorter`, `Better pictures`,
`Different music`, `Just remake it` (all `primary`), then `[Keep it](L)`.

Other confirmations are a question and one pair: "Cancel this run?" ([Keep running](L) [Cancel run](D)),
"Remove Gol Gumbaz from the queue?", "Cancel the Nazca booking?", "Restore Camp Century?" ([Not now](L)
[Restore](S)).

Expiry reverts the action zone, and a later tap gets the toast "That question expired; nothing changed."

### 7.14 Details prompt `[plain]` · 🎬 Reviews · `force_reply` · silent · optional

`force_reply` must be set when the message is sent; it "can't be changed when the inline keyboard is edited"
(brief §B). So after a reason chip, one small optional message invites detail:
```
📝 <b>Rejected for: Pictures.</b> Reply here to add details, or leave it.
     force_reply: true
```
- It has no buttons: the reason is already recorded, so ignoring it is a complete answer.
- The operator's reply gets a ✅ reaction (`setMessageReaction`). There is no "thanks" message. The review card's
  stamp gains `· your note: “…”` (first 60 characters).
- It is deleted on reply, or after 2 hours (`REASON_CARD_HOURS`), and at once if Undo is tapped.
- The review card itself carries the decided stamp (§7.4).

### 7.15 Error and empty states

Pattern:
- **What is true:** state emoji + bold verdict.
- **What did not happen**, so you know nothing half-happened.
- **The one way forward:** a button, or a command in code.
- No apology, no blame, no stack trace on the face (logs go in a Details).

| Situation | Where | Card face |
|---|---|---|
| An action failed | The card that asked, edited | `P ❌ **Couldn't book the slot.** Instagram answered “token expired”. Nothing changed.` · `BTN [Open Accounts](P)` |
| A tap on an old card | Toast | "That card is out of date. The current one is pinned." |
| Two writers raced | Toast | "Updated a moment ago; tap again." |
| Rich send refused (parse or limit) | Same topic | The plain-HTML version of the card (today's text format), with footer text `Simplified view` |
| Telegram 429 | None | Silent hold (rc69). The card catches up on the next edit |
| A provider down | Needs you page (§7.2) | `⛔ Gemini unavailable` |
| Empty screens | Their card | One `P` that says what fills it and how: Queue, Posting ("Nothing booked yet. An approved episode is booked into the next free slot."), Audience, Pictures ("No photo sets yet. `/photos N fetch` gets them.") |
| Review video failed to upload | The review card | `⛔ **Video didn't upload** · nothing is lost`, Approve disabled as "Video missing", `[Send video again](P)` (§7.4) |
| A chart failed to render | Its screen | The table and lede stand alone; the footnote reads `_Chart unavailable; numbers are current._` No broken image, no placeholder |
| A source did not answer | Analytics | Last numbers kept with their age; never shown as zero (§7.7) |

**Long names.** Every episode has two names, both generic (topic-agnostic):
- **Full title** for `H2` (≤ 48 characters, two lines). A longer title is allowed a third line on the review card
  only; it is never cut with "…". Round 1 includes a 70-character title to prove it.
- **Short name** for one-line places (🔔 lines, outcome lines, lists, buttons, toasts): the title before its colon
  or dash ("Nazca Lines"), else its first words up to 18 characters ending on a whole word. Stored once with the
  topic, so every surface uses the same short name.

### 7.16 Studio sub-screens not drawn above

Music, Covers, Ideas, UNESCO marvels, Jobs, Notices, Activity, Accounts and Improvements use the same
skeleton:
- `H2` with the screen emoji, then a lede with a count.
- One table, or a list of ≤ 6 items with number buttons and a pager.
- The actions, `HR`, then the navigation row.

Their content is today's, re-set in these blocks. A screen whose content is a single list uses `OL` with number
buttons, never a table.

### 7.17 Installing a release `[rich]` · Studio dashboard state + 🗞️ Info line

**Ready to install** (dashboard lede, above Needs you)
```
P    📦 **rc71 is ready** · installs when nothing is running, waiting or due
```

**Installing** (the dashboard is edited just before the bot stops)
```
H1   Relic & Riddle
P    📦 **Installing rc71** · back ⟨r: in about 5 minutes⟩
P    _Taps wait until it is back; nothing is lost._
BTN  [Installing rc71…](✕)
F    Started ⟨t: 3:02 PM⟩
```

**Back** (first edit after restart; the banner stays 30 minutes, then the normal dashboard)
```
P    ✅ **Now on rc71** · installed ⟨t: 3:06 PM⟩
```

**🗞️ Info** `[plain]`, silent: `📦 rc71 installed · 3:06 PM · 4 changes` (the time as an entity).

A rollback reads `⚠️ **Rolled back to rc70** · rc71 failed its checks here` on the dashboard, and the same as a
**🔔 Needs you** line, because it needs a decision.

### 7.18 Info cards `[rich]` · 🗞️ Info · silent · weekly

**Policy watch**
```
H2   ⚖️ Policy watch
P    Week of ⟨D: October 26, 2026⟩ · 2 changes
H3   ⚠️ Branded content tools
P    Meta now requires the paid-partnership label on reels that show a sponsor's product. ⟨dt⟩
P    _Impact: none today; we carry no sponsors._
H3   ✅ Reels length
P    Reels up to 3 minutes count for recommendations. ⟨dt⟩
P    _Impact: the deep-dive cut could post as a Reel._
D▸   Sources · 2
       L  transparency.meta.com ↗ · about.fb.com ↗
F    Checked ⟨r: just now⟩
```
With no changes: `P ✅ **No new Meta policy changes** this week.` and the footer only.

**Weekly digest**
```
H2   📊 Weekly digest
P    ⟨D: October 20, 2026⟩–⟨D: October 26, 2026⟩
IMG  1200×900: episodes made vs posted per day, and views a day, 7 days
     caption: “Six episodes made, five posted; views up 18% on last week.”
T[b c]  Made        │ 6 episodes
        Posted      │ 5
        First-time  │ 4 of 6 approved
        Median run  │ 31 min
        Spend       │ $14.20
        Views       │ 18.4k, +18%
H3   Went well
L    Derinkuyu: 4.1k views, best of the month
     Every run passed the rights check
H3   To look at
L    ⚠️ Two runs short of photos when making the video
D▸   Every episode · 6
BTN  [📈 Audience](·)
F    rc70
```

**Settings check**
```
H2   ⚙️ Settings check
P    ⚠️ **1 setting differs** · 14 of 15 as expected
T[b c]  Spending cap │ ✅ $60 a month
        Voice        │ ✅ ElevenLabs, licensed
        Dry run      │ ✅ Off (production)
        Posting      │ ⚠️ Carousel slot 12:00, expected 13:00
D▸   All settings · 15
F    Values shown without secrets · checked ⟨r⟩
```

When the next card of a kind arrives (a week later, beyond the 48-hour deletion window), the previous one is
edited down to one line, `P ⚖️ Policy watch · week of ⟨D⟩ · superseded`, so Info reads as a tidy history.

### 7.19 The ruler (round 0 only) `[rich]` · 🏛️ Studio · deleted after screenshots

```
H2   Ruler
P    1234567890123456789012345678901234567890123456789
T[b c]  cells of 4, 6, 8, 10, 12, 14 characters × 2, 3, 4 columns (one table each)
H1 … H6   “Derinkuyu Underground City” at each size
PQ   the Nazca hook                       P _the Nazca hook_ (italic) — compare
P    ⟨r⟩ · ⟨t⟩ · ⟨wt⟩ · ⟨dt⟩ · ⟨D⟩ for the same moment
P    🏛 vs 🏛️ · 🗓 vs 🗓️ · ⚠ vs ⚠️ (VS16)
P    ==marked== · ~~struck~~ · `code`
BTN  [Link style](L) [Default](·) [Primary](P)
BTN  [12345678](·) [12345678](·) [12345678](·)
BTN  [1234567890123456](·) [1234567890123456](·)
```

### 7.20 History `[rich]` · 🏛️ Studio · from 🩺 Status and Queue › 📮 Posted

Decided review cards leave 🎬 Reviews 6 hours after the decision (operator decision, 29 Oct). Their full record
lives here, so Reviews holds only what waits and today's decisions, and nothing is ever lost.

**List** (newest first, 6 a page)
```
H2   📜 History
P    23 decisions · 18 approved, 5 rejected
BTN  [All ✓](·)        [Approved](·)       [Rejected](·)          (filter; the current one carries ✓)
OL   1. ✅ Nazca Lines — posted ⟨D⟩ · Instagram, Facebook, YouTube
     2. ❌ Camp Century — rejected ⟨D⟩ · Pictures
     3. ✅ Derinkuyu Underground City — approved ⟨D⟩ · posts ⟨wt⟩
     4. 🔄 Dyatlov Pass Incident — made again ⟨D⟩ · “tighten the hook”
     5. ✅ Gol Gumbaz Whispering Gallery — posted ⟨D⟩ · Instagram, Facebook
     6. ❌ Flannan Isle — rejected ⟨D⟩ · Facts or wording
HR
BTN  [1](·) [2](·) [3](·) [4](·) [5](·) [6](·)                → that decision's full card
BTN  [‹ Newer](✕)  [1 / 4](✕)  [Older ›](·)
BTN  [‹ Status](L)                  [🏠 Studio](L)
F    Updated ⟨r⟩
```
- Each row: state emoji (✅ approved or posted, ❌ rejected, 🔄 made again), the episode's short name (§7.15),
  then one detail chosen by outcome: posted → the local date and platforms; approved → the slot; rejected → the
  reason chip; made again → the operator's note (≤ 24 characters, whole words).
- Dates are `⟨D⟩` entities, so they render in the operator's local format; within 6 days they use `⟨w⟩`.
- Filters are a 3-up row of default buttons; the active one ends in ✓ (a text check, not ✅, which is a state).
  Filter and page survive the 15-minute return to the dashboard only until it fires.
- Six number buttons in one row breaks the 5-up rule (§6.2) by one; round 1 checks them at ≥ 44 pt each. If
  they squeeze, a page holds 5.

**A decision, reopened** (number tap; the Studio card becomes the record)
```
VID  the review proxy, from its stored file_id (no upload)
H2   Nazca Lines: The Runway Paradox
P    ✅ **Posted** ⟨D⟩ · approved by you ⟨D⟩
T[b c]  Reel & Short │ ✅ Posted ⟨D⟩ · Instagram ↗
        Stories      │ ✅ 4 posted
        Carousel     │ ✅ Posted ⟨D⟩ · Instagram ↗
        Views        │ 4.4k so far
D▸   Script · 52 s, 142 words
D▸   Captions & first comment
D▸   ⚠️ Worth a look · 2
D▸   Facts & sources · 14
HR
BTN  [📈 Its audience](·)           [📸 Instagram post ↗](↗)
BTN  [‹ History](L)                 [🏠 Studio](L)
F    rc70 · run `a3f9…` · 📡 Production
```
- It is the review card's own content, re-assembled from the run record, minus the action rows. A rejected one
  shows `❌ **Rejected** ⟨D⟩ · Pictures` and the operator's note, and offers `[Restore topic](P)` when the topic
  is still in Restorable.
- **Media rule:** the Studio card is a rich message, so gaining the Video block is allowed (§8). Going back to the
  list needs the video dropped; until round 0 proves a rich edit can drop a Video block, "‹ History" sends a
  fresh Studio card and deletes this one (the dashboard pin moves with it).
- If the proxy file is gone (older than its retention), the card opens without video and its lede adds
  `· video no longer kept`.
- **Empty:** `P 📜 **No decisions yet.** Every approve or reject lands here, with its video and reason.`

---

## 8. Motion and lifecycle

Telegram has no animation a bot controls. "Motion" here means what changes, where, and when. Every edit goes
through the card store's compare-and-set (v2 §6) and is debounced to ≥ 1.2 s per card (rc69 429 hold).

| Card | Topic | Created | Edited in place | Replaced | Removed | Pinned | Notifies |
|---|---|---|---|---|---|---|---|
| Studio dashboard and its screens | Studio | First start | Every state change (2 s debounce) and every tap; back to dashboard after 15 min idle | When > 40 h old, at the next quiet moment (new message + pin, old deleted) | Old copy at replacement | Yes | No |
| Needs you card | Needs you | First start | Item added, handled, page turned | > 40 h | Old copy at replacement | Yes | No |
| New-item line | Needs you | Each new item | Never | — | When handled, or at 40 h | No | **Yes** |
| Review card | Reviews | Each review | Edit, check re-run, new cut, decision, confirmation zone, undo | Send it again (new card; old deleted if < 48 h, else edited to `Superseded · see below`) | 6 h after any decision (operator, 29 Oct); its record lives in 📜 History (§7.20). Past 48 h it cannot be deleted and is edited to a one-line stamp `✅ Approved ⟨D⟩ · in Studio › History` | No | No (the 🔔 line rings) |
| Details prompt | Reviews | After a reason chip | Never | — | On reply, on Undo, or after 2 h | No | No |
| Progress card | Runs | Run start | Each step and every 60 s | — | 30 min after done, failed or cancelled | While live | No |
| Outcome line | Runs | Run end, each posting | Never | — | Never (the log) | No | No |
| Confirmation | (the asking card's action zone) | On tap | — | Reverts after 5 min | — | — | No |
| Info cards | Info | Weekly or on event | Superseded → one-line stamp | — | Never | No | No (silent) |
| Install notice | Studio + Info | Deployer status | Ready → installing → back | — | Banner after 30 min | (the dashboard is pinned) | No |
| Toasts | — | On tap | — | — | — | — | No |
| Design-round mocks | Their topic | Round N | — | — | After screenshots, via `deleteMessages` (≤ 100 ids), always < 48 h | No | No |

Rules:
- **Media rule (v2):** a card may gain media and never needs to lose it. Media cards are edited with
  `editMessageCaption` / `editMessageMedia`.
  - Whether an `editMessageText` with new rich content can drop a Video block is a round 0 test.
  - Until it is proven, decided review cards keep their video.
- **Pins:**
  - Exactly one pinned message in Studio (the dashboard), one in Needs you (its card), and one in Runs while a
    run is live (its progress card, unpinned at the end).
  - Reviews and Info pin nothing.
  - Pins are silent in private chats (confirmed). If a "pinned" service line ever appears, it is deleted at once.
- **The decided stamp** is always the first block after the title, and it replaces the action rows. A decided
  card has no action buttons, except Timed out (Resend, Reject) and the timed Undo (§6.5).
- **Cleanup audit:** daily, the card store's message ids are compared with what each topic should hold. A
  temporary message past its expiry is deleted. A live card missing from the chat is redrawn. Every deletion
  happens before 46 h, logged, never in bulk without ids.
- **Order of a run's end:**
  1. The progress card is edited to Done (silent).
  2. The review card arrives in Reviews (silent).
  3. The outcome line is sent in Runs (silent; the run log).
  4. The Needs you card updates, and **last**, its new-item line is sent: the one notification. Sending it last
     means a tap on the notification lands after the review card already exists.

  One event, one ping (§1.4). The operator may also mute 🎬 Reviews, ⚡ Runs and 🗞️ Info in Telegram's per-topic
  settings and lose nothing; the Help card says so in one line.

---

## 9. Device review and iteration

### 9.1 Global checklist (every mock, every round)

Devices: the iPhone in its normal theme at the default text size and one size larger; the Mac app in a narrow
window (≈ 900 px) and a wide one. Each screenshot is checked for:

1. **Hierarchy:** the title, then the verdict, read in under 2 seconds; one obvious main button.
2. **Width:**
   - no mid-word wrap in any table cell
   - no unit alone on a line
   - no button label cut with "…"
   - nothing truncated on the iPhone
3. **Rhythm:** no block pair that looks like a gap or a collision (divider next to heading, two paragraphs that
   should be one); one divider.
4. **Icons:**
   - every emoji is in the §4.2 set and drawn in colour (VS16)
   - one emoji per line
   - no double icon in topic tabs
5. **Colour:** at most one success and one danger, never adjacent; disabled reads as disabled; the meaning
   survives in greyscale (iOS Settings › Accessibility › Colour Filters › Greyscale screenshot).
6. **Times:** every time is an entity, in local format, at the right distance format, never longer than about
   17 characters.
7. **Media:** video poster, play, scrub, sound; the chart is sharp and labels are legible at arm's length; the
   caption states the trend.
8. **Mac parity:** nothing sparse or stretched; Details and buttons behave the same; side-bar badges right.
9. **Notification:** what the lock screen or banner shows (a new message only), and the per-topic badge.
10. **VoiceOver** (once per round, on two cards): reading order follows block order; no emoji noise; buttons
    named by their verb.

### 9.2 Round 0: calibration (before round 1)

| Test | Settles |
|---|---|
| Portrait video height inside a rich block (iPhone) | §7.4 face budget (does the client cap a 9:16 player's height?) |
| The "edited" mark on a rich card edited in place, with only Buttons blocks vs with an inline keyboard | Whether live cards show "edited"; if they do and an inline keyboard suppresses it, the navigation row moves to `reply_markup` |
| VoiceOver on the review card and the dashboard | §4.2 names (✅ "check mark button"), reading order, buttons named by their verb |
| Largest Telegram text size on the review card | §2.5 budgets degrade by wrapping, never by cutting |
| Ruler mock (§7.19) | The character budgets in §2.5 and §3.3, heading sizes, PQ vs italic hook, link-style look, VS16 rendering, `marked`/struck rendering |
| Video poster, three variants: (a) `cover` as an uploaded JPEG `file_id`; (b) `start_timestamp: 1`; (c) the proxy's first frame encoded as the cover frame | The iPhone dark placeholder (variant (c) changes the proxy, so it is checked for one frame only, no visible flash) |
| Topic tabs with and without a topic icon sticker | §4.3 icon rule |
| Topic recreation in the right colours (dev) | §4.2 |
| The run-end sequence: exactly one sound, from 🔔 Needs you; badges on the silent topics | §1.4, §8 |
| Rich edit that removes a Video block | §8 media rule |
| `date_time` inside a button label and a table cell | §2.4 |
| Chart variants (a) widget panel and (b) transparent with halos, each on iOS light, iOS dark and Mac dark; (b) first checked for flattened alpha | §5.2 (operator picks in round 1) |

### 9.3 Per-mock checks (what to look at)

| Mock | iPhone | Mac |
|---|---|---|
| 7.1 Dashboard (idle, live, needs you, installing) | Masthead serif weight; the env line is unmistakable (dev vs production); key–value values wrap at words; 5-row grid not taller than one screen with the lede | Table does not stretch; grid buttons equal width |
| 7.2 Needs you (1, 4 and 7 items, empty) | The first item and the "Also waiting" list read in one glance; number row fits; empty state reassures | Badge clears when empty |
| 7.3 New-item line | Lock-screen preview whole, topic named | Side-bar badge on Needs you only |
| 7.4 Review (waiting, blocked, edited, decided ×4) | Poster; the title right under the video; PQ size; 7-row table wraps cleanly; Details open and close smoothly; Approve full width and green; Reject far from Approve; copy glyphs inside Details | Video frame poster; inline play; Details behaviour |
| 7.5 Progress (running, slow, done, failed, cancelled) | Stage list alignment of emoji; ◽ vs ▫️ in dark mode; "3 min so far" moves each minute; lede one line; footer ≤ 2 lines | Pinned bar in the Runs topic |
| 7.6 Outcome line | ≤ 40 characters whole on lock screen | Runs reads as a log |
| 7.7 Analytics (3) | Chart legible without zoom; table no wrap ("YouTube long"); number buttons 5-up | Chart not pixelated at the wide window |
| 7.8 Queue (+ Restorable, Posted) | Ordered list numbers align; disabled "Making…" reads why | — |
| 7.9 Posting, episode, slots | Slots table width; struck old slot | — |
| 7.10 Pictures (overview, set) | Slideshow swipe, captions, credit line size | Slideshow arrows |
| 7.11 Status | Providers table width; computed verdict | — |
| 7.13 Confirmation | Only the action zone changes and the scroll does not jump; eight reason chips read as one choice, not alarm; Keep it alone on the last row; expiry counts down | Same |
| 7.14 Details prompt | Reply box opens by itself (`force_reply`); ignoring it is fine; reaction on reply | Same |
| 7.15 Errors | Toasts readable; simplified view footer | — |
| 7.17 Install | Disabled single button; back banner | — |
| 7.18 Info ×3 | Silent arrival; superseded stamp | Badge without sound |
| 7.20 History (list, filters, reopened approved, reopened rejected, empty) | 6 number buttons fit; ✓ on the active filter; reopened video plays from file_id | Same |
| Review card at the largest text size | Table values wrap at words; no button label cut | — |

### 9.4 Iteration protocol

1. **Mock.** The round's cards are built from this document by a script outside the repo
   (`C:\tmp\tg_proto\design_round.py`, modelled on `proto.py`).
   - It aborts unless `getMe` returns `8701645452` / `relicandriddle_dev_bot`. Production is never used.
   - Every card's footer reads `TEST — design round N · mock 7.4b`.
   - An index line goes to 🗞️ Info, silently: `TEST — design round N: 31 mocks`.
   - Buttons are inert (`dsg:*` callbacks answered with the toast "Design round: buttons are inert").
2. **Screenshots.** The operator takes iPhone and Mac screenshots. They are kept as half-size JPEGs under
   `docs/reviews/telegram-cards-<date>/round-N/<mock>-<device>-<theme>.jpg`, with a README of measurements
   (characters per line, wraps found, poster variant) and before/after pairs for every change.
3. **Critique.** Each mock is scored against §9.1 and §9.3: every item passes or fails, with the failing pixel
   named. The fixes go into this document first (a dated "Round N changes" note at its end), then into the
   script.
4. **Revise and resend** as round N+1.
   - The previous round's messages are deleted with `deleteMessages` before 48 h.
   - Test topics are left in place on dev.
5. **Stop rule.** Stop when a full round produces no accepted change: every check passes on both devices, and
   neither the operator nor the critique finds a gain. Then the system is frozen and implementation (v2 §8 steps
   1–5) builds from this document.
   - A gain found later reopens a round. It is never patched in code alone.

---

## 10. Decisions and open questions for the operator

Decided by the operator on 29 Oct:
1. **Theme:** follow the device theme everywhere. Native blocks already do; charts are compared in round 1 as
   (a) widget panel (recommended) vs (b) transparent with halos (§5.2).
2. **Decided review cards** leave 🎬 Reviews 6 hours after the decision; the record lives in 📜 History (§7.20).
3. **Premium:** not now. Custom icons are a mock-only comparison in round 1 (§4.3).

Still open:
4. **`protect_content` on review cards.** Recommended off (§7.4): in your own chat it only stops you saving or
   forwarding a cut for a second opinion. Say if you want it on anyway.
5. **Approve confirmation.** With a 15-minute-before-slot Undo (§6.5), the Approve confirmation could be dropped
   to make approval one tap. Recommended: keep it for round 1, and decide after using Undo for a week.
6. **Eight red reason chips** (§7.13). Round 1 shows them red and default; pick the one that reads calmer.

---

## Critique round 1 (29 Oct 2026)

Independent UI/UX critique against the "elite 10/10" bar, with the operator's 29 Oct decisions folded in.

| Change | Why |
|---|---|
| Only 🔔 Needs you rings; review card and outcome lines are silent; the 🔔 line is sent last (§1.4, §7.3, §7.6, §8) | One run end produced up to three pings for one event |
| Review card lede is the verdict ("Ready to approve · 2 notes to check"); hook moves under it (§7.4) | Principle 2 was broken: the verdict was in the table's first row |
| Four ✅ check rows collapse to "Checks ✅ All 4 passed"; 7 Details → 6, ordered by decision need; cost to Run details; "Findings" → "Worth a look" (§7.4) | Repeated icons, VoiceOver noise and face length under a ~590 pt portrait video |
| Confirmations replace only the action zone, not the card (§6.5, §7.13) | Whole-card flips lost the video (contradicting the media rule), the context, and the scroll position |
| Reject = Reject + reason chip (2 taps); detail reply optional (§7.13, §7.14) | Was 4 steps across two messages; a rejection could lack a reason |
| Undo approval until 15 min before the slot; Undo reject for 10 min (§6.5, §7.4) | Confirmation stops slips, nothing forgave them |
| Needs you shows the first item plus a numbered list; pager and "Next group" retired; empty state names the next run (§7.2) | Backlog size was hidden; reaching item 4 took up to 5 taps |
| Dashboard: Health folds into a verdict Details; Pause moves to Status; "Start next episode"; footer drops "daemon ✅" (§7.1, §7.11) | 15 blocks to 13; accidental-tap risk; jargon; mid-line emoji |
| Plain verbs: Make / Try again / Redo with a note; no "render/regenerate" on buttons (§6.3) | The operator is non-technical |
| ▲ ▼ → "+12%" / "−8%" / "flat" (§2.4) | VoiceOver reads triangles as "black up-pointing triangle" |
| Chart labels 34 → 44 px, ticks 28 → 38 px; small multiples 4:5; charts follow their title; no dual axis (§3.4, §7.7) | 34 px was 9.4 pt on an iPhone, under Apple's 11 pt floor; 4:3 rows were ~80 pt tall |
| Chart theme: two variants with exact palettes, halos and measured contrast on five chat surfaces; "dark by default" removed (§5.2) | Operator decision; (b) flagged for JPEG alpha flattening |
| 📊 Status → 🩺; 📰 Digest → 📊; 📜 Policy watch → ⚖️ so 📜 is History; ⬆️ 🗑️ removed; Telegram-glyph clash audit; ◽ vs ▫️ test (§4.2) | Two chart glyphs side by side; two newspapers; History needed 📜; off-set emoji |
| Mid-line second emoji removed from ledes (Pictures, Status, Settings, Queue, review "Made" line) (§2.2) | §4.1 one-emoji-per-line was broken in five mocks |
| Progress lede drops "58%"; "3 min so far" replaces m:ss; footer to ≤ 2 lines (§7.5) | Third fact, false precision, seconds frozen between 60 s edits |
| New 📜 History screen and reopened-decision card (§7.20) | Operator decision |
| Edge cases: short-name rule, 70-character title, missing video, failed chart, silent source, thin data (§7.7, §7.15) | Not specified |
| `protect_content` dropped (open question); `link_preview_options.is_disabled` on plain lines | It blocked the operator's own saving/forwarding; stray previews |
| Round 0 adds: portrait video height, the "edited" mark, VoiceOver, largest text size, run-end single ping (§9.2) | Unverified assumptions that decide layout |

## Operator critique, 29 Oct (photo-sheet card): hide, grey or show — and rebuild with what Telegram offers

The photo sheet for one topic showed Approve on an approved set, "Copy keep/drop command" buttons, a Keep with
nothing to keep (all photos Topic-tier), no Fetch, and confusing lines ("Topic 11" beside "/photos 8"; "11 of 5
usable"). Not a rule to apply blindly: each action is decided per state, using the features now available.

**Hide, grey or show (every card):**
- **Hide** an action that does not belong to the state (an approved set: "✅ Approved · <time>" + Review again, no
  Approve; a topic out of the queue: "Back to the queue", no photo actions).
- **Grey** (`disabled`) an action that becomes possible after one operator step, so the button teaches the flow and
  the layout never jumps: "Drop selected (0)" greyed until a photo is tapped, then red "Drop selected (2)".
- **Grey with its reason in the meta line** an action blocked for now ("⏳ Fetching… 2 min", "Approve · after the
  photo check").
- **Show** what is possible now; one primary action per card, its colour by meaning.

**Photo card, rebuilt (to prove in round 0):**
1. A **Slideshow** block of the full-size photos replaces the contact sheet; each slide captioned number · tier ·
   licence · what it shows.
2. A **Collage of four, then a Buttons block** of their numbers (①–④), repeated: select right under the photo; a
   selected number shows ☑ and changes style.
3. **Keep** exists only when weak or unchecked photos exist, and only those numbers are selectable for it.
4. **Fetch** by need: primary "Fetch 5 more" when short, quiet "Fetch more" otherwise.
5. **"Type numbers"** (`force_reply`) for large sets ("3 7 9-12"); no copied commands anywhere.
6. **Undo** for a few minutes after a change, instead of a confirmation (dropping photos is reversible).
7. One verdict line ("11 usable · 5 needed · ✅ Approved <tg-time>"), details (licences, credits, quality) folded.
8. **Copy buttons** only for text pasted elsewhere (caption, hashtags, a run id for support) or long lists.
9. One identifier per thing: the topic's number is never shown beside a photo count; "11 usable · 5 needed".

**Same treatment, card by card (each gets its own hide/grey/show table in its mock):** Queue (tap a topic → its
actions; multi-select with greyed "Drop selected (0)"), Posting/bookings (Move, Cancel with confirmation, Post now;
greyed while a post is in flight), Music tracks (no Keep on a kept track), Review card (decided stamp, no stale
buttons; Approve greyed with its reason while a check runs, hidden when the gate blocks), Restorable/Posted (tap to
restore; posted topics ask first), ideas and UNESCO marvels, uploads (topic and licence as buttons), improvement
proposals (paged in Needs you).

## Settled with the operator, 29 Oct

- **Forward/save protection: off.** Review videos and albums are sent without `protect_content` (the operator saves
  or forwards a review video to check it elsewhere; the chat has one member). Shipped with rc72.
- **Approve & Post: one tap + Undo.** "✅ Approved · posts at <tg-time>" with Undo until 15 minutes before the slot;
  no confirmation. **Post now** (publishes immediately, irreversible) keeps its confirmation.
- **Reject: two taps.** The red Reject swaps the buttons area for neutral reason buttons (Pictures · Script · Voice ·
  Music · Length · Facts · Other) and "← Keep it"; a reason rejects at once, records the reason, Undo for 10 minutes.

## Round 0 results (operator's iPhone + Mac, 29 Oct)

- **Review video:** a Video block inside a rich message drops `cover` and `start_timestamp`, and plays muted
  inline on its own (iPhone and Mac). A **plain video message** (sendVideo with `cover`) shows the real cover and
  waits for a tap. Decision: each review is a pair — (1) the video as its own message, cover as poster, carrying the
  main decisions in its inline keyboard; (2) the rich card directly under it (title, verdict, hook quote, facts
  table, folded notes, secondary actions). After the decision the video's caption becomes the stamp ("✅ Approved ·
  posts at <time> · Undo") and the rich card shrinks to one line; both leave Reviews after 6 h (kept in 📜 History).
- **Button weight:** the same style renders **solid** in an inline keyboard under a message but only **tinted**
  inside a rich Buttons block (Mac and iPhone). Main decisions (Approve & post, Cancel run, Reject, Post now) go in
  the inline keyboard; quieter actions stay inside the card. "Link" and "Default" styles look identical on Mac — never
  carry meaning by style alone.
- **Times:** `date_time` entities render live and local on iPhone; the Mac app shows their fallback text. The
  fallback is always the real time written out ("Thu 29 Oct, 4:31 PM MDT"), never a placeholder word.
- **Emoji:** always the emoji presentation (⚠️ with VS16, not ⚠); 🏛 and 🏛️ render alike.
- **Tables:** 3 and 4 columns read cleanly at Mac width with no split words (iPhone rule stays ≤ 4, ≤ 3 by default).
- **Charts:** inside a rich card (Photo block + one-sentence caption) clearly beat a plain photo. Fix: the "0" label
  collided with the first date — drop or offset it.
- **Progress card:** the stage list with times and a live detail line is right. Drop the List bullets (each stage
  already has ✅/⏳/⬜); square markers one size.
- **Photo card:** interleaved Collage + number row works, but the number-to-photo mapping is ambiguous with uneven
  collage layouts — print the number on each photo (as the old sheet did). Too long as slideshow + two collages:
  keep one full-size Slideshow (numbered captions) plus one compact numbered grid for selection. Greyed "Drop
  selected (0)" next to a blue "Fetch more" reads as intended.
- **Pin:** silent, no service line in the topic; unpin prototypes so only real cards are ever pinned.
- **Needs you:** one line per item, one ping, its own unread badge — as designed.
- **Topic rename service lines:** renaming a topic posts a line; topics are created once and never renamed.
