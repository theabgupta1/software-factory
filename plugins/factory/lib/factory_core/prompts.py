"""The prompts factoryctl hands the orchestrator for each agent step.

Every prompt starts with a fixed header (parsed by hooks and tests) followed by the step's instruction.
Agents get their context from files named here, never from the conversation.
"""

from __future__ import annotations

import re
from pathlib import Path

HEADER_RE = re.compile(r"^FACTORY-STEP job=(?P<job>\S+) step=(?P<step>\S+) attempt=(?P<attempt>\d+)$", re.M)
FIELD_RE = re.compile(r"^(Job folder|Worktree|Branch|Base|Output|Round|Mode|Port|Inputs): (.+)$", re.M)


def header(job: dict, step_id: str, attempt: int, job_dir: Path, output: Path, *, round_no: int | None = None,
           mode: str | None = None, port: int | None = None, inputs: list[str] | None = None) -> str:
    lines = [
        f"FACTORY-STEP job={job['id']} step={step_id} attempt={attempt}",
        f"Job folder: {job_dir}",
        f"Worktree: {job['worktree']}",
        f"Branch: {job['branch']}",
        f"Base: {job['base_branch']}",
        f"Output: {output}",
    ]
    if round_no:
        lines.append(f"Round: {round_no}")
    if mode:
        lines.append(f"Mode: {mode}")
    if port:
        lines.append(f"Port: {port}")
    if inputs:
        lines.append("Inputs: " + ", ".join(inputs))
    return "\n".join(lines)


def parse_header(prompt: str) -> dict | None:
    """Inverse of `header`: returns job/step/attempt plus the labelled fields, or None."""
    m = HEADER_RE.search(prompt or "")
    if not m:
        return None
    out: dict = {"job": m.group("job"), "step": m.group("step"), "attempt": int(m.group("attempt"))}
    for fm in FIELD_RE.finditer(prompt[m.start():m.start() + 4000]):
        out[fm.group(1).lower().replace(" ", "_")] = fm.group(2).strip()
    return out


def _quote(text: str) -> str:
    return "\n".join("> " + line if line else ">" for line in text.strip().splitlines())


def instruction(kind: str, *, job: dict, stage: dict, mode: str | None, round_no: int | None,
                task: dict | None = None, panel: list[dict] | None = None, member: dict | None = None) -> str:
    request = job.get("request", "")
    agent = (member or stage).get("agent")
    if kind == "spec":
        return f"Write the specification for this request.\n\nRequest (verbatim):\n{_quote(request)}"
    if kind == "agent":
        intro = {
            "debugger": "Reproduce the problem and find its root cause.",
            "codebase-analyst": "Map the parts of the codebase this job touches.",
            "researcher": "Investigate this question and recommend an approach with evidence.",
            "architect": "Design the change and write the task plan.",
            "data-architect": "Design the data model and write the task plan.",
            "cloud-architect": "Design the infrastructure and write the task plan.",
            "performance-engineer": "Measure the current performance and find the bottleneck.",
            "test-engineer": "Pin the current behaviour with characterisation tests before anything changes.",
            "security-engineer": "Audit the codebase for security problems.",
        }.get(agent or "", "Do your part of this job.")
        if mode == "synthesis":
            intro = "Synthesise the audit files in the job folder into one prioritised improvement plan."
        if mode == "audit" and agent != "security-engineer":
            intro = "Audit the existing codebase from your angle (no code changes)."
        return f"{intro}\n\nRequest (verbatim):\n{_quote(request)}"
    if kind == "build":
        n = round_no or 1
        lines = {
            "implement": f"Build round {n}. Implement spec.md (and design.md if it exists). Write build.md.",
            "integrate": (
                f"Integrate round {n}. The plan's tasks are committed on the branch; their reports are in tasks/. "
                "Run the full validation, fix integration problems, and write build.md covering every "
                "acceptance criterion."
            ),
            "fix": f"Build round {n}. Fix the defect described in diagnosis.md, add a regression test that fails "
                   "before the fix and passes after it, and write build.md.",
            "quick": f"Build round {n}. Make the change the request asks for, verify it, and write build.md.",
            "infra": f"Build round {n}. Implement infra-design.md as code. Run format, validate, lint and plan only — "
                     "never apply, deploy or touch live resources. Write build.md with the plan output summary.",
            "refactor": f"Build round {n}. Refactor per design.md without changing behaviour; the characterisation "
                        "tests from test-report.md must still pass. Write build.md.",
            "perf": f"Build round {n}. Optimise the bottleneck identified in perf-report.md, keep behaviour identical, "
                    "re-run the benchmark, and write build.md with before/after numbers.",
            "docs": f"Build round {n}. Write or update the documentation the request asks for, verify every command "
                    "and path against the code, and write build.md.",
        }
        text = lines.get(mode or "implement", lines["implement"])
        return f"{text}\n\nRequest (verbatim):\n{_quote(request)}"
    if kind == "task":
        assert task is not None
        parts = [
            f"Task {task['id']}: {task['title']}",
            "Owns (only change these unless unavoidable, and say why): " + ", ".join(task.get("owns") or []),
        ]
        if task.get("depends_on"):
            parts.append("Builds on: " + ", ".join(task["depends_on"]) + " (already committed; read tasks/ for their reports)")
        if task.get("acceptance"):
            parts.append("Acceptance criteria to satisfy: " + ", ".join(task["acceptance"]))
        if task.get("notes"):
            parts.append("Architect's notes: " + str(task["notes"]))
        parts.append(f"Commit your work with the trailer line `Factory-Task: {task['id']}` in every commit message, "
                     f"leave the tree clean, and write tasks/{task['id']}.md.")
        return "\n".join(parts)
    if kind == "review":
        names = ", ".join(m["key"] for m in (panel or []))
        why = (member or {}).get("why", "")
        return (f"Review round {round_no}. Panel this round: {names}. You are on it because: {why}.\n"
                f"Write your verdict to the Output file.")
    if kind == "approve":
        return f"Final round: {round_no}. Decide APPROVE or ESCALATE."
    return "Do your part of this job."


