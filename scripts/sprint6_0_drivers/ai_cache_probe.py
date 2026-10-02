"""Offline: which of the 118 would hit the ai_analysis cache? No API calls."""
import sys, re, types
from pathlib import Path
sys.path.insert(0, "/Users/pete/moat-sprint-6.0"); sys.path.insert(0, "/Users/pete/moat-sprint-6.0/scripts")
from moat import config
from moat.analysis import persist
from moat.db.connection import get_connection
import anthropic.resources.messages.messages as mm
mm.Messages.count_tokens = lambda self, **kw: types.SimpleNamespace(input_tokens=0)
keys = []
orig = persist.compute_bundle_key
def wrap(*a, **k):
    key = orig(*a, **k); keys.append(key); return key
persist.compute_bundle_key = wrap
orig_run = persist.run_analysis
conn = get_connection(Path("/Users/pete/moat/data/moat.db"))
hits, misses = [], []
def run_wrap(ticker, *a, **k):
    keys.clear(); r = orig_run(ticker, *a, **k)
    (hits if keys and persist.find_cached_run(ticker, keys[-1], conn) else misses).append(ticker)
    return r
persist.run_analysis = run_wrap
import run_pipeline as rp
rp.run_analysis = run_wrap
run_id = rp.new_run_id(); rp.start_run(conn, run_id)
rp.run_ai_analysis_stage(conn, run_id, dry_run=True, offline=True,
                         exclude_tickers=set(config.AI_ANALYSIS_KNOWN_EXCLUDED_TICKERS))
rp.complete_run(conn, run_id, "ai_analysis", "partial")
log = open(sys.argv[1]).read()
tok = {t: int(n.replace(",", "")) for t, n in re.findall(r"^\s+(\S+): dry_run \(([\d,]+) tokens\)", log, re.M)}
cost = sum(tok.get(t, 0) * 3e-6 + 8000 * 15e-6 for t in misses)
print(f"PROBE run {run_id}: hits {len(hits)}, misses {len(misses)}")
print("misses:", " ".join(sorted(misses)))
print(f"miss cost at sync rates ${cost:.2f}; batch (50% off) ${cost/2:.2f}; tokens known for {sum(t in tok for t in misses)}/{len(misses)}")
