# End card (capsule v3), 2026-10-25

The share-and-follow ask was three caption-style pills over the last three seconds
(`before_pills_odessa.jpg`). The operator called it mediocre: it read as subtitles
and as a wall of stock text. After prototypes (designed card, spoken ask, six
closing devices, three rounds of the capsule), the operator chose **capsule v3**,
now built into the renderer (`src/end_card.py`, `VideoComposer.compose_video`).

## What a Reel now ends on

2.9 s after the narration, over the narration's last graded frame (no captions):

| Time | What happens |
|---|---|
| 0–0.45 s | the last shot keeps pushing in; the dim rises (measured per frame, below) |
| 0.15–0.75 s | RELIC & RIDDLE (Cinzel) fades in; the gold rule draws out from its diamond |
| 0.45–1.3 s | the story's share line rises word by word (balanced lines, safe width) |
| 1.15–1.5 s | FOLLOW FOR THE NEXT RELIC rises in gilt gold, fitted to the safe width |
| 1.35–1.95 s | the capsule outline traces from the bottom centre round both ends, a bright point leading each half |
| 1.95–2.5 s | the bell; a sheen crosses the capsule and lights the letters |
| 2.5–2.9 s | hold |

No handle (it differs between platforms). Sound: the music bed runs on under the
card (the bed is made `end_card.SECONDS` longer), with a whoosh and the bell from
`data/sfx` (their rights files go into the bed's provenance); the whole programme,
card included, is mastered together, and the card's own level is measured in the
mastered mix and set 6 LU under the narrated body (`end_card_level`).

## Measurements (the three sample runs, `measurements.json`)

| Run | Text band (≤ 30) | Band texture (≤ 12) | Above (≤ 40) | Below (≤ 40) | Softened |
|---|---|---|---|---|---|
| Voynich | 22.8 | 3.5 | 34.5 | 39.8 | 0.81 |
| Nazca | 18.2 | 4.5 | 39.7 | 27.8 | 0.85 |
| Memnon | 15.9 | 5.6 | 39.7 | 34.5 | 0.85 |

Luminance 0–255 after the dim. The renderer reproduces the approved prototype's
numbers exactly. Safe area: every element sits inside `REEL.box` (the capsule ends
at x = 885 against the 888 limit of the right-hand like/comment/share rail); the
follow line is the largest size whose capsule fits.

The outline is drawn as a stroke centred on its path (4x supersampled), so both
halves are identical: the prototype's wide lines were offset by drawing direction
and the halves did not meet evenly (operator, 2026-10-25; `capsule_zoom_2x.jpg`,
and `test_the_finished_capsule_outline_is_symmetric`).

## The share line

Written by the script writer for each story (`share_line`), share ask first and
follow after ("Send this to the codebreaker you know, and follow for the next
relic."). The card shows the share half; the captions carry the whole line.
Rejected: stock phrasing with nobody in particular ("share this with a friend",
"someone who loves history"), and an ask the narration's last sentence already
made. When the writer's line fails, the card shows the plain fallback and the
review card says so (info, never a block).

## Recorded for audience analysis

`payload["end_card"]` = variant, start, duration, text, fallback. The audience
collector marks carded runs and, where Facebook's retention graph exists, the
retention at the card's start and the drop across it, so shares and follows can be
compared with and without the card against the watch time it costs.

## Files

- `before_pills_odessa.jpg`: the earlier pills
- `after_voynich.jpg`, `after_nazca.jpg`, `after_memnon.jpg`: the card's last frame
- `voynich_timeline.jpg`: 0.6, 1.3, 1.7, 2.2 and 2.9 s
- `capsule_zoom_2x.jpg`: the outline at 2x
- `measurements.json`: dim, layout and safe width per run
