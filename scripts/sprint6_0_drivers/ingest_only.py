"""Sprint 6.0 Phase 1 re-ingest: the ingest stage ONLY, against the shared DB,
using sprint-6.0 worktree code. Stops before screen, so no API spend."""
import sys
from pathlib import Path
sys.path.insert(0, "/Users/pete/moat-sprint-6.0")
sys.path.insert(0, "/Users/pete/moat-sprint-6.0/scripts")
from moat.db.connection import get_connection, init_db
import run_pipeline as rp

DB = Path("/Users/pete/moat/data/moat.db")
print("migrated:", init_db(DB))
conn = get_connection(DB)
run_id = rp.new_run_id()
rp.start_run(conn, run_id)
print("run_id", run_id, flush=True)
try:
    tickers = [r["ticker"] for r in conn.execute("SELECT ticker FROM companies WHERE is_active = 1")]
    rp.run_ingest_stage(conn, tickers, None)
    rp.complete_run(conn, run_id, "ingest", "complete")
except BaseException:
    rp.complete_run(conn, run_id, "none", "failed")
    raise
