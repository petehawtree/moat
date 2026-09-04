# Sprint 3 external review — corrections required before build

**Reviewed:** `claude/sprint-3-citations-tqgucp:docs/sprints/sprint-3-plan.md`, its proposed `PRD_ADDENDUM.md` §A15, and the current `main` codebase.  
**Verdict:** **approve the direction, but revise the plan before implementation.** Native API citations, source-text preservation, versioned normalization, and a separate citation index are the right foundations. The plan is unusually strong on the real failure mode—bad document extraction that produces superficially plausible analysis. However, the persistence model and the batch/cache decision need correction, and the cost budget needs an explicit output/thinking contingency.

## Non-negotiable changes

| Priority | Finding | Required change |
|---|---|---|
| P0 | The plan says four calls can be both sequential on a 5-minute prompt-cache TTL and submitted as a batch. A batch processes its requests independently; it cannot guarantee the serial dependency required for the second, third, and fourth call to read the first call's cache entry. | Select **one combined request per company + Batch API** as the production architecture. If four-call quality is evaluated, run it synchronously and sequentially on only the three-ticker evaluation set. Do not represent its cached cost as the cost of a parallel batch. |
| P0 | The proposed `citations` schema cannot reliably reconstruct a claim or enforce a citation belongs to its claim. `claim_index` is an incidental API-response position; native citations may split prose into arbitrary text blocks, and a text block can contain more than one assertion. | Add an `analysis_claims` table with a stable `claim_id`, `claim_text`, `claim_order`, `assertion_status` (`asserted`/`insufficient_evidence`), and a composite FK to its `ai_analysis` row. Make `citations.claim_id` a required FK. Treat all non-empty asserted claim text as needing one or more citations. |
| P0 | The plan calls citation rows “append-only” while requiring `resolved_status`, `resolved_score`, `last_verified_at`, and `superseded_at` to change. Those statements conflict. | Keep immutable citation anchors; write each re-anchoring outcome to `citation_resolution_events` (`citation_id`, checked_at, result, score, resolved document/span). Derive current status from the latest event. Use a separate analysis-version/supersession relationship rather than rewriting history. |
| P0 | The planned `citations` DDL omits `norm_version`, although §A15.3/§A15.6 says it is stored on *every anchor*. | Add `norm_version TEXT NOT NULL` to `citations`, and ensure citation resolution verifies `(doc_sha256, norm_version)` together. |
| P1 | `filing_documents.doc_sha256` as the sole primary key is invalid as a filing-section identity: identical boilerplate can occur in two accessions, while one accession has multiple sections. | Use a surrogate `filing_document_id` or a composite identity such as `(accession_number, section_id, norm_version, doc_sha256)`. Preserve a content-addressed immutable local file for every extracted version; never overwrite it in place. |
| P1 | `filings.local_path`/`content_hash` becomes ambiguous once raw HTML, normalized full text, and three normalized sections exist. | Define `filings.local_path` and `content_hash` as the immutable **raw primary-document** receipt (or add dedicated `raw_*` columns). Keep normalized section paths/hashes solely in `filing_documents`. Store the primary-document URL, SEC CIK, HTTP retrieval time, and source-byte hash. |
| P1 | “Latest 10-K” needs a discovery/freshness rule. A fully offline rerun cannot discover a newly filed 10-K or 10-K/A. | Define the boundary: a normal refresh runs ingest/document discovery first, selects the latest accepted filing by filing date/accession, then AI uses only cached immutable bytes. An explicit `--offline` mode may reuse the last selected filing but must state it did not check freshness. Define treatment of `10-K/A` before build. |

## What is correct and should remain

