# Sprint 3 — AI qualitative analysis, citation-enforced (plan)

**Status:** Planned
**Implements:** PRD §5, addendum [§A3](../PRD_ADDENDUM.md#a3-evidence-and-citation-requirement-hardened-from-prd-1),
[§A5](../PRD_ADDENDUM.md#a5-ai-cost-control),
[§A15](../PRD_ADDENDUM.md#a15-sprint-3--citation-architecture-implements-a3-extends-a11)
**Written before the sprint**, unlike the other files here — those are
records of what happened. A `sprint-3.md` retrospective replaces this one at
the end.

## Goal

Produce four cited qualitative analyses — business quality, moat, management,
risk — for each company that clears the quant screen, where **no claim
reaches the database without a citation that still resolves a year from now**.

The second half of that sentence is the sprint. Generating grounded prose is
a day's work; making the grounding survive a re-fetch, a re-chunk and a
superseding 10-K is the rest of it.

## External review

An external review of this plan
([full text](../writeups/sprint-3-plan-external-review.md)) approved the
direction and required seven changes before build. **Every code-level claim
it made reproduced against the repository**, and its independent
re-derivation of the cost ladder agreed with mine to the cent.

| Finding | Disposition |
|---|---|
| Four calls can't be both cache-sequential and batched | 🔴 **Fixed** — combined request + batch is the production architecture; four calls survive only as a synchronous pilot path |
| `claim_index` is an API artefact; can't bind a citation to a claim | 🔴 **Fixed** — `analysis_claims` with a stable `claim_id`; citations FK to it |
| "Append-only" contradicts mutable `resolved_status` / `superseded_at` | 🔴 **Fixed** — immutable anchors + `citation_resolution_events` |
| `norm_version` missing from the `citations` DDL | 🟠 **Fixed** — added, though it was reachable by join |
| `doc_sha256` alone can't identify a filing section | 🔴 **Fixed** — identity is `(accession, section_id, norm_version)` |
| `filings.local_path` ambiguous once sections exist | 🔴 **Fixed** — raw primary document only; normalized text lives in `filing_documents` |
| "Latest 10-K" has no freshness rule; `10-K/A` undefined | 🔴 **Fixed** — refresh contract stated, `--offline` must declare itself; 10-K/A raised as an open decision |
| `INSUFFICIENT EVIDENCE:` as a stop sequence would truncate the response | 🔴 **Fixed** — my error; it stays a parseable status |
| Coverage as cited-chars ÷ total-chars is gameable | 🔴 **Fixed** — coverage is cited asserted claims ÷ asserted claims, and must equal 1.0 |
| No atomicity semantics for a four-analysis response | 🔴 **Fixed** — one transaction, or an attempt row and nothing else |
| The request frame lives only in process memory | 🔴 **Fixed** — `analysis_attempts` (§A15.11) |
| Cache key still says `analysis_type` after the combined call | 🔴 **Fixed** — bundle key, on prompt *content* hash |
| `_get_json()` can't fetch HTML; runner's `run_id` has no `passed_screen` rows | 🔴 **Fixed** — both verified in the code, folded into W1/W7 |
| `$1.35/company` for four batched calls with writes and no hits | ⚫ **Disputed** — I derive $1.10; conclusion unchanged |
| "$28,000/year is unsupported as written" | 🟠 **Partly** — underspecified rather than unsupported; frequency now stated |
| ~$7/quarter needs relabelling; $2.50 pilot cost too low | 🔴 **Fixed** — relabelled as an annual-report average; pilot capped at $10–15 |

### Where the review is challenged

**The $1.35/company figure.** Four batched calls that write the cache and
never read it cost, per company: input `4 × 80,000 × $6.25/MTok = $2.00`,
output `8,000 × $25/MTok = $0.20`, halved for batch = **$1.10** ($102.30
across 93), not $1.35 ($125.55). The likeliest slip is the 1.25× write
premium applied twice. It changes nothing — both figures sit above the
uncached batch price of $0.90, which is the argument that row exists to make.

**"$28,000/year is unsupported."** The review divided $28,000 by the
*uncached* $167.40 to get 167 passes a year and judged that implausible. The
sentence said "at the plan's original configuration", which was the four-call
**cached** rung at $76.26 — and 365 × $76.26 = $27,835, i.e. daily, the
cadence §A6 already sets for prices. The number was supported; its frequency
was left implicit, which is a fair hit. Now stated.

**`norm_version` was reachable, not absent.** It resolves through
`citations.doc_sha256 → filing_documents.norm_version`. Added anyway, because
the review's own fix to `filing_documents` identity changes that join, and an
anchor needing a join to stay interpretable is one migration away from being
unreadable — right change, slightly different reason.

### Added on top of the review

`stop_reason: "refusal"` is checked before reading content. Safety
classifiers can decline a request, and across 93 unattended batched requests
an unhandled refusal would persist as empty content rather than as a failure.

## This sprint is two builds, not one

**Build A — the document layer (the larger, riskier half).** `filings` has
7,497 rows and no document text: `local_path` and `content_hash` are NULL on
every one, because they were derived from XBRL, which carries numbers and
accessions but no narrative. Moat and management judgements live in Item 1,
Item 1A and Item 7 — none of which the pipeline has ever fetched.

**Build B — the analysis.** The Claude call, citation resolution, validation,
persistence, cache.

Planning this as one build is how the sprint overruns. Build A is where the
defects will be (see [§A15.8](../PRD_ADDENDUM.md#a158-section-extraction-is-the-sprints-real-failure-surface)),
and Build B cannot be tested honestly without it.

## Scope

**In:**
- Latest 10-K per company, for the **93 companies that pass the screen** —
  not all 505. The screen is the cost gate.
- Four analysis types over Items 1, 1A and 7.
- Citations captured natively from the API, resolved to durable anchors,
  stored in their own table.
- A recall surface: one command to see the evidence behind any claim.

**Out** (each with a reason, per [§A15.9](../PRD_ADDENDUM.md#a159-explicitly-not-solved-in-sprint-3)):
DEF 14A proxies (so management analysis is thin and must say so), multi-year
filing history, 10-Qs, entailment checking, scores of any kind (Sprint 5's
committee assigns those — an analysis stage that emits numbers invents
ungrounded quantification, which is the failure mode this whole project is
built against).

## The citation design, in one pass

Full reasoning in [§A15](../PRD_ADDENDUM.md#a15-sprint-3--citation-architecture-implements-a3-extends-a11).
The four decisions that matter:

1. **Citations come from the API; claims are ours to parse.** Filing text
   goes in as **plain-text** `document` blocks with
   `citations: {"enabled": true}` — plain text is what returns character
   ranges; custom-content blocks return block indices instead. `cited_text`
   is extracted by the API from what we supplied, so the model cannot invent
   a quote or cite a filing it wasn't given: the fabricated-citation failure
   mode is removed structurally rather than detected afterwards. But a
   response text block is an artefact of how the API split the output, not a
   claim grammar — one block can carry two propositions. Claims are parsed
   from a constrained protocol into `analysis_claims`, and each asserted
   claim is then required to carry a resolving citation.

2. **Request-local coordinates are translated at call time.** The API returns
   `document_index` and character offsets, which mean nothing outside that
   one request. Persisting them raw would repeat §A11 exactly: a number with
   no way back to its source. Every citation is resolved, while the request
   frame is still in hand, into `accession_number` + `section_id` +
   `doc_sha256` + offsets + quote + context.

3. **Two selectors, always.** Offsets (fast, exact, dead the moment the
   document changes) *and* quote + 48 chars of prefix/suffix (survives
   re-fetch and re-normalization, disambiguates a phrase that appears eleven
   times in one 10-K). Either alone is a citation that eventually stops
   meaning anything.

4. **Write path is exact; read path is a ladder.** Accepting a citation
   demands byte equality — that check is aimed at *our own*
   document-index→accession mapping, the one place a silent off-by-one would
   attach real quotes to the wrong company. Recalling one degrades through
   offsets → exact quote in section → exact quote in filing →
   normalization-insensitive → fuzzy-with-context → `unresolved`, recording
   which rung answered. An `unresolved` citation marks its analysis stale; it
   is never deleted and never re-pointed at a better-scoring span.

## Schema changes

Additive only, via `schema.sql` plus `_ADDED_COLUMNS` in
`moat/db/connection.py` (`ai_analysis` holds zero rows today, so nothing
needs backfilling). Revised after external review — the first draft's
persistence model could not reconstruct a claim, and called citation rows
"append-only" while giving them fields that change on every re-anchor.

```text
filings ──< filing_documents            (immutable raw / full / section variants)
ai_analysis ──< analysis_claims ──< citations ──< citation_resolution_events
analysis_attempts                       (one per API request: frame, usage, outcome)
```

```sql
-- The exact text sent to the model. Identity is (accession, section, norm) —
-- not the hash alone: two filings can carry byte-identical boilerplate, and
-- one filing has several sections.
CREATE TABLE filing_documents (
    filing_document_id INTEGER PRIMARY KEY AUTOINCREMENT,
    accession_number   TEXT NOT NULL REFERENCES filings(accession_number),
    section_id         TEXT NOT NULL CHECK (section_id IN ('item_1','item_1a','item_7','full')),
    norm_version       TEXT NOT NULL,
    doc_sha256         TEXT NOT NULL,      -- of normalized_text
    char_length        INTEGER NOT NULL,
    extraction_method  TEXT NOT NULL,
    section_confidence TEXT NOT NULL CHECK (section_confidence IN ('high','low')),
    local_path         TEXT NOT NULL,      -- immutable; never overwritten in place
    created_at         TEXT NOT NULL,
    UNIQUE (accession_number, section_id, norm_version)
);

-- Claims are parsed by us, not inferred from API text-block boundaries.
CREATE TABLE analysis_claims (
    claim_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT NOT NULL,
    ticker           TEXT NOT NULL,
    analysis_type    TEXT NOT NULL,
    claim_order      INTEGER NOT NULL,
    claim_text       TEXT NOT NULL,
    assertion_status TEXT NOT NULL CHECK (assertion_status IN ('asserted','insufficient_evidence')),
    FOREIGN KEY (run_id, ticker, analysis_type)
        REFERENCES ai_analysis(run_id, ticker, analysis_type)
);
CREATE INDEX idx_claims_analysis ON analysis_claims(run_id, ticker, analysis_type);

-- Immutable anchors. Nothing in this table is ever UPDATEd.
CREATE TABLE citations (
    citation_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id         INTEGER NOT NULL REFERENCES analysis_claims(claim_id),
    accession_number TEXT NOT NULL REFERENCES filings(accession_number),
    section_id       TEXT NOT NULL,
    doc_sha256       TEXT NOT NULL,
    norm_version     TEXT NOT NULL,        -- stored, not joined for: see below
    start_char       INTEGER NOT NULL,
    end_char         INTEGER NOT NULL,
    quote            TEXT NOT NULL,
    quote_sha256     TEXT NOT NULL,
    prefix           TEXT,
    suffix           TEXT,
    created_at       TEXT NOT NULL,
    CHECK (start_char >= 0 AND end_char > start_char)
);
CREATE INDEX idx_citations_filing ON citations(accession_number);

-- Every re-anchoring attempt. Current status is the latest event, derived.
CREATE TABLE citation_resolution_events (
    event_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    citation_id    INTEGER NOT NULL REFERENCES citations(citation_id),
    checked_at     TEXT NOT NULL,
    result         TEXT NOT NULL CHECK (result IN
                     ('exact','moved','moved_section','renormalized','fuzzy','unresolved')),
    score          REAL,
    resolved_doc_sha256 TEXT,
    resolved_start INTEGER,
    resolved_end   INTEGER
);

-- The request frame, kept whether the attempt succeeded or failed (§A15.11).
CREATE TABLE analysis_attempts (
    attempt_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT NOT NULL REFERENCES pipeline_runs(run_id),
    ticker           TEXT NOT NULL REFERENCES companies(ticker),
    batch_id         TEXT,
    custom_id        TEXT,                 -- idempotency key for batch retrieval
    model_id         TEXT NOT NULL,
    prompt_sha256    TEXT NOT NULL,
    protocol_version TEXT NOT NULL,
    document_map     TEXT NOT NULL,        -- JSON: slot index -> filing_document_id
    usage_json       TEXT,                 -- input/output/cache token counts
    cost_estimate    REAL,
    outcome          TEXT NOT NULL,        -- 'persisted'|'validation_failed'|'api_error'|'refused'
    failure_reason   TEXT,
    raw_response     TEXT,
    created_at       TEXT NOT NULL
);
```

On existing tables: `filings.local_path` / `content_hash` are defined as the
**raw primary document** receipt (bytes as fetched, with source URL and
retrieval time) — normalized text lives only in `filing_documents`.
`ai_analysis` gains `is_current`, `superseded_by_run_id`, `reused_from_run_id`
and `claim_coverage`; its existing `citations TEXT NOT NULL` column is
retired in favour of `analysis_attempts.raw_response`, so there is one raw
record rather than two independently-derived ones.

`norm_version` is duplicated onto `citations` deliberately. It is reachable
through `doc_sha256`, but resolution must verify `(doc_sha256, norm_version)`
as a pair on its hot path, and an anchor that depends on a join to stay
interpretable is one schema change away from being unreadable.

## Work breakdown

| # | Work | Acceptance |
|---|---|---|
| W1 | **Fetch filing documents** — a bytes/text fetcher (the existing `_get_json()` returns `resp.json()` and cannot fetch HTML) reusing its retry, User-Agent and delay. Resolve `primaryDocument` from SEC `submissions`, store raw bytes immutably outside the `data/filings/CIK*.json` companyfacts namespace. | Retrieval validated on status, content type, minimum length, parseability and source SHA-256; source URL, CIK and retrieval time recorded; `filings.local_path`/`content_hash` populated as the **raw** receipt; a second run re-reads bytes without refetching. |
| W2 | **Normalize and section** — versioned normalization, Item 1/1A/7 extraction into `filing_documents`. | Extraction is observable: heading candidates considered, chosen span, length, and the rule that fired are all recorded. **`high` confidence is defined by written, measurable rules before coding** — a percentage target is not an acceptance test until "high" means something. Fixtures: trapping table of contents, inline XBRL, missing heading, duplicate Item 7-like heading, non-ASCII/whitespace drift. |
| W3 | **Prompt and call** — one combined citation-enabled request per company over plain-text `document` blocks (plain text is what returns `char_location`); claims emitted in a constrained protocol; `INSUFFICIENT EVIDENCE:` as a parseable status, **not** a stop sequence; `max_tokens` 64,000; `stop_reason` checked for `refusal` before reading content. | Synchronous single-ticker run first, then `--batch`; `--dry-run` reports measured tokens and the pricing snapshot used; `--tickers` selects the pilot set by name. |
| W4 | **Parse, resolve and validate** — parse claims from the response; map document slot → accession from the stored frame; byte-equality check; per-claim citation requirement. | A multi-claim block, an uncited connective assertion and an insufficient-evidence outcome each parse correctly; a one-character mismatch and a mis-mapped document index each fail **with no database writes**; coverage computed as asserted claims cited ÷ asserted claims. |
| W5 | **Persist and cache** — one transaction per company; bundle cache key per [§A15.7](../PRD_ADDENDUM.md#a157-cache-key-extends-a5); explicit copy-forward. | No partial company state: either four analyses with claims and citations, or an `analysis_attempts` row recording the failure and nothing else. A second run over an unchanged bundle makes **zero** API calls and marks reuse with `reused_from_run_id` rather than implying a fresh invocation. Changing any one of document hash, prompt hash, model, normalizer or protocol version invalidates exactly that bundle. Batch results are processed independently and retried idempotently by `custom_id`. |
| W6 | **Recall surface** — `scripts/cite.py`, the sibling of `verify.py`. Claims with quote, accession, filing date and both the primary-document and filing-index URLs; `--filing ACCN` inverts it; `--reanchor` runs the ladder and writes events. | Renders the quote from the **immutable local receipt** first, then links EDGAR. Distinguishes `current`, `historical`, `stale source`, `anchor unresolved`, `extraction failed` and `insufficient evidence` — not one collapsed `stale`. Checking any claim takes one command (§A11). |
| W7 | **Wire the stage** — `ai` stage in `run_pipeline.py`. | Selects the **latest successful `quality_scores` run**, not the `run_id` `main()` just created — with `--from-stage ai` that new run has no `passed_screen` rows at all, so today's runner would silently analyse nothing. Refresh contract stated: a normal run discovers filings first, then AI reads only cached immutable bytes; `--offline` reuses the last selected filing and says it did not check freshness. `10-K/A` treatment decided before build. |

Target ~55 tests. The W2 extraction fixtures and the W4 write-path tests carry
the most weight: one guards the failure that looks like a good answer, the
other guards the failure that attaches real quotes to the wrong company.

## Cost

### What the numbers are made of

Four inputs. Only two are known.

| Input | Value | Basis |
|---|---|---|
| Companies × analysis types | 93 × 4 = 372 analyses | **Measured** — the screen's output (§A14) |
| Model price | `claude-opus-5`, $5 / $25 per MTok | **Published.** Cache read 0.1×, write 1.25× (5-min TTL) / 2× (1-hour). No long-context premium — the 1M window bills at standard rates, so a large document isn't penalised beyond its tokens |
| Document tokens per company (`D`) | assumed **80k** for Items 1 + 1A + 7 | **Guess.** Nothing has measured it, and cost is linear in it |
| Output tokens per analysis | assumed 2k | **Guess, and probably low.** Thinking is on by default on Opus 5 and bills as output; 2k counts visible prose only |

Two measurement rules follow. `--dry-run` counts `D` with `count_tokens`
(free) before the first paid call. And **`D` is not portable across models** —
tokenizers differ materially (Sonnet 5's is new; the Opus 4.7+ tokenizer runs
~1–1.35× older ones), so a cross-model comparison has to re-count per model
rather than scale one number.

### The ladder

`D` = 80k input tokens, `V` = 2k billed output per analysis, Opus 5 unless
stated. The external review re-derived every row independently and they
agree to the cent.

| Configuration | Per company | 93 companies |
|---|---:|---:|
| Four uncached calls | $1.80 | $167.40 |
| Four sequential 5-min cached calls | $0.82 | $76.26 |
| One combined call | $0.60 | $55.80 |
| **One combined Batch request — production** | **$0.30** | **$27.90** |
| …at Sonnet 5 (re-count `D` first) | $0.12 | $11.16 |
| …at Haiku 4.5 (re-count `D` first) | $0.06 | $5.58 |

**Combining the calls and batching them lands Opus 5 at ~$28 — below the
price of downgrading to Sonnet on the four-call architecture.** Model choice
is the last lever precisely because it is the only one that lowers the
intelligence ceiling, and here it was not needed.

### Free levers — production takes these

1. **One combined request per company, through the Batch API.** Batch is 50%
   off every token; the combined request sends `D` once. **These are not two
   independent levers that stack on the four-call design** — a batch runs its
   requests independently, so it cannot guarantee calls 2–4 start after call
   1's cache entry is readable. Four calls batched with writes and no hits is
   ~$1.10/company (~$102) — worse than not caching at all under batch (~$0.90,
   ~$84). The combined request needs no cache hit to be cheap, which is what
   settles it.
2. **In-request caching is therefore irrelevant to production.** §A5's
   cross-run cache key and copy-forward stay — that lever is worth more than
   every other one combined. But the 5-minute TTL, the sequential-call
   constraint and the prompt-assembly-order rule apply only to the four-call
   form, which survives as a synchronous evaluation path on the pilot.
3. **Shape the output in the prompt.** A worked example and a claim cap.
   `max_tokens` is a 64,000 backstop, never a savings knob — a truncated
   response is a wasted call.
4. **Not a stop sequence.** An earlier draft proposed registering
   `INSUFFICIENT EVIDENCE:` as one. A stop sequence ends the *entire*
   generation at first occurrence, so in a combined request one insufficient
   sub-point would truncate the other three analyses — and it would look like
   a short answer, not a failure.

### The estimate is not a ceiling

`max_tokens = 64,000` does not cost $64,000, but it *permits* that much
billable output. A combined batched Opus request that actually emitted 64k
costs **$1.00/company — $93 for 93**, three times the estimate. The output
assumption also excludes adaptive thinking, and `D` excludes the system
prompt, instructions, document titles and request framing.

So the estimate is paired with enforcement rather than trusted:

- **A pilot of three deliberately varied filers** — a conventional issuer, an
  inline-XBRL-heavy one, and one with very long risk factors — under a
  **$10–15 evaluation cap**. ("Three tickers costs $2.50" was three cached
  four-call runs; it excluded prompt iterations, failed-validation retries
  and the architecture comparison, and the production shape now costs
  $0.60/company anyway.)
- **A hard $35 production cap**, enforced in code from accumulated usage
  fields, halting submission and retrieval when reached.
- **Authorise the remaining 90 only after** measured p50/p95 input and output
  — thinking included — are recomputed as `N × ((D/1e6)×batch_in +
  (O/1e6)×batch_out)` and published as expected and worst case.
- **`--dry-run` reports the pricing snapshot it used.** Prices change; an
  undocumented constant is a stale estimate waiting to happen.

### Two numbers restated honestly

**"~$28,000/year"** meant: the four-call cached configuration ($76.26 a pass)
re-running **daily**, which is the cadence §A6 sets for prices — 365 ×
$76.26 = $27,835. The frequency was left implicit, which is a fair criticism;
for comparison a weekly uncached run is ~$8.7k/year and a daily uncached one
~$61k/year. The point survives in any of them: not regenerating is worth more
than every other lever combined.

**"~$7/quarter steady state"** is `93 / 4 × $0.30 = $6.98` and is properly
called the **average annual-report refresh cost**. It assumes every
regeneration is a new 10-K, no prompt/model/normalizer change forces one, the
93 stay in scope, and nothing is retried. It is not a general quarterly
figure.

### Quality-trading levers — blocked until there's something to measure

Effort and model selection both trade capability for cost, and the guidance
for both is *sweep against an eval, one change at a time*. **This project has
no eval, and cannot cheaply build one**: citation validation checks
groundedness, not judgement, and §A15.2 leaves entailment unverified. So
there is no automated signal for "was this moat analysis any good" — only a
human reading it.

That has three consequences worth stating plainly:

- **Effort should still be swept, by hand, on three tickers.** The plan
  currently runs Opus 5 at its default `high` with adaptive thinking on — the
  top of the cost curve. On research and knowledge work, which is the shape
  of this task, published sweeps show nearly flat curves: `medium` matching
  the default's accuracy at 70–85% of cost, `low` giving up little for a
  third to a half off. This is the first lever to test and it comes *before*
  the model.
- **Haiku 4.5 is riskier here than its price suggests.** It fits high-volume
  work with checkable outputs; on knowledge questions it has been measured at
  roughly a tenth of Opus 5's cost per question and well below it on
  accuracy. Our outputs are not cheaply checkable — which is exactly the
  condition under which a cheap model's errors go unnoticed.
- **One escalation pattern does work**, because we have a partial failure
  signal: run at low effort, and **re-run only the companies whose citations
  failed validation** at higher effort. It buys groundedness, not judgement —
  but it is the one automated quality gate this pipeline actually has.

### What stays free regardless

Nothing else in the project has a line item: EDGAR, yfinance, SQLite and
Streamlit are all free. And most of this sprint still spends nothing —
**W1, W2, W6 and W7 make no model calls at all**, which is roughly two thirds
of the work including the entire citation architecture. Developing W3–W5
against three tickers costs ~$2.50. The screen is already the cost gate: 93
companies of 505, an 82% cut before a single call.

### Deliberately not doing

- **Retrieval / top-k chunking.** The cheapest option, and it caps citation
  coverage at whatever the retriever found — a model can only cite what it
  was given. The general guidance agrees for this shape: a document that most
  calls consult in full belongs in the prefix, where it is cheap.
- **Files API to "avoid re-sending" documents.** It doesn't save tokens —
  document content bills per request whether inlined or referenced by
  `file_id`. Only caching and the combined call avoid re-billing.
- **An orchestrator or advisor split.** Both pay off only where there is bulk
  to hand off or a wide capability gap inside a tool loop. One company's
  filings fit one context and there is no loop; the coordinator would cost a
  plan, a handoff and a merge for nothing.
- **Per-type context tailoring** (Item 1 for moat, 1A for risk). Moot under
  the combined request, which sends everything once. Dropping a section for
  *every* type is a different and legitimate lever if measured sections turn
  out large.

## Risks

| Risk | Mitigation |
|---|---|
| **Section extraction picks up the table of contents** — yields a 200-char "Risk Factors", the model correctly says "insufficient evidence", and we conclude a company has no risk disclosure. A data bug wearing the costume of a modest answer. | Plausibility bounds, stored confidence, skip-and-flag over proceed. Highest-priority tests. §A15.8 |
| Token estimate wrong by 2-3x | Cost is linear in `D`, which nothing has measured, and the output estimate excludes thinking tokens. `--dry-run` counts input for free; read real output off the first three tickers before scaling |
| Management analysis is weak without the proxy statement | Out of scope, but **labelled** in the output rather than silently thin (§A15.9) |
| Entailment unverified — a real quote attached to a claim it doesn't support | The stated ceiling of this sprint. Named in §A15.2 so it is not mistaken for coverage |
| Corpus size on disk | ~1-3MB text per company, ~300MB total, against 2GB of XBRL cache already there |

## Definition of done

- For **every in-scope company**, either four cited analyses or a structured
  outcome recording why not — `document_extraction_failed`,
  `insufficient_evidence`, `validation_failed`, `api_error`. "Skip the ones
  we can't extract" and "four analyses for each of 93" were contradictory;
  the reconciliation is that an explicit non-analysis is a valid result and
  silence is not.
- **Claim coverage of exactly 1.0** — every asserted claim carries at least
  one resolving citation — enforced by a test that tries to store one that
  doesn't. Character-level coverage is reported as a diagnostic only.
- **No partial company writes.** One transaction; a validation failure leaves
  an `analysis_attempts` row and no analyses.
- `python scripts/cite.py TICKER` shows every claim, its quote rendered from
  the local receipt, its state, and both filing URLs.
- A re-run over an unchanged bundle makes no API calls and records reuse
  explicitly.
- `--reanchor` reports 100% `exact` on a fresh corpus — the baseline against
  which future drift is measured.
- **A human has read all 12 pilot analyses.** No automated test assesses
  investment judgement, so nothing else closes that gap.
- `sprint-3.md` written with what actually happened, including whatever this
  plan got wrong.

## Decisions — confirmed 2026-09-04

Reviewed before build. The four the plan asked for, plus one the plan did not
anticipate.

| # | Decision | Resolution |
|---|---|---|
| 1 | Pilot three varied filers, then the 90 | **Confirmed.** The 90 go only after measured cost and a human read of the 12 pilot analyses. |
| 2 | Management analysis without DEF 14A | **Ship it thin, labelled verbatim** *"10-K only; not an assessment of compensation, incentives, governance or track record"*. No proxy fetcher this sprint. |
| 3 | `10-K/A` amendments | **Prefer the amendment, fall back on extraction failure.** Select the newest `10-K` or `10-K/A` for the latest period; if the chosen document does not yield Items 1/1A/7 above the plausibility floor, fall back to the original `10-K`. Which one was used is recorded on `filing_documents`, not inferred. Most amendments are Part III-only, so the fallback will fire often — the point is that it fires *visibly*. |
| 4 | Budget | **Confirmed.** $10–15 pilot cap, $35 production cap, both enforced in code from accumulated `usage` fields. |
| 5 | Retiring `ai_analysis.citations` | **Guarded `DROP COLUMN`.** The column is `TEXT NOT NULL` and `_migrate()` is additive-only, so retirement is a one-off migration that drops it *only* when `ai_analysis` holds zero rows and raises loudly otherwise. The additive-only rule stays intact for every table that holds data; this is the documented exception, not a precedent. |

## Three API facts checked before build

Checked against current documentation, not recalled. Each one changes
something in the work breakdown.

1. **Citations are incompatible with structured outputs.** A request that sets
   `citations: {"enabled": true}` on a document block *and* `output_config.format`
   returns a 400. So W3's constrained claim protocol **cannot** be
   schema-enforced — it is prompt-instructed plain text, parsed by a strict
   parser of ours, and a malformed response is a `validation_failed` attempt
   row. The plan assumed this without saying the alternative was closed. It is
   closed, which raises the weight of the W4 parser tests.

2. **`fallbacks` is rejected on the Batches API.** The refusal check
   (`stop_reason == "refusal"` before reading content) stands and is necessary,
   but there is no server-side rescue inside a batch: a refusal is terminal for
   that request, recorded as `analysis_attempts.outcome = 'refused'` with an
   explicit resubmit decision. Nobody should reach for the fallback parameter
   and collect a 400 across 93 requests.

3. **Haiku 4.5's context window is 200K, not 1M.** If measured `D` lands at
   150k+ for a large filer — plausible for a bank's Items 1 + 1A + 7 — Haiku is
   not merely a quality downgrade, it does not fit. The cheapest rung of the
   cost ladder is shakier than its price implies.

Everything else in the cost section re-verified: Opus 5 $5/$25, Sonnet 5
$2/$10, Haiku 4.5 $1/$5 per MTok; cache read 0.1×, write 1.25× (5-min) / 2×
(1-hour); batch 50% off with unordered results keyed by `custom_id`; Opus 5
runs adaptive thinking by default and bills it as output, with `budget_tokens`
rejected outright. Every row of the ladder recomputes to the stated figure.

## Corrections folded into the work breakdown

- **`citations` gets a composite foreign key.** It stores
  `(accession_number, section_id, doc_sha256, norm_version)` but the DDL above
  constrains only the accession. `filing_documents` already carries
  `UNIQUE (accession_number, section_id, norm_version)`, so a real composite FK
  is available — an anchor that is referentially sound rather than sound by
  convention.
- **`analysis_attempts.custom_id` gets `UNIQUE`.** W5 promises idempotent batch
  retrieval keyed on it; nothing in the DDL enforced that.
- **The stage is `ai_analysis`, not `ai`.** `scripts/run_pipeline.py` already
  names it; W7 invented a second name.
- **`requirements.txt` pins `anthropic>=0.34`,** which now spans the 1.x major
  release (httpx2, awaited async raw-response, removed parameters). Pin
  `>=1,<2` before anyone installs.
- **The synchronous pilot path must stream.** `max_tokens=64000` on a
  non-streaming call hits the SDK's HTTP timeout; use `.stream()` with
  `.get_final_message()`. Batch is unaffected.
