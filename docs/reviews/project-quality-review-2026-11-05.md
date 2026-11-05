# Project quality review (2026-11-05)

Branch `claude/intelligent-carson-ybsccd` at 581a866. Six independent read-only expert reviews (visual design, audio,
video, content/LLM, monetization/policy, engineering) plus coordinator spot checks against the source. Line numbers
are as of 581a866. Nothing here was changed in code; this is the assessment the operator asked for:

> Is the project equipped with the best, highest-quality tools and programming modules to support the "highest
> quality" mantra? Are the code, logic, algorithms and pipeline designed to produce the highest-quality output
> (graphics, audiovisual production, mastering)? Does it support monetization within platform policy, and adapting
> to other niches?

## 1. Bottom line

**Overall: 5.0 / 10. The project's *discipline* is elite; its *output quality* and *engineering* are not.**

| Subsystem | Score | One line |
|---|---|---|
| Fact safety, rights and release gate | 7.5 | Byte-bound licence evidence, HMAC-signed fact records, fail-closed publishing, idempotent uploads. Rare in this space. |
| Captions | 8 | Per-word reveal, measured contrast, platform-union safe box, sidecars. Production grade. |
| Audio mastering (loudness/limiting) | 7 | Right targets, 4x-oversampled limiter, measure-the-encode loop. |
| Visual design system and typography | 6 | Tokens and safe zones exist; type scale is dead code; OpenType features never used; tracked text loses kerning. |
| Cartography | 7 (design) / NOT OK (review) | Serious work on hand-rolled geometry; still failing its own review after 16 rounds. |
| Video picture path | 5 | BT.601 maths under BT.709 tags, 8-bit grade round-trips, unseeded grain, parallax that ignores its depth map. |
| Sound design (not mastering) | 5 | A whoosh every ~6.5 s, sub-bass hits phones cannot play, pumping ducking, voice MP3-encoded twice. |
| Narrative craft | 5 | Hook rule is advisory; two of five beats are filler; free-tier single-shot writer is the ceiling. |
| Monetization readiness | 4 | No originality floor; YouTube collects no revenue data and is not policy-watched; CC BY music is a Content ID risk. |
| Niche adaptability | 3 | Niche is baked into 20+ places and the brand into 11 modules. A second niche is a fork, not a config. |
| Engineering (architecture, tooling, deps) | 4.3 | Six modules > 3k lines, 69-module import cycle, 146 unvalidated env vars, no lockfile/lint/type config, Pillow with open CVEs. |

**Is it equipped with the best tools?** No. The *categories* are right (Pillow, NumPy, OpenCV, SciPy, ffmpeg+libass,
x264/NVENC, Real-ESRGAN, ONNX, pydantic, SQLite WAL, ElevenLabs/Azure), but every hard problem inside them has been
re-implemented by hand: map projections, polygon clipping and a shapefile parser; label placement; text shaping and
TrueType parsing; a loudness search loop; retry loops; configuration. The mature libraries that exist for each are
absent, and ffmpeg's own quality filters (`zscale`, `loudnorm`, `deesser`, `blackdetect`, `freezedetect`,
`photosensitivity`, `ssim`) are in the installed build and unused. That is the pattern behind the maps needing 16
review rounds: each defect is patched by another branch rather than by a library that is already correct.

**Is the pipeline designed for the highest-quality output?** Partly. Everything that protects the operator from
publishing something false, unlicensed or duplicated is designed to a high standard. What the viewer sees and hears
is not: three silent colour and bit-depth defects degrade every upload, the motion reads as a slideshow, the sound
design is over-decorated, and the opening three seconds (the retention lever) are the least-enforced part of the
script stage.

## 2. Findings that affect every published video (fix first)

Each of these was confirmed by the coordinator in source, not only by the reviewer.

