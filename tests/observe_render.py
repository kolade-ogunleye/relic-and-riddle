"""Run tests/test_brand_system_r4.render_all under the size observer in this (fresh) interpreter and print the
off-ladder (site -> sizes) table as one "OBSERVED=<json>" line. Used by test_brand_system_r4 so no font an earlier
test cached can hide its creation.   python tests/observe_render.py <work dir>"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import size_observer
import test_brand_system_r4 as t

work = Path(sys.argv[1])
work.mkdir(parents=True, exist_ok=True)
with size_observer.observe() as seen:
    t.render_all(work)
print("OBSERVED=" + json.dumps(t._off_ladder(seen)))
