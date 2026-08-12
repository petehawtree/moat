"""Streamlit dashboard (PRD §9, §10). Sprint 1 shows raw ingest coverage;
Sprint 2 adds the ranked/scored table; Sprint 5 adds the Investment Brief
detail page.

Run with: streamlit run moat/dashboard/app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import streamlit as st

from moat.db.connection import get_connection

st.set_page_config(page_title="Project Moat", layout="wide")
st.title("Project Moat")
st.caption("Personal research tool. Not investment advice.")

conn = get_connection()
company_count = conn.execute("SELECT COUNT(*) AS n FROM companies WHERE is_active = 1").fetchone()["n"]

if company_count == 0:
    st.info(
        "No companies loaded yet. Run `python scripts/run_pipeline.py --init-db` "
        "to load the universe and ingest fundamentals/prices."
    )
else:
    st.subheader("Sprint 1 — ingest coverage")
    col1, col2, col3 = st.columns(3)
    col1.metric("Companies in universe", company_count)

    fundamentals_count = conn.execute(
        "SELECT COUNT(DISTINCT ticker) AS n FROM fundamentals_annual"
    ).fetchone()["n"]
    col2.metric("With fundamentals data", fundamentals_count)

    prices_count = conn.execute("SELECT COUNT(DISTINCT ticker) AS n FROM price_history").fetchone()["n"]
    col3.metric("With price history", prices_count)

    st.caption(
        "Ranking/scoring lands in Sprint 2 (sector-relative quant screen, §A2) — "
        "this view is raw data coverage only."
    )

    coverage = pd.read_sql_query(
        """
        SELECT
            c.ticker, c.name, c.sector, c.universe,
            COUNT(f.fiscal_year) AS years_of_fundamentals,
            MAX(f.confidence) AS confidence,
            (SELECT MAX(date) FROM price_history p WHERE p.ticker = c.ticker) AS latest_price_date
        FROM companies c
        LEFT JOIN fundamentals_annual f ON f.ticker = c.ticker
        WHERE c.is_active = 1
        GROUP BY c.ticker
        ORDER BY years_of_fundamentals DESC, c.ticker
        """,
        conn,
    )
    st.dataframe(coverage, use_container_width=True, height=500)

    thin = coverage[coverage["years_of_fundamentals"] < 3]
    if not thin.empty:
        with st.expander(f"{len(thin)} companies with fewer than 3 years of fundamentals"):
            st.dataframe(thin, use_container_width=True)

conn.close()
