"""Streamlit dashboard (PRD §9, §10). Sprint 2 adds the ranked table view;
Sprint 5 adds the Investment Brief detail page.

Run with: streamlit run moat/dashboard/app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st

from moat.db.connection import get_connection

st.set_page_config(page_title="Project Moat", layout="wide")
st.title("Project Moat — Ranked Candidates")
st.caption("Personal research tool. Not investment advice.")

conn = get_connection()
cursor = conn.execute("SELECT COUNT(*) AS n FROM companies")
company_count = cursor.fetchone()["n"]

if company_count == 0:
    st.info(
        "No companies loaded yet. Run `python scripts/run_pipeline.py --init-db "
        "--from-stage universe` once Sprint 1 ingestion is implemented."
    )
else:
    st.write(f"{company_count} companies in universe.")
    # TODO (Sprint 2): ranked table — Company, Quality, Moat, Valuation,
    # Overall Score, Status, sourced from the latest committee_verdicts run.
    # TODO (Sprint 5): click-through to a one-page Investment Brief (PRD §10).

conn.close()
