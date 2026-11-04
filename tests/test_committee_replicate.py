"""Offline tests for scripts/run_committee_replicate.py.

No API calls are made. The real committee stage
(run_pipeline.run_committee_stage -> committee.run_committee) runs against a
synthetic candidate snapshot built with the real schema. Only the model call
(call_persona) and the response parser are stubbed, so cache keys, cache
lookup, persistence and the stage's cost summary are all the production code.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from moat.committee.prompt import PERSONA_SCORE_COMPONENTS
from moat.db.connection import DB_PATH, get_connection, init_db
from moat.evals import constants as eval_constants
from moat.evals.constants import PROFILE_COLUMNS, RUN_IDS
from moat.evals.database import ImmutableDatabase
from scripts import run_committee_replicate as rcr
from scripts.run_committee_replicate import Config, Hooks, ReplicateRunner

CAND = RUN_IDS["candidate"]
QR, VR, CR = CAND["screen"], CAND["valuation"], CAND["committee"]
AI_RUN = "synthetic_ai_run"
TICKERS = ("AAA", "BBB", "CCC")
FREEZE_LINES = ["anthropic==9.9.9", "Some_Package==1.0"]


# ---------------------------------------------------------------------
# Synthetic candidate snapshot
# ---------------------------------------------------------------------

def _fake_persona_patches(cost=0.01):
    def fake_call(client, ticker, persona, context_block, model_id=None, dry_run=False):
        return SimpleNamespace(cost_estimate=cost, stop_reason="end_turn", raw_text=f"{persona} view of {ticker}")

    def fake_parse(persona, raw_text, known_claim_ids):
        return SimpleNamespace(
            is_valid=True, validation_errors=[], raw_text=raw_text, verdict=f"{persona} verdict", statements=[],
            scores={c: 60.0 for c in PERSONA_SCORE_COMPONENTS[persona]},
            severity="low" if persona == "bear" else None,
        )

    return [
        patch("moat.committee.committee.call_persona", side_effect=fake_call),
        patch("moat.committee.committee.parse_persona_response", side_effect=fake_parse),
        patch("moat.config.ANTHROPIC_API_KEY", "dummy-key-not-used"),
    ]


def _with_patches(patches, fn):
    for p in patches:
        p.start()
    try:
        return fn()
    finally:
        for p in reversed(patches):
            p.stop()


def _reorder_to_profile(path: Path) -> None:
    """init_db() orders some migrated columns differently from the frozen
    snapshot. Rebuild those tables in the harness profile's column order."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = OFF")
    for table, columns in PROFILE_COLUMNS["candidate-v1"].items():
        info = {r[1]: r for r in conn.execute(f'PRAGMA table_info("{table}")')}
        if tuple(info) == tuple(columns):
            continue
        defs = []
        for c in columns:
            _, name, typ, notnull, dflt, _pk = info[c]
            d = f'"{name}" {typ}' + (" NOT NULL" if notnull else "") + (f" DEFAULT {dflt}" if dflt is not None else "")
            defs.append(d)
        pk = [r[1] for r in sorted(info.values(), key=lambda r: r[5]) if r[5]]
        if pk:
            defs.append("PRIMARY KEY (" + ", ".join(f'"{c}"' for c in pk) + ")")
        cols = ", ".join(f'"{c}"' for c in columns)
        conn.executescript(f"""
            CREATE TABLE "{table}__new" ({", ".join(defs)});
            INSERT INTO "{table}__new" ({cols}) SELECT {cols} FROM "{table}";
            DROP TABLE "{table}";
            ALTER TABLE "{table}__new" RENAME TO "{table}";
        """)
    conn.commit()
    conn.close()


