# ADR 0001: Deterministic engine, LLM executor

Status: accepted (2026-10)

## Context
The original factory kept its orchestration rules in ~220 lines of skill prose that the model executed, and wrote
`job.json` by hand. Rules were untestable, an interrupted session could not be resumed, and nothing checked that a
transition was legal or an output was valid.

## Decision
Move every rule into `factoryctl`, a Python standard-library engine. The `/factory` skill keeps intake (choosing a
workflow and size) and a loop: ask the engine for one action, execute it literally, report the result. Subagents
cannot spawn subagents, so the orchestrator stays the main session; it just stops deciding.

## Consequences
- Every workflow, failure and resume path is tested with scripted agents and no model (`tests/integration`).
- Sessions can crash or hand over; `resume` reconciles from files.
- The skill is short and hard to misread; the engine's JSON is the contract between them.
- A different executor (a container runner, a mod) can drive the same engine.
- Cost: the orchestrator calls a CLI between steps (milliseconds); the engine is code to maintain.
