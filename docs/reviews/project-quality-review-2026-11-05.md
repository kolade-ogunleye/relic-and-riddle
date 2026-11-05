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

## 9. Gap sweep (2026-11-05, end to end)

Eight further read-only reviews closed the areas the first six did not touch: photo sourcing and review,
prompt-injection resistance, AI illustrations and motion, operator UX/ops/topics/cost, surface economics and asset
quality, accessibility/legal/docs, ops resilience/CI/deployer, and a completeness critic over the whole repository.
The coordinator verified every stop-ship row in source. Rows continue the ledger from QF-40; add them all to
`docs/reviews/quality-fixes-2026-11/STATUS.md`. Rows marked **STOP-SHIP** come before Phase 1 of section 5.

### 9.1 What the sweep changes

The first review found output-quality defects. The sweep found **trust and money defects that outrank them**: a
targeted Wikipedia edit can produce a signed, "verified" false fact; prompts carry untrusted text with no role
separation; illustrations bill an uncapped key; four runbooks call production a dry run while it posts live; a
Telegram button pip-installs into the live daemon; and the repository carries the operator's identity in 154 files.
Separately, the asset bench is weaker than its records claim: the narrator defaults to a stock ElevenLabs voice id,
the "Kodak 2383" LUT is a formula whose colour the composer discards, ten of 25 sound effects are clipped (reviewer
measurement), and every topic-bank hook breaks the writer's own 14-word rule.

Scores (reviewer /10): photo query quality 4, photo ranking 4, pool size 4; LLM trust boundaries 3, injection paths
3; illustration fidelity judging 4.5; operator UX 6.5, operations 6, topic discovery 3, cost accounting 4,
localization 2; disaster runbooks 3, performance budget 3, CI reality 4, self-update/scripts 3, deployer 6; WCAG
partially met (1.2.2 open captions yes, 2.3.1 unmeasured, 1.1.1 partial); legal: repository not licensable as-is.

### 9.2 Ledger rows

