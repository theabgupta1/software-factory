"""Registry of repos that use the factory, so the console can switch between recent projects."""

from __future__ import annotations

import os
from pathlib import Path

from .util import file_lock, now_iso, read_json, write_json_atomic

MAX_PROJECTS = 30


def registry_path() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return Path(base) / "factory" / "projects.json"


def load() -> list[dict]:
    data = read_json(registry_path(), default={}) or {}
    projects = data.get("projects") if isinstance(data, dict) else None
    out = []
    for p in projects or []:
        if isinstance(p, dict) and isinstance(p.get("path"), str) and Path(p["path"], ".factory").is_dir():
            out.append(p)
    return out


def touch(root: Path) -> None:
    path = registry_path()
    try:
        with file_lock(path.parent / ".projects.lock"):
            projects = [p for p in load() if p["path"] != str(root)]
            projects.insert(0, {"path": str(root), "name": root.name, "last_seen": now_iso()})
            write_json_atomic(path, {"projects": projects[:MAX_PROJECTS]})
    except OSError:
        pass  # the registry is a convenience; never fail a job over it
