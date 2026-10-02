"""Sprint 6.0: second committee worker, Z->A, alongside main run
20260930T123721Z (A->Z). Stops at the first ticker the main run has already
verdicted; the main run then cache-hits everything this one persisted ($0)."""
import sys
from pathlib import Path
sys.path.insert(0, "/Users/pete/moat-sprint-6.0"); sys.path.insert(0, "/Users/pete/moat-sprint-6.0/scripts")
import anthropic
from moat import config
from moat.committee.caller import CLIENT_TIMEOUT
from moat.committee.committee import run_committee
from moat.db.connection import get_connection
import run_pipeline as rp
MAIN, QUALITY, VALUATION, CAP = "20260930T133528Z", "20260929T135425Z", "20260929T135455Z", 2.50
conn = get_connection(Path("/Users/pete/moat/data/moat.db"))
ai = {r[0] for r in conn.execute("SELECT DISTINCT ticker FROM ai_analysis WHERE is_current = 1")}
val = {r[0] for r in conn.execute("SELECT DISTINCT ticker FROM valuations WHERE run_id = ?", (VALUATION,))}
tickers = sorted((ai & val) - config.COMMITTEE_KNOWN_EXCLUDED_TICKERS, reverse=True)
client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, timeout=CLIENT_TIMEOUT)
run_id = rp.new_run_id(); rp.start_run(conn, run_id)
print("run_id", run_id, "reverse over", len(tickers), flush=True)
cost, errors = 0.0, 0
try:
    for t in tickers:
        if conn.execute("SELECT 1 FROM committee_verdicts WHERE run_id=? AND ticker=?", (MAIN, t)).fetchone():
            print(f"    met main run at {t} — stopping", flush=True); break
        if cost >= CAP or errors >= 3:
            print(f"    halting (cost ${cost:.2f}, consecutive errors {errors})", flush=True); break
        r = run_committee(t, run_id, VALUATION, QUALITY, conn, client, cost_cap_remaining=CAP - cost)
        cost += r.get("cost_estimate") or 0.0
        errors = errors + 1 if r["outcome"] == "api_error" and r.get("transient") else 0
        print(f"    {t}: {r['outcome']} (${cost:.3f} cumulative)"
              + (f" -> {r['status']} ({r['overall_score']:.1f})" if r.get("status") else "")
              + (f"  [{r.get('reason')}]" if r.get("reason") else ""), flush=True)
    rp.complete_run(conn, run_id, "committee", "partial")
except BaseException:
    rp.complete_run(conn, run_id, "none", "failed"); raise
print(f"DONE reverse worker, spend ${cost:.2f}", flush=True)
