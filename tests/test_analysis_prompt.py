"""Tests for W3 prompt construction and pricing helpers."""
import hashlib
import json

from moat.analysis.prompt import (
    ANALYSIS_TYPES,
    PROTOCOL_VERSION,
    SYSTEM_PROMPT,
    build_request,
)
from moat.analysis.pricing import estimate_cost, get_prices, BATCH_DISCOUNT


# ---------------------------------------------------------------------------
# build_request
# ---------------------------------------------------------------------------

def _sections() -> tuple[dict[str, str], dict[str, int]]:
    texts = {
        "item_1":  "Business description text. " * 100,
        "item_1a": "Risk factors text. " * 100,
        "item_7":  "MD&A discussion text. " * 100,
    }
    ids = {"item_1": 1, "item_1a": 2, "item_7": 3}
    return texts, ids


def test_build_request_document_count():
    texts, ids = _sections()
    content, doc_map = build_request(texts, "AAPL", "2024-09-28", ids)
    # 3 document blocks + 1 text instruction = 4
    doc_blocks = [b for b in content if b["type"] == "document"]
    assert len(doc_blocks) == 3


def test_build_request_citations_enabled():
    texts, ids = _sections()
    content, _ = build_request(texts, "AAPL", "2024-09-28", ids)
    for block in content:
        if block["type"] == "document":
            assert block["citations"] == {"enabled": True}


def test_build_request_document_map_covers_all_sections():
    texts, ids = _sections()
    _, doc_map = build_request(texts, "AAPL", "2024-09-28", ids)
    # document_map must have an entry for every document block (0-based indices)
    doc_count = sum(1 for b in build_request(texts, "AAPL", "2024-09-28", ids)[0]
                    if b["type"] == "document")
    assert set(doc_map.keys()) == set(range(doc_count))
    assert set(doc_map.values()) == {1, 2, 3}


def test_build_request_doc_map_values_match_ids():
    texts, ids = _sections()
    content, doc_map = build_request(texts, "AAPL", "2024-09-28", ids)
    doc_blocks = [b for b in content if b["type"] == "document"]
    for idx, block in enumerate(doc_blocks):
        # The filing_document_id in the map must correspond to the section data
        assert doc_map[idx] in ids.values()


def test_build_request_ticker_in_title():
    texts, ids = _sections()
    content, _ = build_request(texts, "MSFT", "2023-06-30", ids)
    titles = [b["title"] for b in content if b["type"] == "document"]
    assert all("MSFT" in t for t in titles)


def test_build_request_plain_text_source():
    texts, ids = _sections()
    content, _ = build_request(texts, "AAPL", "2024-09-28", ids)
    for block in content:
        if block["type"] == "document":
            assert block["source"]["type"] == "text"
            assert block["source"]["media_type"] == "text/plain"


def test_build_request_full_fallback_only():
    """When only 'full' is available, document_map has exactly one entry."""
    texts = {"full": "Full filing text. " * 500}
    ids   = {"full": 99}
    content, doc_map = build_request(texts, "AAPL", "2024-09-28", ids)
    doc_blocks = [b for b in content if b["type"] == "document"]
    assert len(doc_blocks) == 1
    assert doc_map == {0: 99}


def test_build_request_ends_with_text_instruction():
    texts, ids = _sections()
    content, _ = build_request(texts, "AAPL", "2024-09-28", ids)
    assert content[-1]["type"] == "text"
    assert "AAPL" in content[-1]["text"]


def test_build_request_deterministic():
    """Same inputs → identical document_map and content block titles."""
    texts, ids = _sections()
    content1, map1 = build_request(texts, "AAPL", "2024-09-28", ids)
    content2, map2 = build_request(texts, "AAPL", "2024-09-28", ids)
    assert map1 == map2
    titles1 = [b.get("title") for b in content1 if b["type"] == "document"]
    titles2 = [b.get("title") for b in content2 if b["type"] == "document"]
    assert titles1 == titles2


# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------

def test_system_prompt_contains_all_headers():
    for header in ("## BUSINESS QUALITY", "## MOAT", "## MANAGEMENT", "## RISK"):
        assert header in SYSTEM_PROMPT, f"missing header: {header}"


def test_system_prompt_contains_management_note():
    assert "10-K only" in SYSTEM_PROMPT
    assert "compensation" in SYSTEM_PROMPT


def test_system_prompt_insufficient_evidence_not_stop_sequence():
    """INSUFFICIENT EVIDENCE must appear as a parseable status, not a stop sequence."""
    assert "INSUFFICIENT EVIDENCE:" in SYSTEM_PROMPT


def test_analysis_types_stable():
    assert set(ANALYSIS_TYPES) == {"business_quality", "moat", "management", "risk"}


def test_protocol_version_is_string():
    assert isinstance(PROTOCOL_VERSION, str)
    assert PROTOCOL_VERSION  # non-empty


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

def test_estimate_cost_zero_usage():
    cost = estimate_cost({}, "claude-sonnet-4-6")
    assert cost == 0.0


def test_estimate_cost_batch_half_price():
    usage = {"input_tokens": 100_000, "output_tokens": 8_000}
    sync_cost  = estimate_cost(usage, "claude-sonnet-4-6", is_batch=False)
    batch_cost = estimate_cost(usage, "claude-sonnet-4-6", is_batch=True)
    assert abs(batch_cost - sync_cost * BATCH_DISCOUNT) < 1e-9


def test_estimate_cost_known_value():
    """Spot-check: 80k input + 8k output, sonnet, sync."""
    prices = get_prices("claude-sonnet-4-6")
    usage = {"input_tokens": 80_000, "output_tokens": 8_000}
    expected = (
        80_000 * prices["input"] / 1_000_000
        + 8_000 * prices["output"] / 1_000_000
    )
    assert abs(estimate_cost(usage, "claude-sonnet-4-6") - expected) < 1e-9


def test_get_prices_unknown_model():
    import pytest
    with pytest.raises(ValueError, match="No pricing data"):
        get_prices("claude-unknown-99")
