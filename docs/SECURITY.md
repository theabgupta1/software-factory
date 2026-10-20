# Security

What the factory can and cannot do on your machine, what protects you, and what it needs.

## What the factory never does

- **Never pushes.** Approved work merges into your local base branch (or, with `deliver: branch`, stops with the
  exact `git push` / `gh pr create` commands for you to run).
- **Never deploys, publishes or applies infrastructure.** Infra jobs run format, validate, lint and plan only, and
  always escalate to you.
- **Never touches production data.** Data agents work on local fixtures and extracts; migrations and backfills are
  written, not run against shared databases.
- **Never decides what needs a human.** Money, access/permissions, deploy and CI configuration, migrations,
  production data, new dependencies, product decisions, and any path in your `always_escalate` list go to you.

These are enforced in three layers: the agents' contracts, the engine (which has no code path that pushes or
applies), and the guard hook below. None of them replaces Claude Code's own permission system or sandbox: the factory
runs inside your session with your permissions, and that remains the outer boundary.

## The guard hook

`PreToolUse` runs before every Bash, Write and Edit call. For factory agents (and, with `guard.scope: "all"` in
`.factory/config.json`, for every session in a factory repository) it applies `plugins/factory/guard-policy.json`:

| Decision | Rules |
|---|---|
| deny | push to any remote · recursive delete of `/`, `~`, `..` or wildcards · `--no-preserve-root` · disk formatting and raw device writes · fork bombs · recursive chmod of root or home · `terraform`/`tofu`/`terragrunt` apply, destroy, import and state changes · `pulumi up`/`destroy` · publishing packages, images and releases |
| ask | `kubectl`/`helm` changes · AWS, Google Cloud and Azure CLI calls that change resources · `DROP`/`TRUNCATE` · commands that mention production · migrations against an explicit database URL · `curl … \| sh` · `sudo` · reading credential files · changing global git config |

It also limits where agents write: reviewers only under `.factory/jobs/`, implementers only in their job's worktree
(not your main checkout). The patterns are searched in the whole command, so compound commands (`a && git push`) are
caught. The guard is defence in depth, not a sandbox: a determined process can always find an unlisted way to do
something. If the guard itself fails, it falls back to "ask" for factory agents; every other hook fails open and logs
to `~/.claude/factory-hooks.log`. Run unattended jobs with Claude Code's own sandbox enabled.

## The Factory Console

`factoryctl serve` binds to `127.0.0.1` (a warning is printed for any other address) and:

- refuses requests whose `Host` is not the loopback address and port it serves (stops DNS rebinding; 421);
- requires, for the two write endpoints (`POST /api/jobs/:id/messages`, `POST /api/backlog`), a per-run random token
  that is only in the served page, a same-origin `Origin`, a JSON content type and a body under 64 KB (403/413);
- sends `Content-Security-Policy: default-src 'self'` (no inline script, no remote code), `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`;
- serves static files only from `ui/`, artifacts only from the job's folder (symlinks and `..` refused, 1 MB cap),
  diffs capped at 1.5 MB, and agent transcripts only from under the Claude projects folder;
- renders agent and user text as text: the UI builds the DOM with element APIs and `textContent` (a static test
  forbids HTML template strings); the one HTML path is the markdown renderer, which escapes everything first and
  allows only `http(s)`, relative and in-page links. XSS is tested in the browser with real payloads.

Anyone who can run code as you can read `.factory/` directly; the console adds no new access beyond your loopback.

## Agent inputs and prompt injection

Agents read repository content (code, `AGENTS.md`, standards, issue text from `--from-issue`, review files) that may
contain instructions. The factory reduces the blast radius rather than pretending to prevent it:

- the engine computes prompt context itself (commit lists, diffstats) instead of executing shell snippets in prompts;
- the request is quoted verbatim and labelled; messages from the console are labelled as the user's;
- every action an agent could be talked into that matters (push, deploy, apply, publish, destructive commands) is
  denied or asked by the guard, and merges happen only through the engine after review and approval;
- reviewers cannot change code, and the engine stops the job if the branch moved during a review round.

Review what `/factory` proposes for repositories you do not trust, and keep `auto_merge` on only where a local merge
is acceptable.

## Installation

`scripts/install.sh` writes only `~/.claude/skills/factory`, `~/.claude/skills/factory-live`,
`~/.claude/factory-install.json` and backups under `~/.claude/backups/`. It edits no settings file. It refuses to
replace folders it did not install unless told to, validates the staged copy before swapping it in, and uninstall
checks that every path it removes is under `~/.claude/skills/`. Backups are restored only from entries inside the
Claude home (no links, no absolute or `..` paths).

## Data and network

- Runtime dependencies: Python ≥ 3.9 standard library and git. The UI has no dependencies. `gh` is used only for
  `--from-issue` (read-only `gh issue view`).
- The engine and console make no network calls. The console's recent-projects list is stored at
  `~/.claude/factory/projects.json`.
- Hooks read agent transcripts (already on your disk) to record token usage and show activity; nothing is sent
  anywhere.
- Paid model calls happen only through Claude Code itself — your session, or `scripts/eval.sh` /
  `scripts/verify-local.sh --live`, which ask first and are capped.

## Reporting a problem

Open an issue without exploit details and ask for a private channel, or contact the repository owner directly.
