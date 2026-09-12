# Sprint 3 — AI qualitative analysis, citation-enforced

**Status:** Done | **Plan:** [sprint-3-plan.md](sprint-3-plan.md)

## Goal

Generate four cited analyses per company (business quality, moat, management,
risk) from each company's 10-K — every claim traceable to an exact quote in
an immutable local copy of the filing, with no partial company state and no
silent re-spend on an unchanged filing.

## What shipped

- **W1 — `moat/ingest/filing_fetcher.py`.** Resolves the primary 10-K/10-K/A
  document from SEC EDGAR submissions, fetches and stores it immutably
  outside the existing companyfacts cache, records source URL/CIK/retrieval
  time, re-reads bytes on a second call rather than refetching.
- **W2 — `moat/ingest/section_extractor.py`.** Item 1/1A/7 extraction with
  written, pre-registered confidence rules
  ([sprint-3-section-extraction-rules.md](sprint-3-section-extraction-rules.md))
  rather than a percentage target decided after the fact. Every candidate,
  its rejection reason, the chosen span, and the boundary that closed it are
  recorded in `filing_documents.extraction_trace` — observable, not a black
  box.
- **W3 — `moat/analysis/prompt.py` + `caller.py`.** One combined
  citation-enabled request per company over plain-text document blocks
  (plain text is what returns `char_location` citations); `--dry-run`
  reports measured tokens against the pricing snapshot in use; `--batch`
  submission path for the eventual 90-company run.
- **W4 — `moat/analysis/parser.py`.** Parses claims from interleaved
  cited/non-cited response blocks, maps document slot → accession, checks
  byte-equality between a citation's `cited_text` and the source document,
  computes claim coverage (asserted claims cited ÷ asserted claims).
- **W5 — `moat/analysis/persist.py`.** One transaction per company; bundle
  cache key over sorted doc hashes + prompt hash + model + normalizer +
  protocol version; cache hits copy forward with `reused_from_run_id` and
  make zero API calls.
- **W6 — `scripts/cite.py`.** The recall surface: every claim with its
  quote (rendered from the local receipt, not re-fetched), accession, filing
  date, both EDGAR URLs; `--filing ACCN` inverts the view; `--reanchor` runs
  the resolution ladder and writes `citation_resolution_events`.
- **W7 — `scripts/run_pipeline.py`.** `ai_analysis` stage wired in, reading
  the latest **successful** `quality_scores` run rather than the run
  `main()` just created (which has no `passed_screen` rows yet on
  `--from-stage ai`).
- **114 tests, up from 25 before this sprint (89 new)**: 8 golden extraction
  fixtures, a full parser suite for cited/non-cited/malformed response
  shapes, a filing-fetcher suite, and cache-hit/orphaning/floor-enforcement
  regressions from external judge review (below).

## Results — pilot of three deliberately varied filers

Per the plan's Decision 1: the remaining ~90 companies are authorized only
after a human reads the pilot output. Ran synchronously, under the $15
evaluation cap:

| Ticker | Role | Extraction | Input tok | Cost | Claims | Citations |
|---|---|---|---|---|---|---|
| AAPL | conventional issuer | `sections` (all high) | 29,000 | $0.123 | 34 | 33 |
| KO | inline-XBRL-heavy | `sections` (all high) | 72,641 | $0.253 | 34 | 35 |
| JPM | very long risk factors | `full_fallback` | 495,725 | $1.529 | 35 | 31 |

**Spend: $1.905 of the $15.00 cap.** All 12 analyses `persisted`,
`claim_coverage = 1.0`, zero validation errors. 103 claims total (91
asserted, 12 `INSUFFICIENT EVIDENCE`), 99 citations.

**`--reanchor` baseline: 99/99 citations exact (100%)** — the byte-equality
check re-reads each local receipt, confirms `doc_sha256` is unchanged, and
compares `text[start:end]` against the stored quote character-for-character.
This is the fresh-corpus baseline the plan asked for; future drift shows up
as a departure from 100%, not as a surprise.

