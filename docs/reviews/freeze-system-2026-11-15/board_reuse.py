"""The incremental board (cycle 5, lane BOARD): render only the render_surfaces.py jobs whose inputs changed since a
previous board of the same palette, and reuse the rest byte for byte.

    board.py <tree> <dev> <out> --reuse <previous board dir> [--reuse-sample 2] [--reuse-seed N] ...

The rule: a job is reused only when EVERYTHING its render can depend on is proven the same; anything that cannot be
proven renders. board_deps.py says how a job's inputs are recorded (one process per job) and keyed. Per job:

  in git      its key (board_deps.job_key) at the previous commit and at this one, both from git objects: the whole
              of every module it ran a function of; the module-level code of every other module it loaded; every
              tree file an import it asked for could resolve to; every tracked non-Python file outside tests/ and
              docs/ (data/, fonts/, assets/ ...: in every job's key); every tree path it read, probed or listed;
  not in git  checked against the disk now (inputs_now): each file it read outside the tree (the dev runs), each
              path it probed and each folder it listed (a file that appeared, a new run), each artefact (the tree's
              cache/, the frame cache: still the bytes whose builders are in its key), each environment variable it
              looked up;
  itself      a job that raised, has no record, or could not be recorded whole ("never": a Python child process, an
              .env file, an input it rewrote) renders.
For every job at once (NoReuse, a full render, said in REUSE.json): another palette; a worktree that differs from
its HEAD anywhere (the whole tree, untracked files included); another toolchain (Python, every installed
distribution, Pillow's text libraries, the ffmpeg binary, the machine), other tools/ data (sha256 of every file),
another job list; a previous board without DEPS.json of this schema, its environment record or MANIFEST.json.

Proof at run time, independent of the keys (finish): every reused job the STATIC NET names (static_net: the diff
changed a function of a module the job loaded but, by its record, never ran, and the code in the job's key
mentions that function's name) is rendered anyway and compared, plus --reuse-sample N (default 2) reused jobs
picked at random (seed recorded). A difference aborts (exit 3, REUSE-ABORTED.json).

The gate (verify, called by freeze_gate.py) does not take the plan's word: it reads the diff itself and checks by
set membership that no changed path is among a reused job's inputs, re-derives the static net and requires its jobs
rendered, and proves the sample by content hash: every sampled job's file, rendered at this commit, must be the
bytes the previous board's MANIFEST.json lists. Its detection power is stated in REUSE.json "sample.power".

Record: <out>/REUSE.json (the checks, each job's status and why, each reused file and its sha256), <out>/DEPS.json
(reused jobs keep their records), board.json "reuse".

The composed rows (1, 2, 12, 13 and every sheet) are drawn fresh from the merged surfaces in every board: nothing
of them is reused. composed_inputs() is the proof that what they are drawn from is the full render's.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import board_deps

SCHEMA = "board-reuse/2"
REUSE_NAME, DEPS_NAME, ABORT_NAME = "REUSE.json", "DEPS.json", "REUSE-ABORTED.json"
FIX = board_deps.FIX
TOOL_DATA = ("tools",)   # every file under it (untracked data copied into each worktree: geo, models, ffmpeg)
# environment variables read outside Python (the interpreter, native libraries, ffmpeg): always part of the
# environment record, whoever looked them up
NATIVE_ENV = ("PYTHONHASHSEED", "PYTHONUTF8", "PYTHONPATH", "PYTHONOPTIMIZE", "TZ", "LANG", "LC_ALL", "LC_CTYPE",
              "FONTCONFIG_PATH", "FONTCONFIG_FILE", "FFREPORT", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
              "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS", "RELIC_APP_ROOT")
# surfaces.json keys by what names their entries (board.py merge_shards classifies the same keys)
BY_JOB = ("seconds", "omissions")
BY_SURFACE = ("surfaces", "story_title_px", "retired", "rerouted", "bridge_ladder", "wide_grounds",
              "surface_omissions", "masthead_records", "photo_choice", "pointer_records")
BY_EITHER = ("errors",)
# surfaces.json keys that say how the board was made, not what was drawn (composed_inputs leaves them out)
NOT_DRAWN = ("seconds", "reused_seconds", "input_files", "src_sha256", "count")


class NoReuse(Exception):
    """The previous board cannot be reused at all (the reason is recorded; the board renders in full)."""


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _git(tree: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(tree), *args], capture_output=True, text=True,
                       env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
    if r.returncode != 0:
        raise NoReuse(f"git {' '.join(args)}: {r.stderr.strip()[:200]}")
    return r.stdout


# ------------------------------------------------------------------------------------------------ names
def job_of(surface: str) -> str | None:
    """The render_surfaces.py job that draws a surface (a surfaces.json key / kept PNG stem), or None."""
    s = surface[: -len("__ass")] if surface.endswith("__ass") else surface
    t = s.split("__")
    head = t[0]
    try:
        if head in ("map", "numbers", "timeline", "scale"):
            return f"{head} run_{t[1]} {t[2]}"
        if head == "route":
            run = t[1][: -len("_new_stops")] if t[1].endswith("_new_stops") else t[1]
            return f"route run_{run} {t[2]}"
        if head == "timeline_dense":
            return f"timeline_dense {t[-1]}"
        if head == "cross_section":
            return f"cross-section {t[-1]}"
        if head == "thennow":
            return f"thennow {t[1]} {t[2]}"
        if head in ("bridge", "bridge_follow", "endcard", "endscreen"):
            i, topic = t[1].split("_", 1)
            return f"endcard {i} {topic}"
        if head == "cover":
            return f"cover {t[1]}"
        if head == "social":
            return f"social run_{t[1]}"
        if head == "card":
            return "cards"
        if head == "callouts":
            return f"callouts {t[-1]}"
        if head == "captions":
            return "captions"
    except (IndexError, ValueError):
        return None
    return None


def split_record(rec: dict[str, Any], jobs: set[str]) -> dict[str, Any]:
    """The part of a surfaces.json that belongs to `jobs` (every per-job and per-surface entry of theirs)."""
    out: dict[str, Any] = {}
    for k, v in rec.items():
        if k in BY_JOB:
            out[k] = {n: x for n, x in v.items() if n in jobs}
        elif k in BY_SURFACE:
            out[k] = {n: x for n, x in v.items() if job_of(n) in jobs}
        elif k in BY_EITHER:
            out[k] = {n: x for n, x in v.items() if n in jobs or job_of(n) in jobs}
    return {k: v for k, v in out.items() if v or k in ("surfaces", "errors", "seconds")}


def unmapped(rec: dict[str, Any]) -> list[str]:
    """Entries of a surfaces.json that no job of its `seconds` owns (a naming this tool does not know: no reuse)."""
    jobs = set(rec.get("seconds") or {})
    bad = []
    for k in BY_SURFACE:
        bad += [f"{k}:{n}" for n in (rec.get(k) or {}) if job_of(n) not in jobs]
    bad += [f"errors:{n}" for n in (rec.get("errors") or {}) if n not in jobs and job_of(n) not in jobs]
    return bad


# ------------------------------------------------------------------------------------------------ environment
def toolchain() -> dict[str, Any]:
    """What draws and encodes besides the tree: the interpreter, every installed distribution, Pillow's text
    libraries, the ffmpeg binary, the machine."""
    import PIL
    from PIL import features
    out: dict[str, Any] = {"python": sys.version.split()[0], "pillow": PIL.__version__,
                           "machine": [platform.system(), platform.machine(), platform.processor(),
                                       os.cpu_count()]}
    feats = {}
    for f in ("raqm", "fribidi", "harfbuzz", "freetype2", "libjpeg_turbo", "zlib"):
        try:
            feats[f] = features.version(f) if features.check(f) else None
        except Exception:  # noqa: BLE001
            feats[f] = None
    out["pillow_feature_versions"] = feats
    try:
        import numpy
        out["numpy"] = numpy.__version__
    except ImportError:
        out["numpy"] = None
    try:
        from importlib import metadata
        dists = sorted({f"{d.metadata['Name']}=={d.version}" for d in metadata.distributions() if d.metadata["Name"]},
                       key=str.lower)
        out["distributions"] = {"count": len(dists), "sha256": hashlib.sha256("\n".join(dists).encode()).hexdigest()}
    except Exception:  # noqa: BLE001
        out["distributions"] = None
    exe = shutil.which("ffmpeg")
    if exe:
        v = subprocess.run([exe, "-version"], capture_output=True, text=True).stdout
        out["ffmpeg"] = {"version_output_sha256": hashlib.sha256(v.encode()).hexdigest(),
                         "binary_sha256": sha256_file(Path(exe))}
    else:
        out["ffmpeg"] = None
    out["native_env"] = {k: board_deps.env_state(k, os.environ.get(k)) for k in NATIVE_ENV}
    return out


def tool_data(tree: Path) -> dict[str, str]:
    """{relative path: sha256} of everything under the tree's tools/ (data copied into each worktree, never linked)."""
    out = {}
    for top in TOOL_DATA:
        d = tree / top
        if d.is_dir():
            for p in sorted(x for x in d.rglob("*") if x.is_file() and "__pycache__" not in x.parts):
                out[p.relative_to(tree).as_posix()] = sha256_file(p)
    return out


