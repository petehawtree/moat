"""Tests for the citation resolution ladder (scripts/cite.py, §A15.5).

Sprint 3 shipped only rung 1 (exact). Sprint 3.1 item 3 implements the
remaining five rungs — moved, moved_section, renormalized, fuzzy,
unresolved — plus the stale_analysis flag. No test covered any of this
before Sprint 3.1.
"""
import hashlib
import sqlite3

import pytest

from scripts.cite import _DEGRADED_RESULTS, _reanchor, _resolve_citation


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE ai_analysis (
            run_id TEXT, ticker TEXT, analysis_type TEXT,
            is_current INTEGER DEFAULT 1, stale_analysis INTEGER DEFAULT 0,
            reused_from_run_id TEXT,
            PRIMARY KEY (run_id, ticker, analysis_type)
        );
        CREATE TABLE analysis_claims (
            claim_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT, ticker TEXT, analysis_type TEXT, claim_order INTEGER
        );
        CREATE TABLE filing_documents (
            filing_document_id INTEGER PRIMARY KEY,
            accession_number TEXT, section_id TEXT, norm_version TEXT,
            doc_sha256 TEXT, local_path TEXT
        );
        CREATE TABLE citations (
            citation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_id INTEGER,
            accession_number TEXT, section_id TEXT, doc_sha256 TEXT, norm_version TEXT,
            start_char INTEGER, end_char INTEGER, quote TEXT,
            quote_sha256 TEXT, prefix TEXT, suffix TEXT, created_at TEXT
        );
        CREATE TABLE citation_resolution_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            citation_id INTEGER, checked_at TEXT, result TEXT, score REAL,
            resolved_doc_sha256 TEXT, resolved_start INTEGER, resolved_end INTEGER
        );
        """
    )
    return conn


def _write_section(conn, tmp_path, accession, section_id, text, norm_version="v1"):
    path = tmp_path / f"{accession}_{section_id}.txt"
    path.write_text(text, encoding="utf-8")
    sha = hashlib.sha256(text.encode()).hexdigest()
    conn.execute(
        "INSERT INTO filing_documents (accession_number, section_id, norm_version, "
        "doc_sha256, local_path) VALUES (?,?,?,?,?)",
        (accession, section_id, norm_version, sha, str(path)),
    )
    return sha


def _make_citation(conn, accession, section_id, doc_sha256, start, end, quote,
                    claim_id=1, norm_version="v1"):
    conn.execute(
        "INSERT INTO citations (citation_id, claim_id, accession_number, section_id, "
        "doc_sha256, norm_version, start_char, end_char, quote, quote_sha256, prefix, suffix, created_at) "
        "VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?)",
        (claim_id, accession, section_id, doc_sha256, norm_version, start, end, quote,
         hashlib.sha256(quote.encode()).hexdigest(), "", "", "2026-01-01"),
    )
    return conn.execute(
        "SELECT * FROM citations WHERE citation_id = (SELECT MAX(citation_id) FROM citations)"
    ).fetchone()


# ---------------------------------------------------------------------------
# _resolve_citation — one rung per test
# ---------------------------------------------------------------------------

def test_rung1_exact(tmp_path):
    conn = _conn()
    text = "Revenue grew 12% in the segment, driven by strong demand."
    quote = "grew 12% in the segment"
    start = text.index(quote)
    sha = _write_section(conn, tmp_path, "0001", "item_7", text)
    cite = _make_citation(conn, "0001", "item_7", sha, start, start + len(quote), quote)

    result = _resolve_citation(cite, conn)
    assert result["result"] == "exact"
    assert result["score"] == 1.0
    assert result["start"] == start


def test_rung2_moved(tmp_path):
    """Same doc_sha256 stored on the citation is now stale (content re-extracted
    under the same section_id) but the exact quote is still present, just at a
    different offset."""
    conn = _conn()
    text = "Some preamble text.\n\nRevenue grew 12% in the segment, driven by demand."
    quote = "grew 12% in the segment"
    real_start = text.index(quote)
    sha = _write_section(conn, tmp_path, "0001", "item_7", text)
    # Citation's stored doc_sha256/offsets are from a previous version of the text.
    cite = _make_citation(conn, "0001", "item_7", "stale_sha_not_matching", 0, len(quote), quote)

    result = _resolve_citation(cite, conn)
    assert result["result"] == "moved"
    assert result["start"] == real_start
    assert result["doc_sha256"] == sha


def test_rung3_moved_section(tmp_path):
    """Quote isn't in its recorded section at all anymore, but is present in
    a different section of the same filing (extraction drew the boundary
    differently on re-run)."""
    conn = _conn()
    item_7_text = "Nothing relevant here."
    item_1a_text = "Risk factors include: grew 12% in the segment, unexpectedly."
    _write_section(conn, tmp_path, "0001", "item_7", item_7_text)
    sha_1a = _write_section(conn, tmp_path, "0001", "item_1a", item_1a_text)
    quote = "grew 12% in the segment"
    cite = _make_citation(conn, "0001", "item_7", "stale_sha", 0, len(quote), quote)

    result = _resolve_citation(cite, conn)
    assert result["result"] == "moved_section"
    assert result["doc_sha256"] == sha_1a
    assert item_1a_text[result["start"]:result["end"]] == quote


def test_rung4_renormalized(tmp_path):
    """The stored quote has single spaces; the current text has the same
    words but with a line break / double space where the quote has a single
    space — an exact .find() misses it, the whitespace-flexible search doesn't."""
    conn = _conn()
    text = "Revenue grew 12%\n   in the segment, driven by demand."
    quote = "grew 12% in the segment"
    sha = _write_section(conn, tmp_path, "0001", "item_7", text)
    cite = _make_citation(conn, "0001", "item_7", "stale_sha", 0, len(quote), quote)

    result = _resolve_citation(cite, conn)
    assert result["result"] == "renormalized"
    assert result["doc_sha256"] == sha


