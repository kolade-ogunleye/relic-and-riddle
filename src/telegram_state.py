"""Durable Telegram transport references and at-most-once update admission.

Admission precedes side effects: a crash may interrupt a command, but cannot
silently replay a paid action. Operators inspect /jobs and explicitly retry.
"""
import hashlib
import json
import time


def initialize(db):
    with db._conn() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS telegram_transport (
            namespace TEXT, key TEXT, value TEXT, touched REAL,
            PRIMARY KEY(namespace,key))""")


KEPT = ("review-card", "review-files")   # never purged (QF-114)


def put(db, namespace, key, value):
    # QF-114: every card also binds the master's composition record and its provenance ledger as they are now, so a
    # post can be checked against the review you approved (the fingerprint binds the copy, video, cover, Stories and
    # carousel; these two records it does not). Recorded here, wherever a card is recorded.
    files = review_files(db, str(key).rsplit(":", 1)[0]) if namespace == "review-card" else None
    with db._conn() as conn:
        conn.execute("INSERT OR REPLACE INTO telegram_transport VALUES (?,?,?,?)",
                     (namespace, str(key), json.dumps(value), time.time()))
        if files is not None:
            conn.execute("INSERT OR REPLACE INTO telegram_transport VALUES (?,?,?,?)",
                         ("review-files", str(key), json.dumps(files), time.time()))
        # Review records are kept for good (QF-114): a post is checked against its card for as long as it can post.
        conn.execute("DELETE FROM telegram_transport WHERE namespace NOT IN ('inbox', 'review-card', 'review-files') "
                     "AND touched<?", (time.time()-90*86400,))
        if namespace not in KEPT:
            conn.execute("DELETE FROM telegram_transport WHERE rowid IN (SELECT rowid FROM telegram_transport WHERE namespace=? ORDER BY touched DESC LIMIT -1 OFFSET 10000)", (namespace,))


def get(db, namespace, key, default=None):
    with db._conn() as conn:
        row = conn.execute("SELECT value FROM telegram_transport WHERE namespace=? AND key=?",
                           (namespace, str(key))).fetchone()
    return json.loads(row[0]) if row else default


def admit(db, bot_id, update_id):
    with db._conn() as conn:
        cur = conn.execute("INSERT OR IGNORE INTO telegram_transport VALUES ('inbox',?,?,?)",
                           (f"{bot_id}:{update_id}", '"admitted"', time.time()))
        # Telegram retains updates for at most 24 hours; keep a generous window.
        conn.execute("DELETE FROM telegram_transport WHERE namespace='inbox' AND touched<?", (time.time()-7*86400,))
        return cur.rowcount == 1


def encode(db, data):
    if len(data.encode("utf-8")) <= 64 and not data.startswith("ref:"):
        return data
    key = hashlib.sha256(data.encode()).hexdigest()[:40]
    put(db, "callback", key, data)
    return "ref:" + key


def decode(db, data):
    if data.startswith("ref:"):
        return get(db, "callback", data[4:], "expired:")
    return data


def prepare(db, payload):
    result = dict(payload)
    markup = result.get("reply_markup")
    if markup:
        markup = json.loads(markup) if isinstance(markup, str) else dict(markup)
        for row in markup.get("inline_keyboard", []):
            for button in row:
                if "callback_data" in button:
                    button["callback_data"] = encode(db, button["callback_data"])
        result["reply_markup"] = json.dumps(markup)
    return result


# The master's records and its caption file, beside it (the captions go up on Facebook from the episode's button).
REVIEW_FILES = {"composition record": ".composition.json", "provenance ledger": "provenance.json", "captions": ".srt"}


def review_files(db, run_id) -> dict:
    """{name: SHA-256 or None} of the run's master's composition record, its provenance ledger and its caption file
    (QF-114)."""
    from pathlib import Path
    run = db.get_run(run_id) or {}
    if not run.get("master_video_path"):
        return {}
    master = Path(run["master_video_path"])
    out = {}
    for name, suffix in REVIEW_FILES.items():
        path = master.with_suffix(suffix) if suffix.startswith(".") else master.parent / suffix
        try:
            out[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            out[name] = None
    return out


def review_fingerprint(db, run_id):
    """Bind review to the recorded public copy and actual deliverable bytes."""
    from pathlib import Path
    run = db.get_run(run_id)
    if not run or not run.get("master_video_path"):
        raise ValueError("This run has no review video")
    video = Path(run["master_video_path"])
    root = video.parent.resolve()
    digest = hashlib.sha256(json.dumps(run.get("script_payload") or {}, sort_keys=True).encode())
    paths = {video, Path(run.get("thumbnail_cover_path") or root / "thumbnail_cover.jpg")}
    if (root / "review_proxy.mp4").is_file():
        paths.add(root / "review_proxy.mp4")
    paths.update((root / "stories").glob("story_*.jpg"))
    from .carousel_lazy import PLAN, planned
    # A planned carousel (QF-81) is drawn after review, at its slot: its slides were never on the card and are bound
    # by its own plan and audit, so installing them leaves the approved fingerprint (QF-110) as it was. The plan
    # itself (its photos and their SHA-256s, its posters and its copy's digest) is bound in their place (QF-81b): it
    # is written once while the run is made, before its card, so a plan changed after approval breaks the binding.
    if planned(root):
        paths.add(root / PLAN)
    else:
        paths.update((root / "carousel").glob("carousel_slide_*.jpg"))
    for path in sorted(paths):
        if not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError("A review artifact is missing or outside the run folder")
        digest.update(path.name.encode())
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024*1024), b""):
                digest.update(chunk)
    return digest.hexdigest()