def environment(tree: Path) -> dict[str, Any]:
    """board.json "reuse_env": the inputs of every job that are neither in git nor in a job's own record."""
    data = tool_data(tree)
    tc = toolchain()
    return {"toolchain": tc, "tool_data": data,
            "sha256": hashlib.sha256(json.dumps([tc, sorted(data.items())], sort_keys=True).encode()).hexdigest()}


def flat(d: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(d, dict):
        out: dict[str, Any] = {}
        for k, v in d.items():
            out.update(flat(v, f"{prefix}{k}."))
        return out
    return {prefix[:-1]: d}


# ------------------------------------------------------------------------------------------------ a job's inputs
class Disk:
    """The disk now, asked once per path (the plan and the gate ask for the same files in many jobs)."""

    def __init__(self, tree: Path):
        self.tree = Path(tree)
        self._sha: dict[str, str | None] = {}
        self._untracked: dict[str, str] | None = None

    def sha(self, p: str) -> str | None:
        if p not in self._sha:
            self._sha[p] = sha256_file(Path(p)) if os.path.isfile(p) else None
        return self._sha[p]

    def state(self, p: str) -> str:
        return "dir" if os.path.isdir(p) else "file" if os.path.exists(p) else "absent"

    def names(self, p: str) -> str | None:
        try:
            return hashlib.sha256(json.dumps([n.lower() for n in sorted(os.listdir(p))]).encode()).hexdigest()
        except OSError:
            return None

    def untracked(self) -> dict[str, str]:
        if self._untracked is None:
            self._untracked = board_deps.untracked(self.tree)
        return self._untracked


def inputs_now(rec: dict[str, Any], disk: Disk, tracked: board_deps.Tree, before: dict[str, str]) -> list[str]:
    """What is no longer as the job recorded it, among its inputs that git does not hold (see the module docstring).
    `tracked`: this commit's tree; `before`: the previous tree's untracked files when its board started."""
    bad: list[str] = []
    temp = os.path.normcase(os.path.abspath(tempfile.gettempdir())) + os.sep
    built = rec.get("built") or {}
    for p, sha in (rec.get("reads") or {}).items():
        now = disk.sha(p)
        if now is None:
            if not p.startswith(temp):   # an artefact that is gone is built again by the code in the key
                bad.append(f"read, now gone: {p}")
        elif now != sha:
            bad.append(f"read, now other bytes: {p}")
    for p, state in (rec.get("probes") or {}).items():
        now = disk.state(p)
        if now != state and not (p in built and now == "file" and disk.sha(p) == built[p]["sha256"]):
            bad.append(f"probed {state}, now {now}: {p}")
    for p, sha in (rec.get("listed") or {}).items():
        if disk.names(p) != sha:
            bad.append(f"listed, now other names: {p}")
    for rel, sha in (rec.get("tree_reads") or {}).items():   # the tree's untracked files (an on-disk cache)
        if tracked.real(rel) is None and not rel.startswith(board_deps.TOOLS):
            now = disk.sha(str(disk.tree / rel))
            if now is not None and now != sha:
                bad.append(f"tree artefact read, now other bytes: {rel}")
    for rel, state in (rec.get("tree_probes") or {}).items():
        if tracked.real(rel) is None and rel.strip("/").lower() not in tracked.dirs() \
                and not rel.startswith(board_deps.TOOLS):
            p = str(disk.tree / rel)
            now = disk.state(p)
            mine = built.get("T:" + rel)
            if now != state and not (mine and now == "file" and disk.sha(p) == mine["sha256"]) \
                    and not (state == "file" and now == "absent"):   # a cache that is gone is built again
                bad.append(f"tree path probed {state}, now {now}: {rel}")
    now_untracked = None
    for d in rec.get("tree_listed") or {}:   # an untracked file that came or went in a folder the job listed
        if now_untracked is None:
            now_untracked = disk.untracked()
        pre = d.strip("/") + "/" if d.strip("/") else ""
        was = {r for r in before if r.startswith(pre) and "/" not in r[len(pre):]}
        now = {r for r in now_untracked if r.startswith(pre) and "/" not in r[len(pre):]}
        if "*" in before or "*" in now_untracked or was != now:
            bad.append(f"tree folder listed, its untracked files changed: {d or '.'} {sorted(was ^ now)[:3]}")
    for p, e in built.items():
        q = str(disk.tree / p[2:]) if p.startswith("T:") else p
        now = disk.sha(q)
        if now is not None and now != e["sha256"]:
            bad.append(f"artefact the job built, now other bytes: {p}")
    for p, e in (rec.get("artefacts") or {}).items():
        q = str(disk.tree / p[2:]) if p.startswith("T:") else p
        now = disk.sha(q)
        if now is not None and now != e["sha256"]:
            bad.append(f"artefact, now other bytes: {p}")
    for name, state in (rec.get("env") or {}).items():
        if board_deps.env_state(name, os.environ.get(name)) != state:
            bad.append(f"environment variable {name} differs")
    return bad


def diff_names(tree: Path, prev: str, new: str) -> list[str]:
    """Every path that differs between two commits (added, removed, modified; a rename is both its names)."""
    if prev == new:
        return []
    return [n for n in _git(tree, "diff", "--name-only", "--no-renames", f"{prev}..{new}").splitlines() if n]


def _tokens(unit: str) -> set[str]:
    """The names another module can reach a changed unit by: its own name; a dunder method by its class's."""
    parts = unit.replace(".<body>", "").split(".")
    last = parts[-1]
    if last.startswith("__") and last.endswith("__") and len(parts) > 1:
        return {parts[-2]}
    return {last}


def static_net(tp: board_deps.Tree, tn: board_deps.Tree, deps: dict[str, Any], jobs: list[str],
               diff: list[str]) -> dict[str, list[str]]:
    """{job: ["file::unit", ...]}: for each of `jobs`, the changed functions of modules it loaded whose name the
    code in its key mentions. Derived from git objects and the job's module list only, never from which functions
    the profiler saw: a job named here ran, by its record, none of the changed code, yet refers to it by name, so
    it is rendered and compared instead of trusted."""
    changed: dict[str, dict[str, set[str]]] = {}
    for f in diff:
        if not f.endswith(".py"):
            continue
        a, b = tp.module(f), tn.module(f)
        ua, ub = (a.units if a else {}), (b.units if b else {})
        units = {u for u in set(ua) | set(ub) if ua.get(u) != ub.get(u)}
        if units:
            changed[f.lower()] = {u: _tokens(u) for u in units}
    out: dict[str, list[str]] = {}
    if not changed:
        return out
    for j in jobs:
        rec = (deps.get("jobs") or {}).get(j) or {}
        loaded = {m.lower(): m for m in rec.get("modules") or ()}
        hit = [f for f in changed if f in loaded]
        if not hit:
            continue
        names = board_deps.kept_names(tn, rec)
        found = sorted(f"{loaded[f]}::{u}" for f in hit for u, toks in changed[f].items() if toks & names)
        if found:
            out[j] = found
    return out


def closure_touched(deps: dict[str, Any], jobs: list[str], diff: list[str]) -> dict[str, list[str]]:
    """{job: [changed paths]}: for each of `jobs`, the changed paths that are modules its process loaded. A reused
    job named here loaded a changed module and is reused on the record that it ran no function of it; a reused job
    not named here has no changed path anywhere in its closure (nothing to test)."""
    low = {n.lower(): n for n in diff if n.endswith(".py")}
    out = {}
    for j in jobs:
        rec = (deps.get("jobs") or {}).get(j) or {}
        hit = sorted(low[m.lower()] for m in rec.get("modules") or () if m.lower() in low)
        if hit:
            out[j] = hit
    return out


def power(n_reused: int, n_touched: int, n_net: int, n_random: int) -> dict[str, Any]:
    """What the run-time comparison can and cannot catch, for REUSE.json and the gate. `n_touched`: the reusable
    jobs whose closure the diff touches (the static net's jobs are among them)."""
    rest = max(0, n_touched - n_net)
    p = min(1.0, n_random / rest) if rest else 1.0
    return {"reused_jobs": n_reused, "closure_untouched_by_the_diff": n_reused - n_touched,
            "closure_touched": n_touched, "rendered_for_the_static_net": n_net,
            "rendered_at_random": min(n_random, rest), "left_on_the_record": max(0, rest - n_random),
            "chance_a_given_stale_job_outside_the_net_is_rendered": round(p, 4),
            "statement": f"{n_reused - n_touched} reused job(s): no changed path is a module they loaded or a path "
                         "they read, probed or listed (the gate checks this by set membership on the diff, not by "
                         f"the key); nothing to test. {n_touched} loaded a changed module and are reused on the "
                         "record that they ran no function of it: a stale one passes only if (1) its process ran "
                         "changed code the profiler did not record, (2) no code in its key mentions the changed "
                         f"function's name (else the static net renders it: detection 1.0, {n_net} job(s)), and "
                         f"(3) it is not in the random sample drawn from these jobs (detection {round(p, 4)} per "
                         f"stale job: {min(n_random, rest)} of {rest}; --reuse-sample -1 renders them all)."}


# ------------------------------------------------------------------------------------------------ the plan
def _load_prev(prev_dir: Path) -> tuple[dict, dict, dict]:
    try:
        pb = json.loads((prev_dir / "board.json").read_text(encoding="utf-8"))
        ps = json.loads((prev_dir / "surfaces.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise NoReuse(f"previous board unreadable: {e}") from e
    deps_file = prev_dir / DEPS_NAME
    if not deps_file.is_file():
        raise NoReuse(f"previous board has no {DEPS_NAME} (rendered without the record)")
    deps = json.loads(deps_file.read_text(encoding="utf-8"))
    if deps.get("schema") != board_deps.SCHEMA:
        raise NoReuse(f"previous board's {DEPS_NAME} is {deps.get('schema')!r}, not {board_deps.SCHEMA}: its "
                      "record does not cover every input (render it again with this tooling)")
    return pb, ps, deps


def plan(tree: Path, prev_dir: Path, palette: str, stamp: dict[str, Any], env: dict[str, Any], offered: list[str],
         sample: int = 2, seed: int | None = None) -> dict[str, Any]:
    """Which of `offered` (the jobs render_surfaces.py would run now) to reuse from `prev_dir`. Raises NoReuse with
    the reason when nothing can be."""
    prev_dir = Path(prev_dir)
    pb, ps, deps = _load_prev(prev_dir)
    if pb.get("brand") != palette or ps.get("brand") != palette:
        raise NoReuse(f"previous board is palette {pb.get('brand')!r}, this one {palette!r}")
    ptree = pb.get("tree") or {}
    prev_head, new_head = ptree.get("head"), stamp.get("head")
    if not prev_head or ptree.get("dirty_vs_head") is not False:
        raise NoReuse("previous board's tree was not a clean commit")
    if not new_head or stamp.get("dirty_vs_head") is not False:
        raise NoReuse(f"this worktree differs from its HEAD ({(stamp.get('dirty_paths') or ['?'])[:3]})")
    if "copy_set" in ps:
        raise NoReuse("previous board drew a copy set")
    if not (prev_dir / "MANIFEST.json").is_file():   # the gate proves each reused file against it
        raise NoReuse("previous board has no MANIFEST.json (run freeze_manifest.py on it first)")
    penv = pb.get("reuse_env")
    if not penv:
        raise NoReuse("previous board has no environment record (board.json reuse_env)")
    if penv.get("sha256") != env.get("sha256"):
        a, b = flat({k: penv.get(k) for k in ("toolchain", "tool_data")}), flat(
            {k: env.get(k) for k in ("toolchain", "tool_data")})
        diff = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
        raise NoReuse(f"toolchain or tool data differ: {diff[:6]}")
    prev_jobs = set(ps.get("seconds") or {})
    if set(offered) != prev_jobs:
        raise NoReuse(f"the job list changed: new {sorted(set(offered) - prev_jobs)[:4]}, "
                      f"gone {sorted(prev_jobs - set(offered))[:4]}")
    stray = unmapped(ps)
    if stray:
        raise NoReuse(f"previous surfaces.json entries no job owns: {stray[:6]}")
    took: dict[str, float] = {}
    t0 = time.perf_counter()
    try:
        tp, tn = board_deps.Tree(tree, prev_head), board_deps.Tree(tree, new_head)
    except subprocess.CalledProcessError as e:
        raise NoReuse(f"the previous board's commit {prev_head[:10]} is not in this repository") from e
    diff = diff_names(tree, prev_head, new_head)
    disk = Disk(tree)
    before = ptree.get("untracked") or {}
    errors = ps.get("errors") or {}
    jobs: dict[str, dict[str, Any]] = {}
    for j in offered:
        rec = (deps.get("jobs") or {}).get(j)
        if rec is None:
            jobs[j] = {"status": "rendered", "why": "no record in the previous board"}
            continue
        if any(n == j or job_of(n) == j for n in errors):
            jobs[j] = {"status": "rendered", "why": "raised in the previous board"}
            continue
        if rec.get("never"):
            jobs[j] = {"status": "rendered", "why": "its inputs could not all be recorded", "changed": rec["never"][:8]}
            continue
        kp, kn = board_deps.job_key(tp, rec), board_deps.job_key(tn, rec)
        row = {"key_prev": kp["sha256"], "key": kn["sha256"], "functions": len(rec.get("functions") or ()),
               "modules": len(rec.get("modules") or ()), "whole": rec.get("whole") or []}
        if kp["sha256"] != kn["sha256"]:
            ch = board_deps.changed_rows(kp, kn)
            jobs[j] = {"status": "rendered", "why": "inputs in git changed", "changed": ch[:40],
                       "changed_count": len(ch), **row}
            continue
        bad = inputs_now(rec, disk, tn, before)
        if bad:
            jobs[j] = {"status": "rendered", "why": "inputs outside git changed", "changed": bad[:40],
                       "changed_count": len(bad), **row}
            continue
        jobs[j] = {"status": "reused", **row}
    took["keys_and_inputs_s"] = round(time.perf_counter() - t0, 1)
    reusable = sorted(j for j, r in jobs.items() if r["status"] == "reused")
    t0 = time.perf_counter()
    net = static_net(tp, tn, deps, reusable, diff)
    took["static_net_s"] = round(time.perf_counter() - t0, 1)
    for j, units in net.items():
        jobs[j].update(status="sampled", why="static net: the code in its key names changed functions it did not run",
                       net=units[:20])
    touched = closure_touched(deps, reusable, diff)
    rest = [j for j in reusable if j in touched and j not in net]   # reused on the record: the sample is drawn here
    seed = int.from_bytes(os.urandom(4), "big") if seed is None else seed
    picked = sorted(random.Random(seed).sample(rest, len(rest) if sample < 0 else min(sample, len(rest))))
    for j in picked:
        jobs[j].update(status="sampled", why="random sample of the jobs that loaded a changed module",
                       touched=touched[j][:20])
    return {"schema": SCHEMA, "prev": {"dir": str(prev_dir), "head": prev_head,
                                       "deps_sha256": sha256_file(prev_dir / DEPS_NAME),
                                       "manifest_sha256": sha256_file(prev_dir / "MANIFEST.json")},
            "head": new_head, "palette": palette, "env_sha256": env["sha256"],
            "checks": {"diff_paths": len(diff), "jobs_offered": len(offered),
                       "jobs_without_record": sorted(prev_jobs - set(deps.get("jobs") or {}))},
            "sample": {"seed": seed, "asked": sample, "jobs": sorted(set(picked) | set(net)), "random": picked,
                       "static_net": sorted(net), "closure_touched": sorted(touched),
                       "power": power(len(reusable), len(touched), len(net), len(picked))},
            "jobs": jobs, "plan_seconds": took}


def to_render(p: dict[str, Any]) -> list[str]:
    return [j for j, r in p["jobs"].items() if r["status"] != "reused"]


# ------------------------------------------------------------------------------------------------ carrying it out
def finish(p: dict[str, Any], out: Path, fresh: dict[str, Any] | None, fresh_deps: dict[str, Any] | None
           ) -> dict[str, Any]:
    """After the non-reused jobs rendered into <out>/surfaces (`fresh`: their surfaces.json record, None when no job
    rendered): check the sample against the previous board, copy the reused jobs' files (mtime kept: the file is
    as old as its render), and return the merged surfaces.json record. Writes REUSE.json and DEPS.json.
    Raises SystemExit(3) on a sample mismatch."""
    out, prev_dir = Path(out), Path(p["prev"]["dir"])
    ps = json.loads((prev_dir / "surfaces.json").read_text(encoding="utf-8"))
    pdeps = json.loads((prev_dir / DEPS_NAME).read_text(encoding="utf-8"))
    fresh = fresh or {"surfaces": {}, "errors": {}, "seconds": {}}
    sampled = {j for j, r in p["jobs"].items() if r["status"] == "sampled"}
    reused = {j for j, r in p["jobs"].items() if r["status"] == "reused"}
    # the sample: every record of a sampled job and every kept file equal to the previous board's
    mismatches = []
    want, got = split_record(ps, sampled), split_record(fresh, sampled)
    for k in BY_SURFACE + ("omissions", "errors"):
        a, b = want.get(k) or {}, got.get(k) or {}
        mismatches += [f"{k}:{n}" for n in sorted(set(a) | set(b)) if a.get(n) != b.get(n)]
    checked = 0
    for n in sorted(want.get("surfaces") or {}):
        a, b = prev_dir / "surfaces" / f"{n}.png", out / "surfaces" / f"{n}.png"
        if a.is_file() != b.is_file() or (a.is_file() and sha256_file(a) != sha256_file(b)):
            mismatches.append(f"file:{n}.png")
        checked += a.is_file()
    p["sample"].update(files_checked=checked, surfaces=len(want.get("surfaces") or {}), mismatches=mismatches,
                       passed=not mismatches)
    if mismatches:
        (out / ABORT_NAME).write_text(json.dumps(p, indent=1), encoding="utf-8", newline="\n")
        print(f"board: REUSE ABORTED: {len(mismatches)} sampled record(s) differ from the previous board "
              f"({mismatches[:5]}): a full render is required ({out / ABORT_NAME})", file=sys.stderr, flush=True)
        raise SystemExit(3)
    files: dict[str, dict[str, Any]] = {}
    carried = split_record(ps, reused)
    (out / "surfaces").mkdir(parents=True, exist_ok=True)
    for n in sorted(carried.get("surfaces") or {}):
        src = prev_dir / "surfaces" / f"{n}.png"
        if src.is_file():
            dst = out / "surfaces" / f"{n}.png"
            shutil.copy2(src, dst)
            files[f"surfaces/{n}.png"] = {"status": "reused", "job": job_of(n), "sha256": sha256_file(dst)}
    for n in sorted((split_record(fresh, set(p["jobs"])).get("surfaces")) or {}):
        if (out / "surfaces" / f"{n}.png").is_file():
            files[f"surfaces/{n}.png"] = {"status": p["jobs"][job_of(n)]["status"], "job": job_of(n)}
    merged: dict[str, Any] = {}
    for part in (carried, split_record(fresh, set(p["jobs"]))):
        for k, v in part.items():
            dup = set(merged.setdefault(k, {})) & set(v)
            if dup:
                raise RuntimeError(f"board reuse: {k} {sorted(dup)[:4]} both reused and rendered")
            merged[k].update(v)
    if "seconds" in carried:   # a reused job's seconds are its previous render's, said apart
        merged["reused_seconds"] = {j: s for j, s in carried["seconds"].items()}
    for k in ("brand", "graphic_runs", "brand_validation"):   # equal by the plan's checks (harness code is in every key)
        if k in fresh and k in ps and fresh[k] != ps[k]:
            raise RuntimeError(f"board reuse: {k} differs from the previous board's")
        if k in fresh or k in ps:
            merged[k] = fresh.get(k, ps.get(k))
    merged["input_files"] = {**(ps.get("input_files") or {}), **(fresh.get("input_files") or {})}
    merged["count"] = len(merged.get("surfaces") or {})
    deps = {"schema": board_deps.SCHEMA, "jobs": {}}
    for j in p["jobs"]:
        src = (fresh_deps or {}).get("jobs", {}) if j not in reused else pdeps["jobs"]
        if j in src:
            deps["jobs"][j] = src[j]
    (out / DEPS_NAME).write_text(json.dumps(deps, indent=0, sort_keys=True), encoding="utf-8", newline="\n")
    counts = {s: sum(1 for r in p["jobs"].values() if r["status"] == s) for s in ("reused", "sampled", "rendered")}
    p.update(files=files, counts={**counts, "files_reused": sum(1 for f in files.values() if f["status"] == "reused"),
                                  "seconds_reused": round(sum((carried.get("seconds") or {}).values()), 1)})
    (out / REUSE_NAME).write_text(json.dumps(p, indent=1), encoding="utf-8", newline="\n")
    return merged


def none(out: Path, why: str, palette: str, prev_dir: Path | None) -> dict[str, Any]:
    """REUSE.json of a board that reused nothing, with the reason."""
    rec = {"schema": SCHEMA, "prev": {"dir": str(prev_dir) if prev_dir else None}, "palette": palette,
           "no_reuse": why, "jobs": {}, "files": {}, "counts": {"reused": 0, "files_reused": 0}}
    (Path(out) / REUSE_NAME).write_text(json.dumps(rec, indent=1), encoding="utf-8", newline="\n")
    return rec


def composed_inputs(board: Path) -> dict[str, str]:
    """What the composed rows of a board are drawn from, as hashes: every kept surface file and every drawn key of
    surfaces.json (the keys that say how the board was made, NOT_DRAWN, left out). Equal for an incremental board
    and a full render of the same commit means rows 1-13 and the sheets are drawn by the same code from the same
    bytes (tests: the rows read nothing else of the reuse)."""
    board = Path(board)
    rec = json.loads((board / "surfaces.json").read_text(encoding="utf-8"))
    out = {f"surfaces.json:{k}": hashlib.sha256(json.dumps(v, sort_keys=True).encode()).hexdigest()
           for k, v in sorted(rec.items()) if k not in NOT_DRAWN}
    for p in sorted((board / "surfaces").glob("*")):
        if p.is_file():
            out[f"surfaces/{p.name}"] = sha256_file(p)
    return out


# ------------------------------------------------------------------------------------------------ the gate's check
def membership(rec: dict[str, Any], diff: list[str], tp: board_deps.Tree, tn: board_deps.Tree) -> list[str]:
    """The changed paths that are among a reused job's inputs, by set membership on the diff (no key is derived):
    the gate's own reading of "nothing this job can depend on changed"."""
    ran = {f.partition("::")[0].lower() for f in rec.get("functions") or ()}
    units: dict[str, set[str]] = {}
    for f in rec.get("functions") or ():
        a, _, b = f.partition("::")
        units.setdefault(a.lower(), set()).add(b)
    loaded = {m.lower() for m in rec.get("modules") or ()} | ran
    reads = {r.lower() for r in rec.get("tree_reads") or {}}
    probes = {r.strip("/").lower() for r in rec.get("tree_probes") or {}}
    listed = {r.strip("/").lower() for r in rec.get("tree_listed") or {}}
    asked = set(rec.get("imports") or ())
    syspath = rec.get("syspath") or [""]
    split = {k.lower() for k in board_deps.SPLIT}
    whole = bool(rec.get("whole"))
    bad = []
    for n in diff:
        low = n.lower()
        kind = board_deps.path_class(n)
        if kind == "global":
            bad.append(f"tracked data changed: {n}")
            continue
        if low in reads or low in probes or os.path.dirname(low) in listed \
                or any(low.startswith(p + "/") for p in probes if (p in tp.dirs()) != (p in tn.dirs())):
            bad.append(f"a path the job read, probed or listed changed: {n}")
        if kind != "code":
            continue
        a, b = tp.module(n), tn.module(n)
        if whole and any(n.startswith(r + "/") for r in board_deps.CODE_ROOTS):
            bad.append(f"code changed and the job is keyed on the whole code: {n}")
        elif low in ran and low not in split:
            bad.append(f"a module the job ran code of changed: {n}")
        elif low in ran:   # the harness: the units this job ran, and its module-level code
            if a is None or b is None or a.skeleton != b.skeleton \
                    or any(a.units.get(u) != b.units.get(u) for u in units.get(low, ())):
                bad.append(f"the harness code the job ran changed: {n}")
        elif low in loaded:
            if a is None or b is None or a.skeleton != b.skeleton:
                bad.append(f"module-level code of a module the job loaded changed: {n}")
        elif (a is None) != (b is None) and board_deps.module_names(n, syspath) & asked:
            bad.append(f"a module an import of the job could resolve to came or went: {n}")
    return bad


def verify(render_dir: Path, repo: Path, commit: str) -> tuple[set[str], list[str]]:
    """freeze_gate.py's check of a board with reused files: (the reused paths that are proven, the refusal reasons).
    Independent of the plan's keys (see the module docstring): the previous board must still be there and be its
    commit's; each reused file must be the byte copy of the previous board's file as that board's MANIFEST.json
    lists it; the diff between the two commits, read here, must not touch any input of a reused job (membership);
    every job the static net names must have been rendered; every sampled job's files, rendered at `commit`, must
    hash to the previous board's MANIFEST.json entries (a real render against the reuse claim); the inputs outside
    git are checked on the disk again; and the keys, derived again, must agree."""
    render_dir, repo = Path(render_dir), Path(repo)
    why: list[str] = []
    try:
        rec = json.loads((render_dir / REUSE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return set(), [f"{REUSE_NAME} unreadable: {e}"]
    reused_files = {f: r for f, r in (rec.get("files") or {}).items() if r.get("status") == "reused"}
    if not reused_files:
        return set(), []
    prev_dir = Path(rec["prev"]["dir"])
    try:
        full = _git(repo, "rev-parse", "--verify", f"{commit}^{{commit}}").strip()
        pb, _ps, deps = _load_prev(prev_dir)
        nb = json.loads((render_dir / "board.json").read_text(encoding="utf-8"))
        pm = json.loads((prev_dir / "MANIFEST.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, NoReuse) as e:
        return set(), [f"previous board {prev_dir} cannot be checked: {e}"]
    prev_head = (pb.get("tree") or {}).get("head")
    if rec.get("head") != full:
        why.append(f"{REUSE_NAME} is for {str(rec.get('head'))[:10]}, the gate's commit is {full[:10]}")
    if rec["prev"].get("head") != prev_head or (pb.get("tree") or {}).get("dirty_vs_head") is not False:
        why.append("the previous board is not the clean commit REUSE.json names")
    if (pm.get("repo") or {}).get("head") != prev_head or (pm.get("repo") or {}).get("dirty_vs_head"):
        why.append("the previous board's MANIFEST.json is not its commit's")
    if sha256_file(prev_dir / DEPS_NAME) != rec["prev"].get("deps_sha256"):
        why.append(f"the previous board's {DEPS_NAME} changed since the reuse")
    if pb.get("brand") != rec.get("palette") or nb.get("brand") != rec.get("palette"):
        why.append("palettes differ")
    if (pb.get("reuse_env") or {}).get("sha256") != (nb.get("reuse_env") or {}).get("sha256"):
        why.append("toolchain or tool data differ from the previous board's")
    if (nb.get("tree") or {}).get("dirty_vs_head") is not False:
        why.append("the board's worktree differed from its HEAD")
    if why:
        return set(), why
    try:
        tp, tn = board_deps.Tree(repo, prev_head), board_deps.Tree(repo, full)
        diff = diff_names(repo, prev_head, full)
    except (NoReuse, subprocess.CalledProcessError) as e:
        return set(), [f"the two commits cannot be read: {e}"]
    listed = {e["path"]: e for e in pm.get("images") or []}
    status = {j: (r or {}).get("status") for j, r in (rec.get("jobs") or {}).items()}
    reused_jobs = sorted({r.get("job") for r in reused_files.values()})
    disk = Disk(repo)
    before = (pb.get("tree") or {}).get("untracked") or {}
    bad_jobs: dict[str, str] = {}
    for j in reused_jobs:
        r = (deps.get("jobs") or {}).get(j)
        if r is None:
            bad_jobs[j] = "no record in the previous board"
        elif status.get(j) != "reused":
            bad_jobs[j] = "its job is not recorded as reused"
        elif r.get("never"):
            bad_jobs[j] = f"its inputs could not all be recorded: {r['never'][:2]}"
        else:
            hit = membership(r, diff, tp, tn)
            if hit:
                bad_jobs[j] = f"the diff touches its inputs: {hit[:3]}"
                continue
            now = inputs_now(r, disk, tn, before)
            if now:
                bad_jobs[j] = f"inputs outside git changed: {now[:3]}"
            elif board_deps.job_key(tp, r)["sha256"] != board_deps.job_key(tn, r)["sha256"]:
                bad_jobs[j] = "its key differs between the commits"
    net = static_net(tp, tn, deps, [j for j in reused_jobs if j not in bad_jobs], diff)
    for j, units in net.items():
        bad_jobs[j] = f"the static net names it and it was not rendered: {units[:3]}"
    # the sample, by content hash: files rendered at this commit against the previous board's manifest
    sampled = sorted(j for j, s in status.items() if s == "sampled")
    compared = 0
    for f, r in sorted((rec.get("files") or {}).items()):
        if r.get("status") != "sampled":
            continue
        got = sha256_file(render_dir / f) if (render_dir / f).is_file() else None
        if f not in listed or got != listed[f]["sha256"]:
            why.append(f"sample refuted: {f}, rendered at {full[:10]}, is not the previous board's bytes")
        compared += 1
    kept = sorted(j for j, s in status.items() if s in ("reused", "sampled"))   # the power, derived here again
    net_all = static_net(tp, tn, deps, kept, diff)
    want = power(len(kept), len(closure_touched(deps, kept, diff)), len(net_all),
                 sum(1 for j in sampled if j not in net_all))
    if sampled and not compared:
        why.append("the sample's jobs kept no file to compare")
    if ((rec.get("sample") or {}).get("power") or {}).get("left_on_the_record") != want["left_on_the_record"]:
        why.append(f"REUSE.json's stated detection power is not what its jobs give (left on the record: "
                   f"{want['left_on_the_record']})")
    proven: set[str] = set()
    for f, r in sorted(reused_files.items()):
        job = r.get("job")
        src = prev_dir / f
        if job in bad_jobs:
            why.append(f"reused file unproven: {f} (job {job!r}: {bad_jobs[job]})")
        elif f not in listed or not src.is_file() or sha256_file(src) != listed[f]["sha256"]:
            why.append(f"reused file unproven: {f} (the previous board's file is gone or not its manifest's)")
        elif not (render_dir / f).is_file() or sha256_file(render_dir / f) != listed[f]["sha256"]:
            why.append(f"reused file unproven: {f} (not the byte copy of the previous board's)")
        else:
            proven.add(f)
    return proven, why
