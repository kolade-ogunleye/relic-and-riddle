# Cards: fix round 1 (first frame, chapter card, myth-vs-fact card)

- `before/` is `render_evidence.py --code-root <HEAD 0036b18 worktree>`.
- `after/` is `render_evidence.py --code-root <working tree>`.
- `compare/` holds the 256 labelled half-size side-by-sides, BEFORE | AFTER, each under 2 MB.
- Both runs rendered 103 cards: 34 refused (the same 34 on both sides) and 0 errors.
- Inputs are real run media and fact-sheet facts, through `../fixtures`. Every card field is
  "new: real fact, test-signed field", signed with the throwaway key.

## What changed this round

| Item | Change | Measured |
|---|---|---|
| Hook plate (operator: off-centre) | On the plate, the claim, eyebrow and masthead are centred on x 540 again, refit to `HOOK_MEASURE_CENTRED` 696 so the lines stay inside 192..888. The wash keeps flush left at x 100. The feathered edges and moved-up position (H6 (4)) are kept. | Tunguska bright plate: HEAD set P 120 at 182..899, which runs past the Reel box edge at 888. AFTER sets P 112 at 206..874, centred to within 1 px. `test_the_bright_hook_panel_is_evenly_padded_centred_and_has_no_foot_rule` was off by 48 px and now passes. |
| H6 (5) dissolve | `HOOK_HOLD` goes from 1.09 to 1.07 (`HOOK_FADE` stays 0.16). | The frame on screen at 1.22 s is frame 36 (1.200 s). Its type alpha goes from 0.21 to 0.092, measured on its pixels. Frames 0..32 stay whole, and the type is gone by frame 37. |
| C5 16:9 two-line title | The two-line ladder holds only the sizes whose first cap top can stand within 12 px of the numeral's ink top (176..152 for a 5-act video). A title that would need a smaller two-line size is refused (`no_fit`) instead of being drawn under the numeral's top. | Act 2 at 152: the cap-top gap on drawn ink is -1 px at 1920x1080, 0 at 1280x720 and -2 at 2560x1440. No real title changes size. |
| M12 (3) Telegram note | `telegram_bot.design_card_notes` reads each drawn card's `.design.json` `quality_notes`. The review card lists them under "Design cards". | Tested on the real Richat short run's `verified_script.json`. |

## Regression check against HEAD

38 of 103 renders are identical in size, lines and contrast. The myth cards' lower minimums (8.61 to 7.03) come
from the new SOURCE label, which is planned at its 7.0 target (M12, earlier round). I checked this per element on
richat_long, plain ground, both formats: every element present in both trees draws the same contrast, and the
label is the new minimum. The 16:9 Richat myth sizes (long 104/90 to 92/80; short
128/96 to 120/84) are the earlier rounds' W2 and round-2 trades. At HEAD those cards broke as 'STRUCTURE IS AN',
'DISPROVED WHEN' and 'THEORY WAS'. The 9:16 act 2 chapter goes from 2 lines at 88 to 3 lines at 128 (C6).

## M10 (16:9 richat_long myth >= 84): declined, with the reason

The 1969 field cannot reach a 96 px fact or an 84 px myth on 16:9 without breaking a rule that is already decided:

- From 104 down to 96, the only fact wraps are 5 lines, and they end a line on WHEN ('DISPROVED WHEN'), which is
  rejected as a weak end in the round 2 review. The alternative leaves DISPROVED as a lone word.
- At 92, the largest clean 4-line size, the myth at the ladder's top share (0.88) is 80. A myth of 84 would need
  0.913 of the fact, above `MYTH_WIDE_RATIOS`.
- The no-date field reaches 96 / 84.
