"""Source identity for transformed assets; transformations do not create usage rights."""

import json
import hashlib
import os
import tempfile
import threading
from pathlib import Path

from .credits import sha256_file

_ARCHIVE_LOCK = threading.Lock()


def archive_generator(generator, directory):
    """Keep the exact code bytes used by an artifact despite later repository edits."""
    blob = Path(generator).read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    directory = Path(directory) / "source_versions"
    directory.mkdir(parents=True, exist_ok=True)
    archived = directory / f"generator-{digest}.py"
    with _ARCHIVE_LOCK:
        if sha256_file(archived) != digest:
            handle, temporary = tempfile.mkstemp(dir=directory)
            try:
                with os.fdopen(handle, "wb") as stream:
                    stream.write(blob)
                os.replace(temporary, archived)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
    return archived


def sidecar(path):
    return Path(str(path) + ".lineage.json")


def record_derived(output, sources, operation, settings=None):
    output = Path(output).resolve()
    sources = [Path(p).resolve() for p in sources]
    if output in sources:
        raise ValueError("Derived outputs must preserve their source files")
    records = [{"path": str(p), "sha256": sha256_file(p)} for p in sources]
    digest = sha256_file(output)
    if not digest or not records or not all(r["sha256"] for r in records):
        raise ValueError("Cannot record lineage for missing files")
    sidecar(output).write_text(json.dumps({"path": str(output), "sha256": digest,
        "operation": operation, "settings": settings or {}, "sources": records}, indent=2), encoding="utf-8")


def record_graphic(output, generator, inputs, media_sources=(), source_credits=(), dependencies=()):
    """Declare original drawing while retaining any incorporated third-party media."""
    generator = archive_generator(generator, Path(output).parent)
    record_derived(output, [generator, *media_sources, *dependencies], "procedural_graphic", {
        "inputs": inputs, "media_sources": [str(Path(p).resolve()) for p in media_sources],
        "original_drawing": True,
        "source_credits": list(source_credits),
    })


def source_chain(path, seen=None):
    """Return checked lineage records; fail on stale records, missing sources, or cycles."""
    path = Path(path).resolve()
    seen = set() if seen is None else seen
    if path in seen:
        raise ValueError(f"Cyclic asset lineage: {path}")
    seen = seen | {path}
    record_path = sidecar(path)
    if not record_path.is_file():
        return []
    data = json.loads(record_path.read_text(encoding="utf-8"))
    if Path(data["path"]).resolve() != path or data["sha256"] != sha256_file(path):
        raise ValueError(f"Stale derived asset: {path}")
    records = [{"path": str(record_path), "sha256": sha256_file(record_path), "kind": "lineage"}]
    for source in data["sources"]:
        source_path = Path(source["path"])
        if not source.get("sha256") or sha256_file(source_path) != source["sha256"]:
            raise ValueError(f"Changed or missing source: {source_path}")
        records.append(source)
        records.extend(source_chain(source_path, seen))
    return records
