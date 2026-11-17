# QF-127: the voice licence gate

Source: the operator's statement of 2026-11-14 (production runs only the licensed ElevenLabs voice; edge-tts and
Azure are for development) and risk R1 of the catalogue audit of the same day: Azure's free F0 tier carries no
commercial output rights, yet the engine counted any configured Azure key as a licensed fallback for real runs.
Base: 8d90acd5 (rounds 1 and 2 were built on 38f6f5d3; the measurement is the same on both). Round 2 answers
review 1 (four blockers and its notes), round 3 answers review 2 (two blockers, its "Not done" list and notes);
what each changed is marked "round 2" / "round 3".

## What changed

1. **One licence table** (src/voice_licence.py; sources and dates in docs/sources.md, "Voice licences"). The status
   is a record per provider and tier, never "a key is configured", and a name the table has never seen is not
   licensed.
   - ElevenLabs (round 2: an allow-list): licensed only on the paid plans ElevenLabs names on its pricing page
     (Starter, Creator, Pro, Scale, Business, Enterprise), and only while the subscription's `status` is `active`.
     The Free plan, an unknown plan, any other plan name (the reason then reads "ElevenLabs plan <name> not
     recognised: no commercial licence recorded") and a subscription that is trialing, incomplete, past due or
     free are not. Names are compared trimmed and in lower case. A status is read only where one was given: the
     engine always passes the one the subscription call returned (an answer without one counts as "not
     reported", which is not paid); a record saved before the status was written is judged on its plan name.
     Round 3: the table lists the API's tier ids, not marketing names. The pricing page's own "Choose" links name
     Scale as `growing_business` and Business as `scale_2024_08_10`; both are licensed (docs/sources.md has the
     links as read on 2026-11-14). An id the table does not hold is still not licensed.
     Round 3: a plan that could not be read (the subscription call failed, or its answer named no plan) is not
     licensed and is said as that: "ElevenLabs' plan could not be read (the subscription call failed or named no
     plan): no commercial licence confirmed". "Free plan" is said only when ElevenLabs answered `free`.
   - Azure Speech: licensed only on the paid tier S0, and only when the operator declares it with
     `AZURE_SPEECH_TIER` (src/config.py, .env.example; default F0). S0 covers prebuilt neural voices only (round 2:
     said in the table's text and in the basis saved with each take).
   - edge-tts and anything else: never.
2. **The engine** (src/audio_engine.py). A real run (licensed_only) skips Azure unless that tier is declared, with
   its key and region set, skips ElevenLabs unless the table licenses its plan and status, and raises
   NoLicensedVoice when no licensed voice is left. A dry run is voiced exactly as before (Azure, then edge-tts).
   Each take records its provider, tier, plan status, licence status and the basis of it. The ElevenLabs path, its
   pacing and the spending settings are untouched; no take is stretched.
3. **The hold** (main.py, `_hold_for_voice`). NoLicensedVoice in run_cycle, resume_run and the documentary path
   ends the run as held: its status is failed with the error `NoLicensedVoice: ...` (so Re-render continues from the
   kept script), nothing is approved or posted, and one Needs-you item says so under the run's own key
   `fail:<run>` with the heading "Held: no licensed voice" and the action Re-render. Round 2: the text reads
   "Nothing was voiced by an unlicensed voice" (a paid ElevenLabs take may exist when a second take failed).
4. **The records** (main.py, src/audio_provenance.py). voice.json gains `tier`, `plan_status` (ElevenLabs, round 2)
   and `licence_basis`; the rights record beside the voiceover gains the same and takes its status from the
   table, not from the caller's word. Files already on disk are never rewritten.
5. **One publish gate for every upload of a master's audio** (round 2; src/publisher.py,
   `MetaPublisher.voice_gate`). The run's voice.json is read against the table by one function, called by
   `publish_reel` (Facebook, Instagram, the documentary's YouTube video, a retried Reel) and by `publish_short`
   (the YouTube Short, with its Reel or by its own button). These are the only two places that call an uploader
   with a video. A master that is not licensed is not uploaded: the Reel ends `blocked_unlicensed`, the Short
   answers "not posted: voice not commercially licensed (...)", each with the audit event
   `publish_blocked_unlicensed_voice`. A missing or unreadable record is not licensed.
6. **Masters already made** (src/telegram_bot.py, src/review_panel.py, src/probe.py, main.py). A record from before
   the tier was saved names none: an ElevenLabs take marked licensed stands (the engine checked the plan before
   making it); an Azure take marked licensed does not. Such a master is blocked at the gate, fails the release
   gate's voice-rights check when it is re-checked, is not reused by a real run, and its review card, V2 card and
   review panel say "voice not commercially licensed (...)" in place of the saved "Commercially Licensed" note.
   Round 3: under that flag the review panel's facts line reads "rights ⚠️", never "rights ✅" (a release report
   saved before the row still passes the voice's rights). Round 3 (review 2, blocking 3; CI run 43681536645 was
   red on round 2's commit): the flag as the panel's first note was some 250 characters (360 with the gate off)
   and pushed the "Sensitive topic" chip QF-73 requires into "…and N more". The panel's note is now the flag's
   short form, "⚠️ Voice: voice not commercially licensed (<basis>). On a real run, Regenerate › Music / sound
   re-voices it." (the lede above it says what Approve does; the live card, the V2 card and /ask keep the whole
   sentence), and on a flagged master the note holding the sensitivity chip comes second, before any note that
   may be cut. Neither is shortened or dropped; the QF-73 test is untouched and passes.
   Round 2: the flag and the blocked notice name the way out by the buttons' own names ("Regenerate › Music /
   sound": the script is kept and a real run voices it again; from the blocked notice, "Resend review card"
   first); the flag says "Approve will not post it" only while the gate is on; and `/ask` (src/probe.py) gives
   the record as it reads now, never the saved note.
