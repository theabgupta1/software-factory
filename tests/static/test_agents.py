"""Static checks on the agent definitions.

The generated files in plugins/factory/agents/ must be current, carry the roster's frontmatter, have the ten
required sections in order, and state output contracts that match contracts/artifacts.json and the workflows.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "factory"
sys.path.insert(0, str(PLUGIN / "lib"))

from factory_core import registry  # noqa: E402

SCRIPT = ROOT / "scripts" / "build_agents.py"
AGENTS_DIR = PLUGIN / "agents"
SOURCES_DIR = PLUGIN / "agents-src"
PROTOCOL = PLUGIN / "protocol" / "protocol.md"

ALLOWED_TOOLS = {"Read", "Grep", "Glob", "Bash", "Edit", "Write", "WebSearch", "WebFetch", "NotebookEdit"}
MODELS = {"opus", "sonnet", "haiku", "inherit"}
COLORS = {"discover": "purple", "build": "blue", "data": "cyan", "infra": "orange", "quality": "pink",
          "deliver": "yellow"}
SECTIONS = [
    "Purpose and scope", "Not responsible for", "Inputs", "Output contract", "Working rules", "Verification",
    "Security constraints", "Errors and escalation", "Handoff", "Success criteria",
]
MARKER = "<!-- factory: step=<step-id> attempt=<k> -->"
# Header values an artifact may legitimately never use: a spec or a plan is either ready or blocked.
OPTIONAL_VALUES = {"PARTIAL"}
# Conventional file names for artifacts, checked when no workflow names the file for that agent.
DEFAULT_FILES = {
    "spec": "spec.md", "context": "context.md", "research": "research.md", "design": "design.md",
    "data-design": "data-design.md", "infra-design": "infra-design.md", "test-plan": "test-plan.md",
    "diagnosis": "diagnosis.md", "build": "build.md", "task": "tasks/T<n>.md", "test-report": "test-report.md",
    "dq-report": "dq-report.md", "perf-report": "perf-report.md", "ops-report": "ops-report.md",
    "docs": "build.md", "report": "report.md", "decision": "decision.md",
}
EDIT_INSTRUCTION = re.compile(
    r"^\s*(?:[-*]|\d+\.)?\s*(?:Edit|Modify|Change|Fix|Patch|Refactor|Implement)\s+"
    r"(?:the\s+|this\s+|that\s+|any\s+|a\s+|an\s+)?"
    r"(?:code|source|implementation|functions?|tests?|bug|defect|files?)\b"
)
USES_EDIT_TOOL = re.compile(r"\b(?:use|with) (?:the )?Edit\b")
NEGATION = re.compile(r"\b(?:never|not|no|cannot|nor|without)\b|n't\b", re.I)
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")


def load_json(path: Path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


ROSTER = load_json(PLUGIN / "contracts" / "agents.json")
AGENTS = {a["name"]: a for a in ROSTER["agents"]}
CONTRACTS = load_json(PLUGIN / "contracts" / "artifacts.json")
WORKFLOWS = {p.stem: load_json(p) for p in sorted((PLUGIN / "workflows").glob("*.json"))}
REVIEW_KEYS = {a["review_key"]: name for name, a in AGENTS.items() if a.get("review_key")}


# ---- parsing helpers ---------------------------------------------------------------------------------------
def split_frontmatter(text: str) -> tuple[dict, str]:
    m = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        key, _, value = line.partition(":")
        value = value.strip()
        meta[key.strip()] = json.loads(value) if value.startswith('"') else value
    return meta, text[m.end():]


def outside_fences(text: str) -> list[str]:
    """Lines that are not inside fenced code blocks (fence lines themselves excluded)."""
    out, fenced = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            out.append(line)
    return out


def h2_headings(text: str) -> list[str]:
    return [line[3:].strip() for line in outside_fences(text) if line.startswith("## ")]


def section(text: str, name: str) -> str:
    """Raw text of an H2 section (code blocks included), up to the next H2 outside a fence."""
    out, inside, fenced = [], False, False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if not fenced and line.startswith("## "):
            if inside:
                break
            inside = line[3:].strip() == name
            continue
        if inside:
            out.append(line)
    return "\n".join(out)


def fenced_blocks(text: str) -> list[list[str]]:
    blocks, current = [], None
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            if current is None:
                current = []
            else:
                blocks.append(current)
                current = None
            continue
        if current is not None:
            current.append(line)
    return blocks


def header_forms() -> dict[str, tuple[str, list[str]]]:
    """Header type -> (prefix, allowed values), parsed from the artifact contract regexes."""
    forms = {}
    for name, spec in CONTRACTS["headers"].items():
        m = re.fullmatch(r"\^(?P<prefix>[^(]*)\((?P<alts>[A-Z|]+)\)\$", spec["pattern"])
        assert m, f"unexpected header pattern for {name}: {spec['pattern']}"
        forms[name] = (m.group("prefix"), m.group("alts").split("|"))
    return forms


HEADERS = header_forms()
HEADER_LIKE = re.compile(r"^(?:STATUS|VERDICT): |^(?:APPROVE|ESCALATE)\b")


def template_values(first_line: str, header: str) -> list[str] | None:
    """The values a template's first line offers for a header type, or None if it is not that header."""
    prefix, _ = HEADERS[header]
    if not first_line.startswith(prefix):
        return None
    values = [v.strip() for v in first_line[len(prefix):].split("|")]
    return values if all(re.fullmatch(r"[A-Z]+", v) for v in values) else None