| # | Defect | Evidence | Fix | Effort |
|---|---|---|---|---|
| 1 | **Colour matrix mismatch.** Every RGB→YUV conversion uses swscale's BT.601 default, then the master is tagged BT.709. Reds, skin tones and the brand gold shift on all four platforms, and gold differs between a photo scene and a graphic scene. | `src/graphics.py:250-251` (rgb24 → yuv420p, no matrix), `src/video_composer.py:4216` (`scale=out_range=tv`), `:4228` (bt709 tags). `src/then_now.py:1384-1387` already does it right. Measured: pure red → Y82 U92 V234 (601) vs 63/102/240 (709). | `scale=out_color_matrix=bt709:out_range=tv` (or `zscale=matrix=709:range=limited:dither=error_diffusion`) once at the end of the graph; tag every intermediate clip (graphics, parallax, footage, motion). | 1 day |
| 2 | **8-bit grade round-trips band the shadows.** `curves` and `lut3d` are RGB filters applied to a yuv420p stream; ffmpeg inserts yuv→rgb→yuv around each. Measured 56 → 31 distinct shadow levels; 56 preserved in `gbrp16`. | `src/video_composer.py:4137, 4213-4214` | Keep the picture graph in 16-bit RGB (`gbrp16`) through grade, vignette and captions; convert once with dither. | 2 days |
| 3 | **Unseeded grain triples the bitrate and is stripped by the platforms.** 1.27 MB → 4.38 MB at CRF 18; after a 2 Mb/s platform re-encode SSIM is *worse* with the noise. Renders are non-deterministic. | `src/video_composer.py:4215` (`noise=alls=8:allf=t+u`, no seed) | Remove from the delivery master, or seed it and apply at 2x before the downscale at ≤3. | 1 hour |
| 4 | **"2.5D parallax" is a flat Ken Burns; the depth map is computed and never used, and the 2x upscale is discarded.** | `src/parallax.py:162` (`depth = depth_map(base)`, unused), `:165-190` (affine remap), `:157-161` (base built at 1x). Docstring, lineage and docs claim otherwise. | Depth-weighted remap from a 2x base with `cv2.inpaint`/LaMa for disocclusions, or delete the model call and the claim. | 3-5 days |
| 5 | **Integer-pixel Ken Burns.** Push-ins and drifts resize to `int()` sizes and crop at integer offsets; a 1.5% zoom over 6 s ticks one pixel every ~11 frames. Four copies of the same code. | `src/graphics.py:8378-8386, 7739-7751, 5553-5558`; `src/end_card.py:343`; `src/then_now.py:478`; zoompan `:4121-4125` linear with a hard stop at frame 150 | One shared `kenburns()` using a float affine transform (BICUBIC) and a smoothstep curve; delete the copies. | 2 days |
| 6 | **Transitions fight retention.** 0.4 s crossfade on every cut, a blurry zoom-dissolve forced on cut 2, and **fade-to-black every fourth cut** (three "is it over?" dips per 45 s Reel). | `src/video_composer.py:4160-4170` | Hard cuts on beat boundaries; a 6-8 frame dissolve only on act changes; never fade-to-black inside a Reel. | 1 day |
| 7 | **A transition whoosh on every sentence pause.** With no cue timestamps (every Reel), a whoosh fires at every ≥0.2 s gap or punctuation 3.5 s apart: 5-7 per Reel on the real fixtures, 30 on the 16:9. The clearest "AI slop" tell. | `src/audio_engine.py:1015-1036`; `main.py:2225` passes no cues | Fire only on actual scene cuts or sound-plan reveals, cap at 2 per Reel, -6 dB. | 2 hours |
| 8 | **Sub-bass hits (45-46 Hz) that phones cannot reproduce, which still drive the master limiter 9 dB and duck the narrator.** SFX are RMS-matched to a synth stand-in, not peak-limited. | `src/audio_engine.py:801, 988-994, 1010, 1038-1048, 865-882` | Give impacts a 90-200 Hz body and a 1-3 kHz transient; peak-normalise effects; limit the bed bus separately; add a `highpass=300` "phone check" to QA. | 1 day |
| 9 | **Ducking pumps between words.** `sidechaincompress` release 220 ms, no hold, full-band key; the bed recovers a median 7.4 dB inside word gaps. | `src/video_composer.py:2819` | release ~700 ms, ratio 4, band-limited key (200 Hz-4 kHz); or a numpy envelope with a 300 ms hold. | 0.5 day |
| 10 | **The voice is lossy twice before AAC.** Azure requests MP3 192k; ElevenLabs gets the default `mp3_44100_128`, then 44.1→48 k resample, then AAC. No `seed`, no pronunciation control for names (Derinkuyu, Gol Gumbaz, Richat). | `src/audio_engine.py:449, 377, 367-374, 101-124` | `Riff48Khz16BitMonoPcm` / `output_format=pcm_48000`; set `seed`; per-topic pronunciation map rendered as Azure `<phoneme>`/`<lexicon>` and ElevenLabs pronunciation dictionaries; verify with a faster-whisper transcript diff. | 2 days |
| 11 | **The hook is not enforced.** `hook_problems` fires but is stripped unless `HOOK_FORCES_REWRITE` (False); weak openings ship (Wieliczka opens on 24 words about grey salt). Beats 3-4 are ~40% of runtime with no payoff; no check that the close answers the hook; auto-hedges produce "By one estimate, miners carved four chapels". | `src/script_engine.py:1372, 1721-1749, 2370-2377, 63-66`; `src/fact_sheet.py:100-104` | Make hook faults blocking with the existing one-call `_edit_story` repair; restructure beats (hook → stake → 2-3 reveals → payoff → loop); restrict auto-hedge to numbers/dates with ranges and forbid it in hook/close. | 2 days |
| 12 | **No originality floor, and the output is the archetype both platforms target** (licensed stills + synthetic voice + overlays). Nothing measures how far a Reel is from "narrated slideshow". YouTube is not policy-watched, collects no revenue data, and its metadata is a copy of the Instagram caption. | `docs/sources.md:11-25` (the repo knows the rule); `src/release.py:16-159` (no originality check); `src/policy_watch.py:27-34` (Meta only); `src/youtube_analytics.py:68-70` (no monetary scope); `src/publisher.py:216-231` | `src/originality.py` (original-graphic runtime share, motion vs static, narration-describes-picture ratio) as a `needs_review` floor on the review card; add `yt-analytics-monetary.readonly` and RPM by topic; YouTube areas in policy_watch; a real YouTube metadata block. | 1 week |
| 13 | **Pillow 12.3.0 with open advisories decodes untrusted Wikimedia bytes.** Confirmed with pip-audit (CVE-2026-112393, -96638, -96640, -96778, GHSA-78g4-xqwv-q3g8; fixed in 13.0.0). No lockfile, no hashes, `scipy` unpinned, `pip-audit` is a runtime dependency, `Image.MAX_IMAGE_PIXELS = None` is mutated process-wide while thread pools decode downloads. | `requirements.txt`; `src/commons.py:683`; `src/graphics.py:1079`; `src/upscale.py:210` | `pillow==13.0.0`; `uv lock` with hashes; CI fails on `pip-audit`; byte cap on downloads; never mutate the global. | 1 day |

