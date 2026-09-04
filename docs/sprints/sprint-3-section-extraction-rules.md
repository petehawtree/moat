# Sprint 3 — section extraction rules (W2 prerequisite)

**Status:** Pre-registered, not yet calibrated
**Gates:** [W2](sprint-3-plan.md#work-breakdown) — *"`high` confidence is defined
by written, measurable rules before coding — a percentage target is not an
acceptance test until 'high' means something."*
**Implements:** [§A15.6](../PRD_ADDENDUM.md#a156-decision-normalization-is-versioned),
[§A15.8](../PRD_ADDENDUM.md#a158-section-extraction-is-the-sprints-real-failure-surface)

## Why this file exists before any code

§A15.8 names the failure this sprint is most likely to ship: a naive `Item 1A`
match lands on the **table of contents**, yields `Item 1A. Risk Factors ....
23`, and the model — behaving perfectly — answers `INSUFFICIENT EVIDENCE`. The
analysis is honest, correctly cited, and completely wrong about the company. It
is §A7 and §A10 a third time: **a data defect wearing the costume of a modest
answer.**

Nothing downstream catches it. Citation validation checks that a quote came
from what we supplied; it cannot check that what we supplied was the right 200
kilobytes. So the check has to happen here, and it has to be a number rather
than a judgement, because a judgement made *after* seeing a disappointing
result is indistinguishable from an excuse.

Hence: every threshold below is fixed **now**, before the extractor exists and
before any real filing has been seen. That ordering is the point. A floor
lowered after a company fails it is not a floor.

## What these rules operate on

Normalized text at a named `norm_version`, never raw HTML — offsets index the
normalized bytes (§A15.6). Normalization collapses whitespace, folds unicode
dashes and quotes, resolves HTML entities and non-breaking spaces, and strips
tags. Every rule, threshold and character count below refers to that text.

A change to the normalizer changes these counts, which is exactly why the
version travels with the anchor.

## The document these rules assume

A 10-K's items appear in a fixed order, and the boundary of each section is the
*next* item's heading:

| Section | Starts at | Ends at (first found) |
|---|---|---|
| `item_1` — Business | `Item 1.` | `Item 1A.` |
| `item_1a` — Risk Factors | `Item 1A.` | `Item 1B.`, else `Item 1C.`, else `Item 2.` |
| `item_7` — MD&A | `Item 7.` | `Item 7A.`, else `Item 8.` |

`Item 1B` (Unresolved Staff Comments) and `Item 1C` (Cybersecurity, required
for fiscal years ending on or after 2023-12-15) are both frequently one line
long or absent, which is why `item_1a` needs a three-deep boundary ladder and
the other two do not.

The universe is the S&P 500 (§A1), so every filer is a large accelerated filer.
That matters: the "not required for smaller reporting companies" escape hatch
on Item 1A does not apply to anyone in scope, and the floors below are set
accordingly. They would be wrong for a different universe.

## Step 1 — candidate detection

For each item heading, collect **every** match in the normalized text of a
case-insensitive pattern of the shape:

```
ITEM \s+ <number> \.? \s* [-–—:]? \s* <expected title>?
```

The title is optional in the pattern but recorded when present — a candidate
that carries its expected title ("Risk Factors", "Management's Discussion and
Analysis") is stronger evidence than a bare `Item 1A.`, and the tie-break in
step 3 uses it.

Every candidate is kept in the trace, including the ones about to be rejected.
An extractor that only reports its answer cannot be debugged when the answer is
wrong.

## Step 2 — rejection filters

A candidate is discarded if **any** of these fire. Each one is recorded against
the candidate by name, so the trace says not just *which* span was chosen but
*why the others were not*.

| Filter | Rule | The failure it catches |
|---|---|---|
| `toc_cluster` | ≥ **4 distinct** item headings appear within **±3,000 chars** of the candidate | The table of contents — the §A15.8 trap. Real body sections are thousands of characters apart; a ToC packs the whole outline into one screen |
| `dot_leader` | Within **120 chars** after the candidate: `\.{4,}`, or a run of whitespace then 1–3 digits then a line end | ToC and index entries that survived the cluster test (a short ToC, or an exhibit index) |
| `cross_reference` | The candidate is preceded on its own line by `see`, `in`, `under`, `refer to`, `described in`, `included in`, `pursuant to`, or by a lowercase letter or comma — i.e. it sits mid-sentence rather than at a line start | Body prose that mentions another item: *"…the risks described in Item 1A. Risk Factors…"*. This is the trap that survives every ToC defence, because it is genuinely in the body |
| `part_header_only` | The candidate is immediately preceded (within 80 chars) by `PART I`/`PART II` **and** followed within 200 chars by another item heading | Divider pages that list a part's contents |

If no candidate survives for a section, that section is `failed` with reason
`no_candidate`. Stop; do not relax a filter to manufacture one.

## Step 3 — assignment and tie-break

Survivors are assigned in document order under one constraint: **the chosen
positions for `item_1`, `item_1a` and `item_7` must be strictly increasing, and
every resulting span must clear its hard floor** (step 4).

- Exactly one assignment satisfies both → that is the answer.
- More than one → take the earliest satisfying assignment, prefer candidates
  carrying their expected title, and record `tie_break_fired` with the
  alternatives. **A section whose assignment needed the tie-break can never be
  `high`.**
- None → the sections that cannot be placed are `failed` with reason
  `no_consistent_assignment`.

Sequence consistency is doing more work here than it looks. It is what defeats
the surviving ToC of a filer whose contents page is too short to trip the
cluster test: a ToC's three entries are a few hundred characters apart, so at
least one of the resulting spans cannot clear its floor, and the whole
assignment falls.

## Step 4 — plausibility bounds

Span length is measured from the end of the chosen heading to the start of the
chosen boundary heading.

| Constant | Value | Guards against |
|---|---:|---|
| `ITEM_1_HARD_FLOOR` | 5,000 chars | A boundary landing on the wrong heading |
| `ITEM_1_HIGH_FLOOR` | 15,000 chars | — |
| `ITEM_1A_HARD_FLOOR` | 5,000 chars | The §A15.8 trap's residue |
| `ITEM_1A_HIGH_FLOOR` | 20,000 chars | — |
| `ITEM_7_HARD_FLOOR` | 5,000 chars | MD&A truncated at a sub-heading |
| `ITEM_7_HIGH_FLOOR` | 15,000 chars | — |
| `SECTION_MAX_RATIO` | 0.60 of the normalized document | A missed end boundary that swallows the rest of the filing |
| `ALPHA_RATIO_MIN` | 0.55 alphabetic ÷ non-whitespace chars | A span that ran into the financial statements or an exhibit index — tables are digits and punctuation, not prose |

Below the hard floor, above the ratio, or under the alpha ratio → `failed`.
Between the hard floor and the high floor → at best `low`.

**On the numbers.** §A15.8 proposed ~2,000 characters as the Item 1A floor.
That number detects the ToC artefact (a contents entry normalizes to roughly
50–200 chars, so almost anything catches it) but it does not detect a *partial*
extraction, which is the more dangerous case because it looks like text. For a
large accelerated filer, a 4,000-character Risk Factors section is not a
company with few risks; it is a boundary that fired early. Hence 5,000 hard,
20,000 high.

Every one of these values is a **guess** in the §A14 sense — nothing has
measured a real corpus. They are pre-registered so that the first calibration
run produces evidence rather than rationalization. Calibration is a recorded
event: the distribution of measured lengths across the pilot filers plus the
fixture set is written into `sprint-3.md`, and any threshold moved afterwards
is moved *with that distribution stated and a reason that is not "a company we
wanted failed it."*

## `high`, `low`, `failed` — the definitions

**`high`** requires all six:

1. Exactly one candidate survived step 2 for this section, **or** the tie-break
   did not fire.
2. Both boundaries were found by heading match — not by document end, and not
   by the second or third rung of the `item_1a` boundary ladder.
3. Length ≥ the section's `HIGH_FLOOR`.
4. Length ≤ `SECTION_MAX_RATIO` of the normalized document.
5. Alphabetic ratio ≥ `ALPHA_RATIO_MIN`.
6. The span begins after the last rejected `toc_cluster` candidate in the
   document.

**`low`** — the section was extracted and cleared every hard floor, but at
least one `high` criterion failed. The specific criterion is recorded; "low"
without a reason is not a result.

**`failed`** — no candidate, no consistent assignment, or a hard bound
breached. Recorded with its reason.

**`incorporated_by_reference`** — a fourth outcome, and a real one. A short
span containing `incorporated by reference` (within 500 chars of the heading)
is not a broken extractor: the filer genuinely put that item in an exhibit.
Sprint 3 does not chase Exhibit 13, so this is a structured non-analysis
outcome, distinct from a failure, and `cite.py` must show it as such.

## Step 5 — the fallback ladder

The plan's rule is *"never proceed on a section we do not believe."* Applied
per company, not per section, because a company analysed on two good sections
and one doubtful one produces an analysis nobody can characterise:

| Company's worst section | Action | `extraction_method` |
|---|---|---|
| All three `high` | Analyse the three sections | `sections` |
| Any `low` or `failed` | **Fall back to the full document** for that company | `full_fallback` |
| Full document also implausible (unparseable, or under 20,000 chars) | No analysis; structured `document_extraction_failed` outcome | — |
| Any section `incorporated_by_reference` | Analyse the rest; label the gap in the output, as management analysis is labelled | `sections_partial` |

**The fallback is not free and must not be silent.** A full 10-K runs roughly
2.5–4× the size of Items 1 + 1A + 7, so a company on `full_fallback` costs
something like **$0.75–$1.13** batched against the $0.30 baseline — and that
multiple is itself unmeasured until `--dry-run` counts a real one.

Which forces a budget rule: **if more than 10% of in-scope companies need the
full fallback, stop and fix the extractor.** Ten fallbacks is roughly $8 on top
of a $28 run and still inside the $35 cap; thirty is not, and thirty also means
the extractor is broken in a way the per-company confidence flag is quietly
absorbing. The cap should be hit by the extractor's honesty, never by its
failure rate.

## Observability

W2's acceptance requires that extraction be *observable*: candidates
considered, chosen span, length, and the rule that fired. That needs somewhere
to live, so this document proposes one addition to the Sprint 3 schema:

```sql
-- Added to filing_documents (see sprint-3-plan.md, Schema changes)
extraction_trace TEXT   -- JSON; NULL only for section_id = 'full'
```

Carrying, per section: every candidate position with its rejection filter or
`accepted`; the chosen start and end and how each boundary was found; measured
length, document ratio and alpha ratio; each `high` criterion with pass/fail;
whether the tie-break fired and against what; and the `norm_version` the whole
trace was computed under.

It is a JSON column rather than a table because nothing joins to it — it is
read when a specific extraction is being argued about, which is exactly the
shape §A11 says a receipt should have.

## Fixtures — required before the extractor is trusted

The plan names five. Each becomes a committed normalized-text fixture with an
asserted expected outcome, so a regression is a failing test rather than a
surprising analysis:

| Fixture | Must produce |
|---|---|
| Trapping table of contents | ToC candidates rejected by `toc_cluster`; body spans chosen; all three `high` |
| Inline XBRL-heavy filing | Same spans as its non-XBRL rendering, within a stated tolerance |
| Missing Item 1A heading | `failed` / `no_candidate` → `full_fallback`, **not** a short span |
| Duplicate Item 7-like heading (a cross-reference in body prose) | `cross_reference` rejection; the real span chosen; `high` |
| Non-ASCII and whitespace drift | Identical span boundaries after normalization; `norm_version` recorded |

Two more this document adds, because both are real and neither is covered:

| Fixture | Must produce |
|---|---|
| Item incorporated by reference to Exhibit 13 | `incorporated_by_reference`, not `failed` |
| A section that runs into the financial statements (missing end boundary) | Rejected by `SECTION_MAX_RATIO` or `ALPHA_RATIO_MIN` → `full_fallback` |

## What this document does not settle

The thresholds are pre-registered, not validated — no filing has been measured
against them, and the first calibration run is expected to move at least one.
That is the intended use. What it may not do is move one quietly: a threshold
changed without the measured distribution beside it turns this file from a
control into a decoration.
