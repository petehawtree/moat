#!/usr/bin/env python3
"""Single source of truth for every headline number the README quotes.

Phase 1's evidence audit (see the README restructure commit) found a stale
test count and a superseded screen-pass figure both sitting in the README,
two sections apart, because nothing kept prose numbers in sync with the
repo or the database. `docs/metrics.json` is the committed record now —
every metric carries `value`, `as_of`, and `source` — and this script is
the only thing that writes it.

    python scripts/metrics.py --refresh   # recompute what's reachable, rewrite the json
    python scripts/metrics.py --render    # inject values into README.md between metrics markers
    python scripts/metrics.py --check     # --render in memory; exit non-zero if README.md disagrees

Metrics fall into four kinds:

  - repo    Computable from the working tree alone (git, pytest, gh CLI).
            Always recomputed on --refresh. `gh` is best-effort: if it's
            missing or unauthenticated, the GitHub-issue metrics are
            skipped like a `db` metric would be.
  - db      Needs data/moat.db, which is gitignored and not always present
            (a fresh clone, CI, a machine that never ran the pipeline).
            Recomputed only when the file exists; otherwise the existing
            value and as_of are left untouched and the skip is printed.
            NEVER zeroed for a missing DB — that's Sprint 2.2's "missing
            data treated as failure" bug, repeated here against this
            project's own numbers, and exactly what this script exists to
            prevent.
  - manual  Not recoverable from any automated source. The Sprint 5
            committee pilot's spend is real API cost the committee stage
            computes in memory but never persists (committee_verdicts has
            no cost column — confirmed against moat/db/schema.sql).
            --refresh never touches these; edit docs/metrics.json by hand
            and say why in `source`.
  - derived Arithmetic over other metrics' current values in the json, so
            it inherits whatever staleness its inputs already have.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from moat.db.connection import DB_PATH, get_connection  # noqa: E402

METRICS_PATH = ROOT / "docs" / "metrics.json"
README_PATH = ROOT / "README.md"
CASE_STUDY_PATH = ROOT / "docs" / "case-study.md"
MARKER_START = "<!-- metrics:start -->"
MARKER_END = "<!-- metrics:end -->"

TODAY = date.today().isoformat()


def _run(cmd: list[str]) -> str | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, timeout=120).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


# --- repo metrics -----------------------------------------------------

def compute_commits() -> int | None:
    out = _run(["git", "rev-list", "--count", "HEAD"])
    return int(out.strip()) if out and out.strip().isdigit() else None


def compute_tests() -> tuple[int, int] | None:
    """(collected, passing) — two separate pytest invocations, since a
    --collect-only run doesn't execute anything and a normal run's summary
    line doesn't say how many were collected before dedup/filtering."""
    collect_out = _run([sys.executable, "-m", "pytest", "--collect-only", "-q"])
    run_out = _run([sys.executable, "-m", "pytest", "-q"])
    if collect_out is None or run_out is None:
        return None
    collected_match = re.search(r"(\d+) tests? collected", collect_out)
    passed_match = re.search(r"(\d+) passed", run_out)
    if not collected_match or not passed_match:
        return None
    return int(collected_match.group(1)), int(passed_match.group(1))


def compute_github_issues() -> tuple[int, int] | None:
    out = _run(["gh", "issue", "list", "--state", "all", "--limit", "500", "--json", "state"])
    if not out:
        return None
    try:
        issues = json.loads(out)
    except json.JSONDecodeError:
        return None
    open_n = sum(1 for i in issues if i.get("state") == "OPEN")
    return open_n, len(issues)


# --- db metrics ---------------------------------------------------------

def _db_conn():
    if not DB_PATH.exists():
        return None
    return get_connection()


def compute_db_metrics(conn) -> dict[str, int | float]:
    out: dict[str, int | float] = {}
    out["universe_companies"] = conn.execute(
        "SELECT COUNT(*) AS n FROM companies WHERE is_active = 1"
    ).fetchone()["n"]
    out["fundamentals_coverage"] = conn.execute(
        "SELECT COUNT(DISTINCT ticker) AS n FROM fundamentals_annual"
    ).fetchone()["n"]
    out["filings_rows"] = conn.execute("SELECT COUNT(*) AS n FROM filings").fetchone()["n"]
    out["citations_total"] = conn.execute("SELECT COUNT(*) AS n FROM citations").fetchone()["n"]
    out["ai_analyses_companies"] = conn.execute(
        "SELECT COUNT(DISTINCT ticker) AS n FROM ai_analysis WHERE is_current = 1"
    ).fetchone()["n"]

    # Same "latest non-failed run with quality_scores" shape as the
    # dashboard's own `latest_run` query (moat/dashboard/app.py), so the
    # README and the app can't disagree about which run is current.
    latest_run = conn.execute(
        """
        SELECT run_id FROM pipeline_runs
        WHERE run_id IN (SELECT DISTINCT run_id FROM quality_scores)
          AND status != 'failed'
        ORDER BY started_at DESC LIMIT 1
        """
    ).fetchone()
    if latest_run is not None:
        row = conn.execute(
            "SELECT COUNT(*) AS total, SUM(passed_screen) AS passed FROM quality_scores WHERE run_id = ?",
            (latest_run["run_id"],),
        ).fetchone()
        out["screen_total"] = row["total"]
        out["screen_passed"] = row["passed"] or 0

    spend = conn.execute("SELECT SUM(cost_estimate) AS s FROM analysis_attempts").fetchone()["s"]
    if spend is not None:
        out["ai_spend_analysis_stage"] = round(spend, 3)
    return out


