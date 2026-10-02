"""Sprint 6.0 row-level diff: baseline backup vs re-ingested live DB."""
import sqlite3, collections, math
db = sqlite3.connect("file:/Users/pete/moat/data/moat.db?mode=ro", uri=True)
db.execute("ATTACH 'file:/Users/pete/moat/data/moat-baseline-pre-sprint6.0.db?mode=ro' AS b")
cols = [r[1] for r in db.execute("PRAGMA b.table_info(fundamentals_annual)")
        if r[1] not in ("ticker", "fiscal_year", "retrieved_at")]
P = {r[0] for r in db.execute("SELECT ticker FROM quality_scores WHERE run_id='20260923T122501Z' AND passed_screen=1")}
keys_old = set(db.execute("SELECT ticker, fiscal_year FROM b.fundamentals_annual"))
keys_new = set(db.execute("SELECT ticker, fiscal_year FROM main.fundamentals_annual"))
print(f"## fundamentals_annual rows: baseline {len(keys_old)}, now {len(keys_new)}; "
      f"only-before {len(keys_old-keys_new)}, only-after {len(keys_new-keys_old)}")
def differs(a, b):
    if a is None or b is None: return (a is None) != (b is None)
    if isinstance(a, float) or isinstance(b, float):
        return not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)
    return a != b
sel = ", ".join(f"o.{c}, n.{c}" for c in cols)
rows = db.execute(f"""SELECT o.ticker, o.fiscal_year, {sel} FROM b.fundamentals_annual o
    JOIN main.fundamentals_annual n USING (ticker, fiscal_year)""").fetchall()
changed = collections.Counter(); changed_pass = collections.Counter(); cos = collections.defaultdict(set)
kinds = collections.defaultdict(collections.Counter)
for r in rows:
    t = r[0]
    for i, c in enumerate(cols):
        a, b = r[2 + 2*i], r[3 + 2*i]
        if differs(a, b):
            changed[c] += 1; cos[c].add(t); changed_pass[c] += t in P
            kinds[c]["NULL->value" if a is None else "value->NULL" if b is None else "value->value"] += 1
print("\n| field | rows changed | companies | passer rows | NULL→value | value→NULL | value→value |\n|---|---:|---:|---:|---:|---:|---:|")
for c in cols:
    if changed[c]:
        k = kinds[c]
        print(f"| {c} | {changed[c]} | {len(cos[c])} | {changed_pass[c]} | {k['NULL->value']} | {k['value->NULL']} | {k['value->value']} |")
print("\nunchanged fields:", [c for c in cols if not changed[c]])

# latest fiscal year, passers: debt / ROE / D&A moves
print("\n## Passers, latest fiscal year: material moves (>10% or NULL change)")
q = """SELECT o.ticker, o.fiscal_year, o.total_debt, n.total_debt, o.roe, n.roe,
       o.depreciation_amortization, n.depreciation_amortization
FROM b.fundamentals_annual o JOIN main.fundamentals_annual n USING (ticker, fiscal_year)
JOIN (SELECT ticker, MAX(fiscal_year) fy FROM main.fundamentals_annual GROUP BY ticker) l
  ON l.ticker=o.ticker AND l.fy=o.fiscal_year ORDER BY o.ticker"""
def moved(a, b):
    if a is None or b is None: return (a is None) != (b is None)
    return abs(b - a) > 0.10 * max(abs(a), 1e-9)
def fmt(v, pct=False):
    if v is None: return "NULL"
    return f"{v*100:.1f}%" if pct else f"{v/1e6:,.0f}M"
for t, fy, d0, d1, r0, r1, a0, a1 in db.execute(q):
    if t not in P: continue
    parts = []
    if moved(d0, d1): parts.append(f"debt {fmt(d0)}→{fmt(d1)}")
    if moved(r0, r1): parts.append(f"roe {fmt(r0,1)}→{fmt(r1,1)}")
    if moved(a0, a1): parts.append(f"D&A {fmt(a0)}→{fmt(a1)}")
    if parts: print(f"- {t} FY{fy}: " + "; ".join(parts))

# prices
print("\n## price_history")
for label, sch in (("baseline", "b"), ("now", "main")):
    dist = db.execute(f"SELECT d, COUNT(*) FROM (SELECT MAX(date) d FROM {sch}.price_history GROUP BY ticker) GROUP BY d ORDER BY d DESC LIMIT 5").fetchall()
    n = db.execute(f"SELECT COUNT(*) FROM {sch}.price_history").fetchone()[0]
    print(f"- {label}: {n} rows; latest-date distribution {dist}")
print("- passers with latest close before 2026-09-24:",
      db.execute("SELECT COUNT(*) FROM (SELECT ticker, MAX(date) d FROM main.price_history GROUP BY ticker) WHERE d < '2026-09-24' AND ticker IN (%s)" % ",".join("?"*len(P)), list(P)).fetchone()[0])
