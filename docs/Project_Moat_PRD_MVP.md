# Project Moat

**AI Fundamental Equity Research Analyst — MVP PRD**

Status: Draft for review | Date: 10 August 2026 | Scope: Personal research tool | Initial universe: US + UK listed equities

> **Provenance note:** This Markdown file was generated from the original PDF
> ([Project_Moat_PRD_MVP.pdf](Project_Moat_PRD_MVP.pdf)) on 01 Oct 2026 to make
> it accessible to a general audience on GitHub. The PDF is retained unchanged
> for audit purposes as the original source document from which the project
> started. Where the two differ, the PDF is authoritative for the original
> wording; [PRD_ADDENDUM.md](PRD_ADDENDUM.md) records every scoping decision
> made since.

## Product vision

Build an AI-assisted equity research system based on Buffett/Graham-style
fundamental investing principles. It continuously screens listed companies for
high-quality businesses, durable competitive advantages, strong financials and
attractive valuations, then produces concise, evidence-based investment briefs
for human review.

## MVP objective

> Identify financially exceptional businesses trading at potentially attractive
> valuations and produce a defensible investment thesis including bull case,
> bear case and intrinsic-value ranges.

## 1. Product principles

- **Business before stock** — analyse the underlying company.
- **Quality before valuation** — avoid simply finding statistically cheap businesses.
- **Margin of safety** — valuation must allow for analytical error.
- **Evidence over AI opinion** — conclusions should trace to financial data and filings.
- **Conservative assumptions** — intrinsic value is a range, not false precision.
- **Challenge every thesis** — every candidate receives a bear-case analysis.

## 2. MVP market universe

In scope: S&P 500, NASDAQ 100 and FTSE 350. Out of scope initially: small caps,
ETFs, private companies and markets outside the US/UK. The aim is to keep the
data problem manageable while testing the workflow.

## 3. Core MVP workflow

> Market data → deterministic financial screen → quality score → AI business
> analysis → bull/bear analysis → valuation engine → margin of safety →
> investment rank → human review.

## 4. Quantitative screening

| Metric | Initial criterion |
| --- | --- |
| ROIC | >15% |
| ROE | >15% |
| Free cash flow | Positive; preferably 5–10 year history |
| Revenue / EPS growth | Positive trend |
| Operating margin | >15% |
| Debt | Sensible relative to cash flow |
| Share dilution | Low |
| Gross margin | Stable or improving |

## 5. AI business analysis

- **Business quality:** business model, revenue characteristics, recurring
  revenue, pricing power, customer concentration and capital intensity.
- **Competitive moat:** network effects, brand, switching costs, cost
  advantage, scale, regulation, IP/data advantage.
- **Management:** capital allocation, acquisitions, buybacks, dividends,
  incentives and communication quality.
- **Risks:** competition, regulation, disruption, cyclicality, leverage and
  customer concentration.

## 6. Valuation engine

Primary method: DCF / Owner Earnings. Supporting methods: FCF yield, EV/EBIT,
P/E and historical valuation ranges. Output should show bear/base/bull
scenarios, key assumptions, intrinsic-value range, current price and margin of
safety.

## 7. Investment Committee

Three perspectives:

- **Quality Analyst** — is this an exceptional business?
- **Bear Analyst** — why could the thesis be wrong?
- **Valuation Analyst** — are we paying a sensible price?

A consolidated assessment produces the final research ranking.

## 8. MVP investment score

| Component | Weight |
| --- | --- |
| Business quality | 25% |
| Competitive moat | 20% |
| Financial strength | 15% |
| Management / capital allocation | 10% |
| Valuation | 25% |
| Risk | 5% |

## 9. Primary MVP experience

A ranked dashboard showing Company, Quality, Moat, Valuation, Overall Score and
Status (Investigate / Watch / Reject). Clicking a company opens a one-page
Investment Brief.

## 10. Investment Brief

Company overview; investment thesis; moat evidence; financial quality;
valuation range; margin of safety; bull case; bear case; key things to
monitor; AI conclusion. The AI must explain its conclusion rather than simply
provide a score.

## 11. Monitoring

MVP watchlist alerts: candidate enters top rankings; price crosses valuation
threshold; earnings materially change thesis; major management change;
material financial deterioration. A daily or weekly briefing is sufficient
initially.

## 12. Explicitly out of MVP

Automated trading, broker integration, portfolio optimisation, technical
analysis, options analysis, macro trading signals, social-media sentiment,
cryptocurrency, mobile app, complex autonomous multi-agent infrastructure and
large numbers of paid data sources.

## 13. MVP success criteria

Screen roughly 850 US/UK companies; reduce to ~50–100 quality candidates;
produce automated research briefs for the top ~10–20; refresh financial data
automatically; calculate valuations without manual spreadsheet work; and, most
importantly, produce research that is genuinely useful enough to replace a
significant portion of manual initial analysis.

## 14. Guiding product decision

> Build a deterministic fundamental screener + AI research analyst +
> transparent valuation engine. The numbers determine which companies deserve
> attention; AI explains the business and challenges the thesis; the valuation
> engine determines what price may be attractive; the human remains the
> investment committee.

## 15. MVP roadmap

- **Sprint 1:** data foundation and company universe.
- **Sprint 2:** quantitative screener and ranking dashboard.
- **Sprint 3:** AI research, moat and management analysis.
- **Sprint 4:** DCF / Owner Earnings and scenario valuation.
- **Sprint 5:** investment committee and research briefs.
- **Sprint 6:** watchlist and monitoring.
