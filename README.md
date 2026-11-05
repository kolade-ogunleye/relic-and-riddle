# Relic & Riddle

*Relics and riddles of history, in 45 seconds.*

A documentary production pipeline that researches, fact-checks, voices, renders and publishes short vertical Reels (9:16) about historical curiosities and ancient marvels, with an optional 16:9 documentary cut (a work in progress, paused in production by `VIDEO_FORMATS`: see `docs/documentary-rebuild.md`). Approved Reels can also be uploaded as YouTube Shorts (`YOUTUBE_SHORTS`). Every on-screen and spoken claim is traced to quoted evidence in a verified fact sheet, every asset carries provenance and rights evidence, and nothing posts until a human approves it in Telegram.

```
Evidence (UNESCO registry + Wikipedia + OpenAlex scholarly works)
    │
    ▼
Fact sheet (quoted evidence per fact, source tiers, hedges for disputed figures)
    │
    ▼
Script (written from the fact sheet, every claim trace-checked, spoken length measured)
    │
    ▼
Voice (Azure Speech or ElevenLabs, licensed; edge-tts only where no licence is needed)
    │
    ▼
Pictures (sourced photos, optional Kling motion hook, 2.5D parallax, original graphics)
    │
    ▼
Render (NVENC or x264 H.264, word-timed captions, -14 LUFS / -1.5 dBTP mastering)
    │
    ▼
Quality audit + release gate (re-measured master, rights, provenance, loudness)
    │
    ▼
Human approval (Telegram review card or CLI)
    │
    ▼
Publishing (Upload-Post, then Instagram Graph API, then Facebook Page Reels)
    │
    ▼
Posting slots (Reels at 12:00 and 19:00, Stories 45 min later, carousels Tue–Thu)
```

---

## Core design principles

- **Claims trace to evidence.** A fact sheet holds each fact with the quoted passages that support it and a source tier. Dates need a strong source or two independent sites; numbers need a primary source or two sites. Disputed figures carry a neutral hedge ("by one estimate"). The script is written from the sheet, and a trace check rejects any sentence whose claims the sheet doesn't support. If a sheet is too thin, the run skips to the next topic.
- **Two cost tiers.**
  - **Free (`SCRIPT_ENGINE=free`, `VOICE_PREFER=azure`, `MEDIA_MONTHLY_BUDGET_USD=0`):** Gemini free tier on its own key from a project with billing off (a `gemini-3.8-flash` writer checked by `gemini-3.7-flash`), Azure Speech F0, local parallax and original graphics.
  - **Paid options:** Claude (`claude-opus-5-5`, with `claude-fable-5-1` as the independent second check), ElevenLabs on a paid plan, and Kling motion clips, all under monthly spend caps.
- **Fail closed.** A real run uses only commercially licensed voices (`VOICE_REQUIRE_LICENSED=true`). Missing assets, unverified claims, unlicensed media or a failed audit block release, and the release gate re-measures the master at publish time.
- **Publishing is never doubled.** An OS-owned upload lock, an atomic claim and idempotency keys prevent duplicate posts. An unknown outcome (a timeout, or a 5xx at the final publish step) stops failover rather than risking a second post.
- **One source of truth.** SQLite in WAL mode (`data/pipeline.db`) holds topics, runs, approvals, spend and posting slots.

---

## Production pipeline

