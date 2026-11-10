from __future__ import annotations
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "reconciliation.db"
TABLES = ["merchants", "payment_orders", "payment_attempts", "processor_events", "settlement_batches", "settlement_lines", "bank_statement_lines", "reconciliation_matches", "exceptions", "resolutions"]
FK_CHECKS = [
    ("payment_orders", "merchant_id", "merchants", "merchant_id"),
    ("payment_attempts", "payment_order_id", "payment_orders", "payment_order_id"),
    ("processor_events", "payment_attempt_id", "payment_attempts", "payment_attempt_id"),
    ("processor_events", "payment_order_id", "payment_orders", "payment_order_id"),
    ("settlement_lines", "settlement_batch_id", "settlement_batches", "settlement_batch_id"),
    ("settlement_lines", "payment_order_id", "payment_orders", "payment_order_id"),
    ("settlement_lines", "payment_attempt_id", "payment_attempts", "payment_attempt_id"),
    ("reconciliation_matches", "settlement_batch_id", "settlement_batches", "settlement_batch_id"),
    ("reconciliation_matches", "bank_statement_line_id", "bank_statement_lines", "bank_statement_line_id"),
    ("exceptions", "settlement_batch_id", "settlement_batches", "settlement_batch_id"),
    ("exceptions", "bank_statement_line_id", "bank_statement_lines", "bank_statement_line_id"),
    ("resolutions", "exception_id", "exceptions", "exception_id"),
]