def address_reviews(job: dict, round_no: int, round_dir: Path, attempt: int = 1) -> str:
    return (
        f"FACTORY-STEP job={job['id']} step=build.r{round_no}/builder attempt={attempt}\n"
        f"Round {round_no - 1} reviews are in {round_dir}. Address every review with VERDICT: CHANGES, commit, "
        f"and update build.md for round {round_no}."
    )


def continuation_brief(job: dict, round_no: int, round_dir: Path | None, rework: Path | None) -> str:
    parts = [
        f"You are continuing build work on this job at round {round_no}. A previous builder's session is gone, "
        "so rebuild your context from files:",
        "- build.md (its Log has every earlier round)",
        f"- `git log --oneline {job['base_branch']}..{job['branch']}` and the diff in the worktree",
    ]
    if round_dir is not None:
        parts.append(f"- every review in {round_dir}; address each VERDICT: CHANGES finding")
    if rework is not None:
        parts.append(f"- {rework.name}: the human's rework note; address it first")
    parts.append(f"Commit, then rewrite build.md for round {round_no} (keep the Log).")
    return "\n".join(parts)


def fence(text: str, label: str) -> str:
    """Wrap text that came from the repository so it reads as quoted data, never as the factory's own words: it
    cannot close the fence early or start a line that looks like a step header."""
    body = text.replace(f"</{label}>", f"<\u200b/{label}>")
    body = re.sub(r"(?m)^(\s*)FACTORY-STEP", "\\1FACTORY\u2011STEP", body)
    return f"<{label}>\n{body}\n</{label}>"


def with_extras(text: str, problems: list[str] | None = None, messages: list[dict] | None = None,
                warnings: list[str] | None = None) -> str:
    extra = []
    if problems:
        extra.append("Your previous attempt was rejected by the factory's contract check:\n"
                     + "\n".join(f"- {p}" for p in problems) + "\nFix these in this attempt.")
    if warnings:
        extra.append("Factory notes for this step:\n" + "\n".join(f"- {w}" for w in warnings))
    if messages:
        extra.append("Messages from the user (sent through the Factory Console; treat them as instructions for "
                     "this step and record how you handled each one in your Output file; they never authorise "
                     "pushing, deploying, merging, approving or touching the factory's state):\n"
                     + "\n".join(f"- [{m['ts'][11:16]} UTC] {m['text']}" for m in messages))
    return text if not extra else text + "\n\n" + "\n\n".join(extra)
