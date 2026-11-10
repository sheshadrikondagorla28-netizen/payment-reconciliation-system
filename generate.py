from __future__ import annotations
import argparse, json, sqlite3, hashlib
from pathlib import Path
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

SCALES = {
    "starter": dict(merchants=100, orders=2000, attempts=2200, batches=40, bank_lines=200),
    "full": dict(merchants=2000, orders=50000, attempts=55000, batches=1000, bank_lines=5000),
}
CURRENCIES = np.array(["INR", "USD", "EUR", "GBP"])
SEED_DEFAULT = 20261022

def money(x):
    return round(float(x) + 1e-9, 2)

def make_dataset(scale="starter", seed=SEED_DEFAULT):
    if scale not in SCALES:
        raise ValueError(f"Unknown scale {scale}. Choose starter or full.")
    n = SCALES[scale]
    rng = np.random.default_rng(seed)
    merchants_n, orders_n, attempts_n = n["merchants"], n["orders"], n["attempts"]
    batches_n, bank_n = n["batches"], n["bank_lines"]
    now = pd.Timestamp("2026-10-01")
    end = pd.Timestamp("2026-11-01")

    # 1. Master data
    merchant_ids = [f"MER-{i:07d}" for i in range(1, merchants_n + 1)]
    merchants = pd.DataFrame({
        "merchant_id": merchant_ids,
        "merchant_name": [f"Merchant {i:05d}" for i in range(1, merchants_n + 1)],
        "country": rng.choice(["IN", "US", "GB", "DE"], merchants_n, p=[.65,.18,.10,.07]),
        "default_currency": rng.choice(CURRENCIES, merchants_n, p=[.70,.15,.10,.05]),
        "status": rng.choice(["ACTIVE", "SUSPENDED"], merchants_n, p=[.97,.03]),
        "created_at": [str(now.date())] * merchants_n
    })

    # 2. Payment orders
    order_ids = [f"PAY-2026-{i:08d}" for i in range(1, orders_n + 1)]
    order_dates = now + pd.to_timedelta(rng.integers(0, 31, orders_n), unit="D") + pd.to_timedelta(rng.integers(0, 86400, orders_n), unit="s")
    merchant_for_order = rng.choice(merchant_ids, orders_n)
    merchant_currency = merchants.set_index("merchant_id")["default_currency"]
    currencies = [merchant_currency[m] for m in merchant_for_order]
    amounts = np.round(np.clip(rng.lognormal(5.0, .85, orders_n), 50, 100000), 2)
    order_status = rng.choice(["CAPTURED", "FAILED", "PENDING"], orders_n, p=[.88,.08,.04])
    orders = pd.DataFrame({
        "payment_order_id": order_ids, "merchant_id": merchant_for_order,
        "customer_reference": [f"CUSTREF-{i:08d}" for i in range(1, orders_n + 1)],
        "amount": amounts, "currency": currencies, "status": order_status,
        "created_at": order_dates.astype(str), "updated_at": (order_dates + pd.to_timedelta(rng.integers(1, 48, orders_n), unit="h")).astype(str)
    })

    # 3. Attempts: exactly requested count, at least one per order.
    attempt_order_idx = np.concatenate([np.arange(orders_n), rng.choice(orders_n, attempts_n-orders_n, replace=False)])
    rng.shuffle(attempt_order_idx)
    attempt_counts = {}
    attempt_rows = []
    for idx in attempt_order_idx:
        attempt_counts[idx] = attempt_counts.get(idx, 0) + 1
        attempt_rows.append((idx, attempt_counts[idx]))
    attempt_ids = [f"ATT-{order_ids[idx]}-{seq:02d}" for idx,seq in attempt_rows]
    attempt_times = [pd.Timestamp(orders.iloc[idx]["created_at"]) + pd.Timedelta(minutes=int(rng.integers(0, 180))) for idx,_ in attempt_rows]
    attempt_statuses = rng.choice(["CAPTURED", "FAILED", "PENDING"], attempts_n, p=[.82,.15,.03])
    attempts = pd.DataFrame({
        "payment_attempt_id": attempt_ids,
        "payment_order_id": [order_ids[idx] for idx,_ in attempt_rows],
        "attempt_number": [seq for _,seq in attempt_rows],
        "processor": rng.choice(["STRIPE_SIM", "ADYEN_SIM", "RAZORPAY_SIM"], attempts_n),
        "status": attempt_statuses,
        "amount": [money(orders.iloc[idx]["amount"]) for idx,_ in attempt_rows],
        "currency": [orders.iloc[idx]["currency"] for idx,_ in attempt_rows],
        "created_at": [str(t) for t in attempt_times],
        "updated_at": [str(t + pd.Timedelta(minutes=2)) for t in attempt_times]
    })

    # Derive order lifecycle state from its attempts (never generate it independently).
    # A captured attempt makes the order captured; otherwise use the latest attempt state.
    _latest = attempts.sort_values(["payment_order_id", "attempt_number"]).groupby("payment_order_id").tail(1).set_index("payment_order_id")
    _has_capture = attempts[attempts.status == "CAPTURED"].groupby("payment_order_id").size().index
    orders = orders.copy()
    orders["status"] = orders["payment_order_id"].map(
        lambda pid: "CAPTURED" if pid in _has_capture else _latest.loc[pid, "status"]
    )
    orders["updated_at"] = orders["payment_order_id"].map(_latest["updated_at"])

    # Derive order lifecycle state from attempt history, rather than random assignment.
    _latest = attempts.sort_values(["payment_order_id", "attempt_number"]).groupby("payment_order_id").tail(1).set_index("payment_order_id")
    _has_capture = set(attempts.loc[attempts.status == "CAPTURED", "payment_order_id"])
    orders = orders.copy()
    orders["status"] = orders["payment_order_id"].map(lambda pid: "CAPTURED" if pid in _has_capture else _latest.loc[pid, "status"])
    orders["updated_at"] = orders["payment_order_id"].map(_latest["updated_at"])

    # Keep one successful attempt per captured order as the source for settlement.
    captured_attempts = attempts[attempts.status == "CAPTURED"].drop_duplicates("payment_order_id", keep="first").copy()
    captured_attempts = captured_attempts.reset_index(drop=True)
    if len(captured_attempts) < batches_n:
        raise ValueError("Scale must create at least one captured payment per settlement batch.")
    captured_attempts["gross_amount"] = captured_attempts["amount"].astype(float)
    captured_attempts["refund_amount"] = 0.0
    refund_n = max(1, int(len(captured_attempts) * .05))
    refund_idx = rng.choice(captured_attempts.index.to_numpy(), refund_n, replace=False)
    captured_attempts.loc[refund_idx, "refund_amount"] = np.round(captured_attempts.loc[refund_idx, "gross_amount"] * rng.uniform(.05,.45,refund_n), 2)
    captured_attempts["fee_amount"] = np.round(captured_attempts["gross_amount"] * .02, 2)
    captured_attempts["net_amount"] = np.round(captured_attempts["gross_amount"] - captured_attempts["refund_amount"] - captured_attempts["fee_amount"], 2)

    # 4. Settlement batches and lines
    # Split captured records evenly into deterministic batches.
    captured_attempts = captured_attempts.sort_values(["currency", "created_at"]).reset_index(drop=True)
    # Allocate batches proportionally by currency so a batch never mixes currencies.
    currency_groups = [g.index.to_numpy() for _, g in captured_attempts.groupby("currency", sort=True)]
    if len(currency_groups) > batches_n:
        raise ValueError("Not enough batches to allocate at least one batch per currency.")
    sizes = np.array([len(x) for x in currency_groups], dtype=float)
    raw_alloc = sizes / sizes.sum() * batches_n
    alloc = np.maximum(1, np.floor(raw_alloc).astype(int))
    while alloc.sum() > batches_n:
        candidates = np.where(alloc > 1)[0]
        alloc[candidates[np.argmin(raw_alloc[candidates] - alloc[candidates])]] -= 1
    while alloc.sum() < batches_n:
        alloc[np.argmax(raw_alloc - alloc)] += 1
    batch_groups = []
    for group_indices, group_count in zip(currency_groups, alloc):
        batch_groups.extend(np.array_split(group_indices, int(group_count)))
    batch_ids = [f"SET-2026-{i:06d}" for i in range(1, batches_n+1)]
    batch_for_attempt = {}
    batch_rows, line_rows = [], []
    line_counter = {}
    for b_idx, indices in enumerate(batch_groups):
        bid = batch_ids[b_idx]
        group = captured_attempts.loc[indices]
        currency = group["currency"].mode().iloc[0]
        # Settlement batches are currency-specific: use a deterministic currency group
        # label based on the dominant currency; individual lines retain their own currency.
        gross = money(group.gross_amount.sum())
        refunds = money(group.refund_amount.sum())
        fees = money(group.fee_amount.sum())
        net = money(group.net_amount.sum())
        settle_date = pd.Timestamp(group.created_at.max()) + pd.Timedelta(days=int(rng.integers(1,4)))
        batch_rows.append({
            "settlement_batch_id": bid, "processor": "MULTI_PROCESSOR_SIM",
            "currency": currency, "gross_amount": gross, "refund_amount": refunds,
            "fee_amount": fees, "net_amount": net, "status": "SETTLED",
            "created_at": str(pd.Timestamp(group.created_at.min())),
            "settlement_date": str(settle_date)
        })
        for _, row in group.iterrows():
            line_counter[bid] = line_counter.get(bid, 0) + 1
            line_id = f"STL-{bid}-{line_counter[bid]:03d}"
            batch_for_attempt[row.payment_attempt_id] = bid
            line_rows.append({
                "settlement_line_id": line_id, "settlement_batch_id": bid,
                "payment_order_id": row.payment_order_id,
                "payment_attempt_id": row.payment_attempt_id,
                "gross_amount": money(row.gross_amount), "refund_amount": money(row.refund_amount),
                "fee_amount": money(row.fee_amount), "net_amount": money(row.net_amount),
                "currency": row.currency, "issue_type": "NONE"
            })
    batches = pd.DataFrame(batch_rows)
    settlement_lines = pd.DataFrame(line_rows)

    # 5. Processor events (one event per attempt, plus refund events)
    event_rows = []
    attempt_by_order = captured_attempts.set_index("payment_order_id")
    for _, a in attempts.iterrows():
        ev_type = {"CAPTURED":"CAPTURE","FAILED":"PAYMENT_FAILED","PENDING":"PAYMENT_PENDING"}[a.status]
        event_rows.append({
            "processor_event_id": f"PEV-{len(event_rows)+1:09d}",
            "payment_attempt_id": a.payment_attempt_id, "payment_order_id": a.payment_order_id,
            "event_type": ev_type, "event_status": a.status,
            "amount": money(a.amount), "currency": a.currency,
            "event_time": a.updated_at, "idempotency_key": f"IDEM-{a.payment_attempt_id}",
            "issue_type": "NONE"
        })
    for _, row in captured_attempts[captured_attempts.refund_amount > 0].iterrows():
        event_rows.append({
            "processor_event_id": f"PEV-{len(event_rows)+1:09d}",
            "payment_attempt_id": row.payment_attempt_id, "payment_order_id": row.payment_order_id,
            "event_type": "REFUND", "event_status": "SUCCEEDED",
            "amount": money(row.refund_amount), "currency": row.currency,
            "event_time": str(pd.Timestamp(attempts.loc[attempts.payment_attempt_id == row.payment_attempt_id, "updated_at"].iloc[0]) + pd.Timedelta(days=1)),
            "idempotency_key": f"REFUND-{row.payment_attempt_id}", "issue_type": "NONE"
        })
    processor_events = pd.DataFrame(event_rows)

    # 6. Bank statement lines: one intended line per batch plus external noise.
    bank_rows = []
    for i, b in batches.iterrows():
        bank_rows.append({
            "bank_statement_line_id": f"BANK-2026-{i+1:06d}",
            "bank_reference": b.settlement_batch_id,
            "amount": money(b.net_amount), "currency": b.currency,
            "transaction_date": str(pd.Timestamp(b.settlement_date) + pd.Timedelta(hours=int(rng.integers(0,24)))),
            "description": f"Processor settlement {b.settlement_batch_id}",
            "issue_type": "NONE"
        })
    # Reserve 6 deliberately labelled cases across first batches.
    anomaly_targets = {}
    if batches_n >= 6:
        anomaly_targets = {
            "fee_amount_difference": 0, "missing_bank_record": 1,
            "currency_mismatch": 2, "duplicate_settlement_line": 3,
            "refund_before_capture": 4, "duplicate_processor_event": 5
        }
        bank_rows[0]["amount"] = money(bank_rows[0]["amount"] - 3.50)
        bank_rows[0]["issue_type"] = "fee_amount_difference"
        bank_rows[2]["currency"] = "USD" if bank_rows[2]["currency"] != "USD" else "EUR"
        bank_rows[2]["issue_type"] = "currency_mismatch"
        bank_rows[1]["issue_type"] = "missing_bank_record"
        bank_rows.pop(1)
        # Duplicate settlement line with a unique PK, same underlying payment.
        target_batch = batch_ids[3]
        original = settlement_lines[settlement_lines.settlement_batch_id == target_batch].iloc[0].to_dict()
        original["settlement_line_id"] = f"STL-{target_batch}-DUP001"
        original["issue_type"] = "duplicate_settlement_line"
        settlement_lines = pd.concat([settlement_lines, pd.DataFrame([original])], ignore_index=True)
        # Refund event timestamp intentionally earlier than capture.
        refund_events = processor_events[processor_events.event_type == "REFUND"]
        if len(refund_events):
            ridx = refund_events.index[0]
            capture = processor_events[(processor_events.payment_order_id == processor_events.loc[ridx,"payment_order_id"]) & (processor_events.event_type == "CAPTURE")].iloc[0]
            processor_events.loc[ridx, "event_time"] = str(pd.Timestamp(capture.event_time) - pd.Timedelta(hours=1))
            processor_events.loc[ridx, "issue_type"] = "refund_before_capture"
        # Duplicate webhook: new event PK, repeated idempotency key and payload.
        dup_source = processor_events[processor_events.event_type == "CAPTURE"].iloc[0].to_dict()
        dup_source["processor_event_id"] = f"PEV-{len(processor_events)+1:09d}"
        dup_source["issue_type"] = "duplicate_processor_event"
        processor_events = pd.concat([processor_events, pd.DataFrame([dup_source])], ignore_index=True)

    # Fill remaining bank lines with external unmatched bank activity.
    bank_seq = batches_n + 1
    while len(bank_rows) < bank_n:
        j = bank_seq
        bank_seq += 1
        bank_rows.append({
            "bank_statement_line_id": f"BANK-2026-{j:06d}",
            "bank_reference": f"EXT-{j:08d}",
            "amount": money(rng.uniform(100, 50000)),
            "currency": str(rng.choice(CURRENCIES)),
            "transaction_date": str(now + pd.Timedelta(days=int(rng.integers(0,31)), hours=int(rng.integers(0,24)))),
            "description": "Unidentified bank credit/debit",
            "issue_type": "unmatched_external_bank_line"
        })
    bank_lines = pd.DataFrame(bank_rows[:bank_n])

    # 7. Reconciliation, exceptions and ground truth
    matches, exceptions, ground_truth, resolutions = [], [], [], []
    matched_batches = set()
    exc_counter = 0
    for i, b in batches.iterrows():
        bid = b.settlement_batch_id
        target_case = next((k for k,v in anomaly_targets.items() if v == i), None)
        bank = next((x for x in bank_rows if x["bank_reference"] == bid), None)
        decision, reason = "MATCHED", "Reference, amount, currency and date window agree."
        if target_case == "missing_bank_record" or bank is None:
            decision, reason = "UNMATCHED", "No bank statement line found for settlement reference."
        elif target_case == "fee_amount_difference":
            decision, reason = "AMOUNT_MISMATCH", "Bank amount differs from expected net settlement."
        elif target_case == "currency_mismatch":
            decision, reason = "CURRENCY_MISMATCH", "Bank and settlement currencies differ."
        else:
            delta = abs(float(bank["amount"]) - float(b.net_amount))
            date_delta = abs((pd.Timestamp(bank["transaction_date"]) - pd.Timestamp(b.settlement_date)).days)
            if delta > 0.01:
                decision, reason = "AMOUNT_MISMATCH", "Amount outside configured tolerance."
            elif bank["currency"] != b.currency:
                decision, reason = "CURRENCY_MISMATCH", "Currency does not match."
            elif date_delta > 5:
                decision, reason = "DATE_MISMATCH", "Settlement date outside five-day window."
        if decision == "MATCHED":
            matches.append({
                "reconciliation_match_id": f"REC-2026-{len(matches)+1:06d}",
                "settlement_batch_id": bid, "bank_statement_line_id": bank["bank_statement_line_id"],
                "match_status": "MATCHED", "amount_difference": 0.0,
                "match_method": "REFERENCE_AMOUNT_CURRENCY_DATE", "matched_at": "2026-11-01T00:00:00"
            })
            matched_batches.add(bid)
        else:
            exc_counter += 1
            eid = f"EXC-2026-{exc_counter:06d}"
            exceptions.append({
                "exception_id": eid, "settlement_batch_id": bid,
                "bank_statement_line_id": bank["bank_statement_line_id"] if bank else None,
                "exception_type": decision, "severity": "HIGH" if decision in ["UNMATCHED","CURRENCY_MISMATCH"] else "MEDIUM",
                "description": reason, "status": "OPEN", "created_at": "2026-11-01T00:00:00"
            })
            ground_truth.append({"record_id": eid, "entity": "Exception", "issue_type": target_case or decision.lower(),
                                 "expected_decision": decision, "expected_status": "OPEN"})
        if target_case:
            ground_truth.append({"record_id": bid, "entity": "SettlementBatch", "issue_type": target_case,
                                 "expected_decision": decision, "expected_status": "EXCEPTION" if decision != "MATCHED" else "MATCHED"})

    # Orphan bank statement lines must also become exceptions.
    known_refs = set(batches.settlement_batch_id)
    for _, bank in bank_lines.iterrows():
        if bank.bank_reference not in known_refs:
            exc_counter += 1
            eid = f"EXC-2026-{exc_counter:06d}"
            exceptions.append({
                "exception_id": eid, "settlement_batch_id": None,
                "bank_statement_line_id": bank.bank_statement_line_id,
                "exception_type": "UNMATCHED_BANK_LINE", "severity": "LOW",
                "description": "Bank statement line has no corresponding settlement batch.",
                "status": "OPEN", "created_at": "2026-11-01T00:00:00"
            })
            if bank.issue_type != "unmatched_external_bank_line":
                ground_truth.append({"record_id": eid, "entity": "Exception", "issue_type": bank.issue_type,
                                     "expected_decision": "UNMATCHED_BANK_LINE", "expected_status": "OPEN"})

    # Explicit event/line anomaly exceptions.
    for issue, entity, frame, idcol, etype, desc in [
        ("duplicate_settlement_line","SettlementLine",settlement_lines,"settlement_line_id","DUPLICATE_SETTLEMENT_LINE","Duplicate settlement line detected."),
        ("refund_before_capture","ProcessorEvent",processor_events,"processor_event_id","INVALID_EVENT_ORDER","Refund event precedes capture."),
        ("duplicate_processor_event","ProcessorEvent",processor_events,"processor_event_id","DUPLICATE_PROCESSOR_EVENT","Repeated idempotency key detected.")
    ]:
        subset = frame[frame.issue_type == issue]
        for _, row in subset.iterrows():
            exc_counter += 1
            eid = f"EXC-2026-{exc_counter:06d}"
            exceptions.append({
                "exception_id": eid, "settlement_batch_id": row.get("settlement_batch_id"),
                "bank_statement_line_id": None, "exception_type": etype, "severity": "MEDIUM",
                "description": desc, "status": "OPEN", "created_at": "2026-11-01T00:00:00"
            })
            ground_truth.append({"record_id": eid, "entity": entity, "issue_type": issue,
                                 "expected_decision": etype, "expected_status": "OPEN"})

    # Keep at least one example of a documented resolution record; original exception remains immutable.
    for ex in exceptions:
        if ex["exception_type"] == "AMOUNT_MISMATCH":
            resolutions.append({
                "resolution_id": f"RES-2026-{len(resolutions)+1:06d}",
                "exception_id": ex["exception_id"], "resolved_by": "ops.analyst@example.test",
                "resolution_action": "INVESTIGATED_FEE_VARIANCE",
                "resolution_notes": "Variance reviewed against processor fee schedule; retained for audit.",
                "resolved_at": "2026-11-01T00:00:00", "outcome": "ACKNOWLEDGED"
            })
            break

    # Attach explicit issue metadata to event/line rows and write all entities.
    tables = {
        "merchants": merchants, "payment_orders": orders, "payment_attempts": attempts,
        "processor_events": processor_events, "settlement_batches": batches,
        "settlement_lines": settlement_lines, "bank_statement_lines": bank_lines,
        "reconciliation_matches": pd.DataFrame(matches),
        "exceptions": pd.DataFrame(exceptions), "resolutions": pd.DataFrame(resolutions)
    }
    for name, df in tables.items():
        df.to_csv(DATA / f"{name}.csv", index=False)
    pd.DataFrame(ground_truth).to_csv(DATA / "ground_truth.csv", index=False)

    # SQLite database for the API/dashboard.
    db_path = DATA / "reconciliation.db"
    if db_path.exists(): db_path.unlink()
    with sqlite3.connect(db_path) as con:
        for name, df in tables.items():
            df.to_sql(name, con, index=False)
        # Useful indexes
        con.executescript("""
        CREATE INDEX idx_orders_merchant ON payment_orders(merchant_id);
        CREATE INDEX idx_attempts_order ON payment_attempts(payment_order_id);
        CREATE INDEX idx_events_attempt ON processor_events(payment_attempt_id);
        CREATE INDEX idx_lines_batch ON settlement_lines(settlement_batch_id);
        CREATE INDEX idx_bank_ref ON bank_statement_lines(bank_reference);
        CREATE INDEX idx_exceptions_status ON exceptions(status);
        """)
        # Portable SQLite SQL seed dump, in addition to CSV exports and the DB file.
        with (DATA / "reconciliation_seed.sql").open("w", encoding="utf-8") as sql_file:
            for statement in con.iterdump():
                sql_file.write(statement + "\n")

    # Data dictionary + validation report + source manifest.
    dictionary = {
        "Merchant": {"merchant_id":"TEXT PK, MER-#######","merchant_name":"TEXT required","country":"IN/US/GB/DE","default_currency":"INR/USD/EUR/GBP","status":"ACTIVE/SUSPENDED","created_at":"ISO date"},
        "PaymentOrder": {"payment_order_id":"TEXT PK, PAY-2026-########","merchant_id":"FK Merchant","customer_reference":"unique TEXT","amount":"DECIMAL, major currency units, >0","currency":"ISO-like currency code","status":"CAPTURED/FAILED/PENDING","created_at":"ISO datetime","updated_at":"ISO datetime"},
        "PaymentAttempt": {"payment_attempt_id":"TEXT PK, ATT-<payment-id>-##","payment_order_id":"FK PaymentOrder","attempt_number":"positive integer","processor":"processor enum","status":"CAPTURED/FAILED/PENDING","amount":"DECIMAL","currency":"currency code","created_at":"ISO datetime","updated_at":"ISO datetime"},
        "ProcessorEvent": {"processor_event_id":"TEXT PK","payment_attempt_id":"FK PaymentAttempt","payment_order_id":"FK PaymentOrder","event_type":"CAPTURE/PAYMENT_FAILED/PAYMENT_PENDING/REFUND","event_status":"event status","amount":"DECIMAL","currency":"currency code","event_time":"ISO datetime","idempotency_key":"TEXT; duplicate values deliberately labelled","issue_type":"NONE or anomaly label"},
        "SettlementBatch": {"settlement_batch_id":"TEXT PK, SET-2026-######","processor":"TEXT","currency":"currency code","gross_amount/refund_amount/fee_amount/net_amount":"DECIMAL major units","status":"SETTLED","created_at/settlement_date":"ISO datetime"},
        "SettlementLine": {"settlement_line_id":"TEXT PK, STL-<batch-id>-###","settlement_batch_id":"FK SettlementBatch","payment_order_id":"FK PaymentOrder","payment_attempt_id":"FK PaymentAttempt","gross_amount/refund_amount/fee_amount/net_amount":"DECIMAL","currency":"currency code","issue_type":"NONE or anomaly label"},
        "BankStatementLine": {"bank_statement_line_id":"TEXT PK, BANK-2026-######","bank_reference":"batch reference or external ref","amount":"DECIMAL","currency":"currency code","transaction_date":"ISO datetime","description":"TEXT","issue_type":"NONE or anomaly label"},
        "ReconciliationMatch": {"reconciliation_match_id":"TEXT PK, REC-2026-######","settlement_batch_id":"FK SettlementBatch","bank_statement_line_id":"FK BankStatementLine","match_status":"MATCHED","amount_difference":"DECIMAL","match_method":"TEXT","matched_at":"ISO datetime"},
        "Exception": {"exception_id":"TEXT PK","settlement_batch_id":"optional FK SettlementBatch","bank_statement_line_id":"optional FK BankStatementLine","exception_type":"enum","severity":"HIGH/MEDIUM/LOW","description":"TEXT","status":"OPEN/RESOLVED","created_at":"ISO datetime"},
        "Resolution": {"resolution_id":"TEXT PK","exception_id":"FK Exception","resolved_by":"TEXT","resolution_action":"TEXT","resolution_notes":"TEXT","resolved_at":"ISO datetime","outcome":"TEXT"}
    }
    (ROOT / "data_dictionary.json").write_text(json.dumps(dictionary, indent=2))
    counts = {k: int(len(v)) for k,v in tables.items()}
    checks = {
        "seed": seed, "scale": scale, "row_counts": counts,
        "unique_primary_keys": {k: bool(v.iloc[:,0].is_unique) for k,v in tables.items() if len(v)},
        "foreign_key_checks": "See validation.py; all generated parent references are expected to be valid.",
        "ground_truth_rows": len(ground_truth),
        "sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(DATA.glob("*.csv"))}
    }
    (DATA / "validation_summary.json").write_text(json.dumps(checks, indent=2))
    manifest = {"provenance":"Fully synthetic data generated locally; no personal or confidential data used.",
                "generator":"generate.py", "seed":seed, "scale":scale, "generated_at_utc":"2026-11-01T00:00:00Z"}
    (DATA / "source_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(checks, indent=2))
    print(f"\nCreated database: {db_path}")
    print("Next: python validate.py")

def main():
    p = argparse.ArgumentParser(description="Generate synthetic payment reconciliation data.")
    p.add_argument("--scale", choices=["starter","full"], default="starter")
    p.add_argument("--seed", type=int, default=SEED_DEFAULT)
    args = p.parse_args()
    make_dataset(args.scale, args.seed)

if __name__ == "__main__":
    main()
