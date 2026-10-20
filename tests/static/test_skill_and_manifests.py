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

    def test_generated_docs_are_current(self):
        import subprocess

        proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_docs.py"), "--check"], capture_output=True,
                              text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_doc_links_resolve(self):
        docs = [ROOT / "README.md", *(ROOT / "docs").rglob("*.md"), ROOT / "upstream" / "NOTICE.md"]
        for md in docs:
            for link in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", md.read_text()):
                if not link.startswith(("http://", "https://", "mailto:")):
                    self.assertTrue((md.parent / link).exists(), f"{md.relative_to(ROOT)} links to missing {link}")

    def test_version_matches_engine(self):
        from factory_core import __version__

        plugin = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())
        self.assertEqual(plugin["version"], __version__)

    def test_guard_policy_compiles_and_is_complete(self):
        policy = json.loads((PLUGIN / "guard-policy.json").read_text())
        ids = set()
        source = (PLUGIN / "lib" / "factory_core" / "guard.py").read_text()
        for rule in policy["rules"]:
            if rule.get("check") == "builtin":
                self.assertIn(f'"{rule["id"]}"', source, f"{rule['id']} is not produced by guard.py")
            else:
                re.compile(rule["pattern"])
            self.assertIn(rule["decision"], ("deny", "ask"))
            self.assertTrue(rule["reason"])
            self.assertNotIn(rule["id"], ids)
            ids.add(rule["id"])
        for must in ("no-push", "iac-apply", "rm-root", "publish", "deploy", "main-checkout", "factory-state"):
            self.assertIn(must, ids)
        emitted = set(re.findall(r'ctx\.hit\("([a-z-]+)"', source)) - {"sql-client"}
        self.assertEqual(emitted - ids, set(), "every rule the guard can emit has a decision and reason here")

    def test_ui_assets_referenced_exist(self):
        html = (PLUGIN / "ui" / "index.html").read_text()
        for ref in re.findall(r'(?:href|src)="/assets/([^"]+)"', html):
            self.assertTrue((PLUGIN / "ui" / ref).exists(), ref)
        for js in (PLUGIN / "ui" / "js").rglob("*.js"):
            for imp in re.findall(r'from "(\.[^"]+)"', js.read_text()):
                self.assertTrue((js.parent / imp).resolve().exists(), f"{js.name} imports {imp}")
            self.assertNotIn("innerHTML = `", js.read_text(), f"{js.name}: build DOM with h(), not template HTML")

    def test_no_secrets_or_machine_paths_committed(self):
        # Fixtures that need a secret-shaped value assemble it at run time, so scanners never see one here.
        pattern = re.compile(r"(sk-ant-[A-Za-z0-9]{10,}|[sr]k_live_[0-9A-Za-z]{10,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}"
                             r"|xox[bp]-[0-9A-Za-z-]{10,}|/Users/[a-z]+/|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY)")
        roots = [ROOT / "plugins", ROOT / "scripts", ROOT / "tests", ROOT / "docs"]
        for root in roots:
            for path in root.rglob("*"):
                if not path.is_file() or "node_modules" in path.parts or "__pycache__" in path.parts:
                    continue
                if path.suffix in (".png", ".jpg", ".zip", ".gz", ".pyc"):
                    continue
                hits = [h[0] if isinstance(h, tuple) else h for h in pattern.findall(path.read_text(errors="ignore"))]
                self.assertFalse(hits, f"{path}: {hits[:2]}")


if __name__ == "__main__":
    unittest.main()
