# Archive film in a vertical frame (2026-10-28)

**Source:** *Pompeii And Vesuvius* (1950, public domain, AV Geeks via the Internet Archive), 640x480 (4:3), 505.7 s.
A 6 s segment cut at 35 % by `src/footage.make_segment`, frame taken at 3 s, shown here at half size (540x960).

| | Before: fill and crop | After: fit over blur |
|---|---|---|
| Frame | `before_fill_crop.jpg` | `after_fit_over_blur.jpg` |
| Source width kept | 270 of 640 px (42 %) | 640 of 640 px (100 %) |
| Upscale of kept pixels | 4.0x | 1.69x |
| What shows | half of one sculpted figure, soft | the whole relief: figures, the bull, the lizards |

**Rule now (`footage.frame_filter`):** a source wider than 4:5 is fitted to the 1080 px width, centred over a blurred,
darkened copy of itself; a vertical source still fills the frame. It applies to every clip cut by `make_segment`:
archive film and the operator's own uploads.
