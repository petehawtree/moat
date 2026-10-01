# Sprint 6.0 candidate runs: provenance

Run ids, timestamps and statuses below were checked read-only against
`pipeline_runs` in `/Users/pete/moat/data/moat.db`. Commits were checked with
`git cat-file -t`. Other facts (costs, counts, notes) come from the executing
session and were not re-derived. Driver scripts are in
`scripts/sprint6_0_drivers/` (see its README for SHA-256s and in-place edits).

## Commits (all verified to exist)

| Commit | Committer time | Subject |
|---|---|---|
| `c0ba033` | 2026-09-29 10:39:47 +01:00 (09:39:47Z) | Fix #1: tiered total_debt hierarchy universe-wide |
| `a71b1e4` | 2026-09-29 14:54:01 +01:00 (13:54:01Z) | Fix #13 in the Sprint 6.0 re-run; record operating_margin as definitional |
| `79450dd` | 2026-09-29 15:05:05 +01:00 (14:05:05Z) | Exclude HST as an invalid-metrics REIT (A17, GitHub #2) |
| `42bf3bb` | 2026-10-01 10:57:55 +01:00 | Merge pull request #22 (sprint-6.0) |

## Candidate runs

"Code" is the commit checked out in the sprint-6.0 worktree
(`/Users/pete/moat-sprint-6.0`).

| Stage / run id | Code | Python env | Driver | DB status | Notes |
|---|---|---|---|---|---|
| backup | n/a | n/a | sqlite `.backup` | n/a | `data/moat-baseline-pre-sprint6.0.db` taken 2026-09-29 before the ingest |
| ingest `20260929T134512Z` | `c0ba033` | py3.9 shared `.venv` | `ingest_only.py` | complete / ingest | Ingest stage only. Fundamentals from cached SEC companyfacts (none past the 90-day refresh threshold); prices live from yfinance |
| screen + quality `20260929T135425Z` | `a71b1e4` | py3.9 | `screen_quality.py` | partial / quality | Closed as `partial`/`quality` so valuation reads this run's pass set |
| valuation `20260929T135455Z` | `a71b1e4` | py3.9 | `valuation_only.py` | partial / valuation | 122/122 valued, all prices dated 2026-09-29 |
| AI dry run `20260929T140509Z` | `79450dd` (see note 1) | py3.9 | `ai_stage.py dry` | failed / none | API 503 "credential validation failed"; $0 |
| AI dry run (second) `20260930T092636Z` | `79450dd` | py3.9 | `ai_stage.py dry` | partial / ai_analysis | Token counts only; $0. Id taken from `pipeline_runs` and the run's log (excluded AMT, CCI, GOOGL, HST; 118 tickers from quality run `20260929T135425Z`) |
| AI cache probe `20260930T092839Z` | `79450dd` | py3.9 | `ai_cache_probe.py` | partial / ai_analysis | API stubbed offline; 75 cache hits, 43 misses; $0 |
| AI batch `20260930T093157Z` | `79450dd` | py3.9 (anthropic 0.121) | `ai_stage.py batch`, then `ai_retrieve.py` | partial / ai_analysis | Batch `msgbatch_01AD2bSHucP1Vc29SrsACDv3`; 41 persisted, 2 validation_failed (BR, VRT); $6.85 |
| AI retry `20260930T122917Z` | `79450dd` | py3.9 | `ai_retry.py` | partial / ai_analysis | BR and VRT only, sync; both persisted; $0.64 |
| committee `20260930T123119Z` | `79450dd` | py3.9 (anthropic 0.121) | `committee.py` (cap 9) | failed / none | Failed at once: anthropic 0.x rejects the `httpx2.Timeout` client timeout (TypeError surfaced as "Connection error"); 0 verdicts; status set to failed manually; $0 |
| committee `20260930T123721Z` | `79450dd` | py3.11 worktree `.venv` (anthropic 1.9.0) | `committee.py` (cap 9) | failed / none | A to Z; stopped by the org API usage limit; 36 verdicts |
| committee `20260930T125628Z` | `79450dd` | py3.11 | `committee_reverse.py` (MAIN `20260930T123721Z`, cap 4.50) | failed / none | Z to A worker; stopped by the same limit; 17 verdicts |
| committee `20260930T133528Z` | `79450dd` | py3.11 | `committee.py` (cap 5) | partial / committee | **The authoritative candidate committee run**: 117 verdicts (84 cache hits, 33 new). Its cache hits come from runs marked failed or partial |
| committee `20260930T133537Z` | `79450dd` | py3.11 | `committee_reverse.py` (MAIN `20260930T133528Z`, cap 2.5) | partial / committee | Z to A worker for the final run; stopped at META on meeting the main run; its verdicts were copied into `20260930T133528Z` |

Notes:

1. The first AI dry run started 2026-09-29T14:05:09Z, four seconds after
   `79450dd` was committed (14:05:05Z; the worktree reflog agrees). By timestamp
   the worktree was at `79450dd`, not `a71b1e4`. This is inferred from
   timestamps; the commit was not recorded by the run itself. The run failed
   on an API error, so the distinction does not affect any result.
2. Run ids are verified against the DB. In the DB query (`run_id >= '20260929T13'`)
   the only extra rows were the older `item6_batch_20260909` and
   `minipilot_20260909`, which sort after the date prefix lexically and are not
   Sprint 6.0 runs. No other Sprint 6.0-window run is missing from the table.

## Worktree state

During these stages the sprint-6.0 worktree had no uncommitted changes to
tracked files. Its `data/filings`, `data/sec_company_tickers.json` and `.env`
were symlinks to `/Users/pete/moat`, so it used the shared SEC cache and the
API key.

## Environments

- `/Users/pete/moat/.venv`: Python 3.9.6 (anthropic 0.121.0). Freeze:
  `docs/evals/sprint-6.0-env-py39.txt` (`pip freeze`).
- `/Users/pete/moat-sprint-6.0/.venv`: Python 3.11.16 (anthropic 1.9.0). Built
  2026-09-30 with `uv venv --python 3.11 .venv && uv pip install -r requirements.txt`.
  Freeze: `docs/evals/sprint-6.0-env-py311.txt`
  (`uv pip freeze --python .../.venv/bin/python`).

The freezes were taken on 2026-10-01, after the runs. They show the environments
as they are now, not a record made at run time.

## Baseline run set (provenance not recorded)

Baseline runs: screen `20260923T122501Z`, valuation `20260923T122513Z`,
committee `20260923T153351Z` (all verified in `pipeline_runs`; statuses
partial / valuation, partial / valuation, partial / committee).

The production commit and environment of these runs were **not recorded** at the
time. Reconstructed, **unverified** best estimate: the commit on `origin/main`
at or just before each run's `started_at` (`git log --until=<started_at> -1 origin/main`):

| Run | started_at (UTC) | Reconstructed commit |
|---|---|---|
| screen `20260923T122501Z` | 2026-09-23T12:25:01Z | `252348c` (Ingest: recover capex/D&A from tags the ingest didn't try, GitHub #9) |
| valuation `20260923T122513Z` | 2026-09-23T12:25:13Z | `252348c` |
| committee `20260923T153351Z` | 2026-09-23T15:33:51Z | `7dae6d6` (AI analysis: unique batch custom_id per submission) |

These are guesses. The shared checkout may have had uncommitted changes, and
other sessions were active. Python 3.9 / anthropic 0.x is likely but also
unverified.

## Binding run sets to DB snapshots

Run ids alone do not reproduce fundamentals: `fundamentals_annual` is
overwritten in place. Each run set is bound to a DB snapshot:

- Baseline: `data/moat-baseline-pre-sprint6.0.db` (taken 2026-09-29 before the
  ingest). SHA-256: not recorded here.
- Candidate: `data/moat-candidate-sprint6.0.db`
  - Path: TODO (a separate task is producing this snapshot)
  - SHA-256: TODO
