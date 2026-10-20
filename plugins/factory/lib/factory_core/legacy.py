"""Read-only support for v1 jobs written by the original factory (`factory/jobs/<id>/job.json`)."""

from __future__ import annotations

from pathlib import Path

from .util import first_line

V1_STAGES = {"spec", "build", "review", "approve", "merged", "needs-human"}
REVIEWERS_V1 = ("security", "ux", "ui", "code")


def status_of(stage: str | None) -> str:
    if stage == "merged":
        return "merged"
    if stage == "needs-human":
        return "needs-human"
    return "stale"  # a v1 job mid-flight can't be driven by v2; shown as stale


def summarize_v1(job: dict, job_dir: Path) -> dict:
    stage = job.get("stage")
    status = status_of(stage)
    decision = job_dir / "decision.md"
    reason = ""
    if decision.exists():
        reason = first_line(decision.read_text(encoding="utf-8", errors="replace"))
    return {
        "id": job.get("id", job_dir.name),
        "title": job.get("feature", job_dir.name),
        "workflow": "feature",
        "size": None,
        "status": status,
        "stage": stage,
        "round": job.get("round", 0),
        "branch": job.get("branch"),
        "created_at": None,
        "updated_at": None,
        "attention": None,
        "running": [],
        "running_agents": [],
        "running_detail": [],
        "counters": {},
        "reviews": job.get("reviews") or {},
        "outcome": "merged" if status == "merged" else None,
        "pending_messages": 0,
        "legacy": True,
        "legacy_note": f"v1 job ({reason or stage}); read-only",
    }


def to_detail(job: dict, job_dir: Path) -> dict:
    """A v2-shaped detail view of a v1 job so the console can show its files and verdicts."""
    summary = summarize_v1(job, job_dir)
    steps: dict[str, dict] = {}
    if (job_dir / "spec.md").exists():
        steps["spec"] = {"stage": "spec", "agent": "product-analyst", "status": "done", "output": "spec.md"}
    if (job_dir / "build.md").exists():
        steps["build.r1/builder"] = {"stage": "build", "agent": "builder", "status": "done", "output": "build.md"}
    rounds = []
    for rdir in sorted(job_dir.glob("round-*")):
        try:
            n = int(rdir.name.split("-", 1)[1])
        except ValueError:
            continue
        verdicts = {}
        for key in REVIEWERS_V1:
            f = rdir / f"review-{key}.md"
            if f.exists():
                line = first_line(f.read_text(encoding="utf-8", errors="replace"))
                verdicts[key] = line.split(":", 1)[-1].strip() if line.startswith("VERDICT:") else "invalid"
                agent = {"security": "security-engineer", "ux": "ux-reviewer", "ui": "ui-reviewer",
                         "code": "code-reviewer"}[key]
                steps[f"review.r{n}/{key}"] = {"stage": "review", "agent": agent, "status": "done",
                                                "output": f"{rdir.name}/review-{key}.md", "verdict": verdicts[key]}
        rounds.append({"n": n, "verdicts": verdicts})
    if (job_dir / "decision.md").exists():
        steps["approve"] = {"stage": "approve", "agent": "approver", "status": "done", "output": "decision.md"}
    return {
        **summary,
        "schema": 1,
        "request": job.get("feature"),
        "worktree": job.get("worktree"),
        "steps": steps,
        "rounds": rounds,
        "panel": [{"key": k, "agent": steps.get(f"review.r1/{k}", {}).get("agent"), "why": "v1 fixed panel"}
                  for k in REVIEWERS_V1],
    }
