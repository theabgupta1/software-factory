#!/usr/bin/env bash
# A tiny two-tier app, a spec with four acceptance criteria, and a plan checker mirroring the engine's rules.
set -euo pipefail
git init -q -b main
mkdir -p api web/src
printf 'def create_user(email):\n    return {"email": email}\n' > api/users.py
printf 'export function SignupForm() { return null }\n' > web/src/SignupForm.tsx
cat > spec.md <<'MD'
STATUS: READY

# Rate limit signup

## Requirements
- R1: Limit signup attempts per client IP.

## Acceptance criteria
- AC1: The API rejects the 6th signup attempt from one IP within 10 minutes with HTTP 429.
- AC2: The 429 response carries a Retry-After header.
- AC3: The signup form shows "Too many attempts. Try again in 10 minutes." on a 429.
- AC4: Tests cover AC1-AC3.
MD
cat > check.py <<'PY'
"""Checks plan.json against the factory engine's plan rules."""
import json, re, sys
IMPLEMENTERS = {"builder", "frontend-engineer", "backend-engineer", "sql-engineer", "data-engineer",
                "iac-engineer", "platform-engineer", "test-engineer", "docs-engineer", "debugger", "performance-engineer",
                "data-quality-engineer"}
def fail(msg):
    print(f"CHECK FAILED: {msg}"); sys.exit(1)
try:
    plan = json.load(open("plan.json"))
except Exception as exc:
    fail(f"plan.json is not valid JSON ({exc})")
tasks = plan.get("tasks") if isinstance(plan, dict) else None
if not isinstance(tasks, list) or not 1 <= len(tasks) <= 12:
    fail("plan.json needs a 'tasks' list of 1-12 tasks")
ids = [t.get("id") for t in tasks]
if len(set(ids)) != len(ids) or not all(isinstance(i, str) and re.fullmatch(r"T[1-9]\d?", i) for i in ids):
    fail(f"task ids must be unique T1..T99, got {ids}")
for t in tasks:
    if t.get("agent") not in IMPLEMENTERS:
        fail(f"{t.get('id')}: agent {t.get('agent')!r} cannot implement")
    if not t.get("owns"):
        fail(f"{t.get('id')}: owns must list the paths the task changes")
    for d in t.get("depends_on") or []:
        if d not in ids:
            fail(f"{t['id']} depends on unknown task {d}")
graph = {t["id"]: set(t.get("depends_on") or []) for t in tasks}
done = set()
while len(done) < len(graph):
    ready = [k for k, v in graph.items() if k not in done and v <= done]
    if not ready:
        fail("the dependencies form a cycle")
    done.update(ready)
covered = {a for t in tasks for a in (t.get("acceptance") or [])}
missing = [a for a in ("AC1", "AC2", "AC3", "AC4") if a not in covered]
if missing:
    fail(f"no task owns {', '.join(missing)}")
print("CHECK PASSED")
PY
git add -A && git -c user.email=eval@example.invalid -c user.name=eval commit -qm init
