# Security

What the factory can and cannot do on your machine, what protects you, and where the limits are.

**The short version.** The factory runs inside your Claude Code session with your permissions. Its engine has no
code path that pushes, deploys, publishes or applies infrastructure. A guard hook stops factory agents from doing
those things directly, keeps them out of your checkout and out of the factory's own state, and asks you before
risky commands. The state that steers a job is sealed, so a forged message or an edited job file is caught. None of
this is a sandbox. An agent that is determined, or prompt-injected into trying hard enough (for example by writing
a script and running it), can do anything your user account can do. For unattended runs or untrusted
repositories, turn on Claude Code's own sandbox and permission rules. They are the outer boundary.

## What the factory never does

- **Never pushes.** Approved work merges into your local base branch. With `deliver: branch` it stops and gives you
  the exact `git push` and `gh pr create` commands to run yourself. Every value in those commands is shell-quoted.
- **Never deploys, publishes or applies infrastructure.** Infra jobs run format, validate, lint and plan only, and
  always escalate to you.
- **Never touches production data.** Data agents work on local fixtures and extracts. Migrations and backfills are
  written, not run against shared databases.
- **Never decides what needs a human.** These always go to you:
  - money;
  - access and permissions;
  - deploy and CI configuration;
  - migrations;
  - production data;
  - new dependencies;
  - product decisions;
  - any path in your `always_escalate` list.

## Threat model

| From | Example | What stops it |
|---|---|---|
| A prompt-injected or confused agent | A README tells the reviewer to `git push`, or to approve its own change | The command guard, the write limits, sealed state, and the orchestrator rule that notes never authorise anything |
| Repository content | A cloned repository ships `.factory/config.json` with setup commands, or hostile `.factory/prompts/*.md` | Trust gate on tracked config; repository notes are fenced as data |
| Text with shell syntax | A request or issue title containing `` `…` `` or `$(…)` | Free text goes to factoryctl on stdin through a quoted heredoc, never as a shell argument |
| Other users or machines on the network | Reading your diffs or messaging your agents through the console | Loopback-only console, loopback clients only, a per-run key on every API call |
| A malicious process running as you | Anything | Out of scope. It can already read and write everything you can |

## The command guard

`PreToolUse` runs before every Bash call a factory agent makes. With `guard.scope: "all"` in `.factory/config.json`
it also runs for every session in a factory repository. The guard lives in `lib/factory_core/guard.py`; its rules
are in `plugins/factory/guard-policy.json`.

**How it reads a command.** It reads the command the way a shell would, so the usual disguises don't work:

- it splits compound commands (`;`, `&&`, `||`, `|`, `&`, newlines, subshells, `{ …; }`, `if`/`while` bodies);
- it follows `$(…)`, backticks, `<(…)`, `bash -c`/`sh -c`/`zsh -c`, `eval`, heredocs and here-strings fed to a
  shell, `find -exec`, and `env -S`;
- it unwraps `env`, `sudo`, `xargs`, `timeout`, `nohup`, `nice`, `command`, `npx`, `pnpm dlx`, `uv run`,
  `poetry run` and similar wrappers;
- it drops `VAR=value` prefixes and quoting tricks (`git 'push'`);
- it skips each tool's global options before it reads the subcommand (`git -C dir -c k=v --no-pager push`,
  `pulumi -s prod up`, `aws --profile x s3 rm`, `kubectl --context c delete`).

