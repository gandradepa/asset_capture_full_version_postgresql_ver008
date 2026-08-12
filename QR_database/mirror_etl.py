"""C2 ETL: faithfully mirror the SQLite snapshot into PostgreSQL qr_code_db.

- Exact identifiers preserved (every name quoted).
- All columns created as text (guaranteed load + exact value fidelity); C3 assigns
  real types via ALTER ... TYPE ... USING.
- Generated columns (e.g. ID_check, hidden!=0) are EXCLUDED; re-added STORED in C3.
- NULL vs '' preserved (None -> NULL, '' -> '').
- No FKs/PKs/constraints here (those are C3).
"""
import sqlite3, sys
import psycopg2
from psycopg2.extras import execute_values

SNAP = "/home/developer/QR_database/source_snapshot.db"
s = sqlite3.connect(f"file:{SNAP}?mode=ro", uri=True); s.row_factory = sqlite3.Row
sc = s.cursor()
pg = psycopg2.connect(host="127.0.0.1", port=5433, dbname="qr_code_db", user="developer")
pg.autocommit = False
pc = pg.cursor()

def q(name): return '"' + name.replace('"', '""') + '"'
def conv(v):
    if v is None: return None
    if isinstance(v, (bytes, bytearray)):
        try: return v.decode("utf-8", "replace")
        except Exception: return str(v)
    return str(v)

print("=== clean slate ===", flush=True)
pc.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public; "
           "GRANT ALL ON SCHEMA public TO developer; GRANT ALL ON SCHEMA public TO assetcap_app;")

tables = [r[0] for r in sc.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
summary = []
for t in tables:
    cols = sc.execute(f'PRAGMA table_xinfo("{t}")').fetchall()
    keep = [c["name"] for c in cols if c["hidden"] == 0]      # exclude generated/hidden
    skipped = [c["name"] for c in cols if c["hidden"] != 0]
    coldefs = ", ".join(f"{q(cn)} text" for cn in keep)
    pc.execute(f"CREATE TABLE {q(t)} ({coldefs})")
    sel = ", ".join(q(cn) for cn in keep)
    rows = sc.execute(f"SELECT {sel} FROM {q(t)}").fetchall()
    data = [tuple(conv(v) for v in r) for r in rows]
    if data:
        execute_values(pc, f"INSERT INTO {q(t)} ({sel}) VALUES %s", data, page_size=1000)
    summary.append((t, len(rows), skipped))
pg.commit()

print("=== PARITY (sqlite -> pg) ===", flush=True)
allok = True; tot_s = tot_p = 0
for t, n, skipped in summary:
    pc.execute(f"SELECT count(*) FROM {q(t)}"); pgn = pc.fetchone()[0]
    ok = (pgn == n); allok &= ok; tot_s += n; tot_p += pgn
    note = f"  (excluded generated: {skipped})" if skipped else ""
    print(f"{'OK ' if ok else 'BAD'} {t}: sqlite={n} pg={pgn}{note}", flush=True)
print(f"--- totals: sqlite={tot_s} pg={tot_p} :: {'ALL MATCH' if allok else 'MISMATCH'} ---", flush=True)
pc.close(); pg.close(); s.close()
