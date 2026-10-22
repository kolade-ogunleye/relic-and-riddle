# Telegram operator fixes: development retest

Branch `fix/telegram-operator-panels`; draft PR #2, based on the runtime candidate.
Production is unchanged. Development flags, credentials and spending caps remain
separate. Automated checks do not replace these operator acceptance checks.

| Finding | Implemented behavior | Operator retest |
| --- | --- | --- |
| False Facebook/Instagram warning | Validate Upload-Post HTTP/profile responses, invalidate old cache, recognize server-selected Page | Dashboard → Check accounts → Dashboard |
| Covers leave an image above Dashboard | Replace media panel with text and remove the obsolete media message; ordinary text screens edit in place | Covers → Dashboard; Music review → Dashboard; no unrelated thumbnail/audio remains |
| New cards for account/research completion | Background jobs update their originating panel only while it is current; otherwise results remain in the relevant menu | Start research, stay for completion; repeat and navigate away; open Waiting ideas |
| Backend commands in music instructions | Find instrumental CC0/CC BY tracks by project mood, listen, Keep/Remove, browse approved library | Music → Find new → choose mood → Review waiting → Keep/Remove → Dashboard |
| Separate image commands/confusing instructions | `/images` has topic-specific fetch, upload and review; old aliases remain supported | `/images 1`, `/images fetch 1`, `/images upload 1`, `/images review 1` |
| Manual image upload is difficult | Guided photo/document, source URL, licence, licence URL and author prompts; per-operator durable state | Upload an image you may use; cancel one upload; finish another and review it |
| Missing cancellation | `/jobs` lists research/fetch/account jobs and producing runs; Cancel job or confirmed Cancel run | Cancel a free image fetch; inspect Jobs; test run cancellation only on a disposable approved validation run |
| Clumsy lists | Queue pages, topic-picker pages, approved-music pages and categorized help | Queue → Next/Previous; Images topic pages; Music approved pages; Help categories |
| `/clear` missing | Restored to autocomplete and help; existing review-preserving cleanup retained | `/clear`; verify pending human-review cards survive |
| Stale progress | Process creation identity distinguishes interrupted/unverified workers; historical buttons cannot select a different run | `/progress`; compare run ID and last recorded update |
| Swipe reply context | Run references from buttons; general cards use quoted context and current records | Reply to a run card, then Topic ideas; confirm different context |
| Wrong Make #1 | Display actual next serial and reject outdated queue-start buttons | Compare Dashboard/Queue; change queue before tapping an older start button |

## Navigation and presentation

Telegram's ordinary HTML messages do not offer arbitrary font sizing. Short
screens, clear headings and bounded pages keep these controls usable. Switching
from photo/audio to plain text requires a replacement message; the old media
message is deleted only after the text replacement succeeds. If deletion fails,
its buttons are retired. The logical panel continues on the replacement.
Explicit historical `done:` buttons still close their original card.

A completed job does not overwrite a screen the operator has subsequently
opened or send an extra completion card. Open Jobs to see its state; research
results remain in Waiting ideas and downloads remain in the relevant media menu.
Job failures are recorded separately from completion. Interrupted jobs are not
silently restarted after process restart.

## Uploads and cancellation

Guided uploads are isolated by chat and operator and can resume via Upload my
image or `/images upload N`. Cancel removes only that pending staged file.
Late downloads and stale licence/cancel buttons cannot revive or change another
upload. Files are limited to 20 MiB and validated as images. Finished uploads
retain source/licence/author evidence and start unreviewed. PD/CC0/CC BY policy
is unchanged; neither fetching nor uploading approves assets. Image changes are
refused while their topic has a producing run. Fetch uses existing curated
searches, without silently widening licences. New partial fetch results stay
unreviewed even when cancelled; existing approved/rejected evidence is retained.

