# Contributing

Pull requests are welcome from anyone. **Only the maintainer (@theabgupta1) merges.** Opening a PR never changes
`main`; a PR is merged when its description follows the template, the Tests check is green, and the maintainer has
approved it.

## How to open a pull request

1. Fork the repository and create a branch from `main` (`fix/…`, `feat/…`, `agents/…`, `docs/…`).
2. Make one focused change. Keep unrelated refactors out.
3. Run the free test layers (no model calls, nothing installed):

   ```bash
   scripts/test.sh static unit integration e2e evals js   # Python 3.9+, git, Node 20+ for js
   scripts/test.sh ui                                      # if you touched the console: npm ci && npx playwright install chromium
   ```

4. If you changed `plugins/factory/agents-src/`, `protocol/`, a workflow or `contracts/agents.json`, regenerate and
   commit the generated files:

   ```bash
   python3 scripts/build_agents.py && python3 scripts/build_docs.py
   ```

5. Open the PR against `main` and fill in every section of the template: what and why, the exact steps to
   verify, your test results, the checklist, screenshots for UI changes and the security impact.
6. The **Tests** workflow runs on your PR (read-only, no secrets). First-time contributors' runs start after the
   maintainer approves them.

## What gets a PR declined

- Weakening a safety rule: anything that lets the factory push, deploy, publish or apply infrastructure, lets agents
  write outside their worktree or Output, or bypasses the guard, the sealed state or the console key.
- New runtime dependencies for the engine, hooks or console (the Python standard library and git only).
- Secrets, real credentials or personal data, including in test fixtures.
- Changes without tests, or generated files edited by hand.
- Paid model calls in CI.

## Agent contracts

Each agent is `plugins/factory/agents-src/<name>.md` plus the shared `protocol/protocol.md`; the files in
`plugins/factory/agents/` are generated. Every agent keeps its ten contract sections (Purpose and scope through
Success criteria) and the exact Output header its contract in `contracts/artifacts.json` expects. Run the eval
suite offline (`scripts/test.sh evals`); `scripts/eval.sh` runs it against a model on your own machine and account.

## Security issues

Do not open a public issue for a vulnerability. Use GitHub's **Report a vulnerability** (Security tab) or contact
the maintainer directly. See [docs/SECURITY.md](docs/SECURITY.md) for what the factory protects and what it does not.
