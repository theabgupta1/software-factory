// Job workspace: header, next action, and tabbed sections (Overview, Crew, Flow, Timeline, Artifacts, Changes, Quality).
import { h, mount } from "../dom.js";
import { icon } from "../icons.js";
import { avatar } from "../avatars.js";
import { ago, between, clock, dateTime, duration, eventIcon, humanEvent, stepLabel, tokens } from "../format.js";
import { copyField, statusPill, tabs, workflowBadge, workflowIcon } from "../components.js";
import { renderCrew, openAgent } from "./crew.js";
import { renderFlow } from "./flow.js";
import { renderTimeline } from "./timeline.js";
import { renderArtifacts } from "./artifacts.js";
import { renderChanges } from "./changes.js";
import { renderQuality } from "./quality.js";

export const TABS = [
  { id: "overview", label: "Overview", icon: "home" },
  { id: "crew", label: "Crew", icon: "users" },
  { id: "flow", label: "Flow", icon: "flow" },
  { id: "timeline", label: "Timeline", icon: "bars" },
  { id: "artifacts", label: "Artifacts", icon: "file" },
  { id: "changes", label: "Changes", icon: "diff" },
  { id: "quality", label: "Quality", icon: "shield" },
];

const NEED_LABEL = { escalate: "The approver escalated this job", "plan-approval": "The plan is ready for your approval",
  blocked: "An agent is blocked", limit: "A limit was reached", "rounds-exhausted": "Review rounds are exhausted",
  "merge-busy": "Approved, but the main checkout is busy", "merge-conflict": "Approved, but merging would conflict",
  policy: "A human must approve this by policy", integrity: "The worktree changed during review",
  "approve-merge": "Approved and waiting for you to merge" };

export function renderJob(root, ctx, detail, route) {
  const { job } = detail;
  const tabId = TABS.some((t) => t.id === route.tab) ? route.tab : "overview";
  const crewRunning = detail.crew.filter((c) => c.status === "issued").length;
  const counts = { crew: detail.crew.length, artifacts: detail.artifacts.length,
    quality: detail.quality.open_findings.length || null };
  const panel = h("div", { id: "job-panel", role: "tabpanel", "aria-labelledby": `job-tab-${tabId}`, tabindex: "-1" });

  mount(root, h("div", { class: "stack-lg" },
    header(detail, ctx),
    nextAction(detail, ctx, crewRunning),
    h("div", { class: "stack" },
      tabs(TABS.map((t) => ({ ...t, count: counts[t.id] || null })), tabId,
        (id, focus) => { ctx.go(`#/jobs/${job.id}/${id}`); if (focus) requestAnimationFrame(() => document.getElementById(`job-tab-${id}`)?.focus()); },
        "job"),
      panel)));

  const render = { overview: renderOverview, crew: renderCrew, flow: renderFlow, timeline: renderTimeline,
    artifacts: renderArtifacts, changes: renderChanges, quality: renderQuality }[tabId];
  render(panel, ctx, detail, route);
  if (route.params?.step && tabId === "crew") {
    const entry = detail.crew.find((c) => c.step === route.params.step);
    if (entry && !ctx.agentDrawerFor(entry.step)) openAgent(ctx, detail, entry);
  }
}

function header(detail, ctx) {
  const { job } = detail;
  const created = job.created_at;
  const end = ["merged", "delivered", "cancelled"].includes(job.status) ? job.updated_at : null;
  return h("header", { class: "job-header" },
    h("div", { class: "job-title-row" },
      h("div", { class: "stack", style: { gap: "6px", minWidth: 0 } },
        h("span", { class: "eyebrow" }, job.id),
        h("h1", { class: "h1" }, job.title || job.request || job.id)),
      h("div", { class: "row-wrap" },
        workflowBadge(job.workflow, job.workflow_title),
        job.size ? h("span", { class: "chip" }, `Size ${job.size}`) : null,
        detail.legacy ? h("span", { class: "chip" }, "v1 · read-only") : null,
        statusPill(job.status))),
    h("div", { class: "meta-row" },
      job.branch ? h("span", { class: "mono" }, icon("branch"), job.branch) : null,
      job.base_branch ? h("span", {}, "from ", h("code", {}, job.base_branch)) : null,
      created ? h("span", {}, icon("clock"), `Started ${dateTime(created)}`) : null,
      created ? h("span", {}, `${end ? "Took" : "Elapsed"} ${duration(between(created, end))}`) : null,
      job.updated_at ? h("span", { class: "faint" }, `Updated ${ago(job.updated_at)}`) : null));
}

