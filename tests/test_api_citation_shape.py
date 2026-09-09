"""Credential-gated integration test: the real Anthropic API's citation
response shape (Sprint 3.1, item 4).

Every other parser test builds a CallResult by hand — none had ever checked
that a real citations-enabled response actually has the shape parser.py
assumes. This makes one minimal live call (a two-sentence document, a tiny
max_tokens, the cheapest model) and asserts the response matches what
_make_raw_cite()/resolve_citations() expect: char_location citations with
document_index/start_char_index/end_char_index/cited_text, where cited_text
is a true byte-exact substring of the source document at those offsets —
the exact property W4's write-path assertion depends on (§A15.5).

Skipped unless ANTHROPIC_API_KEY is set. This is the one test in the suite
that costs money (a fraction of a cent on claude-haiku) and needs live
credentials — it is not run by a bare `pytest`/CI invocation that lacks a
key, and this repo does not run it automatically either.
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="requires a live ANTHROPIC_API_KEY — makes one real, minimal (~$0.001) API call",
)


def test_real_citation_response_matches_parser_expectations():
    import anthropic

    from moat.analysis.parser import _make_raw_cite, _reconstruct_stream

    client = anthropic.Anthropic()
    document_text = (
        "Item 7. Management's Discussion and Analysis.\n\n"
        "Revenue grew 12% year over year to $45 million, driven by strong "
        "demand in the enterprise segment."
    )

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {"type": "text", "media_type": "text/plain", "data": document_text},
                    "title": "Test 10-K excerpt",
                    "citations": {"enabled": True},
                },
                {
                    "type": "text",
                    "text": "In one sentence, state the revenue growth figure from the "
                            "document, with a citation.",
                },
            ],
        }],
    )

    content_blocks = [block.model_dump() for block in message.content]
    cited_blocks = [b for b in content_blocks if b.get("citations")]
    assert cited_blocks, f"expected at least one cited block in response: {content_blocks}"

    all_cites = [c for b in cited_blocks for c in b["citations"]]
    assert all_cites
    for c in all_cites:
        assert c["type"] == "char_location"
        for field in ("document_index", "start_char_index", "end_char_index", "cited_text"):
            assert field in c, f"missing {field!r} in citation: {c}"

        raw = _make_raw_cite(c)
        assert raw.document_index == c["document_index"]
        assert raw.cited_text == c["cited_text"]

        # The write-path assertion (§A15.5: "cited_text must equal
        # normalized_text[start_char:end_char] byte-for-byte") depends on
        # this holding for the API's own offsets, not just our resolver.
        assert document_text[c["start_char_index"]:c["end_char_index"]] == c["cited_text"]

    # The stream reconstruction W4 relies on must also accept this shape.
    stream = _reconstruct_stream(content_blocks)
    assert stream
