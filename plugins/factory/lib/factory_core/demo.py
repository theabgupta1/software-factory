"""Demo data for `factoryctl serve --demo` and the console's UI tests.

Builds a throwaway repo and drives the real engine with scripted agents, so every job, event, verdict and
artifact the console shows was produced by the same code paths as a real run. Only the clock, the agents'
words and the token numbers are scripted; the console labels the whole page as demo data.
"""

from __future__ import annotations

import datetime as dt
import json
import random
import shutil
import subprocess
from pathlib import Path

from . import prompts, util
from .engine import Engine
from .paths import PLUGIN_ROOT, RepoPaths
from .util import append_jsonl

UPSTREAM_JOBS = PLUGIN_ROOT.parents[1] / "upstream" / "factory" / "jobs"


class Clock:
    def __init__(self, start: dt.datetime):
        self.t = start

    def __call__(self) -> dt.datetime:
        self.t += dt.timedelta(milliseconds=350)
        return self.t

    def advance(self, minutes: float) -> None:
        self.t += dt.timedelta(minutes=minutes)


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


FILES = {
    "README.md": "# Acme Shop\n\nA small storefront: Python API (`api/`), React web app (`web/`), SQL models (`data/`).\n",
    "AGENTS.md": (
        "# AGENTS.md\n\n## Setup\n- `make setup` installs Python and Node dependencies.\n\n## Dev server\n"
        "- `make dev PORT=<port>` serves the web app with the API.\n\n## Validation\n- `make test` (pytest + vitest)\n"
        "- `make lint` (ruff + eslint), `make typecheck`\n"
    ),
    "api/app.py": "from flask import Flask\n\napp = Flask(__name__)\n\n\n@app.post('/signup')\ndef signup():\n    return {}\n",
    "api/limits.py": "\"\"\"Token-bucket rate limits.\"\"\"\n\n\ndef allow(key: str) -> bool:\n    return True\n",
    "web/src/pages/Signup.tsx": "export function Signup() {\n  return <form />\n}\n",
    "web/src/theme.css": ":root { --accent: #6d5dfc; }\n",
    "data/sql/orders.sql": "select * from raw.orders\n",
    "scripts/prerender.js": "// prerender blog pages\n",
}

SPEC = """STATUS: READY

# {title}

> Feature: {request}

## Context
The signup endpoint lives in `api/app.py` (`POST /signup`). `api/limits.py` already exposes a token-bucket
`allow(key)` helper that nothing calls yet. The form is `web/src/pages/Signup.tsx`.

## Requirements
1. R1 — `POST /signup` rejects more than 5 attempts per IP per 10 minutes with HTTP 429 and a `Retry-After` header.
2. R2 — The limit uses `api/limits.allow`, keyed by client IP (respecting `X-Forwarded-For` only from the proxy).
3. R3 — The form shows a clear, accessible message when the server answers 429.

## Acceptance criteria
1. AC1 — `pytest api/tests/test_signup_limit.py` passes: the 6th request within 10 minutes returns 429.
2. AC2 — The 429 response carries `Retry-After` in seconds.
3. AC3 — On `/signup`, after a 429 the form shows "Too many attempts. Try again in N minutes." in a `role="alert"` region.
4. AC4 — `make lint` and `make test` pass.

## Out of scope
- CAPTCHA, account lockout, email verification.

## Assumptions
- 5 attempts / 10 minutes is a sensible default; it is a config constant, not a product decision.

## Open questions
- None.
"""

BUILD = """STATUS: READY

# Build: {title}

## Summary
Wired `api/limits.allow` into `POST /signup` (429 + `Retry-After`), added the alert to the signup form, and
covered both with tests. Files: `api/app.py`, `api/limits.py`, `api/tests/test_signup_limit.py`,
`web/src/pages/Signup.tsx`.

UI impact: yes

## Acceptance criteria
1. [x] AC1: `pytest api/tests/test_signup_limit.py` — 4 passed.
2. [x] AC2: the test asserts `Retry-After: 600` on the 6th request.
3. [x] AC3: dev server on port {port}; after six submits the alert reads "Too many attempts. Try again in 10 minutes." (role="alert").
4. [x] AC4: `make lint` and `make test` pass.

## Validation
- `pytest api/tests -q`: pass (31 passed)
- `npx vitest run web/src/pages`: pass (12 passed)
- `make lint`: pass
- `make typecheck`: pass

## Log
### Round 1
Implemented R1–R3 across plan tasks T1–T3 and integrated them.
{extra_log}"""