# --- registry -------------------------------------------------------------

REPO_METRICS = {
    "commits": (compute_commits, "git rev-list --count HEAD"),
}

DB_METRIC_SOURCES = {
    "universe_companies": "SELECT COUNT(*) FROM companies WHERE is_active=1 (data/moat.db)",
    "fundamentals_coverage": "SELECT COUNT(DISTINCT ticker) FROM fundamentals_annual (data/moat.db)",
    "filings_rows": "SELECT COUNT(*) FROM filings (data/moat.db)",
    "citations_total": "SELECT COUNT(*) FROM citations (data/moat.db)",
    "ai_analyses_companies": "SELECT COUNT(DISTINCT ticker) FROM ai_analysis WHERE is_current=1 (data/moat.db)",
    "screen_total": "latest quality_scores run, same query as the dashboard's `latest_run` (data/moat.db)",
    "screen_passed": "SUM(passed_screen) over the latest quality_scores run (data/moat.db)",
    "ai_spend_analysis_stage": "SUM(cost_estimate) over analysis_attempts, all outcomes — real dollars spent even on a validation_failed attempt (data/moat.db)",
}

MANUAL_METRICS = {"ai_spend_committee_pilot"}

DERIVED_METRICS = {"ai_spend_total"}


def load_metrics() -> dict:
    if METRICS_PATH.exists():
        return json.loads(METRICS_PATH.read_text())
    return {}


def _set(metrics: dict, key: str, value, source: str) -> None:
    metrics[key] = {"value": value, "as_of": TODAY, "source": source}


def refresh(metrics: dict) -> dict:
    commits = compute_commits()
    if commits is not None:
        _set(metrics, "commits", commits, REPO_METRICS["commits"][1])
    else:
        print("skip: commits — `git` unavailable")

    tests = compute_tests()
    if tests is not None:
        collected, passing = tests
        _set(metrics, "tests_collected", collected, "pytest --collect-only -q")
        _set(metrics, "tests_passing", passing, "pytest -q summary line")
    else:
        print("skip: tests_collected/tests_passing — pytest run failed or produced unparseable output")

    issues = compute_github_issues()
    if issues is not None:
        open_n, total_n = issues
        _set(metrics, "github_issues_open", open_n, "gh issue list --state all")
        _set(metrics, "github_issues_total", total_n, "gh issue list --state all")
    else:
        print("skip: github_issues_open/total — `gh` unavailable, unauthenticated, or rate-limited")

    conn = _db_conn()
    if conn is None:
        print(f"skip: {', '.join(sorted(DB_METRIC_SOURCES))} — data/moat.db not present")
    else:
        try:
            db_values = compute_db_metrics(conn)
        finally:
            conn.close()
        for key, value in db_values.items():
            _set(metrics, key, value, DB_METRIC_SOURCES[key])
        missing = set(DB_METRIC_SOURCES) - set(db_values)
        for key in missing:
            print(f"skip: {key} — not computable from the current schema/data")

    # Manual metrics are never touched here; just confirm they still exist.
    for key in MANUAL_METRICS:
        if key not in metrics:
            print(f"warning: manual metric '{key}' has no entry in docs/metrics.json — add one by hand")

    if "ai_spend_analysis_stage" in metrics and "ai_spend_committee_pilot" in metrics:
        total = metrics["ai_spend_analysis_stage"]["value"] + metrics["ai_spend_committee_pilot"]["value"]
        as_ofs = [metrics["ai_spend_analysis_stage"]["as_of"], metrics["ai_spend_committee_pilot"]["as_of"]]
        _set(
            metrics, "ai_spend_total", round(total, 2),
            "ai_spend_analysis_stage + ai_spend_committee_pilot (oldest input: " + min(as_ofs) + ")",
        )

    return metrics


# --- rendering --------------------------------------------------------