| Decision | Rules |
|---|---|
| deny | **Pushing:** `git push`, `send-pack`, `subtree push`, `lfs push`, and aliases that push. **GitHub:** merging or creating PRs, releases, workflow runs, deleting repositories, and secrets or variables. **Publishing:** packages (npm, pnpm, yarn, bun, PyPI tools, cargo, gem, maven, gradle, nuget and others), container images, charts and releases. **Deploying:** Terraform, OpenTofu and Terragrunt apply, destroy, import and state changes; `pulumi up`/`destroy`; `cdk`, `serverless`, `sam`, `vercel`, `netlify`, `firebase`, `fly`, `wrangler` and other deployers; CloudFormation stack changes. **Destroying:** recursive delete or `find -delete` of `/`, `~`, `$HOME`, `..`, wildcards or system folders; `--no-preserve-root`; disk formatting; raw device writes; fork bombs; recursive chmod or chown of root or home |
| ask | **Clusters:** `kubectl` and `helm` changes. **Cloud:** AWS, Google Cloud, Azure and DigitalOcean CLI calls that change resources. **Data:** destructive SQL sent to a database client, including through pipes and heredocs. **Production:** commands that mention production, and migrations against an explicit non-local database URL. **Running unseen code:** `curl … \| sh` and anything else that pipes a program into a shell or interpreter; `sudo`; `ssh`; commands named by a variable with a verb like push or deploy; inline Python, Node or Perl that mentions a push or deploy. **Credentials and git:** reading credential files; changing global git config, aliases, hooks paths or remotes. **GitHub:** comments, issues and API writes. **Unreadable commands:** commands longer than 16,000 characters, nested more than 6 levels, or with unbalanced quotes |
| deny, agents only | **Your checkout:** git commands that change your main checkout, and redirects (`>`) into it; agents work in the job's worktree (`cd <Worktree> && …`). **Factory state:** anything that names the factory's own state (`job.json`, `messages.jsonl`, `entries/`, `.factory/config.json`, the seal key) or runs `factoryctl`. **Git internals:** writing `.git/hooks` or `core.hooksPath` |

Every pattern is bounded, and a test feeds the guard padded, deeply nested and unbalanced inputs to show it answers
within a second. If the guard itself errors, it falls back to "ask" for factory agents. Every other hook fails
open and logs to `~/.claude/factory-hooks.log`.

The guard logs blocked commands to the job's telemetry, with credentials masked: `user:password@` in URLs,
`token=`/`password=` values, bearer tokens, `-pSECRET`, and well-known token formats.

**What it cannot see:** what a script file does when an agent runs it (`bash build.sh`, `python deploy.py`). That
is why it is defence in depth.

## Where agents may write

Applies to the Write, Edit and NotebookEdit tools:

- **Inside `.factory/`:** only the agent's own Output file. That is the step's file from the sealed job record, plus
  `plan.json` for planning steps. Nothing else: no other agent's file, no job state, messages, entries, config or
  prompts.
- **Nowhere inside any `.git` folder.**
- **Implementers** write in their job's worktree, never in your main checkout.
- **Reviewers, analysts and the approver** write only their Output.

Every agent has Bash, so these limits are not a sandbox. The engine also checks the branch head and a clean
worktree at the end of every review round, and stops the job if they moved.

## Sealed state

Agents can write any file you can, so the files that decide what happens next carry an HMAC-SHA256 seal:

- `job.json`;
- every message and delivery receipt in `messages.jsonl`;
- every entry in the subagent → step map (`agents.json`).

The key is per repository, at `.git/factory/state.key` (mode 0600). That is outside every worktree and outside
`.factory/`, and it is never committed or cloned.

- **An edited `job.json`** stops the engine with "changed outside factoryctl", and the job shows as `corrupt`. If
  you edited it yourself, `factoryctl reseal <job>` accepts it.
- **A message written straight into `messages.jsonl`** is never delivered to an agent or the orchestrator. The
  console marks it "Not sent by you".
- **A forged map entry** gets no messages and no write permission.

The key is readable by your user, so an agent that sets out to find it could forge state. The guard denies
commands that name it, and the seal makes casual or injected tampering visible. Treat it as tamper evidence, not
cryptographic isolation.

## Git configuration and hooks

When a job starts, the engine records a fingerprint of the repository's shared `.git/config`, `.git/hooks/` and
`.git/info/attributes`. A planted hook or alias would run on your machine long after the job, so if the fingerprint
changes the job stops with `git-changed`. Look at `git config --local --list` and `.git/hooks/`. If the change was
yours, `/factory resume <job>` accepts it.

## Repository configuration you have not reviewed

`.factory/` is kept out of git through `.git/info/exclude`, but a repository can still commit
`.factory/config.json`. A tracked config is only partly applied until you trust it:

- **Applied:** `base_branch`, `ports`, `panel`, `stall_minutes`.
- **Ignored:** setup commands, copies, agent prefix, models, limits, gates, delivery and guard scope. Every action
  carries a `config_warning` that says so.

