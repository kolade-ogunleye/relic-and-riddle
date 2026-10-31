# Telegram release review — 2026-10-22

**Verdict: NO-GO for release promotion and a full managed-account production
episode/approval validation.** The implementation has substantial working
features, but current approval, lifecycle and failure-handling defects prevent
certifying it as complete, accurate or free of stale actions. No numeric quality
score or “no further improvement possible” certification is supported.

Reviewed application commit: `784bdaa76e8d3c07018550a660a29f025cf571a6`.
This is an audit of that commit, not a claim that the findings below are fixed.
The audit did not change running application code, credentials, feature flags,
spending caps, production tasks or lab controls. Existing fact-sheet/music/eval
work is excluded. The production deployment remains a separate environment.

## Scope and method

Reviewed command registration/dispatch, all command families and callback paths,
approval storage/coordinator wiring, process/restart behavior, progress and chat
cleanup, navigation, media delivery, guided/legacy uploads, research/music jobs,
licence-review mutations, account summaries, notifications, formatting and reply
context. Primary files are `src/telegram_bot.py`, `src/control.py`,
`src/telegram_commands.py`, `src/telegram_presentation.py`,
`src/operator_jobs.py`, `src/image_upload.py`, `src/telegram_images.py`,
`src/accounts.py`, `src/probe.py`, plus their DB, coordinator, credits, music,
topic-proposal and improvement integrations.

Evidence:

- A fresh full-suite run at the reviewed commit: **711 passed** on Windows.
- AST/dispatch inventory: **33 public menu commands, none without a dispatch
  implementation**. [commands.csv](commands.csv) records the public inventory.
  Dispatch presence does not prove correct or safe behavior.
- **17 reproduced regression observations** in [observed.json](observed.json).
  The [reproducer](reproduce.py) uses temporary databases/files, fake Telegram
  responses and blocked `requests.get/post`. It made no provider or Telegram
  requests and did not generate or publish content.
- Source inspection for additional concurrency, responsiveness, lifecycle and
  compatibility gaps. These are explicitly distinguished from reproduced cases.
