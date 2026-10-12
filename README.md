# Autonomous Meta Reels & Multi-Format Publishing Pipeline (Enterprise Edition)

Production-ready, fully autonomous system for creating and publishing high-retention faceless documentary content on Meta (**Facebook Reels, Instagram Reels, Stories, and Carousels**) in the **Historical Curiosities & Ancient Marvels** niche.

---

## 🏛️ World-Class Architecture ("Best of the Best")

1. **4-Beat Narrative Arc with Self-Contained Circular Bridge**:
   - **Beat 1**: Sensory Hook (0–5s).
   - **Beat 2**: Architectural and Physical Scale (6–22s).
   - **Beat 3**: Unsolved Historical Enigma (23–35s).
   - **Beat 4**: Profound, Self-Contained Insight (36–45s) resetting Beat 1 on infinite loop.
   - **Zero Trailing Conjunctions**: Never ends on trailing words (`when`, `until`, `because`, `and`).
2. **Search-Grounded Claim Verification & Fact Gating Guardrail (`src/fact_checker.py`)**:
   - Extracts atomic factual claims (dates, depths, discoveries, dimensions).
   - Cross-references against academic consensus with confidence scoring.
   - Gates against clickbait conspiracy tropes (`aliens`, `reptilians`, `magic portals`).
3. **Multi-Tier Voice Engine & Broadcast Audio Mastering (`src/audio_engine.py`)**:
   - **Primary**: ElevenLabs documentary voice clone with human breaths and cadence.
   - **Failover**: Automatic `edge-tts` (Christopher Neural) with zero downtime.
   - **Studio Audio DSP**: High-pass rumble filter (80Hz), de-esser EQ (3.2kHz), sidechain compression ducking ambient bed under speech, and EBU R128 loudness normalization strictly targeted to **-14.0 LUFS**.
4. **Kinetic Caption Typography (`src/video_composer.py`)**:
   - **Font**: Anton Regular at 92pt.
   - **Motion**: Running speech active word highlighting (Warm Gold active, Pure White past, Soft Gray upcoming).
   - **Safe Zones**: `MarginV = 540px` (`y = 1380px`), completely clear of native Meta UI overlays.
5. **Locked Variant B Thumbnail Engine (`src/visual_engine.py`)**:
   - Dual-format output: 9:16 Vertical Reel Cover (`1080x1920`) + 1:1 Instagram Profile Grid Crop (`1080x1080`).
   - Soft-feathered 85% opacity dark halo centered over 8K photorealistic key-art.
6. **Modular Multi-Format Monetization Multiplier (`src/multi_format.py`)**:
   - **3-Frame Interactive Stories (9:16)**: Hook slide, revelation slide, and interactive voting/poll sticker prompt.
   - **7-Slide Educational Carousels (1:1)**: High-retention square carousel slides designed for bookmarks and shares.
7. **Thread-Safe SQLite WAL Persistence (`src/db.py`)**:
   - Atomic ACID transactions in `data/pipeline.db` using Write-Ahead Logging (WAL) mode. Zero file-collision risks between daily cron runs and Telegram approval callbacks.
8. **Resilient 3-Tier Publishing Failover (`src/publisher.py`)**:
   - **Tier 1**: Upload-Post.com (Active SaaS subscription).
   - **Tier 2**: Postiz (Self-hosted open-source social publisher; no Meta verification roadblock).
   - **Tier 3**: Direct Meta Graph API (v21.0 native endpoints).
9. **Zero-Trust Operations & Maintenance (`src/ops.py`)**:
   - In-memory secret masking on all logs and Telegram cards.
   - Automated intermediate scratch asset cleanup (> 7 days).
   - Pre-flight health checks (FFmpeg, disk space > 5GB, typography fonts, network).

---

## 🚀 Quick Start & Commands

### 1. Pre-Flight Health Check
```bash
python main.py health
```

### 2. Run Single Autonomous Cycle
```bash
# Full cycle with human-in-the-loop approval step
python main.py run

# Full cycle with explicit topic and auto-approval
python main.py run --topic-id 02_roman_self_healing_concrete --auto-approve

# Dry-run / test simulation (no live external upload)
python main.py run --dry-run
```

### 3. Run Automated Daily Daemon
```bash
# Runs APScheduler daily at 09:00 AM Eastern Time
python main.py daemon --time 09:00
```

### 4. Manage Topics & History (SQLite WAL)
```bash
# View next queued topic
python main.py topic next

# View all 30 topics and publication status
python main.py topic list
```

### 5. Review & Manage Approvals
```bash
# View pending review queue
python main.py review list

# Approve or reject a specific run from CLI
python main.py review approve <run_id>
python main.py review reject <run_id>
```

### 6. Run Complete Integration Verification Test
```bash
python main.py test
```

---

## 📁 Directory Structure

```
├── assets/                  # 8K Key-art & scene images
├── data/
│   ├── pipeline.db          # Thread-safe SQLite database (WAL mode)
│   ├── sfx/                 # Diegetic audio SFX (hammer impact, rolling door)
│   ├── topics/              # 30-day topic bank & JSON mirror history
│   ├── approval_queue.json  # Read-only JSON mirror of approvals
│   └── publishing_log.json  # Read-only JSON mirror of publication payloads
├── fonts/                   # Anton-Regular.ttf & Montserrat-Variable.ttf
├── output/                  # Master MP4s, ASS subtitles, thumbnails, stories & carousels
├── src/
│   ├── config.py            # Centralized settings & locked specs
│   ├── db.py                # SQLite WAL database & atomic transactions
│   ├── ops.py               # Pre-flight health, secret masking & lifecycle cleanup
│   ├── topic_engine.py      # Bank rotation & history manager (backed by SQLite)
│   ├── script_engine.py     # 4-beat circular script & Stage 2.5 Guardrail
│   ├── fact_checker.py      # Automated claim extraction & academic consensus auditing
│   ├── audio_engine.py      # Multi-tier voice dispatcher (ElevenLabs + edge-tts)
│   ├── visual_engine.py     # Hybrid motion sequencing & Variant B thumbnails
│   ├── video_composer.py    # FFmpeg assembly, Anton 92pt subtitles & -14 LUFS mastering
│   ├── multi_format.py      # Companion 3-Frame Stories & 7-Slide Carousels
│   ├── telegram_bot.py      # HITL Telegram approval bot (backed by SQLite)
│   └── publisher.py         # 3-Tier Publishing Failover (Upload-Post -> Postiz -> Meta Graph)
└── main.py                  # CLI Orchestrator & APScheduler daemon
```
