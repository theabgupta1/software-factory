# ADR 0003: Install as skills-dir plugins, staged and reversible

Status: accepted (2026-10)

## Context
Users want one command that installs into `~/.claude` without disturbing their own configuration, and a clean way
back. Claude Code loads a plugin placed in `~/.claude/skills/<name>/` automatically, hooks included.

## Decision
`scripts/install.sh` copies the shipped files (no tests, sources or caches) into a staging folder on the same
filesystem, runs `factoryctl doctor` and `claude plugin validate` there, backs up anything it will replace (one
archive per component), swaps atomically, and records a manifest with checksums. No settings file is edited. A folder
it did not write is refused unless `--replace-foreign`. The marketplace route remains available.

## Consequences
- Foreign files stay byte-identical (tested); uninstall restores what the first install replaced.
- The mod is installed only when the local Claude Code validates it.
- Installing from both the marketplace and the script loads the plugin twice; the installer warns.
