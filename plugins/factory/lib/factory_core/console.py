"""Payloads for the Factory Console: everything the UI shows is computed here from real state."""

from __future__ import annotations

import re
from pathlib import Path

from . import __version__, catalog, config, contracts, gitops, legacy, registry, telemetry
from .paths import RepoPaths
from .store import Store
from .util import FactoryError, first_line, now_iso, read_json, read_jsonl, seconds_between

ARTIFACT_LIMIT = 1_000_000
GROUPS = [
    ("Job", re.compile(r"^(spec|context|research|design|data-design|infra-design|diagnosis|test-plan|perf-report|"
                       r"test-report|plan|build|decision|report|final-report|security-audit|audit-[\w-]+|"
                       r"dq-report|ops-report|rework-\d+|decision-\d+)\.(md|json)$")),
    ("Tasks", re.compile(r"^tasks/")),
    ("Rounds", re.compile(r"^round-\d+/")),
]


def repo_state(paths: RepoPaths) -> dict:
    store = Store(paths)
    try:
        cfg = config.load(paths)
        base = cfg["base_branch"]
        cfg_error = None
    except FactoryError as exc:
        base, cfg_error = None, str(exc)
    jobs = store.summaries()
    backlog_path = paths.backlog if paths.backlog.exists() or not paths.legacy_backlog.exists() else paths.legacy_backlog
    backlog = []
    if backlog_path.exists():
        for line in backlog_path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^- \[( |x|X)\] (.+?)(?: → (\S+))?$", line)
            if m:
                backlog.append({"done": m.group(1) != " ", "text": m.group(2), "job": m.group(3)})
    counts: dict[str, int] = {}
    for j in jobs:
        counts[j["status"]] = counts.get(j["status"], 0) + 1
    return {
        "version": __version__,
        "generated_at": now_iso(),
        "repo": {"name": paths.root.name, "root": str(paths.root), "base_branch": base,
                 "initialized": paths.state.exists(), "config_error": cfg_error},
        "jobs": jobs,
        "counts": counts,
        "attention": sum(1 for j in jobs if j.get("attention")),
        "backlog": backlog,
    }


def _legacy_dir(paths: RepoPaths, job_id: str) -> Path | None:
    d = paths.legacy_jobs / job_id
    return d if (d / "job.json").exists() else None


def job_dir_for(paths: RepoPaths, job_id: str) -> tuple[Path, bool]:
    if not re.match(r"^[0-9]{3,4}-[a-z0-9-]+$", job_id or ""):
        raise FactoryError("bad job id")
    d = paths.job_dir(job_id)
    if (d / "job.json").exists():
        return d, False
    ld = _legacy_dir(paths, job_id)
    if ld is not None:
        return ld, True
    raise FactoryError(f"no job {job_id}")


def artifacts(job_dir: Path, steps: dict) -> list[dict]:
    by_output = {s.get("output"): (sid, s) for sid, s in steps.items() if s.get("output")}
    out = []
    for p in sorted(job_dir.rglob("*")):
        if not p.is_file() or p.suffix not in (".md", ".json") or p.name in ("job.json", "workflow.json"):
            continue
        rel = p.relative_to(job_dir).as_posix()
        if rel.startswith("entries/"):
            continue
        text = p.read_text(encoding="utf-8", errors="replace")[:4000] if p.suffix == ".md" else ""
        line = first_line(text)
        from .contracts import parse_header

        value, tone = parse_header(line)
        sid, step = by_output.get(rel, (None, {}))
        group = next((g for g, rx in GROUPS if rx.search(rel)), "Other")
        out.append({"path": rel, "group": group, "size": p.stat().st_size, "header": value, "tone": tone,
                    "step": sid, "agent": step.get("agent"), "modified": p.stat().st_mtime})
    order = {"Job": 0, "Tasks": 1, "Rounds": 2, "Other": 3}
    return sorted(out, key=lambda a: (order[a["group"]], _round_of(a["path"]), a["path"]))


def _round_of(path: str) -> int:
    m = re.match(r"^round-(\d+)/", path)
    return int(m.group(1)) if m else 0