- Contract checks against the official [Telegram Bot API](https://core.telegram.org/bots/api).
  In particular, callback data has a 64-byte limit, media captions have their own
  length limit, and standard `getFile` downloads are limited by the hosted API.

Run the historical reproducer from the repository root:

```powershell
.\.venv\Scripts\python.exe docs\reviews\telegram-2026-10-22\reproduce.py
```

This script reports observations; exit 0 means collection completed, **not** that
the application passed an acceptance gate. It is not part of the passing pytest
suite. After remediation, turn the scenarios into tests of the required safe
behavior and update this report rather than preserving the bugs as expectations.

## P1 — release blockers

### F01. Old review messages can approve a newer review request

Source: [callback handling](../../../src/telegram_bot.py#L761),
[approval DB](../../../src/db.py).

Reproduced: the approval row points to message **202**; clicking Approve on old
message **101** changes the decision to `approved`. Authorization and first-wins
SQL work, but neither binds the decision to the current message/review revision.
Removing old buttons is best-effort transport behavior, not an acceptance fence.
Reopening a timed-out approval makes this especially relevant.

Required closure: atomically compare run, review revision, current message and
expiry when recording a decision. Test old-card taps after resend, copy edits,
timeout/reopen, duplicate delivery and concurrent approvers. Reject obsolete
cards without approving or generating another stale notification.

### F02. A copy edit can race with approval and keep approval for changed copy

Source: [edit_copy](../../../main.py#L737).

Reproduced: approval is recorded while `_verify_final_copy` is in progress; the
edit then writes a new caption while the approval remains `approved`. The method
checks pending state before verification and commits afterward without an atomic
review-version condition. The publisher can race these operations.

Required closure: serialize/version copy edits and decisions; invalidate the old
review before committing changes. An approval must cover the exact public copy
and artifacts presented. Test edit-versus-approve with deterministic barriers,
including publisher/backup/scheduler observations of the winning version.

### F03. Image approval is not bound to the set the operator reviewed

Source: [send_sheet](../../../src/control.py#L569),
[imgok](../../../src/control.py#L1192), `src/credits.py`.

Reproduced: show **5 images**, add another file/credit, then tap the old button;
the handler approves **6 images**. The callback identifies only the topic.
Fetch/upload use an image lock, but approval/drop/swap do not share one consistent
mutation protocol. Those legacy actions also bypass the producing-topic guard.

Required closure: bind the review to a digest/revision of image contents, credits
and storyboard, and use the same per-topic lock/conditional mutation for every
image operation. A changed set requires a new review. Test concurrent fetch,
upload, drop, swap, approval and production consumption.

### F04. Approval is offered even when review media failed to arrive

Source: [send_approval_request](../../../src/telegram_bot.py#L642).

Reproduced: `_send_review_media` raises; a new Approve card is still sent and its
pending approval recorded. Missing/oversized video can similarly leave a card
without a usable video preview. A valid rendered artifact on disk is not evidence
that the human received it.

Required closure: track successful delivery of required review media, or require
an explicit, recorded out-of-band review path. Do not present a normal approval
action while delivery is incomplete. Test partial albums, failed video upload,
oversized/missing proxies, resend failures and explicit recovery.

### F05. Command consumption is not durable across restart

Source: [poll_once](../../../src/telegram_bot.py#L1352).

Reproduced: the same `/run` update is dispatched by two bot instances recreated
against the same database. `_offset` exists only in memory; Telegram acknowledges
updates on a subsequent offset request. A crash between a side effect and that
acknowledgement can replay a recent paid/destructive command. A timestamp cutoff
does not provide command idempotency.

Required closure: durable inbox/command identity with explicit dispatch and
recovery semantics. Test restart before enqueue, after enqueue, during execution,
after side effects and before update acknowledgement. Recovery must not silently
repeat paid work. Do not describe this as exactly-once behavior without a
corresponding transactional design.

### F06. Topic path resolution can escape the configured assets root

Source: [_resolve_topic_dir](../../../src/control.py#L55), legacy footage path.

Reproduced: `../outside` resolves to a sibling directory containing credits.
Absolute paths and junction/symlink resolution also lack a canonical-root fence.
Footage ingestion uses this resolver before writing its destination. This
requires an authorized operator path; no unauthenticated exploit was demonstrated.

Required closure: resolve only known topic IDs/explicit serials, enforce canonical
containment under configured roots, and reject absolute/drive/UNC/traversal and
escaping link targets. A missing numbered topic set must not fall back to a
different directory's position. Test Windows and portable path cases.

### F07. Old queue controls can modify topics that are no longer queued

Source: [qdrop](../../../src/control.py#L603), DB topic mutations.

Reproduced: an old `qdrop` action archives a topic marked `in_production`. Queue
start has a stale-selection check, but Drop/Bump/Requeue do not provide equivalent
state fencing. This can conflict with production and final topic bookkeeping.

Required closure: make each mutation conditional on allowed current state, check
the affected-row result, and refresh stale panels without pretending the action
succeeded. Test old controls during production, publication and subsequent runs.

### F08. Progress lifecycle can erase another run or create duplicate cards

Source: [update_progress_card](../../../src/telegram_bot.py#L932),
[finish_progress_card](../../../src/telegram_bot.py#L1030), `/clear`.

Two reproduced observations: finishing run A clears the active-progress record
for run B; an arbitrary failed edit produces a new progress message. The latter
includes temporary connection failures, not just deleted messages. `/clear` also
deletes/recreates active progress from the bot process, changing ownership and
message identity while the production worker can continue editing its old ID.

Required closure: fence writes/clears by run and worker generation, keep the
authoritative current message identity, and recreate only a confirmed missing
message. Late updates from closed workers must not resurrect progress. Test
clear/update/finish races, worker restarts, timeout versus missing-message errors,
and preserving the original worker identity and elapsed time.

### F09. Callback payloads are not guaranteed to meet the API limit

Source: cover/topic callback builders and `_api`.

Reproduced: a valid long run directory name produces a **99-byte** cover callback
against the API's **64-byte** limit. No universal encoder/validator exists.
The current latest dev cover happened to use 48 bytes; this is an input-dependent
defect, not a claim that every existing button is currently invalid.

Required closure: durable compact references and UTF-8 byte validation for every
callback builder, including legacy cards and reply-context decoding. Do not
truncate identifiers. Test long/Unicode topic, run, proposal and media identities
and decoding after restart.

### F10. Cover controls are not reliably scoped or truthful

Source: [cover selection](../../../src/control.py#L726),
[ccov](../../../src/control.py#L918), `flip_cover`.

Reproduced: a nonexistent run returns `✅ Cover updated`; a run whose DB status is
`published` still has its cover rewritten and success reported. Source inspection
also shows the Master Review Covers button opens the latest run rather than the
reviewed run, fallback images can retain a Variant B label/cache key, and a
preview click can have different commit behavior depending on message media.

Required closure: bind controls to the reviewed run, enforce exact output-root and
variant validation, make preview/commit semantics explicit, preserve closed-run
artifacts, and report success only after the requested operation succeeds.
Test overlapping reviews, absent variants, closed runs and stale controls.

### F11. Unknown publisher health can be reported as ready

Source: [accounts.problems](../../../src/accounts.py#L74), `status_line`.

Reproduced: with no Upload-Post publisher and Meta configured but
`valid=None, error=Timeout`, `problems()` returns an empty list. The status renderer
uses an empty problem list to claim readiness. Cached readiness is also not
explicitly invalidated when stale or configuration changes.

Required closure: distinguish healthy, unhealthy, unknown and stale results;
include check time and configuration identity. Configuration alone must not
become a successful health check. Test timeout, malformed responses, expired
cache, provider switching and partial service failure.

### F12. Control-plane responsiveness is not bounded

Evidence: source inspection of `poll_once`, `SLOW_COMMANDS`, `cstat`, media send/
edit methods, legacy image/footage/evidence uploads and topic-add actions.

Several callbacks execute file/network/provider/model work directly in the sole
update consumer. For example, Full status calls ElevenLabs quota and Azure auth;
media edits/upload paths can wait through network timeouts. Approval and Cancel
taps cannot be handled by this process until that work returns. Legacy footage/
evidence paths also lack the guided image flow's consistent bounds, validation
and temporary-file commit policy.

Required closure: one lightweight update consumer, bounded background jobs and
outbound scheduling for blocking work, deadlines, and one validated ingestion
path. Test approval/cancel/navigation responsiveness during deliberately slow
uploads, quota/auth failures, rate limits and model calls. Mocked transport tests
must verify the poller can process a second update before the slow job finishes.

### F13. Review/notification text can assert facts not established by state

Source: `format_card_text`, `_handle_callback`, `imgcancel` and status helpers.

Reproduced: an empty payload produces `100% (Pass …)` integrity text without
audit evidence; cancelling image review says the set remains unapproved even
when `reviewed=True`. Approval acknowledgement also claims queued publication
before delivery/scheduling has actually succeeded, including validation contexts.

Required closure: render unknown evidence as unknown, derive outcomes from
persisted state, and distinguish approval recorded, simulated, scheduled,
publishing, published and delivery unknown. Test each state and recovery path.

## P2 — quality and hardening gaps

### F14. Shared formatting does not cover albums and every fallback

Reproduced: `format_payload('sendMediaGroup', ...)` leaves its caption's underline/
uppercase style unchanged. `_album` builds these captions separately; generic
plain-text fallbacks also have different formatting behavior. Existing targeted
panels improved, but the claim of universal presentation coverage is too broad.

Required closure: normalize all supported caption containers, preserve code/
evidence exactly, and apply limits after formatting. Test albums, standalone
media, edits, error fallbacks, Unicode and near-limit HTML. Review long-list
truncation explicitly; some diagnostics still have count limits without pages.

### F15. Legacy paths, aliases and mutation storage need a single contract

Source inspection: canonical image prompts coexist with a separate synchronous
`/image` caption uploader; `/discover` retains multi-card behavior and `uskip`
only dismisses a view, without persistent exclusion. `/critique` and `/bug` are
mentioned in an upload comment but are not implemented aliases. Callback action
names are also accepted as slash-handler commands rather than having a declared
public/compatibility boundary. Historical cleanup tracks message IDs in memory.

Topic/proposal JSON writes are multi-step and not protected by a shared atomic
transaction/lock. Music download/review locking is better, but its short filename
suffix keys are not checked for collisions. These are remaining quality gains,
not proof of a missing route for one of the 33 public menu commands.

Required closure: explicit supported aliases and retired-action responses,
shared ingestion/mutation services, durable cleanup semantics, idempotent Add/
Skip/Keep/Remove, and collision-safe media identity. Preserve working historical
decision recovery without letting obsolete buttons bypass current validation.

### F16. Job/error observability and retention are incomplete

Source inspection: jobs persist an exception type but no actionable sanitized
failure detail and no durable completion/delivery outcome. `recent()` reads and
sorts all job settings; the job and panel/alias collections have no explicit
retention policy. A malformed callback or corrupt wizard state can abort the
rest of a poll batch until the next retry. Several callbacks are acknowledged
only after work completes.

Required closure: per-update fault isolation, prompt acknowledgements, sanitized
root-cause logging and operator recovery instructions, bounded queues/history/
navigation state, and observable notification delivery failures. Test large
history, malformed updates, corrupted state, cancellation races and notification
failure after the job itself succeeded. Never prune paid usage/publication data
as part of Telegram UI retention.

### F17. Replies to run cards can lose their run context

Reproduced: a replied card containing a full `run_context_example` Run ID but no
remaining buttons produces “No run was selected.” Context extraction relies on
a subset of callback prefixes; accepted/expired cards lose those buttons.
`probe_system`'s explicit-run detection also differs from `resolve_run`.

Required closure: persist message-to-run/review identity, preserve it after
decision/edit/restart, and unify run parsing. General cards must continue to
avoid choosing an unrelated latest run. Test live, accepted, expired, edited,
media and historical cards, plus named nonexistent runs.

## What is working and should be preserved

- All 33 public commands have a dispatch implementation; the menu/help catalog is
  shared and Clear/Jobs are registered.
- Authorization checks restrict normal commands/callbacks/uploads to the
  configured chat and approver IDs. No unauthenticated bypass was demonstrated
  in this review.
- Pending approval decisions use atomic first-wins SQL. Its scope needs review-
  version fencing; the existing first-wins protection should remain.
- Guided images keep rights evidence, start unreviewed, isolate operators, and
  reject late downloads/stale wizard buttons. The remaining image-review race
  is a separate weakness.
- Process creation identities improve stale-worker detection. Cooperative
  cancellation and managed spend records should be preserved during fixes.
- Existing-message network retries rewind files; new-message sends are not
  blindly replayed after an ambiguous timeout.
- Development/production credentials and caps remain separate. Infrastructure
  acceptance, Vulkan and Wazuh ownership are separate from this review.

## Remediation and release order

1. Close approval/review identity and immutability defects first: F01–F04 and F10.
   Include media delivery and edit/publish races in the same review model.
2. Close command replay, path/mutation safety and progress fencing: F05–F09.
3. Close readiness/status accuracy and poller responsiveness: F11–F13; then finish
   F14–F17 so the public UX and operational contracts are coherent.
4. Add the new negative/concurrency/restart scenarios to the normal regression
   suite. Run full checks, validate the actual Telegram message shapes/limits,
   then perform operator chat acceptance with the dev bot.
5. Re-review the resulting commit. Only then consider release promotion and a
   managed-account full production validation, while keeping production's own
   environment and existing caps. Live activation remains a separate instruction.

**Do not mark these findings fixed because the existing 711 tests pass.** This
report identifies tests that were missing; it does not claim exhaustive absence
of additional defects or declare the running candidate production-ready.

## Remediation status (2026-10-22, after the audit)

The findings above describe commit `784bdaa`. The remediation that followed changed the
behaviour each scenario exercised, so the historical reproducer now stops at the first
refused action (for example, image approval while a topic is producing). Required safe
behaviour is now asserted by regression tests, mainly in
`tests/test_telegram_release_safety.py`. Full suite: 731 passed on Windows.

| Finding | Closure | Regression test(s) |
| --- | --- | --- |
| F01 old cards approve newer reviews | Decision bound to run, current message, revision and expiry (`decide_telegram_review`) plus the delivered-review fingerprint | `test_current_delivered_review_only_and_first_wins`, `test_changed_or_unverified_review_cannot_approve` |
| F02 edit races approval | Copy commit is a compare-and-swap on the review revision that invalidates the old card in the same transaction | `test_copy_verification_racing_decision_does_not_commit` |
| F03 image approval not bound to the reviewed set | Approval carries a digest of images, credits and storyboard; topic lock shared by image mutations | `test_changed_image_snapshot_is_not_approved` |
| F04 approval offered without delivered media | No approval card unless the review media for this exact copy was delivered | `test_preview_failure_never_offers_approval` |
| F05 command replay after restart | Durable at-most-once admission per bot and update id before any side effect; an interrupted command is retried explicitly from /jobs, never silently | `test_restart_does_not_replay_run_and_bad_update_does_not_abort_batch` |
| F06 topic path escape | Known topic ids/serials only, canonical containment under the assets root | `test_topic_paths_and_numeric_missing_assets_fail_closed` |
| F07 stale queue controls | Conditional mutations on current state; bump/requeue restore paths corrected (requeue refuses only queued/in-production) | `test_queue_and_progress_stale_actions_are_fenced`, `test_next_topic_prefers_queued_then_failed_and_never_repeats_published` |
| F08 progress lifecycle | Clears scoped to their run; stale progress of a finished run is discarded | `test_queue_and_progress_stale_actions_are_fenced`, `test_progress_of_a_finished_run_is_not_kept_alive` |
| F09 callback length | Universal encoder: data over 64 bytes becomes a stored compact reference that survives restart | `test_compact_callbacks_survive_restart`, `test_approval_button_uses_short_key` |
| F10 cover controls | Bound to the reviewed run; closed or missing runs never report success | `test_published_and_missing_cover_never_report_success` |
| F11 unknown health shown as ready | Healthy, unhealthy, unknown and stale are distinct | `test_health_unknown_stale_and_incomplete_integrity_never_pass` |
| F12 poller responsiveness | Navigation and slow commands run beside a lightweight consumer; decisions stay inline | `test_approval_is_handled_while_slow_navigation_runs`, `test_slow_commands_run_beside_the_poller` |
| F13 text asserts unestablished facts | Unknown evidence renders as unknown; approval badge says delivery is not yet confirmed; dry-run progress is explicit | `test_health_unknown_stale_and_incomplete_integrity_never_pass`, `test_dry_run_progress_is_explicit` |
| F14 album formatting | Albums use the shared formatter and caption limit | `test_album_uses_shared_formatter_and_caption_limit` |
| F15 legacy paths and aliases | Typed commands limited to the public catalog plus declared aliases; retired buttons (`done`, `umore`, `umoredone`) answer explicitly and never run old behaviour; UNESCO discovery is one paged panel with persistent Skip; Add is idempotent; topic-bank writes are journalled; collision-safe music keys | `test_mcancel_navigation_and_umoredone_cleanup`, `test_control_discover_umore_and_duplicate_uadd`, `test_add_appends_to_bank_queries_and_db`, `test_music_keys_do_not_collide_on_old_suffix` |
| F16 job observability and retention | Masked failure detail and delivery outcome per job; finished-job history bounded; per-update fault isolation | `test_failed_job_keeps_a_masked_reason_and_history_is_bounded`, `test_restart_does_not_replay_run_and_bad_update_does_not_abort_batch` |
| F17 reply context | Message-to-run identity stored with the card, so replies keep their run after buttons are gone | `test_reply_to_a_decided_card_keeps_its_run`, `test_native_telegram_reply_to_message_routing` |

Still required before promotion (unchanged from the order above): operator chat
acceptance with the dev bot against these behaviours, then a re-review of the
resulting commit. Tests asserting the safe behaviour are evidence, not certification.