def produced_artifacts(name: str) -> list[str]:
    arts = list(AGENTS[name]["outputs"])
    if name in registry.IMPLEMENTERS:
        arts += [a for a in ("build", "task") if a not in arts]
    return arts


def workflow_duties() -> dict[str, list[tuple[str, str]]]:
    """Agent -> list of (kind, text it must mention), derived from the workflow definitions."""
    duties: dict[str, list[tuple[str, str]]] = {n: [] for n in AGENTS}
    has_plan = {wid for wid, wf in WORKFLOWS.items() if any(s.get("plan_json") for s in wf["stages"])}
    for wid, wf in WORKFLOWS.items():
        for stage in wf["stages"]:
            kind = stage["kind"]
            members = [stage] if kind in ("agent", "approve") else stage.get("members", []) if kind == "parallel" else []
            for m in members:
                duties[m["agent"]].append((f"{wid}: output", m["output"]))
                if m.get("mode") or stage.get("mode"):
                    duties[m["agent"]].append((f"{wid}: mode", f"Mode: {m.get('mode') or stage.get('mode')}"))
                if m.get("plan_json"):
                    for key in ("plan.json", '"depends_on"', '"owns"', '"acceptance"'):
                        duties[m["agent"]].append((f"{wid}: plan.json", key))
                    for impl in sorted(registry.IMPLEMENTERS):
                        duties[m["agent"]].append((f"{wid}: plan agents", impl))
            if kind == "build":
                agent = stage.get("agent", "builder")
                names = sorted(registry.IMPLEMENTERS) if agent.startswith("$") else [agent]
                for n in names:
                    duties[n].append((f"{wid}: build output", "build.md"))
                    duties[n].append((f"{wid}: build mode", f"Mode: {stage.get('mode') or 'implement'}"))
                    if wid in has_plan and not agent.startswith("$"):
                        duties[n].append((f"{wid}: integrate", "Mode: integrate"))
            if kind == "panel":
                keys = list(stage.get("always", [])) + [k for r in stage.get("rules", []) for k in r["add"]]
                for key in keys:
                    duties[REVIEW_KEYS[key]].append((f"{wid}: panel", f"round-<N>/review-{key}.md"))
    for impl in registry.IMPLEMENTERS:
        for text in ("Mode: task", "tasks/T<n>.md", "Factory-Task: T<n>", "Mode: quick"):
            duties[impl].append(("implementer", text))
    return duties


DUTIES = workflow_duties()


