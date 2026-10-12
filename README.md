# Autonomous Meta Reels Pipeline

Produces and publishes short documentary Reels for Facebook and Instagram in the
"Historical Curiosities & Ancient Marvels" niche, with a human approval step before anything goes live.

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
| Scene media | `src/visual_engine.py` | Requires `assets/<topic_id>/` with 5 or more images (optional `hook_motion.mp4`). The shared Cappadocia photos are used only for the Derinkuyu topic. |
| Script | `src/script_engine.py` | Claude (`claude-opus-5-5`) researches with web search and returns schema-validated JSON (`src/models.py`): narration, caption, hashtags, thumbnail, Story and carousel copy, and sources. Falls back only to a human-curated `data/scripts/<topic_id>.json`. |
| Guardrail | `src/script_engine.py`, `src/fact_checker.py` | Structural checks (95–115 words, loop-safe ending, caption length, slide counts, pseudo-archaeology tropes), then an independent Claude pass that extracts every claim and checks it with web search. Passes only if every claim is supported with a source, or disputed but already hedged in the script. |
| Voice | `src/audio_engine.py` | ElevenLabs with character timestamps if configured, otherwise edge-tts with word boundaries. |
| Render | `src/video_composer.py` | 1080×1920, 30 fps, H.264 CRF 18, TV-range yuv420p, AAC 48 kHz, `+faststart`. Anton 92pt karaoke captions above the Meta UI safe zone. Loudness measured and limited to −14 LUFS with true peak at or below −1.5 dBTP. |
| Stills | `src/visual_engine.py`, `src/multi_format.py` | Variant B cover (9:16 and 1:1), 3 Story frames, and a 7-slide carousel. Text is auto-fitted. Stories and carousels are for **manual** posting; the poll is added as a native sticker (see `companion_posting.json`). |
| Approval | `src/telegram_bot.py` | Sends the cover, video and review card. Only `TELEGRAM_APPROVER_IDS` can decide. The CLI can decide too; the first decision wins. If nobody decides before the timeout, the run times out. |
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
python -m pytest -q
```

## Adding a topic

1. Add it to `data/topics/topic_bank.json`. New entries are picked up on the next start, and existing state is never overwritten.
2. Put at least 5 licensed images that depict it in `assets/<topic_id>/`. They play in filename order.
3. With `ANTHROPIC_API_KEY` set, the script and fact check are automatic. Without it, the topic can only run
   from a curated `data/scripts/<topic_id>.json` that includes sources.

## Known limitations

- The live publishing integrations (Upload-Post, Instagram Graph) are written against the current documentation and
  tested with mocks, **not against live accounts**. Do one supervised `run` before enabling the daemon.
- The Instagram Graph fallback publishes to Instagram only and uses the default cover frame
  (its custom covers require a public `cover_url`).
- Only Derinkuyu currently has scene images. The other 29 topics fail fast until their `assets/<topic_id>/` folders exist.
- `archive/` holds the earlier prototype scripts and media for reference. Nothing in `src/` uses them.
