# Troubleshooting

Start with `/factory doctor` (or `factoryctl doctor`): it checks Python, git, the repository, the base branch and the
tools each discipline uses. Hook errors are logged to `~/.claude/factory-hooks.log`.

| Symptom | Cause and fix |
|---|---|
| `/factory` is not a command | The plugin is not loaded. `claude plugin list` should show `factory@skills-dir` (or `@software-factory`) as loaded. Re-run `scripts/install.sh`; restart Claude Code |
| Two `/factory` commands, or the old behaviour | An old copy of the original skill in the repository's `.claude/skills/factory`, or both the marketplace and the script installs. Remove one ([MIGRATION.md](MIGRATION.md)) |
| "is being driven by another Claude session" | The job's lease is held by another session that was active in the last 15 minutes. Continue there, or take over with `/factory resume <job>` (it asks before forcing) |
| A step keeps getting re-issued, then the job stops | The agent's output broke its contract (first line, sections). The job's events and the step's problems show why; after `max_attempts` it needs you. `/factory rework <job> <note>` with guidance |
| "Approved, but the main checkout is busy" | Your main checkout has uncommitted changes or is on another branch. Commit or stash, switch to the base branch, then `/factory approve <job>` again |
| "merging would conflict" | The base branch moved. Rework the job (`/factory rework <job> "rebase on main"`) or merge it yourself |
| A job sits in review and nothing happens | The orchestrator session ended. `/factory resume <job>` in any session; finished agents' outputs are recovered, lost ones re-issued |
| An agent card says "quiet" | No transcript activity for `stall_minutes` (20 by default). Message it from the console, or check its session |
| A message stays "queued" | Delivery happens at the agent's next tool call (running) or its next prompt (finished). If the agent never runs again in this job, message the orchestrator instead |
| The console shows nothing / "Disconnected" | `factoryctl serve` stopped. Start it again with `/factory dashboard`; each run has a new link and key |
| The console says "no valid console key" (401) | Open the exact link `factoryctl serve` printed (it ends in `?token=…`). The key changes every time the console starts, so an old tab or bookmark stops working |
| 421 or 403 from the console | You opened it under another host name, from another origin, or from another machine. Use the printed `http://127.0.0.1:<port>/?token=…` link on this machine. The console refuses non-loopback hosts unless you pass `--insecure-host` |
| "job.json was changed outside factoryctl" | Something other than the engine edited the job's state, perhaps an agent. Look at the file (and the job's events) before anything else. If the edit was yours, `factoryctl reseal <job>` accepts it |
| A job stopped with `git-changed` | `.git/config`, `.git/hooks/` or `.git/info/attributes` changed while it ran. Check `git config --local --list` and the hooks; remove anything you did not add. If the change is yours, `/factory resume <job>` |
| A console message shows "Not sent by you" | That line was written into `messages.jsonl` directly instead of through the console or factoryctl, so it failed the integrity check and was never delivered |
| Actions carry a `config_warning`; setup commands are ignored | `.factory/config.json` is tracked by git (it came with the repository) and you have not trusted it on this machine. Read it, then `factoryctl trust` |
| "worktree.setup … has a command the factory guard blocks" | A setup command in `.factory/config.json` pushes, deploys or destroys. Fix the config, then `/factory resume <job>` |
| The console's transcript view is empty | Activity comes from Claude Code transcripts recorded by the hooks. Jobs driven before the hooks were installed, or from another machine, have none |
| A guard "ask" or "deny" surprised you | The reason names the rule; the decisions are in `plugins/factory/guard-policy.json` and the parsing in `lib/factory_core/guard.py`. Denies are deliberate (push, apply, publish, deploy, destructive deletes, your checkout, the factory's state). An agent committing without `cd <Worktree> &&` is denied because it would commit to your checkout |
| New worktrees fail to build or test | Dependencies or untracked files (`.env`) are missing. Set `worktree.copy` and `worktree.setup` in `.factory/config.json` |
| `/factory-board` does nothing | factory-live needs a Claude Code build with the mod API, and an interactive session. `claude plugin validate ~/.claude/skills/factory-live` says whether this build loads it; panes need a wide terminal unless you open them yourself |
| The board says factoryctl was not found | Install both plugins side by side (the script does), or set factory-live's `factoryctl` option to the path |
| `scripts/test.sh` reports NOT RUN | That layer's tools are missing on this machine (reason given). `npm ci && npx playwright install chromium` for browser tests |
| `scripts/eval.sh` stops on Linux | Granting Bash to eval runs needs `bubblewrap` and `socat` |
