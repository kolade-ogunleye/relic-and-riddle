"""The incremental board (cycle 5, lane BOARD): render only the render_surfaces.py jobs whose inputs changed since a
previous board of the same palette, and reuse the rest byte for byte.

    board.py <tree> <dev> <out> --reuse <previous board dir> [--reuse-sample 2] [--reuse-seed N] ...

What a board file's input hash covered before this cycle (board_inputs.input_hashes): its path, the palette, the
whole tree stamp (HEAD and the drawn paths' tree ids) and the union of every data file the board read. Every file of
a board had the same inputs, and any commit changed all of them, so nothing could be reused; and it left out the
toolchain and the tree's untracked data (tools/geo, tools/models).

What a job's inputs are here (all must be equal for a reuse):
  code      the job's code key (board_deps.py): the functions that ran for it in the previous board, the values and
            class headers they name, every module's import-time residue; at the previous commit and at this one,
            both from git objects;
  not code  any of these changed since the previous board changes EVERY job (no reuse at all):
            - a file that is not Python under src/, tests/, fonts/, assets/, the fixtures catalogue, uv.lock or
              pyproject.toml (git diff previous..this);
            - a data file the previous board read (its board.json "inputs", re-hashed now; a frame of fixtures.frame's
              cache that is gone is re-made from its recorded source, so only a different one counts);
            - the tree's untracked data: tools/geo, tools/models, tools/stress (sha256 of every file);
            - the toolchain: Python, Pillow and its text features, numpy, the ffmpeg binary and its build line;
            - the palette; the job list render_surfaces.py offers (a new dev run changes the picks);
  itself    a job that raised in the previous board is rendered again.
A worktree that differs from its HEAD, a previous board without DEPS.json / its environment record / MANIFEST.json,
or a previous board of another palette: no reuse (a full render, said in REUSE.json).

Proof at run time: --reuse-sample N (default 2) reused jobs, picked at random (seed recorded), are rendered anyway
and must equal the previous board's files and records byte for byte; a difference aborts (exit 3, REUSE-ABORTED.json).

Record: <out>/REUSE.json (the checks, each job's status and why, each reused file and its sha256), <out>/DEPS.json
(reused jobs keep their recorded functions), board.json "reuse". freeze_manifest.py marks each file's provenance
from REUSE.json and freeze_gate.py calls verify() (the tools patch of this cycle).

The composed rows (1, 2, 12, 13 and every sheet) are drawn fresh from the merged surfaces: only render_surfaces.py's
jobs are reused in this version.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import board_deps

SCHEMA = "board-reuse/1"
REUSE_NAME, DEPS_NAME, ABORT_NAME = "REUSE.json", "DEPS.json", "REUSE-ABORTED.json"
FIX = board_deps.FIX
# a changed file under these that is not Python changes every job
GLOBAL_PATHS = ("src", "tests", "fonts", "assets", FIX, "uv.lock", "pyproject.toml")
RECORDED_PATHS = ("tests", FIX)   # board_inputs.TREE_DATA: a data file here counts only when a render read it
TOOL_DATA = ("tools/geo", "tools/models", "tools/stress")
# surfaces.json keys by what names their entries (board.py merge_shards classifies the same keys)
BY_JOB = ("seconds", "omissions")
BY_SURFACE = ("surfaces", "story_title_px", "retired", "rerouted", "bridge_ladder", "wide_grounds",
              "surface_omissions", "masthead_records")
BY_EITHER = ("errors",)


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
    """What draws and encodes besides the tree: named as freeze_manifest.py's fingerprint names them."""
    import PIL
    from PIL import features
    out: dict[str, Any] = {"python": sys.version.split()[0], "pillow": PIL.__version__}
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
    exe = shutil.which("ffmpeg")
    if exe:
        v = subprocess.run([exe, "-version"], capture_output=True, text=True).stdout
        out["ffmpeg"] = {"version_output_sha256": hashlib.sha256(v.encode()).hexdigest(),
                         "binary_sha256": sha256_file(Path(exe))}
    else:
        out["ffmpeg"] = None
    return out


def tool_data(tree: Path) -> dict[str, str]:
    """{relative path: sha256} of the tree's untracked render data (copied into each worktree, never linked)."""
    out = {}
    for top in TOOL_DATA:
        d = tree / top
        if d.is_dir():
            for p in sorted(x for x in d.rglob("*") if x.is_file() and "__pycache__" not in x.parts):
                out[p.relative_to(tree).as_posix()] = sha256_file(p)
    return out


def environment(tree: Path) -> dict[str, Any]:
    """board.json "reuse_env": the not-code inputs that are not in git or in the inputs record."""
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


