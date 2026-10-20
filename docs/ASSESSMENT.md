# Assessment of the original factory

The starting point: the ZazenCodes software factory, snapshot in [`upstream/`](../upstream/) (28 files, read in full
before any change).

## Shape

- `.claude/skills/factory/SKILL.md` — the orchestrator: about 220 lines of prose the model executes, including
  writing `job.json` by hand.
- Seven subagents: `spec-writer`, `builder`, `reviewer-security`, `reviewer-ux`, `reviewer-ui`, `reviewer-code`,
  `approver`.
- `factory/dashboard.html` — a 600-line vanilla JS Kanban board polling `python3 -m http.server` directory listings.
- `factory/backlog.md` and `factory/jobs/<id>/{job.json, spec.md, build.md, round-N/review-*.md, decision.md}`.
- One loop: spec → build → four parallel reviews (wait for all; the builder resumed at most twice) → approver →
  local merge to `main`, or needs-human.
- Commands: `/factory <feature>`, `next`, `approve <id>`, `rework <id> <note>`. No tests, hooks, scripts or installer.

## Strengths kept

| Strength | Where it lives now |
|---|---|
| File-based contracts with machine-checkable first lines (`VERDICT:`, `STATUS:`, `APPROVE`/`ESCALATE`) | `contracts/artifacts.json`, checked by the engine and the SubagentStop hook |
| One writer of job state | The engine is the only writer of `job.json` (atomic, locked) |
| One worktree and branch per job, parallel sessions | Unchanged, plus leases so two sessions never drive one job |
| Wait-for-all review rounds, bounded | Panels wait for every member; `max_review_rounds` |
| Builder resumed by name to keep context | `message` actions, with a continuation brief when its session is gone |
| The approver's escalation list | Kept and extended (production data, `always_escalate` paths, infra always) |
| Never push | Engine has no push path; the guard denies it |
| Board shows only real state, escapes markdown | Console reads engine state; escape-first renderer; XSS tests |
| Repo-agnostic agents that read AGENTS.md / CLAUDE.md | All 25 agents; plus `.factory/standards.md` and per-agent prompts |
| Per-job ports and browser sessions | Ports assigned by the engine from `ports.base` |

## Limitations found, and what was done

| # | Limitation | Change |
|---|---|---|
| 1 | One workflow (web features) | 11 workflows: feature, fix, quick, research, review, data, infra, refactor, perf, security, docs |
| 2 | Orchestration as prose; hand-written JSON; not resumable; no transition validation | Deterministic engine with a one-action protocol, idempotent `done`, leases and `resume` (ADR 0001) |
| 3 | All four reviewers on every change | Dynamic panel: code + security always, UX/UI on UI changes, performance for perf/data, with reasons |
| 4 | No history, timestamps, durations or token usage | `events.jsonl`, `telemetry.jsonl` (tokens deduplicated by message id), timeline view, final report |
| 5 | Contracts unenforced; reviewers could edit code unnoticed | Contract checks at two points; reviewer write guard; integrity stop if the branch moves during review |
| 6 | Single Kanban; listing scraping; no graph, timeline, diff, quality, catalog, settings, light theme; drawer without focus trap | Factory Console ([DESIGN-SYSTEM.md](DESIGN-SYSTEM.md)), tested for accessibility, widths and themes |
| 7 | Protocol duplicated in seven files; `main` hard-coded; ports only | One protocol inlined at build time; base branch detected or configured |
| 8 | No guard beyond prompt text; no install or uninstall; no tests | Guard hook with a tested policy; staged, reversible installer; ten test layers and a generated matrix |
| 9 | Native features unused: plugins, hooks, `claude plugin validate/eval/test`, per-agent model override, SendMessage resume | All used: two plugins in a marketplace, five hook events, validation in CI-style tests, an eval suite, the mod test kit, `models` overrides, message-based resume |

## Native versus custom

Native Claude Code: the skill, subagents, the Agent and SendMessage tools, hooks, plugins and marketplaces,
`claude plugin validate/eval/test`, the mod API (factory-live). Custom: the engine and its state files, the worktree
scheme, the verdict protocol, the console.

## Baseline fixtures

The two example jobs in the snapshot — `001-blog-reading-time` (escalated, then approved) and `002-not-found-page`
(auto-merged) — are read unchanged by the engine and the console as v1, read-only jobs, and are part of the tests.
