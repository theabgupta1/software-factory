#!/usr/bin/env python3
"""Runs every test layer, writes each layer's results as JSON, and builds docs/TEST-MATRIX.md from them.

  scripts/test.sh                      every layer that can run here
  scripts/test.sh unit integration     some layers
  scripts/test.sh --matrix             every layer, then the matrix from this run's results
  python3 scripts/testkit.py matrix    the matrix from the results already in test-results/

Layers that cannot run (no Node, no Playwright browsers, no claude CLI, paid model runs) are recorded as NOT RUN with
the reason; nothing is reported as passing that did not run. Exit code: 1 if any check failed, else 0.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "test-results"
MATRIX = ROOT / "docs" / "TEST-MATRIX.md"


def rec(layer: str, suite: str, cid: str, status: str, duration: float = 0.0, detail: str = "") -> dict:
    return {"layer": layer, "suite": suite, "id": cid, "status": status, "duration": round(duration, 3),
            "detail": detail.strip()[:1500]}


def not_run(layer: str, suite: str, reason: str) -> list[dict]:
    return [rec(layer, suite, suite, "not-run", detail=reason)]


def sh(argv: list[str], timeout: int = 1800, env: dict | None = None, cwd: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, env={**os.environ, **(env or {})})


# ---- Python (unittest, no pytest needed) ----------------------------------------------------------------
class Recorder(unittest.TestResult):
    def __init__(self, layer: str, suite: str):
        super().__init__()
        self.layer, self.suite, self.records, self.t0 = layer, suite, [], {}

    def startTest(self, test):
        super().startTest(test)
        self.t0[test.id()] = time.monotonic()
        sys.stderr.write(".")
        sys.stderr.flush()

    def _elapsed(self, test) -> float:
        return time.monotonic() - self.t0.get(test.id(), time.monotonic())

    def _add(self, test, status, detail=""):
        self.records.append(rec(self.layer, self.suite, test.id(), status, self._elapsed(test), detail))

    def addSuccess(self, test):
        super().addSuccess(test)
        self._add(test, "passed")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._add(test, "failed", self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self._add(test, "failed", self._exc_info_to_string(err, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._add(test, "skipped", reason)

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        status = "passed" if err is None else "failed"
        self.records.append(rec(self.layer, self.suite, subtest.id(), status, 0,
                                "" if err is None else self._exc_info_to_string(err, test)))


def pyrun(layer: str, start: str, out: str) -> int:
    """Child process: discover and run one test folder, write records as JSON."""
    suite = unittest.defaultTestLoader.discover(str(ROOT / start), top_level_dir=str(ROOT / start))
    result = Recorder(layer, start)
    suite.run(result)
    sys.stderr.write("\n")
    # A test with failing subtests is reported once as failed even though its own body finished.
    failed_parents = {r["id"].split(" ", 1)[0] for r in result.records if r["status"] == "failed" and " " in r["id"]}
    for r in result.records:
        if r["id"] in failed_parents and r["status"] == "passed":
            r["status"] = "failed"
            r["detail"] = "one or more subtests failed"
    Path(out).write_text(json.dumps(result.records, indent=1))
    return 0


def python_layer(layer: str, start: str) -> list[dict]:
    if not (ROOT / start).is_dir() or not list((ROOT / start).glob("test_*.py")):
        return not_run(layer, start, f"no tests in {start}")
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as fh:
        out = fh.name
    try:
        proc = subprocess.run([sys.executable, str(Path(__file__)), "_pyrun", layer, start, out], cwd=ROOT,
                              timeout=3600, stderr=sys.stderr)
        records = json.loads(Path(out).read_text() or "[]")
    except (ValueError, subprocess.TimeoutExpired) as exc:
        return [rec(layer, start, start, "failed", detail=f"the test run did not complete: {exc}")]
    finally:
        os.unlink(out)
    if proc.returncode and not records:
        return [rec(layer, start, start, "failed", detail=f"test discovery failed (exit {proc.returncode})")]
    return records


# ---- command checks ----------------------------------------------------------------------------------
def cmd_check(layer: str, suite: str, cid: str, argv: list[str], needs: str | None = None, ok_text: str | None = None,
              timeout: int = 600) -> dict:
    if needs and not shutil.which(needs):
        return rec(layer, suite, cid, "not-run", detail=f"{needs} is not installed")
    t0 = time.monotonic()
    try:
        proc = sh(argv, timeout=timeout)
    except subprocess.TimeoutExpired:
        return rec(layer, suite, cid, "failed", time.monotonic() - t0, f"timed out after {timeout}s")
    out = (proc.stdout + proc.stderr).strip()
    ok = proc.returncode == 0 and (ok_text is None or ok_text in out)
    return rec(layer, suite, cid, "passed" if ok else "failed", time.monotonic() - t0, "" if ok else out[-1500:])


def static_extras() -> list[dict]:
    out = [
        cmd_check("static", "claude plugin validate", "validate marketplace (repo root)",
                  ["claude", "plugin", "validate", "."], needs="claude", ok_text="Validation passed"),
        cmd_check("static", "claude plugin validate", "validate plugins/factory",
                  ["claude", "plugin", "validate", "plugins/factory"], needs="claude", ok_text="Validation passed"),
        cmd_check("static", "claude plugin validate", "validate plugins/factory-live",
                  ["claude", "plugin", "validate", "plugins/factory-live"], needs="claude", ok_text="Validation passed"),
        cmd_check("static", "syntax", "python compileall (engine, hooks, scripts)",
                  [sys.executable, "-m", "compileall", "-q", "plugins/factory/lib", "plugins/factory/hooks", "scripts"]),
    ]
    for script in sorted((ROOT / "scripts").glob("*.sh")):
        out.append(cmd_check("static", "syntax", f"bash -n scripts/{script.name}", ["bash", "-n", str(script)]))
    if shutil.which("node"):
        for js in sorted((ROOT / "plugins" / "factory" / "ui" / "js").rglob("*.js")):
            rel = js.relative_to(ROOT).as_posix()
            out.append(cmd_check("static", "syntax", f"node --check {rel}", ["node", "--check", str(js)]))
    else:
        out.append(rec("static", "syntax", "node --check UI modules", "not-run", detail="node is not installed"))
    return out


# ---- JavaScript unit tests (node:test, JUnit reporter) ---------------------------------------------------
def js_layer() -> list[dict]:
    if not shutil.which("node"):
        return not_run("js", "node --test", "node is not installed")
    files = sorted(str(p) for p in (ROOT / "tests" / "js").glob("*.test.js"))
    xml_path = RESULTS / "js.xml"
    sh(["node", "--test", "--test-reporter=junit", f"--test-reporter-destination={xml_path}", *files])
    if not xml_path.exists():
        return [rec("js", "node --test", "node --test", "failed", detail="no JUnit report was written")]
    records = []
    for case in ET.parse(xml_path).getroot().iter("testcase"):
        failure = case.find("failure")
        skipped = case.find("skipped")
        status = "failed" if failure is not None else "skipped" if skipped is not None else "passed"
        detail = (failure.get("message") or failure.text or "") if failure is not None else ""
        records.append(rec("js", "tests/js", f"{case.get('classname') or 'js'}.{case.get('name')}", status,
                           float(case.get("time") or 0), detail))
    return records or [rec("js", "node --test", "node --test", "failed", detail="the report holds no tests")]


# ---- factory-live under claude plugin test, and its type check --------------------------------------------
LINE = re.compile(r"^\((pass|fail|skip|todo)\) (.+?)(?: \[([\d.]+)ms\])?$")


def mod_layer() -> list[dict]:
    records = []
    if not shutil.which("claude"):
        records += not_run("mod", "claude plugin test", "the claude CLI is not installed")
    else:
        t0 = time.monotonic()
        proc = sh(["claude", "plugin", "test", "plugins/factory-live"], timeout=900)
        text = proc.stdout + proc.stderr
        lines = text.splitlines()
        for i, line in enumerate(lines):
            m = LINE.match(line.strip())
            if m and m.group(2) != "the file ran to its end":
                status = {"pass": "passed", "fail": "failed"}.get(m.group(1), "skipped")
                detail = "\n".join(lines[i + 1:i + 6]) if status == "failed" else ""
                records.append(rec("mod", "claude plugin test", m.group(2), status, float(m.group(3) or 0) / 1000, detail))
            elif m and m.group(1) == "fail":
                records.append(rec("mod", "claude plugin test", "module ran to its end", "failed", 0,
                                   "\n".join(lines[i + 1:i + 6])))
        if not records or (proc.returncode and not any(r["status"] == "failed" for r in records)):
            records.append(rec("mod", "claude plugin test", "claude plugin test", "failed", time.monotonic() - t0,
                               text[-1500:]))
    records.append(tsc_check())
    return records


def mod_types() -> Path | None:
    candidates = [os.environ.get("FACTORY_MOD_TYPES", ""),
                  str(ROOT / "plugins/factory-live/.claude-plugin/types/claude-code/index.d.ts")]
    tmp = os.environ.get("TMPDIR", "/tmp")
    candidates += sorted(glob.glob(f"{tmp}/claude-*/bundled-skills/*/*/plugin-authoring/types/claude-code.d.ts")
                         + glob.glob("/tmp/claude-*/bundled-skills/*/*/plugin-authoring/types/claude-code.d.ts"),
                         key=lambda p: os.path.getmtime(p), reverse=True)
    return next((Path(c) for c in candidates if c and Path(c).exists()), None)


def tsc_check() -> dict:
    tsc = shutil.which("tsc") or (str(ROOT / "node_modules/.bin/tsc") if (ROOT / "node_modules/.bin/tsc").exists() else None)
    types = mod_types()
    if not tsc:
        return rec("mod", "tsc", "type-check factory-live (strict)", "not-run", detail="TypeScript (tsc) is not installed")
    if not types:
        return rec("mod", "tsc", "type-check factory-live (strict)", "not-run",
                   detail="this build's mod API declarations were not found (set FACTORY_MOD_TYPES, or load the mod once "
                          "with claude --plugin-dir so Claude Code writes .claude-plugin/types)")
    work = Path(tempfile.mkdtemp(prefix="factory-tsc-"))
    try:
        shutil.copy(types, work / "claude-code.d.ts")
        live = ROOT / "plugins" / "factory-live"
        (work / "tsconfig.json").write_text(json.dumps({
            "compilerOptions": {"target": "es2023", "lib": ["es2023"], "types": [], "module": "esnext",
                                "moduleResolution": "bundler", "strict": True, "noUncheckedIndexedAccess": True,
                                "noEmit": True, "skipLibCheck": True, "jsx": "react", "jsxFactory": "h",
                                "jsxFragmentFactory": "Fragment"},
            "files": [str(work / "claude-code.d.ts")],
            "include": [str(live / "hooks"), str(live / "types"), str(live / "tests")]}))
        return cmd_check("mod", "tsc", "type-check factory-live (strict)", [tsc, "-p", str(work / "tsconfig.json")])
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---- browser UI (Playwright) ----------------------------------------------------------------------------
def ui_layer() -> list[dict]:
    if not shutil.which("npx") or not (ROOT / "node_modules" / "@playwright" / "test").exists():
        return not_run("ui", "playwright", "UI test tooling is not installed (run: npm ci)")
    report = RESULTS / "ui.json"
    report.unlink(missing_ok=True)
    proc = sh(["npx", "playwright", "test", "-c", "tests/ui/playwright.config.js"], timeout=3600)
    if not report.exists():
        tail = (proc.stdout + proc.stderr).strip()[-1500:]
        reason = "browsers missing (npx playwright install chromium)" if "Executable doesn't exist" in tail else tail
        return [rec("ui", "playwright", "playwright", "failed", detail=reason)]
    data = json.loads(report.read_text())
    records = []

    def walk(suite, path):
        for spec in suite.get("specs", []):
            for t in spec.get("tests", []):
                res = (t.get("results") or [{}])[-1]
                status = {"passed": "passed", "skipped": "skipped"}.get(res.get("status"), "failed")
                if t.get("status") == "flaky":
                    status = "failed"
                detail = re.sub(r"\x1b\[[0-9;]*m", "", (res.get("error") or {}).get("message", ""))
                cid = f"[{t.get('projectName')}] {' › '.join(path + [spec['title']])}"
                records.append(rec("ui", spec.get("file", "tests/ui"), cid, status, (res.get("duration") or 0) / 1000, detail))
        for child in suite.get("suites", []):
            walk(child, path + ([child["title"]] if child.get("title") and not child["title"].endswith(".js") else []))

    for s in data.get("suites", []):
        walk(s, [])
    return records or [rec("ui", "playwright", "playwright", "failed", detail="the report holds no tests")]


# ---- paid / machine-local results ------------------------------------------------------------------------
def live_layer() -> list[dict]:
    records = []
    latest = RESULTS / "evals" / "latest.json"
    if latest.exists():
        data = json.loads(latest.read_text())
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(latest.stat().st_mtime))
        for c in data.get("cases", []):
            score = (c.get("aggregates") or {}).get("score")
            ok = isinstance(score, (int, float)) and score >= 0.75
            records.append(rec("live", "claude plugin eval", f"eval {c.get('name')}", "passed" if ok else "failed",
                               detail=f"score {score} (run {when}, cost ${data.get('costUsd')})"))
    else:
        records += not_run("live", "claude plugin eval", "paid model runs: run scripts/eval.sh on your machine; its "
                                                         "results are folded in here")
    smoke = RESULTS / "live-smoke.json"
    if smoke.exists():
        s = json.loads(smoke.read_text())
        records.append(rec("live", "verify-local --live", "live /factory quick job", s.get("status", "failed"),
                           detail=s.get("detail", "")))
    else:
        records += not_run("live", "verify-local --live", "paid model run: scripts/verify-local.sh --live on your machine")
    return records


LAYERS = {
    "static": ("Manifests, skill, agent files (generated, complete, safe), contracts, guard policy, UI assets; plugin "
               "validation by the claude CLI; syntax of every script and UI module",
               lambda: python_layer("static", "tests/static") + static_extras(), "scripts/test.sh static"),
    "unit": ("Engine building blocks, hooks as subprocesses (guard table, delivery, contract check, stop guard), "
             "console server security and API", lambda: python_layer("unit", "tests/unit"), "scripts/test.sh unit"),
    "integration": ("Every workflow end to end with scripted agents in temp git repos: failures, limits, resume, "
                    "plans, merges, messages, repo customisation", lambda: python_layer("integration", "tests/integration"),
                    "scripts/test.sh integration"),
    "e2e": ("factoryctl CLI driven as the skill drives it, then hooks and console API on the result",
            lambda: python_layer("e2e", "tests/e2e"), "scripts/test.sh e2e"),
    "install": ("Installer and uninstaller against throwaway Claude homes; discovery by Claude Code",
                lambda: python_layer("install", "tests/install"), "scripts/test.sh install"),
    "evals": ("Agent eval suite offline: schema, scaffolds, checkers, graders against golden outputs",
              lambda: python_layer("evals", "tests/evals"), "scripts/test.sh evals"),
    "js": ("Console logic in Node: markdown escaping, formatting, diff parsing, routing, commands",
           js_layer, "scripts/test.sh js"),
    "mod": ("factory-live mod under the engine's own test kit on four surfaces, strict type check",
            mod_layer, "scripts/test.sh mod"),
    "ui": ("Console in Chromium: journeys, axe (WCAG 2.1 AA) in both themes, three widths, keyboard, XSS, error states",
           ui_layer, "scripts/test.sh ui"),
    "live": ("Paid, on your machine: agent-quality evals and one live /factory job", live_layer, "scripts/eval.sh, "
             "scripts/verify-local.sh --live"),
}


def run_layers(names: list[str]) -> int:
    RESULTS.mkdir(exist_ok=True)
    failed = 0
    for name in names:
        about, fn, _cmd = LAYERS[name]
        sys.stderr.write(f"== {name}: ")
        t0 = time.monotonic()
        records = fn()
        took = time.monotonic() - t0
        counts = {s: sum(r["status"] == s for r in records) for s in ("passed", "failed", "skipped", "not-run")}
        (RESULTS / f"layer-{name}.json").write_text(json.dumps({"layer": name, "about": about, "seconds": round(took, 1),
                                                               "counts": counts, "records": records}, indent=1))
        sys.stderr.write(f"\n   {counts['passed']} passed, {counts['failed']} failed, {counts['skipped']} skipped, "
                         f"{counts['not-run']} not run ({took:.0f}s)\n")
        for r in records:
            if r["status"] == "failed":
                sys.stderr.write(f"   FAILED {r['id']}\n      {r['detail'][:300]}\n")
        failed += counts["failed"]
    return 1 if failed else 0


# ---- the matrix --------------------------------------------------------------------------------------------
def load_layers() -> dict[str, dict]:
    out = {}
    for name in LAYERS:
        p = RESULTS / f"layer-{name}.json"
        if p.exists():
            out[name] = json.loads(p.read_text())
    return out


def tool_version(argv: list[str]) -> str:
    try:
        p = sh(argv, timeout=30)
        return ((p.stdout or p.stderr).strip().splitlines() or ["?"])[0]
    except (OSError, subprocess.SubprocessError):
        return "not installed"


def components() -> list[tuple[str, str, str, list[tuple[str, str]]]]:
    """(area, component, expected behaviour, [(layer, regex over check ids)]). Built from the code, so a new
    agent, workflow, command, hook or view appears here even before a test covers it."""
    sys.path.insert(0, str(ROOT / "plugins" / "factory" / "lib"))
    from factory_core import registry
    from factory_core.cli import build_parser

    rows = []
    for wid in registry.workflow_ids():
        wf = registry.load_workflow(wid)
        rows.append(("Workflows", f"`{wid}`", wf.get("description", wf.get("title", "")),
                     [("integration", rf"\(workflow='{wid}'\)|test_.*{wid.replace('-', '_')}"),
                      ("unit", rf"Workflows.*\(workflow='{wid}'\)")]))
    roster = registry.roster()
    agents = roster["agents"] if isinstance(roster, dict) else roster
    agents = list(agents.values()) if isinstance(agents, dict) else agents
    eval_agents = {}
    for prompt in (ROOT / "plugins" / "factory" / "evals").rglob("prompt.md"):
        m = re.search(r"factory:([\w-]+)", prompt.read_text())
        if m:
            eval_agents.setdefault(m.group(1), []).append(prompt.parent.name)
    for a in agents:
        name = a["name"]
        rules = [("static", rf"agent='{name}'")]
        expected = f"{a.get('summary', '')} Static: contract file complete, generated, safe."
        if name in eval_agents:
            cases = "|".join(re.escape(c) for c in eval_agents[name])
            rules += [("evals", rf"case='[\w/]*({cases})'"), ("live", rf"eval .*({cases})|claude plugin eval")]
            expected += f" Eval: {', '.join(eval_agents[name])}."
        rows.append(("Agents", f"`{name}`", expected, rules))
    sub = next(x for x in build_parser()._actions if getattr(x, "dest", None) == "cmd")
    loop = {"init", "new", "next", "done", "approve", "tell", "status"}  # the skill's loop, run whole in the CLI flow
    for cmd, parser in sub.choices.items():
        help_text = next((c.help for c in sub._choices_actions if c.dest == cmd), "") or parser.description or ""
        rules = [("e2e", rf"test_cmd_{cmd.replace('-', '_')}\b")]
        if cmd in loop:
            rules.append(("e2e", r"through_the_cli"))
        rows.append(("factoryctl", f"`{cmd}`", help_text, rules))
    hooks = [
        ("PreToolUse guard", "Denies catastrophic and asks on risky commands for factory agents; reviewers write only "
                             "under .factory/jobs", [("unit", r"Guard|WriteGuard"), ("e2e", r"through_the_cli")]),
        ("PostToolUse spawn record + message delivery", "Maps agent ids to steps; hands queued user messages to the "
                                                        "running agent", [("unit", r"SpawnAndMessages")]),
        ("SubagentStop contract check", "Blocks once with the problems when an output breaks its contract; records "
                                        "usage", [("unit", r"SubagentStop")]),
        ("Stop continuation guard", "Keeps the orchestrator from stopping while next has work",
         [("unit", r"StopGuard"), ("e2e", r"through_the_cli")]),
        ("SessionStart", "One line when jobs need the person; exports FACTORYCTL",
         [("unit", r"SessionStart"), ("e2e", r"through_the_cli")]),
        ("Hook performance", "Every hook exits in < 250 ms p95 outside factory context", [("unit", r"RunScript")]),
    ]
    rows += [("Hooks", f"{n}", e, r) for n, e, r in hooks]
    sys.path.insert(0, str(ROOT / "tests"))
    views = [("Command Center", r"home|needs-you|composer|live updates"), ("Job · Overview", r"job-overview|escalated|legacy"),
             ("Job · Crew + agent chat", r"crew|orchestrator"), ("Job · Flow (DAG)", r"flow"),
             ("Job · Timeline", r"timeline"), ("Job · Artifacts", r"artifacts"), ("Job · Changes", r"changes"),
             ("Job · Quality", r"quality"), ("Catalog", r"catalog"), ("Settings", r"settings"),
             ("Errors, offline, not found, XSS", r"untrusted|server unreachable|unknown job|CSP|token|loading")]
    for view, pattern in views:
        rows.append(("Console", view, "Renders real state; accessible (axe, keyboard); no horizontal scroll at 1440/900/390",
                     [("ui", pattern)]))
    rows.append(("Console", "Console logic", "Escaped markdown and safe links, formatting, diff parsing, routes, commands",
                 [("js", r".")]))
    rows.append(("Console", "HTTP API + security", "Host/Origin/token checks, CSP, confined paths, ETag, size limits",
                 [("unit", r"ConsoleServer"), ("e2e", r"through_the_cli")]))
    for feature, pattern in [("Status line + notifications", r"session >"), ("Band above the prompt", r"band"),
                             ("/factory-board pane, Watch, Message", r"board >"), ("Pure rules (model.ts)", r"model >"),
                             ("Type check", r"type-check")]:
        rows.append(("factory-live", feature, "Under claude plugin test on terminal, desktop, vscode and mobile",
                     [("mod", pattern)]))
    for action, pattern in [("install / upgrade / idempotent", r"\.Install\."), ("uninstall / restore", r"\.Uninstall\."),
                            ("discovery by Claude Code", r"\.Discovery\.")]:
        rows.append(("Installer", action, "Backs up what it replaces, never touches foreign files",
                     [("install", pattern)]))
    rows.append(("Live", "One real /factory job", "A quick job runs to merged or needs-human on a fixture repo",
                 [("live", r"verify-local|live /factory")]))
    return rows


def status_of(records: list[dict]) -> str:
    if not records:
        return "NOT COVERED"
    if any(r["status"] == "failed" for r in records):
        return "FAIL"
    if any(r["status"] == "passed" for r in records):
        return "PASS"
    if all(r["status"] == "not-run" for r in records):
        return "NOT RUN"
    return "SKIPPED"


def build_matrix() -> Path:
    layers = load_layers()
    by_layer = {name: data["records"] for name, data in layers.items()}
    commit = tool_version(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"])
    lines = [
        "# Test matrix",
        "",
        "Generated by `scripts/test.sh --matrix` from the results in `test-results/` (do not edit by hand).",
        f"Commit `{commit}` · {time.strftime('%Y-%m-%d %H:%M %Z')} · {platform.system()} {platform.machine()} · "
        f"Python {platform.python_version()} · {tool_version(['node', '--version'])} (Node) · "
        f"{tool_version(['claude', '--version'])}",
        "",
        "Statuses are what happened in this run: PASS (ran and passed), FAIL, SKIPPED (the test skipped itself and "
        "says why), NOT RUN (could not run here, reason given), NOT COVERED (no test touches it yet).",
        "",
        "## Layers",
        "",
        "| Layer | What it checks | Command | Passed | Failed | Skipped | Not run | Time |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for name, (about, _fn, cmd) in LAYERS.items():
        d = layers.get(name)
        if d:
            c = d["counts"]
            lines.append(f"| {name} | {about} | `{cmd}` | {c['passed']} | {c['failed']} | {c['skipped']} | "
                         f"{c['not-run']} | {d['seconds']:.0f}s |")
        else:
            lines.append(f"| {name} | {about} | `{cmd}` | – | – | – | – | not run in this pass |")
    lines += ["", "## Components", "",
              "Every agent, workflow, command, hook, view, mod feature and installer action, from the code, with the "
              "tests that cover it. For agents, PASS here means the contract file is complete and the eval case (if "
              "any) is sound offline; how well the model does the work is the live layer.", ""]
    area = None
    for comp_area, comp, expected, rules in components():
        if comp_area != area:
            area = comp_area
            lines += ["", f"### {area}", "", "| Component | Expected behaviour | Evidence (layer: checks) | Status |",
                      "|---|---|---|---|"]
        evidence, matched = [], []
        for layer, pattern in rules:
            hits = [r for r in by_layer.get(layer, []) if re.search(pattern, r["id"], re.I)]
            if hits:
                ok = sum(r["status"] == "passed" for r in hits)
                bad = sum(r["status"] == "failed" for r in hits)
                nr = sum(r["status"] in ("not-run", "skipped") for r in hits)
                evidence.append(f"{layer}: {ok} passed" + (f", {bad} failed" if bad else "") +
                                (f", {nr} not run" if nr else ""))
                matched += hits
        lines.append(f"| {comp} | {expected.replace('|', '/')} | {'; '.join(evidence) or '–'} | **{status_of(matched)}** |")
    pending = [r for recs in by_layer.values() for r in recs if r["status"] in ("not-run", "skipped")]
    failed = [r for recs in by_layer.values() for r in recs if r["status"] == "failed"]
    lines += ["", "## Not run or skipped, and why", ""]
    lines += [f"- **{r['layer']}** · {r['id']}: {r['detail'] or 'skipped'}" for r in pending] or ["- Nothing."]
    lines += ["", "## Failures", ""]
    lines += [f"- **{r['layer']}** · {r['id']}: {r['detail'][:400]}" for r in failed] or ["- None."]
    lines += ["", "## Limits of what this run proves", "",
              "- Model behaviour is measured only by the paid layer (`scripts/eval.sh`, `scripts/verify-local.sh "
              "--live`); every other layer uses scripted agents that follow the contracts exactly.",
              "- The mod runs under Claude Code's plugin test kit, not in an interactive terminal.",
              "- Browser tests use Chromium only.", ""]
    MATRIX.write_text("\n".join(lines))
    return MATRIX


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["_pyrun"]:
        return pyrun(*argv[1:4])
    if argv[:1] == ["matrix"]:
        print(build_matrix())
        return 0
    parser = argparse.ArgumentParser(prog="test.sh", description=__doc__.split("\n\n")[0])
    parser.add_argument("layers", nargs="*", choices=list(LAYERS), metavar="layer",
                        help=f"layers to run (default: all): {', '.join(LAYERS)}")
    parser.add_argument("--matrix", action="store_true", help="write docs/TEST-MATRIX.md from this run's results")
    args = parser.parse_args(argv)
    names = args.layers or list(LAYERS)
    code = run_layers(names)
    if args.matrix:
        print(f"wrote {build_matrix()}")
    return code


if __name__ == "__main__":
    sys.exit(main())