def build_candidate(path: Path) -> None:
    init_db(path)
    conn = get_connection(path)
    now = "2026-09-30T00:00:00+00:00"
    for run_id, started, stage, status in [
        (AI_RUN, "2026-09-29T00:00:00Z", "ai_analysis", "partial"),
        (QR, "2026-09-29T13:54:25Z", "quality", "partial"),
        (VR, "2026-09-29T13:54:55Z", "valuation", "partial"),
        (CR, "2026-09-30T13:35:28Z", "committee", "partial"),
    ]:
        conn.execute("INSERT INTO pipeline_runs (run_id, started_at, stage_reached, status) VALUES (?,?,?,?)",
                     (run_id, started, stage, status))
    for i, t in enumerate(TICKERS):
        conn.execute("INSERT INTO companies (ticker, name, sector, currency, universe, is_active, added_date) "
                     "VALUES (?,?,?,?,?,1,?)", (t, f"Synthetic {t}", "Technology", "USD", "sp500", "2026-01-01"))
        conn.execute("INSERT INTO fundamentals_annual (ticker, fiscal_year, source, confidence, retrieved_at) "
                     "VALUES (?,?,?,?,?)", (t, 2025, "sec_edgar", "high", now))
        for metric in ("roic", "roe"):
            conn.execute("INSERT INTO quant_scores (run_id, ticker, metric, value, status, sector_percentile, "
                         "sector_peer_group, overall_pass) VALUES (?,?,?,?,?,?,?,1)",
                         (QR, t, metric, 0.2 + i, "pass", 80.0, "Technology"))
        conn.execute("INSERT INTO quality_scores (run_id, ticker, passed_screen, composite_score, metrics_assessed, "
                     "metrics_passed) VALUES (?,?,1,?,8,7)", (QR, t, 87.5))
        conn.execute("INSERT INTO valuations (run_id, ticker, method, scenario, intrinsic_value_low, "
                     "intrinsic_value_high, current_price, margin_of_safety_pct, key_assumptions, created_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?)", (VR, t, "owner_earnings_dcf", "base", 90.0, 110.0, 80.0, 0.2, "{}", now))
        for atype in ("business_quality", "moat", "management", "risk"):
            conn.execute("INSERT INTO ai_analysis (run_id, ticker, analysis_type, content, model, prompt_version, "
                         "cache_key, is_current, created_at) VALUES (?,?,?,?,?,?,?,1,?)",
                         (AI_RUN, t, atype, "{}", "claude-sonnet-4-6", "v1", f"key-{t}-{atype}", now))
    conn.commit()
    # The stored candidate committee: the real stage under the frozen run ID.
    import run_pipeline as rp
    _with_patches(_fake_persona_patches(), lambda: rp.run_committee_stage(conn, CR, cost_cap_usd=5.0))
    assert conn.execute("SELECT COUNT(*) FROM committee_verdicts WHERE run_id = ?", (CR,)).fetchone()[0] == len(TICKERS)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("PRAGMA journal_mode = DELETE")
    conn.close()
    _reorder_to_profile(path)
    os.chmod(path, 0o444)


@pytest.fixture(scope="module")
def candidate_template(tmp_path_factory):
    path = tmp_path_factory.mktemp("candidate") / "candidate.db"
    build_candidate(path)
    return path


@pytest.fixture
def env(tmp_path, candidate_template, monkeypatch):
    source = tmp_path / "source" / "candidate.db"
    source.parent.mkdir()
    source.write_bytes(candidate_template.read_bytes())
    os.chmod(source, 0o444)
    monkeypatch.setitem(eval_constants.SNAPSHOT_SHA256, "candidate", rcr.sha256_file(source))
    monkeypatch.setitem(eval_constants.SNAPSHOT_BYTES, "candidate", source.stat().st_size)
    freeze = tmp_path / "freeze.txt"
    freeze.write_text("# header\n" + "\n".join(FREEZE_LINES) + "\n")
    monkeypatch.setattr(rcr, "APPROVED_FREEZE", freeze)
    monkeypatch.setattr(rcr, "APPROVED_FREEZE_SHA256", rcr.sha256_file(freeze))
    out = tmp_path / "out"
    return SimpleNamespace(source=source, out=out, freeze=freeze, tmp=tmp_path)


def fake_git(status="", diff="", head="0" * 40):
    """Stands in for git in a clean checkout whose candidate scope equals the
    candidate commit. No module is loaded from the repository (see _hooks),
    so the tree listings are never consulted."""
    def git(*args, stdin=None):
        return {"rev-parse": head + "\n", "status": status, "diff": diff, "ls-tree": ""}[args[0]]
    return git


def _hooks(**overrides):
    base = dict(
        run_tests=lambda: {"pytest -q": {"returncode": 0, "summary": "ok"}},
        installed=lambda: ["anthropic==9.9.9", "some-package==1.0"],
        python_version=lambda: rcr.CANDIDATE_PYTHON,
        git=fake_git(),
        loaded_modules=lambda: {},
    )
    base.update(overrides)
    return Hooks(**base)


def _runner(env, hooks=None, cap=5.0, preflight_only=False, source=None):
    cfg = Config(source or env.source, env.out, cap, preflight_only)
    return ReplicateRunner(cfg, hooks or _hooks())


def _run(runner, cost=0.01):
    return _with_patches(_fake_persona_patches(cost), runner.run)


def _work(env):
    return env.out / "work" / "replicate-working.db"


def _files(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()) if root.exists() else []


def _assert_unusable(env):
    sealed = env.out / "sealed"
    assert not (sealed.exists() and list(sealed.glob("*.db")))
    assert len(list((env.out / "failed").glob("operator-failure-*.log"))) == 1


# ---------------------------------------------------------------------
# Success paths
# ---------------------------------------------------------------------

