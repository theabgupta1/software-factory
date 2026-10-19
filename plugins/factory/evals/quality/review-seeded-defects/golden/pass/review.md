VERDICT: CHANGES

## Findings

1. **High: SQL injection** in `invoice()`: the query interpolates `invoice_id` with an f-string. Use a parameterized query (`WHERE id = ?`).
2. **High: hard-coded secret**: `STRIPE_SECRET` is a live Stripe key committed to the repository. Rotate it and read it from the environment.
3. **High: missing authorization (IDOR)**: any signed-in user can read any invoice; `owner_id` is never compared with the current user.
4. **Medium: debug mode** on `0.0.0.0` exposes the Werkzeug debugger (remote code execution) if this runs anywhere reachable.
