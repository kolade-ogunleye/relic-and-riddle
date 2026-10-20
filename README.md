# Relic & Riddle

*Relics and riddles of history, in 45 seconds.*

A documentary production pipeline that researches, fact-checks, voices, renders and publishes short vertical Reels (9:16) about historical curiosities and ancient marvels, with an optional 16:9 documentary cut. Every on-screen and spoken claim is traced to quoted evidence in a verified fact sheet, every asset carries provenance and rights evidence, and nothing posts until a human approves it in Telegram.

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
| **Voiceover** | [`src/audio_engine.py`](src/audio_engine.py) | Azure Speech (`en-US-BrianMultilingualNeural`) and ElevenLabs, in the order set by `VOICE_PREFER`, with word timings; edge-tts only when a licence isn't required (dry runs). |
| **Music & sound** | [`src/music.py`](src/music.py), [`src/audio_engine.py`](src/audio_engine.py) | ElevenLabs Music (paid plans) or approved CC0/CC BY tracks from `data/music/library.json` (credited in captions), falling back to a synthesized drone. Sound effects fire on the words that name what they depict. |
| **Motion** | [`src/motion.py`](src/motion.py), [`src/parallax.py`](src/parallax.py) | Optional Kling motion hook, checked for warping by a feature-homography test (rejected clips are kept with their measurements), otherwise local 2.5D parallax with Depth Anything V2. |
| **Footage** | [`src/footage.py`](src/footage.py) | Only clips the operator supplies: send the video to the bot with the caption `/footage <topic>`. Links are not downloaded. |
| **Original graphics** | [`src/graphics.py`](src/graphics.py), [`src/graphic_match.py`](src/graphic_match.py), [`src/then_now.py`](src/then_now.py) | Stat cards, locator maps, timelines, scale comparisons, cross-sections and routes, drawn from fact-checked specs. Each one is placed on the beat that says what it shows (whole-word match on its own figures and names). |
| **Scene planning** | [`src/scene_planner.py`](src/scene_planner.py), [`src/storyboard.py`](src/storyboard.py) | A multimodal plan picks a picture per beat. An approved storyboard is honoured line by line, matched on the narration's words. |
| **Packaging** | [`src/visual_engine.py`](src/visual_engine.py), [`src/multi_format.py`](src/multi_format.py) | Three cover variants, Story frames with a poll card, and a 7-slide carousel. |
| **Render & encoding** | [`src/video_composer.py`](src/video_composer.py), [`src/encoding.py`](src/encoding.py), [`src/upscale.py`](src/upscale.py) | H.264 via `h264_nvenc` when the GPU is present, otherwise `libx264`. Word-timed Anton captions above Meta's bottom UI zone, SRT/VTT exports, encoded-audio loudness correction, Real-ESRGAN upscaling of low-resolution stills. |
| **Quality audit & release** | [`src/video_qa.py`](src/video_qa.py), [`src/release.py`](src/release.py) | `QualityAuditor` measures loudness, graphic/narration sync, graphic freezes, caption contrast and clearance, plus the carousel, stories, thumbnails and policy. It reports and repairs nothing; a defect is fixed by re-running its stage. The release gate binds the audit to the master's hash. |
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
git clone https://github.com/kolade-ogunleye/relic-and-riddle.git
cd relic-and-riddle
python -m venv .venv
.venv\Scripts\activate      # Windows
# source .venv/bin/activate  # Linux/macOS
pip install -r requirements.txt
```

### 3. Optional tools
* **Real-ESRGAN (Windows):** the portable [realesrgan-ncnn-vulkan](https://github.com/xinntao/Real-ESRGAN/releases) build in `tools/realesrgan/realesrgan-ncnn-vulkan.exe`.
* **Depth Anything V2:** `depth_anything_v2_small.onnx` in `tools/models/`.

### 4. Configuration
```bash
cp .env.example .env
python main.py health --live   # credentials, connectivity and live service probes
```

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
- **Commands (selection):** `/status`, `/next`, `/run`, `/pause`, `/resume`, `/queue`, `/covers`, `/images`, `/review N`, `/drop N 2 4`, `/swap N 2 4`, `/propose`, `/music`, `/issue`, `/alerts`, `/help` for the full list. Send a video with the caption `/footage <topic>` to add footage.
- **Alerts:** published, failed and timed-out runs, voice fallbacks, missed posting slots and the daily heartbeat.

---

## Tests

61 test modules, 528 tests, all network-blocked (a test that opens a real connection fails):

```bash
pytest -q
pytest tests/test_fact_sheet.py tests/test_empirical_claims.py tests/test_integrity_gates.py
pytest tests/test_voice_chain.py tests/test_music.py tests/test_duration_estimate.py
pytest tests/test_encoding.py tests/test_covers.py tests/test_graphics.py tests/test_quality_audit.py
```

---

## Licence

Proprietary. Media attributions and licences (CC0, CC BY, public domain, UNESCO) are recorded per asset in `credits.json` and compiled into captions and provenance ledgers.
