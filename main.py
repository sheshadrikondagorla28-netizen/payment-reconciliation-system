from pathlib import Path
import sqlite3
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

DB = Path(__file__).resolve().parent / "data" / "reconciliation.db"
app = FastAPI(title="Payment Reconciliation API", version="1.0.0",
              description="Synthetic payment-to-bank reconciliation and exception management.")

def query(sql, params=()):
    if not DB.exists():
        raise HTTPException(503, "Database missing. Run python generate.py first.")
    with sqlite3.connect(DB) as con:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(sql, params).fetchall()]

class ResolveRequest(BaseModel):
    resolved_by: str = Field(min_length=3, max_length=120)
    resolution_action: str = Field(min_length=3, max_length=100)
    resolution_notes: str = Field(min_length=3, max_length=1000)
    outcome: str = Field(default="RESOLVED", max_length=40)

@app.get("/")
def root():
    return {"application":"Payment Reconciliation System","status":"running","docs":"/docs"}

@app.get("/health")
def health():
    return {"status":"ok","database_exists":DB.exists()}

@app.get("/metrics")
def metrics():
    q = query
    counts = {}
    for table in ["merchants","payment_orders","payment_attempts","processor_events","settlement_batches",
                  "settlement_lines","bank_statement_lines","reconciliation_matches","exceptions","resolutions"]:
        counts[table] = q(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]
    matched = counts["reconciliation_matches"]
    batches = counts["settlement_batches"]
    counts["reconciliation_rate_percent"] = round(100*matched/batches,2) if batches else 0
    counts["open_exceptions"] = q("SELECT COUNT(*) AS n FROM exceptions WHERE status='OPEN'")[0]["n"]
    counts["total_settlement_net"] = q("SELECT ROUND(COALESCE(SUM(net_amount),0),2) AS n FROM settlement_batches")[0]["n"]
    counts["total_fees"] = q("SELECT ROUND(COALESCE(SUM(fee_amount),0),2) AS n FROM settlement_batches")[0]["n"]
    return counts

@app.get("/matches")
def get_matches(limit: int = 100, offset: int = 0):
    return query("SELECT * FROM reconciliation_matches ORDER BY reconciliation_match_id LIMIT ? OFFSET ?", (min(limit,1000),offset))

@app.get("/exceptions")
def get_exceptions(status: str | None = "OPEN", limit: int = 100, offset: int = 0):
    if status not in (None, "OPEN", "RESOLVED"):
        raise HTTPException(400, "status must be OPEN, RESOLVED, or omitted")
    if status:
        return query("SELECT * FROM exceptions WHERE status=? ORDER BY exception_id LIMIT ? OFFSET ?", (status,min(limit,1000),offset))
    return query("SELECT * FROM exceptions ORDER BY exception_id LIMIT ? OFFSET ?", (min(limit,1000),offset))

@app.post("/exceptions/{exception_id}/resolve")
def resolve_exception(exception_id: str, body: ResolveRequest):
    rows = query("SELECT * FROM exceptions WHERE exception_id=?", (exception_id,))
    if not rows: raise HTTPException(404, "Exception not found")
    if rows[0]["status"] == "RESOLVED": raise HTTPException(409, "Exception already resolved")
    with sqlite3.connect(DB) as con:
        resolution_id = f"RES-API-{con.execute('SELECT COUNT(*) FROM resolutions').fetchone()[0]+1:06d}"
        con.execute("""INSERT INTO resolutions
            (resolution_id,exception_id,resolved_by,resolution_action,resolution_notes,resolved_at,outcome)
            VALUES (?,?,?,?,?,datetime('now'),?)""",
            (resolution_id,exception_id,body.resolved_by,body.resolution_action,body.resolution_notes,body.outcome))
        if body.outcome == "RESOLVED":
            con.execute("UPDATE exceptions SET status='RESOLVED' WHERE exception_id=?", (exception_id,))
        con.commit()
    return {"exception_id":exception_id,"resolution_id":resolution_id,"status":body.outcome}

@app.get("/settlements")
def settlements(limit: int = 100):
    return query("SELECT * FROM settlement_batches ORDER BY settlement_batch_id LIMIT ?", (min(limit,1000),))

@app.get("/settlements/{batch_id}/lines")
def settlement_lines(batch_id: str):
    return query("SELECT * FROM settlement_lines WHERE settlement_batch_id=? ORDER BY settlement_line_id", (batch_id,))
