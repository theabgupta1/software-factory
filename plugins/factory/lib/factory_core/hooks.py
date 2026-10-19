"""Claude Code hook handlers for the factory.

Every handler reads the hook's JSON input and returns either None (no opinion) or a JSON-serialisable dict.
Handlers must be fast and silent outside factory work: no `.factory/` and no factory agent means exit 0 with
no output. `run.py` turns exceptions into a logged no-op, except the guard which fails closed to "ask".
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import contracts, prompts, telemetry
from .paths import PLUGIN_ROOT, plugin_path
from .util import append_jsonl, file_lock, now_iso, read_json, read_jsonl, write_json_atomic

ROSTER_FILE = plugin_path("contracts", "agents.json")
AGENT_TOOLS = {"Agent", "Task"}
MAX_CONTEXT_CHARS = 6000


# ---- shared helpers --------------------------------------------------------------------------------
def _roster() -> dict:
    data = read_json(ROSTER_FILE, default={"agents": []})
    return {a["name"]: a for a in data.get("agents", [])}


def factory_agent(agent_type: str | None) -> str | None:
    """`factory:builder` (plugin) or `builder` (copied install) -> 'builder'; anything else -> None."""
    if not agent_type:
        return None
    name = agent_type.split(":", 1)[1] if agent_type.startswith("factory:") else agent_type
    return name if name in _roster() else None


def find_state(start: str | None) -> Path | None:
    """The nearest `.factory/` directory at or above `start` (bounded walk)."""
    if not start:
        return None
    p = Path(start)
    for _ in range(8):
        candidate = p / ".factory"
        if candidate.is_dir():
            return candidate
        if p.parent == p:
            break
        p = p.parent
    return None


def _agents_map_path(state: Path) -> Path:
    return state / "agents.json"


def _lookup_agent(state: Path, agent_id: str | None) -> dict | None:
    if not agent_id:
        return None
    return (read_json(_agents_map_path(state), default={}) or {}).get(agent_id)


def _job_dir(state: Path, job_id: str) -> Path:
    return state / "jobs" / job_id


def _subagent_transcript(main_transcript: str | None, agent_id: str) -> str | None:
    if not main_transcript:
        return None
    base = Path(main_transcript)
    return str(base.with_suffix("") / "subagents" / f"agent-{agent_id}.jsonl")


# ---- PreToolUse: command and write guard -----------------------------------------------------------
_POLICY: list[dict] | None = None


def _policy() -> list[dict]:
    global _POLICY
    if _POLICY is None:
        data = read_json(plugin_path("guard-policy.json"), default={"rules": []})
        _POLICY = [{**r, "rx": re.compile(r["pattern"])} for r in data.get("rules", [])]
    return _POLICY


def evaluate_command(command: str) -> tuple[str, dict | None]:
    """Returns ('deny'|'ask'|'allow', rule). deny wins over ask."""
    normalized = " ".join((command or "").split())
    hit_ask = None
    for rule in _policy():
        if rule["rx"].search(normalized):
            if rule["decision"] == "deny":
                return "deny", rule
            hit_ask = hit_ask or rule
    return ("ask", hit_ask) if hit_ask else ("allow", None)


def _guard_scope(cwd: str | None) -> str:
    state = find_state(cwd)
    if state is None:
        return "factory"
    cfg = read_json(state / "config.json", default={}) or {}
    return ((cfg.get("guard") or {}).get("scope")) or "factory"


def _decision(kind: str, reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": kind,
                                   "permissionDecisionReason": f"Software Factory guard: {reason}"}}


def _repo_root_of(cwd: str | None) -> Path | None:
    state = find_state(cwd)
    return state.parent if state else None


def pre_tool_use(data: dict) -> dict | None:
    agent = factory_agent(data.get("agent_type"))
    tool = data.get("tool_name")
    tin = data.get("tool_input") or {}
    in_scope = agent is not None or _guard_scope(data.get("cwd")) == "all"
    if not in_scope:
        return None
    if tool == "Bash":
        verdict, rule = evaluate_command(str(tin.get("command", "")))
        if verdict == "allow":
            return None
        _record_guard(data, agent, verdict, rule)
        return _decision(verdict, f"{rule['reason']} (rule {rule['id']})")
    if agent and tool in ("Write", "Edit", "NotebookEdit", "MultiEdit"):
        target = tin.get("file_path") or tin.get("notebook_path")
        if not target:
            return None
        target_real = os.path.realpath(target)
        root = _repo_root_of(data.get("cwd"))
        meta = _roster().get(agent, {})
        writes_code = "Edit" in meta.get("tools", [])
        in_jobs = "/.factory/jobs/" in target_real.replace(os.sep, "/") + "/"
        if not writes_code and not in_jobs:
            reason = (f"{agent} reviews and plans; it may only write its Output file in the job folder, "
                      f"not {target}.")
            _record_guard(data, agent, "deny", {"id": "reviewer-write"})
            return _decision("deny", reason)
        if root is not None and not in_jobs:
            root_real = os.path.realpath(root)
            if target_real == root_real or target_real.startswith(root_real + os.sep):
                reason = ("factory agents change code only in the job's worktree; the main checkout belongs to "
                          f"the user. Use the Worktree path from your prompt instead of {target}.")
                _record_guard(data, agent, "deny", {"id": "main-checkout-write"})
                return _decision("deny", reason)
    return None


def _record_guard(data: dict, agent: str | None, verdict: str, rule: dict | None) -> None:
    state = find_state(data.get("cwd"))
    if state is None:
        return
    info = _lookup_agent(state, data.get("agent_id")) or {}
    if not info.get("job"):
        return
    append_jsonl(_job_dir(state, info["job"]) / "telemetry.jsonl", {
        "type": "guard." + verdict, "ts": now_iso(), "agent_id": data.get("agent_id"), "agent": agent,
        "step": info.get("step"), "rule": (rule or {}).get("id"), "tool": data.get("tool_name"),
        "command": str((data.get("tool_input") or {}).get("command", ""))[:300],
    })


# ---- PostToolUse(Agent): spawn record ---------------------------------------------------------------
def _agent_id_from(response) -> str | None:
    if isinstance(response, dict):
        for key in ("agentId", "agent_id"):
            if isinstance(response.get(key), str):
                return response[key]
        for value in response.values():
            found = _agent_id_from(value)
            if found:
                return found
    elif isinstance(response, list):
        for value in response:
            found = _agent_id_from(value)
            if found:
                return found
    elif isinstance(response, str):
        m = re.search(r"agentId:\s*([A-Za-z0-9_-]+)", response)
        if m:
            return m.group(1)
    return None


def post_agent_spawn(data: dict) -> dict | None:
    tin = data.get("tool_input") or {}
    header = prompts.parse_header(str(tin.get("prompt", "")))
    if not header or not header.get("job_folder"):
        return None
    job_dir = Path(header["job_folder"])
    if not (job_dir / "job.json").exists():
        return None
    state = job_dir.parent.parent
    agent_id = _agent_id_from(data.get("tool_response"))
    response = data.get("tool_response") if isinstance(data.get("tool_response"), dict) else {}
    record = {
        "type": "agent.spawned", "ts": now_iso(), "agent_id": agent_id, "job": header["job"], "step": header["step"],
        "attempt": header["attempt"], "name": tin.get("name"), "subagent_type": tin.get("subagent_type"),
        "model": response.get("resolvedModel") or tin.get("model"),
        "transcript": _subagent_transcript(data.get("transcript_path"), agent_id) if agent_id else None,
        "session": data.get("session_id"),
    }
    append_jsonl(job_dir / "telemetry.jsonl", record)
    if agent_id:
        with file_lock(state / ".agents.lock"):
            mapping = read_json(_agents_map_path(state), default={}) or {}
            mapping[agent_id] = {k: record[k] for k in ("job", "step", "attempt", "name", "subagent_type",
                                                        "transcript", "ts")}
            if len(mapping) > 2000:  # keep the map small; drop the oldest entries
                for key in sorted(mapping, key=lambda k: mapping[k].get("ts") or "")[:500]:
                    mapping.pop(key, None)
            write_json_atomic(_agents_map_path(state), mapping)
    return None


# ---- PostToolUse inside a factory agent: deliver console messages ----------------------------------
def deliver_messages(data: dict) -> dict | None:
    agent = factory_agent(data.get("agent_type"))
    if agent is None or not data.get("agent_id"):
        return None
    state = find_state(data.get("cwd"))
    if state is None:
        return None
    info = _lookup_agent(state, data["agent_id"])
    if not info:
        return None
    job_dir = _job_dir(state, info["job"])
    log = job_dir / "messages.jsonl"
    if not log.exists():
        return None
    queued: dict[str, dict] = {}
    for rec in read_jsonl(log):
        if rec.get("type") == "message":
            to = rec.get("to") or {}
            if to.get("step") == info["step"] or to.get("agent") == agent:
                queued[rec["id"]] = rec
        elif rec.get("type") == "receipt":
            queued.pop(rec.get("id"), None)
    if not queued:
        return None
    lines = []
    for m in queued.values():
        append_jsonl(log, {"type": "receipt", "id": m["id"], "ts": now_iso(), "via": "hook",
                           "step": info["step"], "agent_id": data["agent_id"]})
        lines.append(f"- [{m['ts'][11:16]} UTC] {m['text']}")
    context = ("Factory Console message from the user (sent while you are working on step "
               f"{info['step']}). Treat it as an instruction for this step, adjust your work now, and record how "
               "you handled it in your Output file:\n" + "\n".join(lines))
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                   "additionalContext": context[:MAX_CONTEXT_CHARS]}}


def post_tool_use(data: dict) -> dict | None:
    if data.get("tool_name") in AGENT_TOOLS and not data.get("agent_id"):
        return post_agent_spawn(data)
    return deliver_messages(data)


# ---- SubagentStop: contract check + telemetry -------------------------------------------------------
def _first_prompt(transcript: str | None) -> str:
    if not transcript:
        return ""
    try:
        with open(transcript, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("type") != "user":
                    continue
                content = (rec.get("message") or {}).get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    except OSError:
        return ""
    return ""


def subagent_stop(data: dict) -> dict | None:
    agent = factory_agent(data.get("agent_type"))
    if agent is None:
        return None
    transcript = data.get("agent_transcript_path")
    header = prompts.parse_header(_first_prompt(transcript))
    if not header or not header.get("job_folder") or not header.get("output"):
        return None
    job_dir = Path(header["job_folder"])
    job = read_json(job_dir / "job.json", default=None)
    if not isinstance(job, dict):
        return None
    step = (job.get("steps") or {}).get(header["step"]) or {}
    problems: list[str] = []
    if step.get("status") == "issued" and step.get("attempt") == header["attempt"] and step.get("artifact"):
        out = Path(header["output"])
        check = contracts.check(out, step["artifact"])
        problems = list(check.problems)
        if check.exists and not problems:
            import hashlib

            if step.get("prior") and hashlib.sha256(out.read_bytes()).hexdigest() == step["prior"]:
                problems.append(f"{out.name} is unchanged since this step started; rewrite it for this round")
    usage = telemetry.transcript_usage(Path(transcript)) if transcript else {}
    append_jsonl(job_dir / "telemetry.jsonl", {
        "type": "agent.stopped", "ts": now_iso(), "agent_id": data.get("agent_id"), "agent": agent,
        "job": header["job"], "step": header["step"], "attempt": header["attempt"], "transcript": transcript,
        "usage": usage.get("usage"), "models": usage.get("models"), "tool_uses": usage.get("tool_uses"),
        "seconds": usage.get("seconds"), "problems": problems, "blocked": bool(problems) and not data.get("stop_hook_active"),
    })
    if problems and not data.get("stop_hook_active"):
        return {"decision": "block",
                "reason": "Software Factory contract check failed for your Output file "
                          f"({header['output']}):\n" + "\n".join(f"- {p}" for p in problems)
                          + "\nFix it now, then finish."}
    return None


# ---- Stop: keep the orchestrator from stopping mid-job ---------------------------------------------
def stop(data: dict) -> dict | None:
    if data.get("stop_hook_active") or data.get("agent_id"):
        return None
    state = find_state(data.get("cwd"))
    session = data.get("session_id")
    if state is None or not session:
        return None
    reasons = []
    jobs_dir = state / "jobs"
    if not jobs_dir.is_dir():
        return None
    for job_dir in sorted(jobs_dir.iterdir()):
        job = read_json(job_dir / "job.json", default=None)
        if not isinstance(job, dict) or job.get("status") != "running":
            continue
        if (job.get("lease") or {}).get("session") != session:
            continue
        steps = job.get("steps") or {}
        agents_running = [s for s, v in steps.items() if v.get("status") == "issued" and v.get("kind") != "inline"]
        inline = [s for s, v in steps.items() if v.get("status") == "issued" and v.get("kind") == "inline"]
        orchestrator_msgs = _queued_for_orchestrator(job_dir)
        if orchestrator_msgs:
            reasons.append(f"{job['id']}: the user sent you a message through the Factory Console: "
                           + " | ".join(orchestrator_msgs) + f" — run `factoryctl next {job['id']}` to receive it.")
        elif inline:
            reasons.append(f"{job['id']}: finish {inline[0]} and record it with factoryctl done.")
        elif not agents_running:
            reasons.append(f"{job['id']} is running but no agent is working on it. Run `factoryctl next "
                           f"{job['id']}` and carry out the action (or tell the user why you are stopping).")
    if not reasons:
        return None
    return {"decision": "block", "reason": "Software Factory: " + " ".join(reasons)}


def _queued_for_orchestrator(job_dir: Path) -> list[str]:
    log = job_dir / "messages.jsonl"
    if not log.exists():
        return []
    queued: dict[str, str] = {}
    for rec in read_jsonl(log):
        if rec.get("type") == "message" and (rec.get("to") or {}).get("role") == "orchestrator":
            queued[rec["id"]] = rec.get("text", "")[:300]
        elif rec.get("type") == "receipt":
            queued.pop(rec.get("id"), None)
    return list(queued.values())


# ---- SessionStart: one line when something needs the user; export factoryctl's path ----------------
def session_start(data: dict) -> dict | None:
    env_file = os.environ.get("CLAUDE_ENV_FILE")
    if env_file:
        try:
            ctl = PLUGIN_ROOT / "bin" / "factoryctl"
            with open(env_file, "a", encoding="utf-8") as fh:
                fh.write(f"export FACTORYCTL={_shell_quote(str(ctl))}\n")
                if data.get("session_id"):
                    fh.write(f"export FACTORY_SESSION_ID={_shell_quote(data['session_id'])}\n")
        except OSError:
            pass
    state = find_state(data.get("cwd"))
    if state is None:
        return None
    index = read_json(state / "index.json", default=None)
    if not isinstance(index, dict):
        return None
    attention = [j for j in index.get("jobs", []) if j.get("attention")]
    running = [j for j in index.get("jobs", []) if j.get("status") == "running"]
    if not attention and not running:
        return None
    parts = []
    if attention:
        parts.append(f"{len(attention)} job(s) need the user: " + "; ".join(
            f"{j['id']} ({j['attention']['kind']})" for j in attention[:4]))
    if running:
        parts.append(f"{len(running)} running: " + ", ".join(j["id"] for j in running[:4]))
    text = ("Software Factory in this repo — " + " · ".join(parts)
            + ". Mention this to the user only if relevant; `/factory status` shows the board.")
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


HANDLERS = {
    "pre-tool-use": pre_tool_use,
    "post-tool-use": post_tool_use,
    "subagent-stop": subagent_stop,
    "stop": stop,
    "session-start": session_start,
}
