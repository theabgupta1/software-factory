"""Review panel selection: deterministic, explainable, and only ever growing across rounds."""

from __future__ import annotations

import fnmatch
import re

from . import registry

UI_IMPACT = re.compile(r"^\s*(?:[-*]\s*)?ui[ _-]?impact\s*:\s*(yes|true)\b", re.I | re.M)


def _match(paths: list[str], globs: list[str]) -> list[str]:
    hits = []
    for p in paths:
        name = p.rsplit("/", 1)[-1]
        if any(fnmatch.fnmatch(p, g) or fnmatch.fnmatch(name, g) for g in globs):
            hits.append(p)
    return hits


def predicates(workflow_id: str, paths: list[str], build_text: str, cfg: dict) -> dict[str, str | None]:
    """Map rule name -> reason it fired (None when it didn't)."""
    out: dict[str, str | None] = {"ui": None, "perf": None}
    ui_hits = _match(paths, cfg["panel"].get("ui_globs", []))
    if ui_hits:
        out["ui"] = f"UI files changed ({', '.join(ui_hits[:3])}{'…' if len(ui_hits) > 3 else ''})"
    elif UI_IMPACT.search(build_text or ""):
        out["ui"] = "the builder declared UI impact in build.md"
    if workflow_id in ("perf", "data"):
        out["perf"] = f"{workflow_id} workflow"
    else:
        perf_hits = _match(paths, cfg["panel"].get("perf_globs", []))
        if perf_hits:
            out["perf"] = f"performance-sensitive files changed ({', '.join(perf_hits[:3])})"
    return out


def select(stage: dict, workflow_id: str, previous: list[dict], paths: list[str], build_text: str, cfg: dict,
           round_no: int) -> list[dict]:
    keys = registry.review_keys()
    members: dict[str, dict] = {m["key"]: dict(m) for m in previous}
    for key in stage.get("always", []):
        members.setdefault(key, {"key": key, "agent": keys[key], "why": "always on this panel", "since_round": round_no})
    fired = predicates(workflow_id, paths, build_text, cfg)
    for rule in stage.get("rules", []):
        reason = fired.get(rule["if"])
        if not reason:
            continue
        for key in rule["add"]:
            members.setdefault(key, {"key": key, "agent": keys[key], "why": reason, "since_round": round_no})
    order = list(stage.get("always", [])) + [k for r in stage.get("rules", []) for k in r["add"]]
    return sorted(members.values(), key=lambda m: order.index(m["key"]) if m["key"] in order else 99)