def test_rung5_fuzzy(tmp_path):
    """The quote was lightly edited in place (a number corrected) — not an
    exact or whitespace-only difference, but most of its bytes are still
    there contiguously, above the similarity floor."""
    conn = _conn()
    text = "Revenue grew 14% in the segment, driven by demand."  # was "12%"
    quote = "grew 12% in the segment"
    sha = _write_section(conn, tmp_path, "0001", "item_7", text)
    cite = _make_citation(conn, "0001", "item_7", "stale_sha", 0, len(quote), quote)

    result = _resolve_citation(cite, conn)
    assert result["result"] == "fuzzy"
    assert result["doc_sha256"] == sha
    assert 0.6 <= result["score"] < 1.0


def test_rung6_unresolved(tmp_path):
    conn = _conn()
    text = "This filing no longer discusses that topic at all."
    _write_section(conn, tmp_path, "0001", "item_7", text)
    quote = "grew 12% in the segment, driven by strong overseas demand growth"
    cite = _make_citation(conn, "0001", "item_7", "stale_sha", 0, len(quote), quote)

    result = _resolve_citation(cite, conn)
    assert result["result"] == "unresolved"
    assert result["score"] is None
    assert result["doc_sha256"] is None


# ---------------------------------------------------------------------------
# Repeated-quote disambiguation (confirmed MEDIUM finding, judge report
# 20260909-134928: rungs 2/3/4 used to accept the *first* occurrence of an
# exact-content match with no check it was the right one — a real risk
# since the JPM pilot corpus already has citations whose quote appears
# twice in the same document.)
# ---------------------------------------------------------------------------

