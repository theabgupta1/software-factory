"""Every factoryctl subcommand through the real command line: arguments, JSON out, exit codes, files written."""

from __future__ import annotations

import http.client
import json
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from support import FACTORYCTL, Driver, RepoCase  # noqa: E402

SESSION = "e2e-cmds-0001"


class CliCommands(RepoCase):
    def ctl(self, *args: str, expect: int = 0, json_out: bool = True):
        proc = subprocess.run([sys.executable, str(FACTORYCTL), "--session", SESSION, *args], cwd=self.repo,
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, expect, proc.stderr or proc.stdout)
        return json.loads(proc.stdout) if json_out else proc.stdout

    def merged_job(self) -> str:
        eng = self.engine(SESSION)
        job = eng.new_job("Add a health endpoint", size="S")
        Driver(eng, self.agents).run(job["id"])
        return job["id"]

    def test_cmd_init(self):
        out = self.ctl("init")
        self.assertEqual(out["repo"], str(self.repo))
        self.assertTrue((self.repo / ".factory" / "index.json").exists())
        exclude = (self.repo / ".git" / "info" / "exclude").read_text()
        self.assertIn("/.factory/", exclude, "state never shows up in git status")

    def test_cmd_new(self):
        out = self.ctl("new", "--workflow", "fix", "--size", "S", "--why", "clear bug", "Checkout", "returns", "500")
        self.assertEqual((out["workflow"], out["size"]), ("fix", "S"))
        self.assertTrue(Path(out["worktree"]).is_dir())
        self.assertEqual(self.ctl("new", expect=1, json_out=False), "")

    def test_cmd_next(self):
        job = self.ctl("new", "Add a footer")["job"]
        first = self.ctl("next", job)
        self.assertIn(first["kind"], ("inline", "spawn"))
        again = self.ctl("next", job, "--redeliver")
        self.assertEqual(again["kind"], first["kind"], "--redeliver repeats the outstanding action")

    def test_cmd_done(self):
        job = self.ctl("new", "--size", "S", "Add a footer")["job"]
        action = self.ctl("next", job)
        while action["kind"] == "inline":
            action = self.ctl("done", job, action["step"], "--attempt", str(action["attempt"]), "--via", "inline")["next"]
        entry = action["entries"][0]
        self.agents.handle(entry)
        res = self.ctl("done", job, entry["step"], "--attempt", str(entry["attempt"]), "--via", "spawn")
        self.assertEqual(res["result"], "valid")
        dup = self.ctl("done", job, entry["step"], "--attempt", str(entry["attempt"]), "--via", "spawn")
        self.assertEqual(dup["result"], "duplicate")

    def test_cmd_approve(self):
        self.agents.decision = "ESCALATE"
        eng = self.engine(SESSION)
        job = eng.new_job("Change deploy config", size="S")["id"]
        self.assertEqual(Driver(eng, self.agents).run(job)["kind"], "human")
        out = self.ctl("approve", job)
        self.assertIn(out.get("kind"), ("done", "inline", "spawn"))

    def test_cmd_report(self):
        job = self.merged_job()
        path = Path(self.ctl("report", job)["report"])
        text = path.read_text()
        self.assertIn("## Summary", text)
        self.assertIn(job, text)

    def test_cmd_resume(self):
        job = self.ctl("new", "Add a footer")["job"]
        self.ctl("next", job)
        other = subprocess.run([sys.executable, str(FACTORYCTL), "--session", "another", "resume", job],
                               cwd=self.repo, capture_output=True, text=True)
        self.assertNotEqual(other.returncode, 0, "a fresh lease held by another session is refused")
        forced = json.loads(subprocess.run([sys.executable, str(FACTORYCTL), "--session", "another", "resume", job,
                                            "--force"], cwd=self.repo, capture_output=True, text=True).stdout)
        self.assertIn("epoch", json.dumps(forced))

    def test_cmd_rework(self):
        self.agents.decision = "ESCALATE"
        eng = self.engine(SESSION)
        job = eng.new_job("Change deploy config", size="S")["id"]
        Driver(eng, self.agents).run(job)
        out = self.ctl("rework", job, "--note", "Keep the old config as a fallback")
        self.assertEqual(out.get("kind"), "spawn")
        note = self.paths.job_dir(job) / "rework-1.md"
        self.assertIn("Keep the old config as a fallback", note.read_text())
        prompt = out["entries"][0]["prompt"]
        self.assertIn("rework-1.md", prompt)
        self.assertIn("Round: 2", prompt)
        self.assertNotIn("round 1.", prompt.lower(), "the instruction names the round the header names")

    def test_cmd_cancel(self):
        job = self.ctl("new", "Add a footer")["job"]
        self.ctl("cancel", job, "--reason", "not needed")
        self.assertEqual(self.ctl("status", job, "--json")["status"], "cancelled")

    def test_cmd_tell(self):
        job = self.ctl("new", "Add a footer")["job"]
        msg = self.ctl("tell", job, "--orchestrator", "Pause", "after", "the", "spec")
        self.assertEqual(msg["to"], {"role": "orchestrator"})
        self.ctl("tell", job, "--agent", "nobody", "hi", expect=1, json_out=False)

    def test_cmd_messages(self):
        job = self.ctl("new", "Add a footer")["job"]
        self.ctl("tell", job, "--agent", "builder", "Use the brand colour")
        msgs = self.ctl("messages", job)
        self.assertEqual([(m["text"], m["status"]) for m in msgs], [("Use the brand colour", "queued")])

    def test_cmd_status(self):
        job = self.merged_job()
        board = self.ctl("status", "--json")
        self.assertEqual(board["jobs"][0]["id"], job)
        text = self.ctl("status", json_out=False)
        self.assertIn(job.split("-")[0], text)

    def test_cmd_doctor(self):
        out = self.ctl("doctor", "--json")
        self.assertEqual(out["repo"], str(self.repo))
        names = {c["check"] for c in out["checks"]}
        self.assertTrue(any("python" in n.lower() for n in names), names)
        self.assertTrue(any("git" in n.lower() for n in names), names)

    def test_cmd_catalog(self):
        cat = self.ctl("catalog", "--json")
        self.assertEqual(len(cat["agents"]), 25)
        self.assertIn("feature", {w["id"] for w in cat["workflows"]})

    def test_cmd_plan_validate(self):
        good = self.tmp / "good.json"
        good.write_text(json.dumps({"tasks": [{"id": "T1", "agent": "builder", "title": "x", "owns": ["a/**"]},
                                              {"id": "T2", "agent": "builder", "title": "y", "owns": ["b/**"],
                                               "depends_on": ["T1"]}]}))
        self.assertEqual(self.ctl("plan-validate", str(good))["order"], ["T1", "T2"])
        bad = self.tmp / "bad.json"
        bad.write_text(json.dumps({"tasks": [{"id": "T1", "agent": "approver", "title": "x", "owns": []}]}))
        out = self.ctl("plan-validate", str(bad), expect=1)
        self.assertFalse(out["ok"])

    def test_cmd_serve(self):
        self.ctl("init")
        proc = subprocess.Popen([sys.executable, str(FACTORYCTL), "serve", "--port", "0"], cwd=self.repo,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(proc.stderr.close)
        self.addCleanup(proc.stdout.close)
        self.addCleanup(proc.wait, 10)
        self.addCleanup(proc.terminate)
        line = proc.stdout.readline()
        url = urllib.parse.urlparse(json.loads(line)["url"])
        port, key = url.port, urllib.parse.parse_qs(url.query)["token"][0]
        self.assertGreaterEqual(len(key), 24, "the launch link carries the console key")
        for _ in range(50):
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("GET", "/api/state", headers={"Host": f"127.0.0.1:{port}", "X-Factory-Token": key})
                data = json.loads(conn.getresponse().read())
                break
            except OSError:
                time.sleep(0.1)
        self.assertEqual(data["repo"]["root"], str(self.repo))

    def test_cmd_index(self):
        job = self.merged_job()
        (self.repo / ".factory" / "index.json").unlink()
        self.ctl("index")
        index = json.loads((self.repo / ".factory" / "index.json").read_text())
        self.assertEqual([j["id"] for j in index["jobs"]], [job])

    def test_cmd_backlog(self):
        self.ctl("backlog", "add", "Dark", "mode", json_out=False)
        self.ctl("backlog", "add", "CSV export", json_out=False)
        items = self.ctl("backlog", "list", "--json")["items"]
        self.assertEqual([i["text"] for i in items], ["Dark mode", "CSV export"])
        nxt = self.ctl("backlog", "next", "--json")
        self.assertIn("Dark mode", json.dumps(nxt))

    def test_cmd_trust(self):
        self.write_config({"worktree": {"setup": ["make deps"]}})
        self.git("add", "-f", ".factory/config.json")
        self.git("commit", "-qm", "ship config")
        job = self.ctl("new", "--size", "S", "Add a footer")["job"]
        warned = self.ctl("next", job)
        self.assertEqual((warned["commands"], "factoryctl trust" in warned["config_warning"]), ([], True))
        out = self.ctl("trust")
        self.assertTrue(out["tracked"])
        self.assertEqual(out["config"]["worktree"]["setup"], ["make deps"], "it shows what you are trusting")
        job2 = self.ctl("new", "--size", "S", "Add a header")["job"]
        self.assertEqual(self.ctl("next", job2)["commands"], ["make deps"])

    def test_cmd_reseal(self):
        job = self.ctl("new", "--size", "S", "Add a footer")["job"]
        path = self.repo / ".factory" / "jobs" / job / "job.json"
        data = json.loads(path.read_text())
        data["title"] = "Edited by hand"
        path.write_text(json.dumps(data))
        err = self.ctl("next", job, expect=4, json_out=False)
        self.assertEqual(err, "")
        out = self.ctl("reseal", job)
        self.assertEqual(out["result"], "resealed")
        self.assertIn(self.ctl("next", job)["kind"], ("inline", "spawn"))
