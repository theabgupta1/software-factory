---
max_turns: 30
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Skill, Agent, TodoWrite]
expected_outcome: answer.sql returns Ada 150.00, Brook 120.00, Cyd 90.00 and check.py prints CHECK PASSED.
---

Use the factory:sql-engineer agent for this. In the current directory, shop.db is a SQLite database and QUESTION.md asks a question about it. Have it write the query to answer.sql (a single read-only SELECT in SQLite's dialect). When it is done, run `python3 check.py` yourself and report exactly what it printed.
