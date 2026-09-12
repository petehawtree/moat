# LLM read of the 12-company citation sample — Sprint 3.1

**Date:** 2026-09-12
**Sample:** the 12 companies / 48 analyses picked in [`sprint-3-1.md`](../sprints/sprint-3-1.md) ("The human read" — one per GICS sector: META, ORLY, COST, EQT, GILD, VRSK, ADBE, CRH, NEE; plus three `full_fallback` stress picks: NFLX, UNH, BALL).
**Assessed against:** `Project_Moat_PRD_MVP.pdf` and [`PRD_ADDENDUM.md`](../PRD_ADDENDUM.md).
**Verdict:** Findings hold up against the database, with one numeric correction (below). No corrections needed to the qualitative gaps.

## The eval, as given

No sections or citation anchors are missing: all 12 companies have Business Quality, Moat, Management, and Risk analyses. All 397 asserted claims have one CURRENT, resolvable filing citation; none were stale or unresolved. The 40 remaining entries are correctly marked "insufficient evidence," rather than making uncited claims.

**Gaps to address:**

- Management analyses are not labelled "thin." The addendum requires this because they rely only on the latest 10-K, without proxy-statement incentives/governance evidence or multi-year track-record analysis. None of the 12 analyses makes that limitation clear.
- Citation resolution is not citation entailment. The system proves that each quote exists in the cited filing, but not that it fully supports every inference. For example, claims that evidence "high switching costs," a "self-reinforcing loop," or a "difficult-to-replicate" advantage sometimes extend beyond the literal excerpt. This needs a human or entailment-review pass.
- Universe metadata is incomplete: exchange is blank locally for BALL, CRH, EQT, NEE, UNH, and VRSK, weakening auditable confirmation of the addendum's US-universe constraint.
- This validates the Sprint 3 qualitative-analysis and citation requirements, not the full PRD's valuation, margin-of-safety, Investment Brief, or committee workflow.

The intentional evidence limitations are mostly company-specific profitability detail, retention/switching-cost metrics, and management targets. Notable additional gaps: EQT lacks segment FCF/ROIC and catastrophe-insurance quantification; CRH lacks acquisition-integration KPIs and infrastructure sensitivity; NFLX lacks ad-revenue, churn, and WBD-impact detail; UNH lacks segment ROIC; BALL lacks segment margins/ROIC and patent-based moat evidence.

## Review

Checked directly against `data/moat.db` and the addendum text, not taken on faith.

### Coverage: confirmed exactly
All 12 tickers have exactly one current `business_quality`, `moat`, `management`, and `risk` row → 48 `ai_analysis` rows, matching the claim.

### Claim/citation count: one correction needed
**"397 asserted claims" is wrong — 397 is the citation count, not the claim count.** The sample has:

| | count |
|---|---|
| asserted claims | 361 |
| insufficient_evidence claims | 40 |
| **total claims** | **401** |
| citations (across the 361 asserted claims) | **397** |

397 citations land on 361 asserted claims because 24 claims carry 2 citations and 6 carry 3 (331 carry exactly 1). The underlying finding is still correct — every asserted claim has ≥1 citation, every insufficient_evidence claim has 0 — just phrase it as "397 citations across 361 asserted claims," not "397 asserted claims."

Also confirmed: zero insufficient_evidence claims carry a citation, and zero asserted claims carry none — the coverage rule the addendum requires (§A3, §A15.2) holds exactly for this sample.

### "Resolvable" — worth one clarifying note
All 397 citations show `CURRENT` under `scripts/cite.py`'s live check (matching `doc_sha256` + byte-exact offset against the locally cached filing text — `_citation_state()` in `cite.py`). That's a legitimate resolution check, but it's the addendum's rung-1 exact match only. `citation_resolution_events` has **zero rows** for this sample — the 6-rung ladder (`cite.py --reanchor`, §A15.5) was never run against it, because the human-read invocation (`cite.py TICKER`) doesn't trigger it. So "none were stale or unresolved" is accurate for what was actually checked, but it reflects live-offset resolution against the cached filing, not a resolution-ladder sweep. Not a defect — SEC filings don't change post-filing, so rung-1 holding for all 397 is the expected result — but worth stating precisely if this doc is cited later.

### Management "thin" labelling gap: confirmed
Searched all 12 management analyses for language flagging the 10-K-only/no-proxy/no-track-record limitation the addendum requires (§A15.9: "grounded only in the 10-K is thin, and should be labelled as thin"). None does. Two (BALL, UNH) come closest, with per-claim `INSUFFICIENT EVIDENCE` notes on missing capital-allocation criteria and track record — but that's a claim-level gap flag, not the document-level "this whole analysis is thin" framing the addendum calls for. Confirmed gap.

### Entailment gap: correctly scoped
§A15.2 and §A15.9 of the addendum explicitly name entailment ("that a quote supports the claim it is attached to") as unverified and out of scope for Sprint 3 — citation validation checks groundedness, not judgement. The eval's framing matches the addendum's own stated fence exactly.

### Universe metadata gap: confirmed exactly
Queried `companies.exchange` for all 12 tickers — `NULL` for exactly BALL, CRH, EQT, NEE, UNH, VRSK; populated (`NASDAQ`) for META, ORLY, COST, GILD, ADBE, NFLX. Matches the eval's list precisely.

### Company-specific evidence gaps: confirmed exactly
Pulled every `INSUFFICIENT EVIDENCE:` line for EQT, CRH, NFLX, UNH, and BALL. Each named gap in the eval (EQT segment FCF/ROIC + catastrophe-insurance quantification; CRH acquisition-integration KPIs + infrastructure sensitivity; NFLX ad-revenue/churn/WBD-impact detail; UNH segment ROIC; BALL segment margins/ROIC + patent-based moat evidence) has a matching stored claim. No fabricated or missed gaps.

### Scope statement: consistent with the addendum
The addendum's §A15.9 explicitly excludes quantitative-claims joining, valuation, and brief/committee work from Sprint 3; nothing in Sprint 3.1 changes that. The eval's scope disclaimer is accurate.

## Net

Only correction: replace "397 asserted claims" with "397 citations across 361 asserted claims" wherever this eval is quoted elsewhere. Every other finding — coverage completeness, the missing "thin" label, the entailment gap, the six blank-exchange tickers, and the five company-specific evidence gaps — checks out against the database and the addendum text as written.
