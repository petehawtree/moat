# Sprint 7 — Retrieval layer over SEC filings (plan)

**Status:** Draft for review — not scheduled. Starts only after Sprint 6's
harness has produced its findings and the retro is written, because nothing
here may change the system under test while it is being evaluated.
**Depends on:** Sprint 3's `filing_documents` (section text keyed by
`accession_number, section_id, norm_version`) and `citations` (char offsets
into that text); Sprint 6's eval conventions (frozen spec before results).
**Purpose:** Two parts, as in the README: a capability, and a learning
vehicle for RAG and vector-store implementation. Where they conflict, the
plan says which wins.

## Goal

Build a **read-only retrieval sidecar**, `moat/retrieval/`, that chunks the
stored 10-K sections, embeds them, indexes them, retrieves across filings,
and answers questions with citations that resolve through the existing §A11
verifier. It does not touch `moat/analysis`, `moat/valuation` or
`moat/committee`; the committee's context is unchanged this sprint.

## Why retrieval, given that citations already work

Single-filing analysis hands Claude a whole section as a Citations-API
document (`moat/analysis/prompt.py`). That is correct and stays. Retrieval
earns its place only where whole-section prompting cannot reach:

1. **Cross-filing questions** — "which companies disclose >10% customer
   concentration", "who names tariffs in Item 1A". Today these cost an LLM
   run per filing.
2. **Year-over-year change** within one company's Item 1A / Item 7.
3. **Peer context** — how competitors describe the same risk or segment.

If a phase cannot be tied to one of those three, it is out of scope.

## Corpus facts (measured at plan time, re-measure in Phase 0)

- 93 filings under `data/sections/`, three sections each (`item_1`,
  `item_1a`, `item_7`) plus `full_v1.txt`; about 40MB of section text.
- Roughly 20k chunks at 400–600 tokens (estimate — Phase 0 replaces it with
  a count). At this size exact brute-force search is fast; a vector database
  is **not required** for performance. That is a deliberate part of the
  learning: Phase 3 measures where approximate search starts to matter
  rather than assuming it.
- Item 7 is table-heavy and chunks badly. Items 1 and 1A first.

## Design decisions to record before code (Phase 0, §A28)

| Question | Starting position |
|---|---|
| Chunk identity | `(accession_number, section_id, norm_version, start_char, end_char)` — the exact key `citations` uses, so a chunk is a citation target without translation. |
| Chunk boundaries | Paragraph-aligned, 400–600 tokens, small overlap. Offsets are into the stored normalised text, never a re-derived copy. |
| Embedding model | A local model (bge-small class) as the baseline, consistent with the free, local-first stack. One hosted model (Voyage finance) as a measured comparison only; no hosted-embedding dependency in the default path. |
| Store | `sqlite-vec` in a **separate** `retrieval.db`, not `moat.db`. The eval snapshots hash `moat.db`; a derived index must not alter or version-couple to it. A second backend (LanceDB or Chroma) behind the same interface in Phase 3. |
| Provenance | Every index records model name and version, chunker version, `doc_sha256` per source section. A changed section hash marks its chunks stale, same posture as `[STALE SOURCE]` in `cite.py`. |
| Staleness | Index rebuilds are idempotent and keyed by content hash; no silent partial index. |
| Environment | Use the rebuilt py3.11 `.venv`. Confirm `sqlite3` extension loading works there in Phase 0 — some macOS Pythons block it; if so, record the fallback before building. |

## Phases (offline before live, per the phase-gating rule)

**Phase 0 — decisions and measurement, no retrieval code.** Record the table
above as §A28. Count real chunks under two chunk sizes. Verify extension
loading. Write the retrieval eval spec (below) and get it approved before any
retriever exists.

**Phase 1 — chunk and index, offline.** Chunker with unit tests (offsets
round-trip: `text[start:end]` equals the chunk, for every chunk). Embedding
and `retrieval.db` population. Idempotent rebuild; hash-based staleness.
No LLM calls.

**Phase 2 — retrieve, offline.** Dense, BM25 (SQLite FTS5) and hybrid
(reciprocal-rank fusion) behind one interface, with metadata filters
(ticker, sector, item, fiscal year). Optional cross-encoder rerank as a
separate, switchable stage so its effect is measured alone. Score all
variants on the frozen retrieval eval.

**Phase 3 — vector-store comparison.** Same interface, second backend, and
an exact-vs-approximate experiment: latency, recall loss, index size. The
result is allowed to be "exact search is enough at this scale".

**Phase 4 — cited answers (first live LLM calls).** A `scripts/ask.py` CLI:
retrieve, pass chunks to Claude as Citations-API documents, resolve quotes to
offsets, run them through the existing §A11 verifier. Estimate spend first
and get Pete's authorisation before the first live call, as in Sprint 3.1.

## Retrieval eval (frozen before any retriever is scored)

- **Owner:** Pete writes the question set and the gold passages; Codex may
  build the scorer. Claude builds the retrievers and does not author or edit
  gold labels (eval-roles rule).
- **Set:** about 40–60 questions across the three use cases above, each with
  one or more gold chunks identified by `(accession, section_id, start,
  end)` spans, so gold survives re-chunking by span overlap rather than chunk
  id.
- **Metrics:** recall@5/10 and MRR per variant, split by question type
  (exact-term/numeric vs conceptual, single-company vs cross-company).
  Descriptive, no release gate, consistent with eval-spec-v1.
- **Held-out slice:** a portion of questions is not looked at while tuning
  chunk size or fusion weights. Tuning on the whole set is calibration, and
  is reported as such.
- **Citation faithfulness (Phase 4):** share of answer claims whose quote
  resolves exactly; share the verifier marks `[ANCHOR UNRESOLVED]`. Quote
  exists is not claim supported — §A19.6 still applies, and this sprint does
  not pretend otherwise.

## Out

- Wiring retrieval into the committee or any production prompt (Sprint 8,
  and only through the harness).
- 10-Q, 8-K or transcript ingestion; TTM fundamentals.
- Fine-tuning or training embedding models.
- Any change to `moat/ingest`, `moat/screen`, `moat/quality`,
  `moat/analysis`, `moat/valuation`, `moat/committee`.
- A dashboard tab (optional stretch after Phase 4, not in DoD).

## Definition of done (draft)

- [ ] §A28 records the Phase 0 decisions; real chunk counts replace the
  estimates above.
- [ ] Chunker offsets round-trip against `filing_documents` for every chunk,
  covered by tests.
- [ ] `retrieval.db` is rebuildable from `data/` alone, with model, chunker
  version and `doc_sha256` recorded per chunk.
- [ ] Dense, BM25 and hybrid scored on the frozen eval, by question type.
- [ ] Exact-vs-approximate and second-backend comparison recorded, whatever
  the result.
- [ ] `ask.py` answers resolve through the §A11 verifier; failures are shown,
  not hidden.
- [ ] `moat.db` and the eval snapshots are byte-for-byte unchanged by this
  sprint.
- [ ] Retro written regardless of outcome. "Plain BM25 beat the embeddings on
  this corpus" is a real result.

## Open questions the plan must answer in Phase 0

- Is Item 7 in scope for v1, or does table handling make it a follow-on?
- How are chunks from superseded filings treated: kept for year-over-year
  questions, or filtered by default?
- Where does `retrieval.db` live relative to the licensing-sensitive paths
  (it contains no Morningstar data and should stay inside the repo's
  gitignored `data/`)?
