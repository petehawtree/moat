# Known issues and settled decisions

The allowlist §A19.7 anticipated (`docs/PRD_ADDENDUM.md`): everything below
has already been found, independently triaged, and either filed for later
or deliberately decided a specific way. `judge.sh`'s independent review
(`JUDGE_PROMPT.md`) checks new findings against this file before counting
them — a finding that matches an entry here is **already known**, not
**unknown breakage**, and must not by itself fail the gate. A finding that
*doesn't* match anything here is new and must be reported and counted
normally.

This file is additive-only in spirit: closing a GitHub issue removes its
entry; a new deferred defect or decision gets a new entry, not a rewrite of
an old one. Each entry names enough of the actual symptom (not just a
title) that a reviewer can match a real finding against it without having
to fetch the GitHub issue first.

## Known, filed defects (GitHub issues — not fixed yet, deliberately)

| # | Symptom | Where | Why deferred |
|---|---|---|---|
| [#1](https://github.com/petehawtree/moat/issues/1) | `total_debt IS NULL` for 18 tickers (A, ADSK, ALAB, ALNY, DDOG, DECK, DXCM, GRMN, LULU, MNST, NOW, PLTR, PM, RMD, ROL, SHOP, VRTX, WSM) reads as "no debt" but is really a missing-tag extraction gap — the debt screen metric and `ev_ebit()` both silently pass/compute on it | `moat/ingest/fundamentals_edgar.py` (debt-tag candidate list), `moat/screen/quant_screen.py`, `moat/valuation/engine.py::ev_ebit()` | Fixing the tag hierarchy needs care across the extraction pipeline; operationally excluded (`--exclude` / `config.DEBT_TAG_GAP_TICKERS`) instead of rushed |
| [#2](https://github.com/petehawtree/moat/issues/2) | AMT, SBAC (REITs) score on `gross_margin`/`free_cash_flow`/`debt` — metrics already documented as invalid for the sector (no COGS, FFO/AFFO not GAAP FCF) | `moat/screen/quant_screen.py::SECTOR_INAPPLICABLE_METRICS` doesn't yet cover Real Estate | A real FFO/AFFO-based screen is new methodology, not a bugfix; operationally excluded (`config.REIT_INVALID_METRICS_TICKERS`) |
| [#3](https://github.com/petehawtree/moat/issues/3) | GOOG/GOOGL (and FOXA/FOX, NWSA/NWS) share one CIK; the second ticker's filing lookups return "no filing" even though it's cached under the sibling ticker | `moat/ingest/filing_fetcher.py`, `moat/analysis/persist.py::find_ticker_bundle()` | Needs a CIK-keyed lookup across several call sites, not a one-line patch; GOOGL operationally excluded (`config.DUAL_CLASS_FILING_GAP_TICKERS`) from filing-dependent stages only (valuation is unaffected) |
| [#4](https://github.com/petehawtree/moat/issues/4) | 21/69 item-6 candidates still hit `full_fallback` (whole filing sent instead of targeted sections) for at least 3 distinct causes (tie-break, no-consistent-assignment, one-section-too-short) | `moat/ingest/section_extractor.py` | Each cause needs its own fix; costs more tokens per company but doesn't produce a wrong number, only a less targeted one |
| [#5](https://github.com/petehawtree/moat/issues/5) | A negative trailing owner-earnings base inverts DCF bear/base/bull ordering (bull reads as the worst case) — confirmed on ABNB, CRWD, EIX, PEG, UBER | `moat/valuation/engine.py::dcf_scenario()` | `margin_of_safety()`'s sign guard still prevents a false-positive verdict; this is a misleading range label, not a misleading recommendation |
| [#6](https://github.com/petehawtree/moat/issues/6) | `_cagr()` in the quant screen guards a non-positive *first* value but not the *last* one — a non-positive ending revenue/share-count produces a complex number, then a `TypeError` on the next comparison | `moat/screen/quant_screen.py::_cagr()` | Same bug class already fixed in `valuation/engine.py::historical_revenue_cagr()`, just never carried over; not currently reachable (no ticker's latest fiscal year has non-positive revenue today) |
| [#7](https://github.com/petehawtree/moat/issues/7) | The committee's cache key hashes `ai_analysis`/`valuations` content but not `quant_scores`/`quality_scores` — a screen-only re-run can serve a stale committee verdict | `moat/committee/committee.py::compute_committee_bundle_key()` | Sprint 5's own in-progress code; a contained fix (widen the hashed payload), not urgent since nothing is silently wrong-but-undetectable — the stale verdict is still internally consistent, just not current |

## Known, deliberate design decisions (not defects)

| Decision | What it looks like to flag | Why it's not a defect |
|---|---|---|
| §A19.6 / sprint-5-plan.md decision 1: a Quality/Bear STATEMENT with no `[refs: N]` is allowed, not rejected — shown with an explicit "no citation" caption instead | "Briefs accept and display uncited AI claims" | The chosen entailment control is *surface support for a human to judge*, not *build an automated citation-enforcement gate* — explicitly decided, not discovered after the fact. An unreferenced statement is visibly flagged as such (`moat/dashboard/app.py::_render_persona_response`), never presented as equivalent to a cited one. |
| Valuation Analyst statements never carry `[refs: N]` — they're grounded by the CONTEXT block's own visible quant/DCF figures, not a claim id (`prompt.py` rule 1) | "Valuation claims lack filing references" | By design: there's no claim_id mechanism for quantitative figures at all; the grounding is that the number appears in the context block a reader can check directly. |
| The committee/ai_analysis cost cap is checked before each API call, not reserved/preflighted — a company already in flight can push spend slightly past the cap | "Cost cap is not a hard ceiling" | Matches `moat/analysis/persist.py`'s `run_ai_analysis_stage()`'s identical, already-shipped pattern since Sprint 3 — never flagged as a defect for that stage. If worth hardening, it's a cross-cutting change to both AI-calling stages, decided together, not a committee-specific bug. |
| Sprint 5's real pilot run is intentionally partial (21/69 companies) and `assign_status()`'s 70/50 thresholds are explicitly starting values, not yet pilot-validated | "Sprint 5 is not release-ready" / "thresholds unvalidated" | True, and already the documented state — see README's Sprint 5 status block and `sprint-5-plan.md`'s own Definition of Done. Informational, not a current-scope defect: nothing claims this is finished. |
| `ai_analysis`/`valuation` stages' known-bad-ticker exclusion (`--exclude`) stays opt-in; only the `committee` stage defaults to excluding them (Sprint 5 C8) | "Known-bad tickers aren't excluded by default everywhere" | Deliberate, confirmed decision (sprint-5-plan.md "Open for discussion" #1): continue deferring a wider default-exclusion change across all three stages; committee's own default was scoped to committee specifically since it's the stage producing a ranked recommendation. |
| Financials are excluded from `gross_margin`/`free_cash_flow`/`debt` scoring (§A14); a company with `assessed < 6/8` metrics can't pass the screen regardless of composite score | "Financials score suspiciously low" / "some metrics always unavailable for a sector" | Definitional exclusion — a bank has no COGS, and operating cash flow isn't FCF for a bank — not a data gap. `SECTOR_INAPPLICABLE_METRICS` marks it `not_applicable`, distinct from `unavailable`. |
| 91/91 `passed_screen` companies report `pe_historical_range.low_confidence = True` | "P/E range isn't useful" | Honestly flagged, not hidden (§A20/§A21) — the known price-history-backfill gap, an open decision carried into Sprint 5 planning, not silently wrong output. |

## How to use this file (for the judge, and for humans)

A finding matches an entry here if the **underlying symptom** matches, even
if the wording, severity label, or exact file/line differs — match on
behavior, not on string similarity to a title. When in doubt, prefer
treating a finding as *not* matched (report it as new) over suppressing a
genuinely different problem that happens to touch the same file.

Closing the loop: when a GitHub issue here gets fixed, remove its row in
the same commit that closes it. When a new judge run turns up a real,
previously-unknown defect that's being deferred rather than fixed
immediately, it gets filed as a GitHub issue and added here — not left to
be re-discovered (and re-argued) on every future run.
