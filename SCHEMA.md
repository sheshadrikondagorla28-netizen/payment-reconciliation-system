# Database schema, relationships and lifecycle

## Entity relationship overview

| Parent | Child | Cardinality | Optionality / rule |
|---|---|---|---|
| Merchant | PaymentOrder | 1:N | Every order has exactly one merchant; a merchant may have zero or many orders. |
| PaymentOrder | PaymentAttempt | 1:N | Every attempt belongs to one order; an order has one or more attempts in generated data. |
| PaymentAttempt | ProcessorEvent | 1:N | Every event belongs to one attempt; repeated webhook deliveries are retained as separate event rows. |
| PaymentOrder | SettlementLine | 1:N | A payment may have no settlement line or one normal captured line; deliberate duplicate-line anomalies may create more than one. |
| PaymentAttempt | SettlementLine | 1:N | A captured attempt may appear in a settlement line; anomaly injection can duplicate a line. |
| SettlementBatch | SettlementLine | 1:N | Every settlement line belongs to exactly one batch. |
| SettlementBatch | ReconciliationMatch | 1:0..1 | A batch may be unmatched; only a successful match has a match row. |
| BankStatementLine | ReconciliationMatch | 1:0..1 | A bank line may be unmatched; only a successful match has a match row. |
| SettlementBatch | Exception | 1:0..N | Optional FK; some exceptions concern a batch, others are bank-only or event/line anomalies. |
| BankStatementLine | Exception | 1:0..N | Optional FK; unmatched bank lines are recorded as exceptions. |
| Exception | Resolution | 1:0..N | Resolution rows are append-only history; an exception can have multiple investigations/resolutions. |

There is no N:M relationship in this version, so no bridge table is required. ReconciliationMatch is a linking/decision entity between one batch and one bank line. If future requirements permit one bank credit to settle multiple batches, replace the 1:0..1 rule with an allocation bridge (for example `reconciliation_match_allocations`) and define allocated amounts explicitly.

## Primary and foreign keys

- Primary key: first ID field in each entity export; unique and immutable.
- Foreign keys: `payment_orders.merchant_id`; `payment_attempts.payment_order_id`; `processor_events.payment_attempt_id` and `payment_order_id`; `settlement_lines.settlement_batch_id`, `payment_order_id`, `payment_attempt_id`; `reconciliation_matches.settlement_batch_id` and `bank_statement_line_id`; optional exception references; `resolutions.exception_id`.
- The SQLite database is generated from the same tabular data as the CSVs. `validate.py` independently checks all listed relationships, even where SQLite itself does not enforce foreign-key constraints.

## ID conventions

- Merchant: `MER-0000001`
- Payment order: `PAY-2026-00000001`
- Attempt: `ATT-PAY-2026-00000001-01`
- Processor event: `PEV-000000001`
- Settlement batch: `SET-2026-000001`
- Settlement line: `STL-SET-2026-000001-001`
- Bank statement line: `BANK-2026-000001`
- Reconciliation match: `REC-2026-000001`
- Exception: `EXC-2026-000001`
- Resolution: `RES-2026-000001` (API/UI-created rows use their own `RES-API` / `RES-UI` prefixes).

IDs are deterministic for generated source data and are never reused within an entity export. Runtime resolution IDs are operational IDs and are not part of the seed reproducibility guarantee.

## Lifecycle states

- PaymentOrder: `PENDING`, `CAPTURED`, `FAILED`. Derived from attempts: if any attempt is captured, order is captured; otherwise use the latest attempt state.
- PaymentAttempt: `PENDING`, `CAPTURED`, `FAILED`.
- ProcessorEvent: immutable event facts (`CAPTURE`, `PAYMENT_FAILED`, `PAYMENT_PENDING`, `REFUND`). Duplicate deliveries are preserved and detected by `idempotency_key`.
- SettlementBatch: generated as `SETTLED` after its lines and totals are calculated.
- Exception: `OPEN` -> `RESOLVED`. A resolution with outcome `ACKNOWLEDGED` or `ESCALATED` does not close the exception.
- Resolution: append-only record of who acted, what action was taken, notes, timestamp and outcome.

## Monetary calculations

All generated amounts are in major currency units, rounded to two decimal places for this synthetic dataset:

`net_amount = gross_amount - refund_amount - fee_amount`

Synthetic fee rule: 2% of gross amount. Refunds are partial (a sampled percentage of gross) for a subset of captured transactions. Real payment systems must use processor-specific fee/refund contracts and currency minor-unit rules.

## Reconciliation rule

A bank statement line matches a settlement batch only when all conditions hold:

1. `bank_reference == settlement_batch_id`;
2. `currency` is identical;
3. `abs(bank.amount - settlement_batch.net_amount) <= 0.01`;
4. absolute date difference between bank transaction date and settlement date is at most five calendar days.

A batch with no successful match receives an exception. A bank statement line not used by a successful match also receives an exception. A match row records the exact batch/bank pair and method.

## Deliberate anomalies

Anomalies are injected after baseline records are created and carry `issue_type` labels. Duplicate settlement lines have their own unique line ID and are excluded from the baseline aggregation check. Duplicate processor events retain the same idempotency key but have distinct event IDs. A missing bank record is represented by absence of a bank line plus a batch exception, not by an invalid placeholder row. `ground_truth.csv` is an independent evaluation label file and must not be used as a matching input.