def test_full_run_seals_fresh_replicate_with_identical_inputs(env):
    source_sha = rcr.sha256_file(env.source)
    opened = []
    real_get_connection = rcr.get_connection

    def spy(path=DB_PATH):
        opened.append(Path(path).resolve())
        return real_get_connection(path)

    runner = _runner(env)
    with patch.object(rcr, "get_connection", side_effect=spy):
        code = _run(runner)
    assert code == 0, runner.log_lines
    assert opened and set(opened) == {_work(env).resolve()}
    assert DB_PATH.resolve() not in opened
    assert rcr.sha256_file(env.source) == source_sha

    sealed = list((env.out / "sealed").glob("moat-replicate-*.db"))
    assert len(sealed) == 1
    final = sealed[0]
    assert not final.stat().st_mode & 0o222
    assert not rcr._sidecars(final)
    assert not any((env.out / "work").iterdir())

    with ImmutableDatabase(final, "candidate-v1") as rep:
        rep.validate_schema()
        rows = rep.committee_full_rows(runner.run_id)
        assert len(rows) == len(TICKERS)
        assert all(r["reused_from_run_id"] is None for r in rows)
        assert all(r["quality_run_id"] == QR and r["valuation_run_id"] == VR for r in rows)
        # The stored candidate rows were cleared from the copy, not served from cache.
        assert rep.conn.execute("SELECT COUNT(*) FROM committee_verdicts WHERE run_id = ?", (CR,)).fetchone()[0] == 0
        assert rep.run_row(runner.run_id)["status"] == "complete"

    import json
    prov = json.loads((env.out / "sealed" / f"{runner.run_id}.runtime-provenance.json").read_text())
    assert prov["replicate_run_id"] == runner.run_id
    assert runner.run_id not in {r for runs in RUN_IDS.values() for r in runs.values()}
    assert prov["cohort"] == {"expected": 3, "produced": 3}
    assert prov["estimated_spend_usd"] == pytest.approx(0.09, abs=1e-3)  # 3 tickers x 3 personas x $0.01
    assert prov["cache_clearing"]["rows_removed"] == 3
    assert len(prov["limitations"]) == 3
    assert prov["snapshot"]["sha256"] == rcr.sha256_file(final)
    assert prov["source_candidate"]["sha256"] == source_sha
    assert prov["runtime"]["freeze_entries"] == 2
    assert prov["runtime"]["installed_equals_freeze"] is True
    assert prov["runtime"]["python"] == rcr.CANDIDATE_PYTHON
    assert prov["git"]["candidate_commit"] == rcr.CANDIDATE_COMMIT
    assert prov["validation"]["pre_call_input_validation"] == "passed"
    sealed_files = _files(env.out / "sealed")
    assert sealed_files == sorted([final.name, f"{runner.run_id}.stage.log",
                                   f"{runner.run_id}.runtime-provenance.json", f"{runner.run_id}.operator.log"])
    assert all(not (env.out / "sealed" / f).stat().st_mode & 0o222 for f in sealed_files)


def test_preflight_only_makes_no_stage_call_and_leaves_nothing(env):
    called = []
    hooks = _hooks(run_stage=lambda *a: called.append(a))
    runner = _runner(env, hooks, preflight_only=True)
    assert runner.run() == 0, runner.log_lines
    assert called == []
    assert _files(env.out) == []
    assert any("PREFLIGHT PASSED" in line for line in runner.log_lines)


# ---------------------------------------------------------------------
# Refusals before anything is created
# ---------------------------------------------------------------------

@pytest.mark.parametrize("case, rule", [
    ("inside_repo", "path_inside_repository"),
    ("live_name", "source_is_live_database"),
    ("live_path", "source_is_live_database"),
    ("hash_mismatch", "source_does_not_match_frozen_candidate"),
    ("writable_source", "source_not_sealed"),
])
def test_bad_source_is_refused_before_any_copy(env, case, rule, monkeypatch):
    source = env.source
    if case == "inside_repo":
        # A real, sealed, hash-matching file inside the repo: only the
        # location rule can refuse it.
        source = rcr.REPO_ROOT / "tests" / "_replicate_source_copy.db"
        source.write_bytes(env.source.read_bytes())
        os.chmod(source, 0o444)
    elif case == "live_name":
        source = env.tmp / "moat.db"
    elif case == "live_path":
        source = DB_PATH
    elif case == "hash_mismatch":
        monkeypatch.setitem(eval_constants.SNAPSHOT_SHA256, "candidate", "0" * 64)
    elif case == "writable_source":
        os.chmod(env.source, 0o644)
    opened = []
    try:
        with patch.object(rcr, "get_connection", side_effect=lambda *a: opened.append(a)):
            runner = _runner(env, source=source)
            assert runner.run() == 2
    finally:
        if case == "inside_repo":
            source.unlink()
    assert opened == []
    assert any(f"UNUSABLE: {rule}" in line for line in runner.log_lines), runner.log_lines
    assert not _work(env).exists()
    assert all(p.startswith("failed/") for p in _files(env.out))


