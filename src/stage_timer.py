"""Per-stage wall-clock timings for a run, logged and kept in <run_dir>/timings.json.

Timing is observational only: a failure to record never affects the run.
"""
import json
import logging
import threading
import time
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)
_WRITE_LOCK = threading.Lock()  # background stages and the main thread share one timings file


class StageTimer:
    def __init__(self, run_dir):
        self.path = Path(run_dir) / "timings.json"

    @contextmanager
    def stage(self, name: str):
        started = time.perf_counter()
        ok = False
        try:
            yield
            ok = True
        finally:
            self.record(name, time.perf_counter() - started, ok)

    def record(self, name: str, seconds: float, ok: bool = True) -> None:
        log.info("Stage %-24s %7.2fs%s", name, seconds, "" if ok else " (failed)")
        try:
            with _WRITE_LOCK:
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8"))
                    if not isinstance(data, list):
                        data = []
                except (OSError, ValueError):
                    data = []
                data.append({"stage": name, "seconds": round(seconds, 3), "ok": ok,
                             "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        except OSError as e:
            log.debug("Could not record stage timing: %s", e)