REVIEW_PASS = """VERDICT: PASS

## Checks
`pytest api/tests -q`: pass
`make lint`: pass

## Findings
None.

## Notes
- {note}
"""

REVIEW_CHANGES = """VERDICT: CHANGES

## Checks
`pytest api/tests -q`: pass

## Findings
1. api/app.py:18 The limiter keys on `request.headers["X-Forwarded-For"]` from any client, so an attacker can
   rotate the header to bypass the limit. Trust the header only when `request.remote_addr` is the proxy (high).
2. api/limits.py:22 Buckets are never evicted; a flood of unique keys grows memory without bound. Cap the map
   or use the existing TTL cache in `api/cache.py` (medium).

## Notes
- Consider logging 429s at INFO with the key hashed.
"""

DECISION_ESCALATE = """ESCALATE

## Why
The change is correct and all four reviews pass, but it edits `scripts/prerender.js`, which runs in the deploy
pipeline. Deploy and build-script changes always go to a human.

## Acceptance criteria
1. AC1: met — `npm run build` succeeds and the snapshot contains the reading time.
2. AC2: met — `grep -c "min read" dist/blog/*/index.html` is non-zero for every post.

## For the human
Confirm that adding `readingTime.js` to the prerender cache key is acceptable: it re-renders every post once on
the next deploy. Files: `scripts/prerender.js` (one line).
"""

DATA_DESIGN = """STATUS: READY

# Daily orders pipeline

## Model
`analytics.fct_orders` (grain: one row per order line), partitioned by `order_date`, clustered by
`customer_id`. `analytics.dim_customer` is SCD2 on email and segment.

## Contracts
- Source `raw.orders` is append-only; `updated_at` is monotonic per `order_id`.
- `fct_orders` guarantees uniqueness on (`order_id`, `line_no`) and freshness < 26 h.

## Incremental strategy
Watermark on `updated_at` with a 3-day lookback for late-arriving updates; `MERGE` keyed on
(`order_id`, `line_no`) so re-runs are idempotent. Backfills run per partition with the same SQL.

## Lineage and ownership
`raw.orders` (platform) → `stg_orders` → `fct_orders` (analytics). Owner: data team; SLA 07:00 UTC.

## Rollback
Partitions are rewritten atomically; to roll back, re-run the previous model version for the affected dates.
"""

RESEARCH = """STATUS: READY

# Vector store options for product search

## Question
Which vector store should power semantic product search for ~2M items with metadata filters?

## Findings
1. pgvector (HNSW) handles 2M × 768-dim vectors on a single Postgres instance with filtered queries; it keeps
   search in the existing database (source: https://github.com/pgvector/pgvector, confidence: high).
2. A managed vector database adds a second system to operate but scales horizontally (confidence: medium).
3. The repo already uses Postgres 16 (`api/db.py`) and has no queue for async indexing (source: context.md).

## Facts vs assumptions
- Fact: Postgres 16 is in use. Assumption: query volume stays under 200 QPS this year.

## Options
| Option | Ops cost | Filtering | Fit |
|---|---|---|---|
| pgvector | low | SQL | strong |
| Managed vector DB | medium | native | good |

## Recommendation
Start with pgvector behind a small `search` module so the store can be swapped later.

## Sources
- https://github.com/pgvector/pgvector
- `api/db.py`, `context.md`
"""

CONTEXT = """STATUS: READY

# Codebase context

## Architecture
Flask API in `api/`, React (Vite) app in `web/`, SQL models in `data/`. Postgres 16 via `api/db.py`.

## Conventions
Python: ruff + pytest, type hints on public functions. Web: TypeScript strict, vitest, CSS variables in
`web/src/theme.css`.

## Commands
`make setup`, `make dev PORT=…`, `make test`, `make lint`.

## Hotspots and risks
`api/app.py` mixes routing and business logic; there is no search abstraction yet.
"""

