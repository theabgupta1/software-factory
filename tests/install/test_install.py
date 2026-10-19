"""Installer and uninstaller against throwaway Claude homes: what is written, what is never touched, what comes back."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALL = ROOT / "scripts" / "install.py"
HAS_CLAUDE = shutil.which("claude") is not None


def snapshot(root: Path, skip: tuple[str, ...] = ()) -> dict[str, str]:
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            p = Path(dirpath) / name
            rel = p.relative_to(root).as_posix()
            if not rel.startswith(skip):
                out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


class InstallCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="factory-install-test-"))
        self.home = self.tmp / "claude-home"
        (self.home / "skills" / "other").mkdir(parents=True)
        (self.home / "settings.json").write_text('{"theme": "dark", "hooks": {}}\n')
        (self.home / "CLAUDE.md").write_text("# my rules\n")
        (self.home / "skills" / "other" / "SKILL.md").write_text("---\nname: other\n---\nmine\n")
        self.foreign = snapshot(self.home)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_installer(self, *args: str, script: Path = INSTALL, expect: int = 0) -> dict:
        proc = subprocess.run([sys.executable, str(script), *args, "--config-dir", str(self.home), "--json"],
                              capture_output=True, text=True, timeout=600)
        self.assertEqual(proc.returncode, expect, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def assert_foreign_untouched(self):
        now = snapshot(self.home)
        for rel, digest in self.foreign.items():
            self.assertEqual(now.get(rel), digest, f"{rel} was changed")

    def installed(self) -> dict[str, str]:
        return {k: v for k, v in snapshot(self.home / "skills" / "factory").items() if "__pycache__" not in k}


class Install(InstallCase):
    def test_dry_run_changes_nothing(self):
        before = snapshot(self.home)
        r = self.run_installer("install", "--dry-run")
        self.assertEqual(r["status"], "dry-run")
        self.assertEqual([s["action"] for s in r["steps"]][0], "install")
        self.assertEqual(snapshot(self.home), before)

    def test_install_places_plugins_and_leaves_everything_else_alone(self):
        r = self.run_installer("install")
        self.assertEqual(r["status"], "installed")
        self.assert_foreign_untouched()
        files = self.installed()
        for must in ("skills/factory/SKILL.md", "bin/factoryctl", "hooks/hooks.json", ".claude-plugin/plugin.json",
                     "agents/builder.md", "ui/index.html", "lib/factory_core/engine.py"):
            self.assertIn(must, files)
        for never in ("agents-src/", "protocol/", "tests/", "__pycache__/"):
            self.assertFalse([f for f in files if f.startswith(never) or f"/{never}" in f], never)
        manifest = json.loads((self.home / "factory-install.json").read_text())
        self.assertEqual(manifest["components"]["factory"]["files"], files)
        self.assertIsNone(manifest["components"]["factory"]["restore"])
        self.assertFalse((self.home / "backups").exists(), "a fresh install replaces nothing, so backs up nothing")
        self.assertFalse([p for p in (self.home / "skills").iterdir() if p.name.startswith(".factory-stage")])

    def test_installed_factoryctl_runs_in_a_repo(self):
        self.run_installer("install", "--no-live")
        ctl = self.home / "skills" / "factory" / "bin" / "factoryctl"
        repo = self.tmp / "repo"
        repo.mkdir()
        for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"],
                     ["commit", "-q", "--allow-empty", "-m", "init"]):
            subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(self.home)}
        out = subprocess.run([sys.executable, str(ctl), "init"], cwd=repo, capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue((repo / ".factory" / "index.json").exists())
        doc = subprocess.run([sys.executable, str(ctl), "doctor", "--json"], cwd=repo, capture_output=True, text=True)
        self.assertEqual(doc.returncode, 0, doc.stderr)

    def test_reinstall_is_idempotent(self):
        self.run_installer("install")
        first = snapshot(self.home)
        r = self.run_installer("install")
        self.assertEqual(r["status"], "up-to-date")
        self.assertEqual(snapshot(self.home), first)

    def test_local_edits_are_backed_up_on_upgrade(self):
        self.run_installer("install", "--no-live")
        skill = self.home / "skills" / "factory" / "skills" / "factory" / "SKILL.md"
        skill.write_text(skill.read_text() + "\nmy local tweak\n")
        r = self.run_installer("install", "--no-live")
        self.assertEqual(r["steps"][0]["action"], "upgrade")
        self.assertEqual(r["steps"][0]["locally_modified"], ["skills/factory/SKILL.md"])
        self.assertNotIn("my local tweak", skill.read_text())
        with tarfile.open(r["backup"]) as tar:
            data = tar.extractfile("skills/factory/skills/factory/SKILL.md").read().decode()
        self.assertIn("my local tweak", data)
        self.assertIsNone(json.loads((self.home / "factory-install.json").read_text())["components"]["factory"]["restore"],
                          "uninstall must not 'restore' the factory's own older copy")

    def test_upgrade_from_a_newer_source(self):
        self.run_installer("install", "--no-live")
        src = self.tmp / "newer"
        shutil.copytree(ROOT / "plugins", src / "plugins", ignore=shutil.ignore_patterns("__pycache__", "node_modules"))
        shutil.copytree(ROOT / "scripts", src / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        guide = src / "plugins" / "factory" / "skills" / "factory" / "references" / "intake.md"
        guide.write_text(guide.read_text() + "\nNew guidance.\n")
        r = self.run_installer("install", "--no-live", script=src / "scripts" / "install.py")
        self.assertEqual(r["steps"][0]["action"], "upgrade")
        self.assertIn("New guidance.", (self.home / "skills/factory/skills/factory/references/intake.md").read_text())
        self.assert_foreign_untouched()

    def test_foreign_folder_is_refused_then_replaced_and_restored(self):
        theirs = self.home / "skills" / "factory"
        theirs.mkdir()
        (theirs / "SKILL.md").write_text("---\nname: factory\n---\nthe original zazencodes factory\n")
        before = snapshot(self.home)
        r = self.run_installer("install", "--no-live", expect=3)
        self.assertIn("--replace-foreign", r["error"])
        self.assertEqual(snapshot(self.home), before, "a refused install changes nothing")

        r = self.run_installer("install", "--no-live", "--replace-foreign")
        self.assertEqual(r["steps"][0]["action"], "replace-foreign")
        self.assertTrue(Path(r["backup"]).exists())
        self.run_installer("uninstall")
        self.assertEqual((theirs / "SKILL.md").read_text(), "---\nname: factory\n---\nthe original zazencodes factory\n")
        self.assertEqual(sorted(p.name for p in theirs.iterdir()), ["SKILL.md"])

    def test_corrupt_manifest_is_refused(self):
        (self.home / "factory-install.json").write_text("{not json")
        before = snapshot(self.home)
        r = self.run_installer("install", expect=1)
        self.assertIn("not valid JSON", r["error"])
        self.assertEqual(snapshot(self.home), before)


class Uninstall(InstallCase):
    def test_uninstall_removes_exactly_what_was_installed(self):
        self.run_installer("install")
        (self.home / "factory").mkdir(exist_ok=True)
        (self.home / "factory" / "projects.json").write_text("{}")
        dry = self.run_installer("uninstall", "--dry-run")
        self.assertEqual(dry["status"], "dry-run")
        self.assertTrue((self.home / "skills" / "factory").exists())
        r = self.run_installer("uninstall")
        self.assertEqual(r["status"], "uninstalled")
        self.assertFalse((self.home / "skills" / "factory").exists())
        self.assertFalse((self.home / "skills" / "factory-live").exists())
        self.assertFalse((self.home / "factory-install.json").exists())
        self.assertTrue((self.home / "factory" / "projects.json").exists(), "user data stays unless --purge")
        self.assert_foreign_untouched()
        self.assertEqual(self.run_installer("uninstall")["status"], "not-installed")

    def test_uninstall_keeps_a_copy_of_local_edits(self):
        self.run_installer("install", "--no-live")
        note = self.home / "skills" / "factory" / "my-notes.md"
        note.write_text("keep me")
        r = self.run_installer("uninstall", "--purge")
        self.assertTrue(r["steps"][0]["locally_modified"])
        with tarfile.open(r["backup"]) as tar:
            self.assertEqual(tar.extractfile("skills/factory/my-notes.md").read(), b"keep me")


@unittest.skipUnless(HAS_CLAUDE, "the claude CLI is not installed")
class Discovery(InstallCase):
    def test_claude_code_loads_both_plugins_from_the_home(self):
        r = self.run_installer("install")
        self.assertTrue(r["preflight"]["live"], r["preflight"]["live_reason"])
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(self.home)}
        out = subprocess.run(["claude", "plugin", "list"], capture_output=True, text=True, env=env, timeout=120)
        self.assertIn("factory@skills-dir", out.stdout)
        self.assertIn("factory-live@skills-dir", out.stdout)
        self.assertEqual(out.stdout.count("loaded"), 2, out.stdout)
        details = subprocess.run(["claude", "plugin", "details", "factory"], capture_output=True, text=True, env=env,
                                 timeout=120)
        self.assertIn("Agents (25)", details.stdout)
        self.assertIn("Hooks (5)", details.stdout)


if __name__ == "__main__":
    unittest.main()