## 3. Design and output quality in detail

### Visual design (6/10)
- **Strong:** colour tokens (`src/brand.py`), one sourced safe-zone model (`src/safe_zones.py`), measured contrast on
  delivered pixels, byte-bound reviewed focus, "AI ILLUSTRATION · NOT A PHOTO" badge, OFL fonts with hashed licence
  evidence, raqm shaping on, glyph gating (Königsberg renders correctly).
- **Weak:** `art_direction.type_scale` has no callers; spacing and motion are ~400 per-design constants; easing is
  defined four times; **tracked text is drawn glyph-by-glyph and loses kerning** (`src/typography.py:499-515`), so the
  "raqm everywhere" decision does not reach country names, kickers or eyebrows; **no OpenType feature is used
  anywhere** although Anton carries `smcp`/`c2sc`/`case`/`cpsp`/`kern` and Montserrat `tnum`/`pnum`/`onum` (the
  handover's "Anton has no true small caps" is wrong, so the small-caps decision rests on a false premise); checked
  copy can be **silently truncated with an ellipsis** (`src/typography.py:228-290`) on a channel whose premise is
  verified facts; **the lower half of every 9:16 graphic is empty** because the 35% bottom inset is Meta's *ads*
  guidance, not organic UI; land, borders and scale silhouettes are aliased ImageDraw next to 4x-supersampled markers;
  the stat card never restores its dimmed first figure on the held frame; **`src/design_cards.py` (4,410 lines,
  reviewer-passed 9/9/9) is not imported by the composer** and so produces no published output.
- **Cartography:** Natural Earth 10m/50m, relief, orthographic globe, polylabel, water names and an independent name
  checker are serious work, but projection, clipping, a shapefile reader and the label search are all hand-rolled
  (`src/geo.py:491, 523, 610, 686-764`; `src/graphics.py:2273, 2962-3056, 3371-3504`), which is why every fresh
  set of places exposes a new branch. Round-11 review: no wrong names on 98 renders; 13 below 9 on design.

### Audio (mastering 7, sound design 5)
- **Strong:** -14 LUFS / -1.9 dBFS ceiling, 4x-oversampled `alimiter`, loudness measured on the encoded AAC and
  corrected, sample-exact loop seam, rights evidence for every voice, track and effect, licensed-voice gate.
- **Weak:** findings 7-10 above; a 16 kHz 2-pole low-pass dulls the narrator's air (-0.7 dB at 12 kHz); limiter
  release 80 ms ripples on sub-heavy material; the limiter string is copy-pasted three times; `calibrate_gain` runs
  10-16 ffmpeg passes where `loudnorm` two-pass needs two; QA reads only integrated LUFS and true peak (no LRA,
  dialogue-vs-bed ratio, `silencedetect`, sibilance, phone-speaker check); 24 library tracks each used 72 times;
  ElevenLabs Music is a dead path (`src/audio_provenance.py:72-74` marks it unverified, so the gate blocks it); CC BY
  credit lines omit source and licence URLs for 17 of 24 tracks (`src/music.py:166-169`).

### Video (5/10)
- **Strong:** captions (8/10); sane mux/GOP/AAC flags; timeouts, encode slots, checkpoints; a font gate.
- **Weak:** findings 1-6 above; four lossy JPEG 4:2:0 generations before the render (`src/commons.py:695`,
  `src/colour_balance.py:278`, `src/beat_images.py:807`, `src/upscale.py:555`); one 1080p H.264 master for every
  platform (no 4K/HEVC Shorts variant although stills are already rendered at 2x); NVENC `p4 -cq 21` lands near x264
  CRF ~23 so quality depends on which machine rendered; encoder args are not in the manifest; the release gate cannot
  see a bad picture (no black/freeze/duplicate-frame, flash-safety, letterbox, colour-tag, SSIM or true A/V-offset
  checks, all available in the installed ffmpeg).

### Content (fact safety 7.5, craft 5)
- **Strong:** deterministic number/headline gates, two LLM checks, HMAC-signed records, AI label sent, trace check.
- **Weak:** finding 11; one ~2,000-word system prompt that is ~85% prohibitions with no exemplars, best-of-n or
  critic; a free-tier Gemini flash writer needed 6 rounds for one script; **circular sourcing on the free tier**
  (Antikythera sheet 35/35 quotes from en.wikipedia.org, `src/free_evidence.py:124-187`) makes the "independent"
  second check judge against the same sentences; a `rhetorical_framing` escape hatch passes untraced assertions
  (`src/trace_check.py:155-207`); the learning loops (hook, engagement, cover bandits) are well built and have never
  had data, and need ≥12 measured Reels per lever before they exploit; topic choice is four keyword rules
  (`src/db.py:43-75`); the 16:9 fallback can publish filler acts titled "Introduction / What the Evidence Shows".

### Monetization and policy (4/10 readiness, 7/10 compliance mechanics)
- **Strong:** fail-closed gate re-measures the master and binds rights to SHA-256; forced CC BY credits; BY-SA off by
  default; voice licence gated twice; idempotency keys; correct published/failed/unknown outcome model with no
  failover after unknown.
- **Weak:** finding 12; CC BY music from Freesound/Jamendo with no Content ID clearance field [UNVERIFIED that the
  specific tracks are registered; the risk class is real]; posting slots fixed at 12:00/19:00 ET with no feedback
  loop; AI flag is not passed on `publish_photos`; the blocked-voice message attributes the restriction to Meta CMP
  when it is the TTS vendor's licence (`src/publisher.py:709-711`); face blurring of incidental bystanders in
  licensed editorial photos costs quality for no platform-policy benefit [UNVERIFIED as policy; privacy.py applies
  it to every scene image]; Graph token expiry and API-version sunset are not watched; YouTube secrets are missing
  from the global log masker (`src/ops.py:37-53`); no affiliate/sponsor/membership hooks of any kind.
- **Niche adaptability (3/10):** the niche is hard-coded in the system prompts, banned-phrase lists, VPS word lists,
  beat archetypes, UNESCO ingestion, tier domain lists, meta categories, and the brand string in 11 modules. A
  `Niche`/`BrandProfile` config (YAML/JSON: prompts pack, beat template, hashtag families, evidence plugins, voice,
  palette, account ids, slots) is ~1-2 weeks and is the precondition for a second brand or for licensing the
  pipeline.

### Engineering (4.3/10)
- **Strong:** HMAC-SHA256 with domain separation and `compare_digest`; AES-GCM chunked backups with authenticated
  terminator; fsync+rename atomics; OS-level locks; PID-reuse-safe identity; WAL SQLite with per-call connections;
  secrets masked and never tracked; 3,429 tests with good conftest isolation.
- **Weak:** `main.py` 5,300 lines with a 1,065-line `main()`; `make_command_handler` 1,513 lines; `_process_update`
  cyclomatic complexity 300; **69 of 124 modules in one import cycle held together by 935 function-local imports**;
  `config.py` reads 146 env vars with 42 unguarded casts and no validation (291 `monkeypatch.setattr(config…)` in
  tests); 1,248 date-stamped changelog notes inside docstrings; **146 of 290 test files are date-stamped review-round
  files**, 22 assert on `inspect.getsource()` text, 0 golden-image diffs, 0 property tests, 0 coverage gate; CI is
  ubuntu/py3.11 only while production is Windows + DirectML + NVENC (msvcrt/ctypes/icacls paths never run in CI);
  no pyproject, ruff, mypy/pyright, pre-commit or lockfile; `anthropic` 1.13.0 is a major behind (2.5.0); no signal
  handling (a Windows task stop is a hard kill mid-ffmpeg); plain-text logs with no run_id; `.git` is 214 MB from
  tracked review JPEGs.

## 4. Tools and modules: what would make a real difference

Only entries that change the output or the defect rate. Everything else reviewed was judged not worth adding (listed
at the end).

| Priority | Tool | Purpose | Replaces | Effort |
|---|---|---|---|---|
| Now | ffmpeg `zscale` + `gbrp16` graph (in build) | One dithered BT.709 conversion; 16-bit grade | five bare `scale,format=yuv420p` sites and auto-inserted 8-bit round-trips | 2 days |
| Now | ffmpeg `loudnorm` two-pass, `deesser`, full `ebur128`, `silencedetect`, `astats` (in build) | Deterministic 2-pass mastering; sibilance; LRA/short-term; dropouts | 10-16-pass gain search; two-number QA | 1 day |
| Now | ffmpeg `blackdetect`, `freezedetect`, `photosensitivity`, `cropdetect`, `signalstats`, `ssim`, ffprobe tag checks (in build) | Release-gate picture checks | nothing | 2 days |
| Now | `pillow==13.0.0`, `uv` + `uv.lock` (hashes), `pip-audit` in CI | Close open CVEs; reproducible installs | requirements.txt, runtime pip-audit | 0.5 day |
| Now | Provider features: ElevenLabs `seed`, `pcm_48000`, pronunciation dictionaries; Azure `Riff48Khz16BitMonoPcm`, `<lexicon>`/`<phoneme>` | Lossless, deterministic voice; name control | MP3 intermediates, regex "prosody" | 1 day |
| Next | **Shapely** (GEOS) | polylabel, clip, buffer, simplify, point-in-polygon, union | `src/geo.py:371-391, 485-560, 598-684` and the island/enclave/seam code in graphics.py | 1 week |
| Next | **pyproj** | LAEA/orthographic/azimuthal centred on the site; geodesic routes; ellipsoid scale bar | hand-rolled affine lon/lat, `PolarProjection`, `great_circle`, `_km_per_deg` | 3 days |
| Next | **fontTools** | Font metrics, variable-font instancing, glyph coverage, kerning pairs; proves which features each font has | hand TrueType parser (`src/video_composer.py:277-290`), mask-compare notdef test | 2 days |
| Next | **uharfbuzz** (+ OpenType `features=` through Pillow) | Shape tracked/featured runs once; caption measure == caption draw; `smcp`, `tnum`, `case` | `_draw_tracked`, the unkerned-measure workaround | 1 week |
| Next | **pydantic-settings** | Typed, validated config object | 241 module globals, 42 bare casts, 291 test monkeypatches | 3-4 days |
| Next | **pyproject + ruff + pyright (basic → strict per package) + pre-commit + Windows/py3.13 CI matrix + coverage ratchet** | Stop regressions without review rounds | nothing | 1 week |
| Next | **faster-whisper** (CPU int8) | Transcribe the final voiceover and diff against the script: mispronunciations, dropped words | nothing | 2 days |
| Next | **sentence-transformers** (bge-small) and **SigLIP/CLIP** | Evidence selection by beat relevance; picture-to-beat pre-ranking | positional quote truncation; keyword storyboard scorer | 3 days |
| Next | **spaCy NER** + **textstat** | Every PERSON/GPE/DATE must be in the sheet; readability floor | part of the LLM trace check; nothing | 1 day |
| Next | Claude Sonnet for narration only (keep Gemini for packaging and checks) + an evals harness with a retention rubric | Hook and prose quality; measured prompt changes | single-shot free-tier writer; `improve.judge` | 3 days |
| Next | **google-api-python-client** (YouTube Data API) + monetary Analytics scope | Native upload, thumbnails, captions, playlists; RPM per video | Upload-Post as the only YouTube path | 3 days |
| Later | **skia-python** (or pycairo) | One anti-aliased vector rasteriser with sub-pixel transforms and OpenType text | ImageDraw polygons, 2x/4x supersample hacks, four Ken Burns copies | 3 weeks |
| Later | **pytest-regressions / syrupy** image snapshots, **hypothesis**, **import-linter**, **structlog**, **tenacity**, Git LFS for evidence | Golden-image diffs; property tests; enforced layering; correlated logs; declarative retries; a 214 MB → small `.git` | source-text tests, ad-hoc JPEG evidence, 935 local imports, 44 retry loops | 1-2 weeks |
| Later | Depth-weighted remap with the existing Depth Anything V2 Small map + `cv2.inpaint`/LaMa | Real 2.5D parallax | the unused depth call | 1 week |
| Later | `src/originality.py`, `src/brand_profile.py`/`Niche` config | Originality floor on the review card; multi-brand/multi-niche | nothing; 11 hard-coded brand sites and 20+ niche sites | 2-3 weeks |

**Reviewed and not worth adding:** Manim, Lottie/rlottie, resvg/CairoSVG, pyvips, Pillow-SIMD, libplacebo (no
Vulkan on the VM), SVT-AV1/rav1e uploads, RIFE/FILM, OpenColorIO/colour-science, VapourSynth, OpenCV CUDA
(DirectML host), Marigold/Depth Anything Large (non-commercial), HAT/SwinIR, scenedetect, noisereduce/DeepFilterNet/
RNNoise (TTS has no noise), WhisperX/MFA/aeneas for timing (provider timings are already sample-accurate),
pyrubberband (time-stretch is rejected by policy), essentia, matchering, instructor, LangGraph, guardrails-ai, a
vector DB, promptfoo, C2PA (platforms do not surface it for this media yet), Metricool/Buffer, facebook-business SDK,
poetry, APScheduler 4.x, Celery/Redis, Docker (DirectML/NVENC on Windows), libvmaf until the production ffmpeg has
it (use `ssim` now).

## 5. Roadmap (in order; each step is independently shippable)

1. **Week 1, output defects nobody has to design around:** colour matrix and 16-bit grade (findings 1-2); remove the
   grain (3); hard cuts and no fade-to-black (6); whoosh cap, SFX peak limiting and body, ducking release (7-9);
   PCM voice + seed (10); Pillow 13 + lockfile + CI audit (13). Expected: visibly cleaner uploads on all four
   platforms, a narrator that stays in front of the mix, and reproducible masters.
2. **Week 2, the gate learns to see and hear:** ffmpeg picture checks in the release gate; `loudnorm` two-pass and
   the fuller audio QA; encoder args in the manifest; host-independent delivery encode (x264 for delivery, or tuned
   NVENC); a 4K H.264 Shorts variant from the 2x graph.
3. **Weeks 3-4, the opening three seconds:** hook enforcement, beat restructure, hedge rule, narration on Sonnet with
   exemplars and a judge, evals harness, pronunciation map + whisper check. Then feed the bandits one retention
   reward and cut their arms until ≥40 measured Reels exist.
4. **Weeks 4-6, geometry and type on real libraries:** Shapely + pyproj under the maps (the round-12 that ends the
   review loop), fontTools/uharfbuzz with OpenType features, one shared `kenburns()`, wire `design_cards` into the
   composer, let 9:16 figures use the full frame.
5. **Weeks 6-8, engineering floor:** pyproject/ruff/pyright/pre-commit, pydantic-settings, Windows + py3.13 CI,
   fold the 146 round-files into canonical tests, import-linter, structlog + signal handling, LFS for evidence.
6. **Weeks 8-12, monetization and niches:** originality score on the review card, YouTube monetary analytics and
   policy watch, Content-ID-safe music policy, native YouTube upload, posting-time learning, then the
   `BrandProfile`/`Niche` extraction that makes a second niche a config file.

## 6. Decisions for the operator

- **Originality:** the strongest lever is one operator-authored element per Reel (3 s on-camera intro, a desk/hand
  shot, original B-roll). Code can measure originality; it cannot manufacture it. Decide whether that is in scope.
- **Grain:** keep the film texture (seeded, at 2x, ≤3) or drop it. The review recommends dropping it for delivery.
- **Face blurring:** keep for operator uploads; consider turning it off for licensed CC/PD archival photos
  [UNVERIFIED as a policy requirement; verify against Meta/YouTube privacy rules before changing].
- **Small caps:** Anton does carry `smcp`; the earlier "no faux small caps" decision stands, but true small caps are
  now an option for captions and kickers.
- **Dependencies to approve** (per AGENTS.md, no install without approval): pillow 13.0.0, uv, Shapely, pyproj,
  fontTools, uharfbuzz, pydantic-settings, faster-whisper, sentence-transformers, spaCy, textstat,
  google-api-python-client, pytest-regressions, hypothesis, import-linter, structlog, tenacity.

## 7. Method and limits

- Six parallel read-only reviews on 581a866, each with file:line evidence; the coordinator re-verified the top
  findings in source and ran `pip-audit` for the Pillow advisories.
- No production renders, music audio or voice samples exist in this checkout; the audio and video measurements were
  made on synthetic inputs through the real filter chains and are indicative, not production numbers.
- Platform-policy statements that could not be confirmed from code or a document in the repository are marked
  [UNVERIFIED]; verify against the current Meta and YouTube policy pages before acting on them.
- The underlying reports (one per subsystem) are longer than this synthesis; ask for any of them in full.

## 8. Addendum (2026-11-05): real-footage sourcing

Missed by the six reviews: footage sourcing fell between the video review (composition) and the policy review
(rights), and neither brief named `src/footage.py` or `src/archive_film.py`. Assessed by the coordinator afterwards.

**What exists.** Operator-supplied clips (`/footage <topic>`, `src/footage.py`) are the only footage a normal run
uses (`main.py:2905`). Archive film (`src/archive_film.py`, operator decision 2026-10-28 "real film over AI motion")
searches the Internet Archive and Europeana for openly licensed film, downloads candidates to a pending folder with a
rights record, and uses nothing until the operator approves it. It is rights-safe (licence read from the item's own
record, broadcast captures excluded, host allow-list, size cap, re-encode, forced credit, 40% share cap). AI motion
(Veo/Kling) is off at the default `MEDIA_MONTHLY_BUDGET_USD=0`. YouTube/Pexels/Pixabay scrapers were correctly
retired (eb23e47); `docs/sources.md:35-49` still describes crediting them.

**Why it is not a working source of topic-specific footage.** (1) It runs only on the Telegram command `filmfind`
(`src/control.py:2947-2951`); no pre-run or nightly job calls it, unlike photo pools. (2) The two archives are thin
for this niche (the code itself notes Europeana's Antikythera films are educational-only). (3) The sources with real
coverage are searched for images only: Wikimedia Commons uses `filetype:bitmap` (`src/commons.py:204`) and NASA
`media_type=image` (`src/museum_sources.py:114`), so Commons' CC/PD `.webm` heritage clips and NASA's video library
are never seen. (4) Relevance is a title/subject keyword match (`src/archive_film.py:65`) with no vision check of the
clip. (5) No `assets/*/footage/` exists in the checkout; the path has never run on a production Reel.

| ID | Finding | Acceptance |
|---|---|---|
| QF-36 | Add Commons video (`filetype:video`, `.webm`/`.ogv`) and NASA video (`media_type=video`) to the existing photo-pool fetch, through the same licence checks, vision review (3 frames: shows the named site; no burned-in text, logo or watermark) and approval sheet the photos get. | A topic with Commons video coverage (e.g. a UNESCO site with drone passes) yields ≥1 approved clip in the pool in dry-run; rights record bound to the bytes; credit line present. |
| QF-37 | Run `archive_film.find` in the pre-run/nightly pool job so pending films wait on the review card; keep the operator approval gate. | Nightly job log shows the search per next topic; `filmfind` becomes optional. |
| QF-38 | Vision-check archive-film candidates as photos are checked; label GENERIC vs actual-site from the check, not from the record text. | Test with a deliberately off-topic film that is rejected. |
| QF-39 | Docs: remove Pexels/Pixabay from `docs/sources.md`; README's Footage row describes archive film; originality score (QF-31) weights operator-shot footage above archive footage. | Docs match the code; originality test shows the weighting. |

Add these rows to `docs/reviews/quality-fixes-2026-11/STATUS.md`; Phase 6, after QF-31.