**Human read of the 12 analyses: passed.** Claims were specific, correctly
attributed, and included real negative signal where the filing had it — not
just favorable claims cherry-picked (AAPL's minority device-market-share
admission inside its own moat section, KO's back-to-back BodyArmor
impairments, JPM's Russia sanctions litigation).

## What we found

**External judge review, across five rounds (2 through 6), fixed real
defects before the pilot ever ran** — cache-hit orphaning, quality-run
selection picking the wrong `run_id`, full-fallback floor enforcement,
cache-hit audit receipts, and (Round 6) a `gap_sections` cache-key
mismatch, a stale-row bypass in `prepare_sections()`, a diluted
full-fallback rate denominator, and a `toc_cluster` false positive on
inline cross-references. Full detail is in the commit history (`Sprint 3
Round 4/5/6` commits and the two preceding un-numbered review-fix commits)
rather than repeated here. The five underlying `./judge.sh` reports for
this sprint (2026-09-07/08, `.judge/` is gitignored — see Sprint 3.1's
"Judge review" section for why) are archived, untouched, as
[`docs/judge-reports/judge-report-20260907-131641.md`](../judge-reports/judge-report-20260907-131641.md),
[`…144449.md`](../judge-reports/judge-report-20260907-144449.md),
[`…182737.md`](../judge-reports/judge-report-20260907-182737.md),
[`…184150.md`](../judge-reports/judge-report-20260907-184150.md), and
[`judge-report-20260908-113305.md`](../judge-reports/judge-report-20260908-113305.md) —
not individually mapped to round numbers above; that mapping isn't
recorded anywhere and reconstructing it from report content alone would be
a guess.

**The pilot itself found something the review rounds didn't: section
extraction had likely never worked on a real filing.** Dry-running the
pilot's three tickers before spending anything showed all three resolving
to `full_fallback` — not the mix the design assumes. Root cause:
`_find_end()` used every raw boundary-section match (`item_1b`/`1c`/`2`/
`7a`/`8`) unfiltered to decide where a primary section ends. Nearly every
real 10-K's Item 7 opens with boilerplate that names Item 8 inline —
*"...included in Part II, Item 8 of this Form 10-K"* (AAPL, verbatim),
*'...contained in "Item 8. Financial Statements..." of this report'* (Coca-
Cola, verbatim) — and that inline mention was mistaken for the real Item 8
heading, truncating Item 7 to a few hundred characters and failing its
hard floor. Two fixes: boundary positions now go through the same
cross-reference filter already applied to primary candidates, and that
filter now strips a trailing quote mark before checking what precedes it (a
quoted title reference has the quote character, not a comma or letter, as
the last char before the item number — the existing heuristic missed it).
Verified against AAPL's and Coca-Cola's actual 10-Ks: both now resolve to
`sections` with all three items `high`, down from `full_fallback` on the
whole document — input tokens dropped 60% (AAPL: 71,731 → 29,000) and 66%
(KO: 215,032 → 72,641). Added as fixture 8 in the golden test suite. This
is a real correctness fix, not a pilot-ticker-selection issue — the pattern
it fixes is close to universal boilerplate across 10-Ks.

**Known gap, deferred to Sprint 3.1: `toc_cluster` misreads a real
"incorporated by reference" run as a table of contents.** JPMorgan Chase's
real Item 7 is a one-paragraph IBR stub (the real MD&A is in a separate
exhibit, pages 46–160), immediately followed by equally short IBR stubs for
7A/8/9/9A/9B within ~3,400 chars — the same density signature the filter
uses to detect a real ToC. A backward-looking window was tried as a fix and
reverted: it regressed AAPL and KO, because ordinary short boundary
sections (Item 1B, Item 2) sit just as close to a normal filer's real Item
7. **Deferred, zero current impact** — the 93-company `passed_screen` set
(run `20260822T140041Z`) has no Financials tickers at all, by Sprint 2.2's
deliberate exclusion of financials as unscreenable ([PRD_ADDENDUM.md
§A14](../PRD_ADDENDUM.md#a14-sprint-22-follow-up--status-inversion-and-sector-applicability)),
not an artifact of this run. JPM was a deliberately-chosen pilot stress
case, not a production company. Full detail and fix direction:
[sprint-3-plan.md § Deferred to Sprint
3.1](sprint-3-plan.md#low-toc_cluster-misreads-a-real-ibr-stub-run-as-a-table-of-contents).

**Known gap, not fixed this sprint: the parser doesn't recognize the `---`
divider the model adds between protocol sections.** `_SPLIT_RE` only
splits on `## HEADER`, `CLAIM:`, and `INSUFFICIENT EVIDENCE:` — a markdown
horizontal rule the model volunteers between sections (not requested by the
prompt) isn't a recognized delimiter, so it lands glued onto the last claim
of three of the four sections per company. Confirmed across all three pilot
companies. **Cosmetic only** — citations are resolved from the API's own
`char_location` blocks, independent of this text, so `claim_coverage` and
citation correctness are unaffected — but the stored `claim_text` is
polluted for the affected claims. Not fixed here to avoid stacking a third
unplanned change on top of the extraction fix above without separate
review; flagged for the next pass at `parser.py`.

## Next up

**Sprint 3.1** (renamed 2026-09-08 — see [sprint-3-1-plan.md](sprint-3-1-plan.md);
this backlog is unrelated to the PRD's Sprint 4 valuation engine, which can
proceed independently, see [sprint-4-plan.md](sprint-4-plan.md)), per
[sprint-3-plan.md § Deferred to Sprint
3.1](sprint-3-plan.md#deferred-to-sprint-31): the batch workflow end-to-end
path (needed for the 90-company run's cost profile), non-offline stale-
filing refresh, the remaining three reanchor rungs (moved/fuzzy/
renormalized), the `toc_cluster` IBR-stub gap above, the `---`-divider
parser gap above, and the test-coverage gaps the judge rounds flagged
(`run_ai_analysis_stage`, `retrieve_batch`, real anonymized SEC fixtures).

Then: authorize the remaining ~90 companies, on the corrected extraction
path and the `--batch` submission path rather than the pilot's synchronous
one.
