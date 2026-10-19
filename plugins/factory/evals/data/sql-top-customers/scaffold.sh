#!/usr/bin/env bash
# Builds shop.db, QUESTION.md and check.py in the (empty) workspace.
set -euo pipefail
python3 - <<'PY'
import sqlite3
db = sqlite3.connect("shop.db")
db.executescript("""
CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT, country TEXT);
CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id),
                     status TEXT NOT NULL CHECK (status IN ('paid','refunded','pending')),
                     total_cents INTEGER NOT NULL, created_at TEXT NOT NULL);
INSERT INTO customers VALUES (1,'Ada','ada@example.com','GB'),(2,'Brook',NULL,'US'),(3,'Cyd','cyd@example.com','DE'),
                             (4,'Dee','dee@example.com','FR'),(5,'Eli','eli@example.com','US');
INSERT INTO orders VALUES
 (1,1,'paid',10000,'2026-10-05 10:00:00'), (2,1,'paid',5000,'2026-10-31 23:30:00'), (3,1,'refunded',90000,'2026-10-10 09:00:00'),
 (4,2,'paid',12000,'2026-10-02 12:00:00'), (5,2,'pending',50000,'2026-10-03 08:00:00'),
 (6,3,'paid',9000,'2026-10-15 15:00:00'), (7,3,'paid',3000,'2026-11-01 00:00:00'),
 (8,4,'paid',9000,'2026-10-20 18:00:00'),
 (9,5,'paid',20000,'2026-09-30 23:59:59'), (10,5,'paid',100,'2026-10-01 00:00:00');
""")
db.commit()
PY
cat > QUESTION.md <<'MD'
# Question

Which 3 customers brought in the most **paid** revenue in **October 2026**?

- Count only orders with status `paid` (refunded and pending orders do not count).
- October 2026 means `created_at` from `2026-10-01 00:00:00` up to but not including `2026-11-01 00:00:00` (UTC, stored as text).
- Return two columns: `name`, and `revenue` in dollars rounded to 2 decimals.
- Order by revenue descending, then name ascending. Exactly 3 rows.
MD
cat > check.py <<'PY'
"""Runs answer.sql read-only against shop.db and compares it with the expected answer."""
import re, sqlite3, sys
sql = open("answer.sql").read()
if re.search(r"\b(insert|update|delete|drop|alter|create|attach|pragma)\b", sql, re.I):
    sys.exit(print("CHECK FAILED: answer.sql must be a read-only query") or 1)
db = sqlite3.connect("file:shop.db?mode=ro", uri=True)
try:
    rows = [(str(r[0]), round(float(r[1]), 2)) for r in db.execute(sql).fetchall()]
except Exception as exc:
    sys.exit(print(f"CHECK FAILED: the query does not run: {exc}") or 1)
expected = [("Ada", 150.0), ("Brook", 120.0), ("Cyd", 90.0)]
if rows != expected:
    sys.exit(print(f"CHECK FAILED: got {rows}, expected {expected}") or 1)
print("CHECK PASSED")
PY
