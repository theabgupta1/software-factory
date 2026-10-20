# ADR 0006: One delivery path for messages to running agents

Status: accepted (2026-10)

## Context
Users can message any agent mid-run. A running subagent can be reached two ways: the PostToolUse hook (context added
after its next tool call) or `SendMessage`/`$.session.send` (delivered at its next turn boundary).

## Decision
Running agents receive messages only through the hook; agents that are not running get them folded into their next
prompt; the orchestrator gets them in `notes`. Every delivery appends a receipt. The console and the mod only write
messages.

## Consequences
- No double delivery, one receipt per message, the same behaviour from the console, the CLI and the mod.
- An agent inside one very long tool call receives the message when that call returns.
