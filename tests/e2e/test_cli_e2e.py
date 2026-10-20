"""End to end without a model: the `factoryctl` command line driven exactly as the /factory skill drives it
(one subprocess per action, JSON in and out, one session id), with scripted agents writing the artifacts. Then the
hooks run as real subprocesses inside that job, and the console's API is read for the finished job."""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from support import FACTORYCTL, PLUGIN, FakeAgents, RepoCase, sh  # noqa: E402

from factory_core.server import make_server  # noqa: E402

SESSION = "e2e-session-0001"
HOOK = PLUGIN / "hooks" / "run.py"


class CliDriver:
    """The orchestrator's loop from SKILL.md, through the CLI."""

    def __init__(self, repo: Path, agents: FakeAgents):
        self.repo, self.agents, self.log = repo, agents, []

    def ctl(self, *args: str) -> dict:
        proc = subprocess.run([sys.executable, str(FACTORYCTL), "--session", SESSION, *args], cwd=self.repo,
                              capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise AssertionError(f"factoryctl {' '.join(args)} failed: {proc.stderr or proc.stdout}")
        return json.loads(proc.stdout)

    def run(self, job: str, limit: int = 80) -> dict:
        action = self.ctl("next", job)
        for _ in range(limit):
            self.log.append(action["kind"])
            if action["kind"] in ("human", "done"):
                return action
            if action["kind"] == "spawn":
                for entry in action["entries"]:  # all in one message: they run "in parallel"
                    self.agents.handle(entry)
                nxt = None
                for entry in action["entries"]:
                    nxt = self.ctl("done", job, entry["step"], "--attempt", str(entry["attempt"]), "--via", "spawn")["next"]
                action = nxt if nxt and nxt["kind"] != "wait" else self.ctl("next", job)
            elif action["kind"] == "message":
                self.agents.handle(action["fallback"])
                action = self.ctl("done", job, action["step"], "--attempt", str(action["attempt"]), "--via", "message")["next"]
            elif action["kind"] == "inline":
                action = self.ctl("done", job, action["step"], "--attempt", str(action["attempt"]), "--via", "inline")["next"]
            else:
                raise AssertionError(f"unexpected action {action['kind']}")
        raise AssertionError("the job did not finish")


class CliEndToEnd(RepoCase):
    def hook(self, event: str, payload: dict) -> dict | None:
        proc = subprocess.run([sys.executable, str(HOOK), event], input=json.dumps(payload), capture_output=True,
                              text=True, timeout=30, env={**os.environ, "CLAUDE_PLUGIN_ROOT": str(PLUGIN)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout) if proc.stdout.strip() else None

    def test_feature_job_from_request_to_merge_through_the_cli(self):
        self.agents.verdicts["security"] = ["CHANGES", "PASS"]
        driver = CliDriver(self.repo, self.agents)
        self.assertIn("workflows", driver.ctl("init"))
        job = driver.ctl("new", "--workflow", "feature", "--size", "S", "--why", "small and clear",
                         "Rate limit the signup form")["job"]

        # A message for the builder before it runs is folded into its next prompt, once.
        driver.ctl("tell", job, "--agent", "builder", "Keep the limit configurable")
        done = driver.run(job)
        self.assertEqual(done["kind"], "done")
        self.assertEqual(done["outcome"], "merged")
        self.assertIn("message", driver.log, "round 2 resumes the builder by message")

        builder_prompts = [c for c in self.agents.calls if c["step"].startswith("build.")]
        carrying = [c["step"] for c in builder_prompts if "Keep the limit configurable" in c["prompt"]]
        self.assertEqual(carrying, ["build.r1/builder"])
        log = sh(["git", "log", "--oneline", "main"], self.repo).stdout
        self.assertIn("Build build.r1/builder", log)
        board = driver.ctl("status", "--json")
        self.assertEqual([j["status"] for j in board["jobs"] if j["id"] == job], ["merged"])
        self.assertTrue((self.paths.job_dir(job) / "final-report.md").exists())

        # The hooks, run as Claude Code runs them, inside this repository.
        start = self.hook("session-start", {"hook_event_name": "SessionStart", "cwd": str(self.repo),
                                            "session_id": SESSION, "source": "startup"})
        self.assertIsNone(start, "nothing needs the person, so SessionStart stays silent")
        push = self.hook("pre-tool-use", {"hook_event_name": "PreToolUse", "cwd": str(self.repo), "tool_name": "Bash",
                                          "agent_type": "factory:builder", "agent_id": "a1",
                                          "tool_input": {"command": "git push origin HEAD"}})
        self.assertEqual(push["hookSpecificOutput"]["permissionDecision"], "deny")
        stop = self.hook("stop", {"hook_event_name": "Stop", "cwd": str(self.repo), "session_id": SESSION,
                                  "stop_hook_active": False})
        self.assertIsNone(stop, "the job is finished, so the session may stop")

        # The console serves what the CLI did.
        httpd, _state = make_server(self.paths, port=0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        port = httpd.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", f"/api/jobs/{job}", headers={"Host": f"127.0.0.1:{port}", "X-Factory-Token": _state.token})
        detail = json.loads(conn.getresponse().read())
        self.assertEqual(detail["job"]["status"], "merged")
        self.assertEqual([r["result"] for r in detail["quality"]["matrix"]], ["changes", "pass"])
        builder_msgs = [m for m in detail["messages"] if m["to"].get("agent") == "builder"]
        self.assertEqual([m["status"] for m in builder_msgs], ["delivered"])

    def test_needs_human_then_approve_through_the_cli(self):
        self.agents.decision = "ESCALATE"
        driver = CliDriver(self.repo, self.agents)
        job = driver.ctl("new", "--workflow", "feature", "--size", "S", "Change the deploy config")["job"]
        human = driver.run(job)
        self.assertEqual(human["kind"], "human")
        self.assertIn(f"/factory approve {job}", " ".join(human.get("commands", [])))
        start = self.hook("session-start", {"hook_event_name": "SessionStart", "cwd": str(self.repo),
                                            "session_id": "another", "source": "startup"})
        self.assertIn(job.split("-")[0], json.dumps(start), "a new session is told what needs the person")
        approved = driver.ctl("approve", job)
        self.assertIn(approved.get("kind"), ("done", "spawn", "inline", "wait", "human"))
        final = driver.run(job) if approved.get("kind") != "done" else approved
        self.assertEqual(final.get("outcome"), "merged")
