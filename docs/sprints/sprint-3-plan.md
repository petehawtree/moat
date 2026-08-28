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

1. **Citations come from the API, not the model.** Filing text goes in as
   `document` blocks with `citations: {"enabled": true}`. `cited_text` is
   then extracted by the API from what we supplied — the model cannot invent
   a quote or cite a filing it wasn't given. The fabricated-citation failure
   mode is removed structurally rather than detected afterwards, and each
   returned text block carries its own citations, which is what makes "every
   claim is cited" a mechanical check instead of an aspiration.

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
`moat/db/connection.py` (the existing migration path — `ai_analysis` holds
zero rows today, so nothing needs backfilling).

```sql
-- Text we actually sent to the model. The frame of reference for every
-- offset in `citations` (A15.3, A15.6).
CREATE TABLE filing_documents (
    doc_sha256       TEXT PRIMARY KEY,     -- of normalized_text
    accession_number TEXT NOT NULL REFERENCES filings(accession_number),
    section_id       TEXT NOT NULL,        -- 'item_1'|'item_1a'|'item_7'|'full'
    norm_version     TEXT NOT NULL,        -- normalizer that produced this text
    char_length      INTEGER NOT NULL,
    extraction_method TEXT NOT NULL,       -- how the section boundary was found
    section_confidence TEXT NOT NULL,      -- 'high'|'low' — A15.8
    local_path       TEXT NOT NULL,        -- normalized text on disk
    created_at       TEXT NOT NULL
);

-- One row per (claim, supporting quote). Append-only.
CREATE TABLE citations (
    citation_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT NOT NULL REFERENCES pipeline_runs(run_id),
    ticker           TEXT NOT NULL REFERENCES companies(ticker),
    analysis_type    TEXT NOT NULL,
    claim_index      INTEGER NOT NULL,     -- which text block in the response
    accession_number TEXT NOT NULL REFERENCES filings(accession_number),
    section_id       TEXT NOT NULL,
    doc_sha256       TEXT NOT NULL REFERENCES filing_documents(doc_sha256),
    start_char       INTEGER NOT NULL,     -- position selector
    end_char         INTEGER NOT NULL,
    quote            TEXT NOT NULL,        -- quote selector
    quote_sha256     TEXT NOT NULL,
    prefix           TEXT,                 -- 48 chars before
    suffix           TEXT,                 -- 48 chars after
    resolved_status  TEXT NOT NULL,        -- exact|moved|moved_section|renormalized|fuzzy|unresolved
    resolved_score   REAL,                 -- similarity, when fuzzy
    last_verified_at TEXT NOT NULL,
    superseded_at    TEXT                  -- never UPDATE a row in place
);
CREATE INDEX idx_citations_filing ON citations(accession_number);  -- "what rests on this filing?"
CREATE INDEX idx_citations_claim  ON citations(run_id, ticker, analysis_type, claim_index);
```

Plus, on `ai_analysis`: `norm_version`, `filings_used` (JSON accessions),
`citation_coverage` (cited prose ÷ total prose), `stale` (0/1), and
`content_hash` / `local_path` finally populated on `filings`.

## Work breakdown

