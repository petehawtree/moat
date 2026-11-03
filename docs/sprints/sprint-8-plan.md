# Sprint 8 — MCP tool layer: retrieval first, committee second (plan)

**Status:** Draft for review — not scheduled. Absorbs the "MCP as the
committee's tool/context layer" item from
[`future-sprints.md`](future-sprints.md) (originally §A26, moved out of Sprint
6 by §A27).
**Depends on:** Sprint 7 (the retrieval layer is the first tool worth
serving) and Sprint 6's harness (the only way to say whether tool-fetched
context improved verdicts or merely changed them).

## Goal

1. **Phase A — serve Moat over MCP, read-only.** An MCP server exposing
   retrieval, fundamentals, valuations and verified-citation lookup, usable
   from Claude Desktop/Code. This is the AI-engineering learning goal and is
   valuable on its own, independent of the committee.
2. **Phase B — one controlled committee experiment.** Let one persona fetch
   context through the server instead of receiving a fixed bundle, hold
   everything else fixed, and score it with the Sprint 6 harness.

Phase A has no effect on any shipped output. Phase B is an experiment, not a
production change; adopting its result is a separate decision.

## Surface (proposed)

| Kind | Name | Returns | Notes |
|---|---|---|---|
| Tool | `search_filings` | ranked chunks with `(accession, section_id, norm_version, start, end)`, text, ticker, fiscal year | Sprint 7 hybrid retriever; filters for ticker/sector/item/year. |
| Tool | `get_passage` | exact text for a span, plus `doc_sha256` | Lets a client re-read context around a hit. |
| Tool | `get_fundamentals` | annual series for a ticker, with the §A13 rule intact | Unavailable is returned as unavailable, never zero or NaN-as-number. |
| Tool | `get_valuation` | latest valuation run: scenarios, margin of safety, run id | Always includes the run id so results are attributable. |
| Tool | `verify_citation` | the §A11 state for a quote/span: current, stale, unresolved | Wraps `cite.py`'s resolution logic; no new verifier. |
| Resource | `moat://filing/{accession}/{section}` | section text | Read-only. |

Everything is read-only. No tool writes to `moat.db`, triggers an LLM run, or
mutates the index. Query-time tools open databases read-only.

## The question this sprint has to answer first (Phase 0)

§A3's guarantee is that every asserted claim has a resolvable citation. Today
the **pipeline** controls what the model can see, so the citation target is
known before the call. With tool access the **model** chooses what to fetch.
Phase 0 decides, before code:

- **How tool-fetched text becomes citable.** Candidate: tool results are
  returned as Citations-API documents, so quotes resolve to offsets in the
  returned span, which maps to stored offsets. Alternative: the model quotes
  from tool output and the existing verifier resolves the quote against
  `filing_documents` afterwards. Pick one, state what each cannot guarantee,
  and test it on a handful of filings by hand.
- **What "grounded" means when the model never saw the whole section.** An
  asserted claim whose evidence was never retrieved must fail, not pass.
  Record whether insufficient-evidence rates are expected to rise and how
  that is distinguished from a regression.
- **Cache semantics.** The per-filing analysis cache is keyed on content
  hash. A tool-using persona's input is no longer a fixed function of the
  filing, so cache hits are not valid by default. Decide what the cache key
  includes (tool-call transcript hash) or whether Phase B runs uncached.
- **Server trust boundary.** Filing text is untrusted third-party content
  that the model will read. Tool results are returned as data; the server
  never passes filing text into a position that reads as instruction, and
  Phase B's persona prompt says so. Test with a synthetic filing containing
  an embedded instruction.

## Phases

**Phase 0 — decisions, no code** (recorded as §A29). The four questions
above, plus transport (stdio for Claude Desktop/Code; no network listener
this sprint) and SDK choice.

**Phase A1 — server skeleton and read-only tools, offline.** Tool
schemas, input validation, read-only database opens. Contract tests that
call tools directly (no LLM): span round-trips, §A13 unavailable handling,
run-id attribution, `verify_citation` agrees with `cite.py` on a fixed set.

**Phase A2 — manual use from a real client.** Connect Claude Desktop/Code,
exercise the surface by hand on a short script of questions, record what
the tool descriptions got wrong. Tool descriptions are the interface the
model reads; revise them from observed misuse, and keep the transcripts.

**Phase B0 — experiment design, frozen before running** (Pete owns the spec,
per the eval-roles rule). Which persona; fixed inputs for all else (same
model, temperature, run set, company sample, committee prompt apart from
the context mechanism); the metrics: verdict agreement with the Sprint 6
baseline, Tier A/B/C deltas, citation resolution rate, insufficient-evidence
rate, tool calls per company, tokens and cost. Noise boundary from eval-spec
amendment 1 applies — a difference inside committee replicate noise is not a
finding.

**Phase B1 — cost estimate, then live run.** Estimate spend; Pete authorises
before the first live call. Run the sample through the existing path and the
MCP path; harness scores both. Codex builds any scorer additions; Claude
builds the persona integration and does not edit the frozen spec.

## Out

- Changing the committee's shipped context path, weights or thresholds.
- Write tools, or any tool that triggers an LLM run or ingest.
- Remote/HTTP MCP transport, authentication, multi-user hosting.
- Debate or supervisor-worker workflows (still in `future-sprints.md`;
  they will use whatever tool layer exists after this sprint, and need their
  own baseline).
- More than one persona in Phase B.

## Definition of done (draft)

- [ ] §A29 records the citation, grounding, cache and trust-boundary
  decisions.
- [ ] Server exposes the read-only surface; contract tests pass without an
  LLM; no write path exists (asserted by a test that opens the databases
  read-only).
- [ ] `verify_citation` agrees with `cite.py` on a fixed set.
- [ ] Injection test: an embedded instruction in a synthetic filing does not
  change tool use or output.
- [ ] Transcripts of manual client use kept, with tool-description revisions
  noted.
- [ ] Phase B result recorded against the frozen spec, including "inside the
  noise boundary" or "citation rate dropped" if that is what happened.
- [ ] Retro written regardless of outcome.

## Risks

- **Tool access can quietly weaken citation discipline.** The model may
  assert from memory and cite something it fetched later. Phase B's metrics
  include citation resolution and insufficient-evidence rates so this shows
  up rather than hiding behind a good verdict score.
- **Cost.** Tool loops multiply calls. Estimate first, cap tool calls per
  company, and report cost beside every quality number.
- **Confounding.** If retrieval quality (Sprint 7) is poor, a bad Phase B
  result says nothing about MCP. Phase B starts only if Sprint 7's retrieval
  eval clears the bar Pete sets for it.
