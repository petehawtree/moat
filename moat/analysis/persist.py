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
              (run_id, ticker, batch_id, custom_id, model_id, prompt_sha256,
               protocol_version, document_map, usage_json, cost_estimate,
               outcome, failure_reason, raw_response, created_at)
            VALUES (?,?,NULL,NULL,?,?,?,?,?,?,?,?,NULL,?)
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
    doc_map = {int(k): v for k, v in result.document_map.items()}
    conn.execute(
        """
        INSERT INTO analysis_attempts
          (run_id, ticker, batch_id, custom_id, model_id, prompt_sha256,
           protocol_version, document_map, usage_json, cost_estimate,
           outcome, failure_reason, raw_response, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            run_id, result.ticker,
            result.batch_id, result.custom_id,
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
) -> int:
    """Write the analysis_attempts audit row for a cache-hit (no API call made)."""
    conn.execute(
        """
        INSERT INTO analysis_attempts
          (run_id, ticker, batch_id, custom_id, model_id, prompt_sha256,
           protocol_version, document_map, usage_json, cost_estimate,
           outcome, failure_reason, raw_response, created_at)
        VALUES (?,?,NULL,NULL,?,?,?,?,?,?,?,?,NULL,?)
        """,
        (run_id, ticker, model_id, prompt_sha256,
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

    # Prepare sections (W2 on demand).
    # Amendment-first: prefer the most recent 10-K/A, fall back to original
    # 10-K for the same period when the amendment fails extraction (Decision 3,
    # sprint-3-plan.md). W1 pre-fetches both so the fallback is usually a DB read.
    filing = conn.execute(
        "SELECT accession_number, period_of_report, form_type FROM filings "
        "WHERE ticker = ? AND local_path IS NOT NULL "
        "ORDER BY period_of_report DESC, filing_date DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    if not filing:
        if not dry_run:
            write_stage_failure(ticker, run_id, "no_filing: no cached filing — run W1 first",
                                model_id, conn)
        return {"ticker": ticker, "outcome": "api_error", "reason": "no cached filing — run W1 first"}

    accession = filing["accession_number"]
    period    = filing["period_of_report"] or "unknown"

    try:
        sections = prepare_sections(accession, conn)
    except ValueError as first_err:
        # If the primary is an amendment, retry with the original 10-K.
        if filing["form_type"] == "10-K/A":
            fallback_row = conn.execute(
                "SELECT accession_number FROM filings "
                "WHERE ticker = ? AND form_type = '10-K' "
                "AND period_of_report = ? AND local_path IS NOT NULL "
                "ORDER BY filing_date DESC LIMIT 1",
                (ticker, filing["period_of_report"]),
            ).fetchone()
            if fallback_row:
                try:
                    accession = fallback_row["accession_number"]
                    sections  = prepare_sections(accession, conn)
                except ValueError as second_err:
                    if not dry_run:
                        write_stage_failure(ticker, run_id,
                                            f"extraction_failed: {second_err}", model_id, conn)
                    return {"ticker": ticker, "outcome": "api_error",
                            "reason": str(second_err)}
            else:
                if not dry_run:
                    write_stage_failure(ticker, run_id,
                                        f"extraction_failed: {first_err}", model_id, conn)
                return {"ticker": ticker, "outcome": "api_error", "reason": str(first_err)}
        else:
            if not dry_run:
                write_stage_failure(ticker, run_id,
                                    f"extraction_failed: {first_err}", model_id, conn)
            return {"ticker": ticker, "outcome": "api_error", "reason": str(first_err)}

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
            PROTOCOL_VERSION, reused_from, now,
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