Cancellation is cooperative. A provider request or parallel stage already in
flight may finish and incur its normal charge. Further work stops at the next
checkpoint. Job cancellation preserves completed downloads and existing assets;
it does not undo them. Run cancellation is accepted only while producing, has a
confirmation screen, records `cancelled`, and clears active progress when the
worker acknowledges it. It cannot cancel a publishing upload, infer its outcome,
or erase paid usage/publication evidence. Requeue is an explicit later action.

## Command compatibility audit

The native command menu and categorized `/help` use `src/telegram_commands.py`.
`/clear` and `/jobs` are public. `/images` is the canonical image command;
`/fetchimages N` and `/image N` remain compatibility aliases. The old `/image`
photo-caption format with pipe-separated rights evidence remains supported.
`/music fetch [mood]`, `/music review`, `/music approved` expose the music menu
operations; numeric `/music N` still opens that pending track. `/queue all` and
`/queue full` now open bounded queue pages.

`/start` still opens Dashboard. Historical decision/navigation callbacks remain
for existing cards: removing their implementation would strand pending reviews.
They are not all separate slash commands in autocomplete. Legacy UNESCO
`/discover` can still send its older individual discovery cards; Dashboard →
Topic ideas → UNESCO uses the compact paged panel. `/undo` supports the last
queue archive in the current process; music deletion does not claim to be
undoable. Hidden implementation callbacks are not a second public command menu.
Offline tests do not claim to have exercised paid generation or publication.

## Remaining production gates

Human acceptance of this dev bot, reviewed release promotion preserving prod
configuration, Vulkan upscaler acceptance, configured licensed generation, then
full dry run and human approval under `rr-pipeline$`. Archive validation data
while retaining usage/publication evidence before explicit live activation.
See `docs/runtime-acceptance.md` for the Vulkan/lab handoff. Dev changes alone
cannot repair production's older bot or progress records.

## Verification record: 2026-10-22

Windows full suite after clean-start/presentation changes: **711 passed**.
Connection retry/reload guard regressions also passed (94 focused checks). Tests block real network services.
Development task was reloaded, its bot identity verified as `8701645452`
(`@relicandriddle_dev_bot`), and the live 33-command menu contains Clear, Jobs,
Images and Music with old image aliases omitted from autocomplete. `.env` hash
was unchanged across reload. No production tasks, configuration or bot were
changed. Human chat retests above remain pending.

A live Covers retest exposed an interrupted TLS connection during media editing.
Existing-message edits now retry up to three attempts and rewind attached file
streams. New message sends are not retried after ambiguous network errors.
Failed navigation offers a named Try again action on the current panel.
Successful live Covers retest is still required; retries do not guarantee network
availability.

An earlier development reload interrupted a research job because the stop guard
checked producing runs only. Its existing records are retained and Jobs reports
its dead owner as interrupted. The development stop guard now also refuses to
stop live/unverifiable operator jobs and publishing runs. No research was
replayed automatically; a request already sent may have incurred provider usage.

## Clean start and shared presentation

`/clear` leaves only the normal Dashboard, without a separate cleanup receipt or
long deletion-policy banner. Pending pipeline review cards and genuinely active
progress remain protected. `/start`, `/dashboard` and startup reuse a persisted
Dashboard anchor; returning from media updates that anchor to its replacement.
A deleted/uneditable anchor is replaced safely. Telegram still limits what old
messages a bot can delete; deleting history in the Telegram client remains a
client operation.

All HTML text notifications, panels and photo/audio captions pass through
`src/telegram_presentation.py` at the API boundary: short bold sentence-case
heading, blank line, body; no stacked underline styling or ornamental divider
lines. Protected code/quoted evidence, case-sensitive IDs, licence details and
action metadata are retained. Captions/text remain within Telegram limits.
Review/approval actions keep their existing meaning. Startup state appears in
the Dashboard body rather than introducing a second heading above the brand.

Retest `/clear` then `/start`: one Dashboard should remain. Repeat after opening
Covers and returning; compare Queue, Music, Images, Help and notifications for
consistent heading/spacing. Ordinary notifications do not acquire extra buttons;
interactive panels retain context actions and Dashboard navigation.
