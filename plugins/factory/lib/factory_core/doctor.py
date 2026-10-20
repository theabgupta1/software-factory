"""Environment checks: what the factory can use on this machine, reported honestly."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from .paths import PLUGIN_ROOT

TOOLS = [
    # (name, binary, version args, purpose, category)
    ("git", "git", ["--version"], "worktrees, branches and merges", "required"),
    ("python3", sys.executable, ["--version"], "factoryctl, hooks and the console", "required"),
    ("claude", "claude", ["--version"], "Claude Code CLI", "required"),
    ("gh", "gh", ["--version"], "GitHub PRs and issues (optional)", "vcs"),
    ("agent-browser", "agent-browser", ["--version"], "browser checks by builder and UX/UI reviewers", "browser"),
    ("playwright", "playwright", ["--version"], "browser checks (alternative to agent-browser)", "browser"),
    ("node", "node", ["--version"], "JavaScript/TypeScript projects", "runtime"),
    ("docker", "docker", ["--version"], "containers", "platform"),
    ("kubectl", "kubectl", ["version", "--client"], "Kubernetes manifests", "platform"),
    ("helm", "helm", ["version", "--short"], "Helm charts", "platform"),
    ("terraform", "terraform", ["version"], "IaC fmt/validate/plan", "infra"),
    ("tflint", "tflint", ["--version"], "Terraform lint", "infra"),
    ("checkov", "checkov", ["--version"], "IaC security scanning", "infra"),
    ("aws", "aws", ["--version"], "AWS CLI (read-only use)", "infra"),
    ("gcloud", "gcloud", ["--version"], "Google Cloud CLI (read-only use)", "infra"),
    ("sqlite3", "sqlite3", ["--version"], "local SQL testing", "data"),
    ("duckdb", "duckdb", ["--version"], "local analytical SQL testing", "data"),
    ("psql", "psql", ["--version"], "Postgres client", "data"),
    ("dbt", "dbt", ["--version"], "dbt models and tests", "data"),
    ("spark-submit", "spark-submit", ["--version"], "Spark jobs", "data"),
    ("sqlfluff", "sqlfluff", ["--version"], "SQL lint", "data"),
]


def _version(binary: str, args: list[str]) -> str | None:
    path = binary if Path(binary).is_absolute() else shutil.which(binary)
    if not path:
        return None
    try:
        proc = subprocess.run([path, *args], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return "installed (version unknown)"
    text = (proc.stdout or proc.stderr).strip().splitlines()
    first = text[0] if text else ""
    m = re.search(r"\d+\.\d+(\.\d+)?", first)
    return m.group(0) if m else (first[:40] or "installed")


def tools() -> list[dict]:
    out = []
    for name, binary, args, purpose, category in TOOLS:
        version = _version(binary, args)
        out.append({"name": name, "available": version is not None, "version": version, "purpose": purpose,
                    "category": category})
    return out


def checks() -> list[dict]:
    results = []
    py_ok = sys.version_info >= (3, 9)
    results.append({"check": "python >= 3.9", "ok": py_ok, "detail": sys.version.split()[0]})
    git_v = _version("git", ["--version"])
    merge_tree = False
    if git_v:
        parts = [int(x) for x in git_v.split(".")[:2]]
        merge_tree = parts >= [2, 38]
    results.append({"check": "git installed", "ok": bool(git_v), "detail": git_v or "not found"})
    results.append({"check": "git >= 2.38 (conflict pre-check)", "ok": merge_tree,
                    "detail": "older git still works; merges are attempted and aborted on conflict"})
    results.append({"check": "factory plugin files", "ok": (PLUGIN_ROOT / "contracts" / "agents.json").exists(),
                    "detail": str(PLUGIN_ROOT)})
    results.append({"check": "factory version", "ok": True, "detail": __version__})
    return results


def report() -> dict:
    return {"checks": checks(), "tools": tools()}