def test_rung2_moved_disambiguates_repeated_quote_via_context(tmp_path):
    """The quote 'competition is intense' appears twice; only the second
    occurrence's surrounding text matches this citation's stored
    prefix/suffix. The resolver must land on that one, not the first."""
    conn = _conn()
    quote = "competition is intense"
    text = (
        f"In the widget segment, {quote} and margins are thin.\n\n"
        f"In the gadget segment, {quote} but growth remains strong."
    )
    sha = _write_section(conn, tmp_path, "0001", "item_7", text)

    second_start = text.rindex(quote)
    prefix = text[second_start - 20:second_start]
    suffix = text[second_start + len(quote): second_start + len(quote) + 20]

    cite = _make_citation(conn, "0001", "item_7", "stale_sha", 0, len(quote), quote)
    # _make_citation doesn't take prefix/suffix; set them directly.
    conn.execute(
        "UPDATE citations SET prefix = ?, suffix = ? WHERE citation_id = ?",
        (prefix, suffix, cite["citation_id"]),
    )
    cite = conn.execute("SELECT * FROM citations WHERE citation_id = ?", (cite["citation_id"],)).fetchone()

    result = _resolve_citation(cite, conn)
    assert result["result"] == "moved"
    assert result["start"] == second_start


def test_rung2_moved_falls_through_to_fuzzy_when_context_cannot_disambiguate(tmp_path):
    """Same repeated quote, but the citation's stored prefix/suffix don't
    match *either* occurrence exactly — rungs 2-4 (the exact-content rungs)
    must not guess which one is right, so this falls all the way through
    to fuzzy (which doesn't disambiguate — see _find_fuzzy's docstring).
    Landing on 'fuzzy' rather than 'moved' is the point: it correctly
    reports lower confidence in *which* occurrence, not just that the text
    exists somewhere, and 'fuzzy' is in _DEGRADED_RESULTS so the owning
    analysis gets flagged stale."""
    conn = _conn()
    quote = "competition is intense"
    text = f"First: {quote}. Second: {quote}."
    _write_section(conn, tmp_path, "0001", "item_7", text)

    cite = _make_citation(conn, "0001", "item_7", "stale_sha", 0, len(quote), quote)
    conn.execute(
        "UPDATE citations SET prefix = ?, suffix = ? WHERE citation_id = ?",
        ("nothing that appears in the text", "nor this", cite["citation_id"]),
    )
    cite = conn.execute("SELECT * FROM citations WHERE citation_id = ?", (cite["citation_id"],)).fetchone()

    result = _resolve_citation(cite, conn)
    assert result["result"] == "fuzzy"
    assert result["result"] in _DEGRADED_RESULTS


def test_rung2_moved_is_unresolved_when_quote_is_truly_gone(tmp_path):
    """Belt-and-suspenders: when the quote doesn't exist anywhere (not even
    for fuzzy to partially match), the ladder still ends at unresolved."""
    conn = _conn()
    _write_section(conn, tmp_path, "0001", "item_7", "Nothing related remains in this section.")
    quote = "a very specific claim that is now completely gone from the filing text"
    cite = _make_citation(conn, "0001", "item_7", "stale_sha", 0, len(quote), quote)

    result = _resolve_citation(cite, conn)
    assert result["result"] == "unresolved"


def test_missing_filing_documents_row_is_unresolved_not_a_crash(tmp_path):
    conn = _conn()
    quote = "anything"
    cite = _make_citation(conn, "0001", "item_7", "sha", 0, len(quote), quote)
    result = _resolve_citation(cite, conn)
    assert result["result"] == "unresolved"


# ---------------------------------------------------------------------------
# _reanchor — events, stale_analysis, and the "anchor is never touched" rule
# ---------------------------------------------------------------------------

