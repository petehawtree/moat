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

from moat.config import QUALITY_SCORE_PASS_THRESHOLD
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
    sectors_raw = pd.read_sql_query(
        "SELECT DISTINCT sector FROM companies WHERE is_active = 1 ORDER BY sector", conn
    )["sector"]
    # 15 NASDAQ-100-only companies have no GICS sector at all (docs/PRD_ADDENDUM.md
    # §A9's floor-only fallback) — surfaced as its own filterable option rather
    # than silently dropped from the grids.
    sector_options = sorted(s for s in sectors_raw if s is not None)
    if sectors_raw.isnull().any():
        sector_options.append("(no sector)")

    selected_sectors = st.multiselect("Filter by sector", sector_options, default=sector_options)

    def _filter_by_sector(df: pd.DataFrame) -> pd.DataFrame:
        if not selected_sectors:  # nothing checked reads as "no filter", not "show nothing"
            return df
        return df[df["sector"].fillna("(no sector)").isin(selected_sectors)]

    st.subheader("Sprint 1 — ingest coverage")
    col1, col2, col3 = st.columns(3)
    col1.metric("Companies in universe", company_count)

    fundamentals_count = conn.execute(
        "SELECT COUNT(DISTINCT ticker) AS n FROM fundamentals_annual"
    ).fetchone()["n"]
    col2.metric("With fundamentals data", fundamentals_count)

    prices_count = conn.execute("SELECT COUNT(DISTINCT ticker) AS n FROM price_history").fetchone()["n"]
    col3.metric("With price history", prices_count)

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
    coverage = _filter_by_sector(coverage)
    st.dataframe(coverage, use_container_width=True, height=500)

    thin = coverage[coverage["years_of_fundamentals"] < 3]
    if not thin.empty:
        with st.expander(f"{len(thin)} companies with fewer than 3 years of fundamentals"):
            st.dataframe(thin, use_container_width=True)

    st.divider()
    st.subheader("Sprint 2 — quant screen ranking")
    st.caption(
        "Sector-relative screen (docs/PRD_ADDENDUM.md §A2/§A9): each of the 8 "
        "PRD §4 metrics passes only if it clears both an absolute floor and "
        "the top-tercile bar within its own GICS sector. composite_score is "
        "the % of *assessable* metrics passed — metrics we couldn't measure are "
        "excluded rather than counted as failures (§A13), so `assessed` shows "
        "how much of the company we could actually see."
    )

    latest_run = conn.execute(
        """
        SELECT run_id FROM pipeline_runs
        WHERE run_id IN (SELECT DISTINCT run_id FROM quality_scores)
          AND status != 'failed'
        ORDER BY started_at DESC LIMIT 1
        """
    ).fetchone()

    if latest_run is None:
        st.info(
            "No screen results yet. Run "
            "`python scripts/run_pipeline.py --from-stage screen` after ingest."
        )
    else:
        run_id = latest_run["run_id"]
        ranked = pd.read_sql_query(
            """
            SELECT q.ticker, c.name, c.sector, c.universe,
                   ROUND(q.composite_score, 1) AS composite_score,
                   q.metrics_passed AS passed, q.metrics_assessed AS assessed,
                   q.passed_screen, q.notes,
                   (SELECT MAX(f.confidence) FROM fundamentals_annual f WHERE f.ticker = q.ticker) AS confidence
            FROM quality_scores q
            JOIN companies c ON c.ticker = q.ticker
            WHERE q.run_id = ?
            ORDER BY q.composite_score DESC, q.ticker
            """,
            conn,
            params=(run_id,),
        )
        ranked = _filter_by_sector(ranked)
        passed_n = int(ranked["passed_screen"].sum())
        st.caption(
            f"Run `{run_id}` — {passed_n}/{len(ranked)} companies passed "
            f"(composite_score >= {QUALITY_SCORE_PASS_THRESHOLD})."
        )
        st.dataframe(ranked, use_container_width=True, height=500)

        st.markdown("**Why did a company pass or fail?** Pick a ticker for the per-metric breakdown.")
        pick = st.selectbox("Ticker", ranked["ticker"].tolist()) if not ranked.empty else None
        if pick:
            detail = pd.read_sql_query(
                """
                SELECT metric, status, ROUND(value, 4) AS value, absolute_floor_pass,
                       ROUND(sector_percentile, 1) AS sector_percentile,
                       sector_relative_pass, sector_peer_group
                FROM quant_scores
                WHERE run_id = ? AND ticker = ?
                ORDER BY metric
                """,
                conn,
                params=(run_id, pick),
            )
            st.dataframe(detail, use_container_width=True)

conn.close()
