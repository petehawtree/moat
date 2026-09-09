-- Project Moat — MVP schema
-- SQLite. See docs/PRD_ADDENDUM.md for the reasoning behind fields like
-- confidence, sector peer group, and citation requirements.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
-- Universe
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS companies (
    ticker          TEXT PRIMARY KEY,
    cik             TEXT,                 -- SEC EDGAR identifier (US only for now)
    name            TEXT NOT NULL,
    sector          TEXT,                 -- GICS sector, used for sector-relative screening (A2)
    industry        TEXT,
    exchange        TEXT,
    currency        TEXT NOT NULL DEFAULT 'USD',  -- carried now so FTSE 350 addition needs no migration
    universe        TEXT NOT NULL,        -- 'sp500' | 'nasdaq100' (comma-joined if in both)
    is_active       INTEGER NOT NULL DEFAULT 1,
    added_date      TEXT NOT NULL,
    removed_date    TEXT
);

-- ---------------------------------------------------------------------
-- Prices
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS price_history (
    ticker      TEXT NOT NULL REFERENCES companies(ticker),
    date        TEXT NOT NULL,            -- ISO date
    close       REAL NOT NULL,
    volume      INTEGER,
    source      TEXT NOT NULL DEFAULT 'yfinance',
    retrieved_at TEXT NOT NULL,
    PRIMARY KEY (ticker, date)
);

-- ---------------------------------------------------------------------
-- Fundamentals (annual + quarterly kept separate; same shape)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fundamentals_annual (
    ticker              TEXT NOT NULL REFERENCES companies(ticker),
    fiscal_year         INTEGER NOT NULL,
    period_end_date     TEXT,
    revenue             REAL,
    eps_diluted         REAL,
    net_income          REAL,
    operating_income    REAL,
    operating_margin    REAL,
    gross_margin        REAL,
    roic                REAL,
    roe                 REAL,
    free_cash_flow      REAL,                 -- OCF minus capex; NULL when capex unavailable (A13) — never substituted with OCF
    operating_cash_flow REAL,                 -- kept separately so a missing-capex company still has its cash-flow figure
    capex               REAL,
    total_debt          REAL,
    cash_and_equiv      REAL,
    shares_diluted      REAL,
    source              TEXT NOT NULL,        -- 'sec_edgar' | 'yfinance' | 'derived'
    confidence          TEXT NOT NULL,        -- 'high' | 'medium' | 'low' (A4)
    accession_number    TEXT,                 -- filing this row's figures came from (A11)
    filed               TEXT,                 -- that filing's date, for restatement ordering (A11)
    quality_flags       TEXT,                 -- comma-separated ingest validation failures (A10), null if clean
    retrieved_at        TEXT NOT NULL,
    PRIMARY KEY (ticker, fiscal_year)
);

CREATE TABLE IF NOT EXISTS fundamentals_quarterly (
    ticker              TEXT NOT NULL REFERENCES companies(ticker),
    fiscal_year         INTEGER NOT NULL,
    fiscal_quarter      INTEGER NOT NULL,     -- 1-4
    period_end_date     TEXT,
    revenue             REAL,
    eps_diluted         REAL,
    net_income          REAL,
    operating_income    REAL,
    free_cash_flow      REAL,
    source              TEXT NOT NULL,
    confidence          TEXT NOT NULL,
    retrieved_at        TEXT NOT NULL,
    PRIMARY KEY (ticker, fiscal_year, fiscal_quarter)
);

-- ---------------------------------------------------------------------
-- Share-basis changes (A10/A11)
--
-- A genuine stock split RESTATES prior-period share counts: the same
-- period-end carries a different value in a later 10-K, because the filer
-- rebased its comparatives. A real share issuance (IPO, merger, recap)
-- restates nothing — the count genuinely grew and every filing agrees.
-- That distinction is only visible while we still hold every filing's
-- version of a fact, which is why this is captured at ingest and not
-- inferred later from a jump in the merged series (the Sprint 2 mistake).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS share_basis_changes (
    ticker              TEXT NOT NULL REFERENCES companies(ticker),
    period_end_date     TEXT NOT NULL,       -- the period whose value was restated
    original_value      REAL NOT NULL,       -- as originally filed
    restated_value      REAL NOT NULL,       -- as restated by a later filing
    ratio               REAL NOT NULL,       -- restated / original (≈ the split ratio)
    change_type         TEXT NOT NULL,       -- 'split' | 'unit_correction' (a 1000x/1e6x "restatement" is the filer fixing a unit, not a split)
    original_accession  TEXT,
    original_filed      TEXT,
    restated_accession  TEXT,
    restated_filed      TEXT,
    detected_at         TEXT NOT NULL,
    PRIMARY KEY (ticker, period_end_date)
);

