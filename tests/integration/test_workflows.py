"""End-to-end workflow runs through the real engine with scripted agents in temp git repos."""

from __future__ import annotations

import json
import multiprocessing
import os
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from support import ROOT, Driver, FakeAgents, RepoCase, doc, sh  # noqa: E402

from factory_core import registry  # noqa: E402
from factory_core.engine import Engine  # noqa: E402
from factory_core.paths import RepoPaths  # noqa: E402
from factory_core.store import Store  # noqa: E402
from factory_core.util import FactoryError, read_jsonl  # noqa: E402


class FeatureWorkflow(RepoCase):
    def test_small_feature_merges_into_main(self):
        eng = self.engine()
        job = eng.new_job("Add a greeting function", workflow="feature", size="S", why="small change")
        final = Driver(eng, self.agents).run(job["id"])
        self.assertEqual(final["kind"], "done")
        self.assertEqual(final["outcome"], "merged")
        log = self.git("log", "--oneline", "main")
        self.assertIn(f"Merge factory/{job['id']}", log)
        self.assertFalse(Path(job["worktree"]).exists(), "worktree should be removed after merge")
        self.assertEqual(self.git("branch", "--list", f"factory/{job['id']}"), "")
        saved = Store(self.paths).load(job["id"])
        self.assertEqual(saved["status"], "merged")
        self.assertTrue((self.paths.job_dir(job["id"]) / "final-report.md").exists())
        steps = [c["step"] for c in self.agents.calls]
        self.assertEqual(steps[0], "spec")
        self.assertNotIn("plan", steps, "size S skips planning")
        self.assertIn("review.r1/code", steps)
        self.assertIn("review.r1/security", steps)
        self.assertNotIn("review.r1/ux", steps, "no UI files changed")
        index = json.loads(self.paths.index.read_text())
        self.assertEqual(index["jobs"][0]["status"], "merged")
        types = [e["type"] for e in read_jsonl(self.paths.job_dir(job["id"]) / "events.jsonl")]
        for t in ("job.created", "worktree.created", "panel.selected", "round.completed", "merge.done"):
            self.assertIn(t, types)

    def test_changes_resume_builder_by_message_then_merge(self):
        self.agents.verdicts["security"] = ["CHANGES", "PASS"]
        eng = self.engine()
        job = eng.new_job("Rate limit signup", size="S")
        drv = Driver(eng, self.agents)
        final = drv.run(job["id"])
        self.assertEqual(final["outcome"], "merged")
        msgs = list({a["step"]: a for a in drv.actions if a["kind"] == "message"}.values())
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["to"], f"builder-{job['id']}")
        self.assertIn("Round 1 reviews are in", msgs[0]["message"])
        self.assertIn("fallback", msgs[0])
        saved = Store(self.paths).load(job["id"])
        self.assertEqual([r["result"] for r in saved["rounds"]], ["changes", "pass"])
        self.assertEqual(saved["counters"]["builder_resumes"], 1)

    def test_review_rounds_exhausted_escalates_and_can_be_approved(self):
        self.agents.verdicts["code"] = ["CHANGES"]
        eng = self.engine()
        job = eng.new_job("Refactor everything", size="S")
        final = Driver(eng, self.agents).run(job["id"])
        self.assertEqual(final["kind"], "human")
        self.assertEqual(final["kind"], "human")
        self.assertEqual(final["kind"], "human")
        self.assertEqual(final.get("kind"), "human")
        saved = Store(self.paths).load(job["id"])
        self.assertEqual(saved["human"]["kind"], "rounds-exhausted")
        self.assertEqual(len(saved["rounds"]), 3)
        decision = (self.paths.job_dir(job["id"]) / "decision.md").read_text()
        self.assertTrue(decision.startswith("ESCALATE"))
        self.assertIn("Outstanding CHANGES in round-3/ from: code", decision)
        done = eng.approve(job["id"])
        self.assertEqual(done["kind"], "done")
        self.assertEqual(done["outcome"], "merged")

    def test_blocked_build_then_rework_spawns_fresh_builder(self):
        self.agents.headers["build.r1/builder"] = "STATUS: BLOCKED"
        orig = self.agents.handle

        def handle(entry):
            orig(entry)
            info = __import__("factory_core.prompts", fromlist=["x"]).parse_header(entry["prompt"])
            if info["step"] == "build.r1/builder":
                Path(info["output"]).write_text("STATUS: BLOCKED\n\n# Build\n\n## Blocked\nThe spec contradicts itself.\n")

        self.agents.handle = handle
        eng = self.engine()
        job = eng.new_job("Do the impossible", size="S")
        final = Driver(eng, self.agents).run(job["id"])
        self.assertEqual(final["kind"], "human")
        self.assertEqual(final["kind"], "human")
        saved = Store(self.paths).load(job["id"])
        self.assertEqual(saved["human"]["kind"], "blocked")
        self.assertIn("contradicts", saved["human"]["reason"])
        action = eng.rework(job["id"], "Drop requirement 3; it conflicts with 2.")
        self.assertEqual(action["kind"], "spawn")
        entry = action["entries"][0]
        self.assertEqual(entry["step"], "build.r2/builder")
        self.assertIn("rework-1.md", entry["prompt"])
        self.assertEqual(entry["name"], f"builder-{job['id']}")
        final = Driver(eng, self.agents).run(job["id"], action)
        self.assertEqual(final["outcome"], "merged")

    def test_approver_escalation_waits_then_merges_on_approve(self):
        self.agents.decision = "ESCALATE"
        eng = self.engine()
        job = eng.new_job("Change the deploy script", size="S")
        final = Driver(eng, self.agents).run(job["id"])
        self.assertEqual(final["kind"], "human")
        self.assertEqual(Store(self.paths).load(job["id"])["human"]["kind"], "escalate")
        self.assertIn(f"/factory approve {job['id']}", final["commands"])
        self.assertEqual(eng.approve(job["id"])["outcome"], "merged")

    def test_dirty_main_checkout_blocks_merge_until_clean(self):
        eng = self.engine()
        job = eng.new_job("Add x", size="S")
        (self.repo / "README.md").write_text("# local edit\n")
        final = Driver(eng, self.agents).run(job["id"])
        self.assertEqual(final["kind"], "human")
        self.assertEqual(Store(self.paths).load(job["id"])["human"]["kind"], "merge-busy")
        sh(["git", "checkout", "README.md"], self.repo)
        self.assertEqual(eng.approve(job["id"])["outcome"], "merged")

    def test_merge_conflict_needs_human(self):
        self.agents.build_file = "app.py"
        eng = self.engine()
        job = eng.new_job("Edit app", size="S")
        first = eng.next(job["id"])  # creates the worktree from today's main
        (self.repo / "app.py").write_text("def main():\n    return 2\n# conflicting\n")
        sh(["git", "commit", "-qam", "main moves on"], self.repo)
        final = Driver(eng, self.agents).run(job["id"], first)
        self.assertEqual(final["kind"], "human")
        self.assertEqual(Store(self.paths).load(job["id"])["human"]["kind"], "merge-conflict")
        self.assertIn("app.py", final["reason"])

    def test_invalid_artifact_is_reissued_with_problems_then_limit(self):
        self.agents.headers["review.r1/code"] = "Looks good to me"
        eng = self.engine()
        job = eng.new_job("Add y", size="S")
        final = Driver(eng, self.agents).run(job["id"])
        self.assertEqual(final["kind"], "human")
        attempts = [c for c in self.agents.calls if c["step"] == "review.r1/code"]
        self.assertEqual(len(attempts), 2)
        self.assertIn("rejected by the factory's contract check", attempts[1]["prompt"])
        self.assertEqual(Store(self.paths).load(job["id"])["human"]["kind"], "limit")

    def test_reviewer_editing_code_is_an_integrity_stop(self):
        def sneaky(entry, info):
            Path(info["output"]).write_text(doc("VERDICT: PASS", "Findings"))
            (Path(info["worktree"]) / "app.py").write_text("tampered\n")

        self.agents.hooks["review.r1/security"] = sneaky
        eng = self.engine()
        job = eng.new_job("Add z", size="S")
        final = Driver(eng, self.agents).run(job["id"])
        self.assertEqual(Store(self.paths).load(job["id"])["human"]["kind"], "integrity")
        self.assertEqual(final["kind"], "human")


