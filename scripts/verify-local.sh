#!/usr/bin/env bash
# Verify the Software Factory on YOUR machine, in this order:
#   1. every deterministic test layer, then docs/TEST-MATRIX.md from that run   (free)
#   2. what installing into your Claude home would change (dry run, nothing written)   (free)
#   3. --live: one real /factory job in a throwaway repo, capped by --max-budget-usd   (paid)
#   4. --evals: the agent-quality evals through scripts/eval.sh, capped                 (paid)
#
#   scripts/verify-local.sh [--live] [--evals] [--max-budget-usd 5] [--yes]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIVE=0 EVALS=0 BUDGET=5 YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --live) LIVE=1; shift ;;
    --evals) EVALS=1; shift ;;
    --max-budget-usd) BUDGET="$2"; shift 2 ;;
    --yes|-y) YES=1; shift ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "verify-local.sh: unknown option $1" >&2; exit 64 ;;
  esac
done
cd "$ROOT"

confirm() {
  [ "$YES" = 1 ] && return 0
  read -r -p "$1 [y/N] " answer
  case "$answer" in y|Y|yes) return 0 ;; *) return 1 ;; esac
}

echo "== 1/4 Deterministic tests (no model calls)"
[ -d node_modules/@playwright/test ] || echo "   (browser tests need: npm ci && npx playwright install chromium; they are reported NOT RUN otherwise)"
set +e
scripts/test.sh --matrix
tests_code=$?
set -e

echo
echo "== 2/4 What installing into your Claude home would change (dry run)"
python3 scripts/install.py install --dry-run || true

if [ "$LIVE" = 1 ]; then
  echo
  echo "== 3/4 One live /factory job (paid, capped at \$$BUDGET)"
  command -v claude >/dev/null || { echo "the claude CLI is not installed" >&2; exit 1; }
  if confirm "Run one real /factory quick job in a throwaway repository (spends up to \$$BUDGET)?"; then
    work="$(mktemp -d "${TMPDIR:-/tmp}/factory-live-XXXXXX")"
    repo="$work/demo"
    mkdir -p "$repo" && cd "$repo"
    git init -q -b main
    git config user.email live@example.invalid && git config user.name "Factory live check"
    cat > cli.py <<'PY'
import argparse


def main(argv=None):
    parser = argparse.ArgumentParser(prog="demo")
    parser.add_argument("name", nargs="?", default="world")
    args = parser.parse_args(argv)
    print(f"hello {args.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY
    cat > test_cli.py <<'PY'
from cli import main


def test_greets(capsys):
    assert main(["ada"]) == 0
    assert capsys.readouterr().out == "hello ada\n"
PY
    printf '# demo\n\nRun tests with `python3 -m pytest -q`.\n' > AGENTS.md
    git add -A && git commit -qm init
    start=$(date +%s)
    set +e
    claude -p "/factory quick Add a --version flag to cli.py that prints 1.0.0, with a test" \
      --plugin-dir "$ROOT/plugins/factory" \
      --max-budget-usd "$BUDGET" \
      --allowedTools Bash Read Write Edit Glob Grep Agent Skill TodoWrite \
      --append-system-prompt "This is a non-interactive verification run: spawn every factory agent with run_in_background set to false, and keep executing factoryctl actions until the job is done or needs a human. Never push." \
      --output-format json > "$work/claude.json" 2> "$work/claude.err"
    claude_code=$?
    set -e
    took=$(( $(date +%s) - start ))
    cd "$ROOT"
    python3 - "$repo" "$work" "$claude_code" "$took" <<'PY'
import json, pathlib, subprocess, sys
repo, work, code, took = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
index = repo / ".factory" / "index.json"
jobs = json.loads(index.read_text())["jobs"] if index.exists() else []
job = jobs[0] if jobs else {}
cost = None
try:
    cost = json.loads((work / "claude.json").read_text()).get("total_cost_usd")
except (OSError, ValueError):
    pass
log = subprocess.run(["git", "log", "--oneline", "main"], cwd=repo, capture_output=True, text=True).stdout
ok = job.get("status") in ("merged", "needs-human", "delivered")
detail = (f"job {job.get('id')} ended {job.get('status')} in {took}s, cost ${cost}, claude exit {code}; "
          f"main has {len(log.splitlines())} commits; repo kept at {repo}")
if not jobs:
    detail = f"no job was created (claude exit {code}); see {work}/claude.err and {work}/claude.json"
out = pathlib.Path("test-results/live-smoke.json")
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps({"status": "passed" if ok else "failed", "detail": detail}, indent=2))
print(("PASS: " if ok else "FAIL: ") + detail)
PY
    python3 scripts/testkit.py matrix >/dev/null
  else
    echo "   skipped"
  fi
fi

if [ "$EVALS" = 1 ]; then
  echo
  echo "== 4/4 Agent-quality evals (paid, capped at \$$BUDGET)"
  extra=(); [ "$YES" = 1 ] && extra=(--yes)
  scripts/eval.sh --max-cost-usd "$BUDGET" "${extra[@]}" || true
  python3 scripts/testkit.py matrix >/dev/null
fi

echo
echo "Matrix: docs/TEST-MATRIX.md (deterministic tests exit code: $tests_code)"
echo "Install for real: scripts/install.sh   ·   remove: scripts/uninstall.sh"
exit "$tests_code"