-- ---------------------------------------------------------------------
-- Filings (grounding source for AI analysis — A3)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS filings (
    accession_number    TEXT PRIMARY KEY,
    ticker              TEXT NOT NULL REFERENCES companies(ticker),
    form_type           TEXT NOT NULL,        -- '10-K' | '10-Q' | ...
    filing_date         TEXT NOT NULL,
    period_of_report    TEXT,
    document_url        TEXT NOT NULL,
    local_path          TEXT,                 -- cached copy in data/
    content_hash        TEXT,                 -- used as the AI cache key (A5)
    retrieved_at        TEXT NOT NULL
);

-- ---------------------------------------------------------------------
-- Pipeline runs (every stage writes against a run_id so results are
-- reproducible and comparable run-over-run for monitoring/A5 caching)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id          TEXT PRIMARY KEY,        -- e.g. ISO timestamp
    started_at      TEXT NOT NULL,
    completed_at    TEXT,
    stage_reached   TEXT,                    -- last stage completed
    status          TEXT NOT NULL DEFAULT 'running',  -- 'running'|'complete'|'partial'|'failed' ('partial' = ran cleanly up to an unbuilt stage, A13)
    notes           TEXT
);

-- ---------------------------------------------------------------------
-- Quantitative screen (PRD §4 + sector-relative logic, A2)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS quant_scores (
    run_id              TEXT NOT NULL REFERENCES pipeline_runs(run_id),
    ticker              TEXT NOT NULL REFERENCES companies(ticker),
    sector_peer_group   TEXT,                -- sector used for relative comparison
    metric              TEXT NOT NULL,       -- 'roic' | 'roe' | 'free_cash_flow' | 'revenue_eps_growth' | 'operating_margin' | 'debt' | 'share_dilution' | 'gross_margin' — see moat/screen/quant_screen.py METRICS
    value               REAL,
    absolute_floor_pass INTEGER,             -- 0/1
    sector_percentile   REAL,                -- 0-100, null if not applicable
    sector_relative_pass INTEGER,            -- 0/1
    overall_pass        INTEGER NOT NULL,    -- combines the two per A2; 0 for 'unavailable' — read `status` to tell them apart
    status              TEXT,                -- 'pass' | 'fail' | 'unavailable' (A13): "we couldn't measure this" is not "this company did badly"
    PRIMARY KEY (run_id, ticker, metric)
);

CREATE TABLE IF NOT EXISTS quality_scores (
    run_id          TEXT NOT NULL REFERENCES pipeline_runs(run_id),
    ticker          TEXT NOT NULL REFERENCES companies(ticker),
    passed_screen   INTEGER NOT NULL,        -- did it clear enough of quant_scores to proceed
    composite_score REAL,                    -- % of ASSESSABLE metrics passed (A13), not % of all 8
    metrics_assessed INTEGER,                -- how many of the 8 could actually be measured
    metrics_passed  INTEGER,                 -- how many of those passed
    notes           TEXT,
    PRIMARY KEY (run_id, ticker)
);

-- ---------------------------------------------------------------------
-- Filing documents: normalized and sectioned text (W2 output, Sprint 3)
-- One row per (accession, section, norm_version).
-- section_confidence is 'high'|'low' only — failed/IBR sections do not get rows.
-- extraction_trace is a JSON receipt of every candidate and criterion (A11).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS filing_documents (
    filing_document_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    accession_number    TEXT NOT NULL REFERENCES filings(accession_number),
    section_id          TEXT NOT NULL CHECK (section_id IN ('item_1','item_1a','item_7','full')),
    norm_version        TEXT NOT NULL,
    doc_sha256          TEXT NOT NULL,     -- SHA-256 of the normalized text
    char_length         INTEGER NOT NULL,
    extraction_method   TEXT NOT NULL,     -- 'sections'|'full_fallback'|'sections_partial'
    section_confidence  TEXT NOT NULL CHECK (section_confidence IN ('high','low')),
    local_path          TEXT NOT NULL,     -- immutable file path; never overwritten in place
    extraction_trace    TEXT,              -- JSON; NULL only for section_id = 'full'
    created_at          TEXT NOT NULL,
    UNIQUE (accession_number, section_id, norm_version)
);

