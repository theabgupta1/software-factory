# Software Factory v2 — Plan

Living plan for turning the ZazenCodes software factory (`upstream/`) into a multi-discipline engineering
factory for Claude Code. Decisions marked **fixed** came from the user.

## Fixed decisions

- Nothing is installed into, and no paid model calls run in, the cloud container this was built in.
  Installation, the live `/factory` smoke run and the agent-quality evals run on the user's machine through
  tested scripts (`scripts/install.sh`, `scripts/verify-local.sh`, `scripts/eval.sh`). In the container they
  are validated against isolated temporary Claude homes only.
- Approved jobs keep auto-merging into the local base branch (never pushed); risky changes always escalate.
- The early-access Claude Code **mod** API is used for the in-Claude-Code experience (`plugins/factory-live`).
- The Factory Console shows every agent with its own illustrated avatar, and the user can **message any agent
  mid-run** to steer it.

## 1. What the original is (baseline)

`upstream/` is an untouched snapshot. One `/factory` skill (≈220 lines of orchestration prose), 7 agents
(`spec-writer`, `builder`, `reviewer-{security,ux,ui,code}`, `approver`), a 600-line polling HTML board, and
markdown files per job. One loop: spec → build → 4 parallel reviews (WAIT FOR ALL, ≤2 builder resumes) →
approver → local merge or needs-human.

Kept: machine-checkable first lines (`VERDICT:`, `STATUS:`, `APPROVE|ESCALATE`), one worktree + branch per job,
WAIT-FOR-ALL review rounds, bounded retries, builder resumed by name, the approver's escalation list,
"never push", repo-agnostic agents that read AGENTS.md/CLAUDE.md, real-state-only board.

Fixed: single workflow; orchestration rules only in prose and hand-written `job.json` (fragile, not
resumable); every reviewer always runs; no history/timestamps/tokens; contracts unenforced; board scrapes
directory listings and has one view; protocol text duplicated in every agent; no guard, tests or installer.

## 2. Architecture: deterministic engine, thin LLM executor

```
/factory <request>
  orchestrator (main session) classifies intent → factoryctl job new --workflow W --size S|M|L --why "…"
  loop:  factoryctl next <job>   →  one action
           spawn   entries[] → Agent tool (all entries in ONE message → parallel)
           message to builder-<job> via SendMessage, with a spawn fallback
           inline  a small instruction for the orchestrator itself (e.g. dependency setup)
           wait    agents still running → end the turn, background agents notify on completion
           human   stop and tell the user (escalation, BLOCKED, limit, plan approval, busy checkout)
           done    merged / delivered / cancelled
         factoryctl done <job> <step> --attempt k --status ok   (per step, as each agent finishes)
```

- `factoryctl` is Python ≥3.9 stdlib only. It owns every deterministic side effect (worktree creation, round
  folders, merge) and every rule (transitions, limits, contracts, panel selection, plan execution).
- **Step ids** are deterministic (`spec`, `build.r1/builder`, `review.r2/security`, `task/T3`, `approve`).
  `done` is idempotent: a repeated (step, attempt) is `duplicate`, an older attempt is `stale`, a bad artifact
  is `invalid` and the next `next` re-issues attempt k+1 with the problems quoted.
- **Lease + epoch**: a job is driven by one session at a time (`job.lease`). `factoryctl resume <job>` takes
  the lease, bumps the epoch, and reconciles steps left `issued` by a dead session: a valid output on disk is
  `recovered`, anything else `lost` and re-issued. A builder whose session is gone is re-spawned with a
  continuation brief instead of messaged.
- **Plans** (size ≥ M): the `architect` writes `plan.json` (tasks with agent, depends_on, owns[], acceptance[]).
  Validated (cycles, unknown agents, missing deps). Tasks run **sequentially** in the job worktree; each task
  commits with a `Factory-Task: T3` trailer; `done` verifies the commit exists, the tree is clean and changed
  paths stay inside `owns`. Parallel per-task worktrees are a later opt-in.
- **Dynamic review panel**: code + security always; UX/UI when UI paths change or the builder declares
  `ui_impact: yes`; performance for perf/data work; membership only grows across rounds; each pick records why.
- **State**: `.factory/` (excluded via `.git/info/exclude`) → `config.json`, `backlog.md`, `index.json`
  (summary of all jobs, rewritten on every change — the console, mod and hooks read one file), and per job
  `job.json` (schema 2, atomic writes under `flock`), `events.jsonl` (factoryctl only), `telemetry.jsonl`
  (hooks only), `messages.jsonl` (user ↔ agent), `workflow.json` (snapshot taken at creation so upgrades never
  change a running job), artifacts. Legacy `factory/jobs/` (v1) is read-only and badged.
