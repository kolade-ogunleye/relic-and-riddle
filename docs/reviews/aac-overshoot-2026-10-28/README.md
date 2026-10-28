# AAC true-peak overshoot (2026-10-28)

**Found by:** the end-to-end development run of Roman concrete with archive film (run_roman_self_healing_concrete_1793161347),
which failed "Encoded peak correction cannot satisfy loudness and peak limits". First occurrence on record.

**Cause:** the mastered programme met its target (-14.0 LUFS, -1.9 dBTP), but its single AAC 192 kb/s encode measured
+1.0 dBTP: energy above 16 kHz, which the encoder drops, had been limited and then removed, and the band-limited waveform
rang over the ceiling (a sample peak of +2.7 dBFS in one channel at 26 s). Turning it down by the 2.3 dB needed would have
left the master at -16.3 LUFS, outside tolerance, so `finalize_encoded_audio` rightly refused. The archive film played no
part: footage is muted, and the spike sits under a Pantheon still at 26 s (the film plays at 14 s).

**Fix:** `lowpass=f=16000:poles=2` before the limiter in `_master_chain`.

| Programme | Before the limiter | Programme (WAV) | After AAC 192k |
|---|---|---|---|
| Roman concrete | nothing (before) | -14.0 LUFS, -1.9 dBTP | -14.0 LUFS, **+1.0 dBTP** |
| Roman concrete | 18 kHz low-pass | -14.0, -1.9 | -14.0, -1.6 |
| Roman concrete | **16 kHz low-pass** | -14.0, -1.9 | **-14.0, -1.9** |
| Odessa catacombs | nothing / 16 kHz | -13.9, -1.9 / -13.9, -1.9 | -13.9, -1.9 / -13.9, -1.9 |
| Unfinished obelisk | nothing / 16 kHz | -13.9, -1.9 / -13.9, -1.9 | -13.9, -1.8 / -13.9, -1.9 |
| Trepanation | nothing / 16 kHz | -13.4, -1.9 / -13.5, -1.9 | -13.5, -1.7 / -13.5, -1.7 |

Loudness is unchanged on every programme; no programme encodes worse. The band above 16 kHz is the one AAC at 192 kb/s
does not keep.
