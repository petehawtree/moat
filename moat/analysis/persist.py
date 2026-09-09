"""W5: persist and cache AI analysis results (Sprint 3).

One transaction per company — either four analyses with all claims and
citations, or an analysis_attempts row recording the failure and nothing else.

Bundle cache key covers: sorted doc_sha256s, prompt_sha256, model_id,
norm_version, protocol_version. Any change to any of these invalidates the
bundle and forces a fresh API call.

Cache hit: write new ai_analysis rows with reused_from_run_id pointing at
the original run, plus an analysis_attempts row. Zero API calls.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone

from moat.analysis.caller import CallResult, _prompt_sha256, prepare_sections
from moat.analysis.parser import ParsedResponse, parse_and_validate
from moat.analysis.pricing import DEFAULT_MODEL, estimate_cost
from moat.analysis.prompt import (
    ANALYSIS_TYPES,
    PROTOCOL_VERSION,
    SYSTEM_PROMPT,
    build_request,
    compute_gap_sections,
)
from moat.ingest.section_extractor import NORM_VERSION


# ---------------------------------------------------------------------------
# Bundle cache key
# ---------------------------------------------------------------------------

def compute_bundle_key(
    doc_sha256s: list[str],
    prompt_sha256: str,
    model_id: str,
    norm_version: str,
    protocol_version: str,
) -> str:
    """Stable hash that identifies a unique (document × prompt × model) bundle.

    Any change to any field — new filing, prompt tweak, model upgrade, or
    normalizer revision — produces a different key and forces a fresh call.
    """
    payload = json.dumps(
        {
            "doc_sha256s":       sorted(doc_sha256s),
            "prompt_sha256":     prompt_sha256,
            "model_id":          model_id,
            "norm_version":      norm_version,
            "protocol_version":  protocol_version,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _doc_sha256s_for_result(result: CallResult, conn) -> list[str]:
    doc_map = {int(k): v for k, v in result.document_map.items()}
    sha256s = []
    for fdi in doc_map.values():
        row = conn.execute(
            "SELECT doc_sha256 FROM filing_documents WHERE filing_document_id = ?",
            (fdi,),
        ).fetchone()
        if row:
            sha256s.append(row["doc_sha256"])
    return sha256s


def find_cached_run(ticker: str, bundle_key: str, conn) -> str | None:
    """Return the source run_id for this bundle key, or None if not cached.

    Returns the original (non-copy-forward) run — the one with claims and
    citations attached. After a cache-forward write, the original has
    reused_from_run_id IS NULL and is_current=0 (superseded), but is still
    the authority for claims.
    """
    row = conn.execute(
        "SELECT run_id FROM ai_analysis "
        "WHERE ticker = ? AND cache_key = ? AND reused_from_run_id IS NULL "
        "LIMIT 1",
        (ticker, bundle_key),
    ).fetchone()
    return row["run_id"] if row else None


def _supersede_for_bundle(conn, ticker: str, bundle_key: str, new_run_id: str) -> None:
    """Set is_current=0 on all prior current analyses for this ticker+bundle."""
    conn.execute(
        "UPDATE ai_analysis SET is_current = 0, superseded_by_run_id = ? "
        "WHERE ticker = ? AND cache_key = ? AND run_id != ? AND is_current = 1",
        (new_run_id, ticker, bundle_key, new_run_id),
    )


# ---------------------------------------------------------------------------
# Pipeline-run helper
# ---------------------------------------------------------------------------

def _ensure_pipeline_run(run_id: str, conn) -> None:
    """Insert a pipeline_runs row if one does not already exist."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT OR IGNORE INTO pipeline_runs
            (run_id, started_at, status)
        VALUES (?, ?, 'running')
        """,
        (run_id, now),
    )


# ---------------------------------------------------------------------------
# Persist
# ---------------------------------------------------------------------------

def persist_result(
    result: CallResult,
    parsed: ParsedResponse | None,
    run_id: str,
    conn,
    reused_from_run_id: str | None = None,
) -> int:
    """Write analysis rows in one atomic transaction. Returns attempt_id.

    Four cases:
      1. reused_from_run_id set    → cache hit; write ai_analysis copy-forward only
      2. parsed.is_valid           → write ai_analysis + claims + citations
      3. not parsed.is_valid       → write analysis_attempts failure row only
      4. parsed is None (dry run)  → write analysis_attempts with outcome 'api_error'

    In all cases analysis_attempts is written; in case 3/4 that is the only row.
    """
    now = datetime.now(timezone.utc).isoformat()
    doc_sha256s = _doc_sha256s_for_result(result, conn)
    bundle_key  = compute_bundle_key(
        doc_sha256s,
        result.prompt_sha256,
        result.model_id,
        NORM_VERSION,
        result.protocol_version,
    )

    _ensure_pipeline_run(run_id, conn)

    try:
        # ---- Case 1: cache hit ----
        if reused_from_run_id is not None:
            for at in ANALYSIS_TYPES:
                orig = conn.execute(
                    "SELECT content, claim_coverage FROM ai_analysis "
                    "WHERE run_id = ? AND ticker = ? AND analysis_type = ?",
                    (reused_from_run_id, result.ticker, at),
                ).fetchone()
                if orig:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO ai_analysis
                          (run_id, ticker, analysis_type, content, model,
                           prompt_version, cache_key, is_current,
                           reused_from_run_id, claim_coverage, created_at)
                        VALUES (?,?,?,?,?,?,?,1,?,?,?)
                        """,
                        (run_id, result.ticker, at,
                         orig["content"], result.model_id,
                         result.protocol_version, bundle_key,
                         reused_from_run_id, orig["claim_coverage"], now),
                    )

            _supersede_for_bundle(conn, result.ticker, bundle_key, run_id)

            attempt_id = _write_attempt(
                conn, run_id, result, bundle_key, now,
                outcome="persisted",
                failure_reason=f"cache_hit:reused_from={reused_from_run_id}",
            )
            conn.commit()
            return attempt_id

        # ---- Case 1b: API refusal ----
        if result.stop_reason == "refusal":
            attempt_id = _write_attempt(
                conn, run_id, result, bundle_key, now,
                outcome="refused",
            )
            conn.commit()
            return attempt_id

        # ---- Case 1c: batch item error (no message was ever generated) ----
        # retrieve_batch() sets stop_reason="api_error" for a batch result of
        # type "error" — the item itself failed on Anthropic's side. There's
        # no content to parse, so this must not fall through to Case 3/4,
        # which would misfile it as a validation failure.
        if result.stop_reason == "api_error":
            attempt_id = _write_attempt(
                conn, run_id, result, bundle_key, now,
                outcome="api_error",
                failure_reason="batch_item_error",
            )
            conn.commit()
            return attempt_id

        # ---- Case 3/4: validation failed or no parsed result ----
        if parsed is None or not parsed.is_valid:
            failure = "; ".join((parsed.validation_errors if parsed else ["no parsed result"]))
            attempt_id = _write_attempt(
                conn, run_id, result, bundle_key, now,
                outcome="validation_failed",
                failure_reason=failure,
            )
            conn.commit()
            return attempt_id

        # ---- Case 2: valid — write full rows ----
        coverage = parsed.claim_coverage
        if coverage < 1.0:
            attempt_id = _write_attempt(
                conn, run_id, result, bundle_key, now,
                outcome="validation_failed",
                failure_reason=f"claim_coverage={coverage:.3f} < 1.0",
            )
            conn.commit()
            return attempt_id

        # Assemble per-type content text from claims.
        type_content: dict[str, str] = {at: "" for at in ANALYSIS_TYPES}
        for claim in parsed.claims:
            prefix = (
                f"CLAIM: {claim.claim_text}"
                if claim.assertion_status == "asserted"
                else f"INSUFFICIENT EVIDENCE: {claim.claim_text}"
            )
            type_content[claim.analysis_type] += prefix + "\n"

        for at in ANALYSIS_TYPES:
            conn.execute(
                """
                INSERT OR REPLACE INTO ai_analysis
                  (run_id, ticker, analysis_type, content, model,
                   prompt_version, cache_key, is_current,
                   reused_from_run_id, claim_coverage, created_at)
                VALUES (?,?,?,?,?,?,?,1,NULL,?,?)
                """,
                (run_id, result.ticker, at,
                 type_content[at].strip(), result.model_id,
                 result.protocol_version, bundle_key, coverage, now),
            )

        for claim in parsed.claims:
            conn.execute(
                """
                INSERT INTO analysis_claims
                  (run_id, ticker, analysis_type, claim_order,
                   claim_text, assertion_status)
                VALUES (?,?,?,?,?,?)
                """,
                (run_id, result.ticker, claim.analysis_type,
                 claim.claim_order, claim.claim_text, claim.assertion_status),
            )
            claim_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            for cite in claim.citations:
                conn.execute(
                    """
                    INSERT INTO citations
                      (claim_id, accession_number, section_id, doc_sha256,
                       norm_version, start_char, end_char, quote,
                       quote_sha256, prefix, suffix, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (claim_id, cite.accession_number, cite.section_id,
                     cite.doc_sha256, cite.norm_version,
                     cite.start_char, cite.end_char,
                     cite.cited_text, cite.quote_sha256,
                     cite.prefix, cite.suffix, now),
                )

        attempt_id = _write_attempt(
            conn, run_id, result, bundle_key, now, outcome="persisted",
        )
        conn.commit()
        return attempt_id

    except Exception:
        conn.rollback()
        raise


def write_stage_failure(
    ticker: str,
    run_id: str,
    failure_reason: str,
    model_id: str,
    conn,
) -> int:
    """Write an analysis_attempts audit row for a pre-API failure.

    Used when no CallResult exists — W1 couldn't fetch the filing, or section
    extraction raised before any API call was attempted. Uses outcome='api_error'
    (the existing bucket for infrastructure failures); failure_reason carries
    a typed prefix: 'w1_failed:', 'no_filing:', or 'extraction_failed:'.

    Never raises — this is itself the failure-recording path, so a DB error
    here must not take down the rest of the pipeline stage. Returns -1 (an
    unusable rowid) if the write itself fails, after logging to stderr.
    """
    now = datetime.now(timezone.utc).isoformat()
    try:
        _ensure_pipeline_run(run_id, conn)
        conn.execute(
            """
            INSERT INTO analysis_attempts
              (run_id, ticker, batch_id, custom_id, accession_number, model_id,
               prompt_sha256, protocol_version, document_map, usage_json,
               cost_estimate, outcome, failure_reason, raw_response, created_at)
            VALUES (?,?,NULL,NULL,NULL,?,?,?,?,?,?,?,?,NULL,?)
            """,
            (
                run_id, ticker,
                model_id, "none",
                PROTOCOL_VERSION,
                json.dumps({}), json.dumps({}),
                0.0,
                "api_error", failure_reason,
                now,
            ),
        )
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        print(
            f"    write_stage_failure: failed to record '{failure_reason}' "
            f"for {ticker} (run {run_id}): {exc}",
            file=sys.stderr,
        )
        return -1


def _write_attempt(
    conn,
    run_id: str,
    result: CallResult,
    bundle_key: str,
    now: str,
    outcome: str,
    failure_reason: str | None = None,
) -> int:
    """Write (or, for a batch item, resolve) this ticker's analysis_attempts row.

    result.custom_id is only set for batch items — sync calls always pass
    NULL, and SQLite's UNIQUE never treats NULLs as conflicting, so this is a
    plain INSERT for every sync call. For a batch item, submit_and_persist_batch()
    already wrote a 'pending' row under this exact custom_id; ON CONFLICT
    updates it in place rather than inserting a second row (custom_id is
    UNIQUE), which is how retrieval resolves a pending frame to its outcome.
    """
    doc_map = {int(k): v for k, v in result.document_map.items()}
    conn.execute(
        """
        INSERT INTO analysis_attempts
          (run_id, ticker, batch_id, custom_id, accession_number, model_id,
           prompt_sha256, protocol_version, document_map, usage_json,
           cost_estimate, outcome, failure_reason, raw_response, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(custom_id) DO UPDATE SET
            run_id           = excluded.run_id,
            batch_id         = excluded.batch_id,
            accession_number = excluded.accession_number,
            model_id         = excluded.model_id,
            prompt_sha256    = excluded.prompt_sha256,
            protocol_version = excluded.protocol_version,
            document_map     = excluded.document_map,
            usage_json       = excluded.usage_json,
            cost_estimate    = excluded.cost_estimate,
            outcome          = excluded.outcome,
            failure_reason   = excluded.failure_reason,
            raw_response     = excluded.raw_response,
            created_at       = excluded.created_at
        """,
        (
            run_id, result.ticker,
            result.batch_id, result.custom_id, result.accession,
            result.model_id, result.prompt_sha256,
            result.protocol_version,
            json.dumps(doc_map),
            json.dumps(result.usage),
            result.cost_estimate,
            outcome, failure_reason,
            json.dumps(result.content_blocks) if result.content_blocks else None,
            now,
        ),
    )
    if result.custom_id:
        return conn.execute(
            "SELECT attempt_id FROM analysis_attempts WHERE custom_id = ?",
            (result.custom_id,),
        ).fetchone()[0]
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# ---------------------------------------------------------------------------
# Cache-hit audit receipt
# ---------------------------------------------------------------------------

def _write_cache_attempt(
    conn,
    run_id: str,
    ticker: str,
    model_id: str,
    prompt_sha256: str,
    protocol_version: str,
    reused_from_run_id: str,
    now: str,
    accession: str | None = None,
) -> int:
    """Write the analysis_attempts audit row for a cache-hit (no API call made)."""
    conn.execute(
        """
        INSERT INTO analysis_attempts
          (run_id, ticker, batch_id, custom_id, accession_number, model_id,
           prompt_sha256, protocol_version, document_map, usage_json,
           cost_estimate, outcome, failure_reason, raw_response, created_at)
        VALUES (?,?,NULL,NULL,?,?,?,?,?,?,?,?,?,NULL,?)
        """,
        (run_id, ticker, accession, model_id, prompt_sha256,
         protocol_version,
         json.dumps({}),
         json.dumps({}),
         0.0,
         "persisted",
         f"cache_hit:reused_from={reused_from_run_id}",
         now),
    )
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# ---------------------------------------------------------------------------
# Filing resolution with amendment fallback (Decision 3, sprint-3-plan.md)
#
# Shared by run_analysis() (sync) and find_ticker_bundle() (batch precheck)
# so the two paths can't drift on which companies the fallback rescues — a
# real Sprint 3.1 gap found by external judge review: find_ticker_bundle()
# originally had no fallback at all, silently dropping any company whose
# latest 10-K/A is a Part-III-only stub from the batch instead of retrying
# the original 10-K the way the sync path always has.
# ---------------------------------------------------------------------------

def _resolve_sections_with_amendment_fallback(
    ticker: str, conn,
) -> tuple[str, dict, str] | tuple[None, None, tuple[str, str]]:
    """Resolve (accession, sections, period) for a ticker, retrying the
    original 10-K when the latest 10-K/A's section extraction fails.

    Returns (accession, sections, period) on success.
    Returns (None, None, (error_kind, error_detail)) on failure, where
    error_kind is 'no_filing' or 'extraction_failed' — callers prefix
    write_stage_failure()/find_ticker_bundle()'s error string with it.
    """
    filing = conn.execute(
        "SELECT accession_number, period_of_report, form_type FROM filings "
        "WHERE ticker = ? AND local_path IS NOT NULL "
        "ORDER BY period_of_report DESC, filing_date DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    if not filing:
        return None, None, ("no_filing", "no cached filing — run W1 first")

    accession = filing["accession_number"]
    period    = filing["period_of_report"] or "unknown"

    try:
        sections = prepare_sections(accession, conn)
        return accession, sections, period
    except ValueError as first_err:
        if filing["form_type"] != "10-K/A":
            return None, None, ("extraction_failed", str(first_err))

        fallback_row = conn.execute(
            "SELECT accession_number FROM filings "
            "WHERE ticker = ? AND form_type = '10-K' "
            "AND period_of_report = ? AND local_path IS NOT NULL "
            "ORDER BY filing_date DESC LIMIT 1",
            (ticker, filing["period_of_report"]),
        ).fetchone()
        if not fallback_row:
            return None, None, ("extraction_failed", str(first_err))

        try:
            accession = fallback_row["accession_number"]
            sections  = prepare_sections(accession, conn)
            return accession, sections, period
        except ValueError as second_err:
            return None, None, ("extraction_failed", str(second_err))


# ---------------------------------------------------------------------------
# Cache precheck (Sprint 3.1, item 2 — used by the batch submission path)
#
# submit_batch() has no cache check of its own: every ticker handed to it
# goes through a paid API call even if its bundle is already analyzed.
# run_analysis() (the sync path) avoids that with an inline cache check, but
# that logic lives inside one big function. These two are the same check
# and the same copy-forward write, factored out so the batch path can skip
# cache hits before spending anything, without duplicating run_analysis's
# innards (which item 4 still owes a first test).
# ---------------------------------------------------------------------------

def find_ticker_bundle(ticker: str, model_id: str, conn) -> dict:
    """Resolve the filing/sections/prompt for a ticker and check the cache.

    Does the same W2/W3 prep call_sync() does, without calling the API —
    including the amendment fallback (Decision 3), shared with run_analysis()
    via _resolve_sections_with_amendment_fallback().

    Returns one of:
      {"error": str}
      {"reused_from": run_id_or_None, "bundle_key": str, "accession": str,
       "prompt_sha": str, "content": list[dict]}
        — "content" is the built request body, returned so a batch-mode
        preflight cap check can count its tokens without rebuilding it.
    """
    accession, sections, info = _resolve_sections_with_amendment_fallback(ticker, conn)
    if sections is None:
        error_kind, error_detail = info
        return {"error": f"{error_kind}: {error_detail}"}
    period = info

    section_texts  = {k: v[0] for k, v in sections.items()}
    filing_doc_ids = {k: v[1] for k, v in sections.items()}
    content, _ = build_request(
        section_texts, ticker, period, filing_doc_ids,
        gap_sections=compute_gap_sections(section_texts),
    )
    prompt_sha = _prompt_sha256(SYSTEM_PROMPT, content)

    doc_sha256s = []
    for fdi in filing_doc_ids.values():
        row = conn.execute(
            "SELECT doc_sha256 FROM filing_documents WHERE filing_document_id = ?",
            (fdi,),
        ).fetchone()
        if row:
            doc_sha256s.append(row["doc_sha256"])

    bundle_key = compute_bundle_key(doc_sha256s, prompt_sha, model_id, NORM_VERSION, PROTOCOL_VERSION)
    reused_from = find_cached_run(ticker, bundle_key, conn)
    return {
        "reused_from": reused_from,
        "bundle_key":  bundle_key,
        "accession":   accession,
        "prompt_sha":  prompt_sha,
        "content":     content,
    }


def persist_cache_hit(
    ticker: str,
    run_id: str,
    model_id: str,
    reused_from: str,
    bundle_key: str,
    prompt_sha: str,
    accession: str,
    conn,
) -> int:
    """Copy an already-analyzed bundle forward under a new run_id. Zero API calls.

    Same copy-forward + supersede + audit-attempt sequence as run_analysis()'s
    cache-hit branch and persist_result()'s case 1, for callers (the batch
    precheck) that need to do it before a CallResult exists.
    """
    _ensure_pipeline_run(run_id, conn)
    now = datetime.now(timezone.utc).isoformat()
    for at in ANALYSIS_TYPES:
        orig = conn.execute(
            "SELECT content, claim_coverage FROM ai_analysis "
            "WHERE run_id = ? AND ticker = ? AND analysis_type = ?",
            (reused_from, ticker, at),
        ).fetchone()
        if orig:
            conn.execute(
                """
                INSERT OR REPLACE INTO ai_analysis
                  (run_id, ticker, analysis_type, content, model,
                   prompt_version, cache_key, is_current,
                   reused_from_run_id, claim_coverage, created_at)
                VALUES (?,?,?,?,?,?,?,1,?,?,?)
                """,
                (run_id, ticker, at, orig["content"], model_id,
                 PROTOCOL_VERSION, bundle_key, reused_from, orig["claim_coverage"], now),
            )
    _supersede_for_bundle(conn, ticker, bundle_key, run_id)
    attempt_id = _write_cache_attempt(
        conn, run_id, ticker, model_id, prompt_sha, PROTOCOL_VERSION, reused_from, now,
        accession=accession,
    )
    conn.commit()
    return attempt_id


# ---------------------------------------------------------------------------
# Batch workflow (Sprint 3.1, item 2)
#
# submit_batch() (caller.py) was previously a dead end: it returned a
# batch_id and in-memory partial CallResults that nothing ever persisted or
# retrieved. The fix makes the batch durable at submission time and gives
# retrieval a DB-only entry point:
#   submit_and_persist_batch() — submits, then writes one 'pending'
#     analysis_attempts row per ticker before returning.
#   run_batch_retrieval()      — reconstructs its work list from those
#     'pending' rows (not from any in-memory state) and persists each
#     resolved item through the same persist_result() every other path
#     uses — four analyses or an analysis_attempts row, never partial.
# A crash between the two, or a process restart, loses nothing: the pending
# rows are the queue, and a second run_batch_retrieval() call for the same
# batch_id only sees whatever is still 'pending'.
# ---------------------------------------------------------------------------

def _write_pending_batch_attempts(
    run_id: str,
    partials: dict[str, CallResult],
    conn,
) -> dict[str, int]:
    """Write a 'pending' request frame for every ticker in a submitted batch.

    Called immediately after the Batch API confirms batch_id, before
    returning to the caller — this is the durable record retrieval reads
    back later; nothing else needs to survive in memory.
    """
    _ensure_pipeline_run(run_id, conn)
    now = datetime.now(timezone.utc).isoformat()
    attempt_ids: dict[str, int] = {}
    for ticker, partial in partials.items():
        doc_map = {int(k): v for k, v in partial.document_map.items()}
        conn.execute(
            """
            INSERT INTO analysis_attempts
              (run_id, ticker, batch_id, custom_id, accession_number, model_id,
               prompt_sha256, protocol_version, document_map, usage_json,
               cost_estimate, outcome, failure_reason, raw_response, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,'pending',NULL,NULL,?)
            """,
            (
                run_id, ticker, partial.batch_id, partial.custom_id, partial.accession,
                partial.model_id, partial.prompt_sha256, partial.protocol_version,
                json.dumps(doc_map), json.dumps({}), 0.0, now,
            ),
        )
        attempt_ids[ticker] = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    return attempt_ids


def submit_and_persist_batch(
    client,
    tickers: list[str],
    conn,
    run_id: str,
    model_id: str = DEFAULT_MODEL,
) -> tuple[str, dict[str, int]]:
    """Submit a batch and persist a pending request frame per ticker.

    Returns (batch_id, {ticker: attempt_id}). See module note above — this
    is what makes the batch resumable by run_batch_retrieval() alone.
    """
    from moat.analysis.caller import submit_batch

    batch_id, partials = submit_batch(client, tickers, conn, model_id=model_id)
    attempt_ids = _write_pending_batch_attempts(run_id, partials, conn)
    return batch_id, attempt_ids


def _load_pending_batch(conn, batch_id: str) -> dict[str, CallResult]:
    """Reconstruct partial CallResults for a batch from their persisted
    request frames — the DB, not any in-memory state, is the source of truth
    for what a batch retrieval still owes."""
    rows = conn.execute(
        "SELECT ticker, custom_id, accession_number, model_id, prompt_sha256, "
        "protocol_version, document_map FROM analysis_attempts "
        "WHERE batch_id = ? AND outcome = 'pending'",
        (batch_id,),
    ).fetchall()
    partials: dict[str, CallResult] = {}
    for row in rows:
        doc_map = {int(k): v for k, v in json.loads(row["document_map"]).items()}
        partials[row["ticker"]] = CallResult(
            ticker=row["ticker"],
            accession=row["accession_number"],
            model_id=row["model_id"],
            stop_reason="pending",
            content_blocks=[],
            document_map=doc_map,
            usage={},
            prompt_sha256=row["prompt_sha256"],
            protocol_version=row["protocol_version"],
            cost_estimate=0.0,
            is_batch=True,
            batch_id=batch_id,
            custom_id=row["custom_id"],
        )
    return partials


def run_batch_retrieval(
    client,
    batch_id: str,
    run_id: str,
    conn,
    model_id: str = DEFAULT_MODEL,
) -> dict:
    """Retrieve a batch's results and persist every item. Resumable.

    Only call once the batch status is 'ended' — this does not poll.
    Loads its work list from 'pending' analysis_attempts rows for this
    batch_id; a ticker with no such row (already resolved by a prior call,
    or never submitted) is simply not revisited.
    """
    from moat.analysis.caller import retrieve_batch

    partials = _load_pending_batch(conn, batch_id)
    if not partials:
        return {"batch_id": batch_id, "outcomes": {}, "still_pending": 0}

    hydrated = retrieve_batch(client, batch_id, partials, model_id=model_id)

    outcomes: dict[str, int] = {}
    still_pending = 0
    for ticker, result in hydrated.items():
        if result.stop_reason == "pending":
            # Not present in the batch's results yet — status should be
            # 'ended' by the time this is called, but retrieve_batch() takes
            # that on faith and doesn't poll. Leave it pending for a later call.
            still_pending += 1
            continue

        # "refusal" and "api_error" carry no generated content — persist_result()
        # writes the attempts row straight from stop_reason and must not be
        # handed a parsed response for either.
        parsed = None
        if result.stop_reason not in ("refusal", "api_error"):
            parsed = parse_and_validate(result, conn)

        attempt_id = persist_result(result, parsed, run_id, conn)
        outcome = conn.execute(
            "SELECT outcome FROM analysis_attempts WHERE attempt_id = ?", (attempt_id,)
        ).fetchone()["outcome"]
        outcomes[outcome] = outcomes.get(outcome, 0) + 1

    return {"batch_id": batch_id, "outcomes": outcomes, "still_pending": still_pending}


# ---------------------------------------------------------------------------
# High-level orchestrator: check cache → call → parse → persist
# ---------------------------------------------------------------------------

def run_analysis(
    ticker: str,
    run_id: str,
    client,
    conn,
    model_id: str = DEFAULT_MODEL,
    dry_run: bool = False,
) -> dict:
    """Full W3→W4→W5 pipeline for one ticker. Returns a status dict.

    Cache hit:   no API call; reused_from_run_id written to ai_analysis.
    Dry run:     counts tokens only; no API call, no DB writes.
    Fresh call:  API → parse → validate → persist.
    """
    import anthropic as _anthropic
    from moat.analysis.caller import call_sync

    # Prepare sections (W2 on demand). Amendment-first: prefer the most
    # recent 10-K/A, fall back to original 10-K for the same period when the
    # amendment fails extraction (Decision 3, sprint-3-plan.md) — shared with
    # find_ticker_bundle() via _resolve_sections_with_amendment_fallback().
    accession, sections, info = _resolve_sections_with_amendment_fallback(ticker, conn)
    if sections is None:
        error_kind, error_detail = info
        if not dry_run:
            write_stage_failure(ticker, run_id, f"{error_kind}: {error_detail}", model_id, conn)
        return {"ticker": ticker, "outcome": "api_error", "reason": error_detail}
    period = info

    used_full_fallback = "full" in sections
    section_texts  = {k: v[0] for k, v in sections.items()}
    filing_doc_ids = {k: v[1] for k, v in sections.items()}

    # For sections_partial (no "full" key): inject gap notice for IBR sections.
    gap_sections = compute_gap_sections(section_texts)

    content, document_map = build_request(section_texts, ticker, period, filing_doc_ids,
                                          gap_sections=gap_sections)
    prompt_sha = _prompt_sha256(SYSTEM_PROMPT, content)

    # Get doc_sha256s for bundle key
    doc_sha256s = []
    for fdi in filing_doc_ids.values():
        row = conn.execute(
            "SELECT doc_sha256 FROM filing_documents WHERE filing_document_id = ?",
            (fdi,),
        ).fetchone()
        if row:
            doc_sha256s.append(row["doc_sha256"])

    bundle_key = compute_bundle_key(
        doc_sha256s, prompt_sha, model_id, NORM_VERSION, PROTOCOL_VERSION,
    )

    if dry_run:
        resp = client.messages.count_tokens(
            model=model_id,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        )
        from moat.analysis.pricing import format_dry_run_report
        print(format_dry_run_report(ticker, model_id, resp.input_tokens, is_batch=False))
        return {"ticker": ticker, "outcome": "dry_run", "input_tokens": resp.input_tokens}

    # Cache check
    reused_from = find_cached_run(ticker, bundle_key, conn)
    if reused_from:
        _ensure_pipeline_run(run_id, conn)
        # Write copy-forward rows without a real CallResult (no content_blocks needed)
        now = datetime.now(timezone.utc).isoformat()
        for at in ANALYSIS_TYPES:
            orig = conn.execute(
                "SELECT content, claim_coverage FROM ai_analysis "
                "WHERE run_id = ? AND ticker = ? AND analysis_type = ?",
                (reused_from, ticker, at),
            ).fetchone()
            if orig:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO ai_analysis
                      (run_id, ticker, analysis_type, content, model,
                       prompt_version, cache_key, is_current,
                       reused_from_run_id, claim_coverage, created_at)
                    VALUES (?,?,?,?,?,?,?,1,?,?,?)
                    """,
                    (run_id, ticker, at,
                     orig["content"], model_id,
                     PROTOCOL_VERSION, bundle_key,
                     reused_from, orig["claim_coverage"], now),
                )
        _supersede_for_bundle(conn, ticker, bundle_key, run_id)
        attempt_id = _write_cache_attempt(
            conn, run_id, ticker, model_id, prompt_sha,
            PROTOCOL_VERSION, reused_from, now, accession=accession,
        )
        conn.commit()
        return {
            "ticker": ticker, "outcome": "cache_hit",
            "reused_from_run_id": reused_from, "attempt_id": attempt_id,
            "used_full_fallback": used_full_fallback,
        }

    # Fresh call — pin the resolved accession so call_sync uses the same filing
    # we already prepared sections for (matters when fallback switched to original 10-K).
    result = call_sync(client, ticker, conn, model_id=model_id, dry_run=False, accession=accession)

    if result.stop_reason == "refusal":
        _ensure_pipeline_run(run_id, conn)
        _write_attempt(conn, run_id, result, bundle_key, datetime.now(timezone.utc).isoformat(),
                       outcome="refused")
        conn.commit()
        return {"ticker": ticker, "outcome": "refused", "used_full_fallback": used_full_fallback}

    parsed = parse_and_validate(result, conn)
    attempt_id = persist_result(result, parsed, run_id, conn)

    return {
        "ticker":              ticker,
        "outcome":             "persisted" if parsed.is_valid else "validation_failed",
        "attempt_id":          attempt_id,
        "claim_coverage":      parsed.claim_coverage,
        "errors":              parsed.validation_errors,
        "cost_estimate":       result.cost_estimate,
        "used_full_fallback":  used_full_fallback,
    }
