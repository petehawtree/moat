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
| W3 | **Prompt and call** — `build_prompt`, per-type templates covering PRD §5's sub-points; documents as cached `document` blocks; one claim per text block; `INSUFFICIENT EVIDENCE:` sentinel. | Single-ticker run produces cited blocks per type, or explicit insufficiency; `--dry-run` prints measured token counts and estimated cost without calling the API. |
| W4 | **Resolve and validate** — `moat/ai/citations.py`: index→accession mapping, byte-equality assertion, coverage rule, anchor construction. | Uncited prose fails the stage and persists nothing (§A3); a deliberately mis-mapped document index is caught by the byte check; coverage is computed and stored. |
| W5 | **Persist and cache** — `citations` + `ai_analysis` rows, `cache_key` per [§A15.7](../PRD_ADDENDUM.md#a157-cache-key-extends-a5), copy-forward under a new `run_id`. | A second pipeline run over unchanged filings makes **zero** API calls and still leaves every downstream stage a complete read. |
| W6 | **Recall surface** — `scripts/cite.py`, the sibling of `verify.py`. `cite.py TICKER [--type moat]` prints each claim with its quote, accession, filing date and EDGAR URL; `--filing ACCN` inverts it ("what rests on this filing"); `--reanchor` runs the ladder over stored citations and reports exact/moved/fuzzy/unresolved counts. Dashboard tab renders claims with expandable evidence. | Checking any AI claim takes one command. §A11: *grounding has to be cheaper than guessing, or guessing wins.* |
| W7 | **Wire the stage** — `ai` stage in `run_pipeline.py`, gated on `passed_screen`, with `--limit` and `--dry-run`. | `--from-stage ai` runs end to end; run status stays `partial` at the first unbuilt stage (valuation). |

Target: ~45 tests (31 today), with the W2 extraction tests carrying the most
weight — they are the ones guarding the failure that looks like a good answer.

## Cost

### What the numbers are made of

Four inputs. Only two of them are known.

| Input | Value | Basis |
|---|---|---|
| Companies × analysis types | 93 × 4 = 372 calls | **Measured** — the screen's output (§A14) |
| Model price | `claude-opus-5`, $5 / $25 per MTok in/out | **Published** — cache read 0.1×, cache write 1.25× (5-min TTL) or 2× (1-hour) |
| Document tokens per company (`D`) | assumed **80k** for Items 1 + 1A + 7 | **Guess.** Nothing has measured it. Cost is linear in this number |
| Output tokens per analysis | assumed 2k | Guess. Minor — 11-24% of the bill depending on model. `cited_text` is not billed as output |

Everything below scales with `D`, so W3's `--dry-run` measures it with
`count_tokens` (free) before a cent is spent. At `D` = 50k rather than 80k,
every input figure below falls by ~37%.

**Per company, cached:** `D × (1.25 + 0.1 × 3)` input tokens = **1.55 × D**,
plus 4 × 2k output. The four calls must run **sequentially** — a cache entry
is only readable once the first response has begun streaming, so four
parallel calls all pay full price and write four entries. And the TTL is the
**5-minute** default, not 1 hour: the four calls for one company run
back-to-back, and the 1-hour TTL only doubles the write price for a window
nothing uses.

### What it costs, and what changes it

| Configuration | Per company | 93 companies | 20-company shortlist |
|---|---|---|---|
| Opus 5, documents re-sent per call | $1.80 | $167 | $36 |
| **Opus 5, cached (the plan)** | $0.82 | **$76** | $16 |
| Opus 5, Batch API (50%, uncached) | $0.90 | $84 | $18 |
| Sonnet 5, cached | $0.33 | **$31** | $7 |
| Haiku 4.5, cached | $0.16 | **$15** | $3 |
| Opus 5, cached, steady state (§A5) | — | ~$19/quarter | ~$4/quarter |

Batch and caching don't compose reliably — cache hits inside a concurrent
batch are best-effort — so batch is costed against the uncached price. For
this workload caching beats batching, and the two biggest levers are the
**model** and `D`.

### The optimization that isn't one

The instinct is to send each analysis type only the sections it needs — moat
gets Item 1, risk gets Item 1A. It costs *more*:

| Approach | Input tokens per company |
|---|---|
| All three sections to all four types, cached | **1.55 × D** |
| Half the document per type, no shared prefix | 2.00 × D |
| Shared Item 1 cached + per-type extras | 1.82 × D |

Tailoring the context breaks the shared prefix that made the loop cheap, and
it hands each analysis less evidence to cite. **Send everything to everyone
and cache it** — cheaper and better grounded at once.

### Free, and staying free

The rest of the project has no line items: SEC EDGAR is free, yfinance is
free, SQLite and Streamlit are local. This sprint introduces the first cost
in the tool's history, and most of the sprint still doesn't spend anything:

- **W1, W2, W6, W7 cost $0** — fetching, sectioning, hashing, the recall CLI
  and the pipeline wiring involve no model calls at all. That is roughly two
  thirds of the work, including all of the citation architecture.
- **Developing W3-W5 against 3 tickers costs ~$2.50** (~$1 on Sonnet). The
  full run is a separate, deferrable decision made once the stage works.
- **`count_tokens` is free**, so `D` gets measured before it gets spent.
- **The screen is already the cost gate**: 93 of 505 companies, an 82%
  reduction before a single call.
- **§A5's cache is the real lever.** Re-running all four analyses on every
  scheduled pass would cost ~$28,000/year at Opus prices. Not regenerating is
  worth more than every other saving on this page combined.

**Lower-cost paths, in order of saving per unit of regret:** shorten the
shortlist (linear, and nobody reads 93 analyses); drop to Sonnet 5 or Haiku
4.5 (2.5× / 5× cheaper — and since the API extracts the citations, the
model's job here is judgement, not quotation accuracy); batch it. **Not
recommended:** retrieval that sends only top-k chunks — it is the cheapest
option and it silently caps citation coverage at whatever the retriever
found, which is the one property this sprint exists to guarantee.

**Decide it by measurement, not by list price:** run the same 3 tickers
through Opus 5, Sonnet 5 and Haiku 4.5, and read the four analyses side by
side. If the cheaper model's moat write-up is as well-evidenced, the $61
difference on a full pass is not buying anything.

## Risks

| Risk | Mitigation |
|---|---|
| **Section extraction picks up the table of contents** — yields a 200-char "Risk Factors", the model correctly says "insufficient evidence", and we conclude a company has no risk disclosure. A data bug wearing the costume of a modest answer. | Plausibility bounds, stored confidence, skip-and-flag over proceed. Highest-priority tests. §A15.8 |
| Token estimate wrong by 2-3x | Cost is linear in `D`, which nothing has measured. `--dry-run` counts tokens for free before the first real call; the model choice is a second, larger lever if it comes in high |
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
3. **Budget ceiling, and which model.** The plan assumes Opus 5 at ~$76 for
   the full pass. Sonnet 5 (~$31) and Haiku 4.5 (~$15) are the same pipeline
   with one string changed — worth settling by running 3 tickers through all
   three and comparing the write-ups, not by list price. A shorter shortlist
   cuts any of them linearly.