class PlannedWork(RepoCase):
    def test_plan_tasks_run_sequentially_with_trailers_then_integrate(self):
        self.agents.plan = {"tasks": [
            {"id": "T1", "agent": "backend-engineer", "title": "API", "owns": ["api.py"], "acceptance": ["AC1"]},
            {"id": "T2", "agent": "frontend-engineer", "title": "Page", "owns": ["web/page.tsx"],
             "depends_on": ["T1"]},
        ]}
        eng = self.engine()
        job = eng.new_job("Build a todo API and page", size="M")
        final = Driver(eng, self.agents).run(job["id"])
        self.assertEqual(final["outcome"], "merged")
        steps = [c["step"] for c in self.agents.calls]
        self.assertLess(steps.index("task/T1"), steps.index("task/T2"))
        self.assertLess(steps.index("task/T2"), steps.index("build.r1/builder"))
        t1 = next(c for c in self.agents.calls if c["step"] == "task/T1")
        self.assertEqual(t1["subagent_type"], "factory:backend-engineer")
        self.assertIn("Factory-Task: T1", t1["prompt"])
        integrate = next(c for c in self.agents.calls if c["step"] == "build.r1/builder")
        self.assertIn("Integrate round 1", integrate["prompt"])
        self.assertIn("review.r1/ux", steps, "a .tsx change brings in the UX reviewer")
        saved = Store(self.paths).load(job["id"])
        ui = next(m for m in saved["panel"] if m["key"] == "ui")
        self.assertIn("web/page.tsx", ui["why"])

    def test_task_outside_ownership_is_a_warning_not_a_failure(self):
        self.agents.plan = {"tasks": [{"id": "T1", "agent": "builder", "title": "x", "owns": ["only.py"]}]}

        def task(entry, info):
            wt = Path(info["worktree"])
            for name in ("only.py", "other.py"):
                (wt / name).write_text("x\n")
            sh(["git", "add", "only.py", "other.py"], wt)
            sh(["git", "commit", "-qm", "T1\n\nFactory-Task: T1"], wt)
            Path(info["output"]).write_text(doc("STATUS: READY", "Changes", "Validation"))

        self.agents.hooks["task/"] = task
        eng = self.engine()
        job = eng.new_job("Thing", size="M")
        Driver(eng, self.agents).run(job["id"])
        saved = Store(self.paths).load(job["id"])
        self.assertTrue(any("other.py" in w for w in saved["warnings"]))

    def test_missing_trailer_makes_task_invalid(self):
        self.agents.plan = {"tasks": [{"id": "T1", "agent": "builder", "title": "x", "owns": ["a.py"]}]}

        def task(entry, info):
            wt = Path(info["worktree"])
            with open(wt / "a.py", "a") as fh:
                fh.write(f"attempt {info['attempt']}\n")
            sh(["git", "add", "a.py"], wt)
            sh(["git", "commit", "-qm", "no trailer"], wt)
            Path(info["output"]).write_text(doc("STATUS: READY", "Changes", "Validation"))

        self.agents.hooks["task/"] = task
        eng = self.engine()
        job = eng.new_job("Thing", size="M")
        final = Driver(eng, self.agents).run(job["id"])
        self.assertEqual(final["kind"], "human")
        prompts_t1 = [c["prompt"] for c in self.agents.calls if c["step"] == "task/T1"]
        self.assertEqual(len(prompts_t1), 2)
        self.assertIn("Factory-Task: T1", prompts_t1[1])

    def test_cyclic_plan_is_rejected_and_architect_retried(self):
        self.agents.plan = {"tasks": [
            {"id": "T1", "agent": "builder", "title": "a", "owns": ["a"], "depends_on": ["T2"]},
            {"id": "T2", "agent": "builder", "title": "b", "owns": ["b"], "depends_on": ["T1"]}]}
        eng = self.engine()
        job = eng.new_job("Cyclic", size="M")
        final = Driver(eng, self.agents).run(job["id"])
        self.assertEqual(final["kind"], "human")
        plans = [c for c in self.agents.calls if c["step"] == "plan"]
        self.assertEqual(len(plans), 2)
        self.assertIn("dependency cycle", plans[1]["prompt"])

    def test_large_job_waits_for_plan_approval_and_rework_replans(self):
        eng = self.engine()
        job = eng.new_job("Big migration", size="L")
        final = Driver(eng, self.agents).run(job["id"])
        self.assertEqual(final["kind"], "human")
        self.assertEqual(final["kind"], "human")
        saved = Store(self.paths).load(job["id"])
        self.assertEqual(saved["status"], "awaiting-approval")
        self.assertEqual(saved["human"]["kind"], "plan-approval")
        self.assertIn("plan.json", saved["human"]["files"])
        action = eng.rework(job["id"], "Split T1 in two.")
        self.assertEqual(action["kind"], "spawn")
        self.assertEqual(action["entries"][0]["step"], "plan")
        self.assertIn("rework-1.md", action["entries"][0]["prompt"])
        final = Driver(eng, self.agents).run(job["id"], action)
        self.assertEqual(Store(self.paths).load(job["id"])["status"], "awaiting-approval")
        action = eng.approve(job["id"])
        final = Driver(eng, self.agents).run(job["id"], action)
        self.assertEqual(final["outcome"], "merged")


