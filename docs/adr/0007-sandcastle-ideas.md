# ADR 0007: What was taken from Sandcastle, and what was not

Status: accepted (2026-10)

## Context
[mattpocock/sandcastle](https://github.com/mattpocock/sandcastle) runs coding agents in sandboxes with branch
strategies, prompt templates and worktree hooks. It was reviewed for ideas that fit the factory's architecture.

## Decision
Adopted, in the factory's own terms: `worktree.copy` (copy-on-write) and `worktree.setup`; an engine-computed context
snapshot for reviewers instead of shell expansion in prompts; repository standards and per-agent prompt additions;
`deliver: branch`; stall detection; `--from-issue`; `/factory next --all`. Not adopted: a container per agent
(factory agents are subagents in the user's session and inherit its sandbox and hooks), parallel task branches with a
merge agent (ADR 0002), session forking.

## Consequences
See docs/PLAN.md §10 for the full table. A Sandcastle-based executor that runs issued steps in containers is a natural
follow-up because the engine is executor-agnostic.