7. **Pre-flight and status** (src/ops.py, src/control.py, scripts/runtime_probe.py). For a real run the live
   credentials check and the live probe do not count a working Azure key on F0 as the licensed voice. Round 2: the
   requirement is a real run's only. `run_cycle` tells the pre-flight whether the run is a dry run, and a dry run
   started with `--require-live` (as the bot and the daemon start every worker) accepts any configured voice, as
   before the row. Round 3: the status screen's ElevenLabs line promises no Reels on a plan the table does not
   license. "(~N Reels)" is dropped, the line reads "Not used for real runs: no commercial licence on this plan",
   and one Licence alert gives the basis and what happens to a real run (held; voiced by Azure Speech instead
   when a paid tier is declared and working; with the gate off, voiced on this plan).
8. **VOICE_REQUIRE_LICENSED=false is never silent** (round 2). The switch still does what it did: a real run is
   voiced as a dry run is, and its master is posted. On a service whose runs are real it is now said where the
   operator looks: a "Licence alert" line in the status screen's voice section, a "voice gate" line among the
   pre-flight checks (shown, never a reason not to start), and one Needs-you item at the start of the bot or the
   daemon ("Voice licence gate is off", key `voice-gate-off`, replaced at each start, resolved once the switch is
   back on, action Status). The publish gate still reads the record against the table: an unlicensed upload goes
   out as before. Round 3: what is written says only what is true when it is written. Before the upload the gate
   records the audit event `publish_unlicensed_voice_gate_off` (status allowed), "<Reel|YouTube Short> upload
   allowed with a voice not commercially licensed (...): VOICE_REQUIRE_LICENSED=false: the voice licence gate is
   off". Only once the upload has returned published does `MetaPublisher.voice_posted` record
   `published_unlicensed_voice_gate_off` (status published), "<Reel|YouTube Short> posted with ...", and keep
   that sentence for the publish receipt, the Published notice and the review card's stamp. An upload that
   failed, was skipped or whose outcome is unknown has the first event only, and no line anywhere says "posted".
   A Reel that one account posted before another ended unknown is recorded as posted with the channel that
   posted it; an unknown outcome the operator confirms with "It posted" is recorded then (confirmed_by operator)
   and said in that answer. A dry-run service or a dry run shows none of this.

## Measurement

`python docs/reviews/quality-fixes-2026-11/QF-127/measure.py <repo root>` (no network; every provider and uploader
is a stand-in; nothing is written under the repository). Two parts, each judged in the script, independently of
the code measured.

