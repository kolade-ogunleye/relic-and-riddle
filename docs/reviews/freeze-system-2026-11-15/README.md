# Design-freeze cycle 0: the design SYSTEM (2026-11-15)

FREEZE-PLAYBOOK.md v2 section 1. Built on QF-35a's role tokens (colours, faces, strings): this step names every size,
ratio, alpha, gap, easing and duration the designs share, as tokens read at call time, with the rule that uses each.
The written system is the docstring of `src/brand_system.py`; the tables are below. Trade-off order (binding): gates
and truth > legibility at delivered feed size on the worst hero > subject visibility > one system across surfaces >
hierarchy > rhythm > ornament.

## Where the system lives

- `src/brand_system.py`: `GATES` (operator rulings and WCAG / platform limits: constants, a brand can never move them;
  `BrandConfig` refuses a system override naming one), `SYSTEM_TOKENS` (style values, overridable per brand through
  `BrandConfig.system`, read with `tok()` / `colour_of()` at call time), `TYPE` / `TYPE_FALLBACK` / `TYPE_FLOOR` / `FACE`
  per canvas class, `SPACING`, the one line-end vocabulary (`LINE_END_NEVER`, `LINE_END_LAST_RESORT`, `line_end_ok`),
  the named eases (`EASES`) and `masthead_spec`.
- `src/masthead.py`: the one masthead treatment, its ground solved in the ruled order (nothing; a top-edge INK
  gradient up to 0.60 at its peak; plus the name's halo up to 0.16; plus a levels ceiling under the same shape, never
  under 205; the light-ground INK variant only where bone still misses the gate), every step recorded.
- `src/brand.py`: `BrandConfig.system` (default `SYSTEM_TOKENS`), `derive(..., system=...)`.

## Gates (constants)

| gate | value | source |
|---|---|---|
| contrast.text | 4.5 | WCAG AA on delivered pixels (= brand.TEXT_CONTRAST) |
| contrast.place | 4.8 | placement aim (= brand.PLACE_TARGET) |
| contrast.display | 5.1 | display rows: covers, hook, YouTube |
| contrast.kicker | 5.5 | small gold capitals (960 px q70 re-encode leaves 4.83-5.01) |
| contrast.masthead_gate / _target | 5.5 / 6.5 | operator ruling round 3 (2) |
| contrast.high | 7.0 | ink figure / source line where ruled |
| contrast.mark | 3.0 | WCAG 1.4.11 (graphics.NONTEXT_MIN and callouts.NONTEXT_MIN now read it) |
| scrim.masthead_peak | 0.60 | ruling round 3 (2) |
| halo.masthead_max | 0.16 | ruling round 3 (2) |
| scrim.levels_floor | 205 | park step 5 (BRIGHT_CEILING) |
| scrim.slope_max | 0.5 luma/px | end-card ruling round 3 (1) |
| masthead.grid_cap | 9 px at 124 px wide | QF-87 |
| heading.story_one_line_floor / story_floor | 106 / 120 | rulings round 4 (2) / round 3 (3)(1) |
| motion.caption_cps | 17 | config.CAPTION_MAX_CPS |

## Tokens (brand-overridable)

| group | tokens |
|---|---|
| masthead | weight 700; track 0.0625 em; mark radius 0.30 em; gap 0.36 em; INK edge 0.047 em; colour dark-ground BONE, light-ground INK |
| kicker | text face 800; track 0.15 em; GOLD; lead rule 1.0 cap long, 0.55 cap gap, 0.16 cap thick |
| plates | opaque 1.0; held 0.875; panel 0.78; radius 14 px at 1080; shade INK; halo step 0.08 |
| credit / badge | data 500, 0.04 em, STONE_LT / badge role 600, 0.05 em, BONE on INK at the held tier |
| leading | display 1.13 cap; body 1.36 em; label 1.12 em |
| heading | balance >= 0.55; cover >= 1.08 x the deck's largest interior heading |
| figure | en dash centre within 0.03 em of the figures' centre; ink gap >= 0.06 em each side |
| rules | 4 px at 1080 (scaled); hairline 1 px |
| motion | reveal 0.40 s; word stagger 0.055 s; rise 14 / 10 / 8 px (heading / line / kicker); hook hold 1.07 s, fade 0.16 s; act dissolve 0.5 s; loop crossfade 0.5 s; back 1.5 (8 % overshoot); pop scale 1.4; push 0.0016 / frame, cap 1.24 |

## Type scale (canvas px; carousel = 1080 design px x 4/3 exactly)

| role (face) | tall 1080x1920 | carousel 1440x1800 | wide 1920x1080 | one fallback tier (tall / carousel / wide) |
|---|---|---|---|---|
| display_xl (display) | 150 | 200 | 190 | 120 / 184 / 150 |
| display_l (display) | 138 | 184 | 184 | 124 / 165.3 / 152 |
| display_m (display) | 112 | 149.3 | 136 | 96 / 128 / 112 |
| figure (display) | 300 | 400 | 360 | - |
| kicker (text 800) | 40 | 45.3 | 40 | - |
| body (text) | 46 | 48 | 52 | 40 / 45.3 / 46 |
| label (text) | 30 | 36 | 34 | floor 27 / 36 / 30 |
| data_label (data) | 30 | 36 | 34 | floor 27 / 36 / 30 |
| credit (data) | 24 | 32 | 24 | 22 / - / 22 (floors 22 / 29.3 / 22) |
| masthead (masthead) | 64 (grid-shown covers: absent) | 53.3 | 64 | end screen: brand moment, the largest that fits its group width |

Spacing (px): margin 84 / 112 / 96; kicker-to-heading 24 / 32 / 24; heading-to-body 36 / 48 / 40; body-to-credit
24 / 32 / 24; unit 4. Safe boxes are `src/safe_zones.py`'s, never re-declared.

## What this commit changes (default pixels)

Measured on real runs in `measurements.md` (158 surfaces, base vs head). Default pixels change on: carousel and
Facebook gallery slides (the masthead band: 56 of 56 slides now at >= 6.5:1, halo <= 0.16, scrim peak <= 0.60), the
YouTube thumbnail (masthead bone, halo 0), the 16:9 end screen (masthead mark and WATCH NEXT as the system kicker),
the 9:16 end card (masthead mark geometry) and photo callouts (their dim and plate shadow in INK, at most 5 levels).
Identical: captions (incl. the badge frames), hook / chapter / myth cards, Reel covers and grid tiles, maps, number
cards, then-and-now, Story frames, and the loop bridge (bit-identical frames, checked on the Aswan bridge).

## Board recipe

`board.py` (this folder) renders the 13 rows of the cycle-0 system board fresh from one tree (the lander runs it; the
builder never renders the panel board): `python board.py <tree> <dev checkout> <out> [--brand second]`. See the
build note C:/tmp/qf-handoff/queue/CYCLE0.build-1.md for the per-row render list.

## Acceptance

`python measure.py <repo root>`: the masthead on six committed grounds at the three carried sizes, all within the
rulings (peak <= 0.60, halo <= 0.16, contrast >= 5.5), and the system's counts. `tests/test_brand_system.py` holds the
invariants (gates constant, tokens read at call time, no import-time copies, one line-end vocabulary, named eases
bounded and shared, the masthead within the rulings with drawn == brand text, one treatment on every carrying
surface, the system kicker on the end screen).
