"""PLATEFORM design stream (freeze cycle 6): the hook frame (frame 0) and the 9:16 myth card (last frame) on the
reviewers' named photographs, through the pipeline's own plan calls (design_cards.plan_hook_frame / plan_myth_fact,
frame_at), under the ground form RR_PLATE_FORM names (default "base"). The copy is the board's own hook and myth
fixture (the Richat sheet's claim and myth, tests/test_freeze_cards_2026_11_04: hook_plan, richat_myth), the
pairing every review of these cases used; the photographs are the topics' real asset photographs.

    python pf_cards.py <tree> <dev checkout> <out> <kinds: hook,myth> <pick> ...
        pick: <topic prefix>:<photo stem prefix> (e.g. gobekli_tepe:07) | hero_obelisk | hero_amber

Writes <out>/<form>[__<brand>]/<kind>__<pick>.png and records.json (drawn text, the element boxes and fills, the
ground record). BRAND=second|second-colours configures a test palette (QF-35a render_surfaces).
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
from pathlib import Path


def main() -> None:
    tree, dev, out_root = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve()
    kinds, picks = sys.argv[4].split(","), sys.argv[5:]
    os.environ.update(FONT_SIZE="80", PYTHONIOENCODING="utf-8", RR_FIXTURE_REPO=str(dev))

    def offline(*_a, **_k):
        raise OSError("offline")
    socket.create_connection = offline
    sys.path.insert(0, str(tree))
    sys.path.insert(0, str(tree / "docs/reviews/freeze-panel-2026-11-04/fixtures"))
    import fixtures as fx
    from src import brand, config, design_cards as dc, plate_form
    config.FACT_RECORD_KEY = "test-only-fact-record-key-0123456789abcdef"
    br = os.environ.get("BRAND", "default")
    if br != "default":
        sys.path.insert(0, str(tree / "docs/reviews/quality-fixes-2026-11/QF-35a"))
        import render_surfaces
        brand.configure(render_surfaces.second_brand(brand, {"second": "Montserrat", "second-colours": None}[br]),
                        validate=False)
    from tests import test_freeze_cards_2026_11_04 as fc
    heroes = {h["topic"]: fx.path(h["photo"]) for h in fx.section("cards")["hero_photos"]}
    named = {"hero_amber": heroes["amber_room_heist"], "hero_obelisk": heroes["unfinished_obelisk_aswan"],
             "hero_richat": fx.still("richat_structure_sahara_eye_01_commons_960.jpg")}
    form = plate_form.active()
    out = out_root / (form + ("" if br == "default" else "__" + br))
    out.mkdir(parents=True, exist_ok=True)
    richat = fc.real_sheet("richat_structure_sahara_eye")
    res = {}
    for pk in picks:
        if pk in named:
            ph = Path(named[pk])
        else:
            t, i = pk.split(":", 1)
            hits = sorted(p for d in (dev / "assets").iterdir() if d.is_dir() and d.name.startswith(t)
                          for p in d.glob(f"{i}*.jpg") if "contact_sheet" not in p.name)
            if not hits:
                print("no photo", pk, flush=True)
                continue
            ph = hits[0]
        key = pk.replace(":", "_")
        for kind in kinds:
            t0 = time.time()
            try:
                if kind == "hook":
                    plan = fc.hook_plan(ph)
                    fr = dc.frame_at(plan, 0)
                else:
                    plan = fc.richat_myth(richat, photo=ph, size=fc.TALL)
                    fr = dc.frame_at(plan, plan.frames - 1)
            except Exception as e:   # a refusal is a result (recorded), not a crash
                res[f"{kind}__{key}"] = {"photo": str(ph), "refused": f"{type(e).__name__}: {str(e)[:300]}"}
                print(kind, key, "REFUSED", type(e).__name__, str(e)[:160], flush=True)
                continue
            fr.convert("RGB").save(out / f"{kind}__{key}.png")
            meta = plan.meta or {}
            ground = meta.get("scrim") if kind == "hook" else meta.get("panel_edges")
            els = []
            for e in plan.elements:
                if getattr(e, "kind", "") == "text" and e.text:
                    els.append({"name": e.name, "box": list(e.box), "fill": list(e.fill)[:3], "text": e.text})
            res[f"{kind}__{key}"] = {"photo": str(ph), "text": plan.drawn_text, "elements": els,
                                     "ground": ground, "contrast": meta.get("drawn_contrast_min")}
            print(kind, key, round(time.time() - t0), "s", flush=True)
    f = out / "records.json"
    old = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    old.update(res)
    f.write_text(json.dumps(old, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