def global_diff(tree: Path, prev: str, new: str, read: set[str]) -> list[str]:
    """The changed files prev..new that are not Python under the paths every job can read. Under RECORDED_PATHS
    (data whose reads the previous board recorded: `read`, tree-relative) only a file a render read counts."""
    names = _git(tree, "diff", "--name-only", f"{prev}..{new}", "--", *GLOBAL_PATHS).splitlines()
    return [n for n in names if n and not n.endswith(".py")
            and (os.path.normcase(n).replace(os.sep, "/") in read
                 or not any(n.startswith(p + "/") for p in RECORDED_PATHS))]


def tree_reads(pb: dict[str, Any]) -> tuple[set[str], str]:
    """(the tree-relative data files the previous board read, its tree's path as recorded) from its board.json."""
    root = str((pb.get("tree") or {}).get("path") or "")
    if not root:
        raise NoReuse("previous board does not say where its tree was (board.json tree.path)")
    base = os.path.normcase(os.path.abspath(root)) + os.sep
    out = set()
    for p in (pb.get("inputs") or {}).get("files") or {}:
        q = os.path.normcase(os.path.abspath(p))
        if q.startswith(base):
            out.add(q[len(base):].replace(os.sep, "/"))
    return out, base


def changed_inputs(files: dict[str, str], frame_cache: str, tree_base: str) -> list[str]:
    """The recorded data inputs whose content is no longer the recorded one. A file under the previous tree
    (`tree_base`) is tracked data: git diff between the two commits answers for it."""
    bad = []
    cache = os.path.normcase(os.path.abspath(frame_cache))
    for p, sha in sorted(files.items()):
        if os.path.normcase(os.path.abspath(p)).startswith(tree_base):
            continue
        if not os.path.isfile(p):
            if not os.path.normcase(os.path.abspath(p)).startswith(cache):
                bad.append(f"{p}: gone")
            continue
        if sha256_file(Path(p)) != sha:
            bad.append(f"{p}: changed")
    return bad


# ------------------------------------------------------------------------------------------------ the plan
def code_keys(tree: Path, deps: dict[str, Any], prev_head: str, new_head: str) -> dict[str, dict[str, Any]]:
    """{job: {"prev", "new" (key sha256), "changed" (symbols that differ), sizes}} from git objects of both commits."""
    up = board_deps.universe(tree, prev_head, deps)
    un = up if prev_head == new_head else board_deps.universe(tree, new_head, deps)
    out = {}
    for job, fns in deps["jobs"].items():
        kp = up.key(fns)
        kn = kp if un is up else un.key(fns)
        out[job] = {"prev": kp["sha256"], "new": kn["sha256"], "functions": kp["functions"], "symbols": kp["symbols"],
                    "files": kp["files"],
                    "changed": [] if kp["sha256"] == kn["sha256"] else board_deps.changed_rows(kp, kn)}
    return out


