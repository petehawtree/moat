"""Wait for batch msgbatch_01AD2bSHucP1Vc29SrsACDv3 to end, then retrieve +
persist into run 20260930T093157Z. No new submissions."""
import sys, time
from pathlib import Path
sys.path.insert(0, "/Users/pete/moat-sprint-6.0"); sys.path.insert(0, "/Users/pete/moat-sprint-6.0/scripts")
from moat import config
from moat.db.connection import get_connection
import anthropic, run_pipeline as rp
BATCH, RUN = "msgbatch_01AD2bSHucP1Vc29SrsACDv3", "20260930T093157Z"
client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
while True:
    try:
        b = client.messages.batches.retrieve(BATCH)
        print(time.strftime("%H:%M:%S"), b.processing_status, b.request_counts, flush=True)
        if b.processing_status == "ended":
            break
    except Exception as e:
        print("status check failed:", e, flush=True)
    time.sleep(300)
conn = get_connection(Path("/Users/pete/moat/data/moat.db"))
rp.run_ai_analysis_stage(conn, RUN, retrieve_batch_id=BATCH)
rp.complete_run(conn, RUN, "ai_analysis", "partial")
print("RETRIEVED", flush=True)
