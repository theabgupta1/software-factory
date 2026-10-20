"""Load and validate the factory's declarative definitions: workflows, artifact contracts, agent roster."""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

from .paths import plugin_path
from .util import FactoryError

STAGE_KINDS = {"setup", "agent", "parallel", "build", "panel", "gate", "approve", "merge", "deliver"}
CONDITION_KEYS = {"size_in", "param_true", "policy", "has_plan", "any", "all", "not"}
SIZES = ("S", "M", "L")
IMPLEMENTERS = {
    "builder", "frontend-engineer", "backend-engineer", "sql-engineer", "data-engineer",
    "data-quality-engineer", "iac-engineer", "platform-engineer", "sre", "test-engineer", "docs-engineer",
    "performance-engineer",
}


def _load_json(path: Path) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError as exc:
        raise FactoryError(f"missing factory definition: {path}") from exc
    except json.JSONDecodeError as exc:
        raise FactoryError(f"{path}: invalid JSON ({exc})") from exc


@functools.lru_cache(maxsize=None)
def roster() -> dict:
    data = _load_json(plugin_path("contracts", "agents.json"))
    return {"domains": data["domains"], "agents": {a["name"]: a for a in data["agents"]}}


@functools.lru_cache(maxsize=None)
def contracts() -> dict:
    return _load_json(plugin_path("contracts", "artifacts.json"))


def review_keys() -> dict[str, str]:
    """Panel member key -> agent name, e.g. 'code' -> 'code-reviewer'."""
    return {a["review_key"]: name for name, a in roster()["agents"].items() if a.get("review_key")}


def agent(name: str) -> dict:
    try:
        return roster()["agents"][name]
    except KeyError as exc:
        raise FactoryError(f"unknown agent {name!r}") from exc


def workflow_ids() -> list[str]:
    return sorted(p.stem for p in plugin_path("workflows").glob("*.json"))


def load_workflow(workflow_id: str) -> dict:
    path = plugin_path("workflows", f"{workflow_id}.json")
    if not path.exists():
        raise FactoryError(f"unknown workflow {workflow_id!r}; choose one of: {', '.join(workflow_ids())}")
    wf = _load_json(path)
    problems = validate_workflow(wf)
    if problems:
        raise FactoryError(f"workflow {workflow_id!r} is invalid: " + "; ".join(problems))
    return wf


def _validate_condition(cond: Any, where: str, problems: list[str]) -> None:
    if not isinstance(cond, dict) or len(cond) != 1:
        problems.append(f"{where}: a condition is an object with exactly one key")
        return
    (key, value), = cond.items()
    if key not in CONDITION_KEYS:
        problems.append(f"{where}: unknown condition {key!r}")
    elif key in ("any", "all"):
        if not isinstance(value, list) or not value:
            problems.append(f"{where}: {key} needs a non-empty list")
        else:
            for i, sub in enumerate(value):
                _validate_condition(sub, f"{where}.{key}[{i}]", problems)
    elif key == "not":
        _validate_condition(value, f"{where}.not", problems)
    elif key == "size_in" and (not isinstance(value, list) or not set(value) <= set(SIZES)):
        problems.append(f"{where}: size_in takes a list of {SIZES}")


def validate_workflow(wf: Any) -> list[str]:
    problems: list[str] = []
    if not isinstance(wf, dict):
        return ["workflow must be an object"]
    for key in ("id", "title", "summary", "stages"):
        if key not in wf:
            problems.append(f"missing {key!r}")
    stages = wf.get("stages") or []
    if not stages or stages[0].get("kind") != "setup":
        problems.append("the first stage must be a setup stage")
    ids = [s.get("id") for s in stages]
    if len(ids) != len(set(ids)):
        problems.append("stage ids must be unique")
    agents = roster()["agents"]
    keys = review_keys()
    artifacts = contracts()["artifacts"]
    terminal = {"merge", "deliver"}
    if stages and stages[-1].get("kind") not in terminal:
        problems.append("the last stage must be merge or deliver")
    for s in stages:
        sid, kind = s.get("id"), s.get("kind")
        where = f"stage {sid!r}"
        if kind not in STAGE_KINDS:
            problems.append(f"{where}: unknown kind {kind!r}")
            continue
        if "when" in s:
            _validate_condition(s["when"], where, problems)
        members = []
        if kind in ("agent", "approve"):
            members = [s]
        elif kind == "parallel":
            members = s.get("members") or []
            if not members:
                problems.append(f"{where}: parallel stage needs members")
            mkeys = [m.get("key") for m in members]
            if len(mkeys) != len(set(mkeys)) or None in mkeys:
                problems.append(f"{where}: member keys must be present and unique")
        for m in members:
            name = m.get("agent")
            if name not in agents:
                problems.append(f"{where}: unknown agent {name!r}")
            if m.get("artifact") not in artifacts:
                problems.append(f"{where}: unknown artifact {m.get('artifact')!r}")
            if not m.get("output") or "/" in m.get("output", "") or ".." in m.get("output", ""):
                problems.append(f"{where}: output must be a plain file name in the job folder")
            if "when" in m:
                _validate_condition(m["when"], f"{where}.{m.get('key')}", problems)
        if kind == "build":
            name = s.get("agent", "builder")
            if name != "$param.agent" and name not in agents:
                problems.append(f"{where}: unknown agent {name!r}")
        if kind == "panel":
            for key in list(s.get("always", [])) + [k for r in s.get("rules", []) for k in r.get("add", [])]:
                if key not in keys:
                    problems.append(f"{where}: unknown review key {key!r}")
            for r in s.get("rules", []):
                if r.get("if") not in ("ui", "perf"):
                    problems.append(f"{where}: unknown rule {r.get('if')!r}")
            if s.get("loop_to") not in ids:
                problems.append(f"{where}: loop_to must name a stage")
            elif ids.index(s["loop_to"]) >= ids.index(sid):
                problems.append(f"{where}: loop_to must point backwards")
    if wf.get("rework_to") and wf["rework_to"] not in ids:
        problems.append("rework_to must name a stage")
    return problems
