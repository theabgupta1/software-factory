# ADR 0005: factory-live is a separate plugin, without autopilot

Status: accepted (2026-10)

## Context
The mod API gives Claude Code panes, bands, status lines and notifications, but it is early access and changes
between releases. Builds without it must keep working.

## Decision
Ship the in-terminal experience as `factory-live`, a second plugin that only reads the engine's files and acts through
`factoryctl tell` or by filling the prompt with a command. Leave out autopilot (the mod driving jobs with
`$.agent.spawn`): it would be a second executor beside the skill, with less visibility, on an unstable API.

## Consequences
- The core factory has no dependency on the mod API; the installer adds the mod only where it validates.
- The board can never change a job's course without the user running a command.
- Autopilot remains possible later on the same engine (ADR 0001).