class OtherWorkflows(RepoCase):
    def test_every_workflow_reaches_its_terminal_state(self):
        expected = {"research": "delivered", "review": "delivered", "security": "delivered"}
        for wid in registry.workflow_ids():
            with self.subTest(workflow=wid):
                agents = FakeAgents()
                eng = self.engine()
                params = {"agent": "sql-engineer"} if wid == "quick" else {"repo": "true"} if wid == "research" else {}
                job = eng.new_job(f"Do the {wid} thing", workflow=wid, size="S", params=params)
                action = Driver(eng, agents).run(job["id"])
                if wid == "infra":
                    saved = Store(self.paths).load(job["id"])
                    self.assertEqual(saved["human"]["kind"], "plan-approval", "infra always gates the plan")
                    action = Driver(eng, agents).run(job["id"], eng.approve(job["id"]))
                    self.assertEqual(Store(self.paths).load(job["id"])["human"]["kind"], "policy")
                    action = eng.approve(job["id"])
                self.assertEqual(action["kind"], "done", action)
                self.assertEqual(action["outcome"], expected.get(wid, "merged"))
                if wid == "quick":
                    self.assertTrue(any(c["subagent_type"] == "factory:sql-engineer" for c in agents.calls))
                if wid == "research":
                    steps = {c["step"] for c in agents.calls}
                    self.assertEqual(steps, {"investigate/research", "investigate/context"})
                    self.assertFalse(Path(job["worktree"]).exists(), "unused research worktree is removed")
                if wid == "refactor":
                    steps = [c["step"] for c in agents.calls]
                    self.assertLess(steps.index("characterize"), steps.index("build.r1/builder"))