def generated(name: str) -> str:
    return (AGENTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def body_of(name: str) -> str:
    return (SOURCES_DIR / f"{name}.md").read_text(encoding="utf-8")


# ---- tests -------------------------------------------------------------------------------------------------
class GeneratedFilesAreCurrent(unittest.TestCase):
    def test_build_agents_check_passes(self):
        proc = subprocess.run([sys.executable, str(SCRIPT), "--check"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"stale agent files:\n{proc.stdout}{proc.stderr}")

    def test_sources_match_roster(self):
        self.assertEqual(sorted(p.stem for p in SOURCES_DIR.glob("*.md")), sorted(AGENTS))
        self.assertEqual(sorted(p.stem for p in AGENTS_DIR.glob("*.md")), sorted(AGENTS))
        self.assertEqual(len(AGENTS), 25)

    def test_render_is_deterministic_and_quotes_unsafe_yaml(self):
        spec = importlib.util.spec_from_file_location("build_agents", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        agent = AGENTS["approver"]
        first = mod.render(agent, body_of("approver"), PROTOCOL.read_text(encoding="utf-8"))
        second = mod.render(agent, body_of("approver"), PROTOCOL.read_text(encoding="utf-8"))
        self.assertEqual(first, second)
        self.assertTrue(first.endswith("\n") and not first.endswith("\n\n"))
        self.assertEqual(mod.yaml_scalar("Final gate: APPROVE"), '"Final gate: APPROVE"')
        self.assertEqual(mod.yaml_scalar("Plain summary, with commas."), "Plain summary, with commas.")


class Frontmatter(unittest.TestCase):
    def test_frontmatter_matches_roster(self):
        for name, agent in AGENTS.items():
            with self.subTest(agent=name):
                meta, _ = split_frontmatter(generated(name))
                self.assertEqual(list(meta), ["name", "description", "tools", "model", "color"])
                self.assertEqual(meta["name"], name)
                self.assertEqual(meta["description"], agent["summary"])
                tools = [t.strip() for t in meta["tools"].split(",")]
                self.assertEqual(tools, agent["tools"])
                self.assertLessEqual(set(tools), ALLOWED_TOOLS)
                self.assertIn(meta["model"], MODELS)
                self.assertEqual(meta["model"], agent["model"])
                self.assertEqual(meta["color"], COLORS[agent["domain"]])

    def test_generated_comment_follows_frontmatter(self):
        for name in AGENTS:
            with self.subTest(agent=name):
                _, rest = split_frontmatter(generated(name))
                self.assertTrue(rest.startswith(
                    f"<!-- Generated by scripts/build_agents.py from agents-src/{name}.md and protocol/protocol.md. "
                    "Edit those, then run the script. -->\n"))


class Structure(unittest.TestCase):
    def test_title_and_required_sections_in_order(self):
        for name, agent in AGENTS.items():
            with self.subTest(agent=name):
                body = body_of(name)
                self.assertEqual(body.splitlines()[0], f"# {agent['title']}")
                self.assertEqual(h2_headings(body), SECTIONS)
                self.assertEqual(h2_headings(generated(name)), SECTIONS + ["Factory protocol"])

    def test_protocol_appended_exactly_once(self):
        protocol = PROTOCOL.read_text(encoding="utf-8").strip()
        self.assertFalse([ln for ln in outside_fences(protocol) if re.match(r"^#{1,2} ", ln)],
                         "the protocol is appended under an H2, so it may only use H3 and below")
        for name in AGENTS:
            with self.subTest(agent=name):
                text = generated(name)
                self.assertEqual(text.count("\n## Factory protocol\n"), 1)
                self.assertEqual(text.count(protocol), 1)
                self.assertTrue(text.endswith("\n## Factory protocol\n\n" + protocol + "\n"))

    def test_body_length_and_plain_text(self):
        for name in AGENTS:
            with self.subTest(agent=name):
                body = body_of(name)
                self.assertTrue(60 <= len(body.splitlines()) <= 260, f"{len(body.splitlines())} lines")
                self.assertIsNone(EMOJI.search(body))
        self.assertIsNone(EMOJI.search(PROTOCOL.read_text(encoding="utf-8")))


class OutputContracts(unittest.TestCase):
    def test_templates_cover_every_artifact(self):
        for name in AGENTS:
            contract = section(body_of(name), "Output contract")
            blocks = [b for b in fenced_blocks(contract) if b and HEADER_LIKE.match(b[0].strip())]
            for artifact in produced_artifacts(name):
                spec = CONTRACTS["artifacts"][artifact]
                _, allowed = HEADERS[spec["header"]]
                required = set(allowed) - OPTIONAL_VALUES
                with self.subTest(agent=name, artifact=artifact):
                    serving = []
                    for block in blocks:
                        values = template_values(block[0].strip(), spec["header"])
                        if values is not None and required <= set(values) <= set(allowed):
                            serving.append(block)
                    self.assertTrue(serving, f"no template whose first line offers {sorted(required)}")
                    ok = [b for b in serving
                          if all(any(re.match(rf"^## {re.escape(s)}\b", ln) for ln in b) for s in spec["sections"])]
                    self.assertTrue(ok, f"no {artifact} template has all of {spec['sections']}")

    def test_every_template_header_is_valid_for_the_agent(self):
        for name in AGENTS:
            contract = section(body_of(name), "Output contract")
            headers = {CONTRACTS["artifacts"][a]["header"] for a in produced_artifacts(name)}
            for block in fenced_blocks(contract):
                if not block or not HEADER_LIKE.match(block[0].strip()):
                    continue
                with self.subTest(agent=name, header=block[0]):
                    valid = [h for h in headers
                             if (v := template_values(block[0].strip(), h)) and set(v) <= set(HEADERS[h][1])]
                    self.assertTrue(valid, "header line is not valid for any artifact this agent produces")
                    self.assertEqual(block[1].strip() if len(block) > 1 else "", MARKER)

    def test_artifact_file_names_mentioned(self):
        for name in AGENTS:
            text = body_of(name)
            for artifact in produced_artifacts(name):
                if artifact in DEFAULT_FILES:
                    with self.subTest(agent=name, artifact=artifact):
                        self.assertIn(DEFAULT_FILES[artifact], text)

    def test_workflow_duties_are_described(self):
        for name, duties in DUTIES.items():
            text = body_of(name)
            for where, needle in duties:
                with self.subTest(agent=name, where=where, needle=needle):
                    self.assertIn(needle, text)

    def test_reviewers_name_their_files_and_never_edit(self):
        for key, name in REVIEW_KEYS.items():
            with self.subTest(agent=name):
                text = body_of(name)
                self.assertIn(f"review-{key}.md", text)
                self.assertIn(f"round-<N-1>/review-{key}.md", text, "reviewers re-check their previous round")
                self.assertRegex(text, r"(?i)\b(?:never edit|do not edit)\b")
                self.assertIn('"None."', text)


class Safety(unittest.TestCase):
    def test_agents_without_edit_never_instruct_editing(self):
        for name, agent in AGENTS.items():
            if "Edit" in agent["tools"]:
                continue
            with self.subTest(agent=name):
                body = body_of(name)
                self.assertRegex(body, r"(?i)\bnever (?:edit|change|modify)\b")
                for line in outside_fences(body):
                    if NEGATION.search(line):
                        continue
                    self.assertIsNone(EDIT_INSTRUCTION.match(line), line)
                    self.assertIsNone(USES_EDIT_TOOL.search(line), line)
                    self.assertNotRegex(line, r"\bgit (?:add|commit)\b")

    def test_git_push_only_in_prohibitions(self):
        for name in AGENTS:
            for line in generated(name).splitlines():
                if "git push" in line:
                    with self.subTest(agent=name, line=line):
                        self.assertRegex(line, r"never|Never|not")

    def test_protocol_safety_rules(self):
        protocol = PROTOCOL.read_text(encoding="utf-8")
        for needle in ("cd <worktree> && ", "STATUS: BLOCKED", "## Blocked", "Factory Console message",
                       "Messages from the user", "agent-browser skills get core", "--session <job-id>-",
                       "terraform apply", "never `git add -A`", "PreToolUse guard", "cannot spawn other agents",
                       "AGENTS.md", "CLAUDE.md", "not run", MARKER):
            with self.subTest(needle=needle):
                self.assertIn(needle, protocol)


if __name__ == "__main__":
    unittest.main()