| Stage | Module(s) | What it does |
|---|---|---|
| **Topic rotation** | [`src/topic_engine.py`](src/topic_engine.py), [`src/db.py`](src/db.py) | Picks the next topic: operator-bumped topics first, avoiding two topics of the same theme in a row, then unproduced before failed, then a keyword-based priority score (VPS), then fewest publications. Recovers runs stuck in production for 6 hours. |
| **Evidence & fact sheets** | [`src/fact_sheet.py`](src/fact_sheet.py), [`src/unesco.py`](src/unesco.py), [`src/free_evidence.py`](src/free_evidence.py) | Gathers quoted evidence from the UNESCO World Heritage registry (matched by distinctive name, not geography alone), Wikipedia and OpenAlex (journal articles only rank as scholarly). Sheets are versioned by rules (`RULES_VERSION`), and a sheet built under older rules is rebuilt. |
| **Script & trace check** | [`src/script_engine.py`](src/script_engine.py), [`src/trace_check.py`](src/trace_check.py) | Writes 105–115 word, five-beat scripts from the fact sheet. Before any voiceover, it speaks the narration once with the narrator voice; a draft that would run long or short is sent back with a concrete fix. Rejected sentences are revised surgically. |
| **LLM access** | [`src/llm.py`](src/llm.py), [`src/free_llm.py`](src/free_llm.py), [`src/gemini.py`](src/gemini.py) | Claude via the Anthropic SDK (web-search research, structured output, per-task effort, model fallback chain, monthly budget) or Gemini (free engine). Vision checks fail rather than silently answering without the image. |
| **Voiceover** | [`src/audio_engine.py`](src/audio_engine.py), [`data/voice/`](data/voice/) | The channel's own designed ElevenLabs voice ("Relic & Riddle Narrator", `eleven_v4`) with Azure Speech as the licensed fallback, in the order set by `VOICE_PREFER`, with word timings. Dry runs use the free Azure voice unless `--paid-voice` is given. Each real voiceover calibrates the script stage's length check to the voice's pace. |
| **Music & sound** | [`src/music.py`](src/music.py), [`src/sound_plan.py`](src/sound_plan.py), [`src/audio_engine.py`](src/audio_engine.py) | Approved CC0/CC BY library tracks (credited in captions) or ElevenLabs Music, else a synthesized drone. A sound plan marks the narration's reveals, tension, calm and described actions; code validates it, and the bed places licensed effects (`data/sfx/`, each with rights evidence) and shapes the music's swells and dips from it. Without a model, effects fire on the words that name them. |
| **Motion** | [`src/motion.py`](src/motion.py), [`src/parallax.py`](src/parallax.py) | Optional Kling "living photograph" hook (locked camera, subject unchanged), checked for warping (homography) and for rewritten content (per-region change); rejected clips are kept with their measurements. Otherwise local 2.5D parallax with Depth Anything V2. Off while `MEDIA_MONTHLY_BUDGET_USD=0`. |
| **Footage** | [`src/footage.py`](src/footage.py) | Only clips the operator supplies: send the video to the bot with the caption `/footage <topic>`. Links are not downloaded. |
| **Original graphics** | [`src/graphics.py`](src/graphics.py), [`src/graphic_match.py`](src/graphic_match.py), [`src/then_now.py`](src/then_now.py) | Stat cards, locator maps, timelines, scale comparisons, cross-sections and routes, drawn from fact-checked specs. Each one is placed on the beat that says what it shows (whole-word match on its own figures and names). |
| **Photo pools** | [`scripts/fetch_commons_images.py`](scripts/fetch_commons_images.py), [`src/museum_sources.py`](src/museum_sources.py), [`src/image_review.py`](src/image_review.py), [`src/asset_pipeline.py`](src/asset_pipeline.py) | Up to 20 licensed photos per topic from Wikimedia Commons, The Met, NASA and Smithsonian, ranked by their public metadata (topic words, curator categories, GPS, quality grade), with per-topic namesake exclusions. A vision review keeps photos that show the topic, are usable and safe, and aren't near-duplicates, and approves a pool on merit; the operator's decision always stands. Before each run (and nightly for the next topics) an unready pool is filled and reviewed; a topic without one is skipped, not failed. |
| **Scene planning** | [`src/scene_planner.py`](src/scene_planner.py), [`src/storyboard.py`](src/storyboard.py) | A multimodal plan picks a picture per beat. An approved storyboard is honoured line by line, matched on the narration's words. |
| **Packaging** | [`src/visual_engine.py`](src/visual_engine.py), [`src/multi_format.py`](src/multi_format.py) | Three cover variants, Story frames with a poll card, and a 7-slide carousel. |
| **Render & encoding** | [`src/video_composer.py`](src/video_composer.py), [`src/encoding.py`](src/encoding.py), [`src/upscale.py`](src/upscale.py) | H.264 via `h264_nvenc` when the GPU is present, otherwise `libx264`. Word-timed Anton captions above Meta's bottom UI zone, SRT/VTT exports, encoded-audio loudness correction, Real-ESRGAN upscaling of low-resolution stills. |
| **Quality audit & release** | [`src/video_qa.py`](src/video_qa.py), [`src/release.py`](src/release.py) | `QualityAuditor` measures loudness, graphic/narration sync, graphic freezes, caption contrast and clearance, plus the carousel, stories, thumbnails and policy. Self-healing: scene defects a different picture fixes (graphic out of sync, looping, blank, over the captions) and the vision check's picture mismatches go back to the scene planner for one corrected render. The release gate binds the audit to the master's hash. |
| **Human approval** | [`src/telegram_bot.py`](src/telegram_bot.py), [`src/control.py`](src/control.py) | Review card with a 720p preview, covers, transcript and fact notes, with **Approve**, **Reject** and **Regenerate** buttons. Only configured approver IDs in the configured chat can act; every action is audit-logged. |
| **Publishing & slots** | [`src/publisher.py`](src/publisher.py), [`src/post_schedule.py`](src/post_schedule.py) | Upload-Post first, then the direct Instagram Graph and Facebook Page Reels APIs. With `POST_SCHEDULING=true`, approved Reels are booked into the next free slot and posted when due, by the daemon or the bot (one process posts at a time). |
| **Operations** | [`src/ops.py`](src/ops.py), [`src/privacy.py`](src/privacy.py), [`src/policy_watch.py`](src/policy_watch.py), [`src/maintenance.py`](src/maintenance.py) | Secret-masked console and rotating `logs/pipeline.log`, live credential probes (`health --live`), face privacy on archival imagery, heartbeat and dead-man's-switch ping, weekly Meta policy check, test-gated dependency updates. |