The engine at the root is asked for a narration in 64 combinations of VOICE_PREFER, the ElevenLabs account (round
2 adds a plan name that is not one of ElevenLabs' paid plans, and a paid plan name while trialing) and the Azure
key and declared tier, as a real run asks and as a dry run asks. The publisher at the root is asked for a real
upload of 12 voice records (3 licensed, 9 not), each as a Reel and as a YouTube Short, with the gate on and off.
Round 3 adds two counts. For the engine: real runs held with "Free plan" as the reason although the account is not
on the Free plan (its plan could not be read). For the publisher, with the gate off: the 9 unlicensed records again,
each with six uploads that do not publish (the Reel refused, its outcome unknown, no account set up; the Short
refused, unknown, skipped: 54), counting those for which an audit event or a kept note says "posted"; and "on the
record" now counts a published upload only when the audit log and the kept note say "posted with" for that run.

The base moved from 38f6f5d3 to 8d90acd5 in round 3; before.json was taken again there with this measure.py and
every count it shared with the earlier file is the same. before_round1.json (8306e2c0) is kept as round 1 measured
it, with the measure.py of that time (it has no round 3 counts): 14 of 64 real runs voiced unlicensed, 11
unlicensed masters uploaded with the gate on.

| | before (8d90acd5, before.json) | round 2 (32842d85, before_round2.json) | after (after.json) |
|---|---|---|---|
| real runs voiced by an unlicensed provider or tier (of 64) | 36 | 0 | 0 |
| ... of those, recorded as licensed | 36 | 0 | 0 |
| real runs voiced by Azure | 39 | 15 | 15 (the cases with S0 declared) |
| real runs voiced by ElevenLabs | 15 | 7 | 7 |
| real runs held (no licensed voice) | 10 | 42 | 42 |
| real runs held with "Free plan" as the reason for an account that is not on it (round 3) | 2 | 6 | 0 |
| dry runs voiced by Azure / edge-tts | 48 / 16 | 48 / 16 | 48 / 16 (unchanged) |
| dry-run takes recorded as licensed without being so | 32 | 0 | 0 |
| gate on: unlicensed masters uploaded (18 asked: 9 Reels, 9 Shorts) | 16 | 0 | 0 |
| gate on: licensed masters uploaded (6 asked) | 6 | 6 | 6 |
| gate off: unlicensed masters uploaded (18 asked) | 18 | 18 | 18 (the switch's meaning, unchanged) |
| gate off: ... of those, on the record as posted (an audit event and a kept receipt note that say "posted with", each for its own run) | 0 | 18 | 18 |
| gate off: uploads that did NOT publish (54 asked) for which the audit log or a kept note says "posted" (round 3) | 0 | 54 | 0 |

Real data, counts only, read on 2026-11-14 with the table of this commit; no file was changed.

| saved voice records | runs | marked licensed | licensed as read now | records naming a tier |
|---|---|---|---|---|
| development (dev_runs.json), azure | 30 | 30 | 0 | 0 |
| development, elevenlabs | 4 | 4 | 4 | 0 |
| production sample (prod_sample.json), azure | 1 | 1 | 0 | 0 |
| production sample, elevenlabs | 33 | 33 | 33 | 0 |

The development checkout has 47 run folders, of which 34 have a voice record; the production sample has 34 runs,
all with one. No saved record names a tier, so the allow-list of round 2 changes the reading of none of them: the
37 ElevenLabs masters stand by the rule for records made before the tier was saved, and the 31 Azure masters are
flagged and blocked, as a Reel and as a Short.

## Tests

tests/test_qf127_voice_licence_gate.py: `196` cases. On the base 8d90acd5 `177` fail (102 by an assertion; 72 because the
module, an attribute or a result key of the row does not exist there; 3 by the base's own behaviour: run_cycle
raises NoLicensedVoice where the row holds the run (2), and the real voice step goes on to probe a take the row never
makes (1)) and `19` pass; on round 2's
commit 32842d85 `50` fail, each by an assertion, and `146` pass. The origin/main expectations changed under
decisions QF-127 and QF-127b (answer A in each) are listed in the commit message; rounds 2 and 3 change no other.

## Known consequences

- A development dry run voiced by Azure F0 shows the voice as unlicensed on its review card and as a voice-rights
  finding of the release gate, as a dry run voiced by edge-tts always did. It is still started, made, reviewed and
  simulated as before: the pre-flight asks a dry run for no licensed voice.
- A real run on a machine whose only voice is an Azure F0 key is not started (pre-flight), or held instead of
  voiced.
- A real run on an ElevenLabs plan the table does not name, or on a subscription that is not `active` (a trial, a
  failed payment), is held with that reason. This is the cautious reading of the provider's pages; a plan is added
  to the table only with its source in docs/sources.md.
- A held run's topic is set aside like any failed run's (Re-render, or Back to the queue, from the notice).
- Nobody read a live ElevenLabs account (no provider call is made by a build or a review). Before a release is
  signed, `health --live` on development must show "ElevenLabs creator plan OK" (decision of review 2). The ids
  `growing_business` and `scale_2024_08_10` come from the pricing page's links, not from an account on those plans.
- With the gate off, an upload of unknown outcome that the operator never answers stays in the audit log as
  "upload allowed" only: the log does not claim a post nobody confirmed.
- Not closed by this row: voice.json is not bound to the audio, so a hand-edited record posts; the rights record
  beside the voiceover is bound (hash and script fingerprint). The YouTube Short button is not release-gated
  (as before the row). Making the publish gate ignore VOICE_REQUIRE_LICENSED needs the operator's approval of
  origin/main publisher expectations and is not taken here.