def test_reanchor_writes_one_event_per_citation_and_never_mutates_citations(tmp_path):
    conn = _conn()
    text = "Revenue grew 12% in the segment, driven by demand."
    quote = "grew 12% in the segment"
    sha = _write_section(conn, tmp_path, "0001", "item_7", text)
    start = text.index(quote)

    conn.execute("INSERT INTO ai_analysis (run_id, ticker, analysis_type) VALUES ('run1','TST','moat')")
    conn.execute("INSERT INTO analysis_claims (claim_id, run_id, ticker, analysis_type, claim_order) "
                 "VALUES (1,'run1','TST','moat',1)")
    original = _make_citation(conn, "0001", "item_7", sha, start, start + len(quote), quote, claim_id=1)
    conn.commit()

    _reanchor("TST", conn)

    events = conn.execute("SELECT * FROM citation_resolution_events").fetchall()
    assert len(events) == 1
    assert events[0]["result"] == "exact"

    unchanged = conn.execute("SELECT * FROM citations WHERE citation_id = ?",
                             (original["citation_id"],)).fetchone()
    assert unchanged["start_char"] == original["start_char"]
    assert unchanged["end_char"] == original["end_char"]
    assert unchanged["quote"] == original["quote"]


def test_reanchor_sets_stale_analysis_on_unresolved_citation(tmp_path):
    conn = _conn()
    text = "Nothing related remains in this section."
    _write_section(conn, tmp_path, "0001", "item_7", text)

    conn.execute("INSERT INTO ai_analysis (run_id, ticker, analysis_type) VALUES ('run1','TST','moat')")
    conn.execute("INSERT INTO analysis_claims (claim_id, run_id, ticker, analysis_type, claim_order) "
                 "VALUES (1,'run1','TST','moat',1)")
    quote = "a very specific claim that is now completely gone from the filing text"
    _make_citation(conn, "0001", "item_7", "stale_sha", 0, len(quote), quote, claim_id=1)
    conn.commit()

    _reanchor("TST", conn)

    row = conn.execute(
        "SELECT stale_analysis FROM ai_analysis WHERE run_id = 'run1' AND ticker = 'TST' AND analysis_type = 'moat'"
    ).fetchone()
    assert row["stale_analysis"] == 1


def test_reanchor_does_not_flag_analysis_whose_citations_all_resolve_exact(tmp_path):
    conn = _conn()
    text = "Revenue grew 12% in the segment, driven by demand."
    quote = "grew 12% in the segment"
    sha = _write_section(conn, tmp_path, "0001", "item_7", text)
    start = text.index(quote)

    conn.execute("INSERT INTO ai_analysis (run_id, ticker, analysis_type) VALUES ('run1','TST','risk')")
    conn.execute("INSERT INTO analysis_claims (claim_id, run_id, ticker, analysis_type, claim_order) "
                 "VALUES (1,'run1','TST','risk',1)")
    _make_citation(conn, "0001", "item_7", sha, start, start + len(quote), quote, claim_id=1)
    conn.commit()

    _reanchor("TST", conn)

    row = conn.execute(
        "SELECT stale_analysis FROM ai_analysis WHERE run_id = 'run1' AND ticker = 'TST' AND analysis_type = 'risk'"
    ).fetchone()
    assert row["stale_analysis"] == 0


def test_reanchor_only_flags_the_degraded_analysis_type_not_others(tmp_path):
    """Two analysis_types for the same run/ticker: only the one whose
    citation resolves fuzzy/unresolved gets stale_analysis=1."""
    conn = _conn()
    text = "Revenue grew 12% in the segment, driven by demand."
    quote = "grew 12% in the segment"
    sha = _write_section(conn, tmp_path, "0001", "item_7", text)
    start = text.index(quote)

    conn.execute("INSERT INTO ai_analysis (run_id, ticker, analysis_type) VALUES ('run1','TST','moat')")
    conn.execute("INSERT INTO ai_analysis (run_id, ticker, analysis_type) VALUES ('run1','TST','risk')")
    conn.execute("INSERT INTO analysis_claims (claim_id, run_id, ticker, analysis_type, claim_order) "
                 "VALUES (1,'run1','TST','moat',1)")
    conn.execute("INSERT INTO analysis_claims (claim_id, run_id, ticker, analysis_type, claim_order) "
                 "VALUES (2,'run1','TST','risk',1)")
    _make_citation(conn, "0001", "item_7", sha, start, start + len(quote), quote, claim_id=1)  # exact
    gone_quote = "a claim whose text is nowhere in this filing anymore at all"
    _make_citation(conn, "0001", "item_7", "stale_sha", 0, len(gone_quote), gone_quote, claim_id=2)  # unresolved
    conn.commit()

    _reanchor("TST", conn)

    moat_row = conn.execute(
        "SELECT stale_analysis FROM ai_analysis WHERE run_id='run1' AND ticker='TST' AND analysis_type='moat'"
    ).fetchone()
    risk_row = conn.execute(
        "SELECT stale_analysis FROM ai_analysis WHERE run_id='run1' AND ticker='TST' AND analysis_type='risk'"
    ).fetchone()
    assert moat_row["stale_analysis"] == 0
    assert risk_row["stale_analysis"] == 1


