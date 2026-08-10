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


def init_db(db_path: Path = DB_PATH, schema_path: Path = SCHEMA_PATH) -> None:
    """Create all tables from schema.sql if they don't already exist."""
    conn = get_connection(db_path)
    try:
        conn.executescript(schema_path.read_text())
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Initialized database at {DB_PATH}")
