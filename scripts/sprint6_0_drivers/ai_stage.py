"""Sprint 6.0: ai_analysis stage only. argv[1] = 'dry' or 'batch'."""
import sys
from pathlib import Path
sys.path.insert(0, "/Users/pete/moat-sprint-6.0"); sys.path.insert(0, "/Users/pete/moat-sprint-6.0/scripts")
from moat import config
from moat.db.connection import get_connection
import run_pipeline as rp
dry = sys.argv[1] == "dry"
conn = get_connection(Path("/Users/pete/moat/data/moat.db"))
run_id = rp.new_run_id(); rp.start_run(conn, run_id); print("run_id", run_id, "dry" if dry else "batch", flush=True)
try:
    rp.run_ai_analysis_stage(conn, run_id, dry_run=dry, batch=not dry, cost_cap_usd=10.0,
                             exclude_tickers=set(config.AI_ANALYSIS_KNOWN_EXCLUDED_TICKERS))
    rp.complete_run(conn, run_id, "ai_analysis", "partial")
except BaseException:
    rp.complete_run(conn, run_id, "none", "failed"); raise
