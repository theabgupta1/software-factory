"""Load raw/orders.csv into warehouse.db, latest updated_at per order wins; safe to re-run."""
import csv
import sqlite3

db = sqlite3.connect("warehouse.db")
db.execute("""CREATE TABLE IF NOT EXISTS fct_orders (order_id INTEGER PRIMARY KEY, customer_id INTEGER,
              status TEXT, total_cents INTEGER, updated_at TEXT)""")
with open("raw/orders.csv", newline="") as fh:
    rows = list(csv.DictReader(fh))
with db:
    for r in rows:
        db.execute("""INSERT INTO fct_orders VALUES (?, ?, ?, ?, ?)
                      ON CONFLICT(order_id) DO UPDATE SET customer_id = excluded.customer_id, status = excluded.status,
                        total_cents = excluded.total_cents, updated_at = excluded.updated_at
                      WHERE excluded.updated_at > fct_orders.updated_at""",
                   (int(r["order_id"]), int(r["customer_id"]), r["status"], int(r["total_cents"]), r["updated_at"]))