def test_out_dir_inside_repo_is_refused(env):
    cfg = Config(env.source, rcr.REPO_ROOT / "data" / "replicate-out", 5.0)
    runner = ReplicateRunner(cfg, _hooks())
    try:
        assert runner.run() == 2
        assert any("path_inside_repository" in line for line in runner.log_lines)
    finally:
        import shutil
        shutil.rmtree(rcr.REPO_ROOT / "data" / "replicate-out", ignore_errors=True)


@pytest.mark.parametrize("installed", [
    ["anthropic==1.0.0", "some-package==1.0"],                 # different version
    ["anthropic==9.9.9"],                                      # missing
    ["anthropic==9.9.9", "some-package==1.0", "extra==2.0"],   # extra
    ["anthropic==9.9.9", "some-package==1.0", "Some.Package==1.0"],  # duplicate
])
def test_installed_must_equal_approved_freeze_exactly(env, installed):
    runner = _runner(env, _hooks(installed=lambda: installed))
    assert runner.run() == 2
    assert any("runtime_does_not_equal_approved_freeze" in line for line in runner.log_lines)
    assert not _work(env).exists()


@pytest.mark.parametrize("content", ["", "# nothing pinned\n", "anthropic==9.9.9\n"])
def test_unapproved_freeze_is_refused_even_if_installed_matches(env, content):
    # The reviewer's mutation: an empty (or any other) freeze in place of the
    # approved one. The pinned hash refuses it, whatever is installed.
    env.freeze.write_text(content)
    runner = _runner(env, _hooks(installed=lambda: []))
    assert runner.run() == 2
    assert any("approved_freeze_missing_or_changed" in line for line in runner.log_lines)
    assert not _work(env).exists()


def test_empty_approved_freeze_is_refused(env, monkeypatch):
    # Even if an empty freeze were somehow pinned, it establishes nothing.
    env.freeze.write_text("# header only\n")
    monkeypatch.setattr(rcr, "APPROVED_FREEZE_SHA256", rcr.sha256_file(env.freeze))
    runner = _runner(env, _hooks(installed=lambda: []))
    assert runner.run() == 2
    assert any("runtime_does_not_equal_approved_freeze" in line for line in runner.log_lines)


def test_pinned_freeze_is_the_documented_candidate_freeze():
    assert rcr.APPROVED_FREEZE == rcr.REPO_ROOT / "docs" / "evals" / "sprint-6.0-env-py311.txt"
    assert rcr.sha256_file(rcr.APPROVED_FREEZE) == rcr.APPROVED_FREEZE_SHA256
    assert "anthropic==1.9.0" in rcr.APPROVED_FREEZE.read_text().splitlines()
    assert rcr.CANDIDATE_PYTHON == "3.11.16"


def test_python_version_mismatch_is_refused(env):
    runner = _runner(env, _hooks(python_version=lambda: "3.11.15"))
    assert runner.run() == 2
    assert any("python_version_mismatch" in line for line in runner.log_lines)
    assert not _work(env).exists()


@pytest.mark.parametrize("status, rule", [
    (" M moat/committee/committee.py\n", "repository_not_clean"),
    ("?? moat/committee/anthropic.py\n", "repository_not_clean"),   # untracked shadow
])
def test_unclean_or_untracked_repository_is_refused(env, status, rule):
    runner = _runner(env, _hooks(git=fake_git(status=status)))
    assert runner.run() == 2
    assert any(f"UNUSABLE: {rule}" in line for line in runner.log_lines)
    assert not _work(env).exists()


def test_candidate_code_diff_is_refused(env):
    runner = _runner(env, _hooks(git=fake_git(diff="moat/committee/committee.py\n")))
    assert runner.run() == 2
    assert any("candidate_code_differs" in line and "committee.py" in line for line in runner.log_lines)
    assert not _work(env).exists()


def test_status_asks_git_for_untracked_files(env):
    calls = []
    git = fake_git()
    _runner(env, _hooks(git=lambda *a, **k: calls.append(a) or git(*a, **k)))._runtime()
    assert ("status", "--porcelain", "--untracked-files=all") in calls
    assert ("diff", "--name-only", rcr.CANDIDATE_COMMIT, "HEAD", "--", *rcr.CANDIDATE_CODE_SCOPE) in calls


# The loaded-module check against the real repository history. These use
# real git and committed files only, so they hold in any clean checkout.

def test_candidate_pipeline_module_passes_code_identity():
    mods = {"run_pipeline": str(rcr.REPO_ROOT / "scripts" / "run_pipeline.py"),
            "moat.committee.committee": str(rcr.REPO_ROOT / "moat" / "committee" / "committee.py")}
    assert rcr.code_identity_problems(rcr._git, mods) == {}


def test_module_changed_since_candidate_fails_code_identity():
    # moat/dashboard/app.py changed after 79450dd (PR #25). Loaded, it is
    # non-candidate code even though it equals HEAD.
    mods = {"moat.dashboard.app": str(rcr.REPO_ROOT / "moat" / "dashboard" / "app.py")}
    assert rcr.code_identity_problems(rcr._git, mods) == {"differs_from_candidate": ["moat/dashboard/app.py"]}