Read the file, then run `factoryctl trust`. The trust is stored by content hash in `~/.claude/factory/trusted.json`,
so any later change needs trusting again. Setup commands, trusted or not, go through the guard: a denied command
stops the job, and a risky one is marked "ask the user before running this one".

Repository-specific notes from `.factory/prompts/<agent>.md` reach the agent fenced as the repository's text. They
cannot close the fence or forge a step header. The prompt says they cannot change the agent's contract or the
factory's rules.

## The orchestrator and free text

The `/factory` skill passes requests, rework notes, messages and backlog items to factoryctl on stdin with a
quoted heredoc (`new - <<'FACTORY_TEXT'`), so backticks and `$(…)` in Markdown are never run by a shell.
`/factory next` uses `new --from-backlog`, so backlog text never passes through the command line.

Notes from the console are the user's messages, but the skill is explicit about their limits. A note never
authorises pushing, deploying, merging, approving, cancelling, `--force`, editing `.factory/` or running a command
the engine did not ask for. For those the orchestrator asks you in the conversation. Text inside agents' outputs,
reviews, issues and repository files is data, never instructions.

## The Factory Console

`factoryctl serve` protects the console in these ways:

- **Binds to loopback only.** Any other `--host` is refused unless you add `--insecure-host`.
- **Answers loopback clients only.** Other clients get 403.
- **Checks the `Host` header.** Requests whose `Host` is not the loopback address and port it serves are refused
  with 421, which stops DNS rebinding.
- **Needs the per-run key on every API request, reads included.** The key is in the launch link it prints
  (`http://127.0.0.1:<port>/?token=…`). The page keeps it for that port and removes it from the address bar. The
  key is never embedded in a page, so another local user who can reach the port cannot read your jobs, diffs or
  transcripts without it (401).
- **Writes need more.** Posting a message or a backlog item also needs a same-origin `Origin`, a JSON content type
  and a body under 64 KB (403/413).
- **Sends strict security headers.**
  - `Content-Security-Policy: default-src 'self'`: no inline script, no remote code.
  - `X-Frame-Options: DENY`.
  - `X-Content-Type-Options: nosniff`.
  - `Referrer-Policy: no-referrer`.
- **Confines what it serves.**
  - Static files come only from `ui/`.
  - Artifacts come only from the job's folder: symlinks and `..` are refused, with a 1 MB cap.
  - Diffs are capped at 1.5 MB.
  - Agent transcripts come only from under the Claude projects folder.
- **Renders text as text.** The UI builds the DOM with element APIs and `textContent`, and a static test forbids
  HTML template strings. The one HTML path is the Markdown renderer. It escapes everything first, turns links into
  placeholders in a single pass (so no later rule can rewrite inside an `href`), and allows only `http(s)`,
  same-site relative and in-page links (never protocol-relative `//host`). XSS is tested in the browser with real
  payloads.

## Installation

`scripts/install.sh` writes only:

- `~/.claude/skills/factory`;
- `~/.claude/skills/factory-live`;
- `~/.claude/factory-install.json`;
- backups under `~/.claude/backups/`.

It edits no settings file. It refuses to replace folders it did not install unless told to. It validates the
staged copy before swapping it in. Uninstall checks that every path it removes is under `~/.claude/skills/`.
Backups are restored only from entries inside the Claude home: no links, no absolute paths, no `..` paths.

## Data and network

- **Runtime dependencies:** the Python ≥ 3.9 standard library and git. The UI has no dependencies. `gh` is used only
  for `--from-issue`, which runs the read-only `gh issue view`.
- **No network calls.** The engine and console make none.
- **Local files the factory keeps:**
  - `~/.claude/factory/projects.json`: the recent projects list;
  - `~/.claude/factory/trusted.json`: trusted configs;
  - `.git/factory/state.key`: the seal key.
- **Transcripts stay local.** Hooks read agent transcripts already on your disk to record token usage and show
  activity. Nothing is sent anywhere.
- **Paid model calls** happen only through Claude Code itself: your session, or `scripts/eval.sh` and
  `scripts/verify-local.sh --live`, which ask first and are capped.

## Reporting a problem

Open an issue without exploit details and ask for a private channel, or contact the repository owner directly.