function nextAction(detail, ctx, crewRunning) {
  const { job } = detail;
  if (detail.legacy) {
    return box("done", "info", "A job from the original factory",
      h("p", { class: "small muted" }, "This v1 job is read-only. Its files, verdicts and decision are shown as recorded."));
  }
  if (job.status === "needs-human" || job.status === "awaiting-approval") {
    const hum = job.human || {};
    return box("attention", "bell", NEED_LABEL[hum.kind] || "This job needs you",
      h("div", { class: "stack" },
        h("p", { class: "small", style: { margin: 0 } }, hum.reason),
        (hum.files || []).length ? h("div", { class: "row-wrap" }, h("span", { class: "xs faint" }, "Read first:"),
          hum.files.map((f) => h("a", { class: "chip mono", href: `#/jobs/${job.id}/artifacts?file=${encodeURIComponent(f)}` }, icon("file"), f))) : null,
        (hum.commands || []).map((c) => copyField(c.replace("<note>", "\"what to change\""))),
        h("p", { class: "hint", style: { margin: 0 } }, "Run these in Claude Code in this repository.")));
  }
  if (job.status === "merged") {
    return box("done", "merge", `Merged into ${job.base_branch}`,
      h("p", { class: "small muted", style: { margin: 0 } },
        job.merge?.sha ? h("span", {}, "Merge commit ", h("code", {}, job.merge.sha.slice(0, 12)), ". ") : null,
        "The final report is in ", h("a", { href: `#/jobs/${job.id}/artifacts?file=final-report.md` }, "final-report.md"), "."));
  }
  if (job.status === "delivered") {
    const deliverable = detail.artifacts.find((a) => /^(research|report|security-audit)\.md$/.test(a.path));
    return box("done", "check", "Delivered",
      h("p", { class: "small muted", style: { margin: 0 } }, "The deliverable is ",
        deliverable ? h("a", { href: `#/jobs/${job.id}/artifacts?file=${deliverable.path}` }, deliverable.path) : "in Artifacts", "."));
  }
  if (job.status === "cancelled") return box("cancelled", "x", "Cancelled", h("p", { class: "small muted", style: { margin: 0 } }, `The branch ${job.branch} and its worktree were kept.`));
  const running = detail.crew.filter((c) => c.status === "issued");
  if (running.length) {
    return box("running", "play", `${running.length} ${running.length === 1 ? "agent is" : "agents are"} working`,
      h("div", { class: "row-wrap" }, running.map((c) => h("a", { class: "chip", href: `#/jobs/${job.id}/crew?step=${encodeURIComponent(c.step)}` },
        avatar(ctx.agentMeta(c.agent), { size: "xs", mood: "working", model: false }), `${c.title} · ${stepLabel(c.step)}`))));
  }
  const idle = between(job.lease?.heartbeat_at);
  return box("running", "clock", "Waiting for the orchestrator",
    h("div", { class: "stack" },
      h("p", { class: "small muted", style: { margin: 0 } }, "No agent is running; the Claude Code session that drives this job decides the next step."),
      idle !== null && idle > 900 ? h("div", { class: "stack" }, h("span", { class: "small" }, `The driving session has been quiet for ${duration(idle)}. If it is gone, take the job over:`),
        copyField(`/factory resume ${job.id}`)) : null));
}

function box(kind, iconName, title, body) {
  return h("section", { class: ["next-action", kind], "aria-live": "polite" },
    h("div", { class: "na-icon" }, icon(iconName, "icon icon-lg")),
    h("div", { class: "stack", style: { gap: "8px", minWidth: 0 } }, h("h2", { class: "h3" }, title), body));
}

function renderOverview(root, ctx, detail) {
  const { job } = detail;
  const totalTokens = detail.crew.reduce((n, c) => n + (c.usage?.total || 0), 0);
  const msgs = detail.messages || [];
  const limits = job.limits || {};
  mount(root, h("div", { class: "stack-lg" },
    h("section", { class: "card card-pad stack", "aria-label": "Stages" },
      h("div", { class: "section-head" }, h("h2", { class: "h3" }, "Stages"),
        h("span", { class: "faint small" }, h("span", {}, icon(workflowIcon(job.workflow))), " ", detail.workflow?.title || job.workflow)),
      rail(detail.stages)),
    h("div", { class: "stats" },
      stat("Agent runs", `${job.counters?.agent_runs ?? detail.crew.length}${limits.max_agent_runs ? ` / ${limits.max_agent_runs}` : ""}`),
      stat("Review rounds", `${(job.rounds || []).length}${limits.max_review_rounds ? ` / ${limits.max_review_rounds}` : ""}`),
      stat("Messages", msgs.length ? `${msgs.filter((m) => m.status === "delivered").length} delivered · ${msgs.filter((m) => m.status === "queued").length} queued` : "none"),
      stat("Tokens", totalTokens ? tokens(totalTokens) : "not recorded", totalTokens ? "from agent transcripts" : "recorded by the factory hooks")),
    h("div", { class: "grid-2" },
      h("section", { class: "card card-pad stack" },
        h("h2", { class: "h3" }, "Request"),
        h("blockquote", { class: "request-quote" }, job.request || job.title),
        job.intake?.why ? h("p", { class: "small muted", style: { margin: 0 } }, h("strong", {}, "Why this workflow: "), job.intake.why) : null,
        (job.warnings || []).length ? h("div", { class: "stack" }, job.warnings.map((w) => h("div", { class: "warn-box" }, icon("alert"), w))) : null),
      h("section", { class: "card card-pad stack" },
        h("div", { class: "section-head" }, h("h2", { class: "h3" }, "Recent activity"),
          h("a", { class: "small", href: `#/jobs/${job.id}/timeline` }, "Timeline", icon("chevron"))),
        detail.events.length ? h("div", { class: "events" }, detail.events.slice(-12).reverse().map((ev) =>
          h("div", { class: "event" }, h("time", { datetime: ev.ts }, clock(ev.ts)),
            h("span", { class: "ev-icon" }, icon(eventIcon(ev.type))), h("span", {}, humanEvent(ev)))))
          : h("p", { class: "faint small" }, detail.legacy ? "v1 jobs have no event log." : "No events yet.")))));
}

function stat(label, value, hint) {
  return h("div", { class: "stat" }, h("div", { class: "kpi-label" }, label), h("div", { class: "stat-value" }, value),
    hint ? h("div", { class: "xs faint" }, hint) : null);
}

export function rail(stages) {
  const glyph = { done: "check", active: "play", attention: "bell", skipped: "chevron", upcoming: "dot", cancelled: "x" };
  return h("ol", { class: "rail", style: { listStyle: "none", margin: 0 } }, (stages || []).map((st) =>
    h("li", { class: ["rail-step", st.state], "aria-label": `${st.title}: ${st.state}` },
      h("span", { class: "rail-dot" }, icon(glyph[st.state] || "dot")),
      h("span", { class: "rail-label" }, st.title))));
}
