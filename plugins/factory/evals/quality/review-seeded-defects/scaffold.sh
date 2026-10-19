#!/usr/bin/env bash
# A small Flask-style app; the last commit adds an invoices endpoint with four seeded defects.
set -euo pipefail
git init -q -b main
git config user.email eval@example.invalid
git config user.name eval
cat > app.py <<'PY'
import sqlite3
from flask import Flask, abort, g, jsonify, request

app = Flask(__name__)


def db():
    return sqlite3.connect("app.db")


@app.get("/health")
def health():
    return {"ok": True}
PY
git add -A && git commit -qm "app skeleton"
# The seeded key is assembled here so no key-shaped string is ever committed to this repository.
printf '\nSTRIPE_SECRET = "sk_%s_%s"\n' live "51Hx9$(printf 'fake%.0s' 1 2 3 4)0000" >> app.py
cat >> app.py <<'PY'


@app.get("/invoices/<invoice_id>")
def invoice(invoice_id):
    user = g.get("user")
    if user is None:
        abort(401)
    row = db().execute(f"SELECT id, owner_id, total FROM invoices WHERE id = '{invoice_id}'").fetchone()
    if row is None:
        abort(404)
    return jsonify({"id": row[0], "total": row[2]})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0")
PY
git add -A && git commit -qm "Add invoice endpoint"