def test_reanchor_ignores_superseded_analyses(tmp_path):
    """aa.is_current = 0 rows are excluded from the ladder run entirely."""
    conn = _conn()
    conn.execute(
        "INSERT INTO ai_analysis (run_id, ticker, analysis_type, is_current) "
        "VALUES ('old_run','TST','moat',0)"
    )
    conn.execute("INSERT INTO analysis_claims (claim_id, run_id, ticker, analysis_type, claim_order) "
                 "VALUES (1,'old_run','TST','moat',1)")
    _make_citation(conn, "0001", "item_7", "sha", 0, 5, "hello", claim_id=1)
    conn.commit()

    _reanchor("TST", conn)

    assert conn.execute("SELECT COUNT(*) FROM citation_resolution_events").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# _reanchor — cache-forward analyses (confirmed HIGH finding, judge report
# 20260909-131716: a cache-forward ai_analysis row stores no claims of its
# own — moat/analysis/persist.py's copy-forward writes ai_analysis only —
# so joining analysis_claims straight on aa.run_id found zero claims for any
# cached analysis and silently reanchored nothing for it.)
# ---------------------------------------------------------------------------

def test_reanchor_resolves_cached_analysis_via_source_run(tmp_path):
    """A cache-forward row (run2, reused_from_run_id='run1') has no claims of
    its own; its citations must still be reanchored via run1's claims, and
    any resulting stale_analysis flag must land on run2 (the current row)."""
    conn = _conn()
    text = "Nothing related remains in this section."
    _write_section(conn, tmp_path, "0001", "item_7", text)

    # The source analysis (run1) actually owns the claim + citation.
    conn.execute("INSERT INTO ai_analysis (run_id, ticker, analysis_type, is_current) "
                 "VALUES ('run1','TST','moat',0)")  # superseded by the copy-forward
    conn.execute("INSERT INTO analysis_claims (claim_id, run_id, ticker, analysis_type, claim_order) "
                 "VALUES (1,'run1','TST','moat',1)")
    gone_quote = "a claim whose text is nowhere in this filing anymore at all"
    _make_citation(conn, "0001", "item_7", "stale_sha", 0, len(gone_quote), gone_quote, claim_id=1)

    # The current row is a pure copy-forward: no analysis_claims row of its own.
    conn.execute("INSERT INTO ai_analysis (run_id, ticker, analysis_type, is_current, reused_from_run_id) "
                 "VALUES ('run2','TST','moat',1,'run1')")
    conn.commit()

    _reanchor("TST", conn)

    events = conn.execute("SELECT * FROM citation_resolution_events").fetchall()
    assert len(events) == 1
    assert events[0]["result"] == "unresolved"

    run2_row = conn.execute(
        "SELECT stale_analysis FROM ai_analysis WHERE run_id='run2' AND ticker='TST' AND analysis_type='moat'"
    ).fetchone()
    run1_row = conn.execute(
        "SELECT stale_analysis FROM ai_analysis WHERE run_id='run1' AND ticker='TST' AND analysis_type='moat'"
    ).fetchone()
    assert run2_row["stale_analysis"] == 1  # the current, displayed row
    assert run1_row["stale_analysis"] == 0  # the superseded source row is untouched