def test_module_added_since_candidate_fails_code_identity():
    mods = {"moat.evals.constants": str(rcr.REPO_ROOT / "moat" / "evals" / "constants.py"),
            "tests.test_eval_harness": str(rcr.REPO_ROOT / "tests" / "test_eval_harness.py")}
    # Harness code is exempt from the candidate comparison; anything else new is not.
    assert rcr.code_identity_problems(rcr._git, mods) == {"not_at_candidate": ["tests/test_eval_harness.py"]}


def test_untracked_module_in_repository_fails_code_identity():
    shadow = rcr.REPO_ROOT / "moat" / "committee" / "_shadow_for_test.py"
    shadow.write_text("x = 1\n")
    try:
        problems = rcr.code_identity_problems(rcr._git, {"moat.committee._shadow_for_test": str(shadow)})
    finally:
        shadow.unlink()
    assert problems == {"not_at_head": ["moat/committee/_shadow_for_test.py"],
                        "not_at_candidate": ["moat/committee/_shadow_for_test.py"]}


def test_repo_module_loaded_from_elsewhere_is_shadowed(tmp_path):
    fake = tmp_path / "committee.py"
    fake.write_text("x = 1\n")
    problems = rcr.code_identity_problems(rcr._git, {"moat.committee.committee": str(fake)})
    assert problems == {"shadowed_outside_repository": ["moat.committee.committee"]}


def test_loaded_code_problem_is_refused_before_any_copy(env):
    runner = _runner(env, _hooks(loaded_modules=lambda: {"moat.committee.committee": "/elsewhere/committee.py"}))
    assert runner.run() == 2
    assert any("loaded_code_not_candidate" in line for line in runner.log_lines)
    assert not _work(env).exists()


def test_module_loaded_during_stage_is_checked_after_it(env):
    loaded = {}
    import run_pipeline as rp

    def stage(conn, run_id, cap):
        rp.run_committee_stage(conn, run_id, cost_cap_usd=cap)
        loaded["moat.committee.committee"] = "/elsewhere/committee.py"
    runner = _runner(env, _hooks(run_stage=stage, loaded_modules=lambda: dict(loaded)))
    assert _run(runner) == 2
    _assert_unusable(env)
    assert any("loaded_code_not_candidate_after_stage" in line for line in runner.log_lines)


def test_failing_precondition_tests_are_refused(env):
    runner = _runner(env, _hooks(run_tests=lambda: {"pytest -q": {"returncode": 1, "summary": "1 failed"}}))
    assert runner.run() == 2
    assert any("precondition_tests_failed" in line for line in runner.log_lines)
    assert not _work(env).exists()




# ---------------------------------------------------------------------
# Failures after the working copy exists: always marked, never sealed
# ---------------------------------------------------------------------

def test_stage_exception_marks_working_copy(env):
    def boom(conn, run_id, cap):
        raise RuntimeError("stubbed API failure")
    runner = _runner(env, _hooks(run_stage=boom))
    assert runner.run() == 2
    _assert_unusable(env)
    assert not _work(env).exists()
    assert Path(str(_work(env)) + ".UNUSABLE").exists()
    assert any("incomplete_committee_stage" in line and '"spend": "unknown"' in line for line in runner.log_lines)


def test_cost_cap_halt_is_incomplete_and_marked(env):
    # $1 per persona call against a $1.50 cap: the stage halts part-way.
    runner = _runner(env, cap=1.5)
    assert _run(runner, cost=1.0) == 2
    _assert_unusable(env)
    assert any("incomplete_committee_stage" in line for line in runner.log_lines)
    assert Path(str(_work(env)) + ".UNUSABLE").exists()


def test_tampered_cache_key_fails_validation(env):
    import run_pipeline as rp

    def stage(conn, run_id, cap):
        rp.run_committee_stage(conn, run_id, cost_cap_usd=cap)
        conn.execute("UPDATE committee_verdicts SET cache_key = ? WHERE run_id = ? AND ticker = ?", ("0" * 64, run_id, "AAA"))
        conn.commit()
    runner = _runner(env, _hooks(run_stage=stage))
    assert _run(runner) == 2
    _assert_unusable(env)
    assert any("validation_failed" in line and "replicate_cache_key_mismatch" in line for line in runner.log_lines)


def test_upstream_change_fails_validation(env):
    import run_pipeline as rp

    def stage(conn, run_id, cap):
        rp.run_committee_stage(conn, run_id, cost_cap_usd=cap)
        conn.execute("UPDATE fundamentals_annual SET retrieved_at = 'changed' WHERE ticker = 'AAA'")
        conn.commit()
    runner = _runner(env, _hooks(run_stage=stage))
    assert _run(runner) == 2
    assert any("upstream_tables_changed" in line for line in runner.log_lines)