- **Cost controls**: `max_agent_runs` (40), `max_review_rounds` (3), per-step `max_attempts` (2),
  `max_parallel` (4), per-agent model overrides through the Agent tool's `model`. A limit → needs-human.

## 3. Talking to agents mid-run

`messages.jsonl` holds user messages addressed to a step/agent or to the orchestrator, plus delivery receipts.
Written by the console (`POST /api/jobs/:id/messages`), by `/factory tell <job> <agent> <text>`, or the mod.

Delivery, in order of preference, always recorded with how and when:
1. **Running subagent** — a `PostToolUse` hook fires inside the agent after its next tool call, finds pending
   messages for its step (agent id ↔ step mapping comes from the `PostToolUse(Agent)` spawn record) and
   returns them as `additionalContext`; the mod can also push them with `$.session.send({to:{agentId}})`.
2. **Agent not running** — the message is folded into the next prompt `factoryctl next` issues for that role
   (next round, rework, resume) and marked delivered-by-prompt.
3. **Orchestrator** — returned in the `notes` of the next `next`/`done` response; the Stop hook surfaces it too.

The console shows each message's state honestly: queued → delivered (via hook/mod/prompt, time) → the agent's
next reply from its transcript.

## 4. Agents (25, uniform contracts)

Languages are not agents; implementers adapt to the repo. Each file has: Purpose & scope · Not responsible for ·
Inputs · Output contract · Working rules · Verification · Security · Errors & escalation · Handoff · Success
criteria. The shared protocol is written once (`plugins/factory/protocol/`) and inlined into each agent by
`scripts/build_agents.py` (a test fails if generated files are stale).

| Domain | Agents |
|---|---|
| Discover & plan | product-analyst (was spec-writer), researcher, codebase-analyst, architect |
| Build | builder (incl. integrate mode), frontend-engineer, backend-engineer, debugger |
| Data | data-architect, sql-engineer, data-engineer (ETL/ELT, Spark, orchestration), data-quality-engineer |
| Infra | cloud-architect, iac-engineer, platform-engineer, sre |
| Quality | test-architect, test-engineer, code-reviewer, security-engineer, performance-engineer, ux-reviewer, ui-reviewer, docs-engineer |
| Deliver | approver |

## 5. Workflows

`feature` (default) · `fix` · `quick` · `research` · `review` (audit) · `data` · `infra` (plan-approval gate,
never applies, always escalates) · `refactor` (characterisation tests first) · `perf` · `security` · `docs`.
Subcommands: `plan`, `build`, `next`, `approve`, `rework`, `resume`, `cancel`, `status`, `tell`, `report`,
`dashboard`, `init`, `doctor`.

## 6. Hooks (classic, Python, <100 ms, exit 0 outside factory context)

PreToolUse guard (deny catastrophic / ask risky commands for `factory:` agents; reviewers may only write under
`.factory/jobs/**`) · PostToolUse(Agent) spawn record · PostToolUse message delivery · SubagentStop (contract
check that blocks once; telemetry deduped by message id) · Stop continuation guard (don't stop while `next`
has work) · SessionStart (one line when jobs need you).

## 7. Factory Console (web) and factory-live (mod)

Console: local stdlib server + vanilla ES modules, no build step. Views: **Command Center** (needs-you queue,
active jobs, task composer, backlog, recent) · **Job workspace** (Overview, **Crew** — agent cards with avatars,
live activity from transcripts, chat to any agent —, Orchestration DAG, Timeline, Artifacts, Changes, Quality) ·
**Catalog** (agents with avatars and contracts, workflows as graphs) · **Settings & integrations**. Light/dark,
keyboard-first, WCAG AA, responsive.

factory-live mod: status-line entry, above-prompt band only when something needs you (Approve / Rework / Open
buttons), live pane with jobs and agents (Watch → jump into an agent's transcript), notifications on state
changes, `/factory-board`, message delivery to running agents; experimental autopilot last.

## 8. Testing and verification

Static (manifests, frontmatter, contracts, generated agents current, `claude plugin validate --strict`),
unit, integration with fake agents in temp git repos (every workflow, failure and resume path), hooks as
subprocesses, server security (Host/Origin/token, traversal, symlinks, XSS), Playwright journeys + axe at three
widths in both themes, install tests against temp homes, mod tests via `claude plugin test`. Paid evals and the
live run are scripts for the user's machine. `scripts/test.sh --matrix` writes `docs/TEST-MATRIX.md` from the
actual run; anything not executed is NOT RUN with a reason.

## 9. Build order

1. Engine + `feature` parity + legacy → 2. agents + skill + hooks → 3. console (Crew/chat first) →
4. remaining workflows → 5. mod → 6. installer, evals, matrix → 7. reviews, docs, push.
