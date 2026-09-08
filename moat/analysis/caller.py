"""W3: section preparation, prompt construction, and API call (Sprint 3).

Entry point: call_sync() for a single ticker (streaming, no timeout risk).
             submit_batch() to submit a batch of tickers.

Section preparation runs W2 (section_extractor) on demand and persists results
to filing_documents so subsequent calls are pure DB reads.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from moat.config import ANTHROPIC_API_KEY, DATA_DIR
from moat.ingest.section_extractor import NORM_VERSION, extract_sections, normalize
from moat.analysis.prompt import (
    PROTOCOL_VERSION,
    SYSTEM_PROMPT,
    build_request,
)
from moat.analysis.pricing import DEFAULT_MODEL, estimate_cost, format_dry_run_report

SECTIONS_DIR = DATA_DIR / "sections"

# Minimum normalized character count for a full-fallback document to be usable.
# Below this the filing is likely an amendment stub, a redirect, or a corrupt
# download — not a 10-K with meaningful Item 1/1A/7 text.
FULL_FALLBACK_MIN_CHARS = 20_000


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class CallResult:
    ticker: str
    accession: str
    model_id: str
    stop_reason: str
    content_blocks: list[dict]   # serialized API content blocks
    document_map: dict[int, int] # document_index → filing_document_id
    usage: dict
    prompt_sha256: str
    protocol_version: str
    cost_estimate: float
    is_batch: bool = False
    batch_id: str | None = None
    custom_id: str | None = None


# ---------------------------------------------------------------------------
# Section preparation (W2 on demand)
# ---------------------------------------------------------------------------

def _section_path(accession: str, section_id: str) -> Path:
    accn_clean = accession.replace("-", "")
    return SECTIONS_DIR / accn_clean / f"{section_id}_{NORM_VERSION}.txt"


def _save_section(accession: str, section_id: str, text: str) -> Path:
    path = _section_path(accession, section_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(text, encoding="utf-8")
    return path


def prepare_sections(accession: str, conn) -> dict[str, tuple[str, int]]:
    """Ensure filing_documents rows exist for this accession; return usable sections.

    Returns {section_id: (normalized_text, filing_document_id)}.

    Raises ValueError if the filing has no local_path (W1 not run yet).
    """
    rows = conn.execute(
        "SELECT section_id, local_path, filing_document_id "
        "FROM filing_documents WHERE accession_number = ? AND norm_version = ?",
        (accession, NORM_VERSION),
    ).fetchall()

    existing = {r["section_id"]: r for r in rows}

    # Confidence filter: skip 'failed' sections — they're already absent (filing_documents
    # only stores high/low/IBR). A full_fallback writes 'full'; sections writes item_*.
    result: dict[str, tuple[str, int]] = {}
    for row in existing.values():
        path = Path(row["local_path"])
        if path.exists():
            result[row["section_id"]] = (
                path.read_text(encoding="utf-8"),
                row["filing_document_id"],
            )

    if result:
        return result

    # No filing_documents rows yet — run W2 now.
    filing = conn.execute(
        "SELECT local_path, period_of_report, ticker FROM filings WHERE accession_number = ?",
        (accession,),
    ).fetchone()
    if not filing or not filing["local_path"]:
        raise ValueError(f"no local file for {accession} — run W1 first")

    raw_bytes = Path(filing["local_path"]).read_bytes()
    norm_text = normalize(raw_bytes)
    extraction = extract_sections(norm_text)

    now = datetime.now(timezone.utc).isoformat()
    method = extraction.overall_method

    if method in ("sections", "sections_partial"):
        for section_id in ("item_1", "item_1a", "item_7"):
            sec = extraction.sections[section_id]
            if sec.confidence not in ("high", "low"):
                continue
            text = norm_text[sec.start: sec.end]
            sha = hashlib.sha256(text.encode()).hexdigest()
            local_path = _save_section(accession, section_id, text)
            trace_json = json.dumps(
                extraction.trace.get("sections", {}).get(section_id), ensure_ascii=False
            ) if extraction.trace else None
            conn.execute(
                """
                INSERT OR IGNORE INTO filing_documents
                  (accession_number, section_id, norm_version, doc_sha256,
                   char_length, extraction_method, section_confidence,
                   local_path, extraction_trace, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (accession, section_id, NORM_VERSION, sha, len(text),
                 method, sec.confidence, str(local_path), trace_json, now),
            )
            row_id = conn.execute(
                "SELECT filing_document_id FROM filing_documents "
                "WHERE accession_number = ? AND section_id = ? AND norm_version = ?",
                (accession, section_id, NORM_VERSION),
            ).fetchone()["filing_document_id"]
            result[section_id] = (text, row_id)

    # sections_partial: do NOT fall back to the full document — the available
    # sections are sent as-is; build_request() injects a gap notice for the
    # missing (IBR) sections. full_fallback only when no sections were found.
    if method == "full_fallback" and "full" not in result:
        if len(norm_text) < FULL_FALLBACK_MIN_CHARS:
            raise ValueError(
                f"full_fallback for {accession} is implausibly short "
                f"({len(norm_text):,} chars < {FULL_FALLBACK_MIN_CHARS:,} floor) — "
                f"likely an amendment stub or corrupt download"
            )
        sha = hashlib.sha256(norm_text.encode()).hexdigest()
        local_path = _save_section(accession, "full", norm_text)
        full_trace_json = json.dumps(extraction.trace, ensure_ascii=False) if extraction.trace else None
        conn.execute(
            """
            INSERT OR IGNORE INTO filing_documents
              (accession_number, section_id, norm_version, doc_sha256,
               char_length, extraction_method, section_confidence,
               local_path, extraction_trace, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (accession, "full", NORM_VERSION, sha, len(norm_text),
             method, "low", str(local_path), full_trace_json, now),
        )
        row_id = conn.execute(
            "SELECT filing_document_id FROM filing_documents "
            "WHERE accession_number = ? AND section_id = ? AND norm_version = ?",
            (accession, "full", NORM_VERSION),
        ).fetchone()["filing_document_id"]
        result["full"] = (norm_text, row_id)

    conn.commit()

    if not result:
        raise ValueError(f"section extraction produced no usable sections for {accession}")

    return result


# ---------------------------------------------------------------------------
# Prompt SHA-256 (cache key for analysis_attempts + W5 bundle key)
# ---------------------------------------------------------------------------

def _prompt_sha256(system: str, content: list[dict]) -> str:
    payload = json.dumps({"system": system, "content": content}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Synchronous call (streaming to avoid SDK HTTP timeout on max_tokens=64000)
# ---------------------------------------------------------------------------

def call_sync(
    client: anthropic.Anthropic,
    ticker: str,
    conn,
    model_id: str = DEFAULT_MODEL,
    dry_run: bool = False,
    accession: str | None = None,
) -> CallResult:
    """Fetch sections, build request, call the API synchronously.

    accession: if provided, use this specific filing rather than the latest.
               Used by run_analysis() when retrying with an original 10-K
               after an amendment fails section extraction.
    dry_run=True: count tokens (free) and print the report; no generation.
    Raises ValueError if W1 hasn't been run for this ticker.
    """
    if accession is not None:
        filing = conn.execute(
            "SELECT accession_number, period_of_report FROM filings WHERE accession_number = ?",
            (accession,),
        ).fetchone()
        if not filing:
            raise ValueError(f"no filing row for accession {accession} — run W1 first")
    else:
        filing = conn.execute(
            "SELECT accession_number, period_of_report FROM filings "
            "WHERE ticker = ? AND local_path IS NOT NULL "
            "ORDER BY period_of_report DESC, filing_date DESC LIMIT 1",
            (ticker,),
        ).fetchone()
        if not filing:
            raise ValueError(f"no cached filing for {ticker} — run W1 first")

    accession = filing["accession_number"]
    period    = filing["period_of_report"] or "unknown"

    sections = prepare_sections(accession, conn)
    section_texts = {k: v[0] for k, v in sections.items()}
    filing_doc_ids = {k: v[1] for k, v in sections.items()}

    # For sections_partial: sections that are IBR are absent from section_texts.
    # Inject a gap notice so the model knows explicitly they are unavailable.
    if "full" not in section_texts:
        gap_sections = sorted({"item_1", "item_1a", "item_7"} - set(section_texts))
    else:
        gap_sections = []

    content, document_map = build_request(section_texts, ticker, period, filing_doc_ids,
                                          gap_sections=gap_sections)

    if dry_run:
        resp = client.messages.count_tokens(
            model=model_id,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
        )
        print(format_dry_run_report(ticker, model_id, resp.input_tokens, is_batch=False))
        return CallResult(
            ticker=ticker,
            accession=accession,
            model_id=model_id,
            stop_reason="dry_run",
            content_blocks=[],
            document_map=document_map,
            usage={"input_tokens": resp.input_tokens},
            prompt_sha256=_prompt_sha256(SYSTEM_PROMPT, content),
            protocol_version=PROTOCOL_VERSION,
            cost_estimate=0.0,
        )

    with client.messages.stream(
        model=model_id,
        max_tokens=64_000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    ) as stream:
        message = stream.get_final_message()

    if message.stop_reason == "refusal":
        # Return a refusal CallResult so the caller can persist the audit receipt
        # and continue processing other tickers without aborting.
        return CallResult(
            ticker=ticker,
            accession=accession,
            model_id=model_id,
            stop_reason="refusal",
            content_blocks=[],
            document_map=document_map,
            usage={
                "input_tokens":  message.usage.input_tokens,
                "output_tokens": message.usage.output_tokens,
            },
            prompt_sha256=_prompt_sha256(SYSTEM_PROMPT, content),
            protocol_version=PROTOCOL_VERSION,
            cost_estimate=0.0,
        )

    usage_dict = {
        "input_tokens":                   message.usage.input_tokens,
        "output_tokens":                  message.usage.output_tokens,
        "cache_creation_input_tokens":    getattr(message.usage, "cache_creation_input_tokens", 0),
        "cache_read_input_tokens":        getattr(message.usage, "cache_read_input_tokens", 0),
    }
    cost = estimate_cost(usage_dict, model_id, is_batch=False)

    return CallResult(
        ticker=ticker,
        accession=accession,
        model_id=model_id,
        stop_reason=message.stop_reason,
        content_blocks=[block.model_dump() for block in message.content],
        document_map=document_map,
        usage=usage_dict,
        prompt_sha256=_prompt_sha256(SYSTEM_PROMPT, content),
        protocol_version=PROTOCOL_VERSION,
        cost_estimate=cost,
    )


# ---------------------------------------------------------------------------
# Batch submission
# ---------------------------------------------------------------------------

def _custom_id(ticker: str, prompt_sha: str) -> str:
    return f"{ticker}_{prompt_sha[:16]}"


def submit_batch(
    client: anthropic.Anthropic,
    tickers: list[str],
    conn,
    model_id: str = DEFAULT_MODEL,
) -> tuple[str, dict[str, CallResult]]:
    """Build and submit a batch request for multiple tickers.

    Returns (batch_id, {ticker: partial_CallResult}).
    The partial results have stop_reason='pending' and empty content_blocks;
    they carry document_map + prompt_sha256 needed to write analysis_attempts rows.
    Retrieve results with retrieve_batch() once the batch completes.
    """
    requests = []
    partial: dict[str, CallResult] = {}

    for ticker in tickers:
        filing = conn.execute(
            "SELECT accession_number, period_of_report FROM filings "
            "WHERE ticker = ? AND local_path IS NOT NULL "
            "ORDER BY period_of_report DESC LIMIT 1",
            (ticker,),
        ).fetchone()
        if not filing:
            raise ValueError(f"no cached filing for {ticker} — run W1 first")

        accession = filing["accession_number"]
        period    = filing["period_of_report"] or "unknown"

        sections = prepare_sections(accession, conn)
        section_texts  = {k: v[0] for k, v in sections.items()}
        filing_doc_ids = {k: v[1] for k, v in sections.items()}

        content, document_map = build_request(section_texts, ticker, period, filing_doc_ids)
        sha = _prompt_sha256(SYSTEM_PROMPT, content)
        cid = _custom_id(ticker, sha)

        requests.append(
            anthropic.types.MessageCreateParamsNonStreaming(
                model=model_id,
                max_tokens=64_000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": content}],
                # Note: batch fallbacks param is rejected on the Batches API (API fact #2)
            )
        )
        # Store as a BatchRequestParam the SDK expects
        requests[-1] = {"custom_id": cid, "params": requests[-1]}

        partial[ticker] = CallResult(
            ticker=ticker,
            accession=accession,
            model_id=model_id,
            stop_reason="pending",
            content_blocks=[],
            document_map=document_map,
            usage={},
            prompt_sha256=sha,
            protocol_version=PROTOCOL_VERSION,
            cost_estimate=0.0,
            is_batch=True,
            custom_id=cid,
        )

    batch = client.messages.batches.create(requests=requests)
    batch_id = batch.id

    for result in partial.values():
        result.batch_id = batch_id

    return batch_id, partial


def retrieve_batch(
    client: anthropic.Anthropic,
    batch_id: str,
    partials: dict[str, "CallResult"],
    model_id: str = DEFAULT_MODEL,
) -> dict[str, "CallResult"]:
    """Poll a completed batch and hydrate the partial CallResults.

    Only call once the batch status is 'ended'. Caller is responsible for
    polling; this function does not wait.
    """
    custom_id_to_ticker = {p.custom_id: t for t, p in partials.items()}
    results = dict(partials)

    for item in client.messages.batches.results(batch_id):
        ticker = custom_id_to_ticker.get(item.custom_id)
        if ticker is None:
            continue

        partial = partials[ticker]

        if item.result.type == "error":
            results[ticker] = dataclasses.replace(
                partial, stop_reason="api_error",
            )
            continue

        message = item.result.message

        if message.stop_reason == "refusal":
            results[ticker] = dataclasses.replace(partial, stop_reason="refusal")
            continue

        usage_dict = {
            "input_tokens":                message.usage.input_tokens,
            "output_tokens":               message.usage.output_tokens,
            "cache_creation_input_tokens": getattr(message.usage, "cache_creation_input_tokens", 0),
            "cache_read_input_tokens":     getattr(message.usage, "cache_read_input_tokens", 0),
        }
        results[ticker] = dataclasses.replace(
            partial,
            stop_reason=message.stop_reason,
            content_blocks=[block.model_dump() for block in message.content],
            usage=usage_dict,
            cost_estimate=estimate_cost(usage_dict, model_id, is_batch=True),
        )

    return results
