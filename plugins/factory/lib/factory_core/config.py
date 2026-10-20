"""Per-repo configuration (`.factory/config.json`), every key optional."""

from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
from typing import Any

from .paths import RepoPaths, git
from .util import FactoryError, file_lock, now_iso, read_json, write_json_atomic

DEFAULTS: dict[str, Any] = {
    "base_branch": None,  # auto-detected when unset
    "auto_merge": True,
    "agent_prefix": "factory:",
    "require_plan_approval": ["infra"],
    "always_escalate": [],  # path globs that always need a human, on top of the approver's rules
    "limits": {
        "max_agent_runs": 40,
        "max_review_rounds": 3,
        "max_attempts": 2,
        "max_parallel": 4,
    },
    "ports": {"base": 5100},
    "models": {},  # agent name -> model alias override, e.g. {"code-reviewer": "haiku"}
    "panel": {
        "ui_globs": [
            "*.vue", "*.svelte", "*.tsx", "*.jsx", "*.css", "*.scss", "*.sass", "*.less", "*.html",
            "*.hbs", "*.ejs", "*.erb", "*.twig", "*.astro", "*.mdx", "**/components/**", "**/pages/**",
            "**/views/**", "**/templates/**", "**/app/**/*.ts", "*.swift", "*.storyboard",
            "**/res/layout/**",
        ],
        "perf_globs": [],
    },
    "guard": {"scope": "factory"},
    # Fresh worktrees lack untracked files; `copy` brings them over (copy-on-write when the OS can),
    # `setup` lists the commands the orchestrator runs before any agent starts.
    "worktree": {"copy": [], "setup": [], "setup_timeout_seconds": 900},
    # merge: approved jobs merge into the base branch locally (the original behaviour).
    # branch: approved jobs stop with their branch ready for a pull request; nothing is merged or pushed.
    "deliver": "merge",
    "stall_minutes": 20,
}

_TYPES = {
    "base_branch": (str, type(None)),
    "auto_merge": bool,
    "agent_prefix": str,
    "require_plan_approval": list,
    "always_escalate": list,
    "limits": dict,
    "ports": dict,
    "models": dict,
    "panel": dict,
    "guard": dict,
    "worktree": dict,
    "deliver": str,
    "stall_minutes": int,
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def validate(raw: Any) -> list[str]:
    problems: list[str] = []
    if not isinstance(raw, dict):
        return ["config.json must contain a JSON object"]
    for key, value in raw.items():
        if key not in _TYPES:
            problems.append(f"unknown key {key!r}")
            continue
        if not isinstance(value, _TYPES[key]):
            problems.append(f"{key!r} has the wrong type")
    if raw.get("deliver") not in (None, "merge", "branch"):
        problems.append("'deliver' must be 'merge' or 'branch'")
    wt = raw.get("worktree", {})
    if isinstance(wt, dict):
        for key in wt:
            if key not in DEFAULTS["worktree"]:
                problems.append(f"unknown worktree key {key!r}")
        for key in ("copy", "setup"):
            if key in wt and not (isinstance(wt[key], list) and all(isinstance(x, str) and x.strip() for x in wt[key])):
                problems.append(f"worktree.{key} must be a list of non-empty strings")
        for rel in wt.get("copy", []) if isinstance(wt.get("copy"), list) else []:
            if isinstance(rel, str) and (rel.startswith("/") or ".." in rel.replace("\\", "/").split("/")):
                problems.append(f"worktree.copy entry {rel!r} must be a path inside the repository")
    limits = raw.get("limits", {})
    if isinstance(limits, dict):
        for key, value in limits.items():
            if key not in DEFAULTS["limits"]:
                problems.append(f"unknown limit {key!r}")
            elif not isinstance(value, int) or isinstance(value, bool) or value < 1:
                problems.append(f"limit {key!r} must be a positive integer")
    return problems


# Keys a config that arrived with the repository may set before you trust it: none of them runs a command, picks
# which agents run, or weakens a gate.
SAFE_UNTRUSTED = {"base_branch", "ports", "panel", "stall_minutes"}


def trust_path() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return Path(base) / "factory" / "trusted.json"


def _digest(paths: RepoPaths) -> str:
    try:
        return hashlib.sha256(paths.config.read_bytes()).hexdigest()
    except FileNotFoundError:
        return ""


def is_tracked(paths: RepoPaths) -> bool:
    """Did .factory/config.json come with the repository (tracked by git) rather than from this machine?"""
    if not paths.config.exists():
        return False
    proc = git(["ls-files", "--error-unmatch", "--", ".factory/config.json"], paths.root, check=False)
    return proc.returncode == 0


def is_trusted(paths: RepoPaths) -> bool:
    entry = (read_json(trust_path(), default={}) or {}).get(str(paths.root)) or {}
    return bool(entry.get("sha256")) and entry["sha256"] == _digest(paths)


def trust(paths: RepoPaths) -> dict:
    """Record that the user reviewed the repository's tracked config as it is now."""
    raw = read_json(paths.config, default=None)
    problems = validate(raw if raw is not None else {})
    if raw is None or problems:
        raise FactoryError(f"{paths.config}: " + ("; ".join(problems) or "nothing to trust"))
    path = trust_path()
    with file_lock(path.parent / ".trusted.lock"):
        data = read_json(path, default={}) or {}
        data[str(paths.root)] = {"sha256": _digest(paths), "at": now_iso()}
        write_json_atomic(path, data)
    return {"trusted": str(paths.config), "sha256": data[str(paths.root)]["sha256"], "config": raw}


def load(paths: RepoPaths) -> dict:
    raw = read_json(paths.config, default={})
    problems = validate(raw)
    if problems:
        raise FactoryError(f"{paths.config}: " + "; ".join(problems))
    ignored: list[str] = []
    if raw and is_tracked(paths) and not is_trusted(paths):
        ignored = sorted(k for k in raw if k not in SAFE_UNTRUSTED)
        raw = {k: v for k, v in raw.items() if k in SAFE_UNTRUSTED}
    cfg = _merge(DEFAULTS, raw)
    if ignored:
        cfg["untrusted"] = {
            "ignored": ignored,
            "message": (".factory/config.json came with the repository and is not trusted on this machine, so "
                        f"{', '.join(ignored)} {'is' if len(ignored) == 1 else 'are'} ignored (it could run "
                        "commands or change which agents run). Read it, then run `factoryctl trust` to use it."),
        }
    if not cfg["base_branch"]:
        cfg["base_branch"] = detect_base_branch(paths)
    return cfg


def detect_base_branch(paths: RepoPaths) -> str:
    proc = git(["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], paths.root, check=False)
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip().split("/", 1)[-1]
    for name in ("main", "master", "trunk", "develop"):
        if git(["rev-parse", "--verify", "--quiet", f"refs/heads/{name}"], paths.root, check=False).returncode == 0:
            return name
    current = git(["branch", "--show-current"], paths.root, check=False).stdout.strip()
    return current or "main"
