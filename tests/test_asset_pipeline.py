"""Topics get photo pools without waiting for a person; unready topics are skipped, not failed."""
import importlib
import json


def _real():
    import src.asset_pipeline as ap
    return importlib.reload(ap)  # the conftest stub replaced ensure_pool on the loaded module


def _credits(d, n, **extra):
    d.mkdir(parents=True, exist_ok=True)
    # Each photo carries a "shows the topic" verdict: readiness counts as the video counts (2026-10-28), and a
    # photo never looked at is one the video would not use.
    imgs = [{"file": f"{i:02d}_commons.jpg", "vision_review": {"shows_topic": "yes", "usable": True, "safe": True}}
            for i in range(1, n + 1)]
    for im in imgs:
        (d / im["file"]).write_bytes(b"x")
    (d / "credits.json").write_text(json.dumps({"images": imgs, **extra}), encoding="utf-8")


def test_ready_pool_is_used_as_is(monkeypatch, tmp_path):
    ap = _real()
    _credits(tmp_path / "t1", 6, reviewed=True, reviewed_by="vision")
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

    def fake_fetch(cmd, **k):
        tid = cmd[cmd.index("--topic-id") + 1]
        fetched.append(tid)
        _credits(tmp_path / tid, 6 if tid == "good" else 1)

    monkeypatch.setattr(ap.subprocess, "run", fake_fetch)
    import src.image_review as ir

    def review(topic, d, apply=True):
        from src.credits import load_credits, save_credits
        c = load_credits(d)
        c["reviewed"] = len(c["images"]) >= 5
        save_credits(d, c)
        return {"status": "approved" if c["reviewed"] else "held", "kept": len(c["images"])}
    monkeypatch.setattr(ir, "review_pool", review)
    chosen = ap.first_ready([{"id": "thin"}, {"id": "good"}], tmp_path)
    assert chosen == {"id": "good"} and fetched == ["thin", "good"]