def plan(tree: Path, prev_dir: Path, palette: str, stamp: dict[str, Any], env: dict[str, Any], offered: list[str],
         frame_cache: str, sample: int = 2, seed: int | None = None) -> dict[str, Any]:
    """Which of `offered` (the jobs render_surfaces.py would run now) to reuse from `prev_dir`. Raises NoReuse with
    the reason when nothing can be."""
    prev_dir = Path(prev_dir)
    try:
        pb = json.loads((prev_dir / "board.json").read_text(encoding="utf-8"))
        ps = json.loads((prev_dir / "surfaces.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise NoReuse(f"previous board unreadable: {e}") from e
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
    deps_file = prev_dir / DEPS_NAME
    if not deps_file.is_file():
        raise NoReuse(f"previous board has no {DEPS_NAME} (rendered without the code record)")
    deps = json.loads(deps_file.read_text(encoding="utf-8"))
    penv = pb.get("reuse_env")
    if not penv:
        raise NoReuse("previous board has no environment record (board.json reuse_env)")
    if penv.get("sha256") != env.get("sha256"):
        a, b = flat({k: penv.get(k) for k in ("toolchain", "tool_data")}), flat(
            {k: env.get(k) for k in ("toolchain", "tool_data")})
        diff = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
        raise NoReuse(f"toolchain or tool data differ: {diff[:6]}")
    read, tree_base = tree_reads(pb)
    glob = global_diff(tree, prev_head, new_head, read)
    if glob:
        raise NoReuse(f"non-Python inputs changed {prev_head[:10]}..{new_head[:10]}: {glob[:6]}")
    bad = changed_inputs((pb.get("inputs") or {}).get("files") or {}, frame_cache, tree_base)
    if bad:
        raise NoReuse(f"data inputs changed since the previous board: {bad[:6]}")
    prev_jobs = set(ps.get("seconds") or {})
    if set(offered) != prev_jobs:
        raise NoReuse(f"the job list changed: new {sorted(set(offered) - prev_jobs)[:4]}, "
                      f"gone {sorted(prev_jobs - set(offered))[:4]}")
    stray = unmapped(ps)
    if stray:
        raise NoReuse(f"previous surfaces.json entries no job owns: {stray[:6]}")
    missing = sorted(prev_jobs - set(deps.get("jobs") or {}))
    keys = code_keys(tree, deps, prev_head, new_head)
    errors = ps.get("errors") or {}
    jobs: dict[str, dict[str, Any]] = {}
    for j in offered:
        k = keys.get(j)
        if k is None:
            jobs[j] = {"status": "rendered", "why": "no code record in the previous board"}
        elif any(n == j or job_of(n) == j for n in errors):
            jobs[j] = {"status": "rendered", "why": "raised in the previous board", **_key(k)}
        elif k["prev"] != k["new"]:
            jobs[j] = {"status": "rendered", "why": "code changed", "changed": k["changed"][:40],
                       "changed_count": len(k["changed"]), **_key(k)}
        else:
            jobs[j] = {"status": "reused", **_key(k)}
    reusable = sorted(j for j, r in jobs.items() if r["status"] == "reused")
    seed = int.from_bytes(os.urandom(4), "big") if seed is None else seed
    picked = sorted(random.Random(seed).sample(reusable, min(max(0, sample), len(reusable))))
    for j in picked:
        jobs[j]["status"] = "sampled"
    return {"schema": SCHEMA, "prev": {"dir": str(prev_dir), "head": prev_head,
                                       "deps_sha256": sha256_file(deps_file),
                                       "manifest_sha256": sha256_file(prev_dir / "MANIFEST.json")
                                       if (prev_dir / "MANIFEST.json").is_file() else None},
            "head": new_head, "palette": palette, "env_sha256": env["sha256"],
            "checks": {"global_diff": [], "inputs_rehashed": len((pb.get("inputs") or {}).get("files") or {}),
                       "jobs_offered": len(offered), "jobs_without_code_record": missing},
            "sample": {"seed": seed, "asked": sample, "jobs": picked}, "jobs": jobs}


def _key(k: dict[str, Any]) -> dict[str, Any]:
    return {"key_prev": k["prev"], "key": k["new"], "functions": k["functions"], "symbols": k["symbols"]}


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


# ------------------------------------------------------------------------------------------------ the gate's check
def verify(render_dir: Path, repo: Path, commit: str) -> tuple[set[str], list[str]]:
    """freeze_gate.py's check of a board with reused files: (the reused paths that are proven, the refusal reasons).
    Nothing is taken from REUSE.json on trust but the list of what was reused and where from: the previous board must
    still be there and be its commit's; each reused file must be the byte copy of the previous board's file as that
    board's MANIFEST.json lists it; each reused job's code key is derived again from git objects at the previous
    commit and at `commit` from the previous board's DEPS.json and must be equal; no non-Python input changed between
    the two commits; the previous board's recorded data inputs still hash the same; the sample passed."""
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
        pb = json.loads((prev_dir / "board.json").read_text(encoding="utf-8"))
        nb = json.loads((render_dir / "board.json").read_text(encoding="utf-8"))
        pm = json.loads((prev_dir / "MANIFEST.json").read_text(encoding="utf-8"))
        deps = json.loads((prev_dir / DEPS_NAME).read_text(encoding="utf-8"))
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
    if not (rec.get("sample") or {}).get("passed"):
        why.append("the reuse sample did not pass")
    if why:
        return set(), why
    try:
        read, tree_base = tree_reads(pb)
        glob = global_diff(repo, prev_head, full, read)
        keys = code_keys(repo, deps, prev_head, full)
    except (NoReuse, subprocess.CalledProcessError) as e:
        return set(), [f"keys cannot be derived: {e}"]
    if glob:
        return set(), [f"non-Python inputs changed between the boards: {glob[:6]}"]
    import tempfile
    bad = changed_inputs((pb.get("inputs") or {}).get("files") or {},
                         str(Path(tempfile.gettempdir()) / "rr_fixture_frames"), tree_base)
    if bad:
        return set(), [f"data inputs changed since the previous board: {bad[:6]}"]
    listed = {e["path"]: e for e in pm.get("images") or []}
    proven: set[str] = set()
    for f, r in sorted(reused_files.items()):
        job = r.get("job")
        k = keys.get(job)
        src = prev_dir / f
        if k is None or k["prev"] != k["new"]:
            why.append(f"reused file unproven: {f} (job {job!r}: its code differs between the commits)")
        elif (rec["jobs"].get(job) or {}).get("status") != "reused":
            why.append(f"reused file unproven: {f} (its job is not recorded as reused)")
        elif f not in listed or not src.is_file() or sha256_file(src) != listed[f]["sha256"]:
            why.append(f"reused file unproven: {f} (the previous board's file is gone or not its manifest's)")
        elif not (render_dir / f).is_file() or sha256_file(render_dir / f) != listed[f]["sha256"]:
            why.append(f"reused file unproven: {f} (not the byte copy of the previous board's)")
        else:
            proven.add(f)
    return proven, why