def test_cache_reuse_fails_validation(env):
    import run_pipeline as rp

    def stage(conn, run_id, cap):
        rp.run_committee_stage(conn, run_id, cost_cap_usd=cap)
        conn.execute("UPDATE committee_verdicts SET reused_from_run_id = ? WHERE run_id = ? AND ticker = 'BBB'", (CR, run_id))
        conn.commit()
    runner = _runner(env, _hooks(run_stage=stage))
    assert _run(runner) == 2
    assert any("reused_not_null" in line for line in runner.log_lines)


# ---------------------------------------------------------------------
# Ownership and marking
# ---------------------------------------------------------------------

def test_preexisting_working_copy_is_never_touched(env):
    _work(env).parent.mkdir(parents=True)
    _work(env).write_bytes(b"foreign")
    runner = _runner(env)
    assert runner.run() == 2
    assert _work(env).read_bytes() == b"foreign"
    assert not list(_work(env).parent.glob("*.UNUSABLE*"))


def test_working_copy_appearing_before_creation_is_never_touched(env):
    runner = _runner(env, _hooks(before_create=lambda: _work(env).write_bytes(b"foreign-race")))
    assert runner.run() == 2
    assert _work(env).read_bytes() == b"foreign-race"
    assert not list(_work(env).parent.glob("*.UNUSABLE*"))
    assert any("working_copy_appeared_before_creation" in line for line in runner.log_lines)


def test_foreign_sidecar_is_never_renamed(env):
    wal = Path(str(_work(env)) + "-wal")
    runner = _runner(env, _hooks(before_create=lambda: wal.write_bytes(b"foreign-wal")))
    assert runner.run() == 2
    assert wal.read_bytes() == b"foreign-wal"
    assert not Path(str(wal) + ".UNUSABLE").exists()
    assert Path(str(_work(env)) + ".UNUSABLE").exists()


def test_stale_unusable_file_is_preserved(env):
    _work(env).parent.mkdir(parents=True)
    stale = Path(str(_work(env)) + ".UNUSABLE")
    stale.write_bytes(b"stale")
    runner = _runner(env, _hooks(run_stage=lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))))
    assert runner.run() == 2
    assert stale.read_bytes() == b"stale"
    assert Path(str(_work(env)) + ".UNUSABLE.1").exists()
    assert not _work(env).exists()


def test_marking_failure_exits_3_without_raising(env, monkeypatch):
    def fail_mark(p):
        raise OSError("stubbed rename failure")
    monkeypatch.setattr(ReplicateRunner, "_atomic_mark", staticmethod(fail_mark))
    runner = _runner(env, _hooks(run_stage=lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))))
    assert runner.run() == 3
    assert any("MARK_FAILED" in line for line in runner.log_lines)
    assert len(list((env.out / "failed").glob("operator-failure-*.log"))) == 1


def test_final_name_collision_marks_and_preserves_existing(env, monkeypatch):
    import run_pipeline as rp
    fixed = "20991231T000000Z"
    monkeypatch.setattr(rp, "new_run_id", lambda: fixed)
    (env.out / "sealed").mkdir(parents=True)
    existing = env.out / "sealed" / f"moat-replicate-{fixed}.db"
    existing.write_bytes(b"existing")
    called = []
    runner = _runner(env, _hooks(run_stage=lambda *a: called.append(a)))
    assert _run(runner) == 2
    assert called == []  # refused before the paid call
    assert existing.read_bytes() == b"existing"
    assert any("sealed_artifact_path_exists" in line for line in runner.log_lines)
    assert Path(str(_work(env)) + ".UNUSABLE").exists()


def test_cost_summary_regex_matches_stage_output():
    assert rcr._COST_RE.search("\n  committee summary:\n    persisted: 3\n    total cost estimate: $7.343\n    cap: $15.00\n").group(1) == "7.343"
    assert rcr._COST_RE.search("    cap: $15.00\n") is None


# ---------------------------------------------------------------------
# Offline input validation (also what --preflight-only guarantees)
# ---------------------------------------------------------------------

def _tamper_working_copy_on_creation(env, sql):
    """Change the working copy right after it is copied, before the runner
    reads it. Stands in for any defect that would feed the stage different
    inputs."""
    real = rcr.ReplicateRunner._create_working_copy

    def create(self, src, sha):
        real(self, src, sha)
        conn = sqlite3.connect(self.work)
        conn.execute(sql)
        conn.commit()
        conn.close()
    return create


