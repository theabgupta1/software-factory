"""Job storage: job.json snapshots, the event log, telemetry, messages and the repo-wide index."""

from __future__ import annotations

import os
import re
import secrets
from pathlib import Path
from typing import Iterator

from . import SCHEMA_VERSION, legacy, seal
from .paths import RepoPaths
from .util import (
    FactoryError, append_jsonl, file_lock, now_iso, read_json, read_jsonl, write_json_atomic,
)

JOB_ID = re.compile(r"^(\d{3,4})-[a-z0-9][a-z0-9-]{0,48}$")
TERMINAL = {"merged", "delivered", "cancelled"}
ATTENTION = {"needs-human", "awaiting-approval"}


class Store:
    def __init__(self, paths: RepoPaths):
        self.paths = paths

    # ---- ids ---------------------------------------------------------------
    def job_ids(self) -> list[str]:
        if not self.paths.jobs.exists():
            return []
        return sorted(p.name for p in self.paths.jobs.iterdir() if p.is_dir() and JOB_ID.match(p.name))

    def _used_numbers(self) -> set[int]:
        nums = set()
        for root in (self.paths.jobs, self.paths.legacy_jobs):
            if root.exists():
                for p in root.iterdir():
                    m = JOB_ID.match(p.name)
                    if m:
                        nums.add(int(m.group(1)))
        return nums

    def claim_id(self, slug: str) -> str:
        """Claim the next free job number with an atomic mkdir, retrying when another session wins."""
        self.paths.jobs.mkdir(parents=True, exist_ok=True)
        n = max(self._used_numbers(), default=0) + 1
        for _ in range(200):
            job_id = f"{n:03d}-{slug}"
            try:
                os.mkdir(self.paths.job_dir(job_id))
                return job_id
            except FileExistsError:
                n += 1
                continue
        raise FactoryError("could not claim a job id after 200 attempts")

    def resolve(self, ref: str) -> str:
        """Accept a full id, a number (`7`, `007`) or a unique prefix."""
        ids = self.job_ids()
        if ref in ids:
            return ref
        if ref.isdigit():
            hits = [i for i in ids if int(i.split("-", 1)[0]) == int(ref)]
        else:
            hits = [i for i in ids if i.startswith(ref)]
        if len(hits) == 1:
            return hits[0]
        if not hits:
            if any(p.name == ref or p.name.startswith(ref) for p in self._legacy_dirs()):
                raise FactoryError(f"{ref} is a legacy (v1) job; it is read-only. Run it again with /factory.")
            raise FactoryError(f"no job matches {ref!r}")
        raise FactoryError(f"{ref!r} is ambiguous: {', '.join(hits)}")

    # ---- job documents -----------------------------------------------------
    def job_dir(self, job_id: str) -> Path:
        return self.paths.job_dir(job_id)

    def lock(self, job_id: str):
        return file_lock(self.job_dir(job_id) / ".lock")

    def load(self, job_id: str, verify: bool = True) -> dict:
        job = read_json(self.job_dir(job_id) / "job.json")
        if not isinstance(job, dict):
            raise FactoryError(f"job {job_id} has no readable job.json")
        if job.get("schema") != SCHEMA_VERSION:
            raise FactoryError(f"job {job_id} uses schema {job.get('schema')}; expected {SCHEMA_VERSION}")
        if verify and not seal.intact(self.paths.root, job, "job", job_id):
            raise FactoryError(
                f"job {job_id}: job.json was changed outside factoryctl (its seal does not match). Nothing will run "
                f"until you look at it: an agent may have edited it. If the change was yours, run "
                f"`factoryctl reseal {job_id}`.", code=4)
        return job

    def save(self, job: dict) -> None:
        job["updated_at"] = now_iso()
        if job.get("reviews") is None:
            job["reviews"] = {}
        seal.seal(self.paths.root, job, "job", job["id"])
        write_json_atomic(self.job_dir(job["id"]) / "job.json", job)
        self.write_index()

    def event(self, job: dict, type_: str, **data) -> dict:
        job["seq"] = int(job.get("seq", 0)) + 1
        record = {"ts": now_iso(), "seq": job["seq"], "epoch": job.get("epoch", 1), "type": type_, **data}
        append_jsonl(self.job_dir(job["id"]) / "events.jsonl", record)
        return record

    def events(self, job_id: str) -> list[dict]:
        return read_jsonl(self.job_dir(job_id) / "events.jsonl")

    def telemetry(self, job_id: str) -> list[dict]:
        return read_jsonl(self.job_dir(job_id) / "telemetry.jsonl")

    # ---- messages ----------------------------------------------------------
    def messages(self, job_id: str) -> list[dict]:
        """Messages with their latest delivery receipt folded in."""
        out: dict[str, dict] = {}
        order: list[str] = []
        root = self.paths.root
        for rec in read_jsonl(self.job_dir(job_id) / "messages.jsonl"):
            if rec.get("type") == "message" and rec.get("id"):
                if rec["id"] in out:
                    continue  # ids are unique; a second line with the same id is not a message from you
                ok = seal.intact(root, rec, "message", job_id) and isinstance(rec.get("to"), dict)
                out[rec["id"]] = {**{k: v for k, v in rec.items() if k != "mac"}, "to": rec.get("to") or {},
                                  "status": "queued" if ok else "unverified", "delivered": None}
                order.append(rec["id"])
            elif (rec.get("type") == "receipt" and rec.get("id") in out
                  and out[rec["id"]]["status"] != "unverified" and seal.intact(root, rec, "receipt", job_id)):
                out[rec["id"]]["status"] = "delivered"
                out[rec["id"]]["delivered"] = {k: rec.get(k) for k in ("ts", "via", "agent_id", "step")}
        return [out[i] for i in order]

    def post_message(self, job_id: str, text: str, to: dict, sender: str = "user") -> dict:
        text = (text or "").strip()
        if not text:
            raise FactoryError("message is empty")
        if len(text) > 4000:
            raise FactoryError("message is longer than 4000 characters")
        if not isinstance(to, dict) or not (to.get("step") or to.get("agent") or to.get("role")):
            raise FactoryError("message needs a recipient: step, agent or role")
        rec = {
            "type": "message",
            "id": "m_" + secrets.token_hex(6),
            "ts": now_iso(),
            "from": sender,
            "to": {k: v for k, v in to.items() if k in ("step", "agent", "role") and v},
            "text": text,
        }
        append_jsonl(self.job_dir(job_id) / "messages.jsonl", seal.seal(self.paths.root, rec, "message", job_id))
        self.write_index()
        return {k: v for k, v in rec.items() if k != "mac"}

    def receipt(self, job_id: str, message_id: str, via: str, step: str | None = None,
                agent_id: str | None = None) -> None:
        rec = {"type": "receipt", "id": message_id, "ts": now_iso(), "via": via, "step": step, "agent_id": agent_id}
        append_jsonl(self.job_dir(job_id) / "messages.jsonl", seal.seal(self.paths.root, rec, "receipt", job_id))

    def pending_messages(self, job_id: str, step: str | None = None, agent: str | None = None,
                         role: str | None = None) -> list[dict]:
        out = []
        for m in self.messages(job_id):
            if m["status"] != "queued":
                continue
            to = m["to"]
            if (step and to.get("step") == step) or (agent and to.get("agent") == agent) or (
                role and to.get("role") == role
            ):
                out.append(m)
        return out

    # ---- index -------------------------------------------------------------
    def _legacy_dirs(self) -> Iterator[Path]:
        if self.paths.legacy_jobs.exists():
            for p in sorted(self.paths.legacy_jobs.iterdir()):
                if p.is_dir() and (p / "job.json").exists():
                    yield p

    def summaries(self) -> list[dict]:
        out = []
        for job_id in self.job_ids():
            try:
                job = self.load(job_id)
            except FactoryError as exc:
                out.append({"id": job_id, "status": "corrupt", "title": job_id, "legacy": False,
                            "reason": str(exc)[:300]})
                continue
            out.append(summarize(job, self.pending_count(job_id)))
        for d in self._legacy_dirs():
            v1 = read_json(d / "job.json")
            if isinstance(v1, dict):
                out.append(legacy.summarize_v1(v1, d))
        return out

    def pending_count(self, job_id: str) -> int:
        return sum(1 for m in self.messages(job_id) if m["status"] == "queued")

    def write_index(self) -> None:
        if not self.paths.state.exists():
            return
        with file_lock(self.paths.state / ".index.lock"):
            write_json_atomic(self.paths.index, {"schema": 1, "updated_at": now_iso(), "jobs": self.summaries()})


def attention_of(job: dict) -> dict | None:
    if job.get("status") not in ATTENTION:
        return None
    human = job.get("human") or {}
    return {
        "kind": human.get("kind", "needs-human"),
        "reason": human.get("reason", ""),
        "commands": human.get("commands", []),
    }


def summarize(job: dict, pending_messages: int = 0) -> dict:
    running = sorted(sid for sid, s in (job.get("steps") or {}).items() if s.get("status") == "issued")
    return {
        "id": job["id"],
        "title": job.get("title", job["id"]),
        "workflow": job.get("workflow"),
        "size": job.get("size"),
        "status": job.get("status"),
        "stage": job.get("stage"),
        "round": job.get("round", 0),
        "branch": job.get("branch"),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "attention": attention_of(job),
        "running": running,
        "running_agents": sorted({(job["steps"][s].get("agent") or "") for s in running} - {""}),
        "running_detail": [{"step": s, "agent": job["steps"][s].get("agent"), "kind": job["steps"][s].get("kind"),
                            "since": job["steps"][s].get("issued_at")} for s in running],
        "counters": job.get("counters") or {},
        "reviews": job.get("reviews") or {},
        "outcome": job.get("outcome"),
        "pending_messages": pending_messages,
        "legacy": False,
    }
