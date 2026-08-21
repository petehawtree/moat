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
    free_cash_flow      REAL,
    capex               REAL,
    total_debt          REAL,
    cash_and_equiv      REAL,
    shares_diluted      REAL,
    source              TEXT NOT NULL,        -- 'sec_edgar' | 'yfinance' | 'derived'
    confidence          TEXT NOT NULL,        -- 'high' | 'medium' | 'low' (A4)
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
    status          TEXT NOT NULL DEFAULT 'running',  -- 'running'|'complete'|'failed'
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
    overall_pass        INTEGER NOT NULL,    -- combines the two per A2
    PRIMARY KEY (run_id, ticker, metric)
);

CREATE TABLE IF NOT EXISTS quality_scores (
    run_id          TEXT NOT NULL REFERENCES pipeline_runs(run_id),
    ticker          TEXT NOT NULL REFERENCES companies(ticker),
    passed_screen   INTEGER NOT NULL,        -- did it clear enough of quant_scores to proceed
    composite_score REAL,                    -- deterministic pre-AI quality score
    notes           TEXT,
    PRIMARY KEY (run_id, ticker)
);

-- ---------------------------------------------------------------------
-- AI qualitative analysis (PRD §5) — citations are mandatory, not optional (A3)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai_analysis (
    run_id              TEXT NOT NULL REFERENCES pipeline_runs(run_id),
    ticker              TEXT NOT NULL REFERENCES companies(ticker),
    analysis_type       TEXT NOT NULL,   -- 'business_quality'|'moat'|'management'|'risk'
    content             TEXT NOT NULL,
    citations           TEXT NOT NULL,   -- JSON array of {accession_number, quote}; non-empty required
    model               TEXT NOT NULL,
    prompt_version      TEXT NOT NULL,
    cache_key           TEXT NOT NULL,   -- filing content_hash this was generated from (A5)
    created_at          TEXT NOT NULL,
    PRIMARY KEY (run_id, ticker, analysis_type)
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
