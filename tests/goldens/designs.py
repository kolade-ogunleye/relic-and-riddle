"""The golden-image registry (QF-29a; scripts/goldens.py). A design is added here only when the operator has agreed
it is FROZEN (FREEZE-PLAYBOOK.md); the lander then takes its golden:

    scripts/goldens.py take <name> --frozen-on <date>

Each entry: Design(name, surface, case, format, kind="still"|"video", inputs=(committed repo-relative paths),
render=<function(inputs, work)>, samples=(frame numbers, video only), settings={ENV: value}). The render uses the
design's real code path; a video design returns its clip encoded by the production encoder. See
tests/golden_proof_designs.py for a still and a video example (the mechanism's proof fixtures, not frozen designs).
"""
from __future__ import annotations

from scripts.goldens import Design  # noqa: F401  (the entries' type)

DESIGNS: dict = {}   # name -> Design; empty until the first design is frozen
