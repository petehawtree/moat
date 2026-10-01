"""Sprint 6.0: sync retry of BR and VRT only (everything else excluded)."""
import sys
from pathlib import Path
sys.path.insert(0, "/Users/pete/moat-sprint-6.0"); sys.path.insert(0, "/Users/pete/moat-sprint-6.0/scripts")
from moat.db.connection import get_connection
import run_pipeline as rp
conn = get_connection(Path("/Users/pete/moat/data/moat.db"))
passers = {r[0] for r in conn.execute("SELECT ticker FROM quality_scores WHERE run_id='20260929T135425Z' AND passed_screen=1")}
run_id = rp.new_run_id(); rp.start_run(conn, run_id); print("run_id", run_id, flush=True)
try:
    rp.run_ai_analysis_stage(conn, run_id, cost_cap_usd=1.0, exclude_tickers=passers - {"BR", "VRT"})
    rp.complete_run(conn, run_id, "ai_analysis", "partial")
except BaseException:
    rp.complete_run(conn, run_id, "none", "failed"); raise
print("cost:", conn.execute("SELECT ticker, outcome, ROUND(cost_estimate,3), substr(failure_reason,1,160) FROM analysis_attempts WHERE run_id=?", (run_id,)).fetchall())
