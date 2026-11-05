#!/usr/bin/env python3
"""Produce the eval-spec-v1 Amendment 1 unchanged-input committee replicate.

Operator tooling for the system under test, not the eval harness. The
script runs the committee stage once more, from inputs identical to the
frozen candidate snapshot, and seals the result as a separate read-only
SQLite file for the noise-floor experiment (Amendment 1 §A1). It never
evaluates, scores or interprets the verdicts. That stays with the harness
(moat/evals/noise_floor.py) and is a separate, Pete-authorized step.

Usage (one paid run, needs approval). Run it with the candidate's
interpreter, e.g. /Users/pete/moat-sprint-6.0/.venv/bin/python:

    python scripts/run_committee_replicate.py \\
        --source /private/path/moat-candidate-sprint6.0.db \\
        --out-dir /private/path/replicate/ \\
        --cap-usd 15

Add --preflight-only to run every check without making an API call. This
covers the runtime and code checks, building the working copy, clearing its
cache, and the offline input validation below. The working copy is then
deleted.

Guarantees:

- Inputs. The frozen source is opened only immutably and copied. The live
  data/moat.db is refused as a source, a working copy or an output, and is
  never opened. The source and out-dir must both lie outside this repository.
- Runtime equivalence (Amendment 1 §A1.6). Nothing here is chosen by the
  operator. The Python version must equal CANDIDATE_PYTHON. The installed
  distributions must equal the approved freeze exactly: no package missing,
  different or extra. The freeze file is pinned by path and SHA-256. The
  candidate code scope (CANDIDATE_CODE_SCOPE) must be byte-identical to
  CANDIDATE_COMMIT. The working tree must have no tracked changes and no
  untracked files. Every loaded module must come from the repository, this
  interpreter's site-packages or its standard library. Anything else is
  refused, e.g. a module reached through PYTHONPATH. A repository module must
  equal its HEAD blob, and one in the candidate scope must also equal its
  CANDIDATE_COMMIT blob, so an ignored or untracked file cannot shadow
  candidate code. A site-packages file must match its distribution's RECORD
  hash. So must every startup .pth file, because these run code without
  appearing as a loaded module. The per-user site directory must be
  disabled. uv's two shim files have no RECORD entry and are pinned by
  hash instead. The loaded-module check runs again after the stage. The
  standard library is trusted on the pinned Python version alone, as the
  interpreter's own trust anchor.
- Input validation before any call. After the cache is cleared, the working
  copy is read back immutably and compared with the source. For each stored
  ticker, the committee bundle key is recomputed in both files. It must
  equal the stored cache key, and the two recomputed keys must be equal. The
  stage cohort, upstream tables and schema must match the source.
- Fresh calls. The committee cache is a (ticker, cache_key) lookup across
  every run, and unchanged inputs would make every company a cache hit. So
  every committee_verdicts row is deleted from the disposable working copy,
  and only there. No upstream table is touched. The deleted row counts per
  run ID are recorded.
- One shot. There is no resume and no retry: a resumed run would turn
  finished rows into cache hits. Any failure leaves the attempt unusable.
- Ownership. Each attempt works in its own directory under work/. The
  directory is created with os.mkdir, which fails if it exists, so SQLite
  sidecars beside the working copy can only be the attempt's own. Every file
  the runner writes is acquired with O_CREAT|O_EXCL or os.link, so none can
  overwrite an existing file. That covers the working
  copy, the stage log, the sealed database, the provenance record and the
  operator log. Each owned file is tracked from creation. If the attempt
  fails, every owned file is marked .UNUSABLE, including any already moved
  to sealed/. Marking uses os.link plus os.unlink, which never overwrites, so
  a stale .UNUSABLE file is left alone. Sealed names are checked before the
  paid call as well as at creation.
- Validation before sealing. The checks reuse the harness's own read-only
  adapter (moat.evals.database.ImmutableDatabase), its bundle recomputation
  and its structural-validity rule, rather than re-implementing them.

Exit codes:

- 0: sealed, or preflight passed.
- 2: unusable.
- 3: unusable, and at least one owned file could not be marked or the
  failure log could not be written. The log names the file.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import dataclasses
import hashlib
import importlib
import io
import json
import os
import platform
import re
import secrets
import site
import sqlite3
import subprocess
import sys
import sysconfig
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from moat import config  # noqa: E402
from moat.analysis.pricing import DEFAULT_MODEL  # noqa: E402
from moat.committee.prompt import PERSONAS, PROTOCOL_VERSION  # noqa: E402
from moat.db.connection import DB_PATH, get_connection  # noqa: E402
from moat.evals import constants as eval_constants  # noqa: E402
from moat.evals.benchmark import canonical_ticker  # noqa: E402
from moat.evals.database import ImmutableDatabase  # noqa: E402
from moat.evals.noise_floor import committee_output_is_valid  # noqa: E402

import run_pipeline as rp  # noqa: E402

PROFILE = "candidate-v1"
SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
EXCLUDED_FROM_UPSTREAM = ("committee_verdicts", "pipeline_runs")

# Runtime equivalence pins (docs/evals/sprint-6.0-run-provenance.md). The
# authoritative candidate committee run 20260930T133528Z ran at this commit
# with /Users/pete/moat-sprint-6.0/.venv (Python 3.11.16, anthropic 1.9.0),
# whose distributions equal this freeze exactly.
CANDIDATE_COMMIT = "79450dd9297b46fee00f8e5ac3681f97bb9dc690"
CANDIDATE_PYTHON = "3.11.16"
APPROVED_FREEZE = REPO_ROOT / "docs" / "evals" / "sprint-6.0-env-py311.txt"
APPROVED_FREEZE_SHA256 = "8f44ed7ca9fc38ddda88210303d057cf3bcf0964926e845d6366f80908579929"
# Git pathspecs that must be byte-identical to CANDIDATE_COMMIT. This is all
# production code. Two paths are left out: moat/evals is the eval harness,
# which postdates the candidate, and moat/dashboard is the Streamlit UI,
# which the pipeline never imports (it changed in 221d8f0 for pandas 3). The loaded-module
# check covers both if either is ever imported.
CANDIDATE_CODE_SCOPE = ("moat", "scripts/run_pipeline.py", "requirements.txt",
                        ":(exclude)moat/evals", ":(exclude)moat/dashboard")
# Repository files that may differ from CANDIDATE_COMMIT when loaded. They
# must still equal HEAD.
NON_CANDIDATE_CODE = ("moat/evals/", "scripts/run_committee_replicate.py")
# uv's virtualenv shim: the module and the .pth file that imports it at
# startup. They are the only site-packages files no distribution's RECORD
# lists, so each is pinned by SHA-256. Same content in the candidate and
# shared venvs.
VENV_SHIM = "_virtualenv.py"
VENV_SHIM_SHA256 = "cfb3db86aaa53bb62b5ff764970bec2d71c9228590a0ebec57f6ec926cc0bf1a"
VENV_SHIM_PTH = "_virtualenv.pth"
VENV_SHIM_PTH_SHA256 = "69ac3d8f27e679c81b94ab30b3b56e9cd138219b1ba94a1fa3606d5a76a1433d"
PINNED_UNRECORDED = {VENV_SHIM: VENV_SHIM_SHA256, VENV_SHIM_PTH: VENV_SHIM_PTH_SHA256}
# Imported lazily inside run_committee_stage. Imported up front so that the
# loaded-module check sees them before the paid call.
STAGE_DEPENDENCIES = ("anthropic", "moat.config", "moat.analysis.pricing", "moat.committee.committee",
                      "moat.committee.caller")

# Stated in every provenance record. The second and third are facts about
# the frozen candidate, documented in docs/evals/sprint-6.0-run-provenance.md.
LIMITATIONS = (
    "The server-side model version behind the model ID cannot be verified.",
    "The frozen candidate committee's commit and environment were reconstructed after the fact "
    "(docs/evals/sprint-6.0-run-provenance.md: commit inferred from timestamps; environment "
    "freezes taken after the runs). Runtime equivalence rests on an empty code diff and a "
    "matching freeze, not on a record made by the original run.",
    "Processing order differs. The replicate is one sequential A-Z pass of run_committee_stage. "
    "The candidate cohort was assembled from parallel A-Z and Z-A workers across several runs, "
    "with some verdicts copied forward as cache hits.",
)

# The committee stage's own summary line (scripts/run_pipeline.py). The
# tests pin this format by running the real stage.
_COST_RE = re.compile(r"^\s*total cost estimate: \$([0-9]+(?:\.[0-9]+)?)\s*$", re.M)


class Unusable(Exception):
    """A rule failed. The attempt must not be sealed."""

    def __init__(self, rule: str, counts: dict | None = None):
        super().__init__(rule)
        self.rule = rule
        self.counts = counts or {}


@dataclasses.dataclass
class Config:
    source: Path
    out_dir: Path
    cap_usd: float
    preflight_only: bool = False


@dataclasses.dataclass
class Hooks:
    """Injection points for the offline tests. Production uses the defaults."""
    run_stage: Callable = None          # (conn, run_id, cap) -> None, prints the stage summary
    run_tests: Callable = None          # () -> dict of suite -> {"returncode", "summary"}
    installed: Callable = None          # () -> list of "name==version"
    python_version: Callable = None     # () -> "X.Y.Z"
    before_create: Callable = None      # () -> None, called just before O_EXCL creation
    git: Callable = None                # (*args) -> stdout; raises on a non-zero exit
    loaded_modules: Callable = None     # () -> {module name: file path or None}
    interpreter_roots: Callable = None  # () -> {"site": [...], "stdlib": [...], "user_site_enabled": bool}

    def __post_init__(self):
        self.run_stage = self.run_stage or (lambda conn, run_id, cap: rp.run_committee_stage(conn, run_id, cost_cap_usd=cap))
        self.run_tests = self.run_tests or _run_test_suites
        self.installed = self.installed or _installed_distributions
        self.python_version = self.python_version or platform.python_version
        self.before_create = self.before_create or (lambda: None)
        self.git = self.git or _git
        self.loaded_modules = self.loaded_modules or _loaded_modules
        self.interpreter_roots = self.interpreter_roots or _interpreter_roots


# ---------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sidecars(db: Path) -> list[Path]:
    return [Path(str(db) + s) for s in SIDECAR_SUFFIXES if Path(str(db) + s).exists()]


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _normalise_requirements(lines) -> list[str]:
    out = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, version = line.partition("==")
        out.append(f"{re.sub(r'[-_.]+', '-', name).lower()}{sep}{version}")
    return sorted(out)


def _installed_distributions() -> list[str]:
    return [f"{d.metadata['Name']}=={d.version}" for d in metadata.distributions()]


def _attempt_name() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"attempt-{stamp}-{os.getpid()}-{secrets.token_hex(4)}"


def _loaded_modules() -> dict:
    return {name: getattr(m, "__file__", None) for name, m in list(sys.modules.items())}


def _run_test_suites() -> dict:
    results = {}
    for args in (["tests/test_eval_harness.py"], []):
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        lines = r.stdout.strip().splitlines()
        results[" ".join(["pytest -q", *args])] = {"returncode": r.returncode, "summary": lines[-1] if lines else ""}
    return results


def _git(*args: str, stdin: str | None = None) -> str:
    return subprocess.run(["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True,
                          input=stdin, check=True).stdout


def _tree_blobs(git: Callable, rev: str) -> dict:
    """{repo-relative path: blob sha} for every file at rev."""
    out = {}
    for line in git("ls-tree", "-r", "-z", rev).split("\0"):
        if line:
            meta, path = line.split("\t", 1)
            out[path] = meta.split()[2]
    return out


def _is_repo_module(name: str) -> bool:
    return name in ("moat", "run_pipeline", "run_committee_replicate") or name.startswith("moat.")


def _interpreter_roots() -> dict:
    """The only places outside the repository that code may load from:
    this interpreter's site-packages and its standard library."""
    paths = sysconfig.get_paths()
    site_dirs = {Path(paths[k]).resolve() for k in ("purelib", "platlib")}
    # Every directory site.py processes .pth files from, including any
    # system site-packages a venv has been allowed to see.
    site_dirs |= {Path(d).resolve() for d in site.getsitepackages()}
    std = sorted({Path(paths[k]).resolve() for k in ("stdlib", "platstdlib")})
    return {"site": sorted(site_dirs), "stdlib": std, "user_site_enabled": bool(site.ENABLE_USER_SITE)}


