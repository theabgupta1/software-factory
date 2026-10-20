# Migrating from the original factory

If you used the ZazenCodes factory (`.claude/skills/factory`, seven agents, `factory/dashboard.html`), this is what
changes. Your old jobs keep working as read-only history.

## Commands

| Original | Now | Notes |
|---|---|---|
| `/factory <feature>` | `/factory <request>` | The orchestrator picks one of 11 workflows and a size, and says why |
| `/factory next` | `/factory next [--all]` | Backlog moves to `.factory/backlog.md` (the old `factory/backlog.md` is read if the new one does not exist) |
| `/factory approve <id>` | same | Also approves plans (`/factory build <id>`) |
| `/factory rework <id> <note>` | same | The note is saved as `rework-<n>.md` and given to the builder first |
| open `factory/dashboard.html` | `/factory dashboard` | A local console with a JSON API instead of directory listings |
| — | `/factory status`, `tell`, `resume`, `cancel`, `report`, `doctor`, `init` | New |

## Agents

Agent names are internal (you do not type them), but if you referenced them:

| Original | Now |
|---|---|
| `spec-writer` | `product-analyst` |
| `builder` | `builder` (plus integrate, fix, quick, infra, refactor, perf and docs modes) |
| `reviewer-security` | `security-engineer` |
| `reviewer-ux` | `ux-reviewer` |
| `reviewer-ui` | `ui-reviewer` |
| `reviewer-code` | `code-reviewer` |
| `approver` | `approver` (same escalation list, extended with production data and `always_escalate` paths) |

Eighteen agents are new; see [AGENTS.md](AGENTS.md). The shared "where you work" rules that were copied into every
agent now live once in `plugins/factory/protocol/protocol.md` and are inlined at build time.

## Behaviour

- All four reviewers no longer run on every change: code and security always, UX and UI only when the UI changes,
  performance for perf and data work.
- Review rounds: still up to 3 (configurable with `limits.max_review_rounds`), then the job escalates.
- Orchestration rules moved from the skill into `factoryctl`; jobs are resumable (`/factory resume`).
- `main` is no longer assumed: the base branch is detected (or set in `.factory/config.json`).
- Contracts are checked: a malformed `build.md` or review is rejected and re-issued with the problems listed.
- The factory still never pushes, and approved jobs still merge locally by default.

## State

| Original | Now |
|---|---|
| `factory/jobs/<id>/job.json` (hand-written) | `.factory/jobs/<id>/job.json` (schema 2, engine-written) + `events.jsonl`, `messages.jsonl`, `telemetry.jsonl` |
| `factory/jobs/<id>/round-N/review-*.md` | same names under `.factory/jobs/<id>/round-N/` |
| tracked in git | `.factory/` is excluded through `.git/info/exclude` |

Old jobs under `factory/jobs/` appear in the console and `/factory status` with a "v1 · read-only" badge. New job
numbers continue after the old ones.

## Steps

1. Install the new factory (`scripts/install.sh`, see [INSTALL.md](INSTALL.md)).
2. Remove the old project copies so there is only one `/factory`: `.claude/skills/factory/` and the seven
   `.claude/agents/{spec-writer,builder,reviewer-*,approver}.md` files in your repository. If you had copied the old
   skill to `~/.claude/skills/factory`, the installer stops and asks you to re-run with `--replace-foreign` (it backs
   the old copy up, and uninstall restores it).
3. Run `/factory init` in the repository. Keep `factory/jobs/` if you want the history visible.