| # | Work | Acceptance |
|---|---|---|
| W1 | **Fetch filing documents** — `moat/ingest/filing_documents.py`. Primary 10-K document via SEC `submissions`, cached under `FILINGS_CACHE_DIR`, reusing the existing rate-limit and User-Agent handling. | Latest 10-K text cached for the 93; `filings.local_path` and `content_hash` non-NULL; a second run is fully offline. |
| W2 | **Normalize and section** — HTML→text, versioned normalization, Item 1/1A/7 extraction, `filing_documents` rows. | ≥90% of the 93 yield all three sections at `high` confidence; every low-confidence extraction is flagged and skipped, never silently used; tests against a trapping table of contents and an inline-XBRL filing. |
| W3 | **Prompt and call** — `build_prompt`, per-type templates covering PRD §5's sub-points; documents as `document` blocks; one claim per text block; `INSUFFICIENT EVIDENCE:` sentinel registered as a stop sequence; `max_tokens` 64,000. | Single-ticker run produces cited blocks per type, or explicit insufficiency; `--dry-run` prints measured token counts and estimated cost without calling the API; `--batch` submits through the Batch API. |
| W4 | **Resolve and validate** — `moat/ai/citations.py`: index→accession mapping, byte-equality assertion, coverage rule, anchor construction. | Uncited prose fails the stage and persists nothing (§A3); a deliberately mis-mapped document index is caught by the byte check; coverage is computed and stored. |
| W5 | **Persist and cache** — `citations` + `ai_analysis` rows, `cache_key` per [§A15.7](../PRD_ADDENDUM.md#a157-cache-key-extends-a5), copy-forward under a new `run_id`. | A second pipeline run over unchanged filings makes **zero** API calls and still leaves every downstream stage a complete read. If the four-call design is kept, a standing test asserts `cache_read_input_tokens > 0` on the second call — a broken prefix is silent otherwise. |
| W6 | **Recall surface** — `scripts/cite.py`, the sibling of `verify.py`. `cite.py TICKER [--type moat]` prints each claim with its quote, accession, filing date and EDGAR URL; `--filing ACCN` inverts it ("what rests on this filing"); `--reanchor` runs the ladder over stored citations and reports exact/moved/fuzzy/unresolved counts. Dashboard tab renders claims with expandable evidence. | Checking any AI claim takes one command. §A11: *grounding has to be cheaper than guessing, or guessing wins.* |
| W7 | **Wire the stage** — `ai` stage in `run_pipeline.py`, gated on `passed_screen`, with `--limit` and `--dry-run`. | `--from-stage ai` runs end to end; run status stays `partial` at the first unbuilt stage (valuation). |

Target: ~45 tests (31 today), with the W2 extraction tests carrying the most
weight — they are the ones guarding the failure that looks like a good answer.

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

Each rung is a change to the one above it. `D` = 80k, Opus 5 unless stated.

| Configuration | Per company | 93 companies |
|---|---|---|
| Four calls, documents re-sent each time | $1.80 | $167 |
| Four calls, cached, sequential | $0.82 | $76 |
| **One combined call per company** (no cache needed) | $0.60 | **$56** |
| **…submitted through the Batch API** | $0.30 | **$28** |
| …at Sonnet 5 (re-count `D` first) | ~$0.12 | ~$11 |
| …at Haiku 4.5 (re-count `D` first) | ~$0.06 | ~$6 |
| Steady state under §A5 — only new 10-Ks | — | ~$7/quarter |

The result worth noticing: **combining the calls and batching them lands Opus
5 at ~$28 — below the price of downgrading to Sonnet on the original
architecture.** Architecture beat model choice, which is the usual ordering
and the reason model selection is the last lever here, not the first.

### Free levers — take all of these

None of these trade quality, so none need an eval to justify.

1. **Batch API — 50% off every token, and it stacks with caching.** This
   workload is exactly what batch is for: unattended, scheduled, nobody
   waiting on a response, single-shot requests with no tool loop. Results
   arrive within 24 hours (an expiry, not an SLA). The only caveat is that
   cache hits *inside* a concurrent batch are best-effort — which the
   combined call makes irrelevant, since it needs no cache hit to be cheap.
2. **One call per company instead of four.** Sends `D` once (1.00 × D)
   instead of 1.55 × D cached, and drops the sequencing constraint entirely.
   Costs: one validation failure loses all four analyses rather than one, and
   a prompt change to any single analysis type regenerates all four. Measure
   whether four analyses in one response are as deep as four separate ones
   before committing — this is the one "free" lever that could quietly cost
   quality.
3. **Prompt assembly order.** Caching is a prefix match, so if the four calls
   are kept: stable system prompt first (its own breakpoint — it is shared
   across all 372 calls), then the documents, then the per-type instruction
   **last**. Putting "analyse the moat" before the documents makes the prefix
   differ per type and yields a 0% hit rate with no error and no warning.
4. **Specify the output shape.** A worked example and a cap ("at most eight
   claims") shortens responses directly. `max_tokens` is a backstop, not a
   knob — set it to 64,000; a truncated response is a wasted call, not a
   cheap one.
5. **A stop sequence on the refusal sentinel**, so `INSUFFICIENT EVIDENCE:`
   ends the response instead of paying for an explanation of it.
6. **Write the prompts for this model.** Prompt patterns carried over from
   older models measurably cost more for no accuracy gain — there is nothing
   to port here, so simply don't import the scaffolding habits.
7. **§A5's cache, which dwarfs everything above.** Re-running the stage on
   every scheduled pass would cost ~$28,000/year at the plan's original
   configuration. Not regenerating is worth more than every other lever
   combined.

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
- **Per-type context tailoring** (Item 1 for moat, 1A for risk). Under the
  four-call design it costs *more* — 2.00 × D against 1.55 × D — because it
  breaks the shared prefix, and it hands each analysis less to cite. Under
  the combined call the question disappears. Note this is about *varying* the
  context per type; dropping a section for every type (say Item 7) shrinks
  `D` for everyone and is a legitimate saving if the sections turn out large.

## Risks

| Risk | Mitigation |
|---|---|
| **Section extraction picks up the table of contents** — yields a 200-char "Risk Factors", the model correctly says "insufficient evidence", and we conclude a company has no risk disclosure. A data bug wearing the costume of a modest answer. | Plausibility bounds, stored confidence, skip-and-flag over proceed. Highest-priority tests. §A15.8 |
| Token estimate wrong by 2-3x | Cost is linear in `D`, which nothing has measured, and the output estimate excludes thinking tokens. `--dry-run` counts input for free; read real output off the first three tickers before scaling |
| Management analysis is weak without the proxy statement | Out of scope, but **labelled** in the output rather than silently thin (§A15.9) |
| Entailment unverified — a real quote attached to a claim it doesn't support | The stated ceiling of this sprint. Named in §A15.2 so it is not mistaken for coverage |
| Corpus size on disk | ~1-3MB text per company, ~300MB total, against 2GB of XBRL cache already there |

## Definition of done

- Four cited analyses for each of the 93 screened companies, or a recorded
  reason why not.
- Zero claims stored without a resolving citation, enforced by a test that
  tries to store one.
- `python scripts/cite.py TICKER` shows every claim, its quote, and a live
  EDGAR link.
- A re-run makes no API calls when nothing changed.
- `--reanchor` reports 100% `exact` on a fresh corpus — the baseline against
  which future drift is measured.
- `sprint-3.md` written with what actually happened, including whatever this
  plan got wrong.

## Decisions to confirm before starting

1. **Iterate on 3 tickers first, then run the 93** — recommended; it makes
   W1-W5 cheap to get wrong.
2. **Management analysis without DEF 14A**: ship it thin-and-labelled, or add
   a proxy fetcher to the sprint (~W1.5, one more document type)? Plan
   assumes thin-and-labelled.
3. **Four calls per company, or one?** The combined call is a third off the
   input and removes the caching machinery, at the risk of four shallower
   analyses. It is the largest single lever and the only free one that could
   cost quality — decide it on three tickers, read side by side.
4. **Budget ceiling.** Taking the free levers alone puts a full pass at ~$28
   on Opus 5. Effort and model tier could take it under $10, but both trade
   quality with no eval to catch the loss — so the recommendation is to bank
   the free levers now and leave the tradeable ones until there is something
   worth measuring against.
