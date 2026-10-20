# ADR 0002: Plan tasks run sequentially in one worktree

Status: accepted (2026-10)

## Context
Large jobs need decomposition. Running implementers in parallel on one worktree corrupts it (shared index, racing
commits); parallel worktrees need a merge step that can fail late.

## Decision
The architect writes `plan.json`; the engine validates it and runs tasks one at a time in dependency order, in the
job's worktree. Each task must end with a commit carrying `Factory-Task: Tn` and a clean tree; touching paths outside
`owns` is a warning. The builder then integrates and the panel reviews the whole branch.

## Consequences
- Deterministic, debuggable history (one commit per task), no merge surprises.
- Slower than parallel for wide plans. Parallel per-task worktrees with a merge stage (as in Sandcastle's
  parallel planner) is the planned extension; the plan format already carries what it needs.
