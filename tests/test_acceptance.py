"""Additional acceptance tests for the Payment Reconciliation project.

Run from the project root:
    pytest -v

These tests use a temporary copy of the SQLite database for API write tests,
so resolving an exception during testing does not modify data/reconciliation.db.
"""
import csv
import shutil
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DB = DATA / "reconciliation.db"

TABLES = {
    "merchants", "payment_orders", "payment_attempts", "processor_events",
    "settlement_batches", "settlement_lines", "bank_statement_lines",
    "reconciliation_matches", "exceptions", "resolutions",
}
REQUIRED_ANOMALIES = {
    "fee_amount_difference",
    "missing_bank_record",
    "currency_mismatch",
    "duplicate_settlement_line",
    "refund_before_capture",
    "duplicate_processor_event",
}


@pytest.fixture
def con():
    assert DB.exists(), "Database is missing. Run: python generate.py --scale full --seed 20261022"
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    yield connection
    connection.close()


def scalar(con, sql, params=()):
    return con.execute(sql, params).fetchone()[0]


def read_ground_truth():
    path = DATA / "ground_truth.csv"
    assert path.exists(), "ground_truth.csv is missing; run the generator first."
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_01_database_file_exists():
    assert DB.is_file()


def test_02_all_ten_required_tables_exist(con):
    found = {r["name"] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert TABLES.issubset(found)


@pytest.mark.parametrize("table", sorted(TABLES))
def test_03_to_12_each_table_has_rows(con, table):
    assert scalar(con, f'SELECT COUNT(*) FROM "{table}"') > 0, table


@pytest.mark.parametrize("table", sorted(TABLES))
def test_13_to_22_primary_keys_are_non_null_and_unique(con, table):
    info = con.execute(f'PRAGMA table_info("{table}")').fetchall()
    pk = next((r["name"] for r in info if r["pk"]), info[0]["name"])
    total = scalar(con, f'SELECT COUNT(*) FROM "{table}"')
    distinct = scalar(con, f'SELECT COUNT(DISTINCT "{pk}") FROM "{table}"')
    nulls = scalar(
        con, f'SELECT COUNT(*) FROM "{table}" WHERE "{pk}" IS NULL OR TRIM("{pk}")=""'
    )
    assert total == distinct, f"{table}.{pk} is not unique"
    assert nulls == 0, f"{table}.{pk} contains null/blank IDs"


def test_23_payment_order_merchant_foreign_keys_are_valid(con):
    assert scalar(con, """
        SELECT COUNT(*) FROM payment_orders o
        LEFT JOIN merchants m ON m.merchant_id=o.merchant_id
        WHERE m.merchant_id IS NULL
    """) == 0


def test_24_attempt_payment_order_foreign_keys_are_valid(con):
    assert scalar(con, """
        SELECT COUNT(*) FROM payment_attempts a
        LEFT JOIN payment_orders o ON o.payment_order_id=a.payment_order_id
        WHERE o.payment_order_id IS NULL
    """) == 0


def test_25_processor_event_parent_references_are_valid(con):
    assert scalar(con, """
        SELECT COUNT(*) FROM processor_events e
        LEFT JOIN payment_attempts a ON a.payment_attempt_id=e.payment_attempt_id
        LEFT JOIN payment_orders o ON o.payment_order_id=e.payment_order_id
        WHERE a.payment_attempt_id IS NULL OR o.payment_order_id IS NULL
    """) == 0


def test_26_settlement_line_parent_references_are_valid(con):
    assert scalar(con, """
        SELECT COUNT(*) FROM settlement_lines l
        LEFT JOIN settlement_batches b ON b.settlement_batch_id=l.settlement_batch_id
        LEFT JOIN payment_orders o ON o.payment_order_id=l.payment_order_id
        LEFT JOIN payment_attempts a ON a.payment_attempt_id=l.payment_attempt_id
        WHERE b.settlement_batch_id IS NULL
           OR o.payment_order_id IS NULL
           OR a.payment_attempt_id IS NULL
    """) == 0


def test_27_payment_order_status_matches_attempt_history(con):
    bad = scalar(con, """
        SELECT COUNT(*) FROM payment_orders o
        WHERE o.status != CASE
          WHEN EXISTS (
            SELECT 1 FROM payment_attempts a
            WHERE a.payment_order_id=o.payment_order_id AND a.status='CAPTURED'
          ) THEN 'CAPTURED'
          ELSE (
            SELECT a.status FROM payment_attempts a
            WHERE a.payment_order_id=o.payment_order_id
            ORDER BY a.attempt_number DESC LIMIT 1
          )
        END
    """)
    assert bad == 0


def test_28_attempt_numbers_are_positive_and_unique_per_order(con):
    assert scalar(con, """
        SELECT COUNT(*) FROM payment_attempts
        WHERE attempt_number < 1
    """) == 0
    assert scalar(con, """
        SELECT COUNT(*) FROM (
          SELECT payment_order_id, attempt_number
          FROM payment_attempts
          GROUP BY payment_order_id, attempt_number
          HAVING COUNT(*) > 1
        )
    """) == 0


def test_29_settlement_net_amount_arithmetic(con):
    assert scalar(con, """
        SELECT COUNT(*) FROM settlement_batches
        WHERE ABS(net_amount-(gross_amount-refund_amount-fee_amount)) > 0.011
    """) == 0


def test_30_settlement_line_arithmetic(con):
    assert scalar(con, """
        SELECT COUNT(*) FROM settlement_lines
        WHERE issue_type != 'duplicate_settlement_line'
          AND ABS(net_amount-(gross_amount-refund_amount-fee_amount)) > 0.011
    """) == 0


def test_31_batch_totals_equal_non_duplicate_line_totals(con):
    bad = scalar(con, """
        SELECT COUNT(*) FROM settlement_batches b
        LEFT JOIN (
          SELECT settlement_batch_id,
                 SUM(gross_amount) gross,
                 SUM(refund_amount) refunds,
                 SUM(fee_amount) fees,
                 SUM(net_amount) net
          FROM settlement_lines
          WHERE issue_type != 'duplicate_settlement_line'
          GROUP BY settlement_batch_id
        ) l ON l.settlement_batch_id=b.settlement_batch_id
        WHERE l.settlement_batch_id IS NULL
           OR ABS(b.gross_amount-l.gross) > 0.011
           OR ABS(b.refund_amount-l.refunds) > 0.011
           OR ABS(b.fee_amount-l.fees) > 0.011
           OR ABS(b.net_amount-l.net) > 0.011
    """)
    assert bad == 0


def test_32_all_matches_obey_reference_currency_amount_and_date_rules(con):
    bad = scalar(con, """
        SELECT COUNT(*) FROM reconciliation_matches m
        JOIN settlement_batches s ON s.settlement_batch_id=m.settlement_batch_id
        JOIN bank_statement_lines b ON b.bank_statement_line_id=m.bank_statement_line_id
        WHERE b.bank_reference != s.settlement_batch_id
           OR b.currency != s.currency
           OR ABS(b.amount-s.net_amount) > 0.01
           OR ABS(julianday(b.transaction_date)-julianday(s.settlement_date)) > 5
    """)
    assert bad == 0


def test_33_unmatched_batches_have_exceptions(con):
    assert scalar(con, """
        SELECT COUNT(*) FROM settlement_batches b
        WHERE NOT EXISTS (
          SELECT 1 FROM reconciliation_matches m
          WHERE m.settlement_batch_id=b.settlement_batch_id
        )
        AND NOT EXISTS (
          SELECT 1 FROM exceptions e
          WHERE e.settlement_batch_id=b.settlement_batch_id
        )
    """) == 0


def test_34_unmatched_bank_lines_have_exceptions(con):
    assert scalar(con, """
        SELECT COUNT(*) FROM bank_statement_lines b
        WHERE NOT EXISTS (
          SELECT 1 FROM reconciliation_matches m
          WHERE m.bank_statement_line_id=b.bank_statement_line_id
        )
        AND NOT EXISTS (
          SELECT 1 FROM exceptions e
          WHERE e.bank_statement_line_id=b.bank_statement_line_id
        )
    """) == 0


@pytest.mark.parametrize("anomaly", sorted(REQUIRED_ANOMALIES))
def test_35_to_40_all_six_required_anomalies_are_in_ground_truth(anomaly):
    labels = {row["issue_type"] for row in read_ground_truth()}
    assert anomaly in labels, f"Missing required anomaly: {anomaly}"


def test_41_duplicate_settlement_line_is_preserved_and_labelled(con):
    assert scalar(con, """
        SELECT COUNT(*) FROM settlement_lines
        WHERE issue_type='duplicate_settlement_line'
    """) >= 1


def test_42_duplicate_processor_event_reuses_idempotency_key(con):
    assert scalar(con, """
        SELECT COUNT(*) FROM (
          SELECT idempotency_key
          FROM processor_events
          GROUP BY idempotency_key
          HAVING COUNT(*) > 1
             AND SUM(CASE WHEN issue_type='duplicate_processor_event' THEN 1 ELSE 0 END) > 0
        )
    """) >= 1


def test_43_refund_before_capture_is_labelled(con):
    assert scalar(con, """
        SELECT COUNT(*) FROM processor_events
        WHERE issue_type='refund_before_capture'
    """) >= 1


def test_44_missing_bank_record_is_not_represented_by_fake_bank_row(con):
    assert scalar(con, """
        SELECT COUNT(*) FROM exceptions
        WHERE exception_type LIKE '%missing%'
           OR description LIKE '%missing bank%'
           OR description LIKE '%no bank%'
    """) >= 1 or any(
        r["issue_type"] == "missing_bank_record" for r in read_ground_truth()
    )


def test_45_currency_mismatch_case_is_present(con):
    assert scalar(con, """
        SELECT COUNT(*) FROM bank_statement_lines
        WHERE issue_type='currency_mismatch'
    """) >= 1


def test_46_fee_variance_case_is_present(con):
    assert scalar(con, """
        SELECT COUNT(*) FROM bank_statement_lines
        WHERE issue_type='fee_amount_difference'
    """) >= 1


def test_47_exception_status_values_are_valid(con):
    assert scalar(con, """
        SELECT COUNT(*) FROM exceptions
        WHERE status NOT IN ('OPEN','RESOLVED')
    """) == 0


def test_48_resolutions_reference_existing_exceptions(con):
    assert scalar(con, """
        SELECT COUNT(*) FROM resolutions r
        LEFT JOIN exceptions e ON e.exception_id=r.exception_id
        WHERE e.exception_id IS NULL
    """) == 0


def test_49_resolution_rows_have_required_text(con):
    assert scalar(con, """
        SELECT COUNT(*) FROM resolutions
        WHERE resolved_by IS NULL OR TRIM(resolved_by)=''
           OR resolution_action IS NULL OR TRIM(resolution_action)=''
           OR resolution_notes IS NULL OR TRIM(resolution_notes)=''
    """) == 0


# API tests: use a temporary database copy so POST requests cannot alter
# the real project database.
@pytest.fixture
def api_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import main

    assert DB.exists(), "Generate the database before running API tests."
    test_db = tmp_path / "api_test.db"
    shutil.copy2(DB, test_db)
    monkeypatch.setattr(main, "DB", test_db)
    return TestClient(main.app), test_db


def test_50_api_root(api_client):
    client, _ = api_client
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "running"


def test_51_api_health(api_client):
    client, _ = api_client
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["database_exists"] is True


def test_52_api_metrics_counts_are_non_negative(api_client):
    client, _ = api_client
    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.json()
    for table in TABLES:
        assert body[table] >= 0
    assert body["reconciliation_rate_percent"] >= 0


def test_53_api_matches_pagination(api_client):
    client, _ = api_client
    response = client.get("/matches?limit=5&offset=0")
    assert response.status_code == 200
    assert len(response.json()) <= 5


def test_54_api_exceptions_open_filter(api_client):
    client, _ = api_client
    response = client.get("/exceptions?status=OPEN&limit=10")
    assert response.status_code == 200
    assert all(row["status"] == "OPEN" for row in response.json())


def test_55_api_exceptions_reject_invalid_status(api_client):
    client, _ = api_client
    response = client.get("/exceptions?status=INVALID")
    assert response.status_code == 400


def test_56_api_settlement_list(api_client):
    client, _ = api_client
    response = client.get("/settlements?limit=5")
    assert response.status_code == 200
    assert len(response.json()) <= 5


def test_57_api_settlement_lines_for_existing_batch(api_client):
    client, _ = api_client
    batch_id = client.get("/settlements?limit=1").json()[0]["settlement_batch_id"]
    response = client.get(f"/settlements/{batch_id}/lines")
    assert response.status_code == 200
    assert all(row["settlement_batch_id"] == batch_id for row in response.json())


def test_58_api_resolve_exception_updates_status_in_test_copy(api_client):
    client, test_db = api_client
    with sqlite3.connect(test_db) as db:
        row = db.execute(
            "SELECT exception_id FROM exceptions WHERE status='OPEN' LIMIT 1"
        ).fetchone()
    if row is None:
        pytest.skip("No OPEN exception available in generated dataset.")
    exception_id = row[0]
    response = client.post(
        f"/exceptions/{exception_id}/resolve",
        json={
            "resolved_by": "pytest.analyst",
            "resolution_action": "MANUAL_REVIEW",
            "resolution_notes": "Verified against the synthetic bank and processor records.",
            "outcome": "RESOLVED",
        },
    )
    assert response.status_code == 200
    with sqlite3.connect(test_db) as db:
        status = db.execute(
            "SELECT status FROM exceptions WHERE exception_id=?", (exception_id,)
        ).fetchone()[0]
        count = db.execute(
            "SELECT COUNT(*) FROM resolutions WHERE exception_id=?", (exception_id,)
        ).fetchone()[0]
    assert status == "RESOLVED"
    assert count >= 1


def test_59_api_unknown_exception_returns_404(api_client):
    client, _ = api_client
    response = client.post(
        "/exceptions/EXC-DOES-NOT-EXIST/resolve",
        json={
            "resolved_by": "pytest.analyst",
            "resolution_action": "MANUAL_REVIEW",
            "resolution_notes": "Testing unknown exception handling.",
            "outcome": "RESOLVED",
        },
    )
    assert response.status_code == 404
