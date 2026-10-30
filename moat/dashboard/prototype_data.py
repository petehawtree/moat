"""Read-only data access for the dashboard redesign prototypes.

The prototype gallery deliberately sits beside the production dashboard.  It
uses the same SQLite data, but keeps the exploratory presentation code out of
``app.py`` until a direction has been chosen.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

import pandas as pd


def latest_run_id(conn: sqlite3.Connection, table: str) -> str | None:
    """Return the newest successful pipeline run that produced ``table``."""
    allowed = {"quality_scores", "valuations", "committee_verdicts"}
    if table not in allowed:
        raise ValueError(f"Unsupported pipeline table: {table}")
    row = conn.execute(
        f"""
        SELECT run_id FROM pipeline_runs
        WHERE run_id IN (SELECT DISTINCT run_id FROM {table})
          AND status != 'failed'
        ORDER BY started_at DESC LIMIT 1
        """
    ).fetchone()
    return row["run_id"] if row else None


def most_complete_committee_run_id(conn: sqlite3.Connection) -> str | None:
    """Return the widest non-failed committee batch, newest on a tie.

    Sprint 6 used paired forward/reverse workers.  The reverse worker started
    seconds later but contains only its 49-row shard; those verdicts were then
    copied into the 117-row main run.  Selecting by ``started_at`` alone makes
    the shard look authoritative and hides ADBE/DECK's Investigate verdicts.
    Coverage is therefore the primary signal for committee batches.
    """
    row = conn.execute(
        """
        SELECT pr.run_id, COUNT(DISTINCT cv.ticker) AS verdict_count
        FROM pipeline_runs pr
        JOIN committee_verdicts cv ON cv.run_id = pr.run_id
        WHERE pr.status != 'failed'
        GROUP BY pr.run_id
        ORDER BY verdict_count DESC, pr.started_at DESC
        LIMIT 1
        """
    ).fetchone()
    return row["run_id"] if row else None


def load_gallery_data(conn: sqlite3.Connection) -> dict[str, Any]:
    """Load the compact, shared model used by all five prototype views."""
    quality_run = latest_run_id(conn, "quality_scores")
    valuation_run = latest_run_id(conn, "valuations")
    committee_run = most_complete_committee_run_id(conn)

    companies = pd.read_sql_query(
        "SELECT ticker, name, sector, industry, universe FROM companies WHERE is_active = 1",
        conn,
    )

    quality = pd.DataFrame()
    if quality_run:
        quality = pd.read_sql_query(
            """
            SELECT ticker, ROUND(composite_score, 1) AS quality_score,
                   metrics_passed, metrics_assessed, passed_screen
            FROM quality_scores WHERE run_id = ?
            """,
            conn,
            params=(quality_run,),
        )

    committee = pd.DataFrame()
    if committee_run:
        committee = pd.read_sql_query(
            """
            SELECT ticker, ROUND(overall_score, 1) AS overall_score, status,
                   ROUND(business_quality_score, 1) AS business_quality,
                   ROUND(competitive_moat_score, 1) AS competitive_moat,
                   ROUND(financial_strength_score, 1) AS financial_strength,
                   ROUND(management_score, 1) AS management,
                   ROUND(valuation_score, 1) AS valuation,
                   ROUND(risk_score, 1) AS risk,
                   bear_case_severity, data_confidence, investment_thesis,
                   key_things_to_monitor, ai_conclusion, ai_claims_run_ids,
                   created_at
            FROM committee_verdicts WHERE run_id = ?
            """,
            conn,
            params=(committee_run,),
        )

    valuations = pd.DataFrame()
    if valuation_run:
        valuations = pd.read_sql_query(
            """
            SELECT ticker, scenario, intrinsic_value_low, current_price,
                   margin_of_safety_pct, price_date
            FROM valuations
            WHERE run_id = ? AND method = 'owner_earnings_dcf'
            """,
            conn,
            params=(valuation_run,),
        )

    universe = companies.merge(quality, on="ticker", how="left").merge(
        committee, on="ticker", how="left"
    )
    if not valuations.empty:
        dcf = valuations.pivot_table(
            index="ticker", columns="scenario", values="intrinsic_value_low", aggfunc="first"
        ).rename(columns={"bear": "dcf_bear", "base": "dcf_base", "bull": "dcf_bull"})
        bear_rows = valuations[valuations["scenario"] == "bear"][
            ["ticker", "current_price", "margin_of_safety_pct", "price_date"]
        ]
        universe = universe.merge(dcf, on="ticker", how="left").merge(
            bear_rows, on="ticker", how="left"
        )

    committee_names = universe[universe["overall_score"].notna()].copy()
    committee_names = committee_names.sort_values(
        ["overall_score", "ticker"], ascending=[False, True]
    ).reset_index(drop=True)

    screened = int(quality["ticker"].nunique()) if not quality.empty else 0
    passed = int(quality["passed_screen"].sum()) if not quality.empty else 0
    statuses = committee["status"].value_counts().to_dict() if not committee.empty else {}
    funnel = {
        "universe": int(len(companies)),
        "screened": screened,
        "passed": passed,
        "briefed": int(len(committee)),
        "investigate": int(statuses.get("Investigate", 0)),
        "watch": int(statuses.get("Watch", 0)),
        "reject": int(statuses.get("Reject", 0)),
    }

    sector = pd.DataFrame()
    if not quality.empty:
        sector = companies[["ticker", "sector"]].merge(quality, on="ticker", how="inner")
        sector["sector"] = sector["sector"].fillna("No sector")
        sector = (
            sector.groupby("sector", as_index=False)
            .agg(screened=("ticker", "count"), passed=("passed_screen", "sum"))
        )
        sector["pass_rate"] = sector["passed"] / sector["screened"]
        sector = sector.sort_values("pass_rate", ascending=False)

    refreshed = conn.execute("SELECT MAX(date) AS value FROM price_history").fetchone()["value"]
    return {
        "runs": {"quality": quality_run, "valuation": valuation_run, "committee": committee_run},
        "universe": universe,
        "committee": committee_names,
        "valuations": valuations,
        "sector": sector,
        "funnel": funnel,
        "refreshed": refreshed,
    }


def load_company_detail(conn: sqlite3.Connection, ticker: str, data: dict[str, Any]) -> dict[str, Any]:
    """Load evidence and metric detail for one company in the gallery."""
    quality_run = data["runs"]["quality"]
    committee_run = data["runs"]["committee"]
    valuation_run = data["runs"]["valuation"]

    company = conn.execute("SELECT * FROM companies WHERE ticker = ?", (ticker,)).fetchone()
    verdict = None
    if committee_run:
        verdict = conn.execute(
            "SELECT * FROM committee_verdicts WHERE run_id = ? AND ticker = ?",
            (committee_run, ticker),
        ).fetchone()

    metrics = pd.DataFrame()
    if quality_run:
        metrics = pd.read_sql_query(
            """
            SELECT metric, status, value, sector_percentile,
                   absolute_floor_pass, sector_relative_pass
            FROM quant_scores WHERE run_id = ? AND ticker = ? ORDER BY metric
            """,
            conn,
            params=(quality_run, ticker),
        )

    valuation = pd.DataFrame()
    if valuation_run:
        valuation = pd.read_sql_query(
            """
            SELECT method, scenario, intrinsic_value_low, current_price,
                   margin_of_safety_pct, key_assumptions, price_date
            FROM valuations WHERE run_id = ? AND ticker = ?
            """,
            conn,
            params=(valuation_run, ticker),
        )

    claims_run_ids: dict[str, str] = {}
    if verdict and verdict["ai_claims_run_ids"]:
        try:
            claims_run_ids = json.loads(verdict["ai_claims_run_ids"])
        except (TypeError, json.JSONDecodeError):
            claims_run_ids = {}
    if not claims_run_ids:
        claims_run_ids = {
            row["analysis_type"]: row["run_id"]
            for row in conn.execute(
                "SELECT analysis_type, run_id FROM ai_analysis WHERE ticker = ? AND is_current = 1",
                (ticker,),
            )
        }

    claim_frames: list[pd.DataFrame] = []
    for analysis_type, run_id in claims_run_ids.items():
        frame = pd.read_sql_query(
            """
            SELECT ac.claim_id, ac.analysis_type, ac.claim_text, ac.assertion_status,
                   ci.quote, ci.accession_number, ci.section_id, f.document_url,
                   f.form_type, f.filing_date
            FROM analysis_claims ac
            LEFT JOIN citations ci ON ci.claim_id = ac.claim_id
            LEFT JOIN filings f ON f.accession_number = ci.accession_number
            WHERE ac.run_id = ? AND ac.ticker = ? AND ac.analysis_type = ?
            ORDER BY ac.claim_order
            """,
            conn,
            params=(run_id, ticker, analysis_type),
        )
        if not frame.empty:
            claim_frames.append(frame)
    claims = pd.concat(claim_frames, ignore_index=True) if claim_frames else pd.DataFrame()

    monitor: list[str] = []
    if verdict and verdict["key_things_to_monitor"]:
        try:
            value = json.loads(verdict["key_things_to_monitor"])
            monitor = value if isinstance(value, list) else []
        except (TypeError, json.JSONDecodeError):
            monitor = []

    filings = pd.DataFrame()
    if not claims.empty:
        filings = claims[
            ["accession_number", "form_type", "filing_date", "document_url"]
        ].dropna(subset=["accession_number"]).drop_duplicates()

    return {
        "company": dict(company) if company else {},
        "verdict": dict(verdict) if verdict else {},
        "metrics": metrics,
        "valuation": valuation,
        "claims": claims,
        "filings": filings,
        "monitor": monitor,
    }
