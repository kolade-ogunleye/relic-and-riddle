# Share and follow prompts (2026-10-25)

Meta's creator dashboard, on the first real Reel (Kailasa, 24 Oct), listed two steps that can only be
taken while the content is made: "ask your community to share your content" and "grow your following with
your content". Neither has an API: the words have to be in the post.

## What was built

One line per episode, the writer's when it passes its checks (`share_line`: at most 90 characters, no
hashtags, at most one emoji, it must ask for a share and a follow) or a plain fallback, placed by code:

- in the caption and the carousel caption, as its own paragraph before the hashtags (`script_engine.with_share_line`);
- on the last carousel slide, between the question and "TELL US IN THE COMMENTS" (`multi_format._render_debate`);
- over the last three seconds of the Reel, as one to three caption-style pills stacked above the caption band,
  inside the Reel safe area, fading in (`video_composer._end_prompt_events`). Never below 44 px; a line that
  would not fit two pills at 52 px is set on three at 44. Documentaries (16:9) get no prompt.

The line is part of the public copy before the fact review is recorded, so the review binds to it.

## Evidence (Nazca Lines development run, 25 Oct)

| File | What it shows |
|---|---|
| `end_frame_with_caption.jpg` | 0.9 s into the last scene: the narration's caption pill below, the prompt's three pills above it, all inside the safe area (half size) |
| `end_frame_after.jpg` | 2.6 s: the prompt alone (half size) |
| `carousel_last_before.jpg` | the closing slide as it was |
| `carousel_last_after.jpg` | the closing slide with the line |

Pill rectangles measured by `video_qa.caption_pills` on the frame's caption file: (282, 1004)-(799, 1082),
(288, 916)-(792, 994), (313, 828)-(767, 906); the Reel safe box is (65, 288)-(888, 1248) and the caption band
starts at y 1098. Tests: `tests/test_share_follow.py`.
