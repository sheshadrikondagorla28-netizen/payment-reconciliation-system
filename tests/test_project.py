import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "reconciliation.db"

def test_database_exists():
    assert DB.exists(), "Run python generate.py before pytest."

def test_expected_tables_exist():
    with sqlite3.connect(DB) as con:
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    expected = {"merchants","payment_orders","payment_attempts","processor_events","settlement_batches",
                "settlement_lines","bank_statement_lines","reconciliation_matches","exceptions","resolutions"}
    assert expected.issubset(names)

def test_primary_ids_unique():
    with sqlite3.connect(DB) as con:
        for table in ["merchants","payment_orders","payment_attempts","processor_events","settlement_batches",
                      "settlement_lines","bank_statement_lines","reconciliation_matches","exceptions","resolutions"]:
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
            if not cols: continue
            pk = cols[0]
            total, distinct = con.execute(f"SELECT COUNT(*), COUNT(DISTINCT {pk}) FROM {table}").fetchone()
            assert total == distinct, table
