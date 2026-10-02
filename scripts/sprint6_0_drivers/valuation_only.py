"""Sprint 6.0: screen + quality only (no API calls), closed as 'partial' so
the valuation run that follows reads THIS run's pass set."""
import sys
from pathlib import Path
sys.path.insert(0, "/Users/pete/moat-sprint-6.0"); sys.path.insert(0, "/Users/pete/moat-sprint-6.0/scripts")
from moat.db.connection import get_connection
import run_pipeline as rp
conn = get_connection(Path("/Users/pete/moat/data/moat.db"))
run_id = rp.new_run_id(); rp.start_run(conn, run_id); print("run_id", run_id, flush=True)
try:
    rp.run_valuation_stage(conn, run_id)
    rp.complete_run(conn, run_id, "valuation", "partial")
except BaseException:
    rp.complete_run(conn, run_id, "none", "failed"); raise
