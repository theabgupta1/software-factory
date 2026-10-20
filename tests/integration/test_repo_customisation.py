"""Repo-level customisation (several ideas adapted from mattpocock/sandcastle): worktree preparation, context
snapshots in prompts, repository standards, per-agent prompt additions, branch delivery, stall detection and
starting a job from a GitHub issue."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from support import FACTORYCTL, Driver, RepoCase, sh  # noqa: E402

from factory_core import config, console  # noqa: E402
from factory_core.store import Store  # noqa: E402


class WorktreePreparation(RepoCase):
    def test_untracked_files_are_copied_into_new_worktrees(self):
        (self.repo / ".gitignore").write_text(".env\nnode_modules/\n")
        sh(["git", "add", ".gitignore"], self.repo)
        sh(["git", "commit", "-qm", "ignore"], self.repo)
        (self.repo / ".env").write_text("API_KEY=dev\n")
        (self.repo / "node_modules" / "pkg").mkdir(parents=True)
        (self.repo / "node_modules" / "pkg" / "index.js").write_text("module.exports = 1\n")
        self.write_config({"worktree": {"copy": [".env", "node_modules", "missing.txt"]}})
        eng = self.engine()
        job = eng.new_job("x", size="S")
        eng.next(job["id"])
        wt = Path(job["worktree"])
        self.assertEqual((wt / ".env").read_text(), "API_KEY=dev\n")
        self.assertTrue((wt / "node_modules" / "pkg" / "index.js").exists())
        saved = Store(self.paths).load(job["id"])
        self.assertEqual(saved["copied"], [".env", "node_modules"], "missing paths are skipped")
        self.assertEqual(sh(["git", "status", "--porcelain"], wt).stdout.strip(), "", "copied files stay untracked/ignored")

    def test_configured_setup_commands_become_the_inline_instruction(self):
        self.write_config({"worktree": {"setup": ["npm ci", "npm run build"], "setup_timeout_seconds": 600}})
        eng = self.engine()
        job = eng.new_job("x", size="S")
        action = eng.next(job["id"])
        self.assertEqual(action["kind"], "inline")
        self.assertEqual(action["commands"], ["npm ci", "npm run build"])
        self.assertIn("1. `cd ", action["instruction"])
        self.assertIn("&& npm run build`", action["instruction"])
        self.assertIn("600", action["instruction"])

    def test_config_rejects_paths_outside_the_repo(self):
        for bad in (["../secrets"], ["/etc/passwd"]):
            with self.subTest(bad=bad):
                self.assertTrue(config.validate({"worktree": {"copy": bad}}))
        self.assertTrue(config.validate({"deliver": "push"}))
        self.assertTrue(config.validate({"worktree": {"bogus": 1}}))
        self.assertEqual(config.validate({"deliver": "branch", "worktree": {"copy": [".env"]}}), [])


class PromptContext(RepoCase):
    def test_reviewers_get_commits_and_diffstat_and_round_deltas(self):
        self.agents.verdicts["code"] = ["CHANGES", "PASS"]
        eng = self.engine()
        job = eng.new_job("x", size="S")
        Driver(eng, self.agents).run(job["id"])
        r1 = next(c for c in self.agents.calls if c["step"] == "review.r1/code")
        r2 = next(c for c in self.agents.calls if c["step"] == "review.r2/code")
        approve = next(c for c in self.agents.calls if c["step"] == "approve")
        self.assertIn("Context snapshot", r1["prompt"])
        self.assertIn("Build build.r1/builder", r1["prompt"], "commit subjects are listed")
        self.assertIn("feature.py", r1["prompt"], "diffstat lists changed files")
        self.assertIn("Commits since round 1 was reviewed (1)", r2["prompt"])
        self.assertIn("Context snapshot", approve["prompt"])
        build = next(c for c in self.agents.calls if c["step"] == "build.r1/builder")
        self.assertNotIn("Context snapshot", build["prompt"], "implementers read the repo themselves")

    def test_standards_and_per_agent_additions(self):
        (self.paths.state / "prompts").mkdir(parents=True)
        (self.paths.state / "standards.md").write_text("# Standards\nUse tabs.\n")
        (self.paths.state / "prompts" / "code-reviewer.md").write_text("Flag any use of eval().")
        eng = self.engine()
        job = eng.new_job("x", size="S")
        Driver(eng, self.agents).run(job["id"])
        for call in self.agents.calls:
            self.assertIn("standards.md", call["prompt"])
        code = next(c for c in self.agents.calls if c["step"] == "review.r1/code")
        sec = next(c for c in self.agents.calls if c["step"] == "review.r1/security")
        self.assertIn("Flag any use of eval()", code["prompt"])
        self.assertNotIn("Flag any use of eval()", sec["prompt"])


class BranchDelivery(RepoCase):
    def test_deliver_branch_leaves_an_unmerged_ready_branch(self):
        self.write_config({"deliver": "branch"})
        eng = self.engine()
        job = eng.new_job("Add a thing", size="S")
        final = Driver(eng, self.agents).run(job["id"])
        self.assertEqual(final["kind"], "done")
        self.assertEqual(final["outcome"], "branch")
        self.assertIn(f"git push -u origin {job['branch']}", final["commands"])
        self.assertNotIn("Merge factory/", self.git("log", "--oneline", "main"), "nothing merged")
        self.assertTrue(self.git("rev-parse", "--verify", job["branch"]), "branch kept")
        self.assertFalse(Path(job["worktree"]).exists(), "clean worktree removed")
        saved = Store(self.paths).load(job["id"])
        self.assertEqual(saved["status"], "delivered")


class StallDetection(RepoCase):
    def test_running_agent_without_activity_is_flagged(self):
        self.write_config({"stall_minutes": 1})
        eng = self.engine()
        job = eng.new_job("x", size="S")
        action = eng.next(job["id"])
        eng.done(job["id"], action["step"], action["attempt"])
        store = Store(self.paths)
        with store.lock(job["id"]):
            j = store.load(job["id"])
            j["steps"]["spec"]["issued_at"] = "2020-01-01T00:00:00.000Z"
            store.save(j)
        crew = console.job_detail(self.paths, job["id"])["crew"]
        spec = next(c for c in crew if c["step"] == "spec")
        self.assertTrue(spec["stalled"])
        self.assertGreater(spec["quiet_seconds"], 3600)


class FromIssue(RepoCase):
    def test_new_job_from_a_github_issue_via_gh(self):
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        payload = {"number": 42, "title": "Checkout fails with coupons", "body": "Steps: apply SAVE10, pay.",
                   "url": "https://github.com/acme/shop/issues/42"}
        gh.write_text("#!/bin/sh\n" + f"echo '{json.dumps(payload)}'\n")
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "CLAUDE_CODE_SESSION_ID": "S1"}
        out = subprocess.run([sys.executable, str(FACTORYCTL), "new", "--workflow", "fix", "--size", "S",
                              "--from-issue", "42"], cwd=self.repo, capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 0, out.stderr)
        job_id = json.loads(out.stdout)["job"]
        job = Store(self.paths).load(job_id)
        self.assertEqual(job["title"], "Checkout fails with coupons")
        self.assertIn("Steps: apply SAVE10", job["request"])
        self.assertEqual(job["params"]["issue"], "42")
        self.assertIn("issues/42", job["params"]["issue_url"])

    def test_missing_request_is_an_error(self):
        out = subprocess.run([sys.executable, str(FACTORYCTL), "new"], cwd=self.repo, capture_output=True, text=True)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("give a request", out.stderr)


if __name__ == "__main__":
    unittest.main()
