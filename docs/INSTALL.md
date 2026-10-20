# Install

## Requirements

| | Needed for |
|---|---|
| Claude Code (recent; the mod needs a build with the mod API, 2.1.30x or later) | everything |
| Python ≥ 3.9 | the engine, hooks and console (standard library only) |
| git ≥ 2.31 recommended | worktrees and merges (`merge-tree` conflict checks need ≥ 2.38; older gits fall back to a trial merge) |
| `gh` (optional) | `/factory … --from-issue N` |
| Node 20+ and `npm ci` (optional) | the browser and JS tests only |

## Recommended: the install script

```bash
git clone https://github.com/theabgupta1/software-factory && cd software-factory
scripts/verify-local.sh           # tests + a dry run of the install; no model calls
scripts/install.sh --dry-run      # exactly what would be written, replaced and backed up
scripts/install.sh                # install
```

What it does:

1. Checks Python, git and the `claude` CLI; decides whether to include `factory-live` (only if your Claude Code
   validates the mod; `--no-live` / `--with-live` override).
2. Copies the shipped files (no tests, sources or caches) into a staging folder under `~/.claude/skills/`, runs the
   staged `factoryctl doctor` and `claude plugin validate` on it.
3. Backs up anything it will replace to `~/.claude/backups/<component>-preinstall-<time>.tar.gz`.
4. Swaps the staged folders into `~/.claude/skills/factory` and `~/.claude/skills/factory-live`, where Claude Code
   loads them automatically (`factory@skills-dir`, `factory-live@skills-dir`), hooks included.
5. Writes `~/.claude/factory-install.json`: version, source commit, every file with its checksum.

It never edits `settings.json` or any other file of yours. If `~/.claude/skills/factory` exists and was not written by
this installer (for example a copy of the original factory), it stops with exit code 3 and changes nothing; re-run
with `--replace-foreign` to back it up and replace it — uninstall then restores it. Use `--config-dir DIR` (or
`CLAUDE_CONFIG_DIR`) for a Claude home elsewhere.

Check what is installed: `python3 scripts/install.py status`. Upgrade: pull and run `scripts/install.sh` again (it
reports `up-to-date` when nothing changed, and backs up your local edits when something did).

## Alternative: the marketplace

```text
/plugin marketplace add theabgupta1/software-factory
/plugin install factory@software-factory
/plugin install factory-live@software-factory      # optional
```

Do not use both routes at once: the plugin would load twice (the install script warns when it sees a marketplace
install).

## First run

In a repository:

```text
/factory init                          # creates .factory/ (git-ignored through .git/info/exclude)
/factory doctor                        # what the factory found: git, base branch, tools for each discipline
/factory Add a health endpoint         # your first job
/factory dashboard                     # the console
/factory-board                         # the in-terminal board (factory-live)
```

Optional per repository: `.factory/config.json` (see the README), `.factory/standards.md`,
`.factory/prompts/<agent>.md`, and `worktree.setup` commands if new worktrees need more than the factory can infer.

## Verify on your machine

`scripts/verify-local.sh` runs every deterministic test layer and writes `docs/TEST-MATRIX.md`, then shows the
install dry run. Two opt-in steps spend money, ask first and are capped:

```bash
scripts/verify-local.sh --live --max-budget-usd 5     # one real /factory quick job in a throwaway repository
scripts/verify-local.sh --evals --max-budget-usd 10   # the agent-quality evals (scripts/eval.sh)
```

Their results are folded into the test matrix.

## Uninstall

```bash
scripts/uninstall.sh --dry-run
scripts/uninstall.sh            # removes what was installed, restores what the first install replaced
scripts/uninstall.sh --purge    # also removes ~/.claude/factory (the console's recent-projects list)
```

Files you changed inside the installed folders are archived to `~/.claude/backups/` before removal. Repository state
(`.factory/`, job branches and worktrees) is never touched; remove it with `git worktree remove` and `rm -rf .factory`
if you want it gone.
