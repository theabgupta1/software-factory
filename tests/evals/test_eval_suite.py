"""The agent-quality eval suite, checked without a model: every case loads by the documented schema, every scaffold
and checker runs, and every regex grader passes the golden good output and fails the golden bad one.

What this cannot check is the model itself; `scripts/eval.sh` runs the suite for real (paid, on your machine).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVALS = ROOT / "plugins" / "factory" / "evals"
sys.path.insert(0, str(ROOT / "plugins" / "factory" / "lib"))

from factory_core import plan as engine_plan  # noqa: E402

# From https://code.claude.com/docs/en/plugin-evals (schema 1.1).
PROMPT_KEYS = {"schema_version", "name", "description", "tags", "plugins", "runs", "expected_outcome", "model",
               "max_turns", "timeout_seconds", "allowed_tools", "append_system_prompt", "env"}
CASE_KEYS = {"schema_version", "name", "description", "tags", "plugins", "runs", "expected_outcome", "graders",
             "execution", "context"}
CONTEXT_KEYS = {"scaffold_script", "history_file", "add_dirs"}
READ_ONLY_TOOLS = {"Read", "Glob", "Grep", "NotebookRead", "Skill", "AskUserQuestion", "Agent", "TodoWrite",
                   "TaskCreate", "TaskGet", "TaskList", "TaskUpdate", "TaskStop"}
GRADER_KEYS = {
    "regex": {"type", "weight", "arm", "pattern", "flags", "match", "target"},
    "tool_used": {"type", "weight", "arm", "tool", "input_match", "min", "max"},
    "tool_order": {"type", "weight", "arm", "before", "after"},
    "file_exists": {"type", "weight", "arm", "path", "exists"},
    "llm": {"type", "weight", "arm", "criteria", "focus"},
    "baseline": {"type", "weight", "arm", "baseline_file", "criteria"},
}


# ---- a strict parser for the YAML these files use (no dependency) -------------------------------------
def _scalar(text: str):
    text = text.strip()
    if text.startswith("'") and text.endswith("'"):
        return text[1:-1].replace("''", "'")
    if text.startswith('"') and text.endswith('"'):
        return json.loads(text)
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        return [_scalar(x) for x in _split(inner)] if inner else []
    if text.startswith("{") and text.endswith("}"):
        out = {}
        for part in _split(text[1:-1]):
            k, _, v = part.partition(":")
            out[k.strip()] = _scalar(v)
        return out
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    return {"true": True, "false": False, "null": None}.get(text, text)


def _split(text: str) -> list[str]:
    parts, depth, cur, quote = [], 0, "", None
    for ch in text:
        if quote:
            quote = None if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
            continue
        cur += ch
    return [p.strip() for p in parts + [cur] if p.strip()]


def mini_yaml(text: str) -> dict:
    root: dict = {}
    stack = [(-1, root)]
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        m = re.fullmatch(r"([A-Za-z_][\w-]*):(?:\s+(.*))?", line.strip())
        if not m:
            raise ValueError(f"line {n}: not a 'key: value' line: {line!r}")
        while stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        if m.group(2) is None or m.group(2) == "":
            parent[m.group(1)] = {}
            stack.append((indent, parent[m.group(1)]))
        else:
            parent[m.group(1)] = _scalar(m.group(2))
    return root


def frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text()
    m = re.match(r"---\n(.*?)\n---\n?(.*)", text, re.S)
    if not m:
        raise AssertionError(f"{path}: no frontmatter")
    return mini_yaml(m.group(1)), m.group(2)


def cases() -> list[Path]:
    return sorted(p.parent for p in EVALS.rglob("prompt.md"))


def js_regex(pattern: str, flags: str | None) -> re.Pattern:
    # The patterns are JavaScript regexes; keep them to the subset Python reads the same way.
    assert "(?" not in pattern.replace("(?:", "").replace("(?=", "").replace("(?!", "").replace("(?<", ""), pattern
    return re.compile(pattern, re.I if flags == "i" else 0)


def regex_passes(grader: dict, workspace: Path, last_message: str, trace: str) -> bool | None:
    target = grader.get("target", "last_message")
    if target == "last_message":
        text = last_message
    elif target == "trace":
        text = trace
    elif isinstance(target, dict) and target.get("source") == "file":
        p = workspace / target["path"]
        if not p.exists():
            return None
        text = p.read_text()
    else:
        return None
    if text is None:
        return None
    found = js_regex(grader["pattern"], grader.get("flags")).findall(text)
    match = grader.get("match")
    if match == "not_contains":
        return not found
    if isinstance(match, str) and match.startswith("count:"):
        return len(found) == int(match.split(":")[1])
    return bool(found)


class Schema(unittest.TestCase):
    def test_there_are_cases(self):
        self.assertGreaterEqual(len(cases()), 7)

    def test_every_case_loads_by_the_documented_schema(self):
        names = set()
        for case in cases():
            with self.subTest(case=case.relative_to(EVALS).as_posix()):
                meta, body = frontmatter(case / "prompt.md")
                self.assertLessEqual(set(meta), PROMPT_KEYS)
                self.assertTrue(body.strip(), "the prompt body is the prompt")
                self.assertLessEqual(set(meta.get("allowed_tools", [])), READ_ONLY_TOOLS,
                                     "other tools come from --allow-tools in scripts/eval.sh")
                self.assertLessEqual(meta.get("max_turns", 10), 200)
                self.assertLessEqual(meta.get("timeout_seconds", 300), 3600)
                yml = mini_yaml((case / "case.yaml").read_text())
                self.assertEqual(yml.get("schema_version"), "1.1")
                self.assertLessEqual(set(yml), CASE_KEYS)
                self.assertNotIn(yml["name"], names)
                names.add(yml["name"])
                ctx = yml.get("context", {})
                self.assertLessEqual(set(ctx), CONTEXT_KEYS)
                if "scaffold_script" in ctx:
                    self.assertTrue((case / ctx["scaffold_script"]).exists())
                graders = sorted((case / "graders").glob("*.md"))
                self.assertTrue(graders, "a case with no grader does not load")
                for g in graders:
                    gm, gbody = frontmatter(g)
                    self.assertIn(gm.get("type"), GRADER_KEYS, g.name)
                    self.assertLessEqual(set(gm), GRADER_KEYS[gm["type"]], g.name)
                    self.assertIn(gm.get("arm"), (None, "with-only", "both"), g.name)
                    if gm["type"] == "regex":
                        js_regex(gm["pattern"], gm.get("flags"))
                        self.assertIn(gm.get("flags"), (None, "i"), g.name)
                    if gm["type"] == "llm":
                        self.assertRegex(gbody, r"PASS if .+") and self.assertRegex(gbody, r"FAIL if .+")

    def test_parser_agrees_with_pyyaml(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed (the suite's own parser is used)")
        for f in list(EVALS.rglob("case.yaml")) + list(EVALS.rglob("*.md")):
            if "golden" in f.parts or f.name == "README.md":
                continue
            text = f.read_text()
            if f.suffix == ".md":
                text = re.match(r"---\n(.*?)\n---", text, re.S).group(1)
            with self.subTest(file=f.relative_to(EVALS).as_posix()):
                self.assertEqual(mini_yaml(text), yaml.safe_load(text))


class Goldens(unittest.TestCase):
    """Regex graders against golden outputs: the good one passes every grader, the bad one loses score."""

    def score(self, case: Path, golden: Path) -> tuple[float, list[str]]:
        last = (golden / "last_message.txt").read_text() if (golden / "last_message.txt").exists() else None
        trace = (golden / "trace.txt").read_text() if (golden / "trace.txt").exists() else None
        total = passed = 0.0
        failed = []
        for g in sorted((case / "graders").glob("*.md")):
            gm, _ = frontmatter(g)
            if gm["type"] != "regex":
                continue
            ok = regex_passes(gm, golden, last, trace)
            if ok is None:
                continue
            total += gm.get("weight", 1)
            passed += gm.get("weight", 1) if ok else 0
            if not ok:
                failed.append(g.stem)
        return (passed / total if total else 1.0), failed

    def test_good_outputs_pass_and_bad_outputs_fail(self):
        for case in cases():
            if not (case / "golden").exists():
                continue
            with self.subTest(case=case.relative_to(EVALS).as_posix()):
                good, failed = self.score(case, case / "golden" / "pass")
                self.assertEqual(good, 1.0, f"golden pass fails {failed}")
                bad, _ = self.score(case, case / "golden" / "fail")
                self.assertLess(bad, 0.5, "the golden bad output should lose most of the regex score")


class Scaffolds(unittest.TestCase):
    """Each scaffold builds its fixture in an empty folder, and each checker tells the goldens apart."""

    def build(self, case: Path) -> Path:
        ws = Path(tempfile.mkdtemp(prefix="eval-ws-"))
        self.addCleanup(shutil.rmtree, ws, True)
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin:" + str(Path(sys.executable).parent), "HOME": str(ws),
               "TERM": "dumb", "TMPDIR": tempfile.gettempdir()}
        proc = subprocess.run(["bash", str(case / "scaffold.sh")], cwd=ws, capture_output=True, text=True, env=env,
                              timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return ws

    def check(self, ws: Path, golden: Path) -> subprocess.CompletedProcess:
        for f in golden.iterdir():
            if f.suffix != ".txt":
                shutil.copy(f, ws / f.name)
        return subprocess.run([sys.executable, "check.py"], cwd=ws, capture_output=True, text=True, timeout=120)

    def test_scaffolds_run_and_checkers_separate_goldens(self):
        for case in cases():
            if not (case / "scaffold.sh").exists():
                continue
            with self.subTest(case=case.relative_to(EVALS).as_posix()):
                ws = self.build(case)
                if not (ws / "check.py").exists():
                    continue
                good = self.check(ws, case / "golden" / "pass")
                self.assertEqual((good.returncode, good.stdout.strip()), (0, "CHECK PASSED"), good.stdout + good.stderr)
                ws2 = self.build(case)
                bad = self.check(ws2, case / "golden" / "fail")
                self.assertNotEqual(bad.returncode, 0)
                self.assertIn("CHECK FAILED", bad.stdout)

    def test_review_fixture_has_the_seeded_defects_in_the_last_commit(self):
        ws = self.build(EVALS / "quality" / "review-seeded-defects")
        diff = subprocess.run(["git", "diff", "HEAD~1", "HEAD"], cwd=ws, capture_output=True, text=True).stdout
        for defect in ("f\"SELECT", "sk_live_", "debug=True", "owner_id"):
            self.assertIn(defect, diff)

    def test_plan_checker_agrees_with_the_engine(self):
        case = EVALS / "discover" / "plan-maps-acceptance"
        good = json.loads((case / "golden" / "pass" / "plan.json").read_text())
        bad = json.loads((case / "golden" / "fail" / "plan.json").read_text())
        self.assertEqual(engine_plan.validate(good)[0], [])
        self.assertTrue(engine_plan.validate(bad)[0])


if __name__ == "__main__":
    unittest.main()
