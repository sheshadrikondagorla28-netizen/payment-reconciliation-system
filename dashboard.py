from pathlib import Path
import sqlite3
import pandas as pd
import streamlit as st

DB = Path(__file__).resolve().parent / "data" / "reconciliation.db"
st.set_page_config(page_title="Payment Reconciliation", page_icon="💳", layout="wide")
st.title("💳 Payment Reconciliation Dashboard")
st.caption("Synthetic processor-to-bank settlement reconciliation")

if not DB.exists():
    st.error("Database not found. In the project terminal run: python generate.py --scale starter")
    st.stop()

def read(sql, params=()):
    with sqlite3.connect(DB) as con:
        return pd.read_sql_query(sql, con, params=params)

metrics = read("""
SELECT
 (SELECT COUNT(*) FROM merchants) merchants,
 (SELECT COUNT(*) FROM payment_orders) payment_orders,
 (SELECT COUNT(*) FROM payment_attempts) payment_attempts,
 (SELECT COUNT(*) FROM settlement_batches) settlement_batches,
 (SELECT COUNT(*) FROM bank_statement_lines) bank_lines,
 (SELECT COUNT(*) FROM reconciliation_matches) matched,
 (SELECT COUNT(*) FROM exceptions WHERE status='OPEN') open_exceptions,
 (SELECT ROUND(COALESCE(SUM(fee_amount),0),2) FROM settlement_batches) fees
""").iloc[0]
a,b,c,d = st.columns(4)
a.metric("Merchants", f"{int(metrics.merchants):,}")
b.metric("Payment orders", f"{int(metrics.payment_orders):,}")
c.metric("Settlement batches", f"{int(metrics.settlement_batches):,}")
d.metric("Bank statement lines", f"{int(metrics.bank_lines):,}")
e,f,g,h = st.columns(4)
e.metric("Matched batches", f"{int(metrics.matched):,}")
f.metric("Open exceptions", f"{int(metrics.open_exceptions):,}")
g.metric("Reconciliation rate", f"{(100*metrics.matched/metrics.settlement_batches if metrics.settlement_batches else 0):.2f}%")
h.metric("Total fees", f"{metrics.fees:,.2f}")

left,right = st.columns(2)
with left:
    st.subheader("Exceptions by type")
    exc = read("SELECT exception_type, COUNT(*) count FROM exceptions GROUP BY exception_type ORDER BY count DESC")
    if len(exc): st.bar_chart(exc.set_index("exception_type"))
    else: st.info("No exceptions.")
with right:
    st.subheader("Settlement batch status")
    st.write({"Matched":int(metrics.matched),
              "Not matched / exception":int(metrics.settlement_batches-metrics.matched)})

st.subheader("Exception queue")
status = st.selectbox("Filter status", ["OPEN","RESOLVED","ALL"])
sql = "SELECT * FROM exceptions" + ("" if status=="ALL" else " WHERE status=?") + " ORDER BY exception_id DESC LIMIT 500"
exceptions = read(sql, () if status=="ALL" else (status,))
st.dataframe(exceptions, use_container_width=True, hide_index=True)

st.subheader("Resolve an exception")
if len(exceptions):
    open_ids = read("SELECT exception_id FROM exceptions WHERE status='OPEN' ORDER BY exception_id")
    if len(open_ids):
        chosen = st.selectbox("Exception ID", open_ids.exception_id.tolist())
        who = st.text_input("Analyst", "operations.analyst")
        action = st.text_input("Resolution action", "MANUAL_REVIEW")
        notes = st.text_area("Resolution notes", "Investigated source records and documented the outcome.")
        outcome = st.selectbox("Outcome", ["RESOLVED","ACKNOWLEDGED","ESCALATED"])
        if st.button("Save resolution"):
            with sqlite3.connect(DB) as con:
                rid = f"RES-UI-{con.execute('SELECT COUNT(*) FROM resolutions').fetchone()[0]+1:06d}"
                con.execute("""INSERT INTO resolutions VALUES (?,?,?,?,?,?,?)""",
                    (rid,chosen,who,action,notes,pd.Timestamp.now().isoformat(),outcome))
                if outcome == "RESOLVED":
                    con.execute("UPDATE exceptions SET status='RESOLVED' WHERE exception_id=?", (chosen,))
                con.commit()
            st.success(f"Saved {rid}. Refresh the page to see the updated status.")
    else:
        st.info("There are no open exceptions to resolve.")
else:
    st.info("No exceptions found.")
