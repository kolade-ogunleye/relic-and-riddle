# Lane B cycle 3: the YouTube headline chain, before and after

Lander rulings (2026-11-19, under the operator's delegation, and the lander's review of the first image set):
- The YouTube thumbnail's headline is a ranked chain of the run's signed lines, in this order: youtube_headline,
  thumbnail line1 + line2, the carousel cover heading, the tagline, the Story hook line, and the eyebrow (last).
- Each candidate must pass every gate below. If it fails one, the next candidate is tried. The thumbnail is refused
  only when the whole chain fails.
  - Truth: every figure is held to the run's evidence. The evidence is the narration, the stat cards and the verified
    graphics data and fact sheet. It is never the candidate's own field or other derived copy, such as the title.
    The drawn headline is checked again after the render.
  - It reads as a headline.
  - It does not repeat the video title.
  - It is specific: it carries a figure or a word of this run's subject, and it is not made only of the brand's
    generic vocabulary (data/headline_generic_vocabulary.json).
  - It sets in the render, and it actually draws a headline.
- A non-thumbnail candidate is the headline alone, so the render's own fallback to the eyebrow or tagline cannot
  slip a line past the gates.
- qa.json and the review card record what was DRAWN, and from which source.
- The copy stage holds the design's standard 904 px text column.

`youtube_chain_before_after.jpg` and the `pair*.jpg` files show seven real dev runs, each thumbnail at 1280x720.
BEFORE is one copy (the old path), rendered on the same tree. AFTER is the chain. The seventh pair is Roman
concrete's Reel copy.

Measured on dev (31 runs) and the production sample (34 runs; counts only, no images), each on the run's own hero:

| path | dev refused before / after | production refused before / after |
|---|---|---|
| Reel copy as the YouTube headline | 15 / 2 | 18 / 2 |
| documentary title (production's path) | 9 / 3 | 6 / 2 |

Source mix after:

| path | dev | production |
|---|---|---|
| Reel copy | thumbnail 12, carousel 9, story 4, tagline 2, eyebrow 2 | carousel 12, thumbnail 9, story 6, tagline 5 |
| documentary | thumbnail 11, carousel 8, story 5, eyebrow 3, tagline 1 | thumbnail 17, carousel 7, tagline 5, story 2, eyebrow 1 |

Every remaining refusal is recorded with each source's reason:
- Roman concrete 1793161347 and 1793163918, both paths: every specific candidate fails a heading rule at 904 px.
  The tagline "THE SECRET OF ROME" is too long for a single 3-word line and draws nothing. The eyebrow "ANCIENT
  MARVELS" is generic.
- Voynich 1792965308, documentary: the title draws "1404", one end of the signed span 1404–1438.
- Derinkuyu 1793286295, both paths: the copy says "280 FT" without the narration's "approximately".
- Voynich 1793365215, both paths: no specific candidate sets, and the documentary title's "600 YEAR" is not evidence.

Scripts (in the lane builder's scratchpad): chain_measure.py and make_pairs.py.
