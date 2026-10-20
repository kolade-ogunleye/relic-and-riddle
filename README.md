# Relic & Riddle

*Relics and riddles of history, in 45 seconds.*

Autonomous, broadcast-grade documentary production engine that researches, fact-checks, voices, animates, renders, and publishes short-form vertical Reels (9:16) and widescreen documentaries (16:9) in the **"Historical Curiosities & Ancient Marvels"** niche. Built with zero-hallucination fact sheets, multi-model AI routing, closed-loop self-healing quality gates, and human-in-the-loop (HITL) cryptographic approval before anything goes live.

```
Evidence Layer (UNESCO + Wikipedia + Scholarly Citations)
    │
    ▼
Fact Sheet Compilation (Deterministic auto-hedging & quote extraction)
    │
    ▼
Script Generation (Category-Aware Hook Grammar + Trace-Checking Gate)
    │
    ▼
Voice Synthesis Chain (ElevenLabs ➔ Azure Speech F0 ➔ Edge-TTS)
    │
    ▼
Visual & Motion Synthesis (AI Motion Hooks + 2.5D Parallax + Procedural Graphics)
    │
    ▼
Multi-Format Packaging (3-Variant Thumbnails + 7-Slide Carousels + Stories)
    │
    ▼
Hardware Video Composition (NVENC H.264 + Anton Typography + EBU R128 Audio)
    │
    ▼
Closed-Loop Self-Healing Audit (Collision detection, contrast & LUFS auto-remediation)
    │
    ▼
Human Approval Gate (Telegram Bot interactive cards / CLI review)
    │
    ▼
Multi-Platform Distribution (Upload-Post SaaS ➔ Instagram Graph API ➔ Facebook Page Reels)
    │
    ▼
Idempotent Post Scheduling (Peak slots: 12:00 & 19:00 EST, 48h carousel spacing)
```

---

## Core Design Principles

- **Zero-Tolerance Claim Gating & Anti-Hallucination:** Scripts must trace 100% of claims to verified quotes in pre-compiled fact sheets. Disputed metrics or single-source estimates require natural journalistic hedges (e.g., *"researchers calculate"*, *"models suggest"*). Robotic sentence prefixes (e.g., *"Estimated, ..."*) are strictly banned.
- **Dual-Engine Architecture ($0.00 Free Tier vs. Premium Studio):**
  - **Free Tier (`SCRIPT_ENGINE=free`, `VOICE_PREFER=azure`, `MEDIA_MONTHLY_BUDGET_USD=0`):** Operates at $0.00 ongoing cost using Google Gemini Free Tier (`gemini-3.8-flash` writer, `gemini-3.7-flash` checker) with zero billing risk, Azure Cognitive Speech F0 (500k commercial characters/month), local 2.5D Parallax, and procedural graphics.
  - **Premium Studio Tier:** Uses Anthropic Claude (`claude-opus-5-5`, `claude-sonnet-5-5`), ElevenLabs neural voice cloning, and AI motion video (Google Veo / Kling AI) constrained by monthly spend caps.
- **Closed-Loop Self-Healing Validation:** After rendering, a deterministic QA auditor analyzes subtitle-to-graphic bounds, WCAG typographic contrast ($\ge 4.5:1$), and EBU R128 audio loudness, automatically adjusting layouts and re-rendering prior to human review.
- **Fail Closed & Cryptographically Safe:** Missing assets, unverified claims, missing credentials, or unapproved runs halt immediately. Every publication carries an idempotent run key and distributed publication lock to prevent duplicate posts.
- **One Source of Truth:** SQLite in Write-Ahead Logging (WAL) mode (`data/pipeline.db`) maintains ACID transactions for all topics, runs, approvals, and scheduled slots.

---

## Production Pipeline Architecture