DIAGNOSIS_TRANSCRIPT = [
    ("text", "Reproducing the 500 on checkout. Reading AGENTS.md and the checkout route first."),
    ("tool", "Read", {"file_path": "api/checkout.py"}),
    ("result", "def checkout(cart):\n    total = sum(i.price * i.qty for i in cart.items)\n    ..."),
    ("tool", "Bash", {"command": "cd <worktree> && pytest api/tests/test_checkout.py -q"}),
    ("result", "1 failed: test_checkout_with_coupon — TypeError: unsupported operand type(s) for -: 'Decimal' and 'float'"),
    ("text", "The coupon discount is a float while prices are Decimal. Checking where the coupon is loaded."),
    ("tool", "Grep", {"pattern": "discount", "path": "api"}),
]

SECURITY_TRANSCRIPT = [
    ("text", "Round 2: re-checking my round-1 findings against the new commits."),
    ("tool", "Bash", {"command": "cd <worktree> && git log --oneline main..HEAD"}),
    ("result", "9f1c2ab Trust X-Forwarded-For only from the proxy\n4d2e8aa Evict idle rate-limit buckets\n…"),
    ("tool", "Read", {"file_path": "api/app.py"}),
    ("result", "client_ip = request.remote_addr if request.remote_addr not in TRUSTED_PROXIES else …"),
    ("text", "Finding 1 is fixed: the header is only trusted from TRUSTED_PROXIES. Checking the eviction cap next."),
]

UX_TRANSCRIPT = [
    ("text", "Starting the dev server on my port and opening /signup at phone width."),
    ("tool", "Bash", {"command": "cd <worktree> && make dev PORT=5041 &"}),
    ("result", "Serving on http://localhost:5041"),
    ("tool", "Bash", {"command": "agent-browser --session 004-ux open http://localhost:5041/signup --width 390"}),
    ("result", "Opened. Screenshot saved."),
]


