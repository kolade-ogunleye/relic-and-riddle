# 16:9 documentary: work in progress

**Status (2026-10-23):** paused in production and being rebuilt in development. Reels, and their
YouTube Shorts, are what the channel publishes in the meantime.

## How the pause works

The formats that run are set in `.env`, not in code:

| Setting | Values | Production | Development |
|---|---|---|---|
| `VIDEO_FORMATS` | `reel`, `documentary` or `reel,documentary` (default `reel`) | `reel` (leave unset or set it to `reel`) | `reel,documentary` |
| `YOUTUBE_SHORTS` | `true` / `false` (default `true` since QF-83) | on (an explicit `false` turns it off) | `true` |
| `YOUTUBE_SHORTS_PRIVACY` | `public`, `unlisted`, `private` (default `YOUTUBE_PRIVACY`) | `unlisted` to start | `unlisted` |

- A format not listed in `VIDEO_FORMATS` is declined on every route before anything is spent: the
  Telegram queue (its "Reel + 16:9 documentary" button is hidden, and an older copy of the button
  starts nothing), `main.py run --format deep-dive|dual`, and any direct call to `produce_deep_dive`.
- The daemon's daily run asks for every format and is narrowed to `VIDEO_FORMATS`. With `reel` it makes
  a Reel, exactly as before. With both, it makes a Reel and then its documentary.
- A Short is a separate upload, made after the Reel's Facebook and Instagram post (or simulated in a dry
  run). It is recorded as channel `youtube-shorts`, and its outcome never changes the Reel's.

Re-enable the documentary in production only after the acceptance criteria below are met and the
operator approves it. That means adding `documentary` to production's `VIDEO_FORMATS` through the
normal promotion procedure (`docs/deployment.md`).

## Goal

An 8–15 minute documentary that:
- hooks in its first 30 seconds;
- keeps viewers watching (chapters, each act with a turn or question, no static stretches);
- starts conversation (a sourced open question, pinned first comment);
- qualifies for the YouTube Partner Program. Each video must add clear original value, so it is not
  "inauthentic" (mass-produced or templated) content.

## Where it stands (Richat test run, 2026-10-23)

What works:
- Every act is fact-checked and rewritten up to 3 times.
- A progress card and a failure card.
- Mastering at −14 LUFS.
- QA scored it 100/100.
- Captions in SRT and VTT.
- A YouTube upload with the synthetic-media disclosure.

What falls short:
1. **Length:** 3 min 16 s. Mid-roll ads need 8 minutes or more; the target is 8–15.
2. **Pictures:** 21 shots of about 9 s each from 20 Commons stills with slow zooms. It reads as a
   narrated slideshow.
3. **Scene matching:** the step that matches pictures to the narration can fail. On free-quota
   exhaustion, or a billed vision timeout, the photos fall back to file order and no longer follow the
   narration. It should degrade to text-only matching (photo captions against the narration), not to
   filename order.
4. **Original graphics:** one (a locator map). A flagship needs one or more per act: timeline, scale
   comparison, cross-section or diagram, labelled photo callouts. These are the clearest originality
   signal for the Partner Program.
5. **Motion:** add 2.5D parallax on suitable photos, licensed archive footage where it exists, and
   act-transition sequences.
6. **Voice:** a licensed, more expressive voice with pacing changes by act.
7. **Structure:** a cold open, a chapter card per act, a mid-video re-hook, and an end screen with a
   subscribe prompt and a linked Reel or Short.
8. **Thumbnail:** one 16:9 image. YouTube's "Test & Compare" (up to 3 thumbnails) works only in YouTube
   Studio, so 3 strong 16:9 options are made for manual testing.
9. **Topic-specific code left over:** Derinkuyu-specific fallback act titles in
   `generate_deep_dive._build_fallback`.

Rejected for now: an AI-generated on-camera presenter (avatar). It costs money per minute, needs the
synthetic-content disclosure, and is a pattern YouTube's reviewers associate with mass-produced
channels. It also doesn't fix gaps 2–4, which are the real reasons the video feels static.

## Acceptance criteria (for re-enabling in production)

- 8–15 minutes. Every factual sentence is still traced to the fact sheet, as now.
- At least one original graphic per act. No still shown longer than 12 seconds without motion.
- A scene plan that follows the narration even when the vision model is unavailable.
- An operator review of three consecutive dev documentaries on different topics, all approved without
  edits.
- A simulated YouTube publish, then one unlisted real upload, approved explicitly.
