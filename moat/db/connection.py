"""SQLite connection helper for Project Moat.

Single-file local database — see docs/PRD_ADDENDUM.md for why (no
infra needed for a personal research tool run on one machine).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "moat.db"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Return a connection with foreign keys enabled and Row access."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


# Columns added to existing tables after their first release. SQLite's
# CREATE TABLE IF NOT EXISTS won't add these to a database created by an
# earlier schema version, so they're applied separately — see _migrate.
_ADDED_COLUMNS = {
    "quant_scores": {
        "status": "TEXT",             # A13 pass/fail/unavailable
    },
    "quality_scores": {
        "metrics_assessed": "INTEGER",  # A13 coverage
        "metrics_passed": "INTEGER",
    },
    "share_basis_changes": {
        "change_type": "TEXT",        # A10 split vs unit-correction
    },
    "fundamentals_annual": {
        "accession_number": "TEXT",   # A11 provenance
        "filed": "TEXT",              # A11 provenance
        "quality_flags": "TEXT",      # A10 ingest validation
        "operating_cash_flow": "REAL",  # A13: FCF is no longer substituted with OCF
    },
}


def _migrate(conn) -> list[str]:
    """Add any columns missing from an existing database. Returns what it added.

    Deliberately additive only: no drops, no type changes, no backfill. A
    column added here is NULL on existing rows, which is the honest state —
    those rows were ingested before we retained the information, and we
    can't invent it retroactively (docs/PRD_ADDENDUM.md §A4).
    """
    applied = []
    for table, columns in _ADDED_COLUMNS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue  # table doesn't exist yet; schema.sql will create it with the columns
        for name, decl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                applied.append(f"{table}.{name}")
    conn.commit()
    return applied


def init_db(db_path: Path = DB_PATH, schema_path: Path = SCHEMA_PATH) -> list[str]:
    """Create all tables from schema.sql if missing, then apply column migrations.

    Returns the list of migrated columns (empty when already up to date).
    """
    conn = get_connection(db_path)
    try:
        conn.executescript(schema_path.read_text())
        conn.commit()
        return _migrate(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Initialized database at {DB_PATH}")
