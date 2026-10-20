# Testing

```bash
scripts/test.sh                 # every layer that can run on this machine
scripts/test.sh unit e2e        # some layers
scripts/test.sh --matrix        # every layer, then docs/TEST-MATRIX.md from this run
scripts/eval.sh                 # agent-quality evals with real model runs (paid, capped, asks first)
scripts/verify-local.sh [--live] [--evals]   # the whole thing on your machine
```

`scripts/test.sh` needs only Python 3.9+ and git. Layers that need more (Node for JS and browser tests, `npm ci` and
Chromium for Playwright, the `claude` CLI for plugin validation and mod tests, `tsc` for the mod type check, model
credentials for the live layer) are reported **NOT RUN** with the reason, never as passing. Results land in
`test-results/layer-*.json`; the matrix is generated from them.

## Layers

| Layer | Where | What it proves |
|---|---|---|
| static | `tests/static`, plus `claude plugin validate` and syntax checks | Manifests; the skill matches the CLI; every agent file is generated from its source, has all ten contract sections and safe tool lists; contracts registry; guard policy compiles; UI assets exist and build no HTML strings; generated docs are current; no secret-shaped strings anywhere |
| unit | `tests/unit` | Contracts, plans, panels, config, prompts, telemetry, workflows; every hook as a subprocess with real JSON (a 60+ command guard table, write guard, spawn and message delivery, SubagentStop, Stop guard, SessionStart, p95 latency); the console server's API and security model |
| integration | `tests/integration` | Every workflow end to end with scripted agents in temp git repos: review loops, exhausted rounds, blocked builds, rework, escalations, dirty checkout, merge conflicts, invalid outputs, integrity stops, plans (order, trailers, ownership, cycles, approval gates), leases and resume, idempotent `done`, messages, limits, legacy jobs, concurrent ids, backlog, repository customisation |
| e2e | `tests/e2e` | The `factoryctl` command line driven the way the skill drives it, through to a merge and through escalate-and-approve, then the hooks and the console API on the result; one test per subcommand |
| install | `tests/install` | The installer against throwaway Claude homes: dry run, foreign files untouched, idempotence, upgrades, local-edit backups, foreign-folder refusal and restore, corrupt manifest, uninstall, discovery by Claude Code |
| evals | `tests/evals` | The eval suite without a model: schema, scaffolds, checkers, and every regex grader against golden good and bad outputs |
| js | `tests/js` | Console logic in Node: markdown escaping and link safety, formatting, diff parsing, routing, command building |
| mod | `claude plugin test plugins/factory-live`, `tsc` | factory-live against the engine's own test kit on terminal, desktop, VS Code and mobile, with fs, process, agents and the clock answered from memory; strict type check against this build's API |
| ui | `tests/ui` (Playwright, Chromium) | Journeys on the demo factory (needs-you → command, composer, agent chat, orchestrator chat, tabs and DAG by keyboard, artifacts, diff, catalog, settings, shortcuts, live updates); axe (WCAG 2.1 A/AA) on every view in light and dark; no horizontal scroll at 1440, 900 and 390 px; XSS payloads; CSP; offline, loading and not-found states |
| live | `scripts/eval.sh`, `scripts/verify-local.sh --live` | Paid: the agents on real tasks, and one real `/factory` job |

## Scripted agents

`tests/support.py` provides `FakeAgents`, which read the factory prompt header exactly like a real agent (output path,
worktree, round) and write contract-shaped artifacts and commits, and `Driver`, which plays the orchestrator. Override
verdicts per review key and round, headers per step, or whole steps with hooks to create any situation. Tests use a
throwaway `CLAUDE_CONFIG_DIR`.

## Agent-quality evals

`plugins/factory/evals/` holds `claude plugin eval` cases: intake routing, SQL against a fixture with real-data traps,
an idempotent ETL load, a security review of seeded defects, research structure, secure Terraform that never applies,
and a plan that maps every acceptance criterion. Each fixture is built by a scaffold script, and where a result can be
checked by running something, the case ships a checker that the harness session runs itself, so the grade rests on
real output rather than the agent's claim. The eval format has no custom-code graders, so grading is regex on files
and on the trace, `tool_used` process checks, and short LLM rubrics for judgement.

`scripts/eval.sh [--max-cost-usd 10] [--runs 1] [--baseline] [--tag data]` runs the offline checks first, shows what
it will run and asks; `--baseline` adds the no-plugin arm to measure what the plugin adds.

## Adding tests

- A new workflow: `tests/integration/test_workflows.py::OtherWorkflows` runs every shipped workflow to its terminal
  state; add focused tests for its special stages.
- A new agent: the static suite checks it automatically once it is in `contracts/agents.json` and `agents-src/`; add an
  eval case if its output can be checked.
- A new console view: add it to `VIEWS` in `tests/ui/helpers.js` (axe, widths and errors then cover it) and a journey.
- A new command: a `test_cmd_<name>` in `tests/e2e/test_cli_commands.py`; the matrix maps it by name.
