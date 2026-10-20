---
max_turns: 30
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Skill, Agent, TodoWrite]
expected_outcome: etl.py loads 4 distinct orders with the latest status, twice with the same result; check.py prints CHECK PASSED.
---

Use the factory:data-engineer agent for this. The current directory has raw/orders.csv and SPEC.md. Have it write etl.py as SPEC.md describes. When it is done, run `python3 check.py` yourself and report exactly what it printed.