| Stage | Module(s) | Description & Key Responsibilities |
|---|---|---|
| **Topic Engine & Rotation** | [`src/topic_engine.py`](src/topic_engine.py), [`src/db.py`](src/db.py) | Selects topics using Virality Potential Scoring (VPS 0–100) based on cognitive tension, high-stakes scale, and factual depth. Prioritizes unproduced topics, then failed runs, then least-published. Auto-recovers stale runs after 6 hours. |
| **Evidence & Fact Sheets** | [`src/fact_sheet.py`](src/fact_sheet.py), [`src/unesco.py`](src/unesco.py), [`src/free_evidence.py`](src/free_evidence.py) | Harvests high-tier evidence from official UNESCO World Heritage database records, Wikipedia, and scholarly journals. Builds structured JSON fact sheets with deterministic auto-hedging and dual Julian/Gregorian calendar normalization. |
| **Script Engine & Trace Check** | [`src/script_engine.py`](src/script_engine.py), [`src/trace_check.py`](src/trace_check.py) | Drafts 105–115 word scripts using Category-Aware Hook Grammar (Architectural Paradox, Lost Tech Anomaly, Cataclysmic Anomaly). Enforces zero-tolerance claim-to-evidence trace checking; surgical rewriters fix only rejected sentences without hallucinating new claims. |
| **Multi-Model LLM Routing** | [`src/llm.py`](src/llm.py), [`src/gemini.py`](src/gemini.py) | Unified abstraction supporting Google Gemini, Anthropic Claude, and OpenAI GPT-4o. Tracks character/token spend against monthly budget limits (`CLAUDE_MONTHLY_BUDGET_USD`) with automatic cross-provider failover. |
| **Voiceover Synthesis** | [`src/audio_engine.py`](src/audio_engine.py) | 3-tier fallback chain: ElevenLabs (with character timestamps) ➔ Azure Cognitive Speech (`en-US-BrianMultilingualNeural` on F0 free tier) ➔ Edge-TTS with phonetic word-boundary alignment. |
| **Music & Sound Design** | [`src/music.py`](src/music.py), [`src/audio_engine.py`](src/audio_engine.py) | ElevenLabs Music generation (on paid plans) or royalty-free CC0/CC BY tracks from `data/music/library.json` (credited in captions), falling back to synthesized dual-sine ambient drone (55 Hz + 82.5 Hz). Word-triggered Foley SFX integration. |
| **Motion & Footage** | [`src/motion.py`](src/motion.py), [`src/footage.py`](src/footage.py), [`src/parallax.py`](src/parallax.py) | AI Motion Hooks via Google Veo (`veo-3.1-fast-generate-preview`) and Kling AI with ORB homography structural integrity gates (rejects morphological drift/warping). Fallback to local 2.5D Parallax with Depth Anything V2 Small. Public domain 4K aerial footage via Wikimedia Commons and YouTube Creative Commons. |
| **Procedural Graphics** | [`src/graphics.py`](src/graphics.py), [`src/then_now.py`](src/then_now.py) | Generates dynamic glowing stat cards, animated locator maps with pulsing coordinate beacons, and Then-and-Now comparison split screens. Positioned with visual buffers above kinetic subtitle pills. |
| **Multi-Format Key-Art** | [`src/visual_engine.py`](src/visual_engine.py), [`src/multi_format.py`](src/multi_format.py) | Produces 3-variant thumbnail suites (Variant A Blueprint, Variant B Golden Intrigue, Variant C Minimal Poster) with depth-sandwich subject cutouts, 3 Story frames with interactive poll cards, and 7-slide editorial Carousels. |
| **Video Composition & Encoding** | [`src/video_composer.py`](src/video_composer.py), [`src/encoding.py`](src/encoding.py), [`src/upscale.py`](src/upscale.py) | Hardware-accelerated H.264 rendering (`h264_nvenc` NVIDIA GPU, Intel QSV, AMD AMF, or `libx264`). Burns Anton small-caps kinetic karaoke subtitles. Masters audio to EBU R128 standard ($-14.0$ LUFS, $\le -1.5$ dBTP). AI 4× image upscaling via Real-ESRGAN Vulkan. |
| **Closed-Loop Self-Healing QA** | [`src/video_qa.py`](src/video_qa.py) | ClosedLoopAuditor validates frame-by-frame element positioning, text overlap, carousel contrast ratios, and subtitle safe zones ($0.58 \times H$ margin). Auto-heals and re-renders if issues occur. |
| **Human Approval (HITL)** | [`src/telegram_bot.py`](src/telegram_bot.py) | Telegram bot delivers review card, 720p preview video, and thumbnail covers with inline **Approve**, **Reject**, and **Regenerate** buttons. Approvals restricted to authorized Telegram user IDs. CLI approval fallback. |
| **Publishing & Post Scheduling** | [`src/publisher.py`](src/publisher.py), [`src/post_schedule.py`](src/post_schedule.py) | Publishes via Upload-Post.com (Facebook & Instagram), with direct Instagram Graph API and Facebook Page Reels API fallbacks. Automated daemon books peak engagement slots (12:00 & 19:00 EST), delays Stories by 45m, and spaces Carousels by 48 hours. |
| **Operations & Security** | [`src/ops.py`](src/ops.py), [`src/privacy.py`](src/privacy.py), [`src/policy_watch.py`](src/policy_watch.py) | Automatic secret masking across all logs, face privacy redaction on archival imagery, dead-man's-switch health monitoring, weekly Meta policy audits, and test-gated dependency maintenance. |

