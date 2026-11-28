# Subject source evaluation (design-freeze cycle 6, SUBJECT stream)

- `subject_labels.json`: the BUILDER'S labelled set (not an independent review): 128 dev-pool / asset photographs,
  stratified over every topic (5+ per topic, every photograph named in the cycle-5 reviews and panel 7), each with the
  subject box drawn by eye on the full picture (0-1 of the whole EXIF-turned image), `kind` (object: a bounded
  subject; scene: the whole view is the subject) and a one-line reason. A second, independent pass is to check a sample.
- The failure that matters: a predicted box that MISSES or CUTS the labelled subject by more than 15 %
  (`cover(gt, pred) < 0.85`, evalboxes.py). IoU is reported beside it.
- Scripts (run with RR_DEV = the dev checkout): pool.py (the 353 pool photographs), select_gt.py (the labelled
  selection and its gridded views), run_candidates.py (each candidate's raw output), evaluate.py / eval_det.py /
  feats.py / rules.py (the tables), corpus_cmp.py / cmp_report.py (base vs tip over the pool and every window).
- SOURCES.md: the downloaded models, their URLs, licences, versions and SHA-256.