@pytest.mark.parametrize("preflight_only", [True, False])
@pytest.mark.parametrize("sql, counter", [
    ("UPDATE valuations SET current_price = 81.0 WHERE ticker = 'AAA'", "cross_snapshot_bundle_mismatch"),
    ("UPDATE fundamentals_annual SET retrieved_at = 'changed' WHERE ticker = 'AAA'", "upstream_tables_changed"),
])
def test_input_validation_runs_before_any_call(env, monkeypatch, preflight_only, sql, counter):
    monkeypatch.setattr(rcr.ReplicateRunner, "_create_working_copy", _tamper_working_copy_on_creation(env, sql))
    # Tampering means the hash check cannot pass; skip it so that only the
    # input validation is left to catch the change.
    monkeypatch.setattr(rcr, "sha256_file", _sha_ignoring_work(env))
    called = []
    runner = _runner(env, _hooks(run_stage=lambda *a: called.append(a)), preflight_only=preflight_only)
    assert runner.run() == 2
    assert called == []
    assert any("input_validation_failed" in line and counter in line for line in runner.log_lines), runner.log_lines
    _assert_unusable(env)
    assert Path(str(_work(env)) + ".UNUSABLE").exists()


def _sha_ignoring_work(env):
    real = rcr.sha256_file

    def sha(path):
        if Path(path).resolve() == _work(env).resolve():
            return real(env.source)
        return real(path)
    return sha


def test_preflight_logs_bundle_key_verification(env):
    runner = _runner(env, preflight_only=True)
    assert runner.run() == 0, runner.log_lines
    assert any("input validation passed" in line and "bundle keys recomputed" in line for line in runner.log_lines)


# ---------------------------------------------------------------------
# Further post-stage validation counters
# ---------------------------------------------------------------------

@pytest.mark.parametrize("sql, counter", [
    ("UPDATE committee_verdicts SET quality_run_id = 'other' WHERE run_id = :r AND ticker = 'AAA'", "wrong_quality_run"),
    ("UPDATE committee_verdicts SET valuation_run_id = 'other' WHERE run_id = :r AND ticker = 'AAA'", "wrong_valuation_run"),
    ("UPDATE committee_verdicts SET status = 'Maybe' WHERE run_id = :r AND ticker = 'AAA'", "structurally_invalid"),
    ("UPDATE committee_verdicts SET cache_key = NULL WHERE run_id = :r AND ticker = 'AAA'", "null_cache_key"),
    ("UPDATE committee_verdicts SET ticker = 'aaa' WHERE run_id = :r AND ticker = 'AAA'", "cohort_mismatch"),
    ("CREATE TABLE extra_table (x)", "schema_changed"),
    ("INSERT INTO pipeline_runs (run_id, started_at, stage_reached, status) VALUES ('stray', 'x', 'none', 'failed')",
     "unexpected_pipeline_runs"),
])
def test_post_stage_validation_counters(env, sql, counter):
    import run_pipeline as rp

    def stage(conn, run_id, cap):
        rp.run_committee_stage(conn, run_id, cost_cap_usd=cap)
        conn.execute(sql, {"r": run_id}) if ":r" in sql else conn.execute(sql)
        conn.commit()
    runner = _runner(env, _hooks(run_stage=stage))
    code = _run(runner)
    assert code == 2
    _assert_unusable(env)
    assert any(counter in line for line in runner.log_lines if "UNUSABLE" in line), runner.log_lines


# ---------------------------------------------------------------------
# Auxiliary artifacts: exclusive acquisition, ownership, marking
# ---------------------------------------------------------------------

FIXED_RUN = "20991231T000000Z"


@pytest.fixture
def fixed_run_id(monkeypatch):
    import run_pipeline as rp
    monkeypatch.setattr(rp, "new_run_id", lambda: FIXED_RUN)
    return FIXED_RUN


@pytest.mark.parametrize("name", [
    f"moat-replicate-{FIXED_RUN}.db", f"{FIXED_RUN}.stage.log",
    f"{FIXED_RUN}.runtime-provenance.json", f"{FIXED_RUN}.operator.log",
])
def test_foreign_sealed_artifact_refused_before_the_call(env, fixed_run_id, name):
    (env.out / "sealed").mkdir(parents=True)
    foreign = env.out / "sealed" / name
    foreign.write_bytes(b"foreign")
    called = []
    runner = _runner(env, _hooks(run_stage=lambda *a: called.append(a)))
    assert runner.run() == 2
    assert called == []
    assert foreign.read_bytes() == b"foreign"
    assert any("sealed_artifact_path_exists" in line for line in runner.log_lines)
    assert Path(str(_work(env)) + ".UNUSABLE").exists()
    assert not list((env.out / "sealed").glob("*.UNUSABLE*"))


