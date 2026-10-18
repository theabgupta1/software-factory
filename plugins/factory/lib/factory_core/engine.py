"""The workflow engine.

`next` turns a job's state into exactly one action for the LLM orchestrator; `done` records the outcome of one
step. Everything deterministic (worktrees, round folders, panel selection, merging, limits, contract checks)
happens here, so the orchestrator never has to remember the rules.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION, config, contracts, gitops, panel, plan, prompts, registry, report
from .paths import RepoPaths, ensure_state_dir
from .store import TERMINAL, Store
from .util import FactoryError, now_iso, seconds_between, slugify, write_text_atomic

PROTOCOL_VERSION = 1
LEASE_TTL_SECONDS = 15 * 60
RETRYABLE = {"invalid", "lost", "failed", "rework"}
ADVANCE = object()  # handler finished its stage
JUMP = object()  # handler moved job["stage"] itself


def session_from_env() -> str | None:
    return os.environ.get("FACTORY_SESSION_ID") or os.environ.get("CLAUDE_CODE_SESSION_ID") or None


class Engine:
    def __init__(self, paths: RepoPaths, session: str | None = None):
        self.paths = paths
        self.store = Store(paths)
        self.cfg = config.load(paths)
        self.session = session or session_from_env() or "unknown"

    # =====================================================================================
    # Job creation
    # =====================================================================================
    def new_job(self, request: str, workflow: str = "feature", size: str = "M", why: str = "",
                params: dict[str, str] | None = None, title: str | None = None) -> dict:
        request = (request or "").strip()
        if not request:
            raise FactoryError("the request is empty")
        if size not in registry.SIZES:
            raise FactoryError(f"size must be one of {', '.join(registry.SIZES)}")
        wf = registry.load_workflow(workflow)
        params = dict(params or {})
        if workflow == "quick":
            agent = params.setdefault("agent", "builder")
            if agent not in registry.IMPLEMENTERS:
                raise FactoryError(f"quick jobs need an implementing agent; {agent!r} is not one")
        ensure_state_dir(self.paths)
        job_id = self.store.claim_id(slugify(title or request))
        num = int(job_id.split("-", 1)[0])
        now = now_iso()
        job: dict[str, Any] = {
            "schema": SCHEMA_VERSION,
            "id": job_id,
            "num": num,
            "title": (title or request.splitlines()[0])[:120],
            "request": request,
            "workflow": workflow,
            "workflow_title": wf["title"],
            "size": size,
            "params": params,
            "intake": {"why": why.strip(), "at": now, "session": self.session},
            "status": "running",
            "stage": wf["stages"][0]["id"],
            "round": 0,
            "round_floor": 0,
            "repo": str(self.paths.root),
            "base_branch": self.cfg["base_branch"],
            "base_sha": None,
            "branch": f"factory/{job_id}",
            "worktree": str(self.paths.worktree_for(job_id)),
            "created_at": now,
            "updated_at": now,
            "epoch": 1,
            "seq": 0,
            "lease": {"session": self.session, "epoch": 1, "heartbeat_at": now},
            "steps": {},
            "panel": [],
            "rounds": [],
            "plan": None,
            "gates": {},
            "counters": {"agent_runs": 0, "builder_resumes": 0},
            "limits": dict(self.cfg["limits"]),
            "human": None,
            "decision": None,
            "merge": None,
            "outcome": None,
            "warnings": [],
            "history": [],
            "reviews": {},
        }
        job_dir = self.store.job_dir(job_id)
        write_text_atomic(job_dir / "workflow.json", _dump(wf))
        self.store.event(job, "job.created", workflow=workflow, size=size, why=job["intake"]["why"],
                         request=request[:500])
        self.store.event(job, "stage.started", stage=job["stage"])
        self.store.save(job)
        return job

    # =====================================================================================
    # Public operations
    # =====================================================================================
    def next(self, job_id: str) -> dict:
        job_id = self.store.resolve(job_id)
        with self.store.lock(job_id):
            job = self.store.load(job_id)
            self._lease(job)
            action = self._advance(job)
            self.store.save(job)
            return action

    def resume(self, job_id: str, force: bool = False) -> dict:
        job_id = self.store.resolve(job_id)
        with self.store.lock(job_id):
            job = self.store.load(job_id)
            holder = (job.get("lease") or {}).get("session")
            age = seconds_between((job.get("lease") or {}).get("heartbeat_at"), now_iso()) or 0
            if holder not in (None, "unknown", self.session) and age < LEASE_TTL_SECONDS and not force:
                raise FactoryError(
                    f"{job_id} was driven by another session {int(age)}s ago. If that session is gone, "
                    f"run: factoryctl resume {job_id} --force"
                )
            self._take_over(job)
            action = self._advance(job)
            self.store.save(job)
            return action

    def done(self, job_id: str, step_id: str, attempt: int, status: str = "ok", agent_id: str | None = None,
             via: str | None = None, note: str | None = None) -> dict:
        if status not in ("ok", "error", "killed"):
            raise FactoryError("status must be ok, error or killed")
        job_id = self.store.resolve(job_id)
        with self.store.lock(job_id):
            job = self.store.load(job_id)
            self._lease(job)
            step = job["steps"].get(step_id)
            if step is None:
                raise FactoryError(f"{job_id} has no step {step_id!r}")
            if attempt < step["attempt"]:
                return {"result": "stale", "problems": [], "next": self._advance_and_save(job)}
            if step["status"] != "issued":
                return {"result": "duplicate", "problems": [], "next": self._advance_and_save(job)}
            if agent_id:
                step["agent_id"] = agent_id
            if via:
                step["via"] = via
            step["done_at"] = now_iso()
            result, problems = self._finish(job, step_id, step, status, note)
            action = self._advance(job)
            self.store.save(job)
            return {"result": result, "problems": problems, "next": action}

    def approve(self, job_id: str) -> dict:
        job_id = self.store.resolve(job_id)
        with self.store.lock(job_id):
            job = self.store.load(job_id)
            self._lease(job)
            human = job.get("human") or {}
            kind = human.get("kind")
            if job["status"] == "awaiting-approval" and kind == "plan-approval":
                job["gates"][human["stage"]] = {"status": "approved", "at": now_iso(), "session": self.session}
                self.store.event(job, "gate.approved", stage=human["stage"])
            elif job["status"] == "needs-human" and kind in ("escalate", "policy", "approve-merge", "merge-busy",
                                                             "merge-conflict", "rounds-exhausted"):
                merge_stage = next((s["id"] for s in self._wf(job)["stages"] if s["kind"] == "merge"), None)
                if merge_stage is None:
                    raise FactoryError(f"{job_id}'s workflow has no merge stage")
                job["stage"] = merge_stage
                self.store.event(job, "human.approved", kind=kind)
            else:
                raise FactoryError(
                    f"{job_id} is {job['status']} ({kind or 'no pending decision'}); nothing to approve. "
                    f"Use: factoryctl rework {job_id} --note '…' or factoryctl cancel {job_id}"
                )
            job["status"] = "running"
            job["human"] = None
            action = self._advance(job)
            self.store.save(job)
            return action

    def rework(self, job_id: str, note: str) -> dict:
        note = (note or "").strip()
        if not note:
            raise FactoryError("rework needs a note saying what to change")
        job_id = self.store.resolve(job_id)
        with self.store.lock(job_id):
            job = self.store.load(job_id)
            self._lease(job)
            if job["status"] not in ("needs-human", "awaiting-approval", "delivered"):
                raise FactoryError(f"{job_id} is {job['status']}; rework applies to jobs waiting on you or delivered")
            wf = self._wf(job)
            ids = [s["id"] for s in wf["stages"]]
            job_dir = self.store.job_dir(job_id)
            k = 1 + len(list(job_dir.glob("rework-*.md")))
            rework_file = job_dir / f"rework-{k}.md"
            write_text_atomic(rework_file, note + "\n")
            human = job.get("human") or {}
            if human.get("kind") == "plan-approval":
                gate_idx = ids.index(human["stage"])
                target = next(s["id"] for s in reversed(wf["stages"][:gate_idx]) if s["kind"] in ("agent", "parallel"))
            else:
                target = wf.get("rework_to") or ("build" if "build" in ids else ids[-2])
            self._archive_from(job, target, reason=f"rework-{k}")
            if (job_dir / "decision.md").exists():
                os.replace(job_dir / "decision.md", job_dir / f"decision-{k}.md")
            stage = self._stage(job, target)
            if stage["kind"] == "build":
                job["round"] = max(job.get("round", 0), 0) + 1
                job["round_floor"] = job["round"] - 1
            job["rework"] = {"file": rework_file.name, "target": target, "at": now_iso()}
            job["stage"] = target
            job["status"] = "running"
            job["human"] = None
            job["decision"] = None
            job["outcome"] = None
            self.store.event(job, "job.reworked", target=target, file=rework_file.name, note=note[:500])
            action = self._advance(job)
            self.store.save(job)
            return action

    def cancel(self, job_id: str, reason: str = "") -> dict:
        job_id = self.store.resolve(job_id)
        with self.store.lock(job_id):
            job = self.store.load(job_id)
            if job["status"] in TERMINAL:
                raise FactoryError(f"{job_id} is already {job['status']}")
            for step in job["steps"].values():
                if step["status"] == "issued":
                    step["status"] = "lost"
            job["status"] = "cancelled"
            job["outcome"] = "cancelled"
            job["human"] = None
            self.store.event(job, "job.cancelled", reason=reason[:300])
            self.store.save(job)
            return self._done_action(job, f"cancelled; the branch {job['branch']} and worktree were kept")

    def tell(self, job_id: str, text: str, step: str | None = None, agent: str | None = None,
             role: str | None = None) -> dict:
        job_id = self.store.resolve(job_id)
        job = self.store.load(job_id)
        if step and step not in job["steps"]:
            raise FactoryError(f"{job_id} has no step {step!r}")
        if agent and agent not in registry.roster()["agents"]:
            raise FactoryError(f"unknown agent {agent!r}")
        if role and role != "orchestrator":
            raise FactoryError("role must be 'orchestrator'")
        msg = self.store.post_message(job_id, text, {"step": step, "agent": agent, "role": role})
        with self.store.lock(job_id):
            job = self.store.load(job_id)
            self.store.event(job, "message.posted", id=msg["id"], to=msg["to"])
            self.store.save(job)
        return msg

    # =====================================================================================
    # Lease
    # =====================================================================================
    def _lease(self, job: dict) -> None:
        lease = job.get("lease") or {}
        holder = lease.get("session")
        if holder and holder != self.session and "unknown" not in (holder, self.session):
            age = seconds_between(lease.get("heartbeat_at"), now_iso()) or 0
            if age < LEASE_TTL_SECONDS:
                raise FactoryError(
                    f"{job['id']} is being driven by another Claude session (active {int(age)}s ago). "
                    f"Continue there, or take it over with: factoryctl resume {job['id']} --force"
                )
            self._take_over(job)
            return
        job["lease"] = {"session": self.session, "epoch": job.get("epoch", 1), "heartbeat_at": now_iso()}

    def _take_over(self, job: dict) -> None:
        job["epoch"] = int(job.get("epoch", 1)) + 1
        job["lease"] = {"session": self.session, "epoch": job["epoch"], "heartbeat_at": now_iso()}
        self.store.event(job, "lease.acquired", session=self.session)
        for step_id, step in job["steps"].items():
            if step["status"] != "issued" or step.get("epoch", 1) >= job["epoch"]:
                continue
            if step.get("kind") != "inline" and step.get("artifact"):
                check = contracts.check(self._out(job, step), step["artifact"])
                if check.ok and self._fresh(job, step):
                    step["done_at"] = now_iso()
                    self._finish(job, step_id, step, "ok", "recovered after the driving session ended")
                    self.store.event(job, "step.recovered", step=step_id)
                    continue
            step["status"] = "lost"
            step["lost"] = int(step.get("lost", 0)) + 1
            self.store.event(job, "step.lost", step=step_id)

    # =====================================================================================
    # The state machine
    # =====================================================================================
    def _advance_and_save(self, job: dict) -> dict:
        action = self._advance(job)
        self.store.save(job)
        return action

    def _advance(self, job: dict) -> dict:
        if job["status"] in TERMINAL:
            return self._done_action(job, f"already {job['status']}")
        if job["status"] in ("needs-human", "awaiting-approval"):
            return self._human_action(job)
        wf = self._wf(job)
        ids = [s["id"] for s in wf["stages"]]
        for _ in range(200):  # guards against a malformed workflow looping forever
            stage = self._stage(job, job["stage"])
            if not self._cond(job, stage.get("when")):
                job["steps"].setdefault(f"skip:{stage['id']}", {"status": "skipped", "stage": stage["id"],
                                                                "kind": "skip", "attempt": 0})
                self.store.event(job, "stage.skipped", stage=stage["id"])
                job["stage"] = ids[ids.index(stage["id"]) + 1]
                continue
            handler = getattr(self, "_h_" + stage["kind"])
            result = handler(job, stage)
            if result is ADVANCE:
                if (job.get("rework") or {}).get("target") == stage["id"]:
                    job.pop("rework", None)
                self.store.event(job, "stage.completed", stage=stage["id"])
                job["stage"] = ids[ids.index(stage["id"]) + 1]
                self.store.event(job, "stage.started", stage=job["stage"])
                continue
            if result is JUMP:
                continue
            return self._finalise_action(job, result)
        raise FactoryError(f"{job['id']}: workflow did not settle; this is a bug in the workflow definition")

    # ---- stage handlers ------------------------------------------------------------------
    def _h_setup(self, job: dict, stage: dict):
        repo = self.paths.root
        if not job.get("base_sha"):
            job["base_sha"] = gitops.create_worktree(repo, Path(job["worktree"]), job["branch"], job["base_branch"])
            self.store.event(job, "worktree.created", worktree=job["worktree"], base_sha=job["base_sha"])
        elif not Path(job["worktree"]).exists():
            gitops.create_worktree(repo, Path(job["worktree"]), job["branch"], job["base_branch"])
            self.store.event(job, "worktree.recreated", worktree=job["worktree"])
        if not stage.get("install"):
            return ADVANCE
        sid = "setup/deps"
        step = job["steps"].get(sid)
        if step is None or step["status"] in RETRYABLE:
            step = self._record_issue(job, sid, {"stage": stage["id"], "kind": "inline", "agent": None,
                                                 "via": "inline"})
            if step is None:
                return self._limit_action(job)
        if step["status"] == "issued":
            return {
                "kind": "inline",
                "step": sid,
                "attempt": step["attempt"],
                "worktree": job["worktree"],
                "instruction": (
                    f"Prepare the fresh worktree {job['worktree']}: run the setup that AGENTS.md (or CLAUDE.md) "
                    "gives for a fresh checkout, such as installing dependencies, from inside the worktree. If the "
                    "repo documents no setup, there is nothing to run. Then record the result with "
                    f"`factoryctl done {job['id']} {sid} --attempt {step['attempt']} --status ok` "
                    "(or `--status error --note \"<what failed>\"`)."
                ),
                "say": f"{job['id']}: preparing the worktree",
            }
        return ADVANCE

    def _h_agent(self, job: dict, stage: dict):
        sid = stage["id"]
        step = job["steps"].get(sid)
        if step is None or step["status"] in RETRYABLE:
            entry = self._issue_agent(job, sid, stage, stage)
            return entry if isinstance(entry, dict) and entry.get("kind") == "human" else self._spawn([entry])
        if step["status"] == "issued":
            return self._wait([sid])
        return self._after_document(job, sid, step)

    def _h_approve(self, job: dict, stage: dict):
        result = self._h_agent(job, stage)
        if result is not ADVANCE:
            return result
        step = job["steps"][stage["id"]]
        decision = step.get("value")
        job["decision"] = decision
        text = self._read(job, step)
        if decision == "ESCALATE":
            why = contracts.section_text(text, "For the human") or contracts.section_text(text, "Why")
            return self._to_human(job, "escalate", why or "The approver escalated this job.",
                                  [f"/factory approve {job['id']}", f"/factory rework {job['id']} <note>"],
                                  ["decision.md"])
        wf = self._wf(job)
        if wf.get("escalate_always"):
            return self._to_human(job, "policy",
                                  f"{wf['title']} jobs always need a human: review the plan output in build.md "
                                  "and apply it yourself after merging. The factory never applies changes.",
                                  [f"/factory approve {job['id']}", f"/factory rework {job['id']} <note>"],
                                  ["decision.md", "build.md"])
        globs = self.cfg.get("always_escalate") or []
        if globs:
            changed = gitops.changed_paths(self.paths.root, job["base_branch"], job["branch"])
            hits = [p for p in changed if any(fnmatch.fnmatch(p, g) for g in globs)]
            if hits:
                return self._to_human(job, "policy",
                                      "Approved, but these files match always_escalate in .factory/config.json: "
                                      + ", ".join(hits[:8]),
                                      [f"/factory approve {job['id']}", f"/factory rework {job['id']} <note>"],
                                      ["decision.md"])
        if not self.cfg.get("auto_merge", True):
            return self._to_human(job, "approve-merge", "Approved. auto_merge is off, so merging waits for you.",
                                  [f"/factory approve {job['id']}"], ["decision.md"])
        return ADVANCE

    def _h_parallel(self, job: dict, stage: dict):
        members = [m for m in stage["members"] if self._cond(job, m.get("when"))]
        entries, running = [], []
        for m in members:
            sid = f"{stage['id']}/{m['key']}"
            step = job["steps"].get(sid)
            if step is None or step["status"] in RETRYABLE:
                entry = self._issue_agent(job, sid, stage, m)
                if entry.get("kind") == "human":
                    return entry
                entries.append(entry)
            elif step["status"] == "issued":
                running.append(sid)
        if entries:
            return self._spawn(entries, running)
        if running:
            return self._wait(running)
        for m in members:
            sid = f"{stage['id']}/{m['key']}"
            result = self._after_document(job, sid, job["steps"][sid])
            if result is not ADVANCE:
                return result
        return ADVANCE

    def _h_build(self, job: dict, stage: dict):
        if job.get("round", 0) == 0:
            job["round"] = 1
            job["round_floor"] = 0
        n = job["round"]
        job_dir = self.store.job_dir(job["id"])
        planned = job.get("plan") or {}
        mode = stage.get("mode") or "implement"
        if planned.get("order") and n == planned.get("round", 1):
            for tid in planned["order"]:
                sid = f"task/{tid}"
                step = job["steps"].get(sid)
                if step is None or step["status"] in RETRYABLE:
                    task = planned["tasks"][tid]
                    entry = self._issue_task(job, sid, stage, task)
                    return entry if entry.get("kind") == "human" else self._spawn([entry])
                if step["status"] == "issued":
                    return self._wait([sid])
                if step.get("value") == "BLOCKED":
                    return self._blocked(job, sid, step)
            mode = "integrate"
        sid = f"build.r{n}/builder"
        step = job["steps"].get(sid)
        if step is None or step["status"] in RETRYABLE:
            agent = self._build_agent(job, stage)
            rework = job.get("rework") if (job.get("rework") or {}).get("target") == stage["id"] else None
            prev = self._previous_builder(job, n)
            round_dir = job_dir / f"round-{n - 1}" if n > 1 else None
            can_message = (
                prev is not None and rework is None and prev.get("session") == self.session
                and prev.get("epoch") == job["epoch"] and (step is None or step["status"] != "lost")
            )
            if n > 1 + job.get("round_floor", 0) and can_message:
                fallback = self._issue_agent(job, sid, stage, {**stage, "agent": agent, "output": "build.md",
                                                             "artifact": "build"}, kind="build", mode=mode,
                                             brief=prompts.continuation_brief(job, n, round_dir, None),
                                             via="message", deliver_messages=False)
                if fallback.get("kind") == "human":
                    return fallback
                job["counters"]["builder_resumes"] += 1
                pending = self.store.pending_messages(job["id"], step=sid, agent=agent)
                message = prompts.with_extras(prompts.address_reviews(job, n, round_dir), messages=pending)
                fallback["prompt"] = prompts.with_extras(fallback["prompt"], messages=pending)
                for m in pending:
                    self.store.receipt(job["id"], m["id"], via="prompt", step=sid)
                return {
                    "kind": "message",
                    "step": sid,
                    "attempt": fallback["attempt"],
                    "to": f"builder-{job['id']}",
                    "agent_id": prev.get("agent_id"),
                    "message": message,
                    "fallback": fallback,
                    "say": f"{job['id']}: round {n - 1} asked for changes → resuming the builder",
                }
            brief = None
            if n > 1 or rework:
                brief = prompts.continuation_brief(job, n, round_dir if n > 1 else None,
                                                   job_dir / rework["file"] if rework else None)
                if n > 1:
                    job["counters"]["builder_resumes"] += 1
            entry = self._issue_agent(job, sid, stage, {**stage, "agent": agent, "output": "build.md",
                                                        "artifact": "build"}, kind="build", mode=mode, brief=brief,
                                      via="respawn" if n > 1 else "spawn")
            if entry.get("kind") == "human":
                return entry
            if rework:
                job.pop("rework", None)
            return self._spawn([entry])
        if step["status"] == "issued":
            return self._wait([sid])
        return self._after_document(job, sid, step)

    def _h_panel(self, job: dict, stage: dict):
        n = job.get("round") or 1
        job["round"] = n
        rnd = self._round(job, n)
        job_dir = self.store.job_dir(job["id"])
        if rnd is None:
            changed = gitops.changed_paths(self.paths.root, job["base_branch"], job["branch"])
            build_text = (job_dir / "build.md").read_text(encoding="utf-8", errors="replace") \
                if (job_dir / "build.md").exists() else ""
            members = panel.select(stage, job["workflow"], job.get("panel") or [], changed, build_text, self.cfg, n)
            job["panel"] = members
            (job_dir / f"round-{n}").mkdir(parents=True, exist_ok=True)
            rnd = {"n": n, "head": gitops.head(Path(job["worktree"])), "members": [m["key"] for m in members],
                   "verdicts": {m["key"]: "pending" for m in members}, "result": None, "started_at": now_iso()}
            job["rounds"].append(rnd)
            job["reviews"] = dict(rnd["verdicts"])
            self.store.event(job, "panel.selected", round=n,
                             members=[{"key": m["key"], "agent": m["agent"], "why": m["why"]} for m in members])
            self.store.event(job, "round.started", round=n)
        members = {m["key"]: m for m in job["panel"]}
        entries, running = [], []
        for key in rnd["members"]:
            sid = f"review.r{n}/{key}"
            step = job["steps"].get(sid)
            if step is None or step["status"] in RETRYABLE:
                m = members[key]
                entry = self._issue_agent(job, sid, stage, {"agent": m["agent"], "output": f"round-{n}/review-{key}.md",
                                                            "artifact": "review", "key": key, "why": m["why"]},
                                          kind="review", panel_members=[members[k] for k in rnd["members"]])
                if entry.get("kind") == "human":
                    return entry
                entries.append(entry)
            elif step["status"] == "issued":
                running.append(sid)
        if entries:
            return self._spawn(entries, running, say=f"{job['id']}: review round {n} → "
                                                    + ", ".join(rnd["members"]))
        if running:
            return self._wait(running)
        worktree = Path(job["worktree"])
        if gitops.head(worktree) != rnd["head"] or not gitops.is_clean(worktree):
            return self._to_human(job, "integrity",
                                  f"The worktree changed during review round {n} (reviewers must not edit code). "
                                  "Inspect the worktree, then rework or cancel the job.",
                                  [f"/factory rework {job['id']} <note>", f"/factory cancel {job['id']}"], [])
        verdicts = {k: job["steps"][f"review.r{n}/{k}"].get("value") for k in rnd["members"]}
        rnd["verdicts"] = verdicts
        job["reviews"] = dict(verdicts)
        changes = [k for k, v in verdicts.items() if v == "CHANGES"]
        rnd["result"] = "changes" if changes else "pass"
        rnd["completed_at"] = now_iso()
        self.store.event(job, "round.completed", round=n, verdicts=verdicts)
        if not changes:
            return ADVANCE
        used = n - job.get("round_floor", 0)
        if used < job["limits"]["max_review_rounds"]:
            job["round"] = n + 1
            job["stage"] = stage["loop_to"]
            self.store.event(job, "stage.looped", to=stage["loop_to"], round=n + 1, changes=changes)
            return JUMP
        decision = job_dir / "decision.md"
        write_text_atomic(decision, "ESCALATE\n\n## Why\nReview rounds exhausted. Outstanding CHANGES in "
                                    f"round-{n}/ from: {', '.join(changes)}.\n")
        job["decision"] = "ESCALATE"
        return self._to_human(job, "rounds-exhausted",
                              f"Review rounds exhausted ({used} of {job['limits']['max_review_rounds']}). "
                              f"Still asking for changes: {', '.join(changes)}.",
                              [f"/factory rework {job['id']} <note>", f"/factory approve {job['id']}"],
                              [f"round-{n}/review-{k}.md" for k in changes])

    def _h_gate(self, job: dict, stage: dict):
        gate = job["gates"].get(stage["id"]) or {}
        if gate.get("status") == "approved":
            return ADVANCE
        job["status"] = "awaiting-approval"
        files = [f for f in ("design.md", "data-design.md", "infra-design.md", "plan.json")
                 if (self.store.job_dir(job["id"]) / f).exists()]
        job["human"] = {
            "kind": "plan-approval",
            "stage": stage["id"],
            "reason": stage.get("reason", "Waiting for your approval."),
            "commands": [f"/factory build {job['id']}", f"/factory rework {job['id']} <note>"],
            "files": files,
            "at": now_iso(),
        }
        self.store.event(job, "gate.waiting", stage=stage["id"])
        return self._human_action(job)

    def _h_merge(self, job: dict, stage: dict):
        result = gitops.merge(self.paths.root, job["base_branch"], job["branch"], Path(job["worktree"]), job["title"])
        self.store.event(job, "merge." + ("done" if result.status == "merged" else "failed"),
                         status=result.status, message=result.message, sha=result.sha)
        if result.status == "merged":
            job["status"] = "merged"
            job["outcome"] = "merged"
            job["merge"] = {"sha": result.sha, "at": now_iso(), "note": result.cleanup}
            self._write_report(job)
            msg = f"merged into {job['base_branch']} ({(result.sha or '')[:8]})"
            if result.cleanup:
                msg += f"; {result.cleanup}"
            return self._done_action(job, msg)
        kind = "merge-busy" if result.status == "busy" else "merge-conflict"
        hint = ("Commit or stash your changes and switch the main checkout to "
                f"{job['base_branch']}, then approve again." if result.status == "busy"
                else f"Resolve it on {job['branch']} in {job['worktree']} (or rework), then approve again.")
        return self._to_human(job, kind, f"Approved but not merged: {result.message}. {hint}",
                              [f"/factory approve {job['id']}", f"/factory rework {job['id']} <note>"], [])

    def _h_deliver(self, job: dict, stage: dict):
        note = gitops.remove_unused_worktree(self.paths.root, Path(job["worktree"]), job["branch"], job["base_branch"])
        job["status"] = "delivered"
        job["outcome"] = "delivered"
        self.store.event(job, "job.delivered", note=note)
        self._write_report(job)
        return self._done_action(job, "delivered" + (f"; {note}" if note else ""))

    # ---- step issue & completion --------------------------------------------------------
    def _record_issue(self, job: dict, sid: str, fields: dict) -> dict | None:
        limits = job["limits"]
        prev = job["steps"].get(sid) or {}
        failures = int(prev.get("failures", 0))
        if failures >= limits["max_attempts"]:
            return None
        if fields.get("kind") != "inline" and job["counters"]["agent_runs"] >= limits["max_agent_runs"]:
            return None
        if fields.get("kind") != "inline":
            job["counters"]["agent_runs"] += 1
        step = {
            **fields,
            "attempt": int(prev.get("attempt", 0)) + 1,
            "failures": failures,
            "lost": int(prev.get("lost", 0)),
            "status": "issued",
            "session": self.session,
            "epoch": job["epoch"],
            "issued_at": now_iso(),
            "done_at": None,
            "problems": prev.get("problems") or [],
        }
        job["steps"][sid] = step
        self.store.event(job, "step.issued", step=sid, attempt=step["attempt"], agent=step.get("agent"),
                         via=step.get("via"))
        return step

    def _issue_agent(self, job: dict, sid: str, stage: dict, member: dict, *, kind: str | None = None,
                     mode: str | None = None, brief: str | None = None, via: str = "spawn",
                     panel_members: list[dict] | None = None, task: dict | None = None,
                     deliver_messages: bool = True) -> dict:
        agent = member["agent"]
        meta = registry.agent(agent)
        mode = mode or member.get("mode") or stage.get("mode")
        kind = kind or ("approve" if stage["kind"] == "approve" else
                        "spec" if agent == "product-analyst" else "agent")
        prev = job["steps"].get(sid) or {}
        step = self._record_issue(job, sid, {
            "stage": stage["id"], "kind": kind, "role": member.get("key") or stage["id"], "agent": agent,
            "name": f"builder-{job['id']}" if kind == "build" else None, "output": member["output"],
            "artifact": member["artifact"], "mode": mode, "round": job.get("round") or None,
            "why": member.get("why"), "via": via, "commits": bool(member.get("commits")),
            "plan_json": bool(member.get("plan_json")),
        })
        if step is None:
            return self._limit_action(job, sid)
        job_dir = self.store.job_dir(job["id"])
        output = job_dir / member["output"]
        step["prior"] = self._fingerprint(output)
        port = None
        if "port_offset" in meta:
            port = self.cfg["ports"]["base"] + 10 * (job["num"] % 900) + int(meta["port_offset"])
        inputs = [p.name for p in sorted(job_dir.glob("*.md")) if p.name != member["output"]]
        if (job_dir / "plan.json").exists():
            inputs.append("plan.json")
        text = prompts.header(job, sid, step["attempt"], job_dir, output, round_no=job.get("round") or None,
                              mode=mode, port=port, inputs=inputs[:12])
        body = prompts.instruction("task" if task else kind, job=job, stage=stage, mode=mode,
                                   round_no=job.get("round"), panel=panel_members, member=member, task=task)
        if brief:
            body = brief + "\n\n" + body
        pending = self.store.pending_messages(job["id"], step=sid, agent=agent) if deliver_messages else []
        rework = job.get("rework")
        warnings = []
        if rework and rework.get("target") == stage["id"]:
            warnings.append(f"The human asked for rework in {rework['file']}; read it first and address it.")
        prompt = prompts.with_extras(text + "\n\n" + body, problems=prev.get("problems") if prev else None,
                                     messages=pending, warnings=warnings)
        for m in pending:
            self.store.receipt(job["id"], m["id"], via="prompt", step=sid)
        model = (self.cfg.get("models") or {}).get(agent)
        step["model"] = model or meta.get("model")
        entry = {
            "step": sid,
            "attempt": step["attempt"],
            "subagent_type": f"{self.cfg['agent_prefix']}{agent}",
            "name": step.get("name"),
            "model": model,
            "description": f"{job['id']} {stage['id']}" + (f" r{job['round']}" if kind in ("review", "build") else "")
                           + (f" {member.get('key')}" if member.get("key") else ""),
            "prompt": prompt,
            "output": str(output),
            "run_in_background": True,
        }
        self._remember(job, sid, entry)
        return entry

    def _entry_file(self, job: dict, sid: str) -> Path:
        return self.store.job_dir(job["id"]) / "entries" / (sid.replace("/", "__") + ".json")

    def _remember(self, job: dict, sid: str, entry: dict) -> None:
        write_text_atomic(self._entry_file(job, sid), _dump(entry))

    def redeliver(self, job_id: str) -> dict:
        """Re-send the spawn entries of steps that are issued but whose agents the orchestrator lost track of.

        Attempts are not incremented; use this after context compaction or a lost tool result.
        """
        job_id = self.store.resolve(job_id)
        with self.store.lock(job_id):
            job = self.store.load(job_id)
            self._lease(job)
            entries = []
            for sid, step in job["steps"].items():
                if step["status"] != "issued" or step.get("kind") == "inline":
                    continue
                path = self._entry_file(job, sid)
                if path.exists():
                    import json

                    entries.append(json.loads(path.read_text(encoding="utf-8")))
            self.store.event(job, "steps.redelivered", steps=[e["step"] for e in entries])
            self.store.save(job)
            if not entries:
                return self._finalise_action(job, self._advance(job))
            return self._finalise_action(job, self._spawn(entries, say=f"{job_id}: re-sending "
                                                                     + ", ".join(e["step"] for e in entries)))

    def _issue_task(self, job: dict, sid: str, stage: dict, task: dict) -> dict:
        member = {"agent": task["agent"], "output": f"tasks/{task['id']}.md", "artifact": "task",
                  "key": task["id"], "why": f"plan task {task['id']}"}
        (self.store.job_dir(job["id"]) / "tasks").mkdir(exist_ok=True)
        head_before = (job["steps"].get(sid) or {}).get("head_before") or gitops.head(Path(job["worktree"]))
        entry = self._issue_agent(job, sid, stage, member, kind="task", mode="task", task=task)
        if entry.get("kind") == "human":
            return entry
        job["steps"][sid]["task"] = task["id"]
        job["steps"][sid]["head_before"] = head_before
        return entry

    def _finish(self, job: dict, sid: str, step: dict, status: str, note: str | None) -> tuple[str, list[str]]:
        problems: list[str] = []
        if step.get("kind") == "inline":
            if status == "ok":
                step["status"] = "done"
                self.store.event(job, "step.done", step=sid, note=note)
                return "valid", []
            step["status"] = "failed"
            step["failures"] = int(step.get("failures", 0)) + 1
            step["problems"] = [note or "setup failed"]
            self.store.event(job, "step.failed", step=sid, note=note)
            self._to_human(job, "blocked", f"Worktree setup failed: {note or 'no details given'}",
                           [f"/factory resume {job['id']}", f"/factory cancel {job['id']}"], [])
            return "invalid", step["problems"]
        if status != "ok":
            problems.append(f"the agent ended with status {status}" + (f": {note}" if note else ""))
        check = contracts.check(self._out(job, step), step["artifact"])
        problems += check.problems
        if check.exists and not self._fresh(job, step):
            problems.append(f"{step['output']} was not updated during this step; rewrite it for this round")
        step["header"] = check.header
        step["value"] = check.value
        step["tone"] = check.tone
        if not problems and check.value != "BLOCKED":
            problems += self._postconditions(job, sid, step)
        if problems:
            step["status"] = "invalid"
            step["failures"] = int(step.get("failures", 0)) + 1
            step["problems"] = problems
            self.store.event(job, "step.invalid", step=sid, problems=problems)
            return "invalid", problems
        step["status"] = "done"
        step["problems"] = []
        if step.get("kind") == "review":
            for rnd in job["rounds"]:
                if rnd["n"] == step.get("round"):
                    rnd["verdicts"][step["role"]] = check.value
                    job["reviews"] = dict(rnd["verdicts"])
        self.store.event(job, "step.done", step=sid, value=check.value, agent=step.get("agent"), note=note)
        return "valid", []

    def _postconditions(self, job: dict, sid: str, step: dict) -> list[str]:
        problems: list[str] = []
        worktree = Path(job["worktree"])
        kind = step.get("kind")
        if kind in ("build", "task") or step.get("commits"):
            if not gitops.is_clean(worktree):
                problems.append("the worktree has uncommitted changes to tracked files; commit or revert them")
        if kind == "task":
            tid = step["task"]
            commits = gitops.commits_with_trailer(worktree, step.get("head_before") or job["base_sha"],
                                                  f"Factory-Task: {tid}")
            if not commits:
                problems.append(f"no commit carries the trailer 'Factory-Task: {tid}'")
            else:
                changed = gitops.paths_between(worktree, step.get("head_before") or job["base_sha"], "HEAD")
                owns = (job.get("plan") or {}).get("tasks", {}).get(tid, {}).get("owns") or []
                outside = plan.outside_ownership(changed, owns)
                if outside:
                    warning = f"{tid} changed files outside its ownership: {', '.join(outside[:6])}"
                    step.setdefault("warnings", []).append(warning)
                    job["warnings"].append(warning)
                    self.store.event(job, "ownership.violation", step=sid, paths=outside[:20])
        if step.get("plan_json"):
            data = plan.load(self.store.job_dir(job["id"]))
            if data is None:
                problems.append("plan.json was not written next to the design")
            else:
                plan_problems, order = plan.validate(data)
                if plan_problems:
                    problems += [f"plan.json: {p}" for p in plan_problems]
                else:
                    job["plan"] = {"order": order, "tasks": {t["id"]: t for t in data["tasks"]},
                                   "round": max(1, job.get("round") or 1), "validated_at": now_iso()}
                    self.store.event(job, "plan.validated", order=order)
        return problems

    def _after_document(self, job: dict, sid: str, step: dict):
        if step["status"] in ("invalid", "failed") and int(step.get("failures", 0)) >= job["limits"]["max_attempts"]:
            return self._to_human(job, "blocked",
                                  f"{sid} failed its contract {step['failures']} times: " + "; ".join(step["problems"][:3]),
                                  [f"/factory rework {job['id']} <note>", f"/factory cancel {job['id']}"],
                                  [step.get("output")])
        if step.get("value") == "BLOCKED":
            return self._blocked(job, sid, step)
        if step.get("value") == "PARTIAL":
            warning = f"{step.get('output')} is PARTIAL"
            if warning not in job["warnings"]:
                job["warnings"].append(warning)
        return ADVANCE

    def _blocked(self, job: dict, sid: str, step: dict):
        text = self._read(job, step)
        reason = contracts.section_text(text, "Blocked") or f"{step.get('output')} says BLOCKED"
        return self._to_human(job, "blocked", reason[:1200],
                              [f"/factory rework {job['id']} <note>", f"/factory cancel {job['id']}"],
                              [step.get("output")])

    # ---- actions ---------------------------------------------------------------------------
    def _spawn(self, entries: list[dict], running: list[str] | None = None, say: str | None = None) -> dict:
        return {"kind": "spawn", "entries": entries, "running": running or [],
                "say": say or " · ".join(e["description"] for e in entries)}

    def _wait(self, running: list[str]) -> dict:
        return {"kind": "wait", "running": running,
                "say": "waiting for " + ", ".join(running),
                "hint": "End your turn; background agents notify you when they finish. If one of these agents is "
                        "not actually running, run: factoryctl next <job> --redeliver"}

    def _to_human(self, job: dict, kind: str, reason: str, commands: list[str], files: list) -> dict:
        job["status"] = "needs-human"
        job["human"] = {"kind": kind, "reason": reason.strip(), "commands": commands,
                        "files": [f for f in files if f], "at": now_iso()}
        self.store.event(job, "human.needed", kind=kind, reason=reason.strip()[:500])
        if kind in ("escalate", "policy", "limit", "blocked", "integrity", "rounds-exhausted"):
            self._write_report(job)
        return self._human_action(job)

    def _human_action(self, job: dict) -> dict:
        human = job.get("human") or {}
        return {**{k: v for k, v in human.items() if k != "kind"}, "kind": "human", "need": human.get("kind"),
                "say": f"{job['id']} needs you ({human.get('kind', 'decision')}): {human.get('reason', '')[:200]}"}

    def _limit_action(self, job: dict, sid: str | None = None) -> dict:
        runs = job["counters"]["agent_runs"]
        if runs >= job["limits"]["max_agent_runs"]:
            reason = f"Agent run limit reached ({runs}/{job['limits']['max_agent_runs']})."
        else:
            reason = f"{sid} exhausted its {job['limits']['max_attempts']} attempts."
        return self._to_human(job, "limit", reason,
                              [f"/factory rework {job['id']} <note>", f"/factory cancel {job['id']}"], [])

    def _done_action(self, job: dict, message: str) -> dict:
        report_path = self.store.job_dir(job["id"]) / "final-report.md"
        return {"kind": "done", "outcome": job.get("outcome") or job["status"],
                "report": str(report_path) if report_path.exists() else None,
                "say": f"{job['id']}: {message}"}

    def _finalise_action(self, job: dict, action: dict) -> dict:
        action = {"v": PROTOCOL_VERSION, "job": job["id"], "epoch": job["epoch"], **action}
        notes = self.store.pending_messages(job["id"], role="orchestrator")
        if notes:
            action["notes"] = [{"id": m["id"], "ts": m["ts"], "text": m["text"]} for m in notes]
            for m in notes:
                self.store.receipt(job["id"], m["id"], via="next")
        return action

    # ---- helpers ---------------------------------------------------------------------------
    def _wf(self, job: dict) -> dict:
        snap = self.store.job_dir(job["id"]) / "workflow.json"
        try:
            import json

            return json.loads(snap.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return registry.load_workflow(job["workflow"])

    def _stage(self, job: dict, stage_id: str) -> dict:
        for s in self._wf(job)["stages"]:
            if s["id"] == stage_id:
                return s
        raise FactoryError(f"{job['id']}: unknown stage {stage_id!r}")

    def _round(self, job: dict, n: int) -> dict | None:
        return next((r for r in job["rounds"] if r["n"] == n), None)

    def _out(self, job: dict, step: dict) -> Path:
        return self.store.job_dir(job["id"]) / step["output"]

    def _fingerprint(self, path: Path) -> str | None:
        import hashlib

        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except FileNotFoundError:
            return None

    def _fresh(self, job: dict, step: dict) -> bool:
        """The output differs from what was on disk when the step was issued."""
        current = self._fingerprint(self._out(job, step))
        return current is not None and current != step.get("prior")

    def _read(self, job: dict, step: dict) -> str:
        try:
            return self._out(job, step).read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return ""

    def _build_agent(self, job: dict, stage: dict) -> str:
        agent = stage.get("agent", "builder")
        if agent == "$param.agent":
            agent = (job.get("params") or {}).get("agent") or "builder"
        return agent

    def _previous_builder(self, job: dict, n: int) -> dict | None:
        for k in range(n - 1, 0, -1):
            step = job["steps"].get(f"build.r{k}/builder")
            if step and step["status"] == "done":
                return step
        return None

    def _archive_from(self, job: dict, stage_id: str, reason: str) -> None:
        ids = [s["id"] for s in self._wf(job)["stages"]]
        later = set(ids[ids.index(stage_id):])
        for sid in list(job["steps"]):
            step = job["steps"][sid]
            stage = step.get("stage")
            round_keyed = sid.startswith(("build.r", "review.r", "task/"))
            if stage in later and not round_keyed:
                job["history"].append({**step, "id": sid, "archived": reason, "archived_at": now_iso()})
                del job["steps"][sid]
        for gate_id in list(job["gates"]):
            if gate_id in later:
                del job["gates"][gate_id]

    def _cond(self, job: dict, cond: dict | None) -> bool:
        if not cond:
            return True
        (key, value), = cond.items()
        if key == "size_in":
            return job.get("size") in value
        if key == "param_true":
            return str((job.get("params") or {}).get(value, "")).lower() in ("1", "true", "yes", "y")
        if key == "policy":
            listed = self.cfg.get(value) or []
            return job["workflow"] in listed or "all" in listed
        if key == "has_plan":
            return bool((job.get("plan") or {}).get("order")) == bool(value)
        if key == "any":
            return any(self._cond(job, c) for c in value)
        if key == "all":
            return all(self._cond(job, c) for c in value)
        if key == "not":
            return not self._cond(job, value)
        raise FactoryError(f"unknown condition {key!r}")

    def _write_report(self, job: dict) -> None:
        try:
            report.write(self.store, job)
        except Exception as exc:  # a report must never break the workflow
            job["warnings"].append(f"final report could not be written: {exc}")


def _dump(data: Any) -> str:
    import json

    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
