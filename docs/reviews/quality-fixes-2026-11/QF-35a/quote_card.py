"""QF-35a: the quote card (held: config.QUOTE_CARDS_ENABLED is false and the renderer refuses "held", so no run
and no committed card field carries one) drawn by one code tree, as pixel hashes.

    python quote_card.py <tree>

Run it on the base tree and on this row's tree: the default hashes must be equal. Inputs, all committed: the design
tests' quote field (tests/test_design_cards_2026_11_03.py `quote()` on its SHEET, a test fixture, not a run's
words), signed with the tests' throwaway key and with the hold lifted in this process only, on the committed Richat
still of the freeze-panel fixtures; every frame, 9:16 and 16:9. Where the tree has the brand configuration the card
is drawn again in the test brand of tests/test_qf35a_role_tokens.py (its palette, masthead and Montserrat display)
and, if that refuses for fit, with today's display face kept. Prints JSON; writes nothing.
"""
import hashlib
import importlib.util
import json
import os
import socket
import sys
from pathlib import Path

FIX = Path("docs") / "reviews" / "freeze-panel-2026-11-04" / "fixtures"
CARD_FIELD_KEY = "test-only-fact-record-key-0123456789abcdef"   # the tests' key (tests/conftest.py)
TODAY_GOLD = (242, 182, 48)


def _offline(*_a, **_k):
    raise RuntimeError("quote_card.py is offline")


def main(root: Path) -> None:
    root = root.resolve()
    os.environ.update(FONT_SIZE="80", PYTHONIOENCODING="utf-8")
    socket.create_connection = _offline
    socket.socket.connect = _offline
    sys.path.insert(0, str(root))
    from src import brand, config
    from src import design_cards as dc
    config.FACT_RECORD_KEY = CARD_FIELD_KEY
    config.QUOTE_CARDS_ENABLED = True
    from tests import test_design_cards_2026_11_03 as t
    still = root / FIX / "stills" / "richat_structure_sahara_eye_01_commons_960.jpg"

    def drawn() -> dict:
        out = {}
        for (w, h), tag in (((1080, 1920), "916"), ((1920, 1080), "169")):
            try:
                plan = dc.plan_quote_card(t.quote(photo=still), t.SHEET, w, h)
            except dc.CardRefused as e:
                out[f"quote {tag}"] = {"refused": str(e)[:160]}
                continue
            hz, n = hashlib.sha256(), 0
            for k in range(plan.frames):
                rgb = dc.frame_at(plan, k).convert("RGB")
                hz.update(f"{rgb.width}x{rgb.height};".encode())
                hz.update(rgb.tobytes())
                n += 1
            out[f"quote {tag}"] = {"sha256": hz.hexdigest(), "frames": n,
                                   "faces": sorted({e.face for e in plan.elements if e.kind == "text"}),
                                   "today_gold_fills": sum(tuple(e.fill) == TODAY_GOLD for e in plan.elements)}
        return out

    result = {"schema": 1, "default": drawn()}
    if hasattr(brand, "use"):
        spec = importlib.util.spec_from_file_location("qf35a_test", root / "tests" / "test_qf35a_role_tokens.py")
        test = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(test)
        second = test._second_brand()
        with brand.use(second):
            result["second_brand"] = drawn()
        with brand.use(second.derive("Second test brand, display face kept",
                                     fonts={"display": brand.DEFAULT_BRAND.fonts["display"]})):
            result["second_brand_display_kept"] = drawn()
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: quote_card.py <tree>")
    main(Path(sys.argv[1]))
