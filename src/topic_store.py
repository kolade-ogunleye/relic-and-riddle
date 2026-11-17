"""Serialize topic-bank mutations and recover interrupted metadata commits.

QF-111: the runtime never writes the tracked data/topics/topic_bank.json or image_queries.json. A host's own topics
(added in Telegram: /add, an approved proposal) and their photo searches are kept in this host's copies
(src/host_state.py, data/host/); every reader takes the shipped bank with this host's additions after it
(read_bank, read_queries), so a release can still ship bank edits and new topics. A topic the release ships under
the same id as one this host added is the release's.
"""
from contextlib import contextmanager
from functools import wraps
import json
from pathlib import Path
import threading

from . import host_state
from .publication_lock import publication_lock

_LOCK = threading.RLock()
_LOCAL = threading.local()


@contextmanager
def transaction(bank):
    with _LOCK:
        if getattr(_LOCAL, "active", False):
            yield
            return
        with publication_lock(bank, "topic-store"):
            _LOCAL.active = True
            try:
                recover(bank)
                yield
            finally:
                _LOCAL.active = False


def _shipped(path) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _own(path) -> object:
    copy = host_state.host_copy(path)
    if copy == Path(path):
        return None
    try:
        return json.loads(copy.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_bank(bank) -> dict:
    """The topic bank this host uses: the shipped bank, then this host's own topics (ids it does not ship)."""
    shipped = _shipped(bank)
    if not isinstance(shipped, dict):
        shipped = _own(bank) if isinstance(_own(bank), dict) else None
        if shipped is None:
            raise FileNotFoundError(f"No topic bank at {bank}")
        return shipped
    own = _own(bank)
    topics = list(shipped.get("topics") or [])
    ids = {t.get("id") for t in topics if isinstance(t, dict)}
    for topic in (own.get("topics") or []) if isinstance(own, dict) else []:
        if isinstance(topic, dict) and topic.get("id") not in ids:
            topics.append(topic)
            ids.add(topic.get("id"))
    merged = dict(shipped, topics=topics)
    if "total" in merged:
        merged["total"] = len(topics)
    return merged


def read_queries(queries) -> dict:
    """The photo searches this host uses: the shipped ones, then this host's own (topics the release does not list)."""
    shipped = _shipped(queries)
    own = _own(queries)
    merged = dict(own) if isinstance(own, dict) else {}
    merged.update(shipped if isinstance(shipped, dict) else {})
    return merged


def _own_part(path, value, kind):
    """What this host keeps of `value`: the topics (or search entries) the shipped file does not hold."""
    shipped = _shipped(path)
    if kind == "bank":
        ids = {t.get("id") for t in ((shipped or {}).get("topics") or []) if isinstance(t, dict)} \
            if isinstance(shipped, dict) else set()
        topics = [t for t in value.get("topics") or [] if isinstance(t, dict) and t.get("id") not in ids]
        return dict(value, topics=topics, total=len(topics)) if "total" in value else dict(value, topics=topics)
    keys = set(shipped) if isinstance(shipped, dict) else set()
    return {k: v for k, v in value.items() if k not in keys}


def _journal(bank) -> Path:
    return host_state.host_copy(Path(bank).with_suffix(".transaction.json"))


def recover(bank):
    # A journal left beside the tracked bank by code before QF-111 is finished the same way (into this host's copies).
    for journal in dict.fromkeys((_journal(bank), Path(bank).with_suffix(".transaction.json"))):
        if journal.is_file():
            for entry in json.loads(journal.read_text(encoding="utf-8")):
                target = Path(entry["path"])
                if target.resolve().parent != Path(bank).resolve().parent:
                    raise ValueError("Invalid topic transaction destination")
                kind = "bank" if target.resolve() == Path(bank).resolve() else "queries"
                value = entry["value"]
                in_place = host_state.host_copy(target) == target   # outside the checkout: the file is the whole store
                host_state.write_json(target, value if in_place else _own_part(target, value, kind))
            journal.unlink()


def commit(bank, queries, bank_value, query_value):
    """Writes the bank and searches as read_bank/read_queries returned them plus the caller's change: this host's
    part of each (QF-111), through a journal in this host's copies so an interrupted commit finishes on recovery."""
    host_state.write_json(_journal(bank), [{"path": str(Path(queries).resolve()), "value": query_value},
                                           {"path": str(Path(bank).resolve()), "value": bank_value}])
    recover(bank)


def serialized(function):
    @wraps(function)
    def call(*args, **kwargs):
        from .topic_proposer import BANK
        with transaction(BANK):
            return function(*args, **kwargs)
    return call
