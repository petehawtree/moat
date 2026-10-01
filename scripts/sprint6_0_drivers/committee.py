"""Sprint 6.0: committee stage only, default exclusions, $9 cap."""
import sys
from pathlib import Path
sys.path.insert(0, "/Users/pete/moat-sprint-6.0"); sys.path.insert(0, "/Users/pete/moat-sprint-6.0/scripts")
from moat.db.connection import get_connection
import run_pipeline as rp
conn = get_connection(Path("/Users/pete/moat/data/moat.db"))
run_id = rp.new_run_id(); rp.start_run(conn, run_id); print("run_id", run_id, flush=True)
try:
    rp.run_committee_stage(conn, run_id, cost_cap_usd=5.0)
    rp.complete_run(conn, run_id, "committee", "partial")
except BaseException:
    rp.complete_run(conn, run_id, "none", "failed"); raise
