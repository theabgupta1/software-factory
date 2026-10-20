"""The catalog: agents (roster + their generated contract files) and workflows, for the console and docs."""

from __future__ import annotations

import re
from pathlib import Path

from . import registry
from .paths import plugin_path

FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    m = FRONTMATTER.match(text)
    if not m:
        return {}, text
    meta: dict = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith(" "):
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, text[m.end():]


def _sections(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    current = None
    buf: list[str] = []
    for line in body.splitlines():
        m = re.match(r"^## (.+)$", line)
        if m:
            if current:
                out[current] = "\n".join(buf).strip()
            current, buf = m.group(1).strip(), []
        elif current:
            buf.append(line)
    if current:
        out[current] = "\n".join(buf).strip()
    return out


def agents() -> list[dict]:
    r = registry.roster()
    workflows = {w: registry.load_workflow(w) for w in registry.workflow_ids()}
    keys = registry.review_keys()
    used: dict[str, list[str]] = {}
    for wid, wf in workflows.items():
        for stage in wf["stages"]:
            names = []
            if stage["kind"] in ("agent", "approve"):
                names = [stage["agent"]]
            elif stage["kind"] == "parallel":
                names = [m["agent"] for m in stage["members"]]
            elif stage["kind"] == "build":
                names = [stage.get("agent", "builder")]
            elif stage["kind"] == "panel":
                names = [keys[k] for k in stage.get("always", []) + [k for x in stage.get("rules", []) for k in x["add"]]]
            for n in names:
                if n.startswith("$"):
                    continue
                used.setdefault(n, [])
                if wid not in used[n]:
                    used[n].append(wid)
    out = []
    for name, a in r["agents"].items():
        path = plugin_path("agents", f"{name}.md")
        sections: dict[str, str] = {}
        if path.exists():
            _, body = parse_frontmatter(path.read_text(encoding="utf-8"))
            sections = _sections(body)
        out.append({
            **a,
            "workflows": sorted(used.get(name, [])),
            "contract": {k: sections[k] for k in sections if not k.startswith("Factory protocol")},
        })
    return out


WORKFLOW_ORDER = ["feature", "fix", "quick", "research", "review", "data", "infra", "refactor", "perf", "security", "docs"]


def workflows() -> list[dict]:
    out = []
    ids = registry.workflow_ids()
    for wid in sorted(ids, key=lambda w: (WORKFLOW_ORDER.index(w) if w in WORKFLOW_ORDER else 99, w)):
        wf = registry.load_workflow(wid)
        out.append(wf)
    return out


def catalog() -> dict:
    r = registry.roster()
    return {"domains": r["domains"], "agents": agents(), "workflows": workflows(),
            "artifacts": registry.contracts()["artifacts"]}


def skill_files() -> list[Path]:
    return sorted(plugin_path("skills").glob("*/SKILL.md"))
