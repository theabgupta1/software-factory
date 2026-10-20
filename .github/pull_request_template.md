<!--
Thanks for contributing. Only the maintainer (@theabgupta1) merges pull requests; a PR is merged when every step
below is done and the maintainer has approved it. Read CONTRIBUTING.md first. Keep each PR to one change.
-->

## What and why

<!-- One or two sentences: what this changes and the problem it solves. Link the issue: "Fixes #123". -->

## Type of change

- [ ] Bug fix
- [ ] New feature or workflow
- [ ] Agent contract change (`plugins/factory/agents-src/`)
- [ ] Console / factory-live UI
- [ ] Docs only
- [ ] Safety or security (guard, hooks, sealing, console access)

## How to verify

<!-- Exact steps a reviewer can copy and paste, from a fresh clone. Example:
1. `scripts/test.sh unit integration`
2. `python3 plugins/factory/bin/factoryctl serve --demo` and open the printed link
3. Go to Job 004 → Crew and check that …
-->

1.
2.
3.

## Test results

<!-- Paste the summary lines from `scripts/test.sh` (layers you ran, passed / failed / not run). -->

```text

```

## Checklist

- [ ] `scripts/test.sh` passes for every layer this change touches (all free layers if unsure)
- [ ] I added or updated tests for the behaviour I changed
- [ ] If I changed an agent source, workflow or the roster: I ran `python3 scripts/build_agents.py` and
      `python3 scripts/build_docs.py` and committed the generated files
- [ ] If I changed the console UI: screenshots below, both themes, and `scripts/test.sh ui` passes
- [ ] No new runtime dependency (the engine and hooks use the Python standard library only)
- [ ] No secrets, tokens, personal data or real credentials anywhere (fixtures use obvious placeholders)
- [ ] The factory's safety rules are not weakened: it still never pushes, deploys, publishes or applies
      infrastructure; agents still cannot touch the user's checkout or the factory's state. A change to the guard
      adds cases to `GUARD_TABLE` in `tests/unit/test_hooks.py`
- [ ] Docs updated (README, `docs/`) where behaviour changed

## Screenshots (UI changes)

<!-- Before / after, light and dark. -->

## Security impact

<!-- "None", or what changes for the guard, hooks, sealed state, console access or installer, and why it is safe. -->
