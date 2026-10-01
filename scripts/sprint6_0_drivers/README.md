# Sprint 6.0 driver scripts

These are the one-off drivers that ran individual pipeline stages for Sprint 6.0,
instead of `scripts/run_pipeline.py --from-stage`, which runs every later stage
including the paid committee.

They hard-code absolute paths and run ids, and are kept as provenance, not as
reusable tools. Before reaching the Sprint 6.0 code, `ingest_only.py` inserts
`/Users/pete/moat-sprint-6.0` (the sprint-6.0 worktree) onto `sys.path`.

See `docs/evals/sprint-6.0-run-provenance.md` for which driver produced which run.

## Final versions only

Three files were edited in place between runs, so the committed versions are the
**final** versions only:

- `ai_stage.py`: `cost_cap_usd` was 8.0 for the dry runs and 10.0 for the batch run.
- `committee.py`: `cost_cap_usd` was 9.0 for runs `20260930T123119Z`,
  `20260930T123721Z` and `20260930T125628Z`, and 5.0 for the final run
  `20260930T133528Z`.
- `committee_reverse.py`: `MAIN` and `CAP` were `20260930T123721Z` / 4.50 for run
  `20260930T125628Z`, then `20260930T133528Z` / 2.50 for run `20260930T133537Z`.

## SHA-256 (as committed, byte-for-byte copies)

```
bc02fd7e3e8eca3ece834c63d27982303c1180c0d8ab090ea90bcd254ec6be6a  ingest_only.py
829be8f0a07497fffbedd980b1949cd4a9b50e55c119fec578ee263479d1394d  screen_quality.py
6d12c214f28a2c53bd23cc46e24cd8bfde29465e6addbf08ee94fb58ca5e609b  valuation_only.py
9633f01444337e862367d3136193ba7ae3f62f5c2b5204cb89b3b79f39ebdf9d  ai_stage.py
917c272fa4c077b1fec19b19b7d4d808e6f013957cee2cb648dca86202ad41ea  ai_cache_probe.py
7a3100397d09ac55795842d9309434de5a36621703b712b3864ed0bbf7a9827a  ai_retrieve.py
aaf19bdd1531b9c607176350485144c3d12291f8a205da8ff7133ba2b09b52ca  ai_retry.py
fa04ca2274efcbef71ce80905828dd10ce5045465289e24dbceb8aa83d91bef3  committee.py
6912d3b292ad5aa0e1183c02c0cd1223366855a5ea82e1dd4368b462833eec7d  committee_reverse.py
df99d6b44d22debd6ec54ef184fdabcbc416c440439572c7da4c59efd5e1d67f  diff.py
```
