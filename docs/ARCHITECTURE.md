# Architecture

The factory has one rule at its centre: **the model decides what to write, code decides what happens next.** Every
orchestration rule — which stage runs, which reviewers sit on a panel, when a limit stops a job, whether an output is
acceptable, when a branch merges — lives in a deterministic, tested engine. The LLM orchestrator executes one action
at a time and reports back. That is what makes the factory resumable, testable without a model, and predictable.

```text
            you ── /factory <request> ──▶  orchestrator  (your Claude Code session, the /factory skill)
                                              │   ▲
                         factoryctl next/done │   │ one action (JSON)
                                              ▼   │
   .factory/  ◀── reads/writes ──  factoryctl engine (Python stdlib, plugins/factory/lib/factory_core)
      │                                       │
      │                                       ├── git: worktree + branch per job, local merge (never push)
      │                                       └── contracts, workflows, plans, panels, limits, leases
      │
      ├── hooks (PreToolUse guard, PostToolUse spawn record + message delivery, SubagentStop contract check,
      │          Stop continuation guard, SessionStart notice)       ── run inside Claude Code on every tool call
      ├── Factory Console (factoryctl serve: local HTTP + vanilla JS)  ── reads state, posts messages/backlog
      └── factory-live mod (Claude Code mod API)                       ── reads state, tells via factoryctl
```

## Components

| Part | Where | Role |
|---|---|---|
| `/factory` skill | `plugins/factory/skills/factory/` | Intake (workflow, size, one-line why), the action loop, the commands. Short on purpose: no orchestration rules |
| Agents | `plugins/factory/agents/` (generated from `agents-src/` + `protocol/`) | 25 specialists with uniform contracts ([AGENTS.md](AGENTS.md)) |
| Playbooks | `plugins/factory/playbooks/` | Deep, shared practice an agent reads on demand; the roster's `playbooks` field decides who, and the engine names the file in the prompt. `design.md`: references (Mobbin, Dribbble, Awwwards…), design direction, craft bar, motion, 3D, accessibility and performance budgets, critique loop |
| Engine | `plugins/factory/lib/factory_core/` + `bin/factoryctl` | State machine, workflows, plans, panels, contracts, git, store, report |
| Workflows | `plugins/factory/workflows/*.json` | Declarative stage lists ([WORKFLOWS.md](WORKFLOWS.md)) |
| Contracts | `plugins/factory/contracts/artifacts.json`, `agents.json` | Output headers + required sections; the roster (domains, models, hues, review seats) |
| Hooks | `plugins/factory/hooks/` + `factory_core/hooks.py` + `guard-policy.json` | Safety and live delivery inside Claude Code |
| Console | `factory_core/server.py`, `console.py`, `plugins/factory/ui/` | Local web UI ([DESIGN-SYSTEM.md](DESIGN-SYSTEM.md)) |
| factory-live | `plugins/factory-live/` | Status line, attention band, board pane inside Claude Code |

## The action loop

`factoryctl next <job>` returns exactly one action; `factoryctl done <job> <step> --attempt k` records a finished
step and returns the next action. The skill executes actions literally:

