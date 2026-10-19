#!/usr/bin/env bash
# Agent-quality evals: real model runs of the factory's agents against fixtures (plugins/factory/evals).
# They run on YOUR machine with YOUR Claude credentials and cost money; every run is capped by --max-cost-usd.
#
#   scripts/eval.sh [--max-cost-usd 10] [--runs 1] [--threshold 0.75] [--case GLOB] [--tag TAG] [--baseline] [--yes]
#
# --baseline adds the no-plugin arm (doubles the cost) and reports the score delta the plugin buys.
# Results: test-results/evals/<timestamp>/ (aggregate-result.json, report.html) and test-results/evals/latest.json,
# which scripts/test.sh --matrix folds into docs/TEST-MATRIX.md.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COST=10 RUNS=1 THRESHOLD=0.75 ABLATION=none YES=0
FILTERS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --max-cost-usd) COST="$2"; shift 2 ;;
    --runs) RUNS="$2"; shift 2 ;;
    --threshold) THRESHOLD="$2"; shift 2 ;;
    --case) FILTERS+=(--case "$2"); shift 2 ;;
    --tag) FILTERS+=(--tag "$2"); shift 2 ;;
    --baseline) ABLATION=with-without; shift ;;
    --yes|-y) YES=1; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "eval.sh: unknown option $1" >&2; exit 64 ;;
  esac
done

command -v claude >/dev/null || { echo "eval.sh: the claude CLI is not installed" >&2; exit 1; }
version="$(claude --version | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
python3 - "$version" <<'PY' || { echo "eval.sh: claude plugin eval needs Claude Code 2.1.269 or newer (found $version)" >&2; exit 1; }
import sys
sys.exit(0 if tuple(map(int, sys.argv[1].split("."))) >= (2, 1, 269) else 1)
PY
if [ "$(uname -s)" = "Linux" ]; then
  for tool in bwrap socat; do
    command -v "$tool" >/dev/null || { echo "eval.sh: granting Bash to eval runs on Linux needs $tool (bubblewrap/socat)" >&2; exit 1; }
  done
fi

echo "Checking the suite offline first (schema, scaffolds, checkers, graders on goldens)…"
( cd "$ROOT" && python3 -m unittest -q tests.evals.test_eval_suite ) \
  || { echo "eval.sh: the offline suite check failed; fix it before spending money" >&2; exit 1; }

cases=$(find "$ROOT/plugins/factory/evals" -name prompt.md | wc -l | tr -d ' ')
arms=1; [ "$ABLATION" = with-without ] && arms=2
cat <<EOF

About to run the factory's agent evals with real model calls:
  cases: $cases (filters: ${FILTERS[*]:-none}) · runs per case: $RUNS · arms: $arms · pass threshold: $THRESHOLD
  hard cost ceiling: \$$COST (claude plugin eval --max-cost-usd)
  tools granted to the runs: Bash, Write, Edit (inside the eval sandbox); fixtures built by each case's scaffold.sh
  this trusts the plugin directory $ROOT/plugins/factory (your checkout)
EOF
if [ "$YES" != 1 ]; then
  read -r -p "Run them? [y/N] " answer
  case "$answer" in y|Y|yes) ;; *) echo "Not run."; exit 0 ;; esac
fi

stamp="$(date +%Y%m%d-%H%M%S)"
out="$ROOT/test-results/evals/$stamp"
mkdir -p "$out"
set +e
claude plugin eval "$ROOT/plugins/factory" \
  --scaffold --trust-plugin --no-publish \
  --allow-tools Bash Write Edit \
  --runs "$RUNS" --ablation "$ABLATION" --threshold "$THRESHOLD" --max-cost-usd "$COST" \
  --output-dir "$out" "${FILTERS[@]}" --json "$out/result.json"
code=$?
set -e
cp "$out/result.json" "$ROOT/test-results/evals/latest.json" 2>/dev/null || true
python3 - "$out/result.json" "$code" <<'PY'
import json, sys
path, code = sys.argv[1], int(sys.argv[2])
try:
    r = json.load(open(path))
except (OSError, ValueError):
    sys.exit(print(f"No result file was written (exit {code})."))
agg = r.get("aggregates", {})
print(f"\nOverall score {agg.get('overallScore')} · {agg.get('casesPassed')}/{agg.get('casesTotal')} cases passed"
      f" · cost ${r.get('costUsd')} · {r.get('durationSeconds')}s · exit {code}")
for c in r.get("cases", []):
    a = c.get("aggregates", {})
    delta = f" (Δ {a['delta']:+.2f} vs no plugin)" if isinstance(a.get("delta"), (int, float)) else ""
    print(f"  {c.get('name')}: {a.get('score')}{delta}")
if r.get("partial"):
    print(f"Partial run: {r.get('partialReason')}")
PY
exit "$code"
