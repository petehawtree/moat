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
    "filings": {
        # W1 (Sprint 3): document_url is the EDGAR index page (already present);
        # primary_document_url is the direct URL of the fetched HTML file.
        # local_path and content_hash are in the schema already (nullable).
        "primary_document_url": "TEXT",
    },
    "ai_analysis": {
        # Sprint 3: new lifecycle and coverage columns.
        # citations TEXT is retired via a guarded DROP below — see _retire_legacy.
        "is_current":           "INTEGER NOT NULL DEFAULT 1",
        "superseded_by_run_id": "TEXT",
        "reused_from_run_id":   "TEXT",
        "claim_coverage":       "REAL",
    },
}


def _retire_legacy(conn) -> list[str]:
    """One-off guarded column removals. Additive-only rule does not apply here.

    ai_analysis.citations (TEXT NOT NULL) is replaced by analysis_claims +
    citations tables. Dropped only when ai_analysis holds zero rows — if the
    table has data the column is load-bearing and the caller must intervene.
    Raises RuntimeError rather than silently leaving stale structure in place.
    """
    retired = []
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(ai_analysis)")}
    if "citations" not in existing:
        return retired  # already done

    count = conn.execute("SELECT COUNT(*) FROM ai_analysis").fetchone()[0]
    if count > 0:
        raise RuntimeError(
            f"ai_analysis.citations cannot be retired: table has {count} rows. "
            "Migrate or clear the table before upgrading."
        )

    # SQLite doesn't support DROP COLUMN before 3.35.0; use recreate idiom.
    # Must use explicit CREATE TABLE (not CREATE TABLE AS SELECT) to preserve
    # NOT NULL constraints and allow a UNIQUE key that analysis_claims FKs to.
    other_cols = [
        row for row in conn.execute("PRAGMA table_info(ai_analysis)")
        if row["name"] != "citations"
    ]
    col_defs = []
    for c in other_cols:
        decl = f'"{c["name"]}" {c["type"]}'
        if c["notnull"]:
            decl += " NOT NULL"
        if c["dflt_value"] is not None:
            decl += f" DEFAULT {c['dflt_value']}"
        col_defs.append(decl)
    col_defs_sql = ",\n    ".join(col_defs)
    cols_sql = ", ".join(f'"{c["name"]}"' for c in other_cols)
    conn.executescript(f"""
        BEGIN;
        CREATE TABLE ai_analysis_new (
            {col_defs_sql},
            UNIQUE (run_id, ticker, analysis_type)
        );
        INSERT INTO ai_analysis_new ({cols_sql}) SELECT {cols_sql} FROM ai_analysis;
        DROP TABLE ai_analysis;
        ALTER TABLE ai_analysis_new RENAME TO ai_analysis;
        COMMIT;
    """)
    retired.append("ai_analysis.citations")
    return retired


def _migrate(conn) -> list[str]:
    """Add any columns missing from an existing database. Returns what it added.

    Deliberately additive only: no drops, no type changes, no backfill. A
    column added here is NULL on existing rows, which is the honest state —
    those rows were ingested before we retained the information, and we
    can't invent it retroactively (docs/PRD_ADDENDUM.md §A4).

    The one documented exception — ai_analysis.citations — is handled
    separately by _retire_legacy().
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
    applied += _retire_legacy(conn)

    # Ensure ai_analysis has the UNIQUE constraint that analysis_claims FKs to.
    # _retire_legacy() may have already run (citations already dropped) so this
    # is a standalone step rather than part of that migration.
    existing_indexes = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='ai_analysis'"
        )
    }
    if "idx_ai_analysis_pk" not in existing_indexes:
        conn.execute(
            "CREATE UNIQUE INDEX idx_ai_analysis_pk "
            "ON ai_analysis(run_id, ticker, analysis_type)"
        )
        applied.append("ai_analysis: UNIQUE INDEX on (run_id, ticker, analysis_type)")

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