class Lease(RepoCase):
    def test_other_session_is_refused_while_lease_is_fresh(self):
        job = self.engine("A").new_job("x", size="S")
        with self.assertRaises(FactoryError):
            self.engine("B").next(job["id"])

    def test_resume_recovers_finished_work_and_reissues_lost_work(self):
        a = self.engine("A")
        job = a.new_job("x", size="S")
        action = a.next(job["id"])
        action = a.done(job["id"], action["step"], action["attempt"])["next"]
        self.assertEqual(action["entries"][0]["step"], "spec")
        # The spec agent finished but session A died before calling done.
        self.agents.handle(action["entries"][0])
        b = self.engine("B")
        with self.assertRaises(FactoryError):
            b.resume(job["id"])
        nxt = b.resume(job["id"], force=True)
        self.assertEqual(nxt["entries"][0]["step"], "build.r1/builder", "spec was recovered from disk")
        saved = Store(self.paths).load(job["id"])
        self.assertEqual(saved["epoch"], 2)
        self.assertEqual(saved["steps"]["spec"]["status"], "done")
        # The builder dies without output: resume marks it lost and re-issues attempt 2.
        nxt = self.engine("C").resume(job["id"], force=True)
        self.assertEqual(nxt["entries"][0]["step"], "build.r1/builder")
        self.assertEqual(nxt["entries"][0]["attempt"], 2)

    def test_done_is_idempotent(self):
        eng = self.engine()
        job = eng.new_job("x", size="S")
        a = eng.next(job["id"])
        first = eng.done(job["id"], a["step"], a["attempt"])
        again = eng.done(job["id"], a["step"], a["attempt"])
        self.assertEqual(first["result"], "valid")
        self.assertEqual(again["result"], "duplicate")
        self.assertEqual(eng.done(job["id"], a["step"], 0)["result"], "stale")

    def test_round_two_after_session_change_respawns_with_brief(self):
        self.agents.verdicts["code"] = ["CHANGES", "PASS"]
        a = self.engine("A")
        job = a.new_job("x", size="S")
        drv = Driver(a, self.agents)
        action = a.next(job["id"])
        # Run until the round-2 build is about to be issued by A, then switch sessions.
        while True:
            if action["kind"] == "message":
                break
            if action["kind"] == "inline":
                action = a.done(job["id"], action["step"], action["attempt"])["next"]
                continue
            for e in action["entries"]:
                self.agents.handle(e)
            for e in action["entries"]:
                res = a.done(job["id"], e["step"], e["attempt"])
            action = res["next"]
        b = self.engine("B")
        nxt = b.resume(job["id"], force=True)
        self.assertEqual(nxt["kind"], "spawn")
        self.assertIn("previous builder's session is gone", nxt["entries"][0]["prompt"])
        self.assertEqual(Driver(b, self.agents).run(job["id"], nxt)["outcome"], "merged")


