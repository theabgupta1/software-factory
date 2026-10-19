"""Static checks: the /factory skill, plugin manifests, hooks config, guard policy and workflow coverage."""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from support import PLUGIN, ROOT  # noqa: E402

from factory_core import catalog, registry  # noqa: E402

SKILL = PLUGIN / "skills" / "factory" / "SKILL.md"


class Skill(unittest.TestCase):
    def setUp(self):
        self.text = SKILL.read_text()
        self.meta, self.body = catalog.parse_frontmatter(self.text)

    def test_frontmatter(self):
        self.assertEqual(self.meta.get("name"), "factory")
        self.assertGreater(len(self.meta.get("description", "")), 80)
        self.assertIn("next", self.meta.get("argument-hint", ""))

    def test_every_action_kind_is_handled(self):
        for kind in ("spawn", "message", "inline", "wait", "human", "done"):
            self.assertRegex(self.body, rf"\*\*{kind}\*\*", f"SKILL.md must say how to execute '{kind}'")
        for needle in ("--redeliver", "--via fallback", "run_in_background", "verbatim", "notes"):
            self.assertIn(needle, self.body)

    def test_every_workflow_is_documented(self):
        intake = (SKILL.parent / "references" / "intake.md").read_text()
        for wid in registry.workflow_ids():
            self.assertIn(f"`{wid}`", self.body, wid)
            self.assertIn(f"`{wid}`", intake, wid)

    def test_referenced_files_exist(self):
        for ref in re.findall(r"`(references/[\w./-]+)`", self.body):
            self.assertTrue((SKILL.parent / ref).exists(), ref)

    def test_safety_rules_present(self):
        for rule in ("Never push", "never run `git merge`", "Copy prompts verbatim"):
            self.assertIn(rule.lower(), self.body.lower())

    def test_skill_matches_cli(self):
        from factory_core.cli import build_parser

        sub = next(a for a in build_parser()._actions if a.dest == "cmd")
        for cmd in ("new", "next", "done", "approve", "rework", "tell", "resume", "cancel", "report", "serve",
                    "doctor", "init", "backlog", "status"):
            self.assertIn(cmd, sub.choices, cmd)


class Manifests(unittest.TestCase):
    def test_plugin_and_marketplace(self):
        plugin = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
        self.assertEqual(plugin["name"], "factory")
        self.assertRegex(plugin["version"], r"^\d+\.\d+\.\d+$")
        market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
        names = {p["name"] for p in market["plugins"]}
        self.assertIn("factory", names)
        for p in market["plugins"]:
            self.assertTrue((ROOT / p["source"]).is_dir(), p["source"])

    def test_version_matches_engine(self):
        from factory_core import __version__

        plugin = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
        self.assertEqual(plugin["version"], __version__)

    def test_guard_policy_compiles_and_is_complete(self):
        policy = json.loads((PLUGIN / "guard-policy.json").read_text())
        ids = set()
        for rule in policy["rules"]:
            re.compile(rule["pattern"])
            self.assertIn(rule["decision"], ("deny", "ask"))
            self.assertTrue(rule["reason"])
            self.assertNotIn(rule["id"], ids)
            ids.add(rule["id"])
        for must in ("no-push", "iac-apply", "rm-root", "publish"):
            self.assertIn(must, ids)

    def test_ui_assets_referenced_exist(self):
        html = (PLUGIN / "ui" / "index.html").read_text()
        for ref in re.findall(r'(?:href|src)="/assets/([^"]+)"', html):
            self.assertTrue((PLUGIN / "ui" / ref).exists(), ref)
        for js in (PLUGIN / "ui" / "js").rglob("*.js"):
            for imp in re.findall(r'from "(\.[^"]+)"', js.read_text()):
                self.assertTrue((js.parent / imp).resolve().exists(), f"{js.name} imports {imp}")
            self.assertNotIn("innerHTML = `", js.read_text(), f"{js.name}: build DOM with h(), not template HTML")

    def test_no_secrets_or_machine_paths_committed(self):
        pattern = re.compile(r"(sk-ant-[A-Za-z0-9]{10,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}|/Users/[a-z]+/|BEGIN (RSA|OPENSSH) PRIVATE KEY)")
        for path in PLUGIN.rglob("*"):
            if path.is_file() and path.suffix in (".py", ".js", ".json", ".md", ".css", ".html"):
                hits = pattern.findall(path.read_text(errors="ignore"))
                self.assertFalse(hits, f"{path}: {hits[:2]}")


if __name__ == "__main__":
    unittest.main()