def render_block(metrics: dict) -> str:
    def v(key, default="—"):
        return metrics[key]["value"] if key in metrics else default

    collected, passing = v("tests_collected"), v("tests_passing")
    if isinstance(collected, int) and isinstance(passing, int):
        skipped = collected - passing
        tests_line = f"{collected} tests, {passing} passing" + (
            f" ({skipped} opt-in live-API test)" if skipped > 0 else ""
        )
    else:
        tests_line = "test count unavailable"

    issues_line = (
        f"{v('github_issues_total')} GitHub issues filed, {v('github_issues_open')} open — tracked, not hidden"
        if "github_issues_total" in metrics else "GitHub issue count unavailable"
    )

    citations = v("citations_total")
    citations_str = f"{citations:,}" if isinstance(citations, int) else str(citations)
    spend = v("ai_spend_total")
    spend_str = f"~${spend:,.1f}" if isinstance(spend, (int, float)) else "spend unavailable"

    lines = [
        MARKER_START,
        "| | | |",
        "|---|---|---|",
        f"| {v('commits')} commits | {v('universe_companies')}-company universe, "
        f"{v('fundamentals_coverage')} with fundamentals | {v('screen_passed')}/{v('screen_total')} pass the quant screen |",
        f"| {v('ai_analyses_companies')} companies fully AI-analyzed, {citations_str} citations | "
        f"{spend_str} total AI spend across every pilot to date | {tests_line} |",
        f"| {issues_line} | | |",
        "",
        "Sources: commit count is `git rev-list --count HEAD`. Universe/fundamentals "
        "coverage and the screen result are cross-checked live against `data/moat.db` "
        "(Sprint 1 / Sprint 3.1 retros give the history). The AI-analyzed/citation figures "
        "are the Sprint 3.1 batch run. Spend is the analysis-stage total from "
        "`analysis_attempts.cost_estimate` (data-derived, all outcomes — a failed attempt "
        "still cost real money) plus the Sprint 5 committee pilot's spend, which the "
        "committee stage computes but doesn't persist, so that one figure stays "
        "hand-maintained (see `docs/metrics.json`). Test count is `pytest`'s own "
        "collection, run against `HEAD`. Issue count is "
        "[GitHub Issues](https://github.com/petehawtree/moat/issues). "
        f"Generated by `scripts/metrics.py --render`, {TODAY}.",
        MARKER_END,
    ]
    return "\n".join(lines)


def render_into(path: Path, block: str) -> str | None:
    if not path.exists():
        return None
    text = path.read_text()
    if MARKER_START not in text or MARKER_END not in text:
        return None
    pattern = re.compile(re.escape(MARKER_START) + r".*?" + re.escape(MARKER_END), re.DOTALL)
    return pattern.sub(block, text, count=1)


def cmd_refresh() -> int:
    metrics = load_metrics()
    metrics = refresh(metrics)
    METRICS_PATH.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n")
    print(f"wrote {METRICS_PATH.relative_to(ROOT)}")
    return 0


def cmd_render() -> int:
    metrics = load_metrics()
    if not metrics:
        print("docs/metrics.json is empty — run --refresh first", file=sys.stderr)
        return 1
    block = render_block(metrics)
    wrote_any = False
    for path in (README_PATH, CASE_STUDY_PATH):
        new_text = render_into(path, block)
        if new_text is None:
            print(f"skip: {path.relative_to(ROOT)} — no file, or no metrics markers")
            continue
        path.write_text(new_text)
        print(f"rendered into {path.relative_to(ROOT)}")
        wrote_any = True
    return 0 if wrote_any else 1


def cmd_check() -> int:
    metrics = load_metrics()
    if not metrics:
        print("docs/metrics.json is empty — nothing to check against", file=sys.stderr)
        return 1
    block = render_block(metrics)
    ok = True
    for path in (README_PATH, CASE_STUDY_PATH):
        if not path.exists():
            continue
        current = path.read_text()
        if MARKER_START not in current:
            continue
        expected = render_into(path, block)
        if expected != current:
            print(f"MISMATCH: {path.relative_to(ROOT)} does not match docs/metrics.json — "
                  f"run `python scripts/metrics.py --render` and commit the result")
            ok = False
    if ok:
        print("metrics blocks match docs/metrics.json")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--refresh", action="store_true", help="recompute what's reachable, rewrite docs/metrics.json")
    group.add_argument("--render", action="store_true", help="inject docs/metrics.json values into metrics-marker blocks")
    group.add_argument("--check", action="store_true", help="exit non-zero if a rendered file disagrees with docs/metrics.json")
    args = parser.parse_args()

    if args.refresh:
        return cmd_refresh()
    if args.render:
        return cmd_render()
    return cmd_check()


if __name__ == "__main__":
    raise SystemExit(main())
