# Relic & Riddle

*Relics and riddles of history, in 45 seconds.*

Researches, fact-checks, voices, renders and publishes short documentary Reels for Facebook and Instagram
in the "Historical Curiosities & Ancient Marvels" niche, with a human approval step before anything goes live.

```
topic → assets check → script (Claude + web search) → fact check (Claude + web search)
      → voice → ambient bed → thumbnails / Stories / carousel → master render
      → Telegram or CLI approval → publish (Upload-Post, else Instagram Graph API)
```

## Design principles

- **Fail closed.** No script source, a failed fact check, missing topic images, no approval, or no publisher
  stops the run and records why. Nothing is approved or "published" by default.
- **Honest outcomes.** A run ends as `published`, `simulated` (dry run), `failed`, `rejected`, `timeout`, or
  `publish_unknown`. Only `published` counts toward a topic's history.
- **No duplicate posts.** Upload-Post calls carry an `Idempotency-Key` (the run ID), so a retry after a timeout is safe.
  If a publish may have gone through (timeout mid-publish, server error, partial success),
  failover stops and the run is marked `publish_unknown` for a human to check.
- **One source of truth.** All state lives in `data/pipeline.db` (SQLite, WAL mode, foreign keys on).

## What each stage does

| Stage | Module | Notes |
|---|---|---|
| Topic rotation | `src/db.py`, `src/topic_engine.py` | Never-produced topics first, then failed ones (retry), then least-published. Human-rejected topics wait for `topic requeue`. Topics stuck in production for more than 6 hours are recovered. |
| Scene media | `src/visual_engine.py`, `src/credits.py` | Requires `assets/<topic_id>/` with 5 or more images (optional `hook_motion.mp4`). The shared Cappadocia photos are used only for the Derinkuyu topic. Images fetched from Wikimedia Commons are used only after a person approves them, and CC BY credits are added to the caption automatically. |
| Script | `src/script_engine.py` | Claude (`claude-opus-5-5`) researches with web search and returns schema-validated JSON (`src/models.py`): narration, caption, hashtags, thumbnail, Story and carousel copy, and sources. Falls back only to a curated `data/scripts/<topic_id>.json`, which can be published only after a person has checked it and set `"_reviewed_by_human": true` (dry runs may use it unreviewed). If a fact check fails, the draft is revised (up to twice) using the findings. If Opus is overloaded or unavailable, calls fall back to `CLAUDE_FALLBACK_MODELS`. Every call's cost is recorded, with an optional monthly cap (`CLAUDE_MONTHLY_BUDGET_USD`). Exhausted credit stops the run and alerts you, because every Claude model draws on the same account credit. |
| Guardrail | `src/script_engine.py`, `src/fact_checker.py` | Structural checks (95–115 words, loop-safe ending, caption length, slide counts, pseudo-archaeology tropes), then an independent Claude pass that extracts every claim and checks it with web search. Passes only if every claim is supported with a source, or disputed but already hedged in the script. |
| Voice | `src/audio_engine.py` | ElevenLabs with character timestamps if configured, otherwise edge-tts with word boundaries. |
| Render | `src/video_composer.py` | 1080×1920, 30 fps, H.264 CRF 18, TV-range yuv420p, AAC 48 kHz, `+faststart`. Anton 92pt karaoke captions above the Meta UI safe zone. Loudness measured and limited to −14 LUFS with true peak at or below −1.5 dBTP. |
| Stills | `src/visual_engine.py`, `src/multi_format.py` | Variant B cover (9:16 and 1:1), 3 Story frames, and a 7-slide carousel. Text is auto-fitted. Stories and carousels are for **manual** posting; the poll is added as a native sticker (see `companion_posting.json`). |
| Approval | `src/telegram_bot.py` | Sends the cover, a 720p review copy (a few MB, under Telegram's upload limit) and the review card. Only `TELEGRAM_APPROVER_IDS` can decide. The CLI can decide too; the first decision wins. If nobody decides before the timeout, the run times out. |
| Publish | `src/publisher.py` | Upload-Post (Facebook + Instagram) first, then the Instagram Graph API resumable Reels upload, which posts the first comment. |
| Ops | `src/ops.py` | Masks secrets in every log line (including Telegram tokens inside URLs), runs pre-flight checks, and prunes scratch audio older than 7 days. |

## Setup

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt      # Windows; use .venv/bin/python elsewhere
cp .env.example .env                                          # then fill in credentials
python main.py health --live                                  # shows anything still missing
```

FFmpeg/ffprobe must be on PATH.

## Commands

```bash
python main.py test                              # end-to-end dry run on Derinkuyu; publishes nothing
python main.py run [--topic-id ID] [--dry-run]   # one cycle, waits for approval
python main.py run --auto-approve                # skips review (recorded as "auto-approve-flag")
python main.py daemon --time 09:00               # daily at that time in DEFAULT_TIMEZONE

python main.py review list
python main.py review approve|reject|regenerate <run_id>   # while a run is waiting
python main.py review publish <run_id> [--dry-run]         # publish a finished, unpublished run

python main.py topic next | list | requeue <topic_id> | repair
python main.py assets list | approve <topic_id>
python scripts/fetch_commons_images.py [--topic-id ID] [--allow-share-alike]
python -m pytest -q
```

## Telegram operations

While `python main.py daemon` runs, the bot also:

- **Alerts** you when a run fails, an approval times out, publishing fails or its outcome is unknown, and when a Reel goes live.
- **Sends a daily heartbeat** at `HEARTBEAT_TIME`. Set `HEALTHCHECK_PING_URL` (a free healthchecks.io check) to be emailed if the heartbeat stops, for example if the PC is off.
- **Answers commands** from approvers only, in your chat: `/status`, `/next`, `/pause`, `/resume`, `/run` (a run still needs your approval before anything posts), `/help`.
- **Reviews image sets**: `/images` lists the sets, `/review N` sends topic N's numbered contact sheet with an **Approve set** button, and `/drop N 2 5` removes wrong images.

- **Checks dependencies weekly** (Mondays): outdated pinned packages and known vulnerabilities (`pip-audit`). **Apply updates** installs minor/patch and security fixes on a git branch and merges only if every test passes, otherwise rolls back. Major versions are left for manual review. CLI: `python main.py update [--apply]`.

`python main.py bot` runs only the Telegram side (commands, image review, approvals), with no scheduled runs. Use it to review images before switching the daemon on.

## Adding a topic

1. Add it to `data/topics/topic_bank.json`. New entries are picked up on the next start, and existing state is never overwritten.
2. Scene images, either:
   - your own licensed images (5 or more) in `assets/<topic_id>/`, played in filename order; or
   - `python scripts/fetch_commons_images.py --topic-id <id>`, which uses the searches in `data/topics/image_queries.json`.
     It keeps only public-domain, CC0 and CC BY files whose titles match the topic keywords (CC BY-SA is opt-in, because
     its share-alike terms can extend to the finished video). It records author and licence in `credits.json` and writes
     `contact_sheet.jpg`. Look at the sheet, delete any wrong images (re-running the script refills the gaps), then run
     `python main.py assets approve <topic_id>`. Keyword matching still lets through false positives, such as a rock band
     called "Greek Fire", which is why approval is required.
3. With `ANTHROPIC_API_KEY` set, the script and fact check are automatic. Without it, the topic can only run
   from a curated `data/scripts/<topic_id>.json` that includes sources.

## Known limitations

- The live publishing integrations (Upload-Post, Instagram Graph) are written against the current documentation and
  tested with mocks, **not against live accounts**. Do one supervised `run` before enabling the daemon.
- The Instagram Graph fallback publishes to Instagram only and uses the default cover frame
  (its custom covers require a public `cover_url`).
- Fetched Commons image sets are all **unreviewed**, and some topics don't yet have enough matching images
  (check with `python main.py assets list`). Wikimedia rate-limits anonymous clients heavily; set `COMMONS_CONTACT` to speed this up.
- The Derinkuyu curated script was drafted by Claude and is marked unreviewed. Review it before publishing.
- `archive/` holds the earlier prototype scripts and media for reference. Nothing in `src/` uses them.
