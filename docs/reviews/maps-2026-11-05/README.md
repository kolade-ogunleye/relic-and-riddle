# Maps and timelines: handover from the cloud session (2026-11-05)

The map and timeline work is committed as **work in progress**: it has NOT passed its final independent review.

- `round11_review1.md`: the latest independent review (VERDICT: NOT OK; no wrong names on 98 new renders, 13 renders below 9 on design).
- `round11_fix2.md`: what the fix round after that review changed (not yet reviewed), and its open items and decisions.
- `round11_fix1.md`: the round-11 rebuild (identity from geometry, leader-or-drop, outline gate, ruling R11, 200-site sweep).
- `harness/builder/`: render harness (`hr11.py` renders a job list at both sizes, `boards.py` builds before/after boards, `sweep.py`).
- `harness/reviewer/`: the reviewer's renderer (`hr.py`), its independent name checker (`mychk.py`, raw Natural Earth, own point-in-polygon), board and pair builders and test runners.

Paths were rewritten from the cloud scratchpad to `./maps_work` (renders, boards, logs) and `python` (the project venv); adjust before running.
The render images themselves were not transferred (hundreds of MB); re-render with the harness.
