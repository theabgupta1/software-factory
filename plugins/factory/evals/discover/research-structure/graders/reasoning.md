---
type: llm
focus: { source: file, path: research.md }
---

PASS if the research weighs both options against concurrency (SQLite has a single writer even in WAL mode), durability and operational cost, makes a recommendation that follows from those points and states its confidence, and marks which statements are verified facts and which are assumptions.
FAIL if it recommends one option without weighing the other, states the single-writer limit wrongly, or presents guesses as facts.
