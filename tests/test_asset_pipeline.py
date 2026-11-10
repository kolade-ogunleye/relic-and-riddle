"""Topics get photo pools without waiting for a person; unready topics are skipped, not failed."""
import importlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
from PIL import Image


def _real():
    import src.asset_pipeline as ap
    return importlib.reload(ap)  # the conftest stub replaced ensure_pool on the loaded module


def _credits(d, n, **extra):
    d.mkdir(parents=True, exist_ok=True)
    # Each photo carries a "shows the topic" verdict: readiness counts as the video counts (2026-10-28), and a
    # photo never looked at is one the video would not use.
    imgs = [{"file": f"{i:02d}_commons.jpg", "shot_class": ("wide", "interior", "detail")[i % 3], "vision_review": {"shows_topic": "yes", "usable": True, "safe": True}}
            for i in range(1, n + 1)]
    for index, im in enumerate(imgs):
        pixels = np.random.default_rng(index).integers(0, 256, (48, 64, 3), dtype=np.uint8)
        Image.fromarray(pixels).save(d / im["file"])
    (d / "credits.json").write_text(json.dumps({"images": imgs, "pool_fetched_at": datetime.now(UTC).isoformat(), **extra}), encoding="utf-8")


def test_ready_pool_is_used_as_is(monkeypatch, tmp_path):
    ap = _real()
    _credits(tmp_path / "t1", 12, reviewed=True, reviewed_by="vision")
    monkeypatch.setattr(ap.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fetched")))
    assert ap.ensure_pool({"id": "t1"}, tmp_path)


def test_operator_hold_stands(monkeypatch, tmp_path):
    ap = _real()
    _credits(tmp_path / "t1", 2, reviewed=False, reviewed_by="operator")
    monkeypatch.setattr(ap.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fetched")))
    assert not ap.ensure_pool({"id": "t1"}, tmp_path)


def test_empty_pool_is_fetched_then_reviewed_and_unready_topics_skipped(monkeypatch, tmp_path):
    ap = _real()
    fetched = []
    _credits(tmp_path / "thin", 1)  # an exhausted short pool; the good pool starts empty

    def fake_fetch(cmd, **k):
        tid = cmd[cmd.index("--topic-id") + 1]
        fetched.append(tid)
        _credits(tmp_path / tid, 12 if tid == "good" else 1)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(ap.subprocess, "run", fake_fetch)
    import src.image_review as ir

    def review(topic, d, apply=True):
        from src.credits import load_credits, save_credits
        c = load_credits(d)
        c["reviewed"] = len(c["images"]) >= 9
        save_credits(d, c)
        return {"status": "approved" if c["reviewed"] else "held", "kept": len(c["images"])}
    monkeypatch.setattr(ir, "review_pool", review)
    chosen = ap.first_ready([{"id": "thin"}, {"id": "good"}], tmp_path)
    assert chosen == {"id": "good"} and fetched == ["thin", "good"]