---

## Technical specifications

### Video
* **Dimensions:** 1080×1920 (Reel); 1920×1080 (documentary), via `src/delivery_profiles.py`
* **Codec:** 30 fps H.264, yuv420p, `+faststart`
* **Duration:** script target 42–48 s; voiceover ceiling 52 s; release gate accepts 42–58 s
* **Narration:** 105–115 words, with the spoken length measured before the voiceover

### Audio
* **Format:** 48 kHz stereo AAC
* **Loudness:** −14.0 LUFS integrated, true peak ≤ −1.5 dBTP, measured on the encoded file
* **Mix:** music ducked under narration

---

## Setup

### 1. Prerequisites
- **Python 3.11+**
- **FFmpeg & ffprobe** on PATH (with `libass`; NVENC for GPU encoding)
- **NVIDIA GPU** recommended (NVENC, Depth Anything ONNX, Real-ESRGAN)

### 2. Environment
```bash
git clone https://github.com/<owner>/relic-and-riddle.git
cd relic-and-riddle
python -m venv .venv
.venv\Scripts\activate      # Windows
# source .venv/bin/activate  # Linux/macOS
pip install -r requirements.txt
```

### 3. Optional tools
* **Real-ESRGAN (Windows):** the portable [realesrgan-ncnn-vulkan](https://github.com/xinntao/Real-ESRGAN/releases) build in `tools/realesrgan/realesrgan-ncnn-vulkan.exe`.
* **Depth Anything V2:** `depth_anything_v2_small.onnx` in `tools/models/`.
* **Map data (Natural Earth, public domain):** `tools/` is gitignored, so a fresh clone or a deploy from
  GitHub has neither file until it is fetched. A run logs a `Map data missing` WARNING naming each missing file.
  ```bash
  python scripts/fetch_geodata.py        # country outlines -> tools/geo/ne_50m_admin_0_countries.geojson (~3 MB); without it no maps are made
  python scripts/fetch_relief.py         # shaded relief    -> tools/geo/relief/SR_HR.tif (21600x10800); without it maps render flat
  python scripts/fetch_relief.py --medium  # or the smaller 1:50m SR_50M.tif (10800x5400), used when SR_HR.tif is absent
  python scripts/fetch_marine.py         # marine polygons  -> tools/geo/ne_10m_geography_marine_polys.geojson (~1.4 MB); without it no water is named
                                         # and ice shelves  -> tools/geo/ne_50m_antarctic_ice_shelves_polys.geojson (~0.1 MB); without it Antarctica's shelves are drawn as sea
  python scripts/fetch_geodata.py --10m  # 1:10m map subunits -> tools/geo/ne_10m_admin_0_map_subunits.geojson (~12 MB); every locator close-up is drawn from it, and the site's home is the entity whose polygon holds it (Gibraltar, Tokelau, Ascension)
  ```
  The last two download zipped shapefiles from naciscdn.org and save them as GeoJSON (parsed by `struct` alone).
  Without them a run still makes its maps: no sea is named (logged once), the ice shelves read as sea (logged
  once), and the close-up is drawn from the coarser 1:50m outlines, its home the 1:50m country holding the site (a
  site more than 50 km from all of that ground is marked on the open sea, no home named, logged). An older
  `tools/geo/ne_10m_admin_0_countries.geojson` is no longer read: run `--10m` again.

### 4. Configuration
```bash
cp .env.example .env
python main.py health --live   # credentials, connectivity and live service probes
```

For Windows production provisioning, environment separation, dry-run validation,
cleanup, promotion, and rollback, see [the deployment guide](docs/deployment.md).

---

## CLI reference

### Production
```bash
python main.py test                                    # end-to-end dry run on Derinkuyu (publishes nothing)
python main.py run --topic-id derinkuyu_underground_city               # waits for approval
python main.py run --topic-id derinkuyu_underground_city --auto-approve --dry-run
python main.py run --topic-id derinkuyu_underground_city --format dual # Reel + 16:9 documentary
python main.py run --schedule-posts                    # once approved, book a posting slot
python main.py resume <run_id> [--auto-approve] [--dry-run]
```

### Fact sheets
```bash
python main.py facts --topic-id tunguska_event_1908
python main.py facts --next 5
python main.py facts --topic-id tunguska_event_1908 --force
```

### Review
```bash
python main.py review list
python main.py review approve <run_id>
python main.py review reject <run_id>
python main.py review regenerate <run_id>
python main.py review send <run_id>                    # re-send a timed-out review card
python main.py review publish <run_id> [--dry-run]
```

### Topics and assets
```bash
python main.py topic next
python main.py topic list
python main.py topic requeue <topic_id>
python main.py topic propose
python main.py topic add <slug>
python scripts/fetch_commons_images.py --topic-id <topic_id>
python main.py assets list
python main.py assets approve <topic_id>
python main.py assets review [topic_id]   # vision review: keep, drop and approve on merit
```

### Automation
```bash
python main.py daemon --time 09:00   # daily run, posting slots, heartbeat, nightly fact sheets, weekly checks
python main.py bot                   # Telegram commands, approvals and posting slots; no daily run
python main.py policy [--telegram]
python main.py update [--apply]
python main.py backup [--all]
```

---

## Telegram

When the daemon or the bot is running, approvers can work from Telegram:

- **Review cards:** 720p preview, covers, transcript and fact notes, with Approve / Reject / Regenerate.
- **Commands (selection):** `/dashboard`, `/render` (`/render 4`: that topic, moved to the front), `/produce 3`, `/reviews`, `/posts` (with `slots`, and `now`, `move`, `cancel`, `retry` followed by the episode's number on Posting), `/status`, `/queue` (with `bump 4`, `drop 4`, `requeue 4`, `undo`, `all`), `/photos 4` (with `fetch`, `review`, `keep 2 7`, `drop 1 5`, `upload`), `/covers`, `/music`, `/ideas`, `/pause`, `/resume`; `/help` lists every group. The older names were removed on 2026-10-25; only Telegram's own `/start` opens the Dashboard. Send a photo with the caption `/photos <topic> | source: URL | licence: … | licence_url: URL | author: Name` to add it to a set. Send a video (up to 20 MB) with the caption `/footage <topic> | source: URL | licence: … | licence_url: URL` to add footage. Posting lists every booked episode; 🗓 Slots shows the coming week's Reel, Stories and carousel slots, booked or free, and the slot each waiting carousel will take. Post now and Cancel ask before they act.
- **Alerts:** published, failed and timed-out runs, voice fallbacks, missed posting slots and the daily heartbeat.

---

## Tests

1,203 tests (1,197 in the deployer's git-less, ffmpeg-only environment), all network-blocked (a test that opens a real connection fails):

```bash
pytest -q
pytest tests/test_fact_sheet.py tests/test_empirical_claims.py tests/test_integrity_gates.py
pytest tests/test_voice_chain.py tests/test_music.py tests/test_duration_estimate.py
pytest tests/test_encoding.py tests/test_covers.py tests/test_graphics.py tests/test_quality_audit.py
```

---

## Licence

Proprietary. Media attributions and licences (CC0, CC BY, public domain, UNESCO) are recorded per asset in `credits.json` and compiled into captions and provenance ledgers.

## Backup operations

See [OneDrive enrollment, verification, and isolated recovery](docs/backup.md).
The existing desktop-sync mode is preserved until API access is provisioned.