---

## Technical Specifications

### Video & Visuals
* **Dimensions:** 1080×1920 (9:16 vertical Reel) & 1920×1080 (16:9 widescreen documentary via `src/delivery_profiles.py`)
* **Framerate & Codec:** 30 fps, H.264 (NVENC hardware accelerated), CRF 18, TV-range yuv420p, `+faststart`
* **Target Duration:** 42.0s – 48.0s (Hard ceiling: 52.0s)
* **Target Word Count:** 105 – 115 spoken words
* **Typography:** Anton 72pt small capitals with 14px rounded translucent charcoal pill (`&H20101010&`), warm gold `#FFD700` emphasis words, baseline at $y = 1248$ (safe above Meta UI zones)

### Audio Mastering
* **Sample Rate:** 48,000 Hz, 16-bit stereo AAC
* **Loudness Normalization:** Integrated $-14.0$ LUFS (ITU-R BS.1770-4 / EBU R128)
* **True Peak Ceiling:** $\le -1.5$ dBTP
* **Acoustic Ducking:** Dynamic sidechain ducking of background music under narration with exponential release

---

## Setup & Installation

### 1. Prerequisites
- **Python 3.11+**
- **FFmpeg & ffprobe 7.0+** (Compiled with `libass`, `cairo`, and `nvenc` on your system PATH)
- **NVIDIA GPU** (Recommended for NVENC acceleration, Depth Anything ONNX, and Real-ESRGAN)

### 2. Virtual Environment & Dependencies
```bash
# Clone the repository
git clone https://github.com/your-username/relic-and-riddle.git
cd relic-and-riddle

# Create and activate virtual environment
python -m venv .venv
.venv\Scripts\activate      # Windows (PowerShell/CMD)
# source .venv/bin/activate  # Linux/macOS

# Install dependencies
pip install -r requirements.txt
```

