"""Five Streamlit-native UI directions for the Project Moat dashboard.

Run with:
    streamlit run moat/dashboard/prototypes.py

This is a review gallery, not a replacement for ``app.py``.  Every concept
reads the real database and intentionally emphasizes a different research
job so a redesign decision can be made with something concrete on screen.
"""
from __future__ import annotations

import html
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import streamlit as st

from moat.dashboard.prototype_data import load_company_detail, load_gallery_data
from moat.db.connection import get_connection


st.set_page_config(
    page_title="Moat · UI concept gallery",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)


COMMON_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@500;600;700&display=swap');
:root {
  --ink:#15211b; --muted:#68746e; --paper:#f5f4ef; --surface:#ffffff;
  --line:#dfe3dd; --green:#0c6b4e; --green-soft:#dceee6; --amber:#b87716;
  --amber-soft:#f8ead2; --red:#a44336; --red-soft:#f5dfda; --navy:#152b3a;
}
html, body, [class*="css"] { font-family:'DM Sans', sans-serif; }
.stApp { background:var(--paper); color:var(--ink); }
[data-testid="stAppViewContainer"] > .main { background:var(--paper); }
.block-container { max-width:1440px; padding-top:1.3rem; padding-bottom:4rem; }
[data-testid="stSidebar"] { background:#10231d; border-right:0; }
[data-testid="stSidebar"] * { color:#eef4ef; }
[data-testid="stSidebar"] [data-testid="stRadio"] label { padding:.16rem 0; }
[data-testid="stMetric"] { background:var(--surface); border:1px solid var(--line); border-radius:12px; padding:14px 16px; }
[data-testid="stMetricLabel"] { color:var(--muted); }
[data-testid="stMetricValue"] { color:var(--ink); font-family:'Manrope', sans-serif; }
h1,h2,h3 { font-family:'Manrope',sans-serif !important; letter-spacing:-.035em; color:var(--ink); }
h1 { font-size:2.15rem !important; }
h2 { font-size:1.45rem !important; }
h3 { font-size:1.08rem !important; }
.moat-brand { font:700 1.38rem 'Manrope'; letter-spacing:-.04em; margin:.25rem 0 .15rem; }
.moat-brand span { color:#79d0a8; }
.sidebar-kicker { color:#9fb3aa !important; font-size:.72rem; text-transform:uppercase; letter-spacing:.12em; margin-bottom:1rem; }
.topbar { display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid var(--line); padding:.2rem 0 .8rem; margin-bottom:1.35rem; }
.eyebrow { color:var(--green); text-transform:uppercase; letter-spacing:.12em; font-size:.72rem; font-weight:700; }
.meta { color:var(--muted); font-size:.78rem; }
.hero { background:var(--navy); color:#f6f5ef; border-radius:18px; padding:30px 34px; margin:8px 0 22px; position:relative; overflow:hidden; }
.hero:after { content:''; position:absolute; width:240px; height:240px; border-radius:50%; right:-70px; top:-110px; border:45px solid rgba(121,208,168,.12); }
.hero h1,.hero h2,.hero h3 { color:#fff; margin:0; }
.hero p { max-width:780px; color:#c7d2cd; font-size:1.02rem; margin:.55rem 0 0; }
.concept-note { background:#eef2ee; border-left:3px solid var(--green); padding:12px 16px; border-radius:0 10px 10px 0; color:#435049; margin-bottom:18px; font-size:.88rem; }
.card { background:var(--surface); border:1px solid var(--line); border-radius:14px; padding:18px 20px; height:100%; box-shadow:0 1px 1px rgba(20,40,30,.02); }
.card-label { color:var(--muted); font-size:.72rem; text-transform:uppercase; letter-spacing:.09em; font-weight:700; }
.card-value { font:700 1.65rem 'Manrope'; color:var(--ink); margin-top:4px; }
.card-sub { color:var(--muted); font-size:.82rem; margin-top:3px; }
.pill { display:inline-flex; align-items:center; border-radius:999px; padding:4px 9px; font-size:.72rem; font-weight:700; letter-spacing:.02em; }
.pill-watch { background:var(--amber-soft); color:#85540d; }
.pill-investigate { background:var(--green-soft); color:var(--green); }
.pill-reject { background:var(--red-soft); color:var(--red); }
.pill-neutral { background:#e9ece9; color:#53615a; }
.company-title { display:flex; align-items:center; gap:12px; margin-bottom:4px; }
.ticker-mark { width:42px; height:42px; border-radius:11px; display:flex; align-items:center; justify-content:center; background:#dceee6; color:var(--green); font-weight:800; font-size:.78rem; }
.score-ring { width:96px; height:96px; border-radius:50%; display:flex; flex-direction:column; align-items:center; justify-content:center; background:conic-gradient(var(--green) var(--score), #e2e7e3 0); position:relative; }
.score-ring:after { content:''; position:absolute; inset:8px; border-radius:50%; background:white; }
.score-ring strong,.score-ring span { position:relative; z-index:1; }
.score-ring strong { font:700 1.45rem 'Manrope'; }
.score-ring span { font-size:.65rem; color:var(--muted); }
.score-row { display:grid; grid-template-columns:145px 1fr 35px; gap:10px; align-items:center; margin:9px 0; font-size:.8rem; }
.score-track { height:6px; background:#e7ebe8; border-radius:9px; overflow:hidden; }
.score-fill { height:100%; background:var(--green); border-radius:9px; }
.callout { border-radius:12px; padding:14px 16px; margin:8px 0; }
.callout-bull { background:#e5f1eb; border:1px solid #c9e3d7; }
.callout-bear { background:#f5e6e1; border:1px solid #ecd0c7; }
.claim-card { background:white; border:1px solid var(--line); border-radius:12px; padding:16px 18px; margin:10px 0; }
.claim-type { color:var(--green); font-size:.68rem; font-weight:700; text-transform:uppercase; letter-spacing:.1em; }
.quote { color:#4f5c55; border-left:2px solid #9bcab5; margin-top:10px; padding-left:13px; font-size:.86rem; }
.funnel { display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin:14px 0 22px; }
.funnel-step { background:white; border:1px solid var(--line); padding:16px; border-radius:12px; position:relative; }
.funnel-step:not(:last-child):after { content:'›'; position:absolute; right:-9px; top:27px; z-index:2; color:#88a096; font-size:1.4rem; }
.funnel-step strong { font:700 1.5rem 'Manrope'; display:block; }
.funnel-step span { color:var(--muted); font-size:.76rem; }
.pipeline { display:grid; grid-template-columns:repeat(7,1fr); gap:8px; margin:14px 0 10px; }
.pipeline-step { background:#152b3a; color:#f5f4ef; border-radius:10px; padding:14px 12px; min-height:82px; }
.pipeline-step strong { display:block; font-size:.78rem; margin-top:8px; }
.pipeline-step span { color:#79d0a8; font:700 .65rem 'Manrope'; letter-spacing:.08em; }
.control-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin:14px 0 18px; }
.control-card { background:#edf3ef; border:1px solid #d4e2d9; border-radius:12px; padding:16px; }
.control-card strong { display:block; margin:5px 0 4px; }
.control-card span { color:var(--muted); font-size:.78rem; line-height:1.4; }
.story-number { color:#79d0a8; font:700 .76rem 'Manrope'; letter-spacing:.12em; }
.story-block { border-top:1px solid var(--line); padding:28px 0 22px; }
.source-chip { display:inline-block; padding:5px 8px; border:1px solid var(--line); background:white; border-radius:7px; margin:3px 4px 3px 0; color:#516058; font-size:.72rem; }
.density-table { font-size:.82rem; }
div[data-baseweb="tab-list"] { gap:20px; }
button[data-baseweb="tab"] { background:transparent; border:0; padding:0 0 9px; }
button[data-baseweb="tab"] p { color:#68746e !important; }
button[data-baseweb="tab"][aria-selected="true"] p { color:#0c6b4e !important; font-weight:700; }
div[data-baseweb="tab-highlight"] { background-color:#0c6b4e !important; }
.stDataFrame { border:1px solid var(--line); border-radius:12px; overflow:hidden; }
@media (max-width: 900px) { .funnel,.control-grid { grid-template-columns:repeat(2,1fr); } .pipeline { grid-template-columns:repeat(2,1fr); } .score-row{grid-template-columns:110px 1fr 30px;} }
</style>
"""


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def money(value: object) -> str:
    try:
        if pd.isna(value):
            return "—"
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def pct(value: object, *, already_percent: bool = False) -> str:
    try:
        if pd.isna(value):
            return "—"
        number = float(value) if already_percent else float(value) * 100
        return f"{number:+.1f}%"
    except (TypeError, ValueError):
        return "—"


def pill(status: object) -> str:
    base = str(status or "Unrated")
    cls = base.lower() if base.lower() in {"watch", "investigate", "reject"} else "neutral"
    label = f"{base} · provisional" if base != "Unrated" else base
    return f'<span class="pill pill-{cls}">{esc(label)}</span>'


def html_block(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def research_markdown(value: object, fallback: str = "_Not available._") -> None:
    """Render stored research prose without Streamlit treating dollars as math."""
    text = str(value) if value else fallback
    st.markdown(text.replace("$", "\\$"))


def concept_note(title: str, copy: str) -> None:
    html_block(f'<div class="concept-note"><strong>{esc(title)}</strong> — {esc(copy)}</div>')


def company_row(data: dict, ticker: str) -> pd.Series:
    matches = data["universe"][data["universe"]["ticker"] == ticker]
    return matches.iloc[0] if not matches.empty else pd.Series(dtype=object)


def score_bars(row: pd.Series) -> None:
    labels = [
        ("Business quality", "business_quality"),
        ("Competitive moat", "competitive_moat"),
        ("Financial strength", "financial_strength"),
        ("Management", "management"),
        ("Valuation", "valuation"),
        ("Risk resilience", "risk"),
    ]
    bits = []
    for label, key in labels:
        value = float(row.get(key, 0) or 0)
        bits.append(
            f'<div class="score-row"><span>{label}</span><div class="score-track">'
            f'<div class="score-fill" style="width:{max(0, min(100, value))}%"></div></div>'
            f'<strong>{value:.0f}</strong></div>'
        )
    html_block("".join(bits))


def valuation_cards(row: pd.Series) -> None:
    cols = st.columns(4)
    items = [
        ("Market price", money(row.get("current_price")), "Latest stored close"),
        ("Bear value", money(row.get("dcf_bear")), "Conservative case"),
        ("Base value", money(row.get("dcf_base")), "Central case"),
        ("Bull value", money(row.get("dcf_bull")), "Optimistic case"),
    ]
    for col, (label, value, sub) in zip(cols, items):
        with col:
            html_block(
                f'<div class="card"><div class="card-label">{label}</div>'
                f'<div class="card-value">{value}</div><div class="card-sub">{sub}</div></div>'
            )


def render_header(data: dict, prototype: str) -> None:
    refreshed = data.get("refreshed") or "unknown"
    try:
        refreshed = datetime.fromisoformat(refreshed).strftime("%d %b %Y")
    except (TypeError, ValueError):
        pass
    html_block(
        f'<div class="topbar"><div><span class="eyebrow">UI concept gallery</span></div>'
        f'<div class="meta">{esc(prototype)} · data through {esc(refreshed)}</div></div>'
    )


def render_decision_desk(data: dict, detail: dict, ticker: str) -> None:
    render_header(data, "Decision desk")
    concept_note(
        "Decision-first cockpit",
        "For a portfolio manager who starts with the ranked queue, then asks why a name deserves time.",
    )
    row = company_row(data, ticker)
    html_block(
        '<div class="hero"><span class="eyebrow" style="color:#79d0a8">Today’s research queue</span>'
        '<h1>What deserves attention now?</h1><p>A compact decision surface: pipeline health, '
        'ranked opportunities, valuation tension, and evidence confidence in one scan.</p></div>'
    )
    f = data["funnel"]
    cols = st.columns(4)
    for col, item in zip(
        cols,
        [
            ("Passed screen", f["passed"], f'{f["screened"]} assessed'),
            ("Committee-ready", f["briefed"], f'{f["passed"] - f["briefed"]} awaiting / excluded'),
            ("Watch", f["watch"], "Needs a catalyst or price"),
            ("Reject", f["reject"], "Fails current evidence"),
        ],
    ):
        with col:
            html_block(
                f'<div class="card"><div class="card-label">{esc(item[0])}</div>'
                f'<div class="card-value">{item[1]}</div><div class="card-sub">{esc(item[2])}</div></div>'
            )

    st.markdown("### Ranked research queue")
    queue = data["committee"].copy().head(15)
    queue["margin_of_safety"] = queue["margin_of_safety_pct"].map(pct)
    queue = queue[
        ["ticker", "name", "sector", "status", "overall_score", "quality_score", "margin_of_safety", "data_confidence"]
    ].rename(
        columns={
            "ticker": "Ticker", "name": "Company", "sector": "Sector", "status": "Call",
            "overall_score": "Committee", "quality_score": "Quality",
            "margin_of_safety": "Bear MoS", "data_confidence": "Confidence",
        }
    )
    st.dataframe(
        queue,
        hide_index=True,
        width="stretch",
        height=400,
        column_config={
            "Committee": st.column_config.ProgressColumn("Committee", min_value=0, max_value=100, format="%.1f"),
            "Quality": st.column_config.ProgressColumn("Quality", min_value=0, max_value=100, format="%.1f"),
        },
    )

    st.markdown("### Selected decision")
    left, middle, right = st.columns([1.15, 1.8, 1.15])
    with left:
        html_block(
            f'<div class="card"><div class="company-title"><div class="ticker-mark">{esc(ticker)}</div>'
            f'<div><strong>{esc(row.get("name"))}</strong><div class="card-sub">{esc(row.get("sector"))}</div></div></div>'
            f'<div style="display:flex;align-items:center;justify-content:space-between;margin-top:18px">'
            f'{pill(row.get("status"))}<div class="score-ring" style="--score:{float(row.get("overall_score",0) or 0)}%">'
            f'<strong>{float(row.get("overall_score",0) or 0):.1f}</strong><span>of 100</span></div></div></div>'
        )
    with middle:
        html_block('<div class="card"><div class="card-label">Committee thesis</div>')
        research_markdown(detail["verdict"].get("investment_thesis"), "_No thesis available._")
        html_block("</div>")
    with right:
        html_block('<div class="card"><div class="card-label">Decision tension</div>')
        st.metric("Bear margin of safety", pct(row.get("margin_of_safety_pct")))
        st.caption(
            f"Bear severity: {detail['verdict'].get('bear_case_severity', '—')} · "
            f"Evidence: {detail['verdict'].get('data_confidence', '—')}"
        )
        html_block("</div>")


def render_workbench(data: dict, detail: dict, ticker: str) -> None:
    render_header(data, "Company workbench")
    concept_note(
        "Company-first workspace",
        "For an analyst spending 10–30 minutes on one name; this is the strongest candidate for the core redesign.",
    )
    row = company_row(data, ticker)
    company = detail["company"]
    left, right = st.columns([5, 1])
    with left:
        html_block(
            f'<div class="company-title"><div class="ticker-mark">{esc(ticker)}</div><div>'
            f'<h1 style="margin:0">{esc(company.get("name"))}</h1>'
            f'<div class="meta">{esc(company.get("sector") or "No sector")} · {esc(company.get("industry") or "Industry unavailable")} '
            f'· {esc(company.get("universe"))}</div></div></div>'
        )
    with right:
        html_block(
            f'<div style="text-align:right;padding-top:8px">{pill(row.get("status"))}'
            f'<div class="card-sub" style="margin-top:7px">Committee {row.get("overall_score", "—")}/100</div></div>'
        )

    valuation_cards(row)
    tabs = st.tabs(["Decision brief", "Quality", "Valuation", "Risks & monitoring", "Sources"])
    with tabs[0]:
        left, right = st.columns([1.8, 1])
        with left:
            st.markdown("### Investment thesis")
            research_markdown(detail["verdict"].get("investment_thesis"))
            st.markdown("### Committee conclusion")
            research_markdown(detail["verdict"].get("ai_conclusion"))
        with right:
            st.markdown("### Scorecard")
            score_bars(row)
            st.caption(
                "These are model-assisted committee components. Status cut-offs are provisional pending benchmark validation."
            )
    with tabs[1]:
        st.markdown("### Eight-metric quality screen")
        metrics = detail["metrics"].copy()
        if metrics.empty:
            st.info("No metric detail available.")
        else:
            metrics["sector_percentile"] = metrics["sector_percentile"].round(1)
            st.dataframe(
                metrics[["metric", "status", "value", "sector_percentile"]],
                hide_index=True,
                width="stretch",
                column_config={
                    "sector_percentile": st.column_config.ProgressColumn(
                        "Sector percentile", min_value=0, max_value=100, format="%.1f"
                    )
                },
            )
    with tabs[2]:
        st.markdown("### Price against intrinsic-value scenarios")
        valuation_cards(row)
        st.caption(
            "Margin of safety is calculated against the bear scenario. A negative bear value is deliberately shown as non-investable, never as a misleading percentage."
        )
        if not detail["valuation"].empty:
            with st.expander("Model assumptions"):
                for _, item in detail["valuation"].iterrows():
                    label = item["method"] + (f" · {item['scenario']}" if item["scenario"] else "")
                    st.markdown(f"**{label}**")
                    try:
                        st.json(json.loads(item["key_assumptions"] or "{}"))
                    except json.JSONDecodeError:
                        st.code(item["key_assumptions"] or "{}")
    with tabs[3]:
        st.markdown("### What would change the view?")
        if detail["monitor"]:
            for index, item in enumerate(detail["monitor"], 1):
                html_block(
                    f'<div class="claim-card"><span class="claim-type">Monitor {index:02d}</span>'
                    f'<div style="margin-top:6px">{esc(item)}</div></div>'
                )
        else:
            st.info("No monitoring items available.")
    with tabs[4]:
        render_source_register(detail)


def render_opportunity_map(data: dict, detail: dict, ticker: str) -> None:
    render_header(data, "Opportunity map")
    concept_note(
        "Screen-first explorer",
        "For idea generation and portfolio triage; patterns and outliers lead, individual briefs follow.",
    )
    html_block(
        '<div class="hero"><span class="eyebrow" style="color:#79d0a8">Universe intelligence</span>'
        '<h1>Find quality the market has not priced kindly</h1><p>Map the screened universe by '
        'business quality, committee conviction, sector, and valuation—without pretending one score is the answer.</p></div>'
    )
    chart = data["committee"].copy()
    chart["valuation_signal"] = chart["valuation"].fillna(1).clip(lower=1)
    chart["Status"] = chart["status"].fillna("Unrated")
    st.scatter_chart(
        chart,
        x="quality_score",
        y="overall_score",
        color="Status",
        size="valuation_signal",
        height=450,
        x_label="Quant quality score",
        y_label="Committee score",
    )
    st.caption("Bubble size = valuation component score. Use this as an attention map, not a buy list.")

    left, right = st.columns([1.15, 1.85])
    with left:
        st.markdown("### Sector screen hit-rate")
        sector = data["sector"].set_index("sector")
        st.bar_chart(sector[["pass_rate"]], horizontal=True, height=390)
    with right:
        st.markdown("### Highest-conviction outliers")
        shortlist = data["committee"].copy().head(12)
        shortlist["Bear MoS"] = shortlist["margin_of_safety_pct"].map(pct)
        st.dataframe(
            shortlist[["ticker", "name", "sector", "overall_score", "quality_score", "Bear MoS", "status"]],
            hide_index=True,
            width="stretch",
            height=390,
            column_config={
                "overall_score": st.column_config.ProgressColumn("Committee", min_value=0, max_value=100),
                "quality_score": st.column_config.ProgressColumn("Quality", min_value=0, max_value=100),
            },
        )

    row = company_row(data, ticker)
    st.markdown(f"### Why {ticker} is on the map")
    valuation_cards(row)


def render_source_register(detail: dict) -> None:
    filings = detail["filings"]
    if filings.empty:
        st.info("No cited source filings available.")
        return
    for _, filing in filings.iterrows():
        label = f"{filing['form_type']} · {filing['filing_date']} · {filing['accession_number']}"
        if filing["document_url"]:
            st.markdown(f"- [{label} ↗]({filing['document_url']})")
        else:
            st.markdown(f"- {label}")


def render_evidence_room(data: dict, detail: dict, ticker: str) -> None:
    render_header(data, "Evidence room")
    concept_note(
        "Trust-first evidence ledger",
        "For diligence, challenge, and audit: claims stay visibly attached to the original filing language.",
    )
    row = company_row(data, ticker)
    claims = detail["claims"]
    cited = int(claims["quote"].notna().sum()) if not claims.empty else 0
    asserted = int((claims["assertion_status"] == "asserted").sum()) if not claims.empty else 0
    sources = int(claims["accession_number"].nunique()) if not claims.empty else 0

    html_block(
        f'<div class="hero"><span class="eyebrow" style="color:#79d0a8">{esc(ticker)} · audit view</span>'
        f'<h1>Can the conclusion survive scrutiny?</h1><p>{esc(detail["company"].get("name"))} is '
        f'{esc(str(row.get("status", "unrated")).lower())}. This view makes the support—not the prose—the primary object.</p></div>'
    )
    cols = st.columns(4)
    for col, item in zip(
        cols,
        [
            ("Claims", len(claims), "Across analysis lenses"),
            ("Asserted", asserted, "Evidence-bearing statements"),
            ("Cited", cited, "Resolved source quotes"),
            ("Source filings", sources, "Distinct SEC accessions"),
        ],
    ):
        with col:
            html_block(
                f'<div class="card"><div class="card-label">{item[0]}</div><div class="card-value">{item[1]}</div>'
                f'<div class="card-sub">{item[2]}</div></div>'
            )

    left, right = st.columns([2.1, 1])
    with left:
        st.markdown("### Claim ledger")
        if claims.empty:
            st.info("No claim ledger available for this company.")
        else:
            for _, claim in claims.head(18).iterrows():
                source = "No resolved citation"
                if claim.get("accession_number"):
                    source = f"{claim.get('form_type') or 'Filing'} · {claim.get('section_id')} · {claim.get('filing_date')}"
                html_block(
                    f'<div class="claim-card"><span class="claim-type">{esc(claim.get("analysis_type"))}</span>'
                    f'<div style="margin-top:6px;font-weight:600">{esc(claim.get("claim_text"))}</div>'
                    + (f'<div class="quote">“{esc(claim.get("quote"))}”</div>' if claim.get("quote") else '')
                    + f'<div class="card-sub" style="margin-top:10px">{esc(source)}</div></div>'
                )
    with right:
        st.markdown("### Audit context")
        html_block(
            f'<div class="card"><div class="card-label">Committee call</div>'
            f'<div style="margin:8px 0">{pill(row.get("status"))}</div>'
            f'<div class="card-sub">Data confidence</div><strong>{esc(row.get("data_confidence") or "—")}</strong>'
            f'<div class="card-sub" style="margin-top:12px">Bear-case severity</div>'
            f'<strong>{esc(row.get("bear_case_severity") or "—")}</strong></div>'
        )
        st.markdown("### Filing register")
        render_source_register(detail)
        st.caption("A citation proves traceability, not entailment. The analyst still judges whether the quote supports the claim.")


def render_storyboard(data: dict, detail: dict, ticker: str) -> None:
    render_header(data, "Demo storyboard")
    concept_note(
        "Institutional product-build narrative",
        "Demonstrate system design, control choices, and a working analyst experience—not a stock pitch.",
    )
    f = data["funnel"]
    row = company_row(data, ticker)
    html_block(
        '<div class="hero" style="padding:42px 42px"><span class="story-number">PROJECT MOAT / 01</span>'
        '<h1 style="font-size:2.75rem!important;max-width:900px">An institutional research workflow, built to show its working.</h1>'
        '<p>Project Moat turns primary filings and market data into a governed research queue—without hiding uncertainty, missing data, or the human decision.</p></div>'
    )

    html_block('<div class="story-block"><span class="story-number">02 / THE PLATFORM</span><h2>One pipeline, explicit hand-offs</h2></div>')
    html_block(
        '<div class="pipeline">'
        + "".join(
            f'<div class="pipeline-step"><span>{index:02d}</span><strong>{label}</strong></div>'
            for index, label in enumerate(
                ["Ingest", "Screen", "Quality", "AI analysis", "Valuation", "Committee", "Human decision"], 1
            )
        )
        + "</div>"
    )
    st.caption("Deterministic data and scoring stages stay separate from model-assisted interpretation; the final action remains human-owned.")

    html_block('<div class="story-block"><span class="story-number">03 / THE CONTROLS</span><h2>Trust is a product feature, not a disclaimer</h2></div>')
    html_block(
        '<div class="control-grid">'
        '<div class="control-card"><div class="card-label">Provenance</div><strong>Claim → quote → filing</strong><span>Research statements remain traceable to accession, section, and original language.</span></div>'
        '<div class="control-card"><div class="card-label">Data gaps</div><strong>Unavailable ≠ failure</strong><span>Unknown inputs are surfaced and excluded rather than silently converted into weak scores.</span></div>'
        '<div class="control-card"><div class="card-label">Valuation safety</div><strong>Bear case first</strong><span>Margin of safety uses the conservative scenario, with explicit guards for invalid negative values.</span></div>'
        '<div class="control-card"><div class="card-label">Governance</div><strong>Provisional by design</strong><span>Status thresholds stay visible but labelled provisional until external benchmarking validates them.</span></div>'
        '</div>'
    )

    html_block('<div class="story-block"><span class="story-number">04 / THE FUNNEL</span><h2>Compression without hidden judgment</h2></div>')
    html_block(
        '<div class="funnel">'
        + "".join(
            f'<div class="funnel-step"><strong>{value}</strong><span>{label}</span></div>'
            for label, value in [
                ("Active universe", f["universe"]),
                ("Quant screened", f["screened"]),
                ("Passed quality", f["passed"]),
                ("Committee briefs", f["briefed"]),
            ]
        )
        + "</div>"
    )
    st.caption("Unavailable inputs are excluded, not silently scored as failures. Sector-relative checks avoid comparing banks with software companies.")

    html_block(
        f'<div class="story-block"><span class="story-number">05 / THE PRODUCT IN USE</span>'
        f'<h2>{esc(ticker)} · one decision, with the detail preserved</h2></div>'
    )
    left, right = st.columns([1.7, 1])
    with left:
        research_markdown(detail["verdict"].get("investment_thesis"), "_Thesis unavailable._")
    with right:
        html_block(
            f'<div class="card" style="text-align:center">{pill(row.get("status"))}'
            f'<div class="card-value" style="font-size:2.5rem;margin-top:14px">{float(row.get("overall_score",0) or 0):.1f}</div>'
            f'<div class="card-sub">committee score / 100</div><hr style="border:0;border-top:1px solid #dfe3dd;margin:16px 0">'
            f'<div class="card-label">Bear margin of safety</div><strong>{pct(row.get("margin_of_safety_pct"))}</strong></div>'
        )

    html_block('<div class="story-block"><span class="story-number">06 / THE ANALYST EXPERIENCE</span><h2>Quality and price can disagree</h2></div>')
    valuation_cards(row)
    bull, bear = st.columns(2)
    with bull:
        html_block('<div class="callout callout-bull"><div class="card-label">What works</div>')
        research_markdown(detail["verdict"].get("quality_analyst_view"))
        html_block("</div>")
    with bear:
        html_block('<div class="callout callout-bear"><div class="card-label">What breaks</div>')
        research_markdown(detail["verdict"].get("bear_analyst_view"))
        html_block("</div>")

    html_block('<div class="story-block"><span class="story-number">07 / THE AUDIT TRAIL</span><h2>Every important claim can be challenged at source</h2></div>')
    claims = detail["claims"]
    if claims.empty:
        st.info("No claims available.")
    else:
        for _, claim in claims[claims["quote"].notna()].head(3).iterrows():
            html_block(
                f'<div class="claim-card"><span class="claim-type">{esc(claim.get("analysis_type"))}</span>'
                f'<div style="margin-top:6px;font-weight:600">{esc(claim.get("claim_text"))}</div>'
                f'<div class="quote">“{esc(claim.get("quote"))}”</div>'
                f'<div class="card-sub" style="margin-top:8px">{esc(claim.get("form_type"))} · '
                f'{esc(claim.get("filing_date"))} · {esc(claim.get("section_id"))}</div></div>'
            )
    st.info("Built as a product system: broad discovery · structured judgment · visible uncertainty · source-level challenge · human decision")


html_block(COMMON_CSS)
with st.sidebar:
    html_block('<div class="moat-brand">Project <span>Moat</span></div><div class="sidebar-kicker">Research interface lab</div>')
    prototype = st.radio(
        "Choose a direction",
        [
            "01 · Decision desk",
            "02 · Company workbench",
            "03 · Opportunity map",
            "04 · Evidence room",
            "05 · Demo storyboard",
        ],
        index=4,
        label_visibility="collapsed",
    )
    st.divider()

conn = get_connection()
data = load_gallery_data(conn)
if data["committee"].empty:
    st.error("No committee data is available. Run the pipeline before opening the prototype gallery.")
    conn.close()
    st.stop()

with st.sidebar:
    options = data["committee"]["ticker"].tolist()
    ticker = st.selectbox("Company in focus", options, index=0)
    st.caption("All concepts use the same live data. Change the company once and compare it across directions.")
    st.caption(
        f"Committee dataset: {data['funnel']['briefed']} verdicts · "
        f"run `{data['runs']['committee']}`"
    )
    st.divider()
    st.caption("Prototype only · production dashboard unchanged")

detail = load_company_detail(conn, ticker, data)

if prototype.startswith("01"):
    render_decision_desk(data, detail, ticker)
elif prototype.startswith("02"):
    render_workbench(data, detail, ticker)
elif prototype.startswith("03"):
    render_opportunity_map(data, detail, ticker)
elif prototype.startswith("04"):
    render_evidence_room(data, detail, ticker)
else:
    render_storyboard(data, detail, ticker)

conn.close()
