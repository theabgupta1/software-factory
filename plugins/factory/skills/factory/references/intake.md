# Intake: choosing a workflow and a size

Read the request, glance at the repository if you need to (top-level layout, AGENTS.md/CLAUDE.md), then pick.
State the choice in one line: `feature, size M — touches the API, the form and tests; worth a short plan.`

## Workflow

| If the request is mainly… | Workflow | Notes |
|---|---|---|
| New or changed product behaviour, an API, a page, a full-stack feature | `feature` | The default |
| A defect, failing test, regression, incident ("X returns 500", "Y broke after deploy") | `fix` | Diagnose before fixing |
| A small, unambiguous change one specialist can do (a query, a config tweak, a doc fix) | `quick` | Pass `--param agent=<implementer>` |
| A question to answer with sources, a technology choice, a feasibility study — no code | `research` | Add `--param repo=true` if it is about this codebase |
| Assess an existing codebase, tech-debt triage, due diligence | `review` | Delivers a prioritised report, no code |
| SQL, ETL/ELT, Spark, data models, schema changes, data-quality checks, analytics performance | `data` | Plans with the data architect |
| Terraform/IaC, Kubernetes, CI/CD, networking, IAM, observability config | `infra` | Always gates the plan; never applies |
| Restructure code without changing behaviour, modernise legacy code | `refactor` | Characterisation tests first |
| Make something measurably faster | `perf` | Baseline first, proves the gain |
| Security assessment or dependency audit | `security` | Delivers findings by severity |
| READMEs, runbooks, ADRs, API docs, release notes | `docs` | Reviewed for accuracy |

Mixed requests: choose by the riskiest part (a feature that adds a migration → `feature` still, the approver
escalates the migration; a pipeline with a new table → `data`). A request with several unrelated outcomes is
several jobs: tell the user and start them one by one (or add them to the backlog).

## Size

| Size | When | Effect |
|---|---|---|
| `S` | One area, a few files, obvious approach | No planning step |
| `M` | Several areas or a non-obvious approach; still one deliverable | Architect writes a design and a task plan; tasks run one by one with owned paths |
| `L` | Cross-cutting, risky or long | As M, and the plan waits for the user's approval before code is written |

When unsure between two sizes, pick the larger one for anything touching auth, payments, data migrations or
public APIs, and the smaller one otherwise.

## When to ask first (one question, AskUserQuestion)

- The request can mean two materially different things and the codebase does not settle it.
- It asks for something destructive or external (delete data, deploy, apply infrastructure, email users):
  confirm the factory should only prepare the change.
- A product decision is required up front (pricing, public copy, what is free vs paid).

Otherwise do not ask: the product analyst records assumptions and open questions in spec.md, and the approver
escalates real product decisions.

## Quick-task specialists

`builder` (general), `frontend-engineer`, `backend-engineer`, `sql-engineer`, `data-engineer`,
`data-quality-engineer`, `iac-engineer`, `platform-engineer`, `sre`, `test-engineer`, `docs-engineer`,
`performance-engineer`.