-- ---------------------------------------------------------------------
-- AI qualitative analysis (PRD §5, Sprint 3)
-- citations TEXT column is retired in favour of analysis_claims + citations tables.
-- Retirement is guarded: _migrate() drops it only when the table holds zero rows.
-- New columns (is_current etc.) are additive, applied via _ADDED_COLUMNS.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai_analysis (
    run_id                  TEXT NOT NULL REFERENCES pipeline_runs(run_id),
    ticker                  TEXT NOT NULL REFERENCES companies(ticker),
    analysis_type           TEXT NOT NULL,  -- 'business_quality'|'moat'|'management'|'risk'
    content                 TEXT NOT NULL,
    citations               TEXT NOT NULL,  -- RETIRED: kept only for legacy DBs; _migrate drops when empty
    model                   TEXT NOT NULL,
    prompt_version          TEXT NOT NULL,
    cache_key               TEXT NOT NULL,  -- bundle prompt_sha256 (A5, §A15.7)
    is_current              INTEGER NOT NULL DEFAULT 1,
    superseded_by_run_id    TEXT,
    reused_from_run_id      TEXT,           -- non-NULL means no API call was made
    claim_coverage          REAL,           -- asserted claims cited ÷ asserted claims; must be 1.0
    stale_analysis          INTEGER NOT NULL DEFAULT 0,  -- Sprint 3.1: set by cite.py --reanchor
                                             -- when any of its citations resolves at 'fuzzy' or
                                             -- 'unresolved' (§A15.5) — the anchor is trusted less,
                                             -- not deleted or re-pointed.
    created_at              TEXT NOT NULL,
    PRIMARY KEY (run_id, ticker, analysis_type)
);

-- Claims are parsed by us from the model's response, not inferred from API
-- text-block boundaries. claim_order is 1-based within each analysis_type.
CREATE TABLE IF NOT EXISTS analysis_claims (
    claim_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT NOT NULL,
    ticker           TEXT NOT NULL,
    analysis_type    TEXT NOT NULL,
    claim_order      INTEGER NOT NULL,
    claim_text       TEXT NOT NULL,
    assertion_status TEXT NOT NULL CHECK (assertion_status IN ('asserted','insufficient_evidence')),
    FOREIGN KEY (run_id, ticker, analysis_type)
        REFERENCES ai_analysis(run_id, ticker, analysis_type)
);
CREATE INDEX IF NOT EXISTS idx_claims_analysis
    ON analysis_claims(run_id, ticker, analysis_type);

-- Immutable citation anchors. Nothing is ever UPDATEd — use citation_resolution_events.
-- Composite FK to filing_documents enforces (accession, section, norm_version) triplet.
CREATE TABLE IF NOT EXISTS citations (
    citation_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id         INTEGER NOT NULL REFERENCES analysis_claims(claim_id),
    accession_number TEXT NOT NULL,
    section_id       TEXT NOT NULL,
    doc_sha256       TEXT NOT NULL,
    norm_version     TEXT NOT NULL,
    start_char       INTEGER NOT NULL,
    end_char         INTEGER NOT NULL,
    quote            TEXT NOT NULL,
    quote_sha256     TEXT NOT NULL,
    prefix           TEXT,
    suffix           TEXT,
    created_at       TEXT NOT NULL,
    CHECK (start_char >= 0 AND end_char > start_char),
    FOREIGN KEY (accession_number, section_id, norm_version)
        REFERENCES filing_documents(accession_number, section_id, norm_version)
);
CREATE INDEX IF NOT EXISTS idx_citations_filing ON citations(accession_number);