def read_artifact(job_dir: Path, rel: str) -> dict:
    if not rel or rel.startswith("/") or "\\" in rel or ".." in Path(rel).parts:
        raise FactoryError("bad artifact path")
    target = (job_dir / rel).resolve()
    if job_dir.resolve() not in target.parents or not target.is_file():
        raise FactoryError("no such artifact")
    if target.suffix not in (".md", ".json", ".txt"):
        raise FactoryError("unsupported artifact type")
    size = target.stat().st_size
    with open(target, encoding="utf-8", errors="replace") as fh:
        text = fh.read(ARTIFACT_LIMIT)
    return {"path": rel, "text": text, "truncated": size > ARTIFACT_LIMIT, "size": size}


def _last_activity(transcript: str | None) -> str | None:
    if not transcript:
        return None
    import datetime as _dt
    import os

    try:
        mtime = os.path.getmtime(transcript)
    except OSError:
        return None
    return _dt.datetime.fromtimestamp(mtime, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def crew(store: Store, job: dict, stall_minutes: int = 20) -> list[dict]:
    """One entry per agent invocation (step attempt), joined with telemetry and messages."""
    roster = registry.roster()["agents"]
    runs = {}
    for r in telemetry.agent_runs(store, job):
        if r.get("step"):
            runs.setdefault(r["step"], []).append(r)
    messages = store.messages(job["id"]) if not job.get("legacy") else []
    out = []
    for sid, step in (job.get("steps") or {}).items():
        agent = step.get("agent")
        if not agent or step.get("kind") in ("skip", "inline"):
            continue
        meta = roster.get(agent, {})
        tele = sorted(runs.get(sid, []), key=lambda r: r.get("spawned_at") or "")
        last = tele[-1] if tele else {}
        msgs = [m for m in messages if (m["to"].get("step") == sid) or (m["to"].get("agent") == agent and (
            m["status"] == "queued" or (m.get("delivered") or {}).get("step") == sid))]
        last_activity = _last_activity(last.get("transcript")) if step.get("status") == "issued" else None
        quiet = seconds_between(last_activity or step.get("issued_at"), now_iso()) if step.get("status") == "issued" else None
        out.append({
            "step": sid,
            "stage": step.get("stage"),
            "kind": step.get("kind"),
            "role": step.get("role"),
            "agent": agent,
            "title": meta.get("title", agent),
            "domain": meta.get("domain"),
            "hue": meta.get("hue"),
            "emblem": meta.get("emblem"),
            "model": last.get("model") or step.get("model") or meta.get("model"),
            "status": step.get("status"),
            "value": step.get("value"),
            "tone": step.get("tone"),
            "attempt": step.get("attempt"),
            "round": step.get("round"),
            "why": step.get("why"),
            "output": step.get("output"),
            "name": step.get("name"),
            "issued_at": step.get("issued_at"),
            "done_at": step.get("done_at"),
            "seconds": seconds_between(step.get("issued_at"), step.get("done_at") or (
                now_iso() if step.get("status") == "issued" else None)),
            "agent_id": last.get("agent_id") or step.get("agent_id"),
            "usage": last.get("usage"),
            "tool_uses": last.get("tool_uses"),
            "has_transcript": bool(last.get("transcript")),
            "problems": step.get("problems") or [],
            "warnings": step.get("warnings") or [],
            "messages": msgs,
            "summary": meta.get("summary"),
            "last_activity": last_activity,
            "quiet_seconds": quiet,
            "stalled": bool(quiet is not None and quiet > stall_minutes * 60),
        })
    status_rank = {"issued": 0, "invalid": 1, "failed": 1, "lost": 2, "done": 3}
    out.sort(key=lambda c: (status_rank.get(c["status"], 4), c.get("issued_at") or ""))
    return out


def stages_view(job: dict, wf: dict) -> list[dict]:
    """Workflow stages with their real state for the stage rail."""
    steps = job.get("steps") or {}
    current = job.get("stage")
    ids = [s["id"] for s in wf.get("stages", [])]
    cur_idx = ids.index(current) if current in ids else -1
    out = []
    for i, s in enumerate(wf.get("stages", [])):
        related = [v for k, v in steps.items() if v.get("stage") == s["id"] or k == f"skip:{s['id']}"]
        skipped = any(v.get("kind") == "skip" for v in related)
        if job.get("status") in ("merged", "delivered") and not skipped:
            state = "done" if (related or s["kind"] in ("merge", "deliver", "setup", "gate")) else "skipped"
        elif skipped:
            state = "skipped"
        elif i < cur_idx:
            state = "done"
        elif i == cur_idx:
            state = {"needs-human": "attention", "awaiting-approval": "attention", "cancelled": "cancelled"}.get(
                job.get("status"), "active")
        else:
            state = "upcoming"
        out.append({"id": s["id"], "kind": s["kind"], "title": s.get("title", s["id"]), "state": state,
                    "agent": s.get("agent") if not str(s.get("agent", "")).startswith("$") else None})
    return out


_CHECK = re.compile(r"^\s*[-*]?\s*`?([^`:\n]{2,160}?)`?\s*[:—–-]\s*(pass(?:ed)?|fail(?:ed)?|ok|error|not run|skipped)\b(.*)$",
                    re.I)
_AC = re.compile(r"^\s*(?:\d+\.|[-*])\s*\[( |x|X)\]\s*(.+)$")
_FINDING = re.compile(r"^\s*\d+\.\s+(.+)$")


def quality(job_dir: Path, job: dict) -> dict:
    """Checks and criteria parsed from what agents actually wrote; nothing is inferred or invented."""
    checks, criteria, findings = [], [], []
    build = job_dir / "build.md"
    if build.exists():
        text = build.read_text(encoding="utf-8", errors="replace")
        for line in contracts.section_text(text, "Validation").splitlines():
            m = _CHECK.match(line)
            if m:
                checks.append({"source": "build.md", "check": m.group(1).strip(), "result": _norm(m.group(2)),
                               "detail": m.group(3).strip(" .")})
        for line in contracts.section_text(text, "Acceptance criteria").splitlines():
            m = _AC.match(line)
            if m:
                body = m.group(2).strip()
                crit, _, evidence = body.partition(":")
                criteria.append({"met": m.group(1) != " ", "criterion": crit.strip(), "evidence": evidence.strip()})
    rounds = job.get("rounds") or []
    latest = rounds[-1]["n"] if rounds else None
    for rdir in sorted(job_dir.glob("round-*")):
        n = _round_of(rdir.name + "/")
        for f in sorted(rdir.glob("review-*.md")):
            key = f.stem.replace("review-", "")
            text = f.read_text(encoding="utf-8", errors="replace")
            for line in contracts.section_text(text, "Checks").splitlines():
                m = _CHECK.match(line)
                if m:
                    checks.append({"source": f"round-{n}/{f.name}", "check": m.group(1).strip(),
                                   "result": _norm(m.group(2)), "detail": m.group(3).strip(" .")})
            if n == latest and first_line(text) == "VERDICT: CHANGES":
                for line in contracts.section_text(text, "Findings").splitlines():
                    m = _FINDING.match(line)
                    if m and m.group(1).strip().lower() not in ("none.", "none"):
                        findings.append({"reviewer": key, "round": n, "text": m.group(1).strip()})
    matrix = []
    for r in rounds:
        matrix.append({"round": r["n"], "verdicts": r.get("verdicts") or {}, "result": r.get("result")})
    return {"checks": checks, "criteria": criteria, "open_findings": findings, "matrix": matrix,
            "warnings": job.get("warnings") or [], "decision": job.get("decision")}


def _norm(word: str) -> str:
    w = word.lower()
    if w.startswith("pass") or w == "ok":
        return "pass"
    if w.startswith("fail") or w == "error":
        return "fail"
    return "not-run"


def job_detail(paths: RepoPaths, job_id: str) -> dict:
    job_dir, is_legacy = job_dir_for(paths, job_id)
    store = Store(paths)
    if is_legacy:
        v1 = read_json(job_dir / "job.json", default={})
        job = legacy.to_detail(v1, job_dir)
        wf = registry.load_workflow("feature")
        return {"job": job, "legacy": True, "events": [], "crew": crew(store, {**job, "legacy": True}),
                "artifacts": artifacts(job_dir, job["steps"]), "messages": [], "stages": stages_view(
                    {**job, "stage": job.get("stage"), "status": job.get("status")}, wf),
                "workflow": wf, "quality": quality(job_dir, job), "plan": None}
    job = store.load(job_id)
    wf = read_json(job_dir / "workflow.json", default=None) or registry.load_workflow(job["workflow"])
    plan_data = read_json(job_dir / "plan.json", default=None)
    try:
        stall = int(config.load(paths).get("stall_minutes") or 20)
    except FactoryError:
        stall = 20
    return {
        "job": job,
        "legacy": False,
        "events": read_jsonl(job_dir / "events.jsonl")[-500:],
        "crew": crew(store, job, stall),
        "artifacts": artifacts(job_dir, job.get("steps") or {}),
        "messages": store.messages(job_id),
        "stages": stages_view(job, wf),
        "workflow": wf,
        "quality": quality(job_dir, job),
        "plan": plan_data if isinstance(plan_data, dict) else None,
    }


def diff(paths: RepoPaths, job_id: str) -> dict:
    job_dir, is_legacy = job_dir_for(paths, job_id)
    if is_legacy:
        return {"available": False, "reason": "v1 jobs do not record the base commit"}
    job = Store(paths).load(job_id)
    base = job.get("base_sha") or job["base_branch"]
    if job.get("merge") and job["merge"].get("sha"):
        ref = job["merge"]["sha"]
    elif gitops.branch_exists(paths.root, job["branch"]):
        ref = job["branch"]
    else:
        return {"available": False, "reason": f"the branch {job['branch']} no longer exists"}
    if not job.get("base_sha"):
        return {"available": False, "reason": "the job has not created its worktree yet"}
    data = gitops.diff(paths.root, base, ref)
    data["commits"] = gitops.log(paths.root, base, ref)
    data["available"] = True
    data["base"] = base
    data["ref"] = ref
    return data


def activity(paths: RepoPaths, job_id: str, agent_id: str, after: int = 0, extra_roots: list[Path] | None = None) -> dict:
    job_dir, _ = job_dir_for(paths, job_id)
    store = Store(paths)
    try:
        job = store.load(job_id)
    except FactoryError:
        return {"items": [], "cursor": 0, "missing": True}
    run = next((r for r in telemetry.agent_runs(store, job) if r.get("agent_id") == agent_id), None)
    if run is None:
        raise FactoryError("unknown agent")
    path = telemetry.transcript_allowed(run.get("transcript"), extra_roots=extra_roots)
    if path is None:
        return {"items": [], "cursor": 0, "missing": True}
    return telemetry.activity(path, after=after)


def settings(paths: RepoPaths | None) -> dict:
    from . import doctor
    from .paths import PLUGIN_ROOT

    policy = read_json(Path(PLUGIN_ROOT, "guard-policy.json"), default={"rules": []})
    hooks = read_json(Path(PLUGIN_ROOT, "hooks", "hooks.json"), default={"hooks": {}})
    cfg, cfg_error, cfg_path = None, None, None
    if paths is not None:
        cfg_path = str(paths.config)
        try:
            cfg = config.load(paths)
        except FactoryError as exc:
            cfg_error = str(exc)
    return {
        "version": __version__,
        "plugin_root": str(PLUGIN_ROOT),
        "config": cfg,
        "config_path": cfg_path,
        "config_exists": bool(paths and paths.config.exists()),
        "config_error": cfg_error,
        "defaults": config.DEFAULTS,
        "doctor": doctor.report(),
        "guard": [{k: r[k] for k in ("id", "decision", "reason")} for r in policy.get("rules", [])],
        "hooks": sorted(hooks.get("hooks", {}).keys()),
    }


def catalog_payload() -> dict:
    return catalog.catalog()
