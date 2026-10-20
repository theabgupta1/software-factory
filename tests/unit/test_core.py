"""Unit tests for the engine's pure building blocks."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import support  # noqa: E402,F401

from factory_core import config, contracts, panel, plan, prompts, registry, telemetry, util  # noqa: E402


def tmpfile(text: str, name: str = "x.md") -> Path:
    d = Path(tempfile.mkdtemp())
    p = d / name
    p.write_text(text, encoding="utf-8")
    return p


class Contracts(unittest.TestCase):
    def test_valid_review(self):
        c = contracts.check(tmpfile("VERDICT: PASS\n\n## Findings\nNone.\n"), "review")
        self.assertTrue(c.ok)
        self.assertEqual((c.value, c.tone), ("PASS", "good"))

    def test_header_normalisation_bom_whitespace_bold(self):
        c = contracts.check(tmpfile("﻿\n  **VERDICT: CHANGES**  \n\n## Findings\n1. x\n"), "review")
        self.assertTrue(c.ok, c.problems)
        self.assertEqual(c.value, "CHANGES")

    def test_wrong_header_and_missing_sections(self):
        c = contracts.check(tmpfile("Looks fine\n"), "review")
        self.assertFalse(c.ok)
        self.assertTrue(any("first line" in p for p in c.problems))
        c = contracts.check(tmpfile("STATUS: READY\n\n## Summary\n"), "build")
        self.assertIn("missing section '## Acceptance criteria'", " ".join(c.problems))

    def test_blocked_needs_a_blocked_section_only(self):
        self.assertTrue(contracts.check(tmpfile("STATUS: BLOCKED\n\n## Blocked\nwhy\n"), "build").ok)
        self.assertFalse(contracts.check(tmpfile("STATUS: BLOCKED\n\nno reason\n"), "build").ok)

    def test_missing_file_and_unknown_artifact(self):
        self.assertIn("was not written", contracts.check(Path("/nonexistent/x.md"), "spec").problems[0])
        self.assertFalse(contracts.check(tmpfile("x"), "nonsense").ok)

    def test_every_artifact_header_and_parse_header(self):
        for name, spec in registry.contracts()["artifacts"].items():
            hdr = registry.contracts()["headers"][spec["header"]]
            self.assertTrue(hdr["good"], name)
        self.assertEqual(contracts.parse_header("ESCALATE"), ("ESCALATE", "stop"))
        self.assertEqual(contracts.parse_header("STATUS: PARTIAL"), ("PARTIAL", "warn"))
        self.assertEqual(contracts.parse_header("hello"), (None, None))

    def test_section_text(self):
        text = "# T\n## Why\nbecause\n### detail\nmore\n## Next\nx\n"
        self.assertEqual(contracts.section_text(text, "why"), "because\n### detail\nmore")


class Plans(unittest.TestCase):
    def task(self, tid, **kw):
        return {"id": tid, "agent": "builder", "title": tid, "owns": [f"{tid.lower()}/**"], **kw}

    def test_valid_plan_orders_by_dependencies_then_id(self):
        problems, order = plan.validate({"tasks": [self.task("T3", depends_on=["T1"]), self.task("T1"), self.task("T2")]})
        self.assertEqual(problems, [])
        self.assertEqual(order, ["T1", "T2", "T3"])

    def test_cycle_unknown_dep_bad_agent_duplicate_and_size(self):
        cases = {
            "cycle": {"tasks": [self.task("T1", depends_on=["T2"]), self.task("T2", depends_on=["T1"])]},
            "unknown task": {"tasks": [self.task("T1", depends_on=["T9"])]},
            "cannot implement": {"tasks": [{**self.task("T1"), "agent": "approver"}]},
            "duplicate": {"tasks": [self.task("T1"), self.task("T1")]},
            "owns must list": {"tasks": [{**self.task("T1"), "owns": []}]},
            "split the job": {"tasks": [self.task(f"T{i}") for i in range(1, 15)]},
            "id must look": {"tasks": [{**self.task("T1"), "id": "task-one"}]},
        }
        for expected, data in cases.items():
            with self.subTest(case=expected):
                problems, _ = plan.validate(data)
                self.assertTrue(any(expected in p for p in problems), problems)
        self.assertEqual(plan.validate("nope")[0], ["plan.json is not valid JSON"])

    def test_ownership(self):
        self.assertEqual(plan.outside_ownership(["api/app.py", "web/x.tsx", "package-lock.json"], ["api/**", "api"]),
                         ["web/x.tsx"])


class Panel(unittest.TestCase):
    cfg = config.DEFAULTS
    stage = {"always": ["code", "security"], "rules": [{"if": "ui", "add": ["ux", "ui"]}, {"if": "perf", "add": ["performance"]}]}

    def keys(self, members):
        return [m["key"] for m in members]

    def test_backend_change_gets_core_panel(self):
        m = panel.select(self.stage, "feature", [], ["api/app.py"], "", self.cfg, 1)
        self.assertEqual(self.keys(m), ["code", "security"])

    def test_ui_files_or_declared_impact_add_ux_ui(self):
        m = panel.select(self.stage, "feature", [], ["web/src/Button.tsx"], "", self.cfg, 1)
        self.assertEqual(self.keys(m), ["code", "security", "ux", "ui"])
        self.assertIn("Button.tsx", m[2]["why"])
        m = panel.select(self.stage, "feature", [], ["api/app.py"], "Summary\nUI impact: yes\n", self.cfg, 1)
        self.assertIn("ux", self.keys(m))
        self.assertIn("declared UI impact", m[2]["why"])

    def test_perf_workflow_and_membership_only_grows(self):
        m1 = panel.select(self.stage, "data", [], ["web/x.css"], "", self.cfg, 1)
        self.assertIn("performance", self.keys(m1))
        m2 = panel.select(self.stage, "data", m1, ["api/only.py"], "", self.cfg, 2)
        self.assertEqual(self.keys(m2), self.keys(m1))
        self.assertEqual([m["since_round"] for m in m2], [1] * len(m2))


class Config(unittest.TestCase):
    def test_validation(self):
        self.assertEqual(config.validate({}), [])
        self.assertIn("unknown key 'colour'", config.validate({"colour": 1}))
        self.assertTrue(config.validate({"auto_merge": "yes"}))
        self.assertTrue(config.validate({"limits": {"max_agent_runs": 0}}))
        self.assertTrue(config.validate({"limits": {"max_agent_runs": True}}))
        self.assertTrue(config.validate({"limits": {"bogus": 3}}))
        self.assertTrue(config.validate([]))

    def test_merge_keeps_defaults(self):
        merged = config._merge(config.DEFAULTS, {"limits": {"max_agent_runs": 9}})
        self.assertEqual(merged["limits"]["max_agent_runs"], 9)
        self.assertEqual(merged["limits"]["max_review_rounds"], 3)


class Prompts(unittest.TestCase):
    def test_header_round_trip(self):
        job = {"id": "007-x", "worktree": "/w", "branch": "factory/007-x", "base_branch": "main"}
        text = prompts.header(job, "review.r2/security", 1, Path("/r/.factory/jobs/007-x"), Path("/o.md"),
                              round_no=2, mode="audit", port=5171, inputs=["spec.md", "build.md"])
        info = prompts.parse_header(text + "\n\nBody")
        self.assertEqual((info["job"], info["step"], info["attempt"]), ("007-x", "review.r2/security", 1))
        self.assertEqual(info["output"], "/o.md")
        self.assertEqual(info["port"], "5171")
        self.assertIsNone(prompts.parse_header("no header here"))

    def test_extras(self):
        out = prompts.with_extras("base", problems=["bad header"], messages=[{"ts": "2026-01-01T10:20:00.000Z", "text": "hi"}])
        self.assertIn("rejected by the factory's contract check", out)
        self.assertIn("[10:20 UTC] hi", out)
        self.assertEqual(prompts.with_extras("base"), "base")


class Telemetry(unittest.TestCase):
    def test_usage_dedupes_repeated_message_ids(self):
        lines = [{"type": "user", "timestamp": "2026-01-01T00:00:00.000Z", "message": {"content": "prompt"}}]
        for out in (3, 40):  # an early partial copy, then the final copy of the same message
            lines.append({"type": "assistant", "timestamp": "2026-01-01T00:00:05.000Z",
                          "message": {"id": "m1", "model": "claude-x", "usage": {"input_tokens": 7, "output_tokens": out},
                                      "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]}})
        p = tmpfile("\n".join(json.dumps(x) for x in lines) + "\n{truncated", "t.jsonl")
        u = telemetry.transcript_usage(p)
        self.assertEqual(u["usage"]["output"], 40)
        self.assertEqual(u["usage"]["input"], 7)
        self.assertEqual(u["tool_uses"], 1)
        self.assertEqual(u["seconds"], 5.0)
        feed = telemetry.activity(p)
        self.assertEqual([i["kind"] for i in feed["items"]], ["tool"])
        self.assertEqual(feed["items"][0]["summary"], "ls")


class Workflows(unittest.TestCase):
    def test_all_shipped_workflows_are_valid(self):
        for wid in registry.workflow_ids():
            with self.subTest(workflow=wid):
                self.assertEqual(registry.validate_workflow(registry.load_workflow(wid)), [])

    def test_validator_catches_mistakes(self):
        wf = json.loads(json.dumps(registry.load_workflow("feature")))
        wf["stages"][1]["agent"] = "ghost"
        wf["stages"][-2]["output"] = "../decision.md"
        panel_stage = next(s for s in wf["stages"] if s["kind"] == "panel")
        panel_stage["loop_to"] = "merge"
        problems = " ".join(registry.validate_workflow(wf))
        for expected in ("unknown agent 'ghost'", "plain file name", "loop_to must point backwards"):
            self.assertIn(expected, problems)


class Util(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(util.slugify("Add a 404 page for unknown routes!"), "404-page-unknown-routes")
        self.assertEqual(util.slugify("!!!"), "job")

    def test_jsonl_skips_corrupt_lines_and_atomic_write(self):
        p = tmpfile('{"a":1}\nnot json\n[1,2]\n{"b":2}\n', "e.jsonl")
        self.assertEqual(util.read_jsonl(p), [{"a": 1}, {"b": 2}])
        target = p.parent / "out.json"
        util.write_json_atomic(target, {"k": "v"})
        self.assertEqual(json.loads(target.read_text()), {"k": "v"})
        self.assertEqual([x.name for x in p.parent.iterdir() if x.name.startswith(".out.json.")], [])

    def test_timestamps(self):
        self.assertEqual(util.seconds_between("2026-01-01T00:00:00.000Z", "2026-01-01T00:01:30.500Z"), 90.5)
        self.assertIsNone(util.seconds_between(None, "2026-01-01T00:00:00Z"))


if __name__ == "__main__":
    unittest.main()
