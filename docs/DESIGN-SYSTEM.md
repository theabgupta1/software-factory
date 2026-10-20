# Factory Console: UX and design system

The console answers three questions, in this order: **what needs me**, **what is happening right now**, and **what
happened and why**. Everything shown is real engine state; when something is unknown (no transcript, no tokens
recorded) the UI says so instead of inventing it.

## Information architecture

| Destination | Purpose | Key content |
|---|---|---|
| **Command Center** `#/` | Decide and start | Headline ("2 decisions need you"), totals, the needs-you queue (each with reason, the exact command and Copy), agents working now, active and finished jobs, the New task composer (builds the exact `/factory` command; or adds to the backlog), backlog |
| **Job workspace** `#/jobs/<id>/<tab>` | Follow and steer one job | Header (id, title, workflow, size, status, branch, timing) and a single **next action** box, then tabs: Overview (stage rail, request, intake reason, AC, decision) · **Crew** (every agent run as a card; open one to see its live activity, output and role, and message it) · Flow (the job as a graph: parallel reviewers share a column, review rounds loop back) · Timeline (Gantt per agent with durations and tokens; filterable event log) · Artifacts (grouped files, rendered markdown, plan table, verdict banner) · Changes (commits, diffstat, per-file diff) · Quality (verdicts per round, acceptance criteria, open findings, checks the agents ran) |
| **Agents & workflows** `#/catalog` | Understand the factory | 25 agents by domain with search and each one's full contract; 11 workflows as stage pipelines |
| **Settings** `#/settings` | See how it is set up | Effective config and its file, environment checks, detected tools per discipline, the guard policy |

Progressive disclosure: lists show the one line that matters (status, stage, verdict chips, who is working); detail
lives one click away in drawers and tabs; raw files are always reachable from Artifacts. The demo (`factoryctl serve
--demo`) carries a permanent "Demo data" banner.

## Agents as "mods"

Every agent has a robot avatar — a small module in its own hue wearing its role's emblem, with a model badge (O, S,
H). The face shows real state: **working** (blinking eyes, pulsing antenna), **happy** (passed, done),
**concerned** (changes requested, escalated), **blocked**, **idle**, **asleep** (upcoming or lost). The same robot
appears on agent cards, the graph, the timeline, the catalog, the chat drawer, and — drawn as terminal pixels or SVG
— on the factory-live board. Hues come from `contracts/agents.json`, so an agent looks the same everywhere.

## Talking to agents

Open any agent card (or graph node, or timeline bar) to get a drawer with its live activity (tool calls and replies
from its transcript) interleaved with your messages, a composer (Ctrl/⌘+Enter sends), and a note that says exactly
how the message will be delivered for that agent's state: at its next tool call while running, in its next prompt
otherwise. Each message shows Queued, then "Delivered mid-run · 12:04" or "Delivered in its prompt". The
orchestrator has its own drawer for job-level instructions.

## Tokens

All values live in `ui/styles/tokens.css`; components use only tokens.

- **Type:** system UI stack and a monospace stack; sizes 11.5 / 12.5 / 13.5 / 15 / 18 / 22 / 28 px.
- **Space:** 4 px scale (4, 8, 12, 16, 20, 24, 32, 40, 48). **Radii:** 4, 6, 10, 14, 20, full.
- **Surfaces:** `--bg`, `--surface`, `--surface-2/3`, `--raised`, borders `--border` / `--border-strong`.
- **Text:** `--text`, `--text-2`, `--text-3` (all AA on every surface they sit on), `--accent` with `--accent-ink` for
  text on accent fills, `--on-status` for text on status fills.
- **Status:** running (blue), pass (green), changes (amber), attention (pink), blocked (red), done/merged (violet),
  neutral (slate). Status text on a tint uses the hue mixed 70% toward the body text colour, so pills and chips stay
  AA in both themes. Status is never colour alone: every pill and chip has an icon or glyph and a word.
- **Themes:** light and dark from the same semantic tokens; follows the OS, overridable with the Light/Dark toggle
  (`t`), remembered per browser.
- **Motion:** 120 ms and 220 ms with one easing curve; only the status marks pulse (never text). `prefers-reduced-motion`
  stops all animation.

## Components

App shell (sidebar, top bar, breadcrumbs, skip link) · cards and KPI tiles · status pills and verdict chips · copy
field (command + Copy) · buttons (primary, default, ghost, icon) · tabs (roving tabindex, arrow keys) · segmented
controls · agent card, upcoming card, working strip · stage rail · flow graph nodes and edges · Gantt rows and bars ·
drawer (modal dialog with focus trap and focus return) · conversation bubbles and composer · toasts (`role=status`)
· empty, loading (skeleton, `aria-busy`), error (with retry) and not-found states · markdown, tables, diff.

## Accessibility (tested)

- Landmarks (`nav`, `main`), one `h1` per view, a skip link, page titles that track the view.
- Keyboard: everything reachable by Tab; tabs and graph nodes by arrow keys; drawers trap focus, close on Escape and
  return focus to the element that opened them (even after a live re-render); shortcuts `g h` / `g a` / `g s`, `/`
  search, `1`–`7` job tabs, `t` theme, `?` help. Live re-renders keep keyboard focus.
- Live regions for status changes and toasts; meaningful labels on icon buttons, avatars and graph nodes.
- axe (WCAG 2.1 A/AA) reports no serious or critical issues on every view in light and dark (Playwright suite).

## Responsive

Breakpoints at 1180, 860 and 520 px. Below 860 px the sidebar becomes a drawer behind the menu button, grids stack,
and wide content (graph, Gantt, tables, diffs) scrolls inside its own panel; the page itself never scrolls
horizontally (tested at 1440, 900 and 390 px). Drawers take the full width on phones.

## Implementation notes

Vanilla ES modules, no build step: `dom.js` (`h()` element builder, `patch()` for keyed re-renders), `api.js` (ETag
polling, token-carrying POST), `markdown.js` (escape-first renderer), `avatars.js`, `components.js`, one module per
view. The page polls `/api/state` every 2.5 s and the open job's detail; unchanged responses are 304s.
