---
max_turns: 30
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Skill, Agent, TodoWrite]
expected_outcome: review.md says VERDICT CHANGES and names the SQL injection, the hard-coded key, the missing ownership check and the debug mode.
---

Use the factory:security-engineer agent to review the last commit of the git repository in the current directory (`git diff HEAD~1 HEAD`). Have it write its review to review.md following its review contract (first line `VERDICT: PASS` or `VERDICT: CHANGES`, then `## Findings`). Do not change any code.
