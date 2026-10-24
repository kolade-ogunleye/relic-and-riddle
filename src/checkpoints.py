"""Atomic stage records: reuse requires matching inputs and intact outputs."""

import hashlib
import json
import os
import tempfile
from pathlib import Path

from .credits import sha256_file


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def file_inputs(paths):
    return {str(Path(p).resolve()): sha256_file(Path(p)) for p in paths}


def portable_inputs(run_dir, paths):
    """file_inputs keyed by the name of a file inside run_dir (and the path of one outside it), so a
    stage's record means the same in another run's folder: a regeneration carries the music bed over
    with the voiceover it was made for (2026-10-24)."""
    run_dir = Path(run_dir).resolve()
    out = {}
    for p in paths:
        p = Path(p).resolve()
        key = p.name if p.parent == run_dir else str(p)
        out[key] = sha256_file(p)
    return out


class Checkpoints:
    def __init__(self, run_dir):
        self.path = Path(run_dir) / "checkpoints.json"

    def read(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def reusable(self, stage, inputs):
        record = self.read().get(stage, {})
        outputs = record.get("outputs", {})
        return bool(outputs and record.get("inputs") == fingerprint(inputs) and
                    all(expected and sha256_file(Path(p)) == expected for p, expected in outputs.items()))

    def complete(self, stage, inputs, outputs):
        self._write(stage, fingerprint(inputs), outputs)

    def adopt(self, stage, inputs_fingerprint, outputs):
        """A stage's record carried over from another run of the same work (its inputs fingerprint, these
        copies of its outputs): a regeneration reuses the voiceover it already paid for."""
        self._write(stage, inputs_fingerprint, outputs)

    def _write(self, stage, inputs_fingerprint, outputs):
        hashes = file_inputs(outputs)
        if not hashes or not all(hashes.values()):
            raise ValueError(f"Cannot checkpoint incomplete stage: {stage}")
        records = self.read()
        records[stage] = {"inputs": inputs_fingerprint, "outputs": hashes}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=self.path.parent, suffix=".json.tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(records, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def topic_inputs(topic):
    """Queue counters and statuses are operational state, not research inputs."""
    operational = {"status", "times_published", "last_published_at", "last_run_id", "updated_at", "priority"}
    return {key: value for key, value in topic.items() if key not in operational}


def cached_artifacts(run_dir, stage, inputs, producer):
    """Reuse an ordered packaging result only while every artifact remains intact."""
    checkpoint = Checkpoints(Path(run_dir) / "stage_cache" / stage)
    if checkpoint.reusable("artifacts", inputs):
        return [Path(p) for p in checkpoint.read()["artifacts"]["outputs"]]
    result = producer()
    outputs = [Path(p) for p in result]
    if outputs and all(p.is_file() for p in outputs):
        checkpoint.complete("artifacts", inputs, outputs)
    return result