| Action | The orchestrator… |
|---|---|
| `spawn` | starts every entry with the Agent tool, all in one message (parallel), copying each prompt verbatim |
| `message` | resumes the named builder with SendMessage; if that fails, spawns the `fallback` entry |
| `inline` | does a small step itself (dependency setup in a new worktree, or the repo's `worktree.setup` commands) |
| `wait` | ends its turn; background agents wake it when they finish |
| `human` | stops and tells the user what to decide and the exact command (`/factory approve …`) |
| `done` | reports the outcome (merged, delivered, cancelled) and the final report |

Every action carries `say` (one line for the user) and `notes` (things the user asked the orchestrator, delivered
here). Step ids are deterministic — `spec`, `build.r2/builder`, `review.r2/security`, `task/T3`, `approve` — and
`done` is idempotent: a repeated `(step, attempt)` is `duplicate`, an older attempt `stale`, a bad artifact
`invalid` (the next `next` re-issues attempt k+1 quoting the problems, up to `max_attempts`).

## Jobs on disk

Each repository that uses the factory gets `.factory/` (added to `.git/info/exclude`, never committed):

```text
.factory/
  config.json            optional settings (README → Configure)
  standards.md           optional: rules every agent follows
  prompts/<agent>.md     optional: extra instructions for one agent
  backlog.md             /factory next works through it
  index.json             one summary per job, rewritten on every change (console, mod and hooks read this)
  agents.json            subagent id → job and step (written by the spawn hook; drives live delivery and Watch)
  jobs/<id>/
    job.json             the job's state (schema 2), written atomically under a file lock
    workflow.json        the workflow as it was when the job started (upgrades never change a running job)
    events.jsonl         append-only history written by the engine
    telemetry.jsonl      append-only records written by hooks (spawns, finishes, tokens, guard decisions)
    messages.jsonl       your messages to agents and their delivery receipts
    spec.md design.md plan.json build.md tasks/T1.md round-1/review-*.md decision.md final-report.md …
```

The job's code lives in a sibling worktree, `<repo>-factory/<id>`, on branch `factory/<id>`. Jobs from the original
factory (`factory/jobs/`) are listed read-only.

## Rules the engine owns

- **Stages and conditions.** A workflow is an ordered list of stages (`setup`, `agent`, `parallel`, `build`, `panel`,
  `gate`, `approve`, `merge`, `deliver`), each with an optional `when` (size, params, policy, plan present).
- **Contracts.** Every artifact has a first-line header (`STATUS: READY|PARTIAL|BLOCKED`, `VERDICT: PASS|CHANGES`,
  `APPROVE|ESCALATE`) and required sections; `done` checks them (BOM, whitespace and bold tolerated). The SubagentStop
  hook runs the same check while the agent is still alive and blocks it once with the problems, so most mistakes are
  fixed in the agent's own session.
- **Plans.** For size M and L the architect writes `plan.json` (tasks with agent, dependencies, owned paths,
  acceptance criteria). The engine validates it (ids, agents, cycles, size), runs tasks one by one in dependency order
  in the job's worktree, requires a commit with `Factory-Task: Tn` per task and a clean tree, and warns when a task
  touches paths it does not own. Then the builder integrates.
- **Review panels.** Code + security always; UX + UI when UI files change or the builder declares UI impact;
  performance for perf/data work. Membership only grows; every seat records why. A round waits for all reviewers;
  any `VERDICT: CHANGES` sends the job back to the builder (resumed by message, or re-spawned with a continuation
  brief if its session is gone); after the last allowed round the job escalates. The engine checks that the branch
  head did not move and the worktree stayed clean during a review round (reviewers must not edit code).
- **Approval and delivery.** The approver escalates money, access, deploy/CI, migrations, production data, new
  dependencies and product decisions; `always_escalate` globs and workflow policy (infra) force escalation. Approved
  work merges locally into the base branch if the main checkout is clean and `git merge-tree` predicts no conflict —
  or, with `deliver: branch`, stops with the branch ready and the push/PR commands for you.
- **Limits.** `max_agent_runs`, `max_review_rounds`, `max_attempts`, `max_parallel`. A limit stops the job for a
  human; nothing continues silently.
- **Integrity.** `job.json`, messages, receipts and the subagent map are sealed with a per-repository key in
  `.git/factory/state.key`; the engine refuses a job whose seal does not match and never delivers an unsealed
  message. The shared `.git/config` and hooks are fingerprinted at job start; a change stops the job
  (`git-changed`). A `.factory/config.json` tracked by git applies only its harmless keys until `factoryctl trust`.
  Details: [SECURITY.md](SECURITY.md).

## Sessions, leases, resume

A job is driven by one Claude session at a time. Its `lease` names the session and an `epoch`. Another session that
calls `next` while the lease is fresh (15 minutes) is refused; after that it may take over only if no agents are
issued — otherwise those agents may still be working, and it must say so explicitly with
`factoryctl resume <job> --force`. A takeover bumps the epoch and reconciles steps left `issued`: if the output is on
disk and valid the step is `recovered`, otherwise `lost` and re-issued. `resume` from the session that already holds
the job takes nothing over (its agents keep running); `next --redeliver` re-sends the exact spawn entries of issued
steps if the orchestrator lost track of them, and refuses entries that changed on disk. A builder whose session is
gone is re-spawned with a continuation brief (build log, branch log, the round's reviews, the rework note) instead
of messaged.

**Rework** restarts at the stage the job stopped in, or at `build` if that comes first — never past a stage that did
not finish — so a blocked spec is specified again, re-planned and re-gated. It starts a new round (rounds are keyed by
number), keeps the rework note until the build completes (retries see it too), recreates a worktree that delivery
removed, and gives the job a fresh agent-run budget.

## Talking to agents mid-run

Messages are written to `messages.jsonl` by the console (`POST /api/jobs/:id/messages`), by `/factory tell`, or by the
factory-live board, addressed to a step, an agent or the orchestrator. Delivery, always recorded with a receipt:

1. **Running agent** — the PostToolUse hook runs inside the agent after each tool call, looks up its step from
   `agents.json`, and hands it the queued messages as `additionalContext` (framed as a message from the user about
   this step).
2. **Agent not running** — the message is folded into the next prompt the engine issues for that agent in this job.
3. **Orchestrator** — returned in the `notes` of the next `next`/`done`; the Stop hook surfaces it if the session is
   about to stop.

The console shows each message as queued → delivered (how and when), next to the agent's live transcript.

## Hooks

All hooks run `python3 hooks/run.py <event>`, exit 0, and do nothing outside factory work (a fast path returns before
importing the engine). Errors are logged to `~/.claude/factory-hooks.log`; the guard alone fails closed (to "ask").

| Event | What it does |
|---|---|
| PreToolUse (Bash, Write, Edit, …) | For factory agents: the command guard (`guard.py`) reads the command like a shell (compound commands, substitutions, `bash -c`, wrappers, global options) and denies push, deploy, publish, apply and destructive commands, changes to your checkout and to the factory's state; asks for risky ones (kubectl/helm/cloud mutations, destructive SQL, `sudo`, pipe-to-shell…). Write/Edit: inside `.factory/` only the agent's own Output; never `.git/`; implementers not in your checkout |
| PostToolUse (Agent) | Records the spawned agent (id, step, transcript path) |
| PostToolUse (other) | Delivers queued messages to the running agent |
| SubagentStop | Contract check on the agent's output (blocks once), usage telemetry deduplicated by message id |
| Stop | Keeps the orchestrator going while `next` still has work for it |
| SessionStart | One line when jobs in this repository need you; exports `FACTORYCTL` and the session id for Bash |

## Console

`factoryctl serve` starts a `ThreadingHTTPServer` on 127.0.0.1 (random port unless `--port`; other hosts need
`--insecure-host`) and prints a launch link carrying a per-run key, which every API request needs. The page is vanilla ES
modules and CSS tokens — no framework, no build step, no CDN. It polls `/api/state` and the open job with ETags.
Reads: state, job detail, diff (`git diff base...branch`, capped), artifacts (confined to the job folder), agent
activity (from transcripts under the Claude projects folder only), catalog, settings. Writes: messages and backlog
items, each requiring the per-run token, a same-origin `Origin`, JSON, and a small body. Security model:
[SECURITY.md](SECURITY.md).

## factory-live

A separate, optional plugin so the core factory works on Claude Code builds without the mod API. It polls
`.factory/index.json` and `agents.json` every two seconds, keeps what it draws in `$.state`, and acts only through
`factoryctl tell` or by filling the prompt with a command for you to run. It never decides anything and never starts
agents. Autopilot (the mod executing actions with `$.agent.spawn`) was considered and left out (ADR 0005).

## Design decisions

See [adr/](adr/): deterministic engine with an LLM executor, sequential plan tasks, skills-dir installation, a
build-free console, factory-live as a separate plugin, one message-delivery path, and what was (and was not) taken
from Sandcastle.