@pytest.mark.parametrize("name", [
    f"moat-replicate-{FIXED_RUN}.db", f"{FIXED_RUN}.stage.log",
    f"{FIXED_RUN}.runtime-provenance.json", f"{FIXED_RUN}.operator.log",
])
def test_foreign_sealed_artifact_appearing_during_stage_is_never_overwritten(env, fixed_run_id, name):
    import run_pipeline as rp
    foreign = env.out / "sealed" / name

    def stage(conn, run_id, cap):
        rp.run_committee_stage(conn, run_id, cost_cap_usd=cap)
        foreign.parent.mkdir(parents=True, exist_ok=True)
        foreign.write_bytes(b"foreign-race")
    runner = _runner(env, _hooks(run_stage=stage))
    assert _run(runner) == 2
    assert foreign.read_bytes() == b"foreign-race"
    assert any("artifact_path_exists" in line for line in runner.log_lines)
    assert not runner.sealed
    # Nothing owned is left under a usable name; the foreign file is untouched.
    remaining = [f for f in _files(env.out) if ".UNUSABLE" not in f and not f.startswith("failed/")]
    assert remaining == [f"sealed/{name}"]
    assert not Path(str(foreign) + ".UNUSABLE").exists()


def test_foreign_working_stage_log_refused_before_the_call(env, fixed_run_id):
    foreign = env.out / "work" / f"{FIXED_RUN}.stage.log"
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(b"foreign")
    called = []
    runner = _runner(env, _hooks(run_stage=lambda *a: called.append(a)))
    assert runner.run() == 2
    assert called == []
    assert foreign.read_bytes() == b"foreign"
    assert not Path(str(foreign) + ".UNUSABLE").exists()


def test_stage_failure_marks_owned_stage_log(env, fixed_run_id):
    # The reviewer's mutation: a failed stage used to leave its stage log
    # unmarked while returning 2.
    def boom(conn, run_id, cap):
        print("partial stage output")
        raise RuntimeError("stubbed API failure")
    runner = _runner(env, _hooks(run_stage=boom))
    assert runner.run() == 2
    stage_log = env.out / "work" / f"{FIXED_RUN}.stage.log"
    assert not stage_log.exists()
    assert Path(str(stage_log) + ".UNUSABLE").read_text() == "partial stage output\n"
    assert not _work(env).exists()
    assert not any("MARK_FAILED" in line for line in runner.log_lines)


def test_failure_after_sealed_moves_marks_everything_owned(env, fixed_run_id, monkeypatch):
    def broken(*a, **k):
        raise OSError("stubbed provenance write failure")
    monkeypatch.setattr(ReplicateRunner, "_provenance", broken)
    runner = _runner(env)
    assert _run(runner) == 2
    sealed = env.out / "sealed"
    assert sorted(p.name for p in sealed.iterdir()) == [
        f"{FIXED_RUN}.stage.log.UNUSABLE", f"moat-replicate-{FIXED_RUN}.db.UNUSABLE"]
    assert not any("MARK_FAILED" in line for line in runner.log_lines)


def test_operator_log_failure_marks_provenance_too(env, fixed_run_id, monkeypatch):
    real = ReplicateRunner._write_exclusive

    def write(self, path, text):
        if path.name.endswith(".operator.log"):
            raise OSError("stubbed operator-log write failure")
        return real(self, path, text)
    monkeypatch.setattr(ReplicateRunner, "_write_exclusive", write)
    runner = _runner(env)
    assert _run(runner) == 2
    names = sorted(p.name for p in (env.out / "sealed").iterdir())
    assert names == sorted(f"{n}.UNUSABLE" for n in (
        f"moat-replicate-{FIXED_RUN}.db", f"{FIXED_RUN}.stage.log", f"{FIXED_RUN}.runtime-provenance.json"))


def test_owned_auxiliary_marking_failure_exits_3(env, fixed_run_id, monkeypatch):
    real = ReplicateRunner._atomic_mark

    def mark(p):
        if str(p).endswith(".stage.log"):
            raise OSError("stubbed mark failure")
        return real(p)
    monkeypatch.setattr(ReplicateRunner, "_atomic_mark", staticmethod(mark))
    runner = _runner(env, _hooks(run_stage=lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))))
    assert runner.run() == 3
    assert any("MARK_FAILED" in line and ".stage.log" in line for line in runner.log_lines)
    assert Path(str(_work(env)) + ".UNUSABLE").exists()


def test_owned_file_gone_missing_exits_3(env, fixed_run_id):
    def stage(conn, run_id, cap):
        (env.out / "work" / f"{run_id}.stage.log").unlink()
        raise RuntimeError("boom")
    runner = _runner(env, _hooks(run_stage=stage))
    assert runner.run() == 3
    assert any("MARK_FAILED" in line and "owned file missing" in line for line in runner.log_lines)


def test_failure_log_write_failure_exits_3(env, monkeypatch):
    real_open = os.open

    def guarded(path, flags, *a):
        if "operator-failure-" in str(path):
            raise OSError("stubbed failure-log write failure")
        return real_open(path, flags, *a)
    monkeypatch.setattr(rcr.os, "open", guarded)
    runner = _runner(env, _hooks(python_version=lambda: "3.9.6"))
    assert runner.run() == 3
