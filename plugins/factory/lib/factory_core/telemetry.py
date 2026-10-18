"""Agent telemetry: what hooks record about spawned agents, and parsing of subagent transcripts.

Claude Code writes each subagent's transcript as JSONL. Assistant messages are repeated once per content
block, and early copies carry partial usage, so usage is de-duplicated by message id keeping the last copy.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .util import read_jsonl, seconds_between, truncate

MAX_TRANSCRIPT_BYTES = 30_000_000


def transcript_usage(path: Path) -> dict:
    """Summarise a subagent transcript: tokens, models, tool calls, first/last timestamps."""
    messages: dict[str, dict] = {}
    models: set[str] = set()
    first = last = None
    tool_uses = 0
    seen_tool_ids: set[str] = set()
    try:
        if path.stat().st_size > MAX_TRANSCRIPT_BYTES:
            return {"error": "transcript too large"}
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                ts = rec.get("timestamp")
                if ts:
                    first = first or ts
                    last = ts
                msg = rec.get("message") or {}
                if rec.get("type") != "assistant" or not isinstance(msg, dict):
                    continue
                if msg.get("model"):
                    models.add(msg["model"])
                mid = msg.get("id") or rec.get("uuid")
                if msg.get("usage"):
                    messages[mid] = msg["usage"]
                for block in msg.get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id") not in seen_tool_ids:
                        seen_tool_ids.add(block.get("id"))
                        tool_uses += 1
    except FileNotFoundError:
        return {"error": "transcript not found"}
    totals = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    for u in messages.values():
        totals["input"] += int(u.get("input_tokens") or 0)
        totals["output"] += int(u.get("output_tokens") or 0)
        totals["cache_read"] += int(u.get("cache_read_input_tokens") or 0)
        totals["cache_write"] += int(u.get("cache_creation_input_tokens") or 0)
    totals["total"] = sum(totals.values())
    return {
        "usage": totals,
        "models": sorted(models),
        "tool_uses": tool_uses,
        "started_at": first,
        "ended_at": last,
        "seconds": seconds_between(first, last),
        "messages": len(messages),
    }


def activity(path: Path, after: int = 0, limit: int = 200) -> dict:
    """A readable feed from a transcript: the agent's text, tool calls and short results."""
    items: list[dict] = []
    line_no = 0
    seen_blocks: set[str] = set()
    try:
        with open(path, encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, start=1):
                if line_no <= after:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                msg = rec.get("message") or {}
                content = msg.get("content")
                ts = rec.get("timestamp")
                if rec.get("type") == "assistant" and isinstance(content, list):
                    for i, block in enumerate(content):
                        if not isinstance(block, dict):
                            continue
                        key = f"{msg.get('id')}:{block.get('id') or i}:{block.get('type')}"
                        if key in seen_blocks:
                            continue
                        seen_blocks.add(key)
                        if block.get("type") == "text" and block.get("text", "").strip():
                            items.append({"kind": "text", "ts": ts, "text": truncate(block["text"].strip(), 4000)})
                        elif block.get("type") == "tool_use":
                            items.append({"kind": "tool", "ts": ts, "tool": block.get("name"),
                                          "summary": summarize_tool_input(block.get("name"), block.get("input"))})
                elif rec.get("type") == "user" and isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            text = block.get("content")
                            if isinstance(text, list):
                                text = " ".join(b.get("text", "") for b in text if isinstance(b, dict))
                            items.append({"kind": "result", "ts": ts, "error": bool(block.get("is_error")),
                                          "text": truncate(str(text or "").strip(), 600)})
                elif rec.get("type") == "user" and isinstance(content, str) and line_no > 1:
                    items.append({"kind": "user", "ts": ts, "text": truncate(content.strip(), 2000)})
                if len(items) >= limit:
                    break
    except FileNotFoundError:
        return {"items": [], "cursor": after, "missing": True}
    return {"items": items, "cursor": line_no, "missing": False}


def summarize_tool_input(tool: str | None, data) -> str:
    if not isinstance(data, dict):
        return ""
    if tool == "Bash":
        return truncate(str(data.get("command", "")), 200)
    for key in ("file_path", "path", "pattern", "url", "query", "description"):
        if data.get(key):
            return truncate(str(data[key]), 200)
    return truncate(json.dumps(data, ensure_ascii=False)[:200], 200)


def agent_runs(store, job: dict) -> list[dict]:
    """One record per spawned agent, joined from telemetry.jsonl (spawn + stop records)."""
    runs: dict[str, dict] = {}
    for rec in read_jsonl(store.job_dir(job["id"]) / "telemetry.jsonl"):
        aid = rec.get("agent_id")
        if not aid:
            continue
        run = runs.setdefault(aid, {"agent_id": aid})
        if rec.get("type") == "agent.spawned":
            run.update({k: rec.get(k) for k in ("step", "attempt", "name", "subagent_type", "model", "transcript",
                                                  "ts") if rec.get(k) is not None})
            run["spawned_at"] = rec.get("ts")
        elif rec.get("type") == "agent.stopped":
            run.update({k: rec.get(k) for k in ("usage", "models", "tool_uses", "seconds", "problems") if k in rec})
            run["stopped_at"] = rec.get("ts")
            if rec.get("transcript") and not run.get("transcript"):
                run["transcript"] = rec["transcript"]
            if rec.get("step") and not run.get("step"):
                run["step"] = rec["step"]
    return list(runs.values())


def transcript_allowed(path: str | None) -> Path | None:
    """Only serve transcripts that live under a Claude projects directory."""
    if not path:
        return None
    p = Path(os.path.realpath(path))
    roots = [Path(os.path.realpath(os.path.expanduser(r))) for r in
             (os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude"), "~/.claude")]
    for root in roots:
        projects = root / "projects"
        if projects in p.parents and p.suffix == ".jsonl":
            return p
    return None
