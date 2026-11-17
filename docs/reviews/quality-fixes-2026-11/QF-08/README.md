# QF-08: voice formats, seeded requests and spoken-only captions

Azure requests 48 kHz PCM; ElevenLabs requests it where supported, with seed 1
and stability 0.65. New takes are WAV, and rights records hash the WAV bytes.
Existing MP3 takes remain reusable through `voiceover_file`; this change does not
buy replacements for existing recordings. Punctuation-only timed tokens are
removed before captions or sidecar cues are built.

The previous candidate defaulted Creator to 24 kHz PCM. Independent review rejected
that because it removed the former voice's 12-16 kHz content. The corrected default
preserves bandwidth with 192 kbps MP3, decoded once to WAV. The measurement reports
that provider encoding even though the resulting file is PCM.

| Path | Before | Corrected default |
|---|---|---|
| ElevenLabs Pro or higher | MP3 44.1 kHz / 128 kbps | PCM 48 kHz |
| ElevenLabs Creator | MP3 44.1 kHz / 128 kbps | MP3 44.1 kHz / 192 kbps, decoded to WAV |
| ElevenLabs Free/Starter | MP3 44.1 kHz / 128 kbps | same provider format, decoded to WAV |
| Azure | MP3 48 kHz / 192 kbps | RIFF PCM 48 kHz |
| Pro + Azure provider MP3 generations | 2 | 0 |
| Pro + Azure resampling steps to master rate | 1 | 0 |
| Creator provider MP3 generations per take | 1 | 1 |
| ElevenLabs seed / stability | none / 0.50 | 1 / 0.65 |
| New file / rights hash | MP3 / MP3 bytes | WAV / WAV bytes |
| Punctuation-only tokens: words / Reel / wide / sidecar | 1 / 1 / 1 / 1 | 0 / 0 / 0 / 0 |

## Plan handling and settings

`ELEVENLABS_OUTPUT_FORMAT` defaults to `pcm_48000`.
`ELEVENLABS_FALLBACK_FORMAT` defaults to `mp3_44100_192`, and
`ELEVENLABS_FALLBACK_FORMAT_TIERS` defaults to `creator`: a known Creator plan
requests that format directly. Free/Starter use
`ELEVENLABS_BASIC_FALLBACK_FORMAT`, default `mp3_44100_128`. Unknown or other plans
try preferred, Creator fallback, then basic fallback. Format rejections retain the
existing model retry for HTTP 400/422. Licensing and spending gates are unchanged.

An explicit `pcm_24000` fallback remains possible, with a 12 kHz bandwidth ceiling.
It is not the default. Invalid format strings fall back to the defaults above.
Every setting is declared in `src/config.py` and documented in `.env.example`.
No environment file was changed. Requests in this row's validation are mocked.

The model itself is unchanged. Seeded generation is a provider request, not a
promise of identical live speech. No live voice or provider acceptance is claimed.
The existing model fallback remains available if a provider rejects its settings.

## Reproduction and bandwidth evidence

Run `python docs/reviews/quality-fixes-2026-11/QF-08/measure.py <repo root>`.
All providers are mocked and every audio input is generated; no keys, network or
ignored recording is required. The mock returns actual PCM, RIFF or ffmpeg-encoded
MP3 bytes in the requested format. It models rejected formats for each plan.

The generated signal includes a 14 kHz component when the provider rate supports
it. After Creator MP3 decoding, 5.94% of its measured energy remains in 13-15 kHz;
24 kHz PCM cannot carry that band. This tests preservation of high-frequency
content, not subjective voice quality. Unit tests separately verify a 14 kHz tone
survives Creator, Free/Starter and unknown-plan fallbacks.

`before.json` and `after.json`/`measurements.json` contain the current measurements.
Acceptance is false on the immediate predecessor and true after the fix.
`round1.json` is historical evidence from an earlier rejected candidate.
The Free-plan format probe explicitly permits unlicensed test speech; it does not
relax the application's licensed-only production gate.

## Validation

The QF-08 regression, audio engine/provenance and configuration-drift checks passed
32 tests (54.69 s). Creator/fallback expectations changed only in this row's new
regression file, which is absent from `origin/main`, to implement the brief's
explicit instruction to fix the rejected bandwidth-losing default. No existing
expected values or tolerance thresholds were changed. Broader voice, resume,
backup and identifier coverage, parent regression, lint and independent review
are recorded with the completed build; full-suite validation remains mandatory.
Final evidence: all 15 regression tests fail on the immediate predecessor
(42.55 s). The broader voice/resume/backup/identifier suite passed 79 tests
(55.37 s). Ruff has no new findings in any changed file. A final logging-only
correction names the actual rejected format when falling through two fallbacks.
