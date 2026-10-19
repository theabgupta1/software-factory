---
max_turns: 25
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Skill, Agent, TodoWrite]
expected_outcome: research.md with STATUS line, Findings, Recommendation and Sources; facts and assumptions labelled.
---

Use the factory:researcher agent: "For a small web app, should we store sessions in SQLite with WAL mode or in Redis? Consider concurrent readers and writers, durability and operations." Have it write research.md in the current directory following its research contract.