-- Re-anchoring audit trail. Current status = latest event for a citation_id.
CREATE TABLE IF NOT EXISTS citation_resolution_events (
    event_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    citation_id         INTEGER NOT NULL REFERENCES citations(citation_id),
    checked_at          TEXT NOT NULL,
    result              TEXT NOT NULL CHECK (result IN
                          ('exact','moved','moved_section','renormalized','fuzzy','unresolved')),
    score               REAL,
    resolved_doc_sha256 TEXT,
    resolved_start      INTEGER,
    resolved_end        INTEGER
);

-- Every API request frame, kept whether the attempt succeeded or failed (§A15.11).
-- custom_id is the idempotency key used for batch retrieval; UNIQUE allows NULL.
-- 'pending' (Sprint 3.1): a batch request frame written at submission time,
-- before any result exists — see moat/analysis/persist.py's
-- submit_and_persist_batch()/run_batch_retrieval(). UPSERTed to a terminal
-- outcome in place (ON CONFLICT(custom_id)) once the batch item resolves, so
-- retrieval is resumable: a second call only sees the rows still 'pending'.
CREATE TABLE IF NOT EXISTS analysis_attempts (
    attempt_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT NOT NULL REFERENCES pipeline_runs(run_id),
    ticker           TEXT NOT NULL REFERENCES companies(ticker),
    batch_id         TEXT,
    custom_id        TEXT UNIQUE,
    accession_number TEXT,             -- filing pinned at request time (Sprint 3.1)
    model_id         TEXT NOT NULL,
    prompt_sha256    TEXT NOT NULL,
    protocol_version TEXT NOT NULL,
    document_map     TEXT NOT NULL,   -- JSON: {document_index: filing_document_id}
    usage_json       TEXT,
    cost_estimate    REAL,
    outcome          TEXT NOT NULL CHECK (outcome IN
                       ('pending','persisted','validation_failed','api_error','refused')),
    failure_reason   TEXT,
    raw_response     TEXT,
    created_at       TEXT NOT NULL
);

-- ---------------------------------------------------------------------
-- Valuation (PRD §6)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS valuations (
    run_id                  TEXT NOT NULL REFERENCES pipeline_runs(run_id),
    ticker                  TEXT NOT NULL REFERENCES companies(ticker),
    method                  TEXT NOT NULL,   -- 'owner_earnings_dcf'|'fcf_yield'|'ev_ebit'|'pe_historical'
    scenario                TEXT,            -- 'bear'|'base'|'bull', null for non-scenario methods
    intrinsic_value_low     REAL,
    intrinsic_value_high    REAL,
    current_price           REAL,
    margin_of_safety_pct    REAL,
    key_assumptions         TEXT,            -- JSON
    created_at              TEXT NOT NULL,
    PRIMARY KEY (run_id, ticker, method, scenario)
);

-- ---------------------------------------------------------------------
-- Investment Committee (PRD §7, §8) and final brief inputs
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS committee_verdicts (
    run_id                      TEXT NOT NULL REFERENCES pipeline_runs(run_id),
    ticker                      TEXT NOT NULL REFERENCES companies(ticker),
    quality_analyst_view        TEXT,
    bear_analyst_view           TEXT,
    valuation_analyst_view      TEXT,
    business_quality_score      REAL,   -- weight 25%
    competitive_moat_score      REAL,   -- weight 20%
    financial_strength_score    REAL,   -- weight 15%
    management_score            REAL,   -- weight 10%
    valuation_score             REAL,   -- weight 25%
    risk_score                  REAL,   -- weight 5%
    overall_score               REAL,
    status                      TEXT,   -- 'Investigate'|'Watch'|'Reject'
    data_confidence             TEXT,   -- rolled up from A4, surfaced on the brief
    created_at                  TEXT NOT NULL,
    PRIMARY KEY (run_id, ticker)
);

-- ---------------------------------------------------------------------
-- Monitoring / watchlist (PRD §11)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS watchlist_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL REFERENCES pipeline_runs(run_id),
    ticker          TEXT NOT NULL REFERENCES companies(ticker),
    event_type      TEXT NOT NULL,  -- 'entered_top_rank'|'price_crossed_threshold'|'earnings_change'|'management_change'|'financial_deterioration'
    detail          TEXT,
    created_at      TEXT NOT NULL,
    acknowledged    INTEGER NOT NULL DEFAULT 0
);
