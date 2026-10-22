"""Serialize topic-bank mutations and recover interrupted metadata commits."""
from contextlib import contextmanager
from functools import wraps
import json
from pathlib import Path
import threading

from .atomic_files import write_json
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


def recover(bank):
    journal = Path(bank).with_suffix(".transaction.json")
    if journal.is_file():
        for entry in json.loads(journal.read_text(encoding="utf-8")):
            target = Path(entry["path"])
            if target.resolve().parent != Path(bank).resolve().parent:
                raise ValueError("Invalid topic transaction destination")
            write_json(target, entry["value"])
        journal.unlink()


def commit(bank, queries, bank_value, query_value):
    journal = Path(bank).with_suffix(".transaction.json")
    write_json(journal, [{"path": str(Path(queries).resolve()), "value": query_value},
                         {"path": str(Path(bank).resolve()), "value": bank_value}])
    recover(bank)


def serialized(function):
    @wraps(function)
    def call(*args, **kwargs):
        from .topic_proposer import BANK
        with transaction(BANK):
            return function(*args, **kwargs)
    return call
