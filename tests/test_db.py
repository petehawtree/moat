"""Sprint 0 smoke test: schema creates cleanly and core tables exist."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moat.db.connection import get_connection, init_db

EXPECTED_TABLES = {
    "companies",
    "price_history",
    "fundamentals_annual",
    "fundamentals_quarterly",
    "filings",
    "pipeline_runs",
    "quant_scores",
    "quality_scores",
    "ai_analysis",
    "valuations",
    "committee_verdicts",
    "watchlist_events",
}


def test_schema_creates_all_expected_tables(tmp_path):
    db_path = tmp_path / "test_moat.db"
    init_db(db_path=db_path)

    conn = get_connection(db_path=db_path)
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    table_names = {row["name"] for row in rows}
    conn.close()

    missing = EXPECTED_TABLES - table_names
    assert not missing, f"Schema is missing expected tables: {missing}"