| ID | Finding (evidence) | Fix | Acceptance |
|---|---|---|---|
| **QF-40 STOP-SHIP** | One Wikipedia edit plus one self-hosted page it cites makes a false number "strong": `two_sites` counts any two hosts regardless of tier (`src/fact_sheet.py:257-260`); `free_evidence.article` fetches every `extlinks` page (`:42-74`); both checkers judge against the same quotes; HMAC signs the result. Reproduced end to end with mocked network. | Corroboration only among tier ≤2 allowlisted hosts; a Wikipedia-cited page inherits tier 3 and never corroborates; a number needs ≥2 allowlisted hosts or a hedge; sign evidence provenance (host tier, fetch path) and show "sources: Wikipedia + 1 site it cites" on the card. | Fixture with Wikipedia + attacker-host quotes carrying one figure → `disputed=True`, hedged; `verify_field` refuses a number/quote field whose only tier ≤2 host is wikipedia.org. |
| **QF-41 STOP-SHIP** | Gemini text path sends `f"{system}\n\n{prompt}"` as one string (`src/gemini.py:129, 315`); no quote is escaped or fenced; `</evidence_quotes>` inside a quote passes verbatim. | Use `system_instruction` + `contents` roles on every Gemini call; escape `<`/`>` in quotes; wrap evidence in a per-call random fence and reject any model output that echoes it; apply the same fence to Commons captions in `scene_planner`/`image_review` (JSON-encoded, ≤120 chars, imperative sentences stripped). | Test asserts `body["system_instruction"]` set and `contents` free of system text; captured prompt contains no literal tag from quote text; injection-corpus test file (≥10 payloads) passes. |
| **QF-42 STOP-SHIP** | Commons `Artist`/file title are force-published verbatim in caption and description (`src/credits.py:125-131`, `main.py:1049-1063`), required by the release gate (`src/release.py:71-74`), exempt from fact/banned-phrase checks (`src/trace_check.py:98-103`), and hidden on the card when the caption is long; a photographer's email is tracked (`assets/derinkuyu_underground_city/credits.json:711`). | Credit with author name + Commons page id or a ≤60-char sanitised title; strip URLs/#/@/emails from author/title at ingest; run credit lines through the URL/banned-phrase check; always render every credit line on the card. | `asset_attribution({"title": "File:x t.ly/y #tag"})` has no URL/hashtag, ≤60 chars; `_card_text` contains 📷 for every asset; no email in any `credits.json`. |
| **QF-43 STOP-SHIP** | Writer-controlled fields reach publication with shape checks only; YouTube title/description and alt texts are posted but never shown (`src/telegram_bot.py:1311-1340`); the card's caption is not the posted one (`_caption_for` and the illustration disclosure run after Approve, `src/publisher.py:599-634`); cost, slot and expiry absent. | Allowlist of publishable fields, each rendered on the card as the exact final string per platform; slot and expiry line; hashtags from a per-topic candidate list; `build_envelope` raises on a field outside the card fingerprint. | Card test: a run with `picture_fit.generated` shows `ILLUSTRATION_DISCLOSURE`; youtube_title/description present; slot line equals `post_schedule.next_reel_slot`. |
| **QF-44 STOP-SHIP** | SSRF: `page_text` checks scheme on the first hop only and follows redirects (`src/free_evidence.py:55-74`); museum/reference image downloads have no host allowlist (`src/commons.py:676-683`, `src/image_gen.py:714-731`). | `allow_redirects=False` with manual hop checks, reject RFC1918/loopback/link-local, HEAD content-type precheck; route every image provider through `footage._host_allowed`. | Mocked 302 to `http://127.0.0.1/` → `page_text` returns None; `download_jpeg` for an off-host museum URL raises. |
| QF-45 | Learned writer rules (`src/improve.py:584-635`) are LLM-drafted from checker text and appended to every prompt after one tap; `_draft_rule` is unfenced. | Rules must match a grammar (Never/Always + wording phrase), reject `#`, URLs, proper nouns; show provenance on the Adopt card. | `_draft_rule` output with `#` or `http` is discarded (test). |
| **QF-46 STOP-SHIP** | Illustration generation bills `GEMINI_API_KEY` with no cap or ledger: zero `media_budget` calls in `src/image_gen.py`; up to 4 candidates × 8 calls per run (`src/config.py:496-499`). | Route through `media_budget.allow/record`; add an "AI images" spend line; price constant marked [UNVERIFIED] until read from billing. | With `MEDIA_MONTHLY_BUDGET_USD=0` a run makes zero image requests; a fake 200 inserts one `media_usage` row shown on the dashboard. |
| **QF-47 STOP-SHIP** | Docs say production runs `daemon --dry-run` (`docs/deployment.md:59,184`, `docs/runtime-contract.md:19`, `docs/unattended-startup.md:26`, `docs/production-probe-handoff.md:5`); code runs `daemon` live (`src/service_runtime.py:91`). CI tests the obsolete `handoff/tools/content_runtime.py` copy that still says `--dry-run`. | Correct the four docs; show "📡 LIVE posting / 🧪 dry run" on the dashboard masthead; delete `deployment/windows/handoff/tools/*` and `handoff/tests/*`, point CI at `runtime/`. | Docs test greps the four files for `daemon --dry-run` and fails; CI unittest path is `runtime/tests`. |
| **QF-48 STOP-SHIP** | `maintenance.apply` (Telegram `upd:apply`) pip-installs into the live venv, runs the full suite inside the daemon, commits and ff-merges the production checkout (`src/maintenance.py:160-187`), which moves HEAD off the signed tag and pauses the deployer. | In production `apply()` raises; `check()` stays report-only; updates arrive as a PR → signed tag through the deployer (Dependabot/Renovate). | Production env: no `upd:apply` button, `apply()` raises before touching git. |
| **QF-49 STOP-SHIP** | Operator and infrastructure identifiers tracked: `omokolade` in 154 files (SFX rights `path` fields, review evidence JSON, docs, deployer scripts), production hostname in 9, GitHub handle in a User-Agent and fixtures, live Telegram forum topic IDs in `.env.example:244-248`. | Rewrite paths repo-relative (re-sign rights records if the path is hashed); placeholders for hostname/handle/IDs; `BRAND_CONTACT` setting for the UA; purge or regenerate `docs/reviews/**/*.json` evidence; `tests/test_no_operator_identifiers.py` with a denylist. | `git grep -Il "omokolade\|lab\.internal\|kolade-ogunleye\|@gmail\.com"` returns nothing outside an allowlisted attribution; the new test is in the suite. |
| **QF-50 STOP-SHIP** | Face crops of identifiable people go to Google's free-tier Gemini (`src/privacy.py:128-262`), no processor named, no data map; `audit_log` holds approver ids with no notice. | `FACE_VERIFY_CLOUD=false` default (local YuNet + geometry); when on, paid tier/DPA and processors named; `docs/privacy.md` data map (purpose, retention, processors, deletion) covering `audit_log`, `audience_*`, `cache/privacy`, `assets/**`; prune rule for `audience_*`. | Test: no HTTP call from `detect_human_faces` with the flag off; `docs/privacy.md` exists with the four tables. |
| QF-51 | Config drift: `YOUTUBE_ANALYTICS_*` read outside config and missing from `SECRET_NAMES` (`src/youtube_analytics.py:34`, `src/ops.py:30`); `RELIC_DEPLOY_REQUEST/STATUS/RELEASE_RECEIPT` undocumented; `tests/test_settings_inventory.py:10` only matches literal `os.getenv("X")`. | Move the three YouTube vars into config and the masker; document the RELIC_* launcher vars; extend the inventory regex to `_env(` and `os.environ.get`. | Inventory test fails on the current tree and passes after. |
| QF-52 | Photo fetch floor is aspect-blind (`src/commons.py:35-36, 577`): 30% of 243 pooled records have a 9:16 crop under 1080 px, 40% under the 1,350 px upscale threshold (Gol Gumbaz 16/16 are 2.2:1 panoramas). | `crop_w = min(w, h*9/16)`; refuse <1080; rank by `crop_w` with a bonus for 3:4/4:5/1:1; request originals when height matters. | New pools: 0 under 1080 crop, ≤10% needing upscale. |
| QF-53 | Pools are one-photographer bursts with near-duplicates; dedup is model-only; `image_quarantine.phash` never applied to photos. | `imagehash` pHash/dHash at fetch (≤10 bits = duplicate), author share cap ≤35%, per-shot-class quota (wide/interior/detail/people-scale/archival). | No two pool photos within 10 bits; max author share ≤35%; ≥3 shot classes per approved pool. |
| QF-54 | 7/31 topics have one search query (`data/topics/image_queries.json`); `candidates()` requires a keyword in the file *title* (`src/commons.py:568`) so categorised files with code-named titles are dropped; English only. | Per-topic structured spec ≥6 queries over ≥4 shot types; title OR category/SDC match; `haswbstatement:P180=<QID>` (depicts) and Wikidata P373 as the anchor; local-language aliases from Wikidata labels. | On 5 test topics ≥2× candidates with no rise in `shows_topic: no`. |
| QF-55 | Category fallback (`scripts/fetch_commons_images.py:177-186`) has none of `topic_anchor`'s namesake guards; only 8/31 topics have an `exclude` list. | Route the fallback through `topic_anchor()`; require `exclude` or a QID per topic. | 0 `shows_topic: no` among category-fallback files over 10 fetches. |
| QF-56 | One free-tier call over 24 thumbnails at 512 px is the whole technical review (`src/image_review.py:33-83`); no watermark/cast/HDR/compression check; no original-vs-reconstruction distinction (a Kotsanas reconstruction outranks the Antikythera fragments). | Deterministic pre-checks (sharpness, clipped highlights, grey-world cast, JPEG quant quality; pyiqa optional); ask for `quality 1-5` and `depiction ∈ {original, reconstruction, replica, museum display, drawing}`; ≤12 images per call at ≥768 px. | Every pool record carries quality + depiction; reconstructions never outrank originals for the topic tier. |
| QF-57 | "Public domain" accepted as worldwide PD though Commons uses it for PD-US-only (`src/credits.py:354`) while LoC US-only items are refused; the fetch script's own Openverse search bypasses the off-by-default guard and has no host allowlist (`scripts/fetch_commons_images.py:59-84, 211-238`). | Parse PD templates/categories and require PD-old-70/PD-Art/CC0 or author death date (Wikidata P570); delete the script's Openverse code and call `museum_sources.openverse_search` behind the flag with Flickr licence re-verification; refuse NASA items crediting a non-NASA agency. | 0 PD records without a recorded worldwide basis; Openverse records only with the flag on and `license_verified_at`. |
| QF-58 | Readiness floor `MIN_SCENE_IMAGES=5` for ~14 beats guarantees repeats and 9-s holds (`src/scene_planner.py:1381, 1443`); 7/25 asset topics unreviewed; no refresh policy; detail crops pick by edge density. | Beat-driven readiness (≥9 distinct usable); fetch until shot-class quotas are met; 90-day re-fetch for pools <12; saliency for detail crops. | No Reel with a repeat or a hold >6 s; ≥9 distinct photos per Reel. |
| QF-59 | Beat-matching fallbacks are bag-of-words over 160-char captions (`src/scene_planner.py:433-450, 1283-1297`); planner images at 512 px; the "different site" rule is prompt text only. | SigLIP/CLIP line→photo ranking for swaps and `related_shot` (dependency approval needed); deterministic check that a candidate's place names ⊆ topic names for named-subject lines; planner images at 768 px. | First-pass fit rate ≥85%; 0 post-render "different site" findings over 10 runs. |
| QF-60 | `_RECONSTRUCTION` regex (`src/image_gen.py:99-105, 118`) fires on ordinary sentences ("Miners worked the salt by hand") and relaxes the nothing-added rule (`src/image_quarantine.py:311-332`). | Drop the regex; the pre-voice structured verdict emits `reconstruction: bool` with a reason, restricted to original-appearance / no-photo-exists interior / mechanism-in-use / construction. | 25 ordinary lines → ≤1 flagged; the three example lines return `""`. |
| QF-61 | Kind-of-thing illustrations have no count/number fidelity check (`src/image_gen.py:540-544`, `src/image_quarantine.py:266-292`); the prompt carries "30 bronze gears… 34 cm" but nothing checks 30 came back. | Extract number+noun pairs and measures into the spec; judge returns `{element, shown, count_seen}` per item plus "list everything depicted"; reject count mismatch or any element not in the facts. | Mocked `count_seen=2` for "three masts" → rejected; listed "cannon" absent from facts → rejected. |
| QF-62 | Anachronism is one boolean against an era from `min()` of every year in the sheet (`src/image_gen.py:496-505`): a sheet with only "excavated 1963" yields a Victorian idiom; modern country name injected as setting. | Checklist from the subject's own dates and region (period range, materials, forbidden: modern objects, text, maps, flags, faces) with per-item structured verdicts; refuse to illustrate when the sheet has no date for the subject's own time. | `derive_era` on "discovered 1901" → not industrial; mocked "looks 19th-century European" for a Bronze Age spec → rejected. |
| QF-63 | Reference comparison passes on the model's self-reported confidence ≥0.85 (`src/image_quarantine.py:378`, `src/config.py:511`), same vendor family as the generator, judged on a 1024 px q80 JPEG of a 3-level quantised composite. | DINOv2-small or SigLIP similarity between candidate edge map and references as a hard floor (dependency approval needed); route the reference role to a different vendor (`llm.structured_call`); judge the raw PNG at ≥1536 px; calibrate on ≥30 labelled pairs. | Reference vs a different object with mocked "yes/0.95" → rejected by the floor; `vision_models()["distinct"]` asserts different vendors. |
| QF-64 | OCR gate runs un-inverted on dark-ground art and rejects any 3-letter token (`src/image_quarantine.py:58, 80-107`); `pytesseract` is not in `requirements.txt`, so with `IMAGE_GEN_REQUIRE_OCR=true` the whole tier is silently skipped; `doctor()` does not mention OCR. | Declare the dependency; invert + Otsu, both polarities, `image_to_data` with conf ≥60 and dictionary filter; report OCR state in `doctor()`. | Hatched engraving without text passes; inverted 40-px "ROMA" rejected; `doctor()` contains "OCR". |
| QF-65 | Illustration record lacks prompt, raw sha256, candidate index and judge verdicts (`src/image_gen.py:1008-1013`); `is_ai_generated` never forced by an illustration (`src/publisher.py:662`); `only_strong` fallback and `strong_shot` let illustrations into Stories/carousel unlabelled (`src/image_review.py:185-188`, `src/multi_format.py:524-527`, `src/scene_planner.py:131-144`). | Record all four; `is_ai_generated = META_IS_AI_GENERATED or used_illustrations(payload)` and same for `containsSyntheticMedia`; filter `is_illustration` out of Story/carousel stills and `strong_shot`. | Lineage has `prompt` and `raw_sha256 == sha256(best.png)`; envelope flag true with generated pictures; Story sequence from `[gap_gen_x.jpg]` draws no illustration frame. |
| QF-66 | Motion clips are never compared with the photo they animate; checks are clip-frame vs clip-frame (`src/motion.py:339-357`); no text/watermark/flicker/resolution gate; 720p results silently upscaled (`:305-313`). | Anchor-vs-frame-0 `ssim` ≥0.85 and homography; `picture_look` on f0 and f_end for burned-in text; `signalstats` YAVG flicker check; `probe` width ≥1080. | Clip from photo B with anchor A → rejected; burned-in word → rejected; 720p raw → rejected. |
| QF-67 | Every illustration/motion threshold is uncalibrated (six pilot clips, synthetic tests); every test mocks the judge and OCR. | `scripts/calibrate_gates.py` over kept accepted/rejected artefacts; a 6-engraving fixture set run through `raw_rubric` + real OCR in CI. | Script prints per-threshold tables; fixture test passes without mocks. |
| QF-68 | No cost per Reel: `llm_usage`/`media_usage` have no `run_id` (`src/db.py:161-176`); TTS characters never recorded; the V2 card's "$0.41" is a literal. | `run_id` contextvar + columns; record TTS chars × rate per voice stage; "💳 This run" line on card, receipt and `/reviews history`. | A dry run leaves ≥1 usage row with its run id; the card shows the sum. |
| QF-69 | The `TELEGRAM_CARDS_V2` card is a mock with invented values (`src/telegram_bot.py:1673-1753`) and undo paths that bypass the fingerprint (`:2096-2141`). | Derive every row from payload/release/credits or omit it; remove undo-approval unless it cancels bookings atomically. | `json.dumps(blocks)` contains none of the literals; undo of a posted Reel is refused. |
| QF-70 | A missed review window (floor 1 h, can close 20 min before the slot) fails the topic for 7 days (`main.py:1364-1377`, `src/config.py:263`); an open review blocks release installs. | Never auto-fail: `pending` with a stale badge under Needs you; 12-h floor; deployer treats an open review as idle. | After the window the topic stays `queued`; `release_gate` reports idle. |
| QF-71 | Every notification rings in V1 (`src/telegram_bot.py:2345-2349`). | `disable_notification` unless `group == "needs"` or the review card. | `notify("x", group="info")` sends `disable_notification=true`. |
| QF-72 | Backup RPO is a day with unbounded snapshots (`src/backup_snapshot.py:9`, `docs/backup.md`); a lapsed restore drill pages nobody. | Hourly SQLite online backup into the spool; prune to N verified snapshots; "last snapshot X h / last drill N d" on `/status` and heartbeat, alerting >26 h / >30 d. | Heartbeat with a 27-h-old snapshot contains "backup stale". |
| QF-73 | Topic bank: 31 topics, `sources` on 1, 31/31 hooks over `HOOK_MAX_WORDS=14`, 12 open on a date, `topic_corrections.json` is a byte-identical duplicate, no sensitivity flag (Dyatlov, Hypogeum, Odessa, Acoustic Kitty, Amber Room), no sub-Saharan Africa/Mesoamerica/SE Asia/Oceania, `#ReelsViral` on 30/31, proposals only on a manual tap. | Rewrite hooks ≤14 words riddle-first or stop passing them; delete the duplicate; `sensitivity` field with spacing and a review-card chip; nightly propose 3 when queued <14; require ≥1 source on add; add 8 topics from empty regions; drop `#ReelsViral`. | `hook_problems()` empty for the whole bank; no region >40%; sensitive topics never on consecutive days; `scripts/lint_topic_bank.py` fails on an entry without sources. |
| QF-74 | No run deadline: `run_in_worker` has no timeout (`main.py:3966`); only `FFMPEG_TIMEOUT_SECONDS=3600` per call inside a retry loop; a late run silently books tomorrow's slot. No per-stage budget; `src/stage_timer.py` has 0 tests. | `run_in_worker(timeout=150*60)` with `timed_out` status and a needs alert; "missed today's slot" alert; `data/stage_budget.json` (script ≤20, voice ≤8, pictures ≤25, render ≤15, QA ≤5, upload ≤5 min) with WARNING + finding at >150%; per-stage p50/p90 in the Monday report. | Worker over timeout → `timed_out`, topic requeued, one needs message; a stage over budget writes a finding. |
| QF-75 | CI never exercises the maps or Windows: ~185 gated tests, all of `src/geo.py` skipped, no Windows job, Pester never runs; zero-test modules `atomic_files`, `stage_timer`, `topic_store`, `shot_archetypes`; 3 tests read fixtures from `docs/reviews/*`. | Cache `tools/geo` in CI and run the fetch scripts; skip ceiling (fail if skipped >25); `windows-latest` job with pytest + Pester; tests for the four modules; fixtures moved to `tests/fixtures/`. | CI summary shows skipped ≤25; Windows job green. |
| QF-76 | A signed release tag installs an unsigned dependency set (`deployment/windows/lib/ContentReleaseDeployer.ps1:151`, network fallback in `Update-ContentReleaseTree.ps1:54`). | `requirements.lock` with hashes (from QF-09's uv.lock); deployer uses `--require-hashes --no-deps` and refuses the network fallback. | Pester asserts `--require-hashes` in the pip argument list; CI fails if lock ≠ requirements. |
| QF-77 | No provider runbook; Upload-Post absent from `probe_live_services`; ElevenLabs voice id never checked (a 404 ships the next Reel in Azure's voice with a log line, `src/audio_engine.py:521-548`); retired Gemini model ids fail on all three keys (`src/free_llm.py:240`); no `/debug_token` expiry warning; no secret-rotation procedure; rotated keys need an unscheduled restart (`src/env_sync.py:213-216`). | `docs/runbooks/{publishing,voice,credentials,models}.md`; probe Upload-Post and `/v1/voices/{id}`; review flag when the voice provider differs from the last published run; weekly model-id check against `GET /models`; `/debug_token` warning at <7 days; `env_sync` restarts at the next idle lease on a changed key. | `health --live` fails on a 404 voice or non-200 Upload-Post; provider change → review flag; models-list test raises a needs alert. |
| QF-78 | `ELEVENLABS_VOICE_ID` defaults to `pNInz6obpgDQGcFmaJgB` (`src/config.py:211`, `.env.example:110`), a stock premade id [UNVERIFIED name]; `data/voice/voices.json` (the designed voice) has no reader in `src/`; the narrator was chosen only for a 44.3-s take length. | Default to `voices.json`'s narrator id; `health --live` fails when `.env` differs; `data/voice/listening.md` with a 3-script blind round scored on warmth, pace, name pronunciation; approved 30-s sample bound by SHA-256. | `health --live` fails when the configured id is not in `voices.json`; listening record with scores exists. |
| QF-79 | 31 of 48 scripts have no test; `eval_models.py`/`eval_facts_first.py` are stale (compare Opus/Sonnet while production writes with Gemini); `eval_prompts.py` is the only eval that measures the production writer; one-off migrations still live. | Classify (live / dev tool / validation / eval / one-off), move one-offs to `scripts/archive/`, make `eval_prompts.py` the QF-17 harness base. | `scripts/README.md` lists every script with its class; archive contains the migrations. |
| QF-80 | Stories: the "poll" frame cannot carry a poll sticker through the API (`src/multi_format.py:517-520`), nothing outside that module mentions placing one, and frames auto-post 45 min after the Reel (`main.py:3373-3385`): a question over an empty band ships every episode. Story insights feed nothing. **Operator decision.** | Cut frame 3 now; pause frames 1-2 until IG followers ≥1,000 and story reach ≥20% of the Reel's 1-d reach for 10 episodes. | No auto-posted frame contains `poll_sticker_room`; `/posts` shows the chosen count. |
| QF-81 | Carousels: 7 rendered per week (14 renders, 7+ vision calls, full QA) into 3 Tue–Thu slots with 7-day expiry, so ≥4 of 7 expire unposted (`main.py:3528-3553`, `src/post_schedule.py:264-272`); Graph insights skipped for `_carousel` (`src/audience.py:453`) so saves/reach are never read; the FB gallery doubles the cost. **Operator decision on FB gallery.** | Render lazily for the run that takes the slot; one copy; enable Graph insights for carousel albums; FB gallery off until FB Reel reach ≥50% of IG. | `carousel/` exists only for booked runs; digest prints saves/reach for carousel vs Reel after 12 posts. |
| QF-82 | Covers: variants a/b/c rendered every run though the selector ignores them (`src/visual_engine.py:308-315`, `src/cover_selector.py:37`); the bandit scores covers on Reel-content signals. | Render final + grid only; freeze the bandit until a cover-specific signal exists (YouTube long-form CTR or a manual IG test). | No `cover_a/b/c` in a run folder; bandit `choose` returns the fixed style while frozen. |
| QF-83 | `YOUTUBE_SHORTS=false` by default (`.env.example:147`) on the one platform with Shorts revenue; the Short thumbnail call is dead by its own docstring (`src/publisher.py:206, 784-794`); the Reel carries no on-screen follow ask (FOLLOW exists only on the paused 16:9 end screen). **Operator decision on the follow overlay.** | Default Shorts on; drop the thumbnail call; optional 1-s FOLLOW overlay on the loop bridge. | Dry run posts a Short; no thumbnail request logged. |
| QF-84 | Music: `choose_mood` defaults to "mysterious" on any LLM failure (`src/music.py:47-60`), so 360 picks fell on 5 tracks and the melancholic set has never played; `uses` increments in dry runs (`:206-213`); the set is four genres from eight uploaders; 23/24 need a loop seam. | Mood from the sound plan's dominant mood; `published_uses` from the publish path; replace with a 4-mood × 3-track set from one or two composers in one key/tempo family (rights per QF-33). | After 12 published Reels no mood >50% and every track ≥1 published use. |
| QF-85 | SFX (reviewer measurement with ffmpeg astats/ebur128): 10 of 25 files clipped (`impact_braam` 10,374 flat samples at +2.3 dBTP, `stone_door` 2,060, `hammer` 689) though `src/sfx_palette.py:60-61` claims clipped takes are rejected; 34.6 LU spread; five effects below 120 Hz; keyword triggers mickey-mouse the narration (`:88-116`). | Regenerate the clipped files with headroom; reject on `Flat_factor>0` or TP >−1 dBTP; normalise to −20 LUFS-S before stand-in matching; re-prompt hiss effects with 100–300 Hz body; cut the table to ≤12 effects tied to settings and plan actions. | Every file: Flat_factor 0, TP ≤ −1 dBTP, I between −26 and −14 LUFS; hits' spectral centroid 150 Hz–3 kHz. |
| QF-86 | LUT: `assets/luts/kodak_2383.cube` is a per-channel sigmoid plus a sine (`scripts/generate_kodak_lut.py:17-45`) titled with Kodak's mark and called "authentic"; the composer discards its colour (`src/video_composer.py:2896-2922`) because it turned gold lime; measured effective curve lifts black to 7.4/255, white to 244, keeps 189/256 levels; brand tokens are graded with the photos. | Grade photo layers only, in 16-bit (ties to QF-02); black at 0; delete the cube and generator or license a measured LUT; rename honestly; remove broadcaster names used as "standards" in `src/privacy.py:8`, `src/multi_format.py:1871`, `main.py:994`. | Graphic-only frames within ΔE 2 of `brand.py` tokens; ≥250 grey levels into the colour conversion; `git grep -i "kodak\|national geographic\|\bBBC\b\|economist" src scripts assets main.py` returns only domain lists. |
| QF-87 | Fonts: Bebas Neue has no caller; Montserrat weight 700 ships twice; the Cinzel masthead is ~4.6 px on a profile-grid tile; OpenType features unused (coordinator verified with fontTools: Anton v2.116 `smcp` maps 244 glyphs, `c2sc`, `case`, `cpsp`, `kern`; Montserrat v9 `smcp` 438, `tnum`, `pnum`, `onum`, `case`). | Delete Bebas and its evidence row; one Montserrat source; masthead absent or ≥9 px cap height at tile size; implement features (QF-21): Pillow `features=["kern","case"]` default, `["tnum","lnum"]` for figures, `["smcp"]` where small caps are wanted (raqm required); libass exposes no feature tags, so captions stay full caps unless a derived font with `smcp` as default forms is built with fontTools. | `fonts/` lists only `FONT_FILES` entries; grid proof asserts the masthead rule; render "Königsberg" with and without `smcp` and assert different bounds with accents intact. |
| QF-88 | Accessibility: no photosensitivity check (extends QF-10; transitions stay under 3 flashes/s but archive film, AI hook clips and operator footage are unmeasured); non-text contrast and colour-blind legibility of published graphics never measured; `CAPTION_MAX_CPS=17` logged not enforced (`src/video_composer.py:1735`). | `photosensitivity`/`blackdetect`/`freezedetect` over the master in `src/release.py` and over footage at ingest; CVD simulation (protan/deutan/tritan) gate on graphics; enforce CPS. | A 10-Hz strobe scene is blocked with a named reason; a map passes the CVD gate; a 20-cps burst fails the build. |
| QF-89 | Instagram gets no caption track; Facebook CC depends on a Page token after posting; nothing records which posts have CC; Stories/Reels have no alt text; diagram slides' alt text names neither site nor country (`src/multi_format.py:2338-2340`); synthetic images are flagged only visually. | `captions_track` per platform in the receipt and on the card; alt text built from the graphic's signed spec; one-line description + "AI illustration" note in the Story/Reel caption when a synthetic image is shown. | Every facebook/youtube publication row carries caption status; map slide alt names site and country. |
| QF-90 | Third-party model licences unrecorded (YuNet, Haar cascade, Depth Anything export, Tesseract); no `THIRD_PARTY_NOTICES.md`; a missing `tools/yunet.onnx` is silent and the degraded result is cached (`src/privacy.py:66-67`). | `THIRD_PARTY_NOTICES.md` and `tools/MANIFEST.json` (file, sha256, source, licence, converter) checked by a test; `_get_yunet` logs once and sets `complete=False`; README lists the fetch. | With the model absent a test sees the warning and a `_partial.jpg`; notices match `pip-licenses` output. |
| QF-91 | Docs contradict code: README "1,203 tests" (3,431), "Reels at 12:00 and 19:00" (one Reel a day, alternating), "2.5D parallax" (flat Ken Burns), Footage row ("only clips the operator supplies"), module table names 35 of 124 modules; `docs/implementation-status.md:32` "repairs nothing" vs `main.py:2958`; `docs/sources.md` Pexels/Pixabay/Veo; `tests/test_footage.py:60-63` asserts a Pexels credit; `src/privacy.py:1-13` names the wrong primary model; AGENTS.md §3.1 requires a `PROJECT.md` that does not exist. | Fix the 15 items listed by the docs review; generate the test count in CI; add `PROJECT.md` (stack, build, test targets, architecture index). | Docs test for each corrected line; `README` count equals `pytest --collect-only`; `PROJECT.md` exists. |

Not added as rows (operator decisions, see section 6): whether to add an on-camera/desk element; keep face
blurring on archival photos; Stories pause; FB gallery cut; FOLLOW overlay; the self-update button (recommended
removed in production, QF-48); topic sensitivity policy (QF-73).

### 9.3 Method and limits of the sweep

Reviewer measurements on audio files (QF-85) and the LUT (QF-86) were made on this checkout's assets with the local
ffmpeg and NumPy; the coordinator re-ran the font parse (QF-87) and verified the stop-ship rows in source but did not
re-measure the SFX. The injection reproduction script is in the cloud scratchpad only; QF-41's acceptance test
recreates it in the repository. Platform-policy and law statements remain [UNVERIFIED] where marked.

## 10. Multi-niche roadmap (2026-11-05)

Operator direction: the pipeline will serve several content niches. Structure: **one engine repository, one small
repository per brand, one running instance per brand** (own DB, output, assets, Telegram bot or forum topic, platform
accounts, Google project and Gemini key). Forking the engine per niche is rejected: ~80% of modules are shared, every
fix would need porting N times, and the identifier cleanup (QF-49) would have to be repeated per fork. A fork is
right only for licensing the engine to a third party.

| ID | Finding | Fix | Acceptance |
|---|---|---|---|
| QF-92 | The engine and the first brand are one repository; brand strings in 11 modules, niche strings in 20+ (QF-35); the deployer installs one repository. | With QF-35: name the product; the engine repository carries no brand strings, topic banks, music or SFX sets; `brand-<slug>` repositories hold `brand.json` (palette, masthead, voice id, slots, hashtag families, account ids), `prompts/`, `data/topics/`, music and SFX sets with rights records, sensitivity policy and `.env.example`, pinned to an engine tag; the deployer installs engine tag X + brand tag Y as one release; one instance per brand via `RELIC_APP_ROOT`. Rename the GitHub repository in the same pass and update every hard-coded reference (deployer, `allowed_signers`, CI, README, `src/archive_film.py:39` User-Agent, provenance). | A second brand repository with a different name, palette and topic bank renders and posts end-to-end in dry run against the same engine tag with no engine change; `git grep -i "relic"` in the engine returns nothing outside tests' fixture names. |

### Planned brands, in order

1. **Consumer cyber-scam storytelling** (CyberAware Consumers / Go 'n' Verify). Evidence plugins: Canadian Anti-Fraud
   Centre, FTC consumer alerts and data, FBI IC3, CISA advisories, CanLII; tier remap (these are tier 1; Wikipedia is
   thin). Beats: hook → the victim's moment → how the scam works → the red flag → what to do (must end on the
   countermeasure). Graphics: scam-flow diagram, SMS/phone mockup card, red-flag callout, stat cards; maps and
   timelines reuse. Imagery: original graphics first (no public-domain photos of scams exist); illustrations only for
   abstract scenes, never a victim; QF-60 to QF-65 are prerequisites. Voice: the operator's own voice is the strongest
   originality signal. Rules: name only people charged or convicted per a court record; victims never identifiable or
   blamed; an "educational" framing line in captions. Policy watch: scam/fraud content rules, naming individuals.
   Effort after QF-35: 3-4 weeks.
2. **Space and astronomy**: the acceptance test for the split. NASA/ESA/JPL plugins, orbit/scale diagrams, pronunciation
   map for missions and objects. Effort: ~1 week.
3. **Finance history and mechanics**: central-bank and regulator plugins, a chart graphic type, a deterministic
   "no advice" check (not just a prompt rule), living-persons/ongoing-litigation sensitivity, financial-advice policy
   area. Effort: ~2 weeks. Start only after brand 1 has ~40 published Reels and the advice rule has tests.

Constraints for every brand: separate Google project and Gemini key (the free quota is per project), separate
platform accounts and Upload-Post profile, and the operator's daily review time (3-5 minutes per brand).
