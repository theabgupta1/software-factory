"""Deterministic final report for a job, generated from state and artifacts (no model tokens)."""

from __future__ import annotations

from pathlib import Path

from . import contracts, gitops, telemetry
from .util import read_jsonl, seconds_between, write_text_atomic


def _fmt_seconds(value: float | None) -> str:
    if value is None:
        return "—"
    value = int(value)
    if value < 60:
        return f"{value}s"
    if value < 3600:
        return f"{value // 60}m {value % 60:02d}s"
    return f"{value // 3600}h {(value % 3600) // 60:02d}m"


def build(store, job: dict) -> str:
    job_dir: Path = store.job_dir(job["id"])
    events = read_jsonl(job_dir / "events.jsonl")
    agents = telemetry.agent_runs(store, job)
    lines = [
        "STATUS: READY",
        "",
        f"# {job['id']}: {job['title']}",
        "",
        "## Summary",
        f"- Workflow: {job.get('workflow_title', job['workflow'])} (size {job.get('size')})",
        f"- Outcome: **{job.get('outcome') or job['status']}**",
        f"- Branch: `{job['branch']}` from `{job['base_branch']}`",
    ]
    if job.get("merge"):
        lines.append(f"- Merge commit: `{(job['merge'].get('sha') or '')[:12]}`")
    if job.get("intake", {}).get("why"):
        lines.append(f"- Why this workflow: {job['intake']['why']}")
    if events:
        lines.append(f"- Elapsed: {_fmt_seconds(seconds_between(events[0]['ts'], events[-1]['ts']))}")
    tokens = sum((a.get("usage") or {}).get("total", 0) for a in agents)
    if tokens:
        lines.append(f"- Tokens recorded by hooks: {tokens:,}")
    lines += ["", "## Request", "", *("> " + ln for ln in job.get("request", "").splitlines()), ""]

    if job.get("human"):
        h = job["human"]
        lines += ["## Waiting on you", "", f"**{h.get('kind')}** — {h.get('reason')}", ""]
        for cmd in h.get("commands", []):
            lines.append(f"- `{cmd}`")
        lines.append("")

    build_md = job_dir / "build.md"
    if build_md.exists():
        ac = contracts.section_text(build_md.read_text(encoding="utf-8", errors="replace"), "Acceptance criteria")
        if ac:
            lines += ["## Acceptance criteria (from build.md)", "", ac, ""]

    if job.get("rounds"):
        lines += ["## Review rounds", "", "| Round | " + " | ".join(job["rounds"][-1]["members"]) + " |",
                  "|---|" + "---|" * len(job["rounds"][-1]["members"])]
        for r in job["rounds"]:
            cells = [r["verdicts"].get(k, "—") or "—" for k in job["rounds"][-1]["members"]]
            lines.append(f"| {r['n']} | " + " | ".join(cells) + " |")
        lines.append("")

    lines += ["## Steps", "", "| Step | Agent | Result | Attempts | Duration | Tokens |", "|---|---|---|---|---|---|"]
    by_step = {a["step"]: a for a in agents if a.get("step")}
    for sid, step in job.get("steps", {}).items():
        if step.get("kind") == "skip":
            continue
        a = by_step.get(sid, {})
        lines.append(
            f"| `{sid}` | {step.get('agent') or 'orchestrator'} | {step.get('value') or step.get('status')} | "
            f"{step.get('attempt', 0)} | {_fmt_seconds(seconds_between(step.get('issued_at'), step.get('done_at')))} | "
            f"{(a.get('usage') or {}).get('total', '—')} |"
        )
    lines.append("")

    if job.get("warnings"):
        lines += ["## Warnings", "", *(f"- {w}" for w in job["warnings"]), ""]

    if job.get("status") == "merged" or Path(job["worktree"]).exists():
        try:
            ref = job["merge"]["sha"] if job.get("merge") else job["branch"]
            base = job.get("base_sha") or job["base_branch"]
            files = gitops.diff(Path(job["repo"]), base, ref)["files"]
            if files:
                lines += ["## Files changed", ""]
                for f in files[:60]:
                    delta = "binary" if f["binary"] else f"+{f['added']} −{f['removed']}"
                    lines.append(f"- `{f['path']}` ({delta})")
                lines.append("")
        except Exception:
            pass

    lines += ["## Artifacts", ""]
    for p in sorted(job_dir.rglob("*.md")):
        if p.name != "final-report.md":
            lines.append(f"- `{p.relative_to(job_dir)}`")
    lines.append("")
    return "\n".join(lines)


def write(store, job: dict) -> Path:
    path = store.job_dir(job["id"]) / "final-report.md"
    write_text_atomic(path, build(store, job))
    return path