class Messages(RepoCase):
    def test_message_to_agent_is_folded_into_its_next_prompt(self):
        eng = self.engine()
        job = eng.new_job("x", size="S")
        msg = eng.tell(job["id"], "Use the existing rate limiter in lib/limits.py", agent="builder")
        drv = Driver(eng, self.agents)
        drv.run(job["id"])
        build = next(c for c in self.agents.calls if c["step"] == "build.r1/builder")
        self.assertIn("lib/limits.py", build["prompt"])
        delivered = Store(self.paths).messages(job["id"])[0]
        self.assertEqual(delivered["id"], msg["id"])
        self.assertEqual(delivered["status"], "delivered")
        self.assertEqual(delivered["delivered"]["via"], "prompt")

    def test_message_to_orchestrator_is_returned_as_notes(self):
        eng = self.engine()
        job = eng.new_job("x", size="S")
        eng.tell(job["id"], "Pause after review please", role="orchestrator")
        action = eng.next(job["id"])
        self.assertEqual(action["notes"][0]["text"], "Pause after review please")
        self.assertNotIn("notes", eng.next(job["id"]))

    def test_message_validation(self):
        eng = self.engine()
        job = eng.new_job("x", size="S")
        with self.assertRaises(FactoryError):
            eng.tell(job["id"], "   ", agent="builder")
        with self.assertRaises(FactoryError):
            eng.tell(job["id"], "hi", agent="no-such-agent")
        with self.assertRaises(FactoryError):
            eng.tell(job["id"], "x" * 5000, agent="builder")