def validate():
    if not DB.exists():
        raise SystemExit("Database not found. Run: python generate.py --scale starter")
    errors, checks = [], {}
    with sqlite3.connect(DB) as con:
        con.row_factory = sqlite3.Row
        present = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in TABLES:
            if table not in present:
                errors.append(f"Missing required table: {table}")
                continue
            info = con.execute(f'PRAGMA table_info("{table}")').fetchall()
            pk = next((r["name"] for r in info if r["pk"]), info[0]["name"])
            n = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            dup = con.execute(f'SELECT COUNT(*) FROM (SELECT "{pk}" FROM "{table}" GROUP BY "{pk}" HAVING COUNT(*)>1)').fetchone()[0]
            nulls = con.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{pk}" IS NULL OR TRIM("{pk}")=""').fetchone()[0]
            checks[table] = {"rows": n, "duplicate_primary_keys": dup, "null_primary_keys": nulls}
            if dup or nulls:
                errors.append(f"{table}: duplicate or blank primary keys")
        for child, fk, parent, pk in FK_CHECKS:
            if child not in present or parent not in present:
                continue
            bad = con.execute(f'SELECT COUNT(*) FROM "{child}" c LEFT JOIN "{parent}" p ON c."{fk}"=p."{pk}" WHERE c."{fk}" IS NOT NULL AND p."{pk}" IS NULL').fetchone()[0]
            checks[f"fk_{child}_{fk}"] = bad
            if bad:
                errors.append(f"{child}.{fk}: {bad} invalid references")
        bad = con.execute("""SELECT COUNT(*) FROM payment_orders o WHERE o.status !=
            CASE WHEN EXISTS(SELECT 1 FROM payment_attempts a WHERE a.payment_order_id=o.payment_order_id AND a.status='CAPTURED') THEN 'CAPTURED'
            ELSE (SELECT a.status FROM payment_attempts a WHERE a.payment_order_id=o.payment_order_id ORDER BY a.attempt_number DESC LIMIT 1) END""").fetchone()[0]
        checks["order_lifecycle_errors"] = bad
        if bad: errors.append(f"{bad} payment orders disagree with attempt history")
        bad = con.execute("SELECT COUNT(*) FROM settlement_batches WHERE ABS(net_amount-(gross_amount-refund_amount-fee_amount))>0.011").fetchone()[0]
        checks["batch_arithmetic_errors"] = bad
        if bad: errors.append(f"{bad} settlement batch arithmetic errors")
        bad = con.execute("""SELECT COUNT(*) FROM settlement_batches b LEFT JOIN
            (SELECT settlement_batch_id, SUM(gross_amount) gross, SUM(refund_amount) refunds, SUM(fee_amount) fees, SUM(net_amount) net
             FROM settlement_lines WHERE issue_type!='duplicate_settlement_line' GROUP BY settlement_batch_id) l USING(settlement_batch_id)
            WHERE l.settlement_batch_id IS NULL OR ABS(b.gross_amount-l.gross)>.011 OR ABS(b.refund_amount-l.refunds)>.011
               OR ABS(b.fee_amount-l.fees)>.011 OR ABS(b.net_amount-l.net)>.011""").fetchone()[0]
        checks["batch_line_aggregation_errors"] = bad
        if bad: errors.append(f"{bad} batches do not reconcile to their non-anomalous lines")
        bad = con.execute("""SELECT COUNT(*) FROM settlement_batches b WHERE
            NOT EXISTS(SELECT 1 FROM reconciliation_matches m WHERE m.settlement_batch_id=b.settlement_batch_id)
            AND NOT EXISTS(SELECT 1 FROM exceptions e WHERE e.settlement_batch_id=b.settlement_batch_id)""").fetchone()[0]
        checks["unmatched_batches_without_exception"] = bad
        if bad: errors.append(f"{bad} unmatched batches have no exception")
        bad = con.execute("""SELECT COUNT(*) FROM bank_statement_lines b WHERE
            NOT EXISTS(SELECT 1 FROM reconciliation_matches m WHERE m.bank_statement_line_id=b.bank_statement_line_id)
            AND NOT EXISTS(SELECT 1 FROM exceptions e WHERE e.bank_statement_line_id=b.bank_statement_line_id)""").fetchone()[0]
        checks["unmatched_bank_lines_without_exception"] = bad
        if bad: errors.append(f"{bad} unmatched bank lines have no exception")
        bad = con.execute("""SELECT COUNT(*) FROM reconciliation_matches m JOIN settlement_batches s USING(settlement_batch_id)
            JOIN bank_statement_lines b USING(bank_statement_line_id) WHERE b.bank_reference!=s.settlement_batch_id
            OR b.currency!=s.currency OR ABS(b.amount-s.net_amount)>.01
            OR ABS(julianday(b.transaction_date)-julianday(s.settlement_date))>5""").fetchone()[0]
        checks["invalid_matches"] = bad
        if bad: errors.append(f"{bad} matches violate reference/amount/currency/date rules")
        bad = con.execute("""SELECT COUNT(*) FROM (SELECT idempotency_key FROM processor_events GROUP BY idempotency_key
            HAVING COUNT(*)>1 AND SUM(CASE WHEN issue_type='duplicate_processor_event' THEN 1 ELSE 0 END)=0)""").fetchone()[0]
        checks["unlabelled_duplicate_webhook_groups"] = bad
        if bad: errors.append(f"{bad} duplicate webhook groups are not labelled")
        checks["labelled_duplicate_settlement_lines"] = con.execute("SELECT COUNT(*) FROM settlement_lines WHERE issue_type='duplicate_settlement_line'").fetchone()[0]
        checks["labelled_refund_before_capture"] = con.execute("SELECT COUNT(*) FROM processor_events WHERE issue_type='refund_before_capture'").fetchone()[0]
        checks["open_exceptions"] = con.execute("SELECT COUNT(*) FROM exceptions WHERE status='OPEN'").fetchone()[0]
        checks["resolved_exceptions"] = con.execute("SELECT COUNT(*) FROM exceptions WHERE status='RESOLVED'").fetchone()[0]
    result = {"passed": not errors, "errors": errors, "checks": checks}
    (ROOT / "data" / "validation_detailed.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    if errors:
        raise SystemExit(1)

if __name__ == "__main__":
    validate()
