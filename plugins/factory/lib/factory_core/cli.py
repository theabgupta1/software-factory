"""`factoryctl`: the command line the /factory skill, hooks, mod and humans use.

Machine-facing commands (new, next, done, resume, approve, rework, cancel, tell) print one JSON object.
Human-facing commands (status, doctor, backlog list) print text unless --json is given.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import __version__, catalog, doctor, plan, registry, report
from .engine import Engine
from .paths import RepoPaths, ensure_state_dir
from .store import Store
from .util import FactoryError, file_lock, now_iso, write_text_atomic


def _print(obj) -> None:
    sys.stdout.write(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def _paths(args) -> RepoPaths:
    return RepoPaths.discover(Path(args.repo) if getattr(args, "repo", None) else None)


def _engine(args) -> Engine:
    return Engine(_paths(args), session=getattr(args, "session", None))


def _params(values: list[str] | None) -> dict[str, str]:
    out = {}
    for v in values or []:
        if "=" not in v:
            raise FactoryError(f"--param expects key=value, got {v!r}")
        k, _, val = v.partition("=")
        if not re.match(r"^[a-z][a-z0-9_]{0,30}$", k):
            raise FactoryError(f"invalid param name {k!r}")
        out[k] = val
    return out


def _request(args) -> str:
    if args.request == ["-"]:
        return sys.stdin.read()
    return " ".join(args.request or [])


# ---- commands -------------------------------------------------------------------------------
def cmd_init(args) -> int:
    paths = _paths(args)
    ensure_state_dir(paths)
    Store(paths).write_index()
    _print({"repo": str(paths.root), "state": str(paths.state), "version": __version__,
            "workflows": registry.workflow_ids()})
    return 0


def cmd_new(args) -> int:
    eng = _engine(args)
    job = eng.new_job(_request(args), workflow=args.workflow, size=args.size, why=args.why or "",
                      params=_params(args.param), title=args.title)
    out = {"job": job["id"], "workflow": job["workflow"], "size": job["size"], "branch": job["branch"],
           "worktree": job["worktree"], "job_dir": str(eng.store.job_dir(job["id"]))}
    if args.backlog_item:
        _backlog_link(eng.paths, args.backlog_item, job["id"])
    out["next"] = eng.next(job["id"])
    _print(out)
    return 0


def cmd_next(args) -> int:
    eng = _engine(args)
    _print(eng.redeliver(args.job) if args.redeliver else eng.next(args.job))
    return 0


def cmd_done(args) -> int:
    _print(_engine(args).done(args.job, args.step, args.attempt, status=args.status, agent_id=args.agent_id,
                              via=args.via, note=args.note))
    return 0


def cmd_resume(args) -> int:
    _print(_engine(args).resume(args.job, force=args.force))
    return 0


def cmd_approve(args) -> int:
    _print(_engine(args).approve(args.job))
    return 0


def cmd_rework(args) -> int:
    note = args.note if args.note != "-" else sys.stdin.read()
    _print(_engine(args).rework(args.job, note))
    return 0


def cmd_cancel(args) -> int:
    _print(_engine(args).cancel(args.job, args.reason or ""))
    return 0


def cmd_tell(args) -> int:
    text = " ".join(args.text) if args.text != ["-"] else sys.stdin.read()
    role = "orchestrator" if args.orchestrator else None
    _print(_engine(args).tell(args.job, text, step=args.step, agent=args.agent, role=role))
    return 0


def cmd_messages(args) -> int:
    store = Store(_paths(args))
    _print(store.messages(store.resolve(args.job)))
    return 0


def cmd_status(args) -> int:
    paths = _paths(args)
    store = Store(paths)
    if args.job:
        job = store.load(store.resolve(args.job))
        if args.json:
            _print(job)
        else:
            sys.stdout.write(_job_text(job))
        return 0
    jobs = store.summaries()
    if args.json:
        _print({"repo": str(paths.root), "jobs": jobs})
        return 0
    sys.stdout.write(_board_text(paths, jobs))
    return 0


GLYPH = {"PASS": "✓", "CHANGES": "✗", "pending": "…", None: "·"}


def _chips(reviews: dict) -> str:
    return " ".join(f"{k} {GLYPH.get(v, '?')}" for k, v in reviews.items())


def _board_text(paths: RepoPaths, jobs: list[dict]) -> str:
    attention = [j for j in jobs if j.get("attention")]
    running = [j for j in jobs if j["status"] == "running"]
    done = [j for j in jobs if j["status"] in ("merged", "delivered", "cancelled")]
    other = [j for j in jobs if j not in attention + running + done]
    lines = [f"Software Factory · {paths.root.name}", ""]
    if not jobs:
        return "\n".join(lines + ["No jobs yet. Start one with: /factory <what you want built>", ""])
    if attention:
        lines.append("NEEDS YOU")
        for j in attention:
            a = j["attention"]
            cmd = (a.get("commands") or [""])[0]
            lines.append(f"  {j['id']:<28} {a['kind']:<16} {a['reason'][:70]}")
            if cmd:
                lines.append(f"  {'':<28} → {cmd}")
        lines.append("")
    if running:
        lines.append("RUNNING")
        for j in running:
            where = f"{j['workflow']} · {j['stage']}" + (f" R{j['round']}" if j.get("round") else "")
            agents = ", ".join(j.get("running_agents") or []) or "—"
            lines.append(f"  {j['id']:<28} {where:<26} agents: {agents}")
            if j.get("reviews"):
                lines.append(f"  {'':<28} {_chips(j['reviews'])}")
        lines.append("")
    if done or other:
        lines.append("FINISHED / OTHER")
        for j in done + other:
            tag = " (v1, read-only)" if j.get("legacy") else ""
            lines.append(f"  {j['id']:<28} {j['status']}{tag}")
        lines.append("")
    return "\n".join(lines)


def _job_text(job: dict) -> str:
    lines = [f"{job['id']} · {job.get('workflow_title', job['workflow'])} · {job['status']}",
             f"  {job['title']}", f"  stage {job['stage']}, round {job.get('round', 0)}, branch {job['branch']}", ""]
    if job.get("human"):
        h = job["human"]
        lines += [f"  Needs you ({h['kind']}): {h['reason']}"] + [f"    {c}" for c in h.get("commands", [])] + [""]
    for sid, step in job["steps"].items():
        if step.get("kind") == "skip":
            continue
        lines.append(f"  {sid:<28} {step.get('agent') or 'orchestrator':<22} {step.get('value') or step['status']}")
    return "\n".join(lines) + "\n"


def cmd_report(args) -> int:
    store = Store(_paths(args))
    job = store.load(store.resolve(args.job))
    path = report.write(store, job)
    _print({"report": str(path)})
    return 0


def cmd_doctor(args) -> int:
    data = doctor.report()
    try:
        data["repo"] = str(_paths(args).root)
    except FactoryError as exc:
        data["repo"] = None
        data["repo_error"] = str(exc)
    if args.json:
        _print(data)
        return 0
    for c in data["checks"]:
        sys.stdout.write(f"{'ok ' if c['ok'] else 'WARN'}  {c['check']}: {c['detail']}\n")
    sys.stdout.write("\nTools\n")
    for t in data["tools"]:
        sys.stdout.write(f"  {'✓' if t['available'] else '·'} {t['name']:<14} {t['version'] or 'not found':<14} {t['purpose']}\n")
    return 0 if all(c["ok"] for c in data["checks"] if c["check"] in ("python >= 3.9", "git installed")) else 1


def cmd_catalog(args) -> int:
    data = catalog.catalog()
    if args.json:
        _print(data)
        return 0
    for d in data["domains"]:
        sys.stdout.write(f"\n{d['title']}\n")
        for a in data["agents"]:
            if a["domain"] == d["id"]:
                sys.stdout.write(f"  {a['name']:<24} {a['model']:<7} {a['summary']}\n")
    sys.stdout.write("\nWorkflows\n")
    for w in data["workflows"]:
        sys.stdout.write(f"  {w['id']:<10} {w['summary']}\n")
    return 0


def cmd_plan_validate(args) -> int:
    try:
        data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _print({"ok": False, "problems": [str(exc)], "order": []})
        return 1
    problems, order = plan.validate(data)
    _print({"ok": not problems, "problems": problems, "order": order})
    return 0 if not problems else 1


def cmd_serve(args) -> int:
    from . import server

    return server.serve(_paths(args) if not args.demo else None, host=args.host, port=args.port,
                        open_browser=args.open, demo=args.demo)


def cmd_index(args) -> int:
    paths = _paths(args)
    ensure_state_dir(paths)
    Store(paths).write_index()
    _print({"index": str(paths.index)})
    return 0


# ---- backlog -----------------------------------------------------------------------------------
BACKLOG_HEADER = "# Backlog\n\n`/factory next` builds the first unchecked item and checks it off with its job id.\n\n"


def _backlog_path(paths: RepoPaths) -> Path:
    if not paths.backlog.exists() and paths.legacy_backlog.exists():
        return paths.legacy_backlog
    return paths.backlog


def _backlog_items(text: str) -> list[dict]:
    items = []
    for i, line in enumerate(text.splitlines()):
        m = re.match(r"^- \[( |x|X)\] (.+)$", line)
        if m:
            body = m.group(2)
            job = None
            jm = re.search(r" → (\S+)$", body)
            if jm:
                job = jm.group(1)
                body = body[: jm.start()]
            items.append({"line": i, "done": m.group(1) != " ", "text": body.strip(), "job": job})
    return items


def _backlog_link(paths: RepoPaths, item: str, job_id: str) -> None:
    path = _backlog_path(paths)
    with file_lock(paths.state / ".backlog.lock"):
        if not path.exists():
            return
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if line == f"- [x] {item} → claimed":
                lines[i] = f"- [x] {item} → {job_id}"
                write_text_atomic(path, "\n".join(lines) + "\n")
                return


def cmd_backlog(args) -> int:
    paths = _paths(args)
    ensure_state_dir(paths)
    path = _backlog_path(paths)
    with file_lock(paths.state / ".backlog.lock"):
        text = path.read_text(encoding="utf-8") if path.exists() else BACKLOG_HEADER
        if args.action == "list":
            items = _backlog_items(text)
            if args.json:
                _print({"path": str(path), "items": items})
            else:
                for it in items:
                    sys.stdout.write(f"{'[x]' if it['done'] else '[ ]'} {it['text']}{' → ' + it['job'] if it['job'] else ''}\n")
            return 0
        if args.action == "add":
            item = " ".join(args.text).strip()
            if not item or "\n" in item or len(item) > 500:
                raise FactoryError("a backlog item is one line of up to 500 characters")
            if not text.endswith("\n"):
                text += "\n"
            write_text_atomic(path, text + f"- [ ] {item}\n")
            _print({"added": item, "path": str(path)})
            return 0
        if args.action == "next":
            lines = text.splitlines()
            for i, line in enumerate(lines):
                if line.startswith("- [ ] "):
                    item = line[6:].strip()
                    lines[i] = f"- [x] {item} → claimed"
                    write_text_atomic(path, "\n".join(lines) + "\n")
                    _print({"item": item, "claimed_at": now_iso()})
                    return 0
            _print({"item": None})
            return 0
    raise FactoryError(f"unknown backlog action {args.action}")


# ---- parser ------------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="factoryctl", description="Software Factory engine for Claude Code")
    p.add_argument("--version", action="version", version=f"factoryctl {__version__}")
    p.add_argument("--repo", help="repository path (default: the current directory's repo)")
    p.add_argument("--session", help="driving Claude session id (default: $CLAUDE_CODE_SESSION_ID)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create .factory/ in this repo").set_defaults(fn=cmd_init)

    s = sub.add_parser("new", help="create a job and return its first action")
    s.add_argument("request", nargs="+", help="the request, or - to read stdin")
    s.add_argument("--workflow", "-w", default="feature", choices=registry.workflow_ids())
    s.add_argument("--size", "-s", default="M", choices=list(registry.SIZES))
    s.add_argument("--why", help="one line: why this workflow and size")
    s.add_argument("--title")
    s.add_argument("--param", action="append", help="key=value (e.g. agent=sql-engineer, repo=true)")
    s.add_argument("--backlog-item", help="link the claimed backlog line to the new job")
    s.set_defaults(fn=cmd_new)

    s = sub.add_parser("next", help="the next action for a job")
    s.add_argument("job")
    s.add_argument("--redeliver", action="store_true", help="re-send spawn entries for issued steps")
    s.set_defaults(fn=cmd_next)

    for name, fn, helptext in (("approve", cmd_approve, "approve a plan gate or an escalated job"),
                               ("report", cmd_report, "write final-report.md")):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("job")
        s.set_defaults(fn=fn)

    s = sub.add_parser("done", help="record that a step finished")
    s.add_argument("job")
    s.add_argument("step")
    s.add_argument("--attempt", type=int, required=True)
    s.add_argument("--status", default="ok", choices=["ok", "error", "killed"])
    s.add_argument("--agent-id")
    s.add_argument("--via", choices=["spawn", "message", "fallback", "respawn", "inline"])
    s.add_argument("--note")
    s.set_defaults(fn=cmd_done)

    s = sub.add_parser("resume", help="take over a job after a crash or from another session")
    s.add_argument("job")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_resume)

    s = sub.add_parser("rework", help="send a job back with a note")
    s.add_argument("job")
    s.add_argument("--note", required=True, help="what to change, or - for stdin")
    s.set_defaults(fn=cmd_rework)

    s = sub.add_parser("cancel", help="cancel a job (branch and worktree are kept)")
    s.add_argument("job")
    s.add_argument("--reason")
    s.set_defaults(fn=cmd_cancel)

    s = sub.add_parser("tell", help="message an agent or the orchestrator mid-run")
    s.add_argument("job")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--step")
    g.add_argument("--agent")
    g.add_argument("--orchestrator", action="store_true")
    s.add_argument("text", nargs="+")
    s.set_defaults(fn=cmd_tell)

    s = sub.add_parser("messages", help="list a job's messages and receipts")
    s.add_argument("job")
    s.set_defaults(fn=cmd_messages)

    s = sub.add_parser("status", help="board of all jobs, or one job")
    s.add_argument("job", nargs="?")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("doctor", help="check the environment")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("catalog", help="agents and workflows")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_catalog)

    s = sub.add_parser("plan-validate", help="validate a plan.json file")
    s.add_argument("file")
    s.set_defaults(fn=cmd_plan_validate)

    s = sub.add_parser("serve", help="start the Factory Console")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=0, help="0 picks a free port")
    s.add_argument("--open", action="store_true", help="open the browser")
    s.add_argument("--demo", action="store_true", help="serve labelled demo data instead of a repo")
    s.set_defaults(fn=cmd_serve)

    sub.add_parser("index", help="rebuild .factory/index.json").set_defaults(fn=cmd_index)

    s = sub.add_parser("backlog", help="list, add or claim backlog items")
    s.add_argument("action", choices=["list", "add", "next"])
    s.add_argument("text", nargs="*")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_backlog)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.fn(args) or 0)
    except FactoryError as exc:
        sys.stderr.write(f"factoryctl: {exc}\n")
        return exc.code or 1
    except KeyboardInterrupt:
        return 130
