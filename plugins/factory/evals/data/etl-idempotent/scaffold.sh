#!/usr/bin/env bash
# Builds raw/orders.csv (duplicates and a late update), SPEC.md and check.py.
set -euo pipefail
mkdir -p raw
cat > raw/orders.csv <<'CSV'
order_id,customer_id,status,total_cents,updated_at
1001,1,pending,5000,2026-10-01T09:00:00Z
1002,2,paid,12000,2026-10-01T10:00:00Z
1001,1,paid,5000,2026-10-01T11:00:00Z
1003,3,paid,900,2026-10-02T08:00:00Z
1002,2,paid,12000,2026-10-01T10:00:00Z
1004,1,refunded,700,2026-10-03T12:00:00Z
1003,3,refunded,900,2026-10-01T07:00:00Z
CSV
cat > SPEC.md <<'MD'
# Load orders

Write `etl.py`: `python3 etl.py` loads `raw/orders.csv` into SQLite `warehouse.db`, table `fct_orders`
(`order_id` INTEGER PRIMARY KEY, `customer_id`, `status`, `total_cents`, `updated_at`).

- One row per `order_id`: the version with the latest `updated_at` wins (rows can arrive out of order and twice).
- Running it again on the same input changes nothing (idempotent): no duplicates, same values.
- Standard library only.
MD
cat > check.py <<'PY'
"""Runs etl.py twice and checks the table it builds."""
import sqlite3, subprocess, sys, os
if os.path.exists("warehouse.db"):
    os.remove("warehouse.db")
def load():
    r = subprocess.run([sys.executable, "etl.py"], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        sys.exit(print(f"CHECK FAILED: etl.py exited {r.returncode}: {r.stderr.strip()[:300]}") or 1)
    db = sqlite3.connect("warehouse.db")
    return sorted(db.execute("SELECT order_id, customer_id, status, total_cents FROM fct_orders").fetchall())
first, second = load(), load()
expected = [(1001, 1, "paid", 5000), (1002, 2, "paid", 12000), (1003, 3, "paid", 900), (1004, 1, "refunded", 700)]
if first != expected:
    sys.exit(print(f"CHECK FAILED: first load gave {first}, expected {expected}") or 1)
if second != first:
    sys.exit(print(f"CHECK FAILED: a second run changed the table: {second}") or 1)
print("CHECK PASSED")
PY
