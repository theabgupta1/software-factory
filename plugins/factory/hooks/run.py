#!/usr/bin/env python3
"""Entry point for every factory hook: `run.py <event>` with the hook JSON on stdin.

Exit code is always 0. A handler error is logged to ~/.claude/factory-hooks.log and treated as "no opinion",
except for the PreToolUse guard, which fails closed by asking the human.
"""

import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "lib"))


def _log(event, exc_text):
    try:
        base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
        with open(os.path.join(base, "factory-hooks.log"), "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {event}: {exc_text}\n")
    except OSError:
        pass


def _has_state(cwd):
    p = cwd or ""
    for _ in range(8):
        if not p:
            return False
        if os.path.isdir(os.path.join(p, ".factory")):
            return True
        parent = os.path.dirname(p)
        if parent == p:
            return False
        p = parent
    return False


def _irrelevant(event, data):
    """Cheap checks so non-factory sessions never import the engine."""
    factory_agent = str(data.get("agent_type") or "").startswith("factory:")
    if event == "post-tool-use":
        spawning = data.get("tool_name") in ("Agent", "Task") and not data.get("agent_id")
        if spawning:
            prompt = str((data.get("tool_input") or {}).get("prompt", ""))
            return "FACTORY-STEP job=" not in prompt
        return not factory_agent
    if event == "subagent-stop":
        return not factory_agent
    if event == "pre-tool-use":
        return not factory_agent and not _has_state(data.get("cwd"))
    if event == "stop":
        return bool(data.get("agent_id")) or not _has_state(data.get("cwd"))
    return False


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    if _irrelevant(event, data):
        return 0
    try:
        from factory_core.hooks import HANDLERS

        handler = HANDLERS.get(event)
        result = handler(data) if handler else None
    except Exception:  # noqa: BLE001 - hooks must never break a session
        _log(event, traceback.format_exc(limit=3).replace("\n", " | "))
        if event == "pre-tool-use" and (data.get("agent_type") or "").startswith("factory:"):
            result = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                             "permissionDecisionReason": "Software Factory guard errored; "
                                                                         "please confirm this action."}}
        else:
            result = None
    if result:
        sys.stdout.write(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