def _distribution_records() -> dict:
    """{resolved file path: (hash algorithm, urlsafe-b64 digest)} from every
    installed distribution's RECORD."""
    out = {}
    for d in metadata.distributions():
        for f in d.files or ():
            if f.hash:
                out[Path(d.locate_file(f)).resolve()] = (f.hash.mode, f.hash.value)
    return out


def _record_digest(path: Path, algorithm: str) -> str:
    return base64.urlsafe_b64encode(hashlib.new(algorithm, path.read_bytes()).digest()).rstrip(b"=").decode()


def code_identity_problems(git: Callable, modules: dict, roots: dict | None = None,
                           records: Callable = _distribution_records) -> dict:
    """List the ways the loaded code could differ from the candidate's. Every
    loaded module with a file must come from one of three origins:

    - this repository. The file must equal its HEAD blob and, unless listed
      in NON_CANDIDATE_CODE, its CANDIDATE_COMMIT blob;
    - this interpreter's site-packages. The file must be listed in an
      installed distribution's RECORD with a matching hash. The one
      exception is the virtualenv shim, which is pinned by hash. Together
      with the exact freeze match, this means every third-party file that
      runs is the approved distribution's own file;
    - this interpreter's standard library (pinned by CANDIDATE_PYTHON).

    Anything else, e.g. a module reached through PYTHONPATH, is refused. A
    moat module from anywhere but the repository counts as shadowed.

    Startup code is checked as well. .pth files run at interpreter startup
    without ever appearing in sys.modules. So every .pth file in the
    site-packages roots must be RECORD-verified or pinned by hash. The
    per-user site directory, which has no RECORD, must be disabled."""
    roots = roots or _interpreter_roots()
    head, cand = _tree_blobs(git, "HEAD"), _tree_blobs(git, CANDIDATE_COMMIT)
    repo = REPO_ROOT.resolve()
    files: dict[str, str] = {}
    site_files: list[tuple[str, Path]] = []
    problems = {"user_site_enabled": [], "unverified_startup_file": [],
                "shadowed_outside_repository": [], "loaded_outside_approved_roots": [],
                "not_in_any_record": [], "differs_from_record": [],
                "not_at_head": [], "differs_from_head": [],
                "not_at_candidate": [], "differs_from_candidate": []}
    for name, file in sorted(modules.items()):
        if not file:
            continue
        path = Path(file).resolve()
        in_site = any(_inside(path, r) for r in roots["site"])
        in_std = not in_site and any(_inside(path, r) for r in roots["stdlib"])
        in_repo = not (in_site or in_std) and _inside(path, repo)
        if _is_repo_module(name) and not in_repo:
            problems["shadowed_outside_repository"].append(name)
        elif in_site:
            site_files.append((name, path))
        elif in_repo:
            files[path.relative_to(repo).as_posix()] = name
        elif not in_std:
            problems["loaded_outside_approved_roots"].append(f"{name}: {path}")
    if roots.get("user_site_enabled"):
        problems["user_site_enabled"].append(site.getusersitepackages())
    rec_cache: list = []

    def verified(path: Path) -> str | None:
        """None if the file is RECORD-verified or pinned, else the reason."""
        pinned = PINNED_UNRECORDED.get(path.name)
        if pinned and path.parent in roots["site"] and sha256_file(path) == pinned:
            return None
        if not rec_cache:
            rec_cache.append(records())  # built once, only if needed
        rec = rec_cache[0]
        if path not in rec:
            return "not_in_any_record"
        return None if _record_digest(path, rec[path][0]) == rec[path][1] else "differs_from_record"

    for d in roots["site"]:
        for pth in sorted(d.glob("*.pth")) if d.is_dir() else ():
            reason = verified(pth.resolve())
            if reason:
                problems["unverified_startup_file"].append(f"{pth.resolve()}: {reason}")
    for name, path in site_files:
        reason = verified(path)
        if reason:
            problems[reason].append(f"{name}: {path}")
    rels = sorted(files)
    shas = git("hash-object", "--stdin-paths", stdin="\n".join(str(repo / r) for r in rels) + "\n").split() if rels else []
    for rel, sha in zip(rels, shas):
        if rel not in head:
            problems["not_at_head"].append(rel)
        elif head[rel] != sha:
            problems["differs_from_head"].append(rel)
        if rel.startswith(NON_CANDIDATE_CODE):
            continue
        if rel not in cand:
            problems["not_at_candidate"].append(rel)
        elif cand[rel] != sha:
            problems["differs_from_candidate"].append(rel)
    return {k: v for k, v in problems.items() if v}