- **Fetch and retain primary-source text before analysis.** This completes the provenance principle in §A3/A5/A11: the current database has filing metadata but no narrative source material.
- **Use native citations, not model-authored quote JSON.** Anthropic documents that cited text is extracted from the supplied documents and supplied as location pointers, which removes the fabricated-quote class of failure. Plain-text documents return character ranges; custom-content documents return block ranges, so the implementation must choose plain-text section documents if it requires `start_char`/`end_char`. [Anthropic citations documentation](https://platform.claude.com/docs/en/build-with-claude/citations)
- **Resolve request-local pointers immediately and retain both position and quote/context selectors.** This is a sound durability design, provided the exact text supplied to the request is immutable and retained.
- **Reject bad section extraction rather than generating from it.** This is correctly identified as the primary engineering risk. Retain the table-of-contents and inline-XBRL fixtures, and add a test for a missing/renamed item heading and a 10-K containing multiple Item 7-like headings.
- **Use the quantitative screen as the spend gate.** Analysis is scoped to the 93 currently passing companies, not the 505-company universe.
- **Keep proxy statements and multi-year judgement expressly out of scope.** The management output must be visibly labelled “10-K only; not an assessment of compensation, incentives, governance, or track record.”

## Citation and output protocol: important implementation corrections

1. **Do not assume “one claim per API text block” can be commanded.** Citation-enabled responses contain multiple text blocks, but block boundaries are an API response feature, not a durable claim grammar. A model can also place two propositions in one cited block, or emit uncited connective prose. Parse a deliberately constrained textual protocol into claims yourself, then validate each parsed asserted claim has citations. Persist both the raw API response and parsed claims.

2. **Do not use `INSUFFICIENT EVIDENCE:` as a request stop sequence.** A stop sequence ends the entire generation at its first occurrence. In a combined call, one insufficient sub-point would truncate the other three analyses. Keep it as a literal, parseable claim status; cap verbosity through the prompt and `max_tokens` instead.

3. **Specify failure and atomicity semantics.** A combined request yields four analyses. Validate the full response in memory, then use one SQLite transaction to insert the four analysis rows, claims, raw response, and citations. If any asserted claim fails validation, persist no asserted analysis from that request; record a separate failed-attempt/audit row with the reason and usage. This prevents a half-written company state and makes retries observable.

4. **Keep citation coverage honest.** “Cited prose ÷ total prose” is easily gamed by short cited phrases around uncited assertions. Define coverage as `asserted_claims_with_at_least_one_resolving_citation / asserted_claims`; acceptance must be exactly `1.0`. Report text-level coverage only as a diagnostic.

5. **Native citation is provenance, not entailment.** The plan acknowledges this correctly. Make the UI wording equally precise: “source excerpt for this claim,” not “verified support.” A lightweight human spot-check—e.g. 3 companies × 4 analyses before production—is required because no automatic test assesses investment judgement.

6. **Store the complete request frame.** Each attempt needs a stable `request_id`, model ID/version, prompt version/hash, analysis protocol version, all document titles/index-to-document mappings, usage fields, batch ID/custom ID, and raw response JSON. The map is necessary to resolve `document_index` safely; it should not exist only in process memory.

## Schema and migration recommendation

Use additive migration, as the plan proposes, but use this shape conceptually:

```text
filings (existing) ──< filing_documents (immutable raw/full/section variants)
ai_analysis (one row per run/company/type) ──< analysis_claims ──< citations
                                                         citations ──< citation_resolution_events
analysis_attempts (request/response/usage/failure audit; one per combined API request)
```

Additional constraints/tests to require:

- `CHECK (section_id IN ('item_1', 'item_1a', 'item_7', 'full'))`; equivalent checks for analysis and resolution statuses.
- `CHECK (start_char >= 0 AND end_char > start_char)` and write-time equality of stored quote to the immutable normalized bytes.
- A composite FK from claims to analysis and an FK from citations to claims. Index `analysis_claims(run_id, ticker, analysis_type)` and `citations(accession_number)`.
- Store the raw API citation object unchanged in the attempt audit. The existing `ai_analysis.citations TEXT NOT NULL` field must either remain as a per-analysis raw JSON audit record or be migrated to an explicit `raw_response_json`; do not maintain two independently-derived citation sources.
- Decide whether an old analysis is **historical** or **current**. A new 10-K should make the prior analysis non-current for the ticker, but must not alter its original citation anchors. Use `superseded_by_run_id`/`is_current`, not an ambiguous date update on every citation.

## Cost review

### Recalculation of the stated ladder

The table is arithmetically correct **given** 93 companies, `D = 80,000` input tokens per company, and `V = 2,000` total billed output tokens per analysis (so `4V = 8,000` for the combined call). With Opus 5 at $5/$25 per MTok, the calculations are:

| Configuration | Formula per company | Result | Plan result |
|---|---:|---:|---:|
| Four uncached calls | `4 × (0.080×$5 + 0.002×$25)` | $1.80 | $1.80 |
| Four sequential 5-min cached calls | `(0.080×$6.25 + 0.002×$25) + 3×(0.080×$0.50 + 0.002×$25)` | $0.82 | $0.82 |
| One combined call | `0.080×$5 + 0.008×$25` | $0.60 | $0.60 |
| One combined Batch request | `0.080×$2.50 + 0.008×$12.50` | $0.30 | $0.30 |

Multiplying by 93 gives **$167.40**, **$76.26**, **$55.80**, and **$27.90**, respectively. The Sonnet 5 and Haiku 4.5 combined-batch estimates also check out at **$11.16** and **$5.58** before any tokenizer-driven change in `D`. Current published list prices are Opus 5 $5/$25, Sonnet 5 $2/$10, and Haiku 4.5 $1/$5 per MTok; batch prices are half. [Anthropic API pricing](https://claude.com/pricing), [Batch API pricing and limits](https://platform.claude.com/docs/en/build-with-claude/batch-processing)

### Cost caveats and corrections

1. **The $28 figure is an estimate, not a ceiling.** The 8,000 output-token assumption explicitly excludes unknown adaptive-thinking output, and `D` excludes the system prompt, instructions, document titles, and request framing. `max_tokens=64,000` does not cost $64,000 automatically, but it permits that much billable output. At the plan's input assumption, a combined Opus Batch call with 64k actual output costs **$1.00/company, or $93 for 93**. Use a production spend ceiling, read actual `input_tokens`, `cache_*`, and `output_tokens` from the three-ticker pilot, then authorize the remaining 90 only if a pre-agreed limit holds.

2. **The four-call cached/batch option is not costed for its likely cache miss.** Four independent batched calls cannot rely on ordered 5-minute reuse. With prompt-cache writes but no hits, the approximate Opus Batch cost is **$1.35/company / $125.55 total**; without cache controls it is about **$0.90/company / $83.70 total**. This is why the combined Batch request should be the production default.

3. **The “$28,000/year” statement is unsupported as written.** It implies approximately `28,000 / 167.4 = 167` full uncached passes/year—one every 2.2 days. A weekly uncached run is about $8.7k/year; a daily one about $61k/year. State the assumed refresh frequency or remove the number.

4. **The ~$7/quarter steady-state number is correct only as an annual-report average.** `93 / 4 × $0.30 = $6.98`, but it assumes all changes are new 10-Ks, no prompt/model/normalizer change forces regeneration, every company remains in scope, and no failed requests are retried. Report it as “average annual-report refresh cost,” not a general quarterly cost.

5. **Three-ticker development is not inherently $2.50.** That number is exactly three synchronous cached four-call runs (`3 × $0.82`). It excludes prompt iterations, failed validation retries, and the required side-by-side architecture/model/effort comparison. Establish a separate evaluation cap (recommend **$10–$15**) and log every attempt.

6. **Prompt caching is irrelevant to the chosen combined architecture.** Keep cache-key/copy-forward logic for cross-run reuse, but do not claim an in-request cache saving for one request per company. Batch still provides the reliable 50% discount; Anthropic notes requests in a batch run independently and recommends a one-hour TTL only when shared-cache hits are needed. [Batch processing documentation](https://platform.claude.com/docs/en/build-with-claude/batch-processing)

### Recommended financial guardrails

- Make combined Opus 5 Batch the default; set a **$35 initial production cap** and stop submission/retrieval once cumulative recorded cost reaches the cap (allow a small documented Batch overshoot).
- Pilot on three deliberately varied 10-Ks: a standard industrial/software issuer, an inline-XBRL-heavy issuer, and a long/high-risk issuer. Measure real `D`, output including thinking, section extraction confidence, claim count, citation success, and human judgement—not merely API success.
- After the pilot, calculate `N × ((D/1e6)×batch_input_rate + (O/1e6)×batch_output_rate)` using actual p50/p95 `D` and `O`; publish expected and worst-case cost before the 90-company submission.
- Keep a no-network `--dry-run` that reports both actual measured tokens and the exact pricing snapshot/version used. Pricing is changeable; never leave model price constants undocumented.

## Work-plan and acceptance revisions

### W1–W2 document layer

- Fetch the SEC submissions metadata to identify the `primaryDocument`, then download the actual filing document, not the existing EDGAR index URL. Reuse the existing descriptive User-Agent, delay, retry/backoff, but add a text/bytes fetcher—`_get_json()` cannot fetch HTML.
- Validate retrieval: status, content type, minimum byte length, HTML parseability, exact source SHA-256, and an immutable path. Record source URL and retrieval timestamp. Do not put HTML documents inside the existing `data/filings/CIK*.json` namespace.
- Make extraction deterministic and observable: heading candidate positions, selected start/end positions, character lengths, confidence/reason, and fixture identifiers. “≥90% high confidence” is a useful target but is not an acceptance test until “high” has specified measurable rules. Define them before coding.
- “Skip low confidence” conflicts with “four analyses for each of 93.” Change Definition of Done to “for every in-scope company, either a complete cited analysis or a structured `document_extraction_failed`/`insufficient_evidence` outcome.”

### W3–W5 analysis and caching

- Implement the one combined prompt first. The four-call form should exist only as an evaluation path until a human comparison shows a material quality difference.
- Correct the combined cache key: the §A15 expression includes `analysis_type`, while one request produces all four types. Use an `analysis_bundle` key containing the bundle prompt/schema version, model, normalizer, and sorted document hashes; individual derived rows inherit the request/attempt ID.
- Include `prompt_version`, **prompt content hash**, model ID, citation protocol version, extraction version, and document set in cache invalidation. The current plan includes most but not all of these explicitly.
- Copy-forward must copy immutable analysis/claim/citation identity intentionally, not fabricate a new citation provenance. Prefer a new association to the prior analysis version or an explicit `reused_from_run_id`; otherwise a new run misleadingly implies a new model invocation.
- Batch validation failures are returned asynchronously. Test a synchronous request first, then process each Batch result independently and persist successes/failures idempotently by `custom_id`.

### W6–W7 recall and UX

- `cite.py` should distinguish **current**, **historical**, **stale source**, **anchor unresolved**, **extraction failed**, and **insufficient evidence**. Do not collapse these into `stale`.
- A live EDGAR link is useful but not itself reproducibility: render the stored quote from the immutable local receipt first, then provide both the primary-document URL and existing filing-index URL.
- Wire `ai_analysis` only after the document stage has a refresh contract. The current runner starts a new `pipeline_runs` record even with `--from-stage ai`; it must select a named/latest successful `quality_scores` run rather than assume its just-created `run_id` has `passed_screen` rows.
- Add a dedicated `--tickers` selector in addition to `--limit`; “first three” is not a meaningful quality sample.

## Tests required before the 93-company run

1. Native-citation proof of concept against one real text section: verify the current SDK/model returns `char_location`, maps the document index correctly, and rejects/handles the actual response shape.
2. Exact write-path test: a deliberate one-character and a document-index mismatch both fail without DB writes.
3. Claim parser tests: multi-claim block, uncited connective assertion, sufficient-evidence assertion, and insufficient-evidence outcome.
4. SQLite transaction/idempotency test for a partial Batch result and safe retry using the same `custom_id`.
5. Section fixtures: table of contents, inline XBRL, missing heading, duplicate heading, and non-ASCII/whitespace normalization drift.
6. Re-anchor tests for each ladder rung plus ambiguous duplicated quote; a fuzzy result must retain its event and never overwrite the original anchor.
7. Cache tests: unchanged bundle makes zero API calls/copies forward explicitly; one document hash, prompt hash, model, normalizer, or analysis-protocol change invalidates exactly the intended bundle.
8. Cost telemetry test: dry run and actual response use the same pricing function; usage fields and per-attempt estimated/actual cost are persisted.

## Recommended decision record for the implementing AI

1. **Production:** one combined, citation-enabled plain-text-document request per company via Message Batches; no prompt cache within the request.
2. **Pilot:** three chosen tickers, synchronous, compare combined vs four-call only if useful; human-review the 12 resulting analyses; $10–$15 evaluation cap.
3. **Persistence:** immutable source documents, immutable citation anchors, separate claims, resolution-event history, and attempt/usage audit.
4. **Acceptance:** 100% cited asserted claims, exact source-anchor validation, explicit non-analysis outcomes, no partial company writes, and visible current/historical/staleness state.
5. **Scale gate:** only submit the remaining 90 after measured p95 input/output cost and pilot quality are accepted against the production budget.

## Sources reviewed

- Project plan and proposed PRD addendum on branch `claude/sprint-3-citations-tqgucp`.
- Current repository schema and pipeline: [`moat/db/schema.sql`](../../moat/db/schema.sql), [`moat/db/connection.py`](../../moat/db/connection.py), [`scripts/run_pipeline.py`](../../scripts/run_pipeline.py), [`moat/ai/analysis.py`](../../moat/ai/analysis.py), and current [`docs/PRD_ADDENDUM.md`](../PRD_ADDENDUM.md).
- [Anthropic citations documentation](https://platform.claude.com/docs/en/build-with-claude/citations), [Message Batches API documentation](https://platform.claude.com/docs/en/build-with-claude/batch-processing), and [Anthropic API pricing](https://claude.com/pricing), checked 2026-09-01.
