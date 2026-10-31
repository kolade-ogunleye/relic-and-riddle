# Licensed sound effects, 2026-10-25 (quality step 4, rc41)

## Measured first

The three most recent episodes with a sound plan (production: Kailasa and Roman concrete; development:
Richat) each planned five or six moments and chose **no effect at all**: the five effects on offer (hammer,
stone door, gear ratchet, water surge, chisel) describe one kind of story, and the planner was told to add an
effect only where the narration describes a sound. The one effect in every episode, the sub-bass hit on the
opening and on the first revelation, was the **synthesized** braam, because the two earlier attempts to record
one came back clipped. The transition whoosh was the only licensed recording in use, and the cavern draft
(the one continuous setting layer) sat 25.6 dB under the music bed, which is silence.

Re-planning six local narrations with the widened palette but the old instruction still chose no effect in
any of them, although their words name a forge, swords, ships, fire and a chisel. The instruction, not the
palette, was the bottleneck.

## What changed

- `src/sfx_palette.py`: one table for the plan (what the model may choose), the bed (word cues, gains, the
  synthesized stand-in each recording replaces, setting layers) and the generator (prompts, lengths). 16 new
  recordings, generated on the paid ElevenLabs plan with rights evidence bound to their bytes: the impact
  braam (0.00% clipped this time), desert wind, sea waves, mountain wind, fire, thunder, footsteps on stone,
  anvil, parchment, crowd, horses, bell, water drip, rockfall, sailing ship, sword clash. 23 recordings in
  all, 15 MB, PCM 16-bit 48 kHz; the subscription counter moved by 66 credits.
- The planner's instruction: an effect where the narration names the thing or action it depicts, at its first
  mention; none for a figure of speech, a number, or a sound the story never brings on screen. The code limits
  stay: at most 4 effects, 2.5 s apart, none in the first half second.
- Setting layers: the desert, the sea and the mountains join the cavern draft, each only from its licensed
  recording, chosen by the setting the narration names most (passing words such as "sand" are not setting
  words), and mixed 14 dB under the music bed instead of 25 dB.

## Measured after (the same six narrations)

| Narration | Old plan | New plan | Setting layer |
|---|---|---|---|
| Richat Structure | 5 moments, no effect | desert wind at 1.9 s | desert wind ("desert") |
| Kailasa temple | 6 moments, no effect | mountain wind at 6.8 s | mountain wind (summit, mountain x2, cliff) |
| Amber Room heist | 5 moments, no effect | 6 moments, no effect (a heist names no sound) | none |
| Damascus steel | no plan recorded | anvil at 2.8 s, sword clash at 37.2 s | none |
| Greek fire | no plan recorded | sailing ship at 3.8 s | none ("sand" is not a setting word) |

Recordings, measured: RMS -8.6 to -48.9 dBFS, peaks at or below 0 dBFS with no sample clipping; each is
scaled at mix time to the loudness of the synthesized effect it replaces, so the balance of the bed is
unchanged. Next: the first production episodes with the new pack, listened to from their review cards, and
the levels adjusted from what is heard, not from the numbers alone.

Sources: ElevenLabs Sound Effects, Creator plan (commercial licence), prompts recorded in each
`data/sfx/<name>.wav.rights.json`.
