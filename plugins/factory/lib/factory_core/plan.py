"""plan.json: the architect's task graph, validated and ordered deterministically."""

from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path

from . import registry

MAX_TASKS = 12
TASK_ID = re.compile(r"^T[0-9]{1,2}$")
# Files any task may touch without owning them (lockfiles, generated indexes).
SHARED_ALLOWLIST = [
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock", "uv.lock", "Cargo.lock", "go.sum",
    "Gemfile.lock", "composer.lock", "*.lock",
]


def load(job_dir: Path) -> dict | None:
    path = job_dir / "plan.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"__invalid__": True}


def validate(plan: object) -> tuple[list[str], list[str]]:
    """Returns (problems, execution order)."""
    problems: list[str] = []
    if not isinstance(plan, dict) or plan.get("__invalid__"):
        return ["plan.json is not valid JSON"], []
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return ["plan.json needs a non-empty 'tasks' list"], []
    if len(tasks) > MAX_TASKS:
        problems.append(f"plan.json has {len(tasks)} tasks; split the job (max {MAX_TASKS})")
    ids: list[str] = []
    by_id: dict[str, dict] = {}
    for i, t in enumerate(tasks):
        where = f"tasks[{i}]"
        if not isinstance(t, dict):
            problems.append(f"{where} must be an object")
            continue
        tid = t.get("id")
        if not isinstance(tid, str) or not TASK_ID.match(tid):
            problems.append(f"{where}: id must look like T1, T2 …")
            continue
        if tid in by_id:
            problems.append(f"duplicate task id {tid}")
            continue
        ids.append(tid)
        by_id[tid] = t
        agent = t.get("agent")
        if agent not in registry.IMPLEMENTERS:
            problems.append(f"{tid}: agent {agent!r} cannot implement tasks (use one of {sorted(registry.IMPLEMENTERS)})")
        if not isinstance(t.get("title"), str) or not t["title"].strip():
            problems.append(f"{tid}: needs a title")
        for key in ("depends_on", "owns", "acceptance"):
            if key in t and not (isinstance(t[key], list) and all(isinstance(x, str) for x in t[key])):
                problems.append(f"{tid}: {key} must be a list of strings")
        if not t.get("owns"):
            problems.append(f"{tid}: owns must list the paths or globs this task may change")
    for tid, t in by_id.items():
        for dep in t.get("depends_on", []) or []:
            if dep not in by_id:
                problems.append(f"{tid}: depends on unknown task {dep}")
            if dep == tid:
                problems.append(f"{tid}: depends on itself")
    order = _topo(by_id)
    if order is None:
        problems.append("plan.json has a dependency cycle")
        order = []
    return problems, order


def _topo(by_id: dict[str, dict]) -> list[str] | None:
    def num(t: str) -> int:
        return int(t[1:])

    deps = {tid: {d for d in (t.get("depends_on") or []) if d in by_id and d != tid} for tid, t in by_id.items()}
    order: list[str] = []
    ready = sorted([t for t, d in deps.items() if not d], key=num)
    done: set[str] = set()
    while ready:
        tid = ready.pop(0)
        order.append(tid)
        done.add(tid)
        for other, d in deps.items():
            if other not in done and other not in ready and other not in order and d <= done:
                ready.append(other)
        ready.sort(key=num)
    return order if len(order) == len(by_id) else None


def outside_ownership(paths: list[str], owns: list[str]) -> list[str]:
    patterns = list(owns) + SHARED_ALLOWLIST
    out = []
    for p in paths:
        name = p.rsplit("/", 1)[-1]
        if not any(
            fnmatch.fnmatch(p, pat) or fnmatch.fnmatch(name, pat) or p.startswith(pat.rstrip("/") + "/")
            for pat in patterns
        ):
            out.append(p)
    return out
