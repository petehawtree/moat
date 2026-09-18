# Judge reports

**Start here: [judge-report-sprint-5-first-clean-run-20260918-143831.md](judge-report-sprint-5-first-clean-run-20260918-143831.md)**
— `PASS WITH CONCERNS`, 0 Critical, 0 High, 0 Medium, 2 Low. The first
report this independent review has ever returned that wasn't `FAIL`.

## Why every other report here says FAIL

Every report before the one above does say `FAIL` — that's real, not a
typo repeated 14 times. But it isn't 14 independent instances of broken
code either. Each `FAIL` mostly re-discovered and re-reported the same
small set of already-known, already-filed, deliberately-deferred defects
(see [`docs/known-issues.md`](../known-issues.md) — currently
[GitHub issues #1, #2, #3, #4, #5, #6](https://github.com/petehawtree/moat/issues))
and a few already-documented design decisions the review kept mistaking
for bugs, because the reviewer had no way to tell "known and tracked"
apart from "unknown breakage" until `docs/known-issues.md` and the
allowlist mechanism in `JUDGE_PROMPT.md` existed (`docs/PRD_ADDENDUM.md`
§A24). The known issues are genuinely excluded from production use
(operational `--exclude`, sector-inapplicable-metric marking) — they
don't reach a real ranking or recommendation, they just hadn't been
fixed yet.

The clean report above is the first one produced *with* that allowlist in
place, and it found two genuinely new defects along the way (both fixed
the same session — see its own commit history) before coming back clean.
It's evidence the review can pass, not evidence nothing was ever wrong.

## Convention

Reports are archived here (`docs/PRD_ADDENDUM.md` §A19.4) in the same
commit that references them, named
`judge-report-sprint-<N>-<timestamp-or-slug>.md`. `.judge/` itself is
gitignored — see §A19.7 for why the review runs automatically (and
advisory-only) on every push to `main`.