def upstream_digest(conn) -> dict:
    """(row count, SHA-256) per table, for every table except the two the
    committee stage writes. Includes SQLite's internal tables, e.g.
    sqlite_sequence."""
    out = {}
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    for t in tables:
        if t in EXCLUDED_FROM_UPSTREAM:
            continue
        h = hashlib.sha256()
        n = 0
        for row in conn.execute(f'SELECT * FROM "{t}" ORDER BY rowid'):
            h.update(repr(tuple(row)).encode())
            n += 1
        out[t] = [n, h.hexdigest()]
    return out


def schema_sql(conn) -> list:
    return [tuple(r) for r in conn.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name")]


def bundle_counts(src, rep, tickers, stored_keys: dict, replicate_keys: dict | None, vr: str, qr: str) -> dict:
    """Recompute each ticker's committee bundle key in both snapshots. The
    candidate key must equal the stored cache key, the replicate key the
    replicate's cache key (when there is one), and the two keys each other."""
    bad = {"candidate_bundle_unavailable": 0, "replicate_bundle_unavailable": 0,
           "candidate_cache_key_mismatch": 0, "replicate_cache_key_mismatch": 0,
           "cross_snapshot_bundle_mismatch": 0}
    for t in tickers:
        kc = src.recompute_committee_bundle_key(t, vr, qr)
        kr = rep.recompute_committee_bundle_key(t, vr, qr)
        bad["candidate_bundle_unavailable"] += kc is None
        bad["replicate_bundle_unavailable"] += kr is None
        bad["candidate_cache_key_mismatch"] += kc != stored_keys[t]
        if replicate_keys is not None:
            bad["replicate_cache_key_mismatch"] += kr != replicate_keys[t]
        bad["cross_snapshot_bundle_mismatch"] += kc != kr
    return bad


def upstream_and_schema_counts(src_upstream, src_schema, rep) -> dict:
    rep_upstream = upstream_digest(rep.conn)
    return {
        "upstream_tables_changed": (sum(src_upstream.get(t) != v for t, v in rep_upstream.items())
                                    + len(set(src_upstream) ^ set(rep_upstream))),
        "schema_changed": int(schema_sql(rep.conn) != src_schema),
    }


# ---------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------

class ReplicateRunner:
    def __init__(self, cfg: Config, hooks: Hooks | None = None):
        self.cfg = cfg
        self.hooks = hooks or Hooks()
        self.base = cfg.out_dir
        # A directory per attempt, created with os.mkdir, which fails if the
        # name exists. Nothing else writes into it, so the SQLite sidecars
        # that appear beside the working copy are this attempt's own.
        self.attempt_dir = self.base / "work" / _attempt_name()
        self.work = self.attempt_dir / "replicate-working.db"
        self.sealed_dir = self.base / "sealed"
        self.log_lines: list[str] = []
        # Current paths of every file this runner created, in creation order.
        # The working copy's sidecars are covered by owned_sidecars instead,
        # because SQLite creates them inside the owned attempt directory.
        self.owned: list[Path] = []
        self.owned_sidecars = False
        self.sealed = False
        self.run_id: str | None = None
        self.stage_log: Path | None = None

    # -- logging --------------------------------------------------------
    def log(self, msg: str) -> None:
        line = f"[{_now()}] {msg}"
        self.log_lines.append(line)
        print(line, flush=True)

    def fail(self, rule: str, **counts) -> None:
        raise Unusable(rule, counts)

    # -- ownership ------------------------------------------------------
    def _open_exclusive(self, path: Path) -> int:
        """Create path, failing if anything exists there. The file is owned
        from the moment it exists."""
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            self.fail("artifact_path_exists", path=str(path))
        self.owned.append(path)
        return fd

    def _write_exclusive(self, path: Path, text: str) -> None:
        with os.fdopen(self._open_exclusive(path), "w") as f:
            f.write(text)
        os.chmod(path, 0o444)

    def _move_owned(self, src: Path, dst: Path) -> None:
        """Move an owned file without ever overwriting: link, then unlink.
        Both names stay owned until the unlink succeeds."""
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(src, dst)
        except FileExistsError:
            self.fail("artifact_path_exists", path=str(dst))
        self.owned.append(dst)
        os.unlink(src)
        self.owned.remove(src)

    def _remove_attempt_dir(self) -> None:
        """Remove the attempt directory once nothing owned is left in it.
        rmdir fails on a non-empty directory, so nothing is ever deleted with
        it. A leftover is logged and kept, and is not a failure."""
        try:
            os.rmdir(self.attempt_dir)
        except OSError as exc:
            self.log(f"note: attempt directory kept: {exc!r}")

    def _release(self, path: Path) -> None:
        os.unlink(path)
        self.owned.remove(path)

    # -- marking --------------------------------------------------------
    @staticmethod
    def _atomic_mark(p: Path) -> str:
        for n in range(1000):
            dest = Path(f"{p}.UNUSABLE" + (f".{n}" if n else ""))
            try:
                os.link(p, dest)
            except FileExistsError:
                continue
            os.unlink(p)
            return str(dest)
        raise OSError(f"no free .UNUSABLE name for {p}")

    def mark_unusable(self) -> tuple[list[str], list[str]]:
        """Mark every file this runner owns. An owned file that has gone
        missing is a failure: it can no longer be marked. Never raises."""
        marked, failures = [], []
        if self.sealed:
            return marked, failures
        for path in list(self.owned):
            paths = [path]
            if path == self.work and self.owned_sidecars:
                paths += _sidecars(self.work)
            for p in paths:
                try:
                    if not os.path.lexists(p):
                        failures.append(f"{p}: owned file missing")
                        continue
                    marked.append(self._atomic_mark(p))
                    if p in self.owned:
                        self.owned.remove(p)
                except BaseException as exc:  # noqa: BLE001
                    failures.append(f"{p}: {exc!r}")
        return marked, failures

    # -- entry ----------------------------------------------------------
    def run(self) -> int:
        try:
            self._run()
            return 0
        except BaseException as exc:  # noqa: BLE001 — every failure must leave the attempt unusable
            code = 2
            try:
                if isinstance(exc, Unusable):
                    self.log(f"UNUSABLE: {exc.rule} {json.dumps(exc.counts, sort_keys=True, default=str)}")
                else:
                    self.log(f"UNUSABLE: unexpected_exception {exc!r}")
            except BaseException:  # noqa: BLE001
                pass
            marked, failures = self.mark_unusable()
            try:
                self.log(f"marked unusable: {marked}")
                if failures:
                    self.log(f"MARK_FAILED: {failures}")
            except BaseException:  # noqa: BLE001
                pass
            if failures:
                code = 3
            try:
                fdir = self.base / "failed"
                fdir.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                fd = os.open(fdir / f"operator-failure-{stamp}-{os.getpid()}.log",
                             os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
                with os.fdopen(fd, "w") as f:
                    f.write("\n".join(self.log_lines) + "\n")
            except BaseException:  # noqa: BLE001
                code = 3
            return code

    # -- the procedure --------------------------------------------------
    def _runtime(self) -> dict:
        """Amendment 1 §A1.6: the interpreter, the distributions and the code
        must all be the candidate's. Nothing here is operator-selected."""
        py = self.hooks.python_version()
        if py != CANDIDATE_PYTHON:
            self.fail("python_version_mismatch", expected=CANDIDATE_PYTHON, actual=py)

        if not APPROVED_FREEZE.is_file() or sha256_file(APPROVED_FREEZE) != APPROVED_FREEZE_SHA256:
            self.fail("approved_freeze_missing_or_changed", path=str(APPROVED_FREEZE))
        expected = _normalise_requirements(APPROVED_FREEZE.read_text().splitlines())
        installed = _normalise_requirements(self.hooks.installed())
        if not expected or len(installed) != len(set(installed)) or set(expected) != set(installed):
            self.fail("runtime_does_not_equal_approved_freeze",
                      missing_or_different=len(set(expected) - set(installed)),
                      extra=len(set(installed) - set(expected)),
                      duplicates=len(installed) - len(set(installed)), expected_entries=len(expected))

        git = self.hooks.git
        head = git("rev-parse", "HEAD").strip()
        dirty = git("status", "--porcelain", "--untracked-files=all")
        if not head or dirty:
            self.fail("repository_not_clean", has_head=int(bool(head)), dirty_or_untracked=len(dirty.splitlines()))
        try:
            git("cat-file", "-e", f"{CANDIDATE_COMMIT}^{{commit}}")
        except subprocess.CalledProcessError:
            self.fail("candidate_commit_unavailable", commit=CANDIDATE_COMMIT, hint="fetch full history")
        diff = git("diff", "--name-only", CANDIDATE_COMMIT, "HEAD", "--", *CANDIDATE_CODE_SCOPE).split()
        if diff:
            self.fail("candidate_code_differs", files=diff)

        for mod in STAGE_DEPENDENCIES:
            importlib.import_module(mod)
        modules = self.hooks.loaded_modules()
        problems = code_identity_problems(git, modules, roots=self.hooks.interpreter_roots())
        if problems:
            self.fail("loaded_code_not_candidate", **problems)
        checked = sum(1 for n, f in modules.items() if f and _inside(Path(f), REPO_ROOT))

        return {
            "git": {"commit": head, "clean_including_untracked": True, "repo": str(REPO_ROOT),
                    "candidate_commit": CANDIDATE_COMMIT, "candidate_code_scope": list(CANDIDATE_CODE_SCOPE),
                    "candidate_code_diff": [], "loaded_repository_modules_checked": checked},
            "runtime": {
                "python": py, "python_expected": CANDIDATE_PYTHON, "python_executable": sys.executable,
                "sqlite": sqlite3.sqlite_version, "platform": platform.platform(),
                "anthropic": metadata.version("anthropic"),
                "approved_freeze": (APPROVED_FREEZE.relative_to(REPO_ROOT).as_posix()
                                    if _inside(APPROVED_FREEZE, REPO_ROOT) else str(APPROVED_FREEZE)),
                "approved_freeze_sha256": APPROVED_FREEZE_SHA256,
                "installed_equals_freeze": True, "freeze_entries": len(expected),
            },
        }

    def _preconditions(self) -> dict:
        cfg = self.cfg
        live = DB_PATH.resolve()
        src = cfg.source
        if src.name == "moat.db" or src.resolve() == live:
            self.fail("source_is_live_database")
        if _inside(src, REPO_ROOT) or _inside(cfg.out_dir, REPO_ROOT):
            self.fail("path_inside_repository", source=int(_inside(src, REPO_ROOT)), out_dir=int(_inside(cfg.out_dir, REPO_ROOT)))
        if self.work.resolve() == live or _inside(live, cfg.out_dir):
            self.fail("out_dir_aliases_live_database")
        if not src.is_file() or src.stat().st_mode & 0o222 or _sidecars(src):
            self.fail("source_not_sealed", exists=int(src.is_file()))
        src_sha = sha256_file(src)
        if src_sha != eval_constants.SNAPSHOT_SHA256["candidate"] or src.stat().st_size != eval_constants.SNAPSHOT_BYTES["candidate"]:
            self.fail("source_does_not_match_frozen_candidate")

        if DEFAULT_MODEL != eval_constants.COMMITTEE_MODEL or PROTOCOL_VERSION != eval_constants.COMMITTEE_PROTOCOL_VERSION:
            self.fail("model_or_protocol_mismatch")

        facts = self._runtime()
        facts["source"] = {"path": str(src.resolve()), "sha256": src_sha, "bytes": src.stat().st_size}

        tests = self.hooks.run_tests()
        for suite, r in tests.items():
            self.log(f"precondition tests: {suite} -> rc {r['returncode']}; {r['summary']}")
        if not tests or any(r["returncode"] != 0 for r in tests.values()):
            self.fail("precondition_tests_failed")
        facts["precondition_tests"] = tests

        if os.path.lexists(self.attempt_dir):
            self.fail("attempt_dir_already_exists", path=str(self.attempt_dir))
        return facts

    def _create_working_copy(self, src: Path, src_sha: str) -> None:
        self.attempt_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.mkdir(self.attempt_dir, 0o700)
        except FileExistsError:
            self.fail("attempt_dir_appeared_before_creation", path=str(self.attempt_dir))
        self.hooks.before_create()
        try:
            fd = os.open(self.work, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            self.fail("working_copy_appeared_before_creation")
        self.owned.append(self.work)
        foreign = _sidecars(self.work)
        self.owned_sidecars = not foreign
        if foreign:
            os.close(fd)
            self.fail("foreign_sidecars_present_at_creation", count=len(foreign))
        with os.fdopen(fd, "wb") as dst, open(src, "rb") as s:
            while chunk := s.read(1 << 20):
                dst.write(chunk)
        if sha256_file(self.work) != src_sha:
            self.fail("working_copy_hash_mismatch")

    def _close_to_delete_mode(self, conn) -> None:
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            mode = conn.execute("PRAGMA journal_mode = DELETE").fetchone()[0]
        finally:
            conn.close()
        if str(mode).lower() != "delete" or _sidecars(self.work):
            self.fail("sidecars_remain", journal_mode=mode, sidecars=len(_sidecars(self.work)))

    def _sealed_names(self, run_id: str) -> dict:
        d = self.sealed_dir
        return {"db": d / f"moat-replicate-{run_id}.db", "stage_log": d / f"{run_id}.stage.log",
                "provenance": d / f"{run_id}.runtime-provenance.json", "operator_log": d / f"{run_id}.operator.log"}

    def _run(self) -> None:
        cfg = self.cfg
        started = _now()
        cand = eval_constants.RUN_IDS["candidate"]
        qr, vr, cr = cand["screen"], cand["valuation"], cand["committee"]
        frozen_ids = {r for runs in eval_constants.RUN_IDS.values() for r in runs.values()}
        self.log(f"start; cap ${cfg.cap_usd:.2f}; preflight_only={cfg.preflight_only}")

        facts = self._preconditions()
        self.log(f"preconditions ok; commit {facts['git']['commit'][:7]}; code == candidate {CANDIDATE_COMMIT[:7]}; "
                 f"python {facts['runtime']['python']}; anthropic {facts['runtime']['anthropic']}; "
                 f"installed == approved freeze ({facts['runtime']['freeze_entries']})")

        with ImmutableDatabase(cfg.source, PROFILE) as src:
            src.validate_schema()
            stored_keys = {r["ticker"]: r["cache_key"] for r in src.committee_full_rows(cr)}
            src_upstream = upstream_digest(src.conn)
            src_schema = schema_sql(src.conn)
            src_runs = {r[0] for r in src.conn.execute("SELECT run_id FROM pipeline_runs")}
        stored = set(stored_keys)
        if not stored:
            self.fail("stored_candidate_cohort_empty")

        self._create_working_copy(cfg.source, facts["source"]["sha256"])
        self.log("working copy created; hash equals source")

        conn = get_connection(self.work)
        try:
            if rp._latest_quality_run_id(conn) != qr or rp._latest_valuation_run_id(conn) != vr:
                self.fail("latest_upstream_runs_do_not_resolve_to_frozen_candidate")
            ai = {r[0] for r in conn.execute("SELECT DISTINCT ticker FROM ai_analysis WHERE is_current = 1")}
            va = {r[0] for r in conn.execute("SELECT DISTINCT ticker FROM valuations WHERE run_id = ?", (vr,))}
            cohort = (ai & va) - config.COMMITTEE_KNOWN_EXCLUDED_TICKERS
            if cohort != stored:
                self.fail("stage_cohort_ne_stored_cohort", stage=len(cohort), stored=len(stored))
            self.log(f"stage cohort == stored candidate cohort ({len(stored)})")

            deleted = {r[0]: r[1] for r in conn.execute(
                "SELECT run_id, COUNT(*) FROM committee_verdicts GROUP BY run_id ORDER BY run_id")}
            conn.execute("DELETE FROM committee_verdicts")
            conn.commit()
            if conn.execute("SELECT COUNT(*) FROM committee_verdicts").fetchone()[0]:
                self.fail("cache_clear_failed")
            self.log(f"removed {sum(deleted.values())} committee_verdicts rows across {len(deleted)} runs (working copy only)")
        finally:
            self._close_to_delete_mode(conn)

        # The harness adapter opens only read-only files. Writable again only
        # for the stage, then read-only for good.
        os.chmod(self.work, 0o444)
        self._validate_inputs(stored, stored_keys, src_upstream, src_schema, src_runs, qr, vr)

        if cfg.preflight_only:
            self._release(self.work)
            self._remove_attempt_dir()
            self.log("preflight only: no API call made")
            self.log("PREFLIGHT PASSED; working copy deleted")
            return

        run_id = rp.new_run_id()
        if run_id in frozen_ids or run_id in src_runs:
            self.fail("run_id_not_distinct")
        self.run_id = run_id
        taken = [str(p) for p in self._sealed_names(run_id).values() if os.path.lexists(p)]
        if taken:
            self.fail("sealed_artifact_path_exists", paths=taken)
        # Acquired before the paid call, so a collision costs nothing.
        stage_fd = self._open_exclusive(self.attempt_dir / f"{run_id}.stage.log")
        self.stage_log = self.owned[-1]

        os.chmod(self.work, 0o644)
        conn = get_connection(self.work)
        completed = None
        spend = None
        try:
            rp.start_run(conn, run_id)
            self.log(f"replicate run_id {run_id}; committee stage starting")
            buf = io.StringIO()
            stage_error = None
            with contextlib.redirect_stdout(buf):
                try:
                    self.hooks.run_stage(conn, run_id, cfg.cap_usd)
                except BaseException as exc:  # noqa: BLE001
                    stage_error = repr(exc)
            out = buf.getvalue()
            with os.fdopen(stage_fd, "w") as f:
                f.write(out)
            os.chmod(self.stage_log, 0o444)
            m = _COST_RE.search(out)
            spend = float(m.group(1)) if m else None
            n_rows = conn.execute("SELECT COUNT(*) FROM committee_verdicts WHERE run_id = ?", (run_id,)).fetchone()[0]
            self.log(f"stage finished; rows {n_rows}/{len(stored)}; estimated spend "
                     f"{spend if spend is not None else 'unknown'}; error {stage_error}")
            complete = stage_error is None and n_rows == len(stored) and spend is not None
            rp.complete_run(conn, run_id, "committee" if complete else "none", "complete" if complete else "failed")
            completed = _now()
            if not complete:
                self.fail("incomplete_committee_stage", expected=len(stored), produced=n_rows,
                          spend=spend if spend is not None else "unknown", stage_error=stage_error)
        finally:
            self._close_to_delete_mode(conn)
        os.chmod(self.work, 0o444)

        # The stage imported its own dependencies; none may be non-candidate.
        problems = code_identity_problems(self.hooks.git, self.hooks.loaded_modules(),
                                          roots=self.hooks.interpreter_roots())
        if problems:
            self.fail("loaded_code_not_candidate_after_stage", **problems)

        result = self._validate(stored, stored_keys, src_upstream, src_schema, src_runs, qr, vr, frozen_ids)
        self._seal(facts, deleted, started, completed, spend, len(stored), result)

    def _validate_inputs(self, stored, stored_keys, src_upstream, src_schema, src_runs, qr, vr) -> None:
        """Offline, before any call: the cleared working copy must present the
        stage with exactly the candidate's inputs."""
        with ImmutableDatabase(self.work, PROFILE) as rep, ImmutableDatabase(self.cfg.source, PROFILE) as src:
            rep.validate_schema()
            bad = bundle_counts(src, rep, sorted(stored), stored_keys, None, vr, qr)
            bad.update(upstream_and_schema_counts(src_upstream, src_schema, rep))
            bad["committee_rows_remaining"] = rep.conn.execute("SELECT COUNT(*) FROM committee_verdicts").fetchone()[0]
            rep_runs = {r[0] for r in rep.conn.execute("SELECT run_id FROM pipeline_runs")}
            bad["unexpected_pipeline_runs"] = int(rep_runs != src_runs)
        failed = {k: v for k, v in bad.items() if v}
        if failed:
            self.fail("input_validation_failed", **failed)
        self.log(f"input validation passed: {len(stored)} bundle keys recomputed in both snapshots, equal to "
                 f"each other and to the stored cache keys; upstream tables and schema identical")

    def _validate(self, stored, stored_keys, src_upstream, src_schema, src_runs, qr, vr, frozen_ids) -> dict:
        run_id = self.run_id
        with ImmutableDatabase(self.work, PROFILE) as rep, ImmutableDatabase(self.cfg.source, PROFILE) as src:
            rep.validate_schema()
            run = rep.run_row(run_id)
            if run_id in frozen_ids or run is None or run["stage_reached"] != "committee" or run["status"] != "complete":
                self.fail("run_row_invalid")
            rows = rep.committee_full_rows(run_id)
            other = rep.conn.execute("SELECT COUNT(*) FROM committee_verdicts WHERE run_id <> ?", (run_id,)).fetchone()[0]
            canon = [canonical_ticker(r["ticker"]) for r in rows]
            if len(canon) != len(set(canon)) or set(canon) != {canonical_ticker(t) for t in stored} or other:
                self.fail("cohort_mismatch", expected=len(stored), produced=len(rows),
                          distinct=len(set(canon)), other_run_rows=other)
            if {r["ticker"] for r in rows} != stored:
                self.fail("cohort_mismatch", expected=len(stored), produced=len(rows), ticker_spelling_differs=1)
            bad = {
                "reused_not_null": sum(r["reused_from_run_id"] is not None for r in rows),
                "wrong_quality_run": sum(r["quality_run_id"] != qr for r in rows),
                "wrong_valuation_run": sum(r["valuation_run_id"] != vr for r in rows),
                "null_cache_key": sum(not r["cache_key"] for r in rows),
                "structurally_invalid": sum(not committee_output_is_valid(r) for r in rows),
            }
            bad.update(bundle_counts(src, rep, sorted(stored), stored_keys,
                                     {r["ticker"]: r["cache_key"] for r in rows}, vr, qr))
            bad.update(upstream_and_schema_counts(src_upstream, src_schema, rep))
            rep_runs = {r[0] for r in rep.conn.execute("SELECT run_id FROM pipeline_runs")}
            bad["unexpected_pipeline_runs"] = int(rep_runs != src_runs | {run_id})
            n_upstream = len(upstream_digest(rep.conn))
        failed = {k: v for k, v in bad.items() if v}
        if failed or _sidecars(self.work):
            self.fail("validation_failed", **failed, sidecars=len(_sidecars(self.work)))
        self.log(f"validation passed: {len(rows)}/{len(stored)} rows; reuse all null; bundles equal; "
                 f"upstream tables identical ({n_upstream}); schema identical")
        return {"rows": len(rows), "upstream_tables": n_upstream}

    def _provenance(self, facts, deleted, started, completed, spend, expected, result, final, final_sha) -> dict:
        return {
            "purpose": "eval-spec-v1 amendment-1 unchanged-input committee replicate",
            "snapshot": {"path": str(final.resolve()), "sha256": final_sha, "bytes": final.stat().st_size,
                         "profile": PROFILE, "read_only": not (final.stat().st_mode & 0o222),
                         "journal_mode": "delete", "vacuum": False},
            "replicate_run_id": self.run_id,
            "source_candidate": {**facts["source"], "runs": eval_constants.RUN_IDS["candidate"]},
            "cache_clearing": {"table": "committee_verdicts", "scope": "working copy only",
                               "rows_removed": sum(deleted.values()), "rows_removed_by_run_id": deleted},
            "git": facts["git"],
            "runtime": facts["runtime"],
            "precondition_tests": facts["precondition_tests"],
            "model": DEFAULT_MODEL, "protocol_version": PROTOCOL_VERSION, "personas": list(PERSONAS),
            "stage_call": f"run_pipeline.run_committee_stage(conn, run_id, cost_cap_usd={self.cfg.cap_usd}), "
                          "default exclusions, sequential",
            "runner": {"path": str(Path(__file__).resolve().relative_to(REPO_ROOT)), "sha256": sha256_file(Path(__file__))},
            "limitations": list(LIMITATIONS),
            "started_utc": started, "completed_utc": completed,
            "approved_cap_usd": self.cfg.cap_usd, "estimated_spend_usd": spend,
            "cohort": {"expected": expected, "produced": result["rows"]},
            "validation": {"pre_call_input_validation": "passed",
                           "all_reused_from_run_id_null": True, "cross_snapshot_bundle_equivalence": "passed",
                           "upstream_tables_identical": result["upstream_tables"], "schema_identical": True,
                           "sidecars": 0},
        }

    def _seal(self, facts, deleted, started, completed, spend, expected, result) -> None:
        names = self._sealed_names(self.run_id)
        final = names["db"]
        self._move_owned(self.work, final)
        self._move_owned(self.stage_log, names["stage_log"])

        final_sha = sha256_file(final)
        prov = self._provenance(facts, deleted, started, completed, spend, expected, result, final, final_sha)
        self._write_exclusive(names["provenance"], json.dumps(prov, indent=2, sort_keys=True))
        self._remove_attempt_dir()
        self.log(f"SEALED {final} sha256 {final_sha} bytes {final.stat().st_size}; provenance {names['provenance']}")
        self._write_exclusive(names["operator_log"], "\n".join(self.log_lines) + "\n")
        self.sealed = True


def parse_args(argv=None) -> Config:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--source", type=Path, required=True, help="frozen candidate snapshot (read-only, outside the repo)")
    p.add_argument("--out-dir", type=Path, required=True, help="private output directory (outside the repo)")
    p.add_argument("--cap-usd", type=float, required=True, help="approved hard API cost cap")
    p.add_argument("--preflight-only", action="store_true",
                   help="run every check, including offline input validation, then delete the working copy; no API call")
    a = p.parse_args(argv)
    if a.cap_usd <= 0:
        p.error("--cap-usd must be positive")
    return Config(a.source, a.out_dir, a.cap_usd, a.preflight_only)


def main(argv=None) -> int:
    return ReplicateRunner(parse_args(argv)).run()


if __name__ == "__main__":
    sys.exit(main())
