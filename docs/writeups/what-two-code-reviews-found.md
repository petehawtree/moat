# Absence of data is not evidence of failure

*Notes from Sprints 2, 2.1 and 2.2 of Project Moat, a personal AI-assisted equity
research tool built on Buffett/Graham fundamental investing principles. Sprint 1's
notes are [here](three-bugs-in-structured-financial-data.md).*

Sprint 2 built the thing the whole project depends on: a deterministic screen that
scores 505 US companies on eight fundamental metrics, judged against their own GICS
sector rather than one flat threshold. It ran, it produced a plausible ranking, and
the top of that ranking looked like exactly the businesses a Buffett-style screen
should surface — Adobe, Mastercard, Meta, Moody's, Nvidia.

Then two external code reviews took it apart, and I audited my own fixes twice more.
Six lessons survived that, most of which I'd have got wrong if I'd stopped at
"the output looks right."

## 1. The same bug, three times: treating "unknown" as "bad"

The screen's first version turned a missing metric into a zero and divided by all
eight. So a company whose ROIC simply wasn't computable from its filings scored
identically to one with genuinely terrible ROIC. Across the universe that mislabelled
**257 ROIC, 202 gross-margin and 201 debt** results as failures.

Sprint 2.2 fixed it by adding an explicit `pass` / `fail` / `unavailable` status —
and then **inverted the same distinction in a new place.** A company carrying debt
with no cash flow to service it fails outright; but debt/FCF is deliberately `None`
there, because dividing by non-positive cash flow is meaningless. The new status
logic keyed on *"is the value missing?"* instead of *"was a verdict reached?"*, so
109 explicit failures were relabelled "unavailable" and vanished from the
denominator. Nine companies passed the screen that shouldn't have.

An unknown **value** and an unknown **verdict** are different things. I wrote a whole
sprint about that distinction and still got it backwards in the one case where a
failure is expressed without a number.

## 2. Provenance doesn't make data correct — it makes errors findable

Sprint 2.1 rebuilt the ingestion layer so every stored figure carries the filing it
came from: raw SEC payloads cached, `accession_number` on 100% of 8,210 rows, the
`filings` table populated from 0 to 7,497 rows with resolvable EDGAR URLs.

The very next review found that operating cash flow was being stored and scored as
**free** cash flow on 155 of 505 companies, and that Camden Property Trust had
ingested **$12.967m** of revenue against a real ~$1.6bn.

Both numbers were wrong *and* perfectly traceable. Provenance is orthogonal to
accuracy. What it buys is that an audit is possible at all, and that a finding
becomes actionable: Camden's root cause took minutes to pin from the cached payload —
REIT rental income is tagged under ASC 842 (leases), our candidate tags only covered
ASC 606 (contracts with customers), so we captured non-lease fee income and missed
99% of the company. That's a specific, testable defect with a specific fix, not
"the revenue looks off."

## 3. Verification that costs a script doesn't happen

The same dilution defect was misdiagnosed **twice** — once by me, once by an external
reviewer. Neither was carelessness. Checking a single number meant writing a throwaway
script and re-hitting SEC's API, so in practice nobody checked and confident prose
filled the gap.

Sprint 2.1 reduced that to `python scripts/verify.py WMT shares_diluted --year 2022`,
which prints every filing that reported that fact, with values, dates and EDGAR links.
It immediately earned its cost by catching **two bugs in the fix it shipped alongside.**

Grounding has to be cheaper than guessing, or guessing wins. That's a tooling
property, not a discipline problem.

## 4. Relative metrics have blast radius

Camden's nonsense revenue didn't just mis-score Camden. Because a percentile is
computed against peers, that one row shifted **all 30** Real Estate companies'
percentiles and pushed one of them (KIM) across the top-tercile pass bar.

A bad row in an absolute metric corrupts one company. A bad row in a *relative*
metric corrupts everyone it's compared against. Implausible rows are now quarantined
from peer groups entirely, not merely flagged.

## 5. Fixing validation can be worse than the bug

My first plausibility rule flagged any margin outside ±100% as impossible. It caught
the real revenue-fragment errors — and also quarantined **Moderna** (−158% operating
margin) and **MicroStrategy** (−1141%), whose losses are entirely real.

Excluding genuinely distressed companies as "bad data" would have been a worse defect
than the one I was fixing, and would have quietly flattered every remaining peer
group — a screen that silently drops its worst constituents looks better than it is.
The rule now keys on *positive* income exceeding revenue, which separates a
fragmentary revenue tag from a large real loss.

## 6. Deferring a fix is not the same as suppressing the output

The addendum had twice documented that FCF margin, debt/FCF and gross margin are
meaningless for banks — a bank has no cost of goods sold, and debt is its raw material
rather than a solvency risk. Both times the conclusion was "defer sector-specific
metrics."

Meanwhile the screen went on scoring financials with those metrics and publishing the
ranks, producing "FCF margins" of 49x and 29x. Deferring the *fix* is a scheduling
decision. Continuing to emit a number computed from inputs you've documented as
meaningless is a *correctness* decision — and I'd made it by never consciously making
it. Financials are now marked `not_applicable` and fall out of the screen as
unscreenable. All 74 of them, the largest sector in the universe. That is the honest
position: the tool has no valid bank screen, and should say so rather than produce a
number.

## A note on the aggregate that didn't move

Comparing the dilution fix against the previous run, the headline was identical:
111 companies passed before, 111 after. A check on that number alone would have
concluded "no impact." Underneath, **67 companies' metric verdicts had changed and
10 had swapped across the pass threshold.**

Aggregate stability is not evidence of per-company correctness — and a summary
statistic is exactly what you look at when you're hoping to be done.

## Where this generalizes

Almost none of these were programming errors. The code did what it said. The defects
were in the *model of the domain* — that a share-count jump means a split, that
revenue lives under one accounting standard, that a missing number means a bad
company, that a metric valid for a manufacturer is valid for a bank. An agent (or a
person) can write correct code against a wrong model of reality all day.

What caught them was running against the golden source across a wide sample,
keeping the evidence, and making it cheap to check a claim — plus two external
reviews whose arithmetic was flawless and whose *interpretation* still needed
verifying. One mislabelled four genuine stock splits as corporate events because it
analysed an exported spreadsheet rather than the filings; the other graded unbuilt,
openly-declared future sprints as high-severity defects. Accepting either wholesale
would have introduced new errors.

Verify the reviewer too. Then fix what's real.
