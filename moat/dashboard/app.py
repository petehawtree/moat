"""Streamlit dashboard (PRD §9, §10). Sprint 1 shows raw ingest coverage;
Sprint 2 adds the ranked/scored table; Sprint 5 adds the Investment Brief
detail page. Reorganized into three tabs (Overview / Investment Committee /
Universe & Screening) so a first-time viewer lands on the committee funnel
and verdicts rather than pipeline internals.

Run with: streamlit run moat/dashboard/app.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import streamlit as st

from moat.committee.parser import extract_statements, extract_verdict
from moat.config import QUALITY_SCORE_PASS_THRESHOLD
from moat.db.connection import get_connection


def _md(text: str) -> str:
    """Escape `$` before handing AI-generated free text to st.markdown.

    Streamlit renders anything between a pair of `$` as LaTeX (KaTeX). AI
    prose routinely mentions two dollar figures in one statement (e.g. "$22.5B
    ... to $25.2B"), which Streamlit then silently parses as a math span
    instead of displaying the text — found while reviewing a real committee
    brief, not something a test over parsed claim_text/refs would catch.
    """
    return text.replace("$", "\\$")

st.set_page_config(page_title="Project Moat", layout="wide")
st.title("Project Moat")
st.warning(
    "Personal research project — **not investment advice. Capital at risk.**"
)

def _resolve_claim_citation(claim_id: int, conn) -> dict | None:
    """One claim_id -> its original filing quote (§A19.6 decision: surface
    the raw quote inline next to every persona inference, no second-pass
    entailment check — the reader eyeballs support/non-support directly).
    """
    row = conn.execute(
        "SELECT ci.quote, ci.accession_number, ci.section_id, f.document_url "
        "FROM citations ci LEFT JOIN filings f ON f.accession_number = ci.accession_number "
        "WHERE ci.claim_id = ? LIMIT 1",
        (claim_id,),
    ).fetchone()
    return dict(row) if row else None


def _cite_label(cite: dict) -> str:
    """'accession · section' caption, with the accession linked to its SEC
    EDGAR filing index page when the filing row carries a URL."""
    acc = cite["accession_number"]
    if cite.get("document_url"):
        acc = f"[{acc}]({cite['document_url']})"
    return f"{acc} · {cite['section_id']}"


def _brief_url(ticker: str) -> str:
    """Shareable deep link to one ticker's Investment Brief. Absolute when
    Streamlit knows the page URL (a real browser session), relative
    otherwise (AppTest), which a browser resolves against the current page
    anyway."""
    base = (st.context.url or "").split("?")[0]
    return f"{base}?brief={ticker}"


def _current_claims_run_ids(ticker: str, conn) -> dict[str, str]:
    """{analysis_type: run_id holding its analysis_claims} for the current
    ai_analysis rows, following the cache-hit reused_from_run_id chain
    (committee.py's _resolve_claims_run_id docstring)."""
    from moat.committee.committee import _resolve_claims_run_id

    rows = conn.execute(
        "SELECT analysis_type, run_id FROM ai_analysis WHERE ticker = ? AND is_current = 1",
        (ticker,),
    ).fetchall()
    return {
        r["analysis_type"]: _resolve_claims_run_id(ticker, r["analysis_type"], r["run_id"], conn)
        for r in rows
    }


def _latest_run_for(table: str, ticker: str, conn) -> str | None:
    """Newest non-failed run with rows for this ticker in `table`
    (valuations or quant_scores)."""
    row = conn.execute(
        f"SELECT run_id FROM pipeline_runs WHERE run_id IN "
        f"(SELECT DISTINCT run_id FROM {table} WHERE ticker = ?) "
        "AND status != 'failed' ORDER BY started_at DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    return row["run_id"] if row else None


def _verdict_inputs(ticker: str, verdict_row, conn) -> dict:
    """The upstream runs a committee verdict was scored on, so every brief
    section renders the same evidence the verdict saw — not whatever ran
    most recently (judge finding: a brief could pair a verdict with newer,
    unscored valuation/quant/AI evidence).

    Verdicts persisted before committee_verdicts recorded its inputs have
    NULL provenance; those fall back to the latest runs with
    `recorded=False`, which the brief states explicitly.
    """
    latest = {
        "valuation_run_id": _latest_run_for("valuations", ticker, conn),
        "quality_run_id": _latest_run_for("quant_scores", ticker, conn),
        "ai_claims_run_ids": _current_claims_run_ids(ticker, conn),
    }
    if verdict_row["valuation_run_id"] is None:
        return {**latest, "recorded": False, "newer": []}
    inputs = {
        "valuation_run_id": verdict_row["valuation_run_id"],
        "quality_run_id": verdict_row["quality_run_id"],
        "ai_claims_run_ids": json.loads(verdict_row["ai_claims_run_ids"] or "{}"),
        "recorded": True,
    }
    newer = []
    if latest["valuation_run_id"] not in (None, inputs["valuation_run_id"]):
        newer.append(f"valuation (run `{latest['valuation_run_id']}`)")
    if latest["quality_run_id"] not in (None, inputs["quality_run_id"]):
        newer.append(f"quant screen (run `{latest['quality_run_id']}`)")
    changed_ai = sorted(
        t for t, r in latest["ai_claims_run_ids"].items() if inputs["ai_claims_run_ids"].get(t) != r
    )
    if changed_ai:
        newer.append(f"AI analysis ({', '.join(changed_ai)})")
    inputs["newer"] = newer
    return inputs


def _source_filings(ticker: str, claims_run_ids: dict[str, str], conn) -> list[dict]:
    """The SEC filing(s) the brief is actually grounded on — every distinct
    accession cited by the given analysis claims runs, newest first.
    Derived from the citations rather than "latest 10-K in `filings`",
    because a newer 10-K can be ingested without the analysis being
    re-run against it.
    """
    accessions: set[str] = set()
    for analysis_type, claims_run_id in claims_run_ids.items():
        accessions.update(
            a["accession_number"] for a in conn.execute(
                "SELECT DISTINCT ci.accession_number FROM citations ci "
                "JOIN analysis_claims ac ON ac.claim_id = ci.claim_id "
                "WHERE ac.run_id = ? AND ac.ticker = ? AND ac.analysis_type = ?",
                (claims_run_id, ticker, analysis_type),
            )
        )
    if not accessions:
        return []
    placeholders = ",".join("?" * len(accessions))
    return [dict(f) for f in conn.execute(
        f"SELECT accession_number, form_type, filing_date, document_url FROM filings "
        f"WHERE accession_number IN ({placeholders}) ORDER BY filing_date DESC",
        tuple(accessions),
    )]


def _render_persona_response(raw_text: str, conn, key_prefix: str, persona: str) -> None:
    """Verdict + STATEMENTs, each STATEMENT's [refs: N] resolved back to
    its original cited quote in an expander — the dashboard-side half of
    the entailment decision (moat/committee/committee.py's module
    docstring; parser.py's extract_verdict/extract_statements).

    An unreferenced STATEMENT is flagged explicitly for Quality/Bear (whose
    claims are supposed to trace to a real filing quote — an unreferenced
    one is analyst synthesis, not a grounded claim, and the reader should
    see that distinction rather than a bullet indistinguishable from a
    cited one). Not flagged for Valuation, where a figure is grounded by
    appearing in the CONTEXT block's own quant/DCF numbers, never a claim
    id, by design (prompt.py's rule 1) — found missing entirely on the
    judge's review of the first real pilot run.
    """
    verdict = extract_verdict(raw_text)
    if verdict:
        st.markdown(_md(verdict))
    for i, stmt in enumerate(extract_statements(raw_text)):
        st.markdown(f"- {_md(stmt.text)}")
        if stmt.refs:
            with st.expander(f"↳ {key_prefix} statement {i + 1}: {len(stmt.refs)} supporting citation(s)", expanded=False):
                for claim_id in stmt.refs:
                    cite = _resolve_claim_citation(claim_id, conn)
                    if cite:
                        st.caption(f"[{claim_id}] {_cite_label(cite)}")
                        st.markdown(f"> {_md(cite['quote'])}")
                    else:
                        st.caption(f"[{claim_id}] — no stored citation found")
        elif persona != "valuation":
            st.caption("⚠️ no citation for this statement — analyst synthesis, not a filing-grounded claim")


def _render_moat_evidence(ticker: str, claims_run_id: str | None, conn) -> None:
    """PRD §10's 'moat evidence' section — the ai_analysis 'moat' claims
    and their citations directly, distinct from the free-text bull case
    prose above (which synthesizes moat alongside everything else).
    Added after judge review of the first real pilot run found this PRD
    §10 field had no dedicated section at all.

    `claims_run_id` is the run actually holding the claims (already
    resolved through the cache-hit chain by _verdict_inputs) — querying a
    cache-hit ai_analysis row's own run_id finds zero claims, which showed
    real moat evidence as "not available" for AAPL and others.
    """
    if claims_run_id is None:
        st.markdown("_not available_")
        return
    claims = conn.execute(
        "SELECT claim_id, claim_text, assertion_status FROM analysis_claims "
        "WHERE run_id = ? AND ticker = ? AND analysis_type = 'moat' ORDER BY claim_order",
        (claims_run_id, ticker),
    ).fetchall()
    if not claims:
        st.markdown("_not available_")
        return
    for c in claims:
        if c["assertion_status"] == "insufficient_evidence":
            st.markdown(f"- _insufficient evidence:_ {_md(c['claim_text'])}")
            continue
        st.markdown(f"- {_md(c['claim_text'])}")
        cite = _resolve_claim_citation(c["claim_id"], conn)
        if cite:
            with st.expander(f"↳ [{c['claim_id']}] source quote", expanded=False):
                st.caption(_cite_label(cite))
                st.markdown(f"> {_md(cite['quote'])}")


def _render_financial_quality(ticker: str, quality_run_id: str | None, conn) -> None:
    """PRD §10's 'financial quality' section — the quant screen metrics
    from the run the verdict was scored on (moat/screen/quant_screen.py),
    same data as the Sprint 2 section's drill-down, pulled into the brief
    itself rather than requiring a second lookup."""
    if quality_run_id is None:
        st.markdown("_not available_")
        return
    df = pd.read_sql_query(
        "SELECT metric, ROUND(value, 4) AS value, status, ROUND(sector_percentile, 1) AS sector_percentile "
        "FROM quant_scores WHERE run_id = ? AND ticker = ? ORDER BY metric",
        conn, params=(quality_run_id, ticker),
    )
    if df.empty:
        st.markdown("_not available_")
        return
    st.dataframe(df, use_container_width=True, hide_index=True)


def _render_valuation_range(ticker: str, valuation_run_id: str | None, conn) -> None:
    """PRD §10's 'valuation range' and 'margin of safety' sections — the
    DCF bear/base/bull range and current price from the `valuations` run
    the verdict was scored on
    (moat/valuation/engine.py), same sign-guard rendering as the Sprint 4
    section, pulled into the brief itself."""
    if valuation_run_id is None:
        st.markdown("_not available_")
        return
    rows = conn.execute(
        "SELECT method, scenario, intrinsic_value_low, current_price, margin_of_safety_pct "
        "FROM valuations WHERE run_id = ? AND ticker = ? AND method = 'owner_earnings_dcf'",
        (valuation_run_id, ticker),
    ).fetchall()
    by_scenario = {r["scenario"]: r for r in rows}
    if "bear" not in by_scenario:
        st.markdown("_not available_")
        return
    current_price = by_scenario["bear"]["current_price"]
    st.markdown(f"Current price: **${current_price:,.2f}**" if current_price is not None else "Current price: _not available_")
    for scenario in ("bear", "base", "bull"):
        r = by_scenario.get(scenario)
        if r is None or r["intrinsic_value_low"] is None:
            st.markdown(f"- DCF {scenario}: _not available_")
        elif r["intrinsic_value_low"] <= 0:
            st.markdown(f"- DCF {scenario}: ${r['intrinsic_value_low']:,.2f} — **bear case negative, not investable on this basis**")
        else:
            st.markdown(f"- DCF {scenario}: ${r['intrinsic_value_low']:,.2f}")
    bear = by_scenario["bear"]
    if bear["intrinsic_value_low"] is not None and bear["intrinsic_value_low"] > 0:
        st.markdown(f"Margin of safety (bear case): **{bear['margin_of_safety_pct']:+.1%}**")
    else:
        st.markdown("Margin of safety: _bear case unavailable/negative — no numeric margin of safety_")


conn = get_connection()
company_count = conn.execute("SELECT COUNT(*) AS n FROM companies WHERE is_active = 1").fetchone()["n"]

if company_count == 0:
    st.info(
        "No companies loaded yet. Run `python scripts/run_pipeline.py --init-db` "
        "to load the universe and ingest fundamentals/prices."
    )
else:
    latest_price_date = conn.execute("SELECT MAX(date) AS d FROM price_history").fetchone()["d"]
    if latest_price_date:
        d = datetime.fromisoformat(latest_price_date)
        st.caption(f"Data refreshed: {d.day} {d.strftime('%b %Y')}")

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

    # --- shared data loads, used across tabs ---

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

    latest_run = conn.execute(
        """
        SELECT run_id FROM pipeline_runs
        WHERE run_id IN (SELECT DISTINCT run_id FROM quality_scores)
          AND status != 'failed'
        ORDER BY started_at DESC LIMIT 1
        """
    ).fetchone()
    ranked = pd.DataFrame()
    if latest_run is not None:
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

    latest_valuation_run = conn.execute(
        """
        SELECT run_id FROM pipeline_runs
        WHERE run_id IN (SELECT DISTINCT run_id FROM valuations)
          AND status != 'failed'
        ORDER BY started_at DESC LIMIT 1
        """
    ).fetchone()
    val = pd.DataFrame()
    if latest_valuation_run is not None:
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

    latest_committee_run = conn.execute(
        """
        SELECT run_id FROM pipeline_runs
        WHERE run_id IN (SELECT DISTINCT run_id FROM committee_verdicts)
          AND status != 'failed'
        ORDER BY started_at DESC LIMIT 1
        """
    ).fetchone()
    committee = pd.DataFrame()
    if latest_committee_run is not None:
        c_run_id = latest_committee_run["run_id"]
        committee = pd.read_sql_query(
            """
            SELECT cv.ticker, c.name, c.sector,
                   ROUND(cv.overall_score, 1) AS overall_score, cv.status,
                   ROUND(cv.business_quality_score, 1) AS business_quality,
                   ROUND(cv.competitive_moat_score, 1) AS competitive_moat,
                   ROUND(cv.financial_strength_score, 1) AS financial_strength,
                   ROUND(cv.management_score, 1) AS management,
                   ROUND(cv.valuation_score, 1) AS valuation,
                   ROUND(cv.risk_score, 1) AS risk,
                   cv.bear_case_severity, cv.data_confidence, cv.ai_claims_run_ids
            FROM committee_verdicts cv JOIN companies c ON c.ticker = cv.ticker
            WHERE cv.run_id = ?
            ORDER BY cv.overall_score DESC
            """,
            conn,
            params=(c_run_id,),
        )
        committee = _filter_by_sector(committee)
        # Link columns rendered via st.column_config.LinkColumn below: the
        # brief deep link (?brief=TICKER, read back further down) and the
        # SEC filing the analysis was grounded on.
        committee.insert(2, "brief", committee["ticker"].map(_brief_url))
        # The filing this verdict's claims cite — from its recorded claims
        # runs, falling back to the current ones for pre-provenance verdicts.
        committee.insert(3, "source_filing", [
            (f[0]["document_url"] if (f := _source_filings(
                t, json.loads(ids) if ids else _current_claims_run_ids(t, conn), conn,
            )) else None)
            for t, ids in zip(committee["ticker"], committee.pop("ai_claims_run_ids"))
        ])

    # ?brief=TICKER deep link: open straight onto the Investment Committee
    # tab with that ticker's brief selected.
    brief_param = st.query_params.get("brief")
    if brief_param not in (committee["ticker"].tolist() if not committee.empty else []):
        brief_param = None

    # Cross-tab metrics computed once here (not inside a `with tab_*:` block) —
    # tab_committee's caption and tab_overview's metric row both read these, and
    # Streamlit executes every tab body top to bottom regardless of display
    # order, so a value assigned inside one tab's block is still visible in a
    # later one *only* by accident of code order. Keeping them here means
    # reordering the `with` blocks below (e.g. to match display order) can
    # never raise a NameError.
    screened_n = len(ranked) if latest_run is not None else None
    passed_n = int(ranked["passed_screen"].sum()) if latest_run is not None else None
    committee_n = len(committee) if latest_committee_run is not None else None
    status_counts = committee["status"].value_counts() if latest_committee_run is not None else {}

    tab_overview, tab_committee, tab_universe = st.tabs(
        ["Overview", "Investment Committee", "Universe & Screening"],
        default="Investment Committee" if brief_param else None,
    )

    with tab_overview:
        cols = st.columns(6)
        cols[0].metric("Universe", len(coverage))
        cols[1].metric("Screened", screened_n if screened_n is not None else "—")
        cols[2].metric("Passed screen", passed_n if passed_n is not None else "—")
        cols[3].metric("Committee briefs", committee_n if committee_n is not None else "—")
        cols[4].metric("Investigate", status_counts.get("Investigate", 0) if latest_committee_run is not None else "—")
        cols[5].metric(
            "Watch / Reject",
            f"{status_counts.get('Watch', 0)} / {status_counts.get('Reject', 0)}"
            if latest_committee_run is not None else "—",
        )

        if latest_committee_run is not None:
            st.caption(
                f"Committee briefs are a deliberately partial pilot ({committee_n}/{passed_n} "
                "companies that passed the screen) — not every passing company has been run "
                "through yet, and `assign_status()`'s 70/50 score thresholds are pilot-then-lock "
                "starting values, not yet validated against real output. See sprint-5-plan.md."
            )

        st.subheader("Screen pass rate by sector")
        if latest_run is None or ranked.empty:
            st.info("No screen results yet.")
        else:
            sector_breakdown = (
                ranked.assign(sector=ranked["sector"].fillna("(no sector)"))
                .groupby("sector")
                .agg(passed=("passed_screen", "sum"), screened=("ticker", "count"))
            )
            # Passed/screened counts alone read as sector size, not pass rate —
            # Technology dominating a stacked passed-vs-not_passed chart is an
            # artifact of how many Technology companies were screened, not of
            # how well the sector passes. Pass rate isolates the latter.
            sector_breakdown["pass_rate"] = sector_breakdown["passed"] / sector_breakdown["screened"]
            sector_breakdown = sector_breakdown.sort_values("pass_rate", ascending=True)
            st.bar_chart(sector_breakdown[["pass_rate"]], horizontal=True)
            with st.expander("Passed / screened counts per sector"):
                st.dataframe(
                    sector_breakdown[["passed", "screened"]].sort_values("screened", ascending=False),
                    use_container_width=True,
                )

    with tab_universe:
        st.subheader("Ingest coverage")
        col1, col2, col3 = st.columns(3)
        col1.metric("Companies in universe", len(coverage))

        fundamentals_count = conn.execute(
            "SELECT COUNT(DISTINCT ticker) AS n FROM fundamentals_annual"
        ).fetchone()["n"]
        col2.metric("With fundamentals data", fundamentals_count)

        prices_count = conn.execute("SELECT COUNT(DISTINCT ticker) AS n FROM price_history").fetchone()["n"]
        col3.metric("With price history", prices_count)

        st.dataframe(coverage, use_container_width=True, height=500)

        thin = coverage[coverage["years_of_fundamentals"] < 3]
        if not thin.empty:
            with st.expander(f"{len(thin)} companies with fewer than 3 years of fundamentals"):
                st.dataframe(thin, use_container_width=True)

        st.divider()
        st.subheader("Quant screen ranking")
        st.caption(
            "Sector-relative screen (docs/PRD_ADDENDUM.md §A2/§A9): each of the 8 "
            "PRD §4 metrics passes only if it clears both an absolute floor and "
            "the top-tercile bar within its own GICS sector. composite_score is "
            "the % of *assessable* metrics passed — metrics we couldn't measure are "
            "excluded rather than counted as failures (§A13), so `assessed` shows "
            "how much of the company we could actually see."
        )

        if latest_run is None:
            st.info(
                "No screen results yet. Run "
                "`python scripts/run_pipeline.py --from-stage screen` after ingest."
            )
        else:
            st.caption(
                f"Run `{latest_run['run_id']}` — {passed_n}/{len(ranked)} companies passed "
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
                    params=(latest_run["run_id"], pick),
                )
                st.dataframe(detail, use_container_width=True)

        st.divider()
        st.subheader("Valuation")
        st.caption(
            "Owner Earnings DCF (bear/base/bull, docs/PRD_ADDENDUM.md §A16) plus three "
            "supporting cross-checks — FCF yield, EV/EBIT, and P/E vs. the company's own "
            "5-10yr range. **Margin of safety uses the bear scenario** — PRD §1's "
            "conservative 'low end of the range', never the midpoint. A bear case whose "
            "intrinsic value is zero or negative shows as its own label, never a number: "
            "the naive `(low - price) / low` formula sign-flips on a negative low, making "
            "the least-safe case look the safest (§A16.4 / V3's guard)."
        )

        if latest_valuation_run is None:
            st.info(
                "No valuation results yet. Run "
                "`python scripts/run_pipeline.py --from-stage valuation` after quality."
            )
        else:
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

    with tab_committee:
        st.subheader("Investment committee")
        st.caption(
            "Three persona perspectives (Quality / Bear / Valuation Analyst, PRD §7) "
            "consolidated into the PRD §8 weighted score. **bear_case_severity is not "
            "one of the six weighted components** — a 'high' severity caps an "
            "otherwise-Investigate score to Watch (never an independent Reject, never "
            "rescues a low score; see `assign_status()`). Every persona statement's "
            "`[refs: N]` resolves back to its original filing quote below — no "
            "second-pass entailment check; the reader eyeballs support directly "
            "(§A19.6)."
        )

        if latest_committee_run is None:
            st.info(
                "No committee results yet. Run "
                "`python scripts/run_pipeline.py --from-stage committee` after valuation."
            )
        else:
            c_run_id = latest_committee_run["run_id"]
            st.caption(
                f"Run `{c_run_id}` — {len(committee)} companies. "
                f"Investigate: {status_counts.get('Investigate', 0)}, "
                f"Watch: {status_counts.get('Watch', 0)}, "
                f"Reject: {status_counts.get('Reject', 0)}."
            )
            st.dataframe(
                committee, use_container_width=True, height=500,
                column_config={
                    "brief": st.column_config.LinkColumn("brief", display_text="Open brief ↗"),
                    "source_filing": st.column_config.LinkColumn(
                        "source filing", display_text="SEC filing ↗",
                        help="The SEC filing the AI analysis and committee cite (EDGAR filing index)",
                    ),
                },
            )

            st.markdown("**Investment Brief** — one-page view per company (PRD §10).")
            brief_options = committee["ticker"].tolist()

            def _sync_brief_param() -> None:
                st.query_params["brief"] = st.session_state["brief_ticker"]

            pick_brief = st.selectbox(
                "Ticker  ", brief_options,
                index=brief_options.index(brief_param) if brief_param else 0,
                key="brief_ticker", on_change=_sync_brief_param,
            ) if not committee.empty else None
            if pick_brief:
                verdict_row = conn.execute(
                    "SELECT * FROM committee_verdicts WHERE run_id = ? AND ticker = ?",
                    (c_run_id, pick_brief),
                ).fetchone()
                company_row = conn.execute(
                    "SELECT * FROM companies WHERE ticker = ?", (pick_brief,)
                ).fetchone()

                st.markdown(f"### {pick_brief} — {company_row['name']}")
                st.caption(
                    f"{company_row['sector'] or 'no GICS sector'} · "
                    f"data confidence: {verdict_row['data_confidence']} · "
                    f"status: **{verdict_row['status']}** ({verdict_row['overall_score']:.1f}/100)"
                )
                st.caption(
                    f"Company overview: {company_row['universe']} universe"
                    + (f" · CIK {company_row['cik']}" if company_row["cik"] else "")
                )
                inputs = _verdict_inputs(pick_brief, verdict_row, conn)
                if not inputs["recorded"]:
                    st.warning(
                        "This verdict predates input provenance, so the runs it was scored on "
                        "weren't recorded. The sections below show the **latest** inputs, which "
                        "may differ from what the committee saw. Re-running the committee stage "
                        "records them."
                    )
                elif inputs["newer"]:
                    st.warning(
                        "Newer inputs exist than this verdict was scored on: "
                        + "; ".join(inputs["newer"])
                        + ". The brief below shows the inputs the verdict actually used; "
                        "re-run the committee stage to score the newer ones."
                    )
                source_filings = _source_filings(pick_brief, inputs["ai_claims_run_ids"], conn)
                if source_filings:
                    st.markdown("**Source filing:** " + " · ".join(
                        f"[{f['form_type']} filed {f['filing_date']} ({f['accession_number']}) ↗]({f['document_url']})"
                        for f in source_filings
                    ))
                else:
                    st.caption("Source filing: none cited")
                st.markdown(f"[Link to this brief ↗]({_brief_url(pick_brief)})")

                st.markdown("#### Moat evidence")
                _render_moat_evidence(pick_brief, inputs["ai_claims_run_ids"].get("moat"), conn)

                st.markdown("#### Financial quality")
                _render_financial_quality(pick_brief, inputs["quality_run_id"], conn)

                st.markdown("#### Valuation range & margin of safety")
                _render_valuation_range(pick_brief, inputs["valuation_run_id"], conn)

                st.markdown("#### Investment thesis")
                st.caption(
                    "⚠️ Synthesized summary (Quality + Valuation Analyst verdict prose), not "
                    "individually cited — see Bull case / Bear case below for the underlying "
                    "STATEMENTs and their resolvable citations."
                )
                st.markdown(_md(verdict_row["investment_thesis"]) if verdict_row["investment_thesis"] else "_not available_")

                st.markdown("#### Bull case (Quality + Valuation Analyst)")
                _render_persona_response(verdict_row["quality_analyst_view"] or "", conn, f"{pick_brief}_quality", "quality")
                _render_persona_response(verdict_row["valuation_analyst_view"] or "", conn, f"{pick_brief}_valuation", "valuation")

                st.markdown("#### Bear case")
                _render_persona_response(verdict_row["bear_analyst_view"] or "", conn, f"{pick_brief}_bear", "bear")

                st.markdown("#### Key things to monitor")
                try:
                    monitor_items = json.loads(verdict_row["key_things_to_monitor"] or "[]")
                except (json.JSONDecodeError, TypeError):
                    monitor_items = []
                for item in monitor_items:
                    st.markdown(f"- {_md(item)}")
                if not monitor_items:
                    st.markdown("_not available_")

                st.markdown("#### AI conclusion")
                st.caption(
                    "⚠️ Synthesized summary (all three persona verdicts), not individually "
                    "cited — same caveat as Investment thesis above."
                )
                st.markdown(_md(verdict_row["ai_conclusion"]) if verdict_row["ai_conclusion"] else "_not available_")

                with st.expander("Component scores"):
                    st.json({
                        "business_quality_score": verdict_row["business_quality_score"],
                        "competitive_moat_score": verdict_row["competitive_moat_score"],
                        "financial_strength_score": verdict_row["financial_strength_score"],
                        "management_score": verdict_row["management_score"],
                        "valuation_score": verdict_row["valuation_score"],
                        "risk_score": verdict_row["risk_score"],
                        "bear_case_severity": verdict_row["bear_case_severity"],
                    })

conn.close()
