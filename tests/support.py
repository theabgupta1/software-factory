"""Test support: temp git repos and scripted fake agents that drive factoryctl like the real orchestrator.

A fake agent reads the prompt header (`FACTORY-STEP …`, `Output:`, `Worktree:`) exactly as a real agent would,
writes its artifact, and commits code where its contract requires it. No model calls.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "factory"
sys.path.insert(0, str(PLUGIN / "lib"))

# Tests never touch the real ~/.claude: the projects registry and anything else keyed on the Claude home
# goes to a throwaway directory that subprocesses (factoryctl, hooks) inherit too.
os.environ["CLAUDE_CONFIG_DIR"] = tempfile.mkdtemp(prefix="factory-test-claude-home-")

from factory_core import prompts  # noqa: E402
from factory_core.engine import Engine  # noqa: E402
from factory_core.paths import RepoPaths  # noqa: E402

FACTORYCTL = PLUGIN / "bin" / "factoryctl"


def sh(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise AssertionError(f"{' '.join(args)} failed in {cwd}: {proc.stderr or proc.stdout}")
    return proc


def make_repo(base: Path, name: str = "repo", files: dict[str, str] | None = None) -> Path:
    repo = base / name
    repo.mkdir(parents=True)
    sh(["git", "init", "-q", "-b", "main"], repo)
    sh(["git", "config", "user.email", "factory@test"], repo)
    sh(["git", "config", "user.name", "Factory Test"], repo)
    sh(["git", "config", "commit.gpgsign", "false"], repo)
    for rel, text in (files or {"README.md": "# demo\n", "app.py": "def main():\n    return 1\n"}).items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    sh(["git", "add", "-A"], repo)
    sh(["git", "commit", "-qm", "init"], repo)
    return repo


def doc(header: str, *sections: str, body: str = "content") -> str:
    parts = [header, "", "# Title", ""]
    for s in sections:
        parts += [f"## {s}", body, ""]
    return "\n".join(parts)


class FakeAgents:
    """Scripted behaviour per step. Override entries in `verdicts`, `headers` or `hooks` per test."""

    def __init__(self):
        self.verdicts: dict[str, list[str]] = {}  # review key -> verdict per round (last repeats)
        self.headers: dict[str, str] = {}  # step id -> forced first line
        self.hooks: dict[str, Callable[[dict, dict], None]] = {}  # step prefix -> custom writer
        self.plan: dict | None = None
        self.decision = "APPROVE"
        self.build_file = "feature.py"
        self.calls: list[dict] = []

    def handle(self, entry: dict) -> None:
        info = prompts.parse_header(entry["prompt"])
        assert info, f"prompt has no factory header: {entry['prompt'][:200]}"
        self.calls.append({"step": info["step"], "attempt": info["attempt"], "subagent_type": entry.get("subagent_type"),
                           "prompt": entry["prompt"]})
        out = Path(info["output"])
        out.parent.mkdir(parents=True, exist_ok=True)
        wt = Path(info["worktree"])
        step = info["step"]
        for prefix, fn in self.hooks.items():
            if step.startswith(prefix):
                fn(entry, info)
                return
        try:
            self._write(entry, info, out, wt, step)
        finally:
            if out.exists():  # every real write differs (new round, new log line); mirror that
                with open(out, "a") as fh:
                    fh.write(f"\n<!-- fake call {len(self.calls)} -->\n")

    def _write(self, entry: dict, info: dict, out: Path, wt: Path, step: str) -> None:
        forced = self.headers.get(step)
        if step.startswith("build.") or step == "build":
            self._commit(wt, self.build_file, f"# {step}\n", f"Build {step}")
            out.write_text(doc(forced or "STATUS: READY", "Acceptance criteria", "Validation", "Log",
                               body=f"{step} attempt {info['attempt']}"))
        elif step.startswith("task/"):
            tid = step.split("/", 1)[1]
            task = (self.plan or {"tasks": []})["tasks"]
            owned = next((t["owns"][0] for t in task if t["id"] == tid), f"{tid.lower()}.py")
            self._commit(wt, owned, f"# {tid}\n", f"Task {tid}\n\nFactory-Task: {tid}")
            out.write_text(doc(forced or "STATUS: READY", "Changes", "Validation"))
        elif step.startswith("review."):
            rnd = int(step.split(".r", 1)[1].split("/", 1)[0])
            key = step.rsplit("/", 1)[1]
            seq = self.verdicts.get(key, ["PASS"])
            verdict = seq[min(rnd - 1, len(seq) - 1)]
            out.write_text(doc(forced or f"VERDICT: {verdict}", "Findings", body="None."))
        elif step.startswith("approve"):
            out.write_text(doc(forced or self.decision, "Why", "Acceptance criteria"))
        elif step == "spec":
            out.write_text(doc(forced or "STATUS: READY", "Requirements", "Acceptance criteria"))
        elif out.name in ("design.md", "data-design.md", "infra-design.md"):
            sections = {"design.md": ["Design"], "data-design.md": ["Model", "Contracts"],
                        "infra-design.md": ["Architecture", "Security"]}[out.name]
            out.write_text(doc(forced or "STATUS: READY", *sections))
            if self.plan is not None:
                (out.parent / "plan.json").write_text(json.dumps(self.plan))
            else:
                (out.parent / "plan.json").write_text(json.dumps({"tasks": [
                    {"id": "T1", "agent": "builder", "title": "Do it", "owns": ["t1.py"]}]}))
        elif out.name == "test-report.md":
            self._commit(wt, "test_characterize.py", "def test_x():\n    assert True\n", "Pin behaviour")
            out.write_text(doc(forced or "VERDICT: PASS", "Results"))
        else:
            from factory_core import contracts, registry

            artifact = next(a for a, spec in registry.contracts()["artifacts"].items() if a == _artifact_for(out.name))
            spec = registry.contracts()["artifacts"][artifact]
            header = {"status": "STATUS: READY", "build": "STATUS: READY", "verdict": "VERDICT: PASS",
                      "decision": "APPROVE"}[spec["header"]]
            out.write_text(doc(forced or header, *spec["sections"]))

    @staticmethod
    def _commit(wt: Path, rel: str, text: str, message: str) -> None:
        p = wt / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a") as fh:
            fh.write(text)
        sh(["git", "add", rel], wt)
        sh(["git", "commit", "-qm", message], wt)


def _artifact_for(name: str) -> str:
    return {
        "context.md": "context", "research.md": "research", "diagnosis.md": "diagnosis", "report.md": "report",
        "perf-report.md": "perf-report", "security-audit.md": "audit", "audit-code.md": "audit",
        "audit-security.md": "audit", "audit-performance.md": "audit", "audit-tests.md": "audit",
        "dq-report.md": "dq-report", "test-plan.md": "test-plan",
    }[name]


class Driver:
    """Plays the orchestrator: executes actions until the job needs a human or is done."""

    def __init__(self, engine: Engine, agents: FakeAgents):
        self.engine = engine
        self.agents = agents
        self.actions: list[dict] = []

    def run(self, job_id: str, action: dict | None = None, limit: int = 120) -> dict:
        action = action or self.engine.next(job_id)
        for _ in range(limit):
            self.actions.append(action)
            kind = action["kind"]
            if kind in ("human", "done"):
                return action
            if kind == "inline":
                action = self.engine.done(job_id, action["step"], action["attempt"], "ok")["next"]
            elif kind == "spawn":
                queue = list(action["entries"])
                for entry in queue:  # all spawned "in parallel" before any finishes
                    self.agents.handle(entry)
                seen = {(e["step"], e["attempt"]) for e in queue}
                carried = None
                while queue:
                    entry = queue.pop(0)
                    nxt = self.engine.done(job_id, entry["step"], entry["attempt"], "ok", via="spawn")["next"]
                    self.actions.append(nxt)
                    if nxt["kind"] in ("message", "inline"):
                        carried = nxt  # issued work that only this response carries
                    if nxt["kind"] == "spawn":
                        for e in nxt["entries"]:
                            if (e["step"], e["attempt"]) not in seen:
                                seen.add((e["step"], e["attempt"]))
                                self.agents.handle(e)
                                queue.append(e)
                action = carried or self.engine.next(job_id)
            elif kind == "message":
                fb = action["fallback"]
                self.agents.handle(fb)
                action = self.engine.done(job_id, action["step"], action["attempt"], "ok", via="message")["next"]
            elif kind == "wait":
                raise AssertionError(f"unexpected wait in a synchronous run: {action}")
            else:
                raise AssertionError(f"unknown action {action}")
        raise AssertionError("job did not settle")


class RepoCase(unittest.TestCase):
    """A test case with a fresh repo and a session-scoped engine."""

    files: dict[str, str] | None = None

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="factory-test-"))
        self.repo = make_repo(self.tmp, files=self.files)
        self.paths = RepoPaths(Path(os.path.realpath(self.repo)))
        self.agents = FakeAgents()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def engine(self, session: str = "S1") -> Engine:
        return Engine(self.paths, session=session)

    def driver(self, session: str = "S1") -> Driver:
        return Driver(self.engine(session), self.agents)

    def git(self, *args: str, cwd: Path | None = None) -> str:
        return sh(["git", *args], cwd or self.repo).stdout.strip()

    def write_config(self, data: dict) -> None:
        (self.paths.state).mkdir(parents=True, exist_ok=True)
        (self.paths.config).write_text(json.dumps(data))
