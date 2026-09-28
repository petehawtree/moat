#!/usr/bin/env python3
"""Normalise a Morningstar screener export into `tier-a-screener.jsonl`.

The external benchmark described in `docs/evals/morningstar-capture-checklist.md`
arrives as one xlsx per watchlist stratum (Morningstar caps an export at 125
rows, so the 518-name universe is split into seven lists). This turns those
exports into one append-only JSONL record per company, which is what the eval
run actually scores against.

    export MOAT_BENCHMARK_DIR=~/.moat-private/benchmark/v1
    python scripts/benchmark_normalise.py --export project-moat-eval-pass_*.xlsx --stratum pass

**This script reads and writes outside the repo, deliberately.** Morningstar's
research is proprietary: captured values may not be committed here (see the
licensing boundary in the checklist). The script carries field *names* and
mappings, which are Morningstar's published methodology and safe to commit;
it never carries a captured value. `MOAT_BENCHMARK_DIR` must resolve outside
the repo and the script refuses to run if it doesn't.

Two derived fields are added because the raw export can't be trusted as-is:

  price_fair_value_calc  `last_price / fair_value`, recomputed. 29 of 109 rows
                         in the pass export disagree with Morningstar's own
                         `Price/Fair Value` by 0.5-7%, because the two fields
                         are struck at different moments and the export has no
                         as-of column to reconcile them. Six of those flip
                         direction depending on which field you read.
                         `price_fair_value` keeps the reported value; scoring
                         should use the recomputed one and say so.

  coverage_tier          "analyst" or "quantitative". Morningstar fills moat and
                         fair value for quant-covered names but leaves the
                         analyst-only ratings blank, and its quant fair values
                         carry full float precision where an analyst's are
                         rounded. Tier B needs this: a name with no assigned
                         analyst has no bull/bear narrative to score recall
                         against, and counting it as a miss would understate the
                         pipeline.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import openpyxl

REPO_ROOT = Path(__file__).resolve().parent.parent

# Morningstar export header -> our field name. Headers match by PREFIX, so more
# specific names MUST come first: "EBITDA" before "EBIT", and the
# "(Normalized)"/"(Forward)" return variants before their bare forms, or the
# shorter name swallows the longer one.
COLUMNS = {
    "Ticker": "ticker",
    "Name": "company_name",
    "Sector": "ms_sector",
    "Industry Group": "ms_industry_group",
    "Industry": "ms_industry",
    "Economic Moat": "economic_moat",
    "Moat Trend": "moat_trend",
    "Morningstar Rating": "star_rating",
    "Fair Value Uncertainty": "uncertainty",
    "Capital Allocation": "capital_allocation",
    "Fair Value": "fair_value",
    "Price/Fair Value": "price_fair_value",
    "Last Price": "last_price",
    "Market Cap": "market_cap",
    "1-Star Price": "one_star_price",
    "5-Star Price": "five_star_price",
    "Return on Invested Capital (Normalized)": "roic_normalized",
    "Return on Invested Capital": "roic",
    "Return on Equity (Normalized)": "roe_normalized",
    "Return on Equity (Forward)": "roe_forward",
    "Return on Equity": "roe",
    "Revenue": "revenue",
    "Gross Margin": "gross_margin",
    "Operating Margin": "operating_margin",
    "Net Income": "net_income",
    "EBITDA": "ebitda",
    "EBIT": "ebit",
    "Cash Flow from Operations": "operating_cash_flow",
    "Capital Expenditures": "capex",
    "Free Cash Flow": "free_cash_flow",
    "Total Debt": "total_debt",
    "Cash (Balance Sheet)": "cash_and_equiv",
    "Total Equity": "total_equity",
    "Working Capital": "working_capital",
    "Goodwill and Other Intangibles": "goodwill_and_intangibles",
    "Shares Outstanding": "shares_outstanding",
    "Enterprise Value": "enterprise_value",
    "Financial Health Grade": "financial_health_grade",
    "Profitability Grade": "profitability_grade",
    "Growth Grade": "growth_grade",
    "Analyst": "analyst",
    "Report Date": "report_date",
}

STRATA = (
    "pass",
    "unassessable-datagap",
    "fail-boundary",
    "unassessable-definitional",
    "fail-clear",
    "unscreened",
)

# A fair value with more than two decimals is computed, not an analyst's
# rounded estimate. Capital Allocation is an analyst-only rating. Either alone
# is suggestive; the script records which signals fired so the inference stays
# auditable rather than becoming a bare label.
def coverage_tier(row: dict) -> tuple[str, list[str]]:
    signals = []
    fv = row.get("fair_value")
    if isinstance(fv, float) and len(repr(fv).split(".")[-1]) > 2:
        signals.append("unrounded_fair_value")
    if not row.get("capital_allocation"):
        signals.append("no_capital_allocation")
    return ("quantitative" if signals else "analyst"), signals



_BAND_LADDER = {1.5625: "Low", 1.9286: "Medium", 2.5833: "High", 3.5: "Very High"}


def _band_to_uncertainty(width):
    """Which uncertainty the star band implies. Derived from 24 analyst-covered
    companies with zero variance. Quantitatively rated names carry a narrower
    band and fall off this ladder, so OFF_LADDER is the cleanest signal that a
    row is quant-rated rather than analyst-covered."""
    if width is None:
        return None
    for w, label in _BAND_LADDER.items():
        if abs(width - w) < 0.02:
            return label
    return "OFF_LADDER"


def resolve_benchmark_dir() -> Path:
    raw = os.environ.get("MOAT_BENCHMARK_DIR")
    if not raw:
        sys.exit(
            "MOAT_BENCHMARK_DIR is not set. Captured benchmark data lives outside\n"
            "this repo by design — see the licensing boundary in\n"
            "docs/evals/morningstar-capture-checklist.md.\n"
            "    export MOAT_BENCHMARK_DIR=~/.moat-private/benchmark/v1"
        )
    d = Path(os.path.expanduser(raw)).resolve()
    if not d.is_dir():
        sys.exit(f"MOAT_BENCHMARK_DIR does not exist: {d}")
    if d == REPO_ROOT or REPO_ROOT in d.parents:
        sys.exit(
            f"MOAT_BENCHMARK_DIR points inside the repo ({d}).\n"
            "Morningstar data may not be committed here. Point it outside the repo."
        )
    return d


def snapshot_date(path: Path, override: str | None) -> str:
    """Morningstar's export has no as-of column; the filename timestamp is the
    only record of when the prices in it were struck. Parse it, and require an
    explicit --snapshot-date if the filename doesn't carry one, rather than
    silently dating the capture 'today'."""
    if override:
        return override
    for part in path.stem.split("_"):
        try:
            return datetime.strptime(part, "%Y-%m-%d").date().isoformat()
        except ValueError:
            continue
    sys.exit(
        f"No YYYY-MM-DD in filename {path.name}; pass --snapshot-date explicitly.\n"
        "The capture date is not recoverable afterwards — do not guess it."
    )


def read_export(path: Path) -> list[dict]:
    ws = openpyxl.load_workbook(path, data_only=True).active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        sys.exit(f"{path.name} is empty")
    header = [str(h).strip() if h is not None else "" for h in rows[0]]

    mapping, unmapped = {}, []
    for i, h in enumerate(header):
        for prefix, field in COLUMNS.items():
            if h.startswith(prefix):
                mapping[i] = field
                break
        else:
            unmapped.append(h)
    if unmapped:
        print(f"  note: unmapped columns ignored: {unmapped}")
    missing = {"ticker", "economic_moat"} - set(mapping.values())
    if missing:
        sys.exit(f"{path.name} is missing required column(s): {sorted(missing)}")

    out = []
    for raw in rows[1:]:
        rec = {}
        for i, field in mapping.items():
            v = raw[i] if i < len(raw) else None
            if isinstance(v, str):
                v = v.strip() or None
            rec[field] = v
        if rec.get("ticker"):
            out.append(rec)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", required=True, help="xlsx path or glob, relative to MOAT_BENCHMARK_DIR")
    ap.add_argument("--stratum", required=True, choices=STRATA)
    ap.add_argument("--snapshot-date", help="YYYY-MM-DD; only if the filename has none")
    ap.add_argument("--out", default="tier-a-screener.jsonl")
    ap.add_argument("--source", default="morningstar-screener")
    args = ap.parse_args()

    bdir = resolve_benchmark_dir()
    paths = sorted(Path(p) for p in glob.glob(str(bdir / args.export)))
    if not paths:
        sys.exit(f"No export matched {args.export} in {bdir}")

    out_path = bdir / args.out
    # Records are keyed on (ticker, stratum, snapshot). Re-running an export
    # replaces its own rows and leaves the other strata alone, so the seven
    # watchlists can be normalised one at a time as they're captured.
    existing = {}
    if out_path.exists():
        for line in out_path.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                existing[(r["ticker"], r["stratum"], r["snapshot_date"])] = r

    added = replaced = 0
    tiers = {"analyst": 0, "quantitative": 0}
    for path in paths:
        snap = snapshot_date(path, args.snapshot_date)
        rows = read_export(path)
        print(f"{path.name}: {len(rows)} rows, snapshot {snap}, stratum {args.stratum}")
        for rec in rows:
            fv, lp = rec.get("fair_value"), rec.get("last_price")
            rec["price_fair_value_calc"] = (
                round(float(lp) / float(fv), 6) if fv and lp else None
            )
            fs, os_ = rec.get("five_star_price"), rec.get("one_star_price")
            rec["required_margin_of_safety_pct"] = (
                round((1 - float(fs) / float(fv)) * 100, 2) if fv and fs else None
            )
            rec["band_width_x"] = round(float(os_) / float(fs), 4) if fs and os_ else None
            rec["uncertainty_implied_by_band"] = _band_to_uncertainty(rec.get("band_width_x"))
            rec["coverage_tier"], rec["coverage_signals"] = coverage_tier(rec)
            tiers[rec["coverage_tier"]] += 1
            rec.update(
                stratum=args.stratum,
                snapshot_date=snap,
                source=args.source,
                source_file=path.name,
            )
            key = (rec["ticker"], args.stratum, snap)
            if key in existing:
                replaced += 1
            else:
                added += 1
            existing[key] = rec

    ordered = sorted(existing.values(), key=lambda r: (r["stratum"], r["ticker"]))
    with out_path.open("w") as fh:
        for r in ordered:
            fh.write(json.dumps(r, default=str) + "\n")

    print(f"\n{out_path}: {len(ordered)} records total ({added} added, {replaced} replaced)")
    print(f"  coverage: {tiers['analyst']} analyst, {tiers['quantitative']} quantitative")
    by_stratum = {}
    for r in ordered:
        by_stratum[r["stratum"]] = by_stratum.get(r["stratum"], 0) + 1
    for s in STRATA:
        if s in by_stratum:
            print(f"  {s:28} {by_stratum[s]:4}")
    remaining = [s for s in STRATA if s not in by_stratum]
    if remaining:
        print(f"  not yet captured: {', '.join(remaining)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
