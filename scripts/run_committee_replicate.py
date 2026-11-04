#!/usr/bin/env python3
"""Produce the eval-spec-v1 Amendment 1 unchanged-input committee replicate.

Operator tooling for the system under test, not the eval harness. The
script runs the committee stage once more, from inputs identical to the
frozen candidate snapshot, and seals the result as a separate read-only
SQLite file for the noise-floor experiment (Amendment 1 §A1). It never
evaluates, scores or interprets the verdicts. That stays with the harness
(moat/evals/noise_floor.py) and is a separate, Pete-authorized step.

Usage (one paid run, needs approval):

    python scripts/run_committee_replicate.py \\
        --source /private/path/moat-candidate-sprint6.0.db \\
        --out-dir /private/path/replicate/ \\
        --cap-usd 15 \\
        --expected-freeze docs/evals/sprint-6.0-env-py311.txt

Add --preflight-only to run every check, build the working copy, clear its
cache and verify that the stage cohort and bundle keys match, then delete
the working copy without making any API call.

Guarantees:

- Inputs. The frozen source is opened only immutably and copied. The live
  data/moat.db is refused as a source, a working copy or an output, and is
  never opened. The source and out-dir must both lie outside this repository.
- Fresh calls. The committee cache is a (ticker, cache_key) lookup across
  every run, and unchanged inputs would make every company a cache hit. So
  every committee_verdicts row is deleted from the disposable working copy,
  and only there. No upstream table is touched. The deleted row counts per
  run ID are recorded.
- One shot. There is no resume and no retry: a resumed run would turn
  finished rows into cache hits. Any failure leaves the attempt unusable.
- Ownership. The working copy is created with O_CREAT|O_EXCL, and the
  runner renames only files it created. Marking uses os.link plus os.unlink,
  which never overwrites, so a stale .UNUSABLE file is left alone.
- Validation before sealing. The checks reuse the harness's own read-only
  adapter (moat.evals.database.ImmutableDatabase), its bundle recomputation
  and its structural-validity rule, rather than re-implementing them.

Exit codes: 0 sealed (or preflight passed), 2 unusable, 3 unusable and at
least one owned file could not be marked (the log names it).
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import io
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
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
from moat.evals.noise_floor import _committee_output_is_valid  # noqa: E402

import run_pipeline as rp  # noqa: E402

PROFILE = "candidate-v1"
SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
EXCLUDED_FROM_UPSTREAM = ("committee_verdicts", "pipeline_runs")

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
    expected_freeze: Path
    preflight_only: bool = False


@dataclasses.dataclass
class Hooks:
    """Injection points for the offline tests. Production uses the defaults."""
    run_stage: Callable = None          # (conn, run_id, cap) -> None, prints the stage summary
    run_tests: Callable = None          # () -> dict of suite -> {"returncode", "summary"}
    installed: Callable = None          # () -> list of "name==version"
    before_create: Callable = None      # () -> None, called just before O_EXCL creation
    git_state: Callable = None          # () -> (head commit, dirty tracked files text)

    def __post_init__(self):
        self.run_stage = self.run_stage or (lambda conn, run_id, cap: rp.run_committee_stage(conn, run_id, cost_cap_usd=cap))
        self.run_tests = self.run_tests or _run_test_suites
        self.installed = self.installed or _installed_distributions
        self.before_create = self.before_create or (lambda: None)
        self.git_state = self.git_state or _git_state


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


def _git_state() -> tuple[str, str]:
    head = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(REPO_ROOT), "status", "--porcelain", "--untracked-files=no"],
                           capture_output=True, text=True).stdout
    return head, dirty


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


# ---------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------

class ReplicateRunner:
    def __init__(self, cfg: Config, hooks: Hooks | None = None):
        self.cfg = cfg
        self.hooks = hooks or Hooks()
        self.base = cfg.out_dir
        self.work = self.base / "work" / "replicate-working.db"
        self.sealed_dir = self.base / "sealed"
        self.log_lines: list[str] = []
        self.owned_work = False
        self.owned_sidecars = False
        self.final: Path | None = None
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
        """Mark every file this runner owns. Never raises."""
        marked, failures = [], []
        targets = []
        if self.owned_work:
            targets.append(self.work)
        if self.final is not None and not self.sealed:
            targets.append(self.final)
        for db in targets:
            suffixes = ("", *SIDECAR_SUFFIXES) if self.owned_sidecars else ("",)
            for s in suffixes:
                p = Path(str(db) + s)
                try:
                    if p.exists():
                        marked.append(self._atomic_mark(p))
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
                (fdir / f"operator-failure-{stamp}-{os.getpid()}.log").write_text("\n".join(self.log_lines) + "\n")
            except BaseException:  # noqa: BLE001
                code = 3
            return code

    # -- the procedure --------------------------------------------------
    def _preconditions(self) -> dict:
        cfg = self.cfg
        facts: dict = {}

        head, dirty = self.hooks.git_state()
        if not head or dirty:
            self.fail("repository_not_clean", has_head=int(bool(head)), dirty=int(bool(dirty)))
        facts["git"] = {"commit": head, "clean_tracked": True, "repo": str(REPO_ROOT)}

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
        facts["source"] = {"path": str(src.resolve()), "sha256": src_sha, "bytes": src.stat().st_size}

        if DEFAULT_MODEL != eval_constants.COMMITTEE_MODEL or PROTOCOL_VERSION != eval_constants.COMMITTEE_PROTOCOL_VERSION:
            self.fail("model_or_protocol_mismatch")

        expected = _normalise_requirements(cfg.expected_freeze.read_text().splitlines())
        installed = set(_normalise_requirements(self.hooks.installed()))
        missing = [r for r in expected if r not in installed]
        if missing:
            self.fail("runtime_does_not_match_expected_freeze", missing_or_different=len(missing))
        facts["runtime"] = {
            "python": platform.python_version(), "python_executable": sys.executable,
            "sqlite": sqlite3.sqlite_version, "platform": platform.platform(),
            "anthropic": metadata.version("anthropic"),
            "expected_freeze": str(cfg.expected_freeze),
            "expected_freeze_sha256": sha256_file(cfg.expected_freeze),
            "expected_freeze_entries_matched": len(expected),
        }

        tests = self.hooks.run_tests()
        for suite, r in tests.items():
            self.log(f"precondition tests: {suite} -> rc {r['returncode']}; {r['summary']}")
        if not tests or any(r["returncode"] != 0 for r in tests.values()):
            self.fail("precondition_tests_failed")
        facts["precondition_tests"] = tests

        if self.work.exists() or _sidecars(self.work):
            self.fail("working_copy_already_exists")
        return facts

    def _create_working_copy(self, src: Path, src_sha: str) -> None:
        self.work.parent.mkdir(parents=True, exist_ok=True)
        self.hooks.before_create()
        try:
            fd = os.open(self.work, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            self.fail("working_copy_appeared_before_creation")
        self.owned_work = True
        foreign = _sidecars(self.work)
        self.owned_sidecars = not foreign
        if foreign:
            os.close(fd)
            self.fail("foreign_sidecars_present_at_creation", count=len(foreign))
        with os.fdopen(fd, "wb") as dst, open(src, "rb") as s:
            shutil.copyfileobj(s, dst, 1 << 20)
        if sha256_file(self.work) != src_sha:
            self.fail("working_copy_hash_mismatch")

    def _run(self) -> None:
        cfg = self.cfg
        started = _now()
        cand = eval_constants.RUN_IDS["candidate"]
        qr, vr, cr = cand["screen"], cand["valuation"], cand["committee"]
        frozen_ids = {r for runs in eval_constants.RUN_IDS.values() for r in runs.values()}
        self.log(f"start; cap ${cfg.cap_usd:.2f}; preflight_only={cfg.preflight_only}")

        facts = self._preconditions()
        self.log(f"preconditions ok; commit {facts['git']['commit'][:7]}; python {facts['runtime']['python']}; "
                 f"anthropic {facts['runtime']['anthropic']}")

        with ImmutableDatabase(cfg.source, PROFILE) as src:
            src.validate_schema()
            stored = {r["ticker"] for r in src.committee_full_rows(cr)}
            src_upstream = upstream_digest(src.conn)
            src_schema = schema_sql(src.conn)
            src_runs = {r[0] for r in src.conn.execute("SELECT run_id FROM pipeline_runs")}
        if not stored:
            self.fail("stored_candidate_cohort_empty")

        self._create_working_copy(cfg.source, facts["source"]["sha256"])
        self.log("working copy created; hash equals source")

        conn = get_connection(self.work)
        completed = None
        spend = None
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

            if cfg.preflight_only:
                self.log("preflight only: no API call made")
            else:
                run_id = rp.new_run_id()
                if run_id in frozen_ids or run_id in src_runs:
                    self.fail("run_id_not_distinct")
                self.run_id = run_id
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
                self.stage_log = self.base / "work" / f"{run_id}.stage.log"
                self.stage_log.write_text(out)
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

            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            mode = conn.execute("PRAGMA journal_mode = DELETE").fetchone()[0]
        finally:
            conn.close()
        if str(mode).lower() != "delete" or _sidecars(self.work):
            self.fail("sidecars_remain", journal_mode=mode, sidecars=len(_sidecars(self.work)))
        os.chmod(self.work, 0o444)

        if cfg.preflight_only:
            os.unlink(self.work)
            self.owned_work = False
            self.log("PREFLIGHT PASSED; working copy deleted")
            return

        result = self._validate(stored, src_upstream, src_schema, src_runs, qr, vr, cr, frozen_ids)
        self._seal(facts, deleted, started, completed, spend, len(stored), result)

    def _validate(self, stored, src_upstream, src_schema, src_runs, qr, vr, cr, frozen_ids) -> dict:
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
            stored_rows = {r["ticker"]: r for r in src.committee_full_rows(cr)}
            bad = {
                "reused_not_null": sum(r["reused_from_run_id"] is not None for r in rows),
                "wrong_quality_run": sum(r["quality_run_id"] != qr for r in rows),
                "wrong_valuation_run": sum(r["valuation_run_id"] != vr for r in rows),
                "null_cache_key": sum(not r["cache_key"] for r in rows),
                "structurally_invalid": sum(not _committee_output_is_valid(r) for r in rows),
                "candidate_bundle_unavailable": 0, "replicate_bundle_unavailable": 0,
                "candidate_cache_key_mismatch": 0, "replicate_cache_key_mismatch": 0,
                "cross_snapshot_bundle_mismatch": 0,
            }
            for r in rows:
                t = r["ticker"]
                kc = src.recompute_committee_bundle_key(t, vr, qr)
                kr = rep.recompute_committee_bundle_key(t, vr, qr)
                bad["candidate_bundle_unavailable"] += kc is None
                bad["replicate_bundle_unavailable"] += kr is None
                bad["candidate_cache_key_mismatch"] += kc != stored_rows[t]["cache_key"]
                bad["replicate_cache_key_mismatch"] += kr != r["cache_key"]
                bad["cross_snapshot_bundle_mismatch"] += kc != kr
            rep_upstream = upstream_digest(rep.conn)
            bad["upstream_tables_changed"] = (
                sum(src_upstream.get(t) != v for t, v in rep_upstream.items()) + len(set(src_upstream) ^ set(rep_upstream)))
            bad["schema_changed"] = int(schema_sql(rep.conn) != src_schema)
            rep_runs = {r[0] for r in rep.conn.execute("SELECT run_id FROM pipeline_runs")}
            bad["unexpected_pipeline_runs"] = int(rep_runs != src_runs | {run_id})
        failed = {k: v for k, v in bad.items() if v}
        if failed or _sidecars(self.work):
            self.fail("validation_failed", **failed, sidecars=len(_sidecars(self.work)))
        self.log(f"validation passed: {len(rows)}/{len(stored)} rows; reuse all null; bundles equal; "
                 f"upstream tables identical ({len(rep_upstream)}); schema identical")
        return {"rows": len(rows), "upstream_tables": len(rep_upstream)}

    def _seal(self, facts, deleted, started, completed, spend, expected, result) -> None:
        run_id = self.run_id
        self.sealed_dir.mkdir(parents=True, exist_ok=True)
        final = self.sealed_dir / f"moat-replicate-{run_id}.db"
        # Link, don't rename: os.link fails if the final name already exists.
        try:
            os.link(self.work, final)
        except FileExistsError:
            self.fail("final_path_exists")
        self.final = final
        os.unlink(self.work)
        self.owned_work = False

        stage_log = self.sealed_dir / f"{run_id}.stage.log"
        shutil.move(str(self.stage_log), stage_log)
        os.chmod(stage_log, 0o444)

        final_sha = sha256_file(final)
        prov = {
            "purpose": "eval-spec-v1 amendment-1 unchanged-input committee replicate",
            "snapshot": {"path": str(final.resolve()), "sha256": final_sha, "bytes": final.stat().st_size,
                         "profile": PROFILE, "read_only": not (final.stat().st_mode & 0o222),
                         "journal_mode": "delete", "vacuum": False},
            "replicate_run_id": run_id,
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
            "validation": {"all_reused_from_run_id_null": True, "cross_snapshot_bundle_equivalence": "passed",
                           "upstream_tables_identical": result["upstream_tables"], "schema_identical": True,
                           "sidecars": 0},
        }
        prov_path = self.sealed_dir / f"{run_id}.runtime-provenance.json"
        prov_path.write_text(json.dumps(prov, indent=2, sort_keys=True))
        os.chmod(prov_path, 0o444)
        self.log(f"SEALED {final} sha256 {final_sha} bytes {final.stat().st_size}; provenance {prov_path}")
        log_path = self.sealed_dir / f"{run_id}.operator.log"
        log_path.write_text("\n".join(self.log_lines) + "\n")
        os.chmod(log_path, 0o444)
        self.sealed = True


def parse_args(argv=None) -> Config:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--source", type=Path, required=True, help="frozen candidate snapshot (read-only, outside the repo)")
    p.add_argument("--out-dir", type=Path, required=True, help="private output directory (outside the repo)")
    p.add_argument("--cap-usd", type=float, required=True, help="approved hard API cost cap")
    p.add_argument("--expected-freeze", type=Path, required=True,
                   help="requirements freeze the running interpreter must match, e.g. docs/evals/sprint-6.0-env-py311.txt")
    p.add_argument("--preflight-only", action="store_true", help="run every offline check, then delete the working copy; no API call")
    a = p.parse_args(argv)
    if a.cap_usd <= 0:
        p.error("--cap-usd must be positive")
    return Config(a.source, a.out_dir, a.cap_usd, a.expected_freeze, a.preflight_only)


def main(argv=None) -> int:
    return ReplicateRunner(parse_args(argv)).run()


if __name__ == "__main__":
    sys.exit(main())
