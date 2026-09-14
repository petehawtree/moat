"""Streamlit dashboard (PRD §9, §10). Sprint 1 shows raw ingest coverage;
Sprint 2 adds the ranked/scored table; Sprint 5 adds the Investment Brief
detail page.

Run with: streamlit run moat/dashboard/app.py
"""
from __future__ import annotations

import json
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

    st.divider()
    st.subheader("Sprint 4 — valuation")
    st.caption(
        "Owner Earnings DCF (bear/base/bull, docs/PRD_ADDENDUM.md §A16) plus three "
        "supporting cross-checks — FCF yield, EV/EBIT, and P/E vs. the company's own "
        "5-10yr range. **Margin of safety uses the bear scenario** — PRD §1's "
        "conservative 'low end of the range', never the midpoint. A bear case whose "
        "intrinsic value is zero or negative shows as its own label, never a number: "
        "the naive `(low - price) / low` formula sign-flips on a negative low, making "
        "the least-safe case look the safest (§A16.4 / V3's guard)."
    )

    latest_valuation_run = conn.execute(
        """
        SELECT run_id FROM pipeline_runs
        WHERE run_id IN (SELECT DISTINCT run_id FROM valuations)
          AND status != 'failed'
        ORDER BY started_at DESC LIMIT 1
        """
    ).fetchone()

    if latest_valuation_run is None:
        st.info(
            "No valuation results yet. Run "
            "`python scripts/run_pipeline.py --from-stage valuation` after quality."
        )
    else:
        v_run_id = latest_valuation_run["run_id"]
        val = pd.read_sql_query(
            """
            SELECT v.ticker, c.name, c.sector, v.method, v.scenario,
                   v.intrinsic_value_low, v.intrinsic_value_high, v.current_price,
                   v.margin_of_safety_pct, v.key_assumptions
            FROM valuations v JOIN companies c ON c.ticker = v.ticker
            WHERE v.run_id = ?
            """,
            conn,
            params=(v_run_id,),
        )
        val = _filter_by_sector(val)

        def _bear_case_cell(row) -> str:
            # NULL intrinsic_value_low means the DCF itself couldn't be
            # computed for this company (no fiscal year with all of
            # net_income/D&A/capex — see key_assumptions for the reason);
            # that's a different, distinguishable case from a *computed*
            # value that happens to be <= 0 (the guarded sign-flip case).
            if pd.isna(row["intrinsic_value_low"]):
                return "no data"
            if row["intrinsic_value_low"] <= 0:
                return "bear case: negative — not investable on this basis"
            return f"{row['margin_of_safety_pct']:+.1%}"

        summary_rows = []
        for ticker, group in val.groupby("ticker"):
            name = group["name"].iloc[0]
            sector = group["sector"].iloc[0]
            current_price = group["current_price"].iloc[0]
            dcf = group[group["method"] == "owner_earnings_dcf"].set_index("scenario")
            fcf_row = group[group["method"] == "fcf_yield"]
            ev_row = group[group["method"] == "ev_ebit"]
            pe_row = group[group["method"] == "pe_historical"]

            row = {
                "ticker": ticker, "name": name, "sector": sector,
                "current_price": round(current_price, 2) if pd.notna(current_price) else None,
            }
            for scenario in ("bear", "base", "bull"):
                value = dcf.loc[scenario, "intrinsic_value_low"] if scenario in dcf.index else None
                row[f"dcf_{scenario}"] = round(value, 2) if pd.notna(value) else None
            row["margin_of_safety"] = (
                _bear_case_cell(dcf.loc["bear"]) if "bear" in dcf.index else "no data"
            )
            if not fcf_row.empty:
                fcf_ka = json.loads(fcf_row.iloc[0]["key_assumptions"])
                row["fcf_yield"] = f"{fcf_ka['fcf_yield']:.1%}" if "fcf_yield" in fcf_ka else "unavailable"
            if not ev_row.empty:
                ev_ka = json.loads(ev_row.iloc[0]["key_assumptions"])
                row["ev_ebit"] = f"{ev_ka['ev_ebit_multiple']:.1f}x" if "ev_ebit_multiple" in ev_ka else "unavailable"
            if not pe_row.empty:
                pe_ka = json.loads(pe_row.iloc[0]["key_assumptions"])
                current_pe = pe_ka.get("current")
                row["pe_current"] = f"{current_pe:.1f}x" if current_pe is not None else "n/a"
                row["pe_range_years"] = pe_ka.get("years_covered")
                row["pe_low_confidence"] = pe_ka.get("low_confidence")
            summary_rows.append(row)

        summary = pd.DataFrame(summary_rows).sort_values("ticker")
        st.caption(
            f"Run `{v_run_id}` — {len(summary)} companies valued. "
            f"P/E range low-confidence (< 5 years of own history): "
            f"{int(summary['pe_low_confidence'].sum())}/{len(summary)} — "
            "expected until price_history is backfilled beyond the current ~2yr "
            "window for tickers ingested before Sprint 4 (see sprint-4.md)."
        )
        st.dataframe(summary, use_container_width=True, height=500)

        st.markdown("**Full assumptions for one company** — the trailing owner-earnings "
                     "series, growth/discount/terminal-growth inputs, and every "
                     "supporting method's raw figures, not just the summary above.")
        pick_val = st.selectbox("Ticker ", summary["ticker"].tolist()) if not summary.empty else None
        if pick_val:
            detail_rows = val[val["ticker"] == pick_val][["method", "scenario", "key_assumptions"]]
            for _, r in detail_rows.iterrows():
                label = r["method"] if r["scenario"] is None else f"{r['method']} ({r['scenario']})"
                st.markdown(f"`{label}`")
                st.json(json.loads(r["key_assumptions"]))

conn.close()
