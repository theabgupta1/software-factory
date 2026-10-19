import csv, sqlite3
db = sqlite3.connect("warehouse.db")
db.execute("CREATE TABLE IF NOT EXISTS fct_orders (order_id INTEGER, customer_id INTEGER, status TEXT, total_cents INTEGER, updated_at TEXT)")
for r in csv.DictReader(open("raw/orders.csv")):
    db.execute("INSERT INTO fct_orders VALUES (?, ?, ?, ?, ?)", (r["order_id"], r["customer_id"], r["status"], r["total_cents"], r["updated_at"]))
db.commit()