class Limits(RepoCase):
    def test_agent_run_limit_stops_the_job(self):
        self.write_config({"limits": {"max_agent_runs": 2}})
        eng = self.engine()
        job = eng.new_job("x", size="S")
        final = Driver(eng, self.agents).run(job["id"])
        self.assertEqual(final["kind"], "human")
        self.assertIn("Agent run limit", Store(self.paths).load(job["id"])["human"]["reason"])

    def test_auto_merge_off_waits_for_approval(self):
        self.write_config({"auto_merge": False})
        eng = self.engine()
        job = eng.new_job("x", size="S")
        final = Driver(eng, self.agents).run(job["id"])
        self.assertEqual(Store(self.paths).load(job["id"])["human"]["kind"], "approve-merge")
        self.assertEqual(eng.approve(job["id"])["outcome"], "merged")

    def test_always_escalate_globs(self):
        self.write_config({"always_escalate": ["*.py"]})
        eng = self.engine()
        job = eng.new_job("x", size="S")
        Driver(eng, self.agents).run(job["id"])
        human = Store(self.paths).load(job["id"])["human"]
        self.assertEqual(human["kind"], "policy")
        self.assertIn("feature.py", human["reason"])

    def test_cancel(self):
        eng = self.engine()
        job = eng.new_job("x", size="S")
        eng.next(job["id"])
        self.assertEqual(eng.cancel(job["id"], "changed my mind")["outcome"], "cancelled")
        with self.assertRaises(FactoryError):
            eng.cancel(job["id"])


class LegacyAndIds(RepoCase):
    def test_legacy_v1_jobs_are_listed_read_only(self):
        src = ROOT / "upstream" / "factory" / "jobs"
        shutil.copytree(src, self.repo / "factory" / "jobs")
        store = Store(self.paths)
        summaries = {s["id"]: s for s in store.summaries()}
        self.assertEqual(summaries["001-blog-reading-time"]["status"], "merged")
        self.assertTrue(summaries["002-not-found-page"]["legacy"])
        with self.assertRaises(FactoryError) as ctx:
            store.resolve("001-blog")
        self.assertIn("read-only", str(ctx.exception))
        job = self.engine().new_job("New work after upgrade", size="S")
        self.assertTrue(job["id"].startswith("003-"), "numbering continues after legacy jobs")

    def test_concurrent_id_claims_are_unique(self):
        (self.paths.jobs).mkdir(parents=True)
        with multiprocessing.get_context("fork").Pool(8) as pool:
            ids = pool.map(_claim, [str(self.paths.root)] * 16)
        self.assertEqual(len(set(ids)), 16)


def _claim(root: str) -> str:
    return Store(RepoPaths(Path(root))).claim_id("same")


class Backlog(RepoCase):
    def test_backlog_claim_and_link(self):
        from support import FACTORYCTL

        def ctl(*args):
            return sh([sys.executable, str(FACTORYCTL), *args], self.repo)

        ctl("backlog", "add", "Add a 404 page")
        ctl("backlog", "add", "Add reading time")
        claimed = json.loads(ctl("backlog", "next").stdout)
        self.assertEqual(claimed["item"], "Add a 404 page")
        env = {**os.environ, "CLAUDE_CODE_SESSION_ID": "S1"}
        import subprocess

        out = subprocess.run([sys.executable, str(FACTORYCTL), "new", claimed["item"], "--size", "S",
                              "--backlog-item", claimed["item"]], cwd=self.repo, capture_output=True, text=True,
                             env=env)
        self.assertEqual(out.returncode, 0, out.stderr)
        job_id = json.loads(out.stdout)["job"]
        text = self.paths.backlog.read_text()
        self.assertIn(f"- [x] Add a 404 page → {job_id}", text)
        self.assertIn("- [ ] Add reading time", text)


if __name__ == "__main__":
    unittest.main()