### 3. Optional Tooling Binaries
* **Real-ESRGAN Upscaler (Windows):** Download the portable [realesrgan-ncnn-vulkan](https://github.com/xinntao/Real-ESRGAN/releases) release into `tools/realesrgan/realesrgan-ncnn-vulkan.exe`.
* **Depth Anything V2 ONNX:** Download `depth_anything_v2_small.onnx` into `tools/models/depth_anything_v2_small.onnx`.

### 4. Environment Configuration
Copy the master template and populate your credentials:
```bash
cp .env.example .env
```
Audit your configuration and verify external connectivity:
```bash
python main.py health --live
```

---

## CLI Command Reference

### Production & Rendering
```bash
# Full end-to-end dry run on Derinkuyu (publishes nothing)
python main.py test

# Produce one Reel for a specific topic (waits for approval)
python main.py run --topic-id 01_derinkuyu_underground_city

# Produce and auto-approve without Telegram interaction
python main.py run --topic-id 01_derinkuyu_underground_city --auto-approve --dry-run

# Produce in dual format (9:16 vertical Reel + 16:9 widescreen documentary)
python main.py run --topic-id 01_derinkuyu_underground_city --format dual

# Resume a failed run reusing verified script and voiceover
python main.py resume <run_id> [--auto-approve] [--dry-run]
```

### Fact Sheet Management
```bash
# Build verified fact sheet from UNESCO & scholarly evidence for a topic
python main.py facts --topic-id 20_tunguska_event_1908

# Pre-build fact sheets for the next 5 queued topics
python main.py facts --next 5

# Force-rebuild an existing fact sheet
python main.py facts --topic-id 20_tunguska_event_1908 --force
```

### Review & Human Approval
```bash
# List all runs awaiting approval
python main.py review list

# Approve, reject, or request script regeneration for a run
python main.py review approve <run_id>
python main.py review reject <run_id>
python main.py review regenerate <run_id>

# Re-send Telegram review card for a timed-out run
python main.py review send <run_id>

# Manually publish an approved, unpublished run
python main.py review publish <run_id> [--dry-run]
```

### Topic & Asset Management
```bash
# Show next topic in queue
python main.py topic next

# List all topics and their statuses
python main.py topic list

# Requeue a failed or rejected topic
python main.py topic requeue <topic_id>

# AI-propose 3 new sourced topics for the bank
python main.py topic propose
python main.py topic add <slug>

# Fetch CC-licensed Wikimedia Commons images for a topic
python scripts/fetch_commons_images.py --topic-id <topic_id>

# Review and approve topic image sets
python main.py assets list
python main.py assets approve <topic_id>
```

### Daemon & Automation
```bash
# Run daily production daemon (runs at scheduled time in DEFAULT_TIMEZONE)
python main.py daemon --time 09:00

# Run daemon with background continuous layout tuning
python main.py daemon --time 09:00 --continuous-tuning

# Run Telegram bot only (handles commands, image review, approvals without production)
python main.py bot

# Check for Meta platform policy changes
python main.py policy [--telegram]

# Audit dependency updates and vulnerabilities
python main.py update [--apply]
```

---

## Telegram Bot Operations

When `python main.py daemon` or `python main.py bot` is running, authorized approvers can manage the pipeline directly via Telegram:

- **Interactive Approval Cards:** Receives 720p review video, full-resolution cover, transcript, fact-checking credentials, and inline buttons (**Approve**, **Reject**, **Regenerate**).
- **Commands:**
  - `/status` — Displays current pipeline state, month-to-date LLM and media spend, and queued runs.
  - `/next` — Shows the next topic scheduled for production.
  - `/run` — Manually triggers a production run for the next queued topic.
  - `/pause` & `/resume` — Holds or resumes scheduled publishing slots.
  - `/images` & `/review N` — Inspects contact sheets for topic image sets with single-tap approval.
  - `/drop N 2 4` — Discards specific irrelevant images from a topic set.
- **Automated Alerts:** Instant notifications on publish success, failed runs, approval timeouts, and daily system heartbeats pinging dead-man's switch URLs.

---

## Testing & Quality Assurance

The codebase includes an extensive suite of 49 automated test modules covering 395+ regression, unit, and integration tests:

```bash
# Run the complete test suite
pytest -q

# Run fact sheet and claim-gating tests
pytest tests/test_fact_sheet.py tests/test_trace_check.py tests/test_empirical_claims.py

# Run audio and voiceover chain tests
pytest tests/test_voice_chain.py tests/test_music.py

# Run video encoding and visual systems tests
pytest tests/test_encoding.py tests/test_covers.py tests/test_graphics.py tests/test_self_healing.py
```

---

## License

Proprietary documentary automation system. All generated media attributions and public domain licenses (CC0, CC BY, UNESCO Open Archive) are recorded in `credits.json` and automatically compiled into publication captions and provenance ledgers.
