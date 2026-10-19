---
max_turns: 30
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Skill, Agent, TodoWrite]
expected_outcome: plan.json with 2-6 tasks, valid ids, owners, acyclic dependencies, and AC1-AC4 each mapped; check.py prints CHECK PASSED.
---

Use the factory:architect agent: read spec.md and the code in the current directory, then write plan.json (the factory's task plan: tasks with id, agent, title, depends_on, owns, acceptance) and design.md. When it is done, run `python3 check.py` yourself and report exactly what it printed.
