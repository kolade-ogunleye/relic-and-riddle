# QF-133: the reconstruction badge draws in its bundled IBM Plex Mono face

## Defect

The AI-reconstruction badge ("AI ILLUSTRATION · NOT A PHOTO") is measured in IBM Plex Mono at weight 600, the
bundled `IBMPlexMono-SemiBold.ttf`. Its ASS style asked libass for "IBM Plex Mono", which is the typographic family
(name ID 16) of the two bundled Plex files. libass matches a Fontname only against a file's legacy family (ID 1),
full name (ID 4) and PostScript name (ID 6). For these files those names are "IBM Plex Mono SmBld" and "IBM Plex
Mono Medm". No bundled face answered the request, so libass drew the badge in a system face. On this PC that was
Arial Bold, which is wider than Plex, and the label ran past the right edge of its plate. compose_video let this
through: for a static badge face it ignored the "no bundled font is named" problem.

## Fix (src/video_composer.py)

- `_badge_family()`: a static badge face is now asked for under the name libass knows the measured file by. The new
  `_libass_fontname(path, fonts_dir)` returns the first of the file's ID 1, ID 4 and ID 6 names that no other bundled
  font carries. For the default brand that is "IBM Plex Mono SmBld", which names `IBMPlexMono-SemiBold.ttf` and no
  other file. A brand whose static badge face is Anton still gets "Anton". Derived badge faces (RR Sans SemiBold,
  RR Serif SemiBold) are unchanged.
- `compose_video`: a badge name that no bundled face carries is now refused for every badge face, as it already was
  for a derived face. The badge style's Bold -1 (700) is unchanged: libass picks the 600 file and adds no
  synthetic weight, because 700 is within 150 of 600.
- Not changed: the plate geometry, the badge size, the position, the colours, and the badge's metric lookup. The
  metric lookup still runs unconditionally in build_subtitles, which is QF-35b's item. It is a different function
  from `_badge_family`, so it is left to that row.

## Measurement (render_badge_cases.py, gen_ass.py; measurements.json)

Six real development runs (gol_gumbaz 1793278143, richat 1793474843, voynich 1792965308, tunguska 1792930600, odessa
1793100750, amber_room 1792805842) were measured in both formats, 12 cases in all. No development run has a
generated illustration, so for each case:

- The run's own `words.json` was written by `build_subtitles` from the base tree (18049e0e) and from this commit,
  with the badge on screen from 0 to 4 s.
- libass (ffmpeg 8.1, libass 0.17.4) drew each file at 1.5 s over the run's own first scene: frame 1 s of
  `parallax/scene_01.mp4`, or its first beat image, cover-cropped to 1080x1920 or 1920x1080.
- The resolved face was read from libass's own `fontselect` log. Its PostScript name was mapped to a bundled file,
  and that file's SHA-256 was compared with `fonts/license_evidence.json`. As a control, the badge was drawn again
  from a folder holding only that file.

| | 9:16 before | 9:16 after | 16:9 before | 16:9 after |
|---|---|---|---|---|
| libass resolves the badge (700) to | Arial-BoldMT (system) | IBMPlexMono-SmBld | Arial-BoldMT (system) | IBMPlexMono-SmBld |
| bundled file, SHA-256 = licence record | none | IBMPlexMono-SemiBold.ttf, f04d7c48…a172, 6 of 6 | none | same, 6 of 6 |
| same pixels drawn from that file alone | n/a | 6 of 6 | n/a | 6 of 6 |
| plate (x0, y0, x1, y1) | 132,300,726,345 | unchanged | 96,54,620,94 | unchanged |
| label ink (x0, y0, x1, y1) | 146,306,761,332 | 146,309,709,331 | 110,59,652,82 | 110,62,606,81 |
| ink past the plate | 35 px (right) | 0 px | 32 px (right) | 0 px |
| ink gap left / right inside the plate | 14 / -35 | 14 / 17 | 14 / -32 | 14 / 14 |
| changed pixels, all inside the badge box | | 8,770 per case | | 6,968 per case |
| changed pixels outside the badge box | | 0 | | 0 |

The badge box is the union of the plate and both label inks: 132,300-761,345 (9:16) and 96,54-652,94 (16:9). All 12
cases agree. The rest of every frame, captions included, is pixel-identical to the base.

Evidence crops (half-size JPEG; each run shows the base strip above the strip from this commit):
`badge_916_before_after.jpg` and `badge_169_before_after.jpg`.

Not in scope, for the badge's design review in the freeze loop: with the right face drawn, the 9:16 label sits 3 px
further from the plate's right edge than from its left (17 against 14). The plate width counts the 1.5 px letter
spacing after the last letter too. Vertically the ink is 9 px under the plate top and 14 px above its foot (16:9: 8
and 13). This change did not investigate why.

## Regression tests

`tests/test_qf133_badge_face.py`:

- For both formats, libass's own resolution of the badge Fontname is exactly one bundled file. Its hash is the
  licence record's hash for the file the badge is measured in, the same badge drawn from that file alone is
  pixel-identical, and the label's ink stays strictly inside its plate.
- Without a renderer, the Fontname is one of the names libass matches in the measured file, and in no other bundled
  font.

On the base these fail by assertion: libass draws the badge in Arial-BoldMT, which is no single bundled file, and
"ibm plex mono" is not one of the SemiBold file's libass names. Four assertions in branch-only tests named the old
Fontname, "IBM Plex Mono". They now name "IBM Plex Mono SmBld": two in `tests/test_captions_round2_2026_11_02.py` and
two in `tests/test_qf35a_role_tokens.py`.