class DemoAgents:
    def __init__(self, clock: Clock, transcripts: Path):
        self.clock = clock
        self.transcripts = transcripts
        self.rng = random.Random(7)
        self.n = 0

    def write(self, entry: dict, text_by_step: dict | None = None, minutes: float = 6.0) -> None:
        info = prompts.parse_header(entry["prompt"])
        out = Path(info["output"])
        out.parent.mkdir(parents=True, exist_ok=True)
        wt = Path(info["worktree"])
        step = info["step"]
        text = (text_by_step or {}).get(step)
        self.clock.advance(minutes)
        if text is None:
            text = _generic(step, out.name, info)
        if step.startswith(("build.", "task/")) or step == "characterize":
            rel = {"task/T1": "api/limits.py", "task/T2": "web/src/pages/Signup.tsx",
                   "task/T3": "api/tests/test_signup_limit.py"}.get(step, "api/app.py")
            p = wt / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as fh:
                fh.write(f"# {step}\n")
            _git(["add", rel], wt)
            msg = f"{step}: {info['job']}"
            if step.startswith("task/"):
                msg += f"\n\nFactory-Task: {step.split('/', 1)[1]}"
            _git(["commit", "-qm", msg], wt)
        out.write_text(text, encoding="utf-8")

    def spawn(self, job_dir: Path, entry: dict, agent_id: str, script: list | None = None,
              finished: bool = True) -> None:
        """Telemetry the hooks would record, plus a readable transcript."""
        info = prompts.parse_header(entry["prompt"])
        transcript = self.transcripts / f"agent-{agent_id}.jsonl"
        lines = [{"type": "user", "timestamp": util.now_iso(), "message": {"role": "user", "content": entry["prompt"]}}]
        for i, item in enumerate(script or [("text", f"Working on {info['step']}."),
                                             ("tool", "Read", {"file_path": "AGENTS.md"}),
                                             ("result", "# AGENTS.md …"),
                                             ("text", "Done; writing my output file.")]):
            ts = util.now_iso()
            if item[0] == "text":
                lines.append({"type": "assistant", "timestamp": ts, "message": {
                    "id": f"m{agent_id}{i}", "model": "claude-demo", "content": [{"type": "text", "text": item[1]}],
                    "usage": {"input_tokens": 1200, "output_tokens": 180}}})
            elif item[0] == "tool":
                lines.append({"type": "assistant", "timestamp": ts, "message": {
                    "id": f"m{agent_id}{i}", "model": "claude-demo",
                    "content": [{"type": "tool_use", "id": f"t{agent_id}{i}", "name": item[1], "input": item[2]}],
                    "usage": {"input_tokens": 900, "output_tokens": 60}}})
            else:
                lines.append({"type": "user", "timestamp": ts, "message": {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": f"t{agent_id}{i - 1}", "content": item[1]}]}})
        transcript.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
        tele = job_dir / "telemetry.jsonl"
        append_jsonl(tele, {"type": "agent.spawned", "ts": lines[0]["timestamp"], "agent_id": agent_id,
                            "job": info["job"], "step": info["step"], "attempt": info["attempt"],
                            "name": entry.get("name"), "subagent_type": entry["subagent_type"],
                            "model": {"opus": "claude-opus", "sonnet": "claude-sonnet"}.get(
                                _model_alias(entry), "claude-sonnet"), "transcript": str(transcript)})
        if finished:
            total_in = self.rng.randint(18_000, 90_000)
            total_out = self.rng.randint(2_000, 14_000)
            append_jsonl(tele, {"type": "agent.stopped", "ts": util.now_iso(), "agent_id": agent_id,
                                "job": info["job"], "step": info["step"], "attempt": info["attempt"],
                                "transcript": str(transcript),
                                "usage": {"input": total_in, "output": total_out, "cache_read": total_in * 3,
                                          "cache_write": total_in // 4,
                                          "total": total_in + total_out + total_in * 3 + total_in // 4},
                                "models": ["claude-demo"], "tool_uses": self.rng.randint(6, 40), "problems": []})


def _model_alias(entry: dict) -> str:
    from . import registry

    name = entry["subagent_type"].split(":", 1)[-1]
    return registry.roster()["agents"].get(name, {}).get("model", "sonnet")


def _generic(step: str, name: str, info: dict) -> str:
    title = info["job"].split("-", 1)[1].replace("-", " ").capitalize()
    if step.startswith("review."):
        return REVIEW_PASS.format(note="Matches the surrounding code; no blocking issues.")
    if step.startswith("approve"):
        return "APPROVE\n\n## Why\nAll criteria met with evidence; reviews pass; merges cleanly.\n\n## Acceptance criteria\n1. AC1: met\n"
    if step == "spec":
        return SPEC.format(title=title, request=title)
    if step.startswith("task/"):
        return f"STATUS: READY\n\n# {step}\n\n## Changes\n- Implemented {step}.\n\n## Validation\n- `make test`: pass\n"
    if step.startswith("build."):
        return BUILD.format(title=title, port=5040, extra_log="")
    if name == "design.md":
        return ("STATUS: READY\n\n# Design\n\n## Design\nUse the existing token bucket in `api/limits.py`; key by trusted "
                "client IP; surface 429 in the form.\n\n## Failure modes\nLimiter state is per-process; acceptable "
                "for one instance.\n")
    if name == "research.md":
        return RESEARCH
    if name == "context.md":
        return CONTEXT
    return f"STATUS: READY\n\n# {title}\n\n## Summary\nDone.\n"


def build(root: Path) -> RepoPaths:
    """Create the demo repo under `root` and return its paths."""
    repo = root / "acme-shop"
    repo.mkdir(parents=True)
    _git(["init", "-q", "-b", "main"], repo)
    _git(["config", "user.email", "demo@factory.local"], repo)
    _git(["config", "user.name", "Factory Demo"], repo)
    _git(["config", "commit.gpgsign", "false"], repo)
    for rel, text in FILES.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-qm", "Initial storefront"], repo)
    if UPSTREAM_JOBS.exists():  # the original factory's example jobs, shown as read-only v1 jobs
        shutil.copytree(UPSTREAM_JOBS, repo / "factory" / "jobs")
    transcripts = root / "transcripts"
    transcripts.mkdir()
    paths = RepoPaths(repo.resolve())
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    clock = Clock(now - dt.timedelta(hours=9))
    util.set_clock(clock)
    try:
        _jobs(paths, clock, DemoAgents(clock, transcripts))
    finally:
        util.set_clock(None)
    return paths


def _drive(eng: Engine, job_id: str, agents: DemoAgents, texts: dict, stop_at: str | None = None,
           minutes: dict | None = None, hold: set | None = None) -> dict:
    """Run like the orchestrator until `stop_at` is issued or the job settles; steps in `hold` stay running."""
    action = eng.next(job_id)
    job_dir = eng.store.job_dir(job_id)
    for _ in range(80):
        kind = action["kind"]
        if kind in ("human", "done", "wait"):
            return action
        if kind == "inline":
            agents.clock.advance(2)
            action = eng.done(job_id, action["step"], action["attempt"])["next"]
            continue
        entries = action["entries"] if kind == "spawn" else [action["fallback"]]
        if any(e["step"] == stop_at for e in entries):
            for e in entries:
                agents.n += 1
                aid = f"demo{agents.n:03d}"
                running = e["step"] in (hold or set())
                if not running:
                    agents.write(e, texts, (minutes or {}).get(e["step"], 5))
                agents.spawn(job_dir, e, aid, script=texts.get(f"script:{e['step']}"), finished=not running)
                if not running:
                    eng.done(job_id, e["step"], e["attempt"], agent_id=aid)
            return eng.next(job_id)
        result = None
        for e in entries:
            agents.n += 1
            aid = f"demo{agents.n:03d}"
            agents.write(e, texts, (minutes or {}).get(e["step"], 5))
            agents.spawn(job_dir, e, aid, script=texts.get(f"script:{e['step']}"))
        for e in entries:
            result = eng.done(job_id, e["step"], e["attempt"], agent_id=f"demo{agents.n - len(entries) + 1 + entries.index(e):03d}")
        action = result["next"]
        if action["kind"] == "spawn" and stop_at is None:
            continue
    return action


def _jobs(paths: RepoPaths, clock: Clock, agents: DemoAgents) -> None:
    eng = Engine(paths, session="demo-session")

    # 003 — merged small feature
    job = eng.new_job("Add a dark mode toggle to the header", workflow="feature", size="S",
                      why="Small, self-contained UI change.")
    _drive(eng, job["id"], agents, {})
    clock.advance(40)

    # 004 — planned feature mid review round 2, with live agents and a message in flight
    plan = {"tasks": [
        {"id": "T1", "agent": "backend-engineer", "title": "Rate-limit POST /signup", "owns": ["api/**"],
         "acceptance": ["AC1", "AC2"]},
        {"id": "T2", "agent": "frontend-engineer", "title": "Show the 429 alert on the form",
         "owns": ["web/src/pages/**"], "depends_on": ["T1"], "acceptance": ["AC3"]},
        {"id": "T3", "agent": "test-engineer", "title": "Tests for the limit and the alert",
         "owns": ["api/tests/**"], "depends_on": ["T1", "T2"], "acceptance": ["AC1", "AC4"]}]}
    job = eng.new_job("Rate limit the signup form: max 5 attempts per IP per 10 minutes", workflow="feature",
                      size="M", why="Touches API, UI and tests; worth a short plan.")
    jd = eng.store.job_dir(job["id"])

    def design_writer(entry):
        (jd / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")

    texts = {"spec": SPEC.format(title="Rate limit the signup form", request=job["request"]),
             "build.r1/builder": BUILD.format(title="Rate limit the signup form", port=5040, extra_log=""),
             "review.r1/security": REVIEW_CHANGES,
             "review.r1/code": REVIEW_PASS.format(note="Nice reuse of `api/limits.allow`."),
             "review.r1/ui": REVIEW_PASS.format(note="The alert uses the existing danger token."),
             "review.r1/ux": REVIEW_PASS.format(note="Copy is clear; focus moves to the alert."),
             "build.r2/builder": BUILD.format(title="Rate limit the signup form", port=5040, extra_log=(
                 "### Round 2\n- security #1: trust X-Forwarded-For only from TRUSTED_PROXIES (9f1c2ab).\n"
                 "- security #2: cap buckets at 10k with idle eviction (4d2e8aa).\n")),
             "review.r2/code": REVIEW_PASS.format(note="Eviction is covered by a test."),
             "review.r2/ui": REVIEW_PASS.format(note="No visual change since round 1."),
             "script:review.r2/security": SECURITY_TRANSCRIPT,
             "script:review.r2/ux": UX_TRANSCRIPT}
    orig_write = agents.write

    def write(entry, t=None, minutes=6.0):
        orig_write(entry, t, minutes)
        if prompts.parse_header(entry["prompt"])["step"] == "plan":
            design_writer(entry)

    agents.write = write
    eng.tell(job["id"], "Please reuse the TTL cache in api/cache.py for bucket eviction.", agent="builder")
    _drive(eng, job["id"], agents, texts, stop_at="review.r2/code",
           minutes={"build.r1/builder": 22, "build.r2/builder": 14, "task/T1": 16, "task/T2": 12, "task/T3": 9},
           hold={"review.r2/security", "review.r2/ux"})
    agents.write = orig_write
    eng.tell(job["id"], "Also confirm the 429 copy is announced by screen readers.", agent="ux-reviewer")
    clock.advance(3)

    # 005 — data pipeline awaiting plan approval
    job = eng.new_job("Build a daily incremental orders pipeline into analytics.fct_orders with quality checks",
                      workflow="data", size="L", why="New pipeline with a data contract; large, so the plan gates.")
    jd5 = eng.store.job_dir(job["id"])
    data_plan = {"tasks": [
        {"id": "T1", "agent": "sql-engineer", "title": "stg_orders and fct_orders models with MERGE",
         "owns": ["data/sql/**"], "acceptance": ["AC1", "AC2"]},
        {"id": "T2", "agent": "data-engineer", "title": "Daily schedule, watermark and backfill command",
         "owns": ["data/pipelines/**"], "depends_on": ["T1"], "acceptance": ["AC3"]},
        {"id": "T3", "agent": "data-quality-engineer", "title": "Uniqueness, freshness and reconciliation checks",
         "owns": ["data/checks/**"], "depends_on": ["T1"], "acceptance": ["AC4"]},
        {"id": "T4", "agent": "docs-engineer", "title": "Runbook for failures and backfills",
         "owns": ["docs/runbooks/**"], "depends_on": ["T2", "T3"]}]}

    def write5(entry, t=None, minutes=6.0):
        orig_write(entry, t, minutes)
        if prompts.parse_header(entry["prompt"])["step"] == "data-design":
            (jd5 / "plan.json").write_text(json.dumps(data_plan, indent=2), encoding="utf-8")

    agents.write = write5
    _drive(eng, job["id"], agents, {"data-design": DATA_DESIGN,
                                    "spec": SPEC.format(title="Daily orders pipeline", request=job["request"])},
           minutes={"data-design": 18})
    agents.write = orig_write
    clock.advance(25)

    # 006 — escalated by the approver
    job = eng.new_job("Show estimated reading time on blog posts (prerendered pages too)", workflow="feature",
                      size="S", why="Small feature, but it touches the prerender script.")
    _drive(eng, job["id"], agents, {"approve": DECISION_ESCALATE})
    clock.advance(30)

    # 007 — research delivered
    job = eng.new_job("Which vector store should we use for semantic product search?", workflow="research",
                      size="M", why="A technical decision that needs sources, not code.", params={"repo": "true"})
    _drive(eng, job["id"], agents, {"investigate/research": RESEARCH, "investigate/context": CONTEXT},
           minutes={"investigate/research": 24})
    clock.advance(12)

    # 008 — bug fix, debugger still investigating
    job = eng.new_job("Checkout returns 500 when a coupon is applied", workflow="fix", size="S",
                      why="A defect with a clear symptom; diagnose before fixing.")
    _drive(eng, job["id"], agents, {"script:diagnose": DIAGNOSIS_TRANSCRIPT}, stop_at="diagnose", hold={"diagnose"})
