// Command Center: what needs you, who is working, active and finished jobs, a task composer and the backlog.
import { h, mount, patch } from "../dom.js";
import { icon } from "../icons.js";
import { avatar } from "../avatars.js";
import { post } from "../api.js";
import { ago, between, duration, stepLabel } from "../format.js";
import { copyField, copyText, empty, statusPill, toast, verdictChips, workflowBadge } from "../components.js";

const WORKFLOWS = [
  ["auto", "Let the factory decide"], ["feature", "Feature"], ["fix", "Bug fix"], ["quick", "Quick task"],
  ["research", "Research"], ["review", "Codebase review"], ["data", "Data engineering"], ["infra", "Infrastructure"],
  ["refactor", "Refactor"], ["perf", "Performance"], ["security", "Security audit"], ["docs", "Documentation"],
];

export function commandFor({ text, workflow, size, agent }) {
  const parts = ["/factory"];
  if (workflow && workflow !== "auto") parts.push(workflow);
  if (size && size !== "auto") parts.push(`--size ${size}`);
  if (workflow === "quick" && agent) parts.push(`--agent ${agent}`);
  parts.push((text || "").trim().replace(/\s+/g, " ") || "<describe the outcome>");
  return parts.join(" ");
}

export function renderHome(root, ctx) {
  const state = ctx.state;
  const view = h("div", { class: "stack-lg" });
  const jobs = state.jobs || [];
  const attention = jobs.filter((j) => j.attention);
  const running = jobs.filter((j) => j.status === "running");
  const finished = jobs.filter((j) => ["merged", "delivered", "cancelled", "stale"].includes(j.status));
  const working = running.flatMap((j) => (j.running_detail || []).filter((r) => r.agent).map((r) => ({ ...r, job: j })));

  view.appendChild(h("div", { class: "hero" },
    h("div", { class: "stack" },
      h("span", { class: "eyebrow" }, "Command Center"),
      h("h1", { class: "h1" }, headline(attention.length, working.length, jobs.length)),
      h("div", { class: "hero-meta" },
        h("span", { class: "chip mono" }, icon("folder"), state.repo?.name || "repository"),
        state.repo?.base_branch ? h("span", { class: "chip mono" }, icon("branch"), state.repo.base_branch) : null)),
    h("div", { class: "row" },
      h("a", { class: "btn btn-primary", href: "#new-task" }, icon("plus"), "New task"))));

  view.appendChild(h("div", { class: "kpis", role: "list", "aria-label": "Factory totals" },
    kpi("Needs you", attention.length, attention.length ? "var(--st-attention)" : null),
    kpi("Running jobs", running.length),
    kpi("Agents working", working.length),
    kpi("Finished", finished.filter((j) => j.status !== "stale").length)));

  if (attention.length) {
    view.appendChild(h("section", { class: "stack", "aria-labelledby": "attn-h" },
      h("div", { class: "section-head" }, h("h2", { class: "h3", id: "attn-h" }, "Needs you"),
        h("span", { class: "faint small" }, "Decisions only you can make")),
      h("div", { class: "attention-list" }, attention.map((j) => attentionCard(j, ctx)))));
  }

  if (working.length) {
    view.appendChild(h("section", { class: "stack", "aria-labelledby": "work-h" },
      h("div", { class: "section-head" }, h("h2", { class: "h3", id: "work-h" }, "Working now"),
        h("span", { class: "faint small" }, "Live from the jobs' state")),
      h("div", { class: "working-strip" }, working.map((w) => {
        const meta = ctx.agentMeta(w.agent);
        return h("a", { class: "working-item", href: `#/jobs/${w.job.id}/crew?step=${encodeURIComponent(w.step)}` },
          avatar(meta, { size: "md", mood: "working" }),
          h("div", { class: "truncate" },
            h("div", { class: "truncate", style: { fontWeight: 600 } }, meta.title || w.agent),
            h("div", { class: "xs faint truncate" }, `${w.job.id.split("-")[0]} · ${stepLabel(w.step)}`),
            h("div", { class: "xs faint" }, icon("clock"), " ", duration(between(w.since)))));
      }))));
  }

  const grid = h("div", { class: "grid-2" });
  grid.appendChild(h("div", { class: "stack-lg" },
    jobsCard("Active", running.concat(jobs.filter((j) => j.attention)), "No active jobs. Start one from the composer.", ctx),
    jobsCard("Finished", finished, "Finished jobs will appear here.", ctx)));
  grid.appendChild(h("div", { class: "stack-lg" }, composer(ctx), backlogCard(state, ctx)));
  view.appendChild(grid);

  if (!jobs.length) {
    view.insertBefore(h("div", { class: "card" }, empty("spark", "No jobs yet",
      "Describe an outcome in the composer and run the command in Claude Code. Jobs, agents and their work appear here live.")), grid);
  }
  mount(root, view);
}

function headline(attn, working, total) {
  if (attn) return `${attn} ${attn === 1 ? "decision needs" : "decisions need"} you`;
  if (working) return `${working} ${working === 1 ? "agent is" : "agents are"} working`;
  if (total) return "All quiet on the factory floor";
  return "Ready when you are";
}

function kpi(label, value, color) {
  return h("div", { class: "card kpi", role: "listitem" },
    h("div", { class: "kpi-value", style: color ? { color } : {} }, String(value)),
    h("div", { class: "kpi-label" }, label));
}

function attentionCard(job, ctx) {
  const a = job.attention;
  const label = { escalate: "Escalated by the approver", "plan-approval": "Plan ready for approval", blocked: "Blocked",
    limit: "Limit reached", "rounds-exhausted": "Review rounds exhausted", "merge-busy": "Approved — main checkout busy",
    "merge-conflict": "Approved — merge conflict", policy: "Needs a human by policy", integrity: "Integrity stop",
    "approve-merge": "Approved — waiting to merge" }[a.kind] || a.kind;
  const primary = (a.commands || [])[0];
  return h("article", { class: "attention-card" },
    h("div", { class: "attention-icon" }, icon("bell", "icon icon-lg")),
    h("div", { class: "stack", style: { gap: "6px" } },
      h("div", { class: "row-wrap" },
        h("a", { href: `#/jobs/${job.id}`, class: "job-title" }, job.title),
        h("span", { class: "pill attention" }, label)),
      h("div", { class: "reason" }, a.reason),
      primary ? copyField(primary.replace("<note>", "\"…\""), "Copy command") : null),
    h("a", { class: "btn btn-sm", href: `#/jobs/${job.id}` }, "Open", icon("chevron")));
}

function jobsCard(title, jobs, emptyText, ctx) {
  const seen = new Set();
  const list = jobs.filter((j) => (seen.has(j.id) ? false : seen.add(j.id)));
  return h("section", { class: "card", "aria-label": `${title} jobs` },
    h("div", { class: "card-head" }, h("h2", { class: "h3" }, title), h("span", { class: "faint small" }, `${list.length}`)),
    list.length ? h("div", { class: "job-list" }, list.slice(0, 30).map((j) => jobRow(j, ctx)))
      : h("div", { class: "card-body faint small" }, emptyText));
}

function jobRow(j, ctx) {
  const wf = ctx.workflowTitle(j.workflow);
  return h("a", { class: "job-row", href: `#/jobs/${j.id}` },
    h("div", { class: "truncate" },
      h("div", { class: "job-title truncate" }, j.title),
      h("div", { class: "job-meta" },
        h("span", { class: "mono" }, j.id.split("-")[0]),
        workflowBadge(j.workflow, wf),
        j.stage && j.status === "running" ? h("span", {}, `${j.stage}${j.round ? ` · round ${j.round}` : ""}`) : null,
        verdictChips(j.status === "running" ? j.reviews : null),
        j.pending_messages ? h("span", { class: "msg-badge" }, icon("message"), `${j.pending_messages} queued`) : null,
        j.legacy ? h("span", { class: "chip" }, "v1 · read-only") : null,
        j.updated_at ? h("span", {}, ago(j.updated_at)) : null)),
    h("div", { class: "row" },
      (j.running_detail || []).length ? h("span", { class: "avatar-stack" },
        j.running_detail.slice(0, 4).map((r) => avatar(ctx.agentMeta(r.agent), { size: "sm", mood: "working", model: false }))) : null,
      statusPill(j.status)));
}

function composer(ctx) {
  const state = { text: "", workflow: "auto", size: "auto", agent: "builder" };
  let saved = null;
  try { saved = JSON.parse(sessionStorage.getItem("factory.composer") || "null"); } catch { /* ignore */ }
  if (saved) Object.assign(state, saved);
  const preview = h("div", {});
  const agentField = h("div", { class: "field" });
  const save = () => { try { sessionStorage.setItem("factory.composer", JSON.stringify(state)); } catch { /* ignore */ } };
  const update = () => {
    mount(preview, copyField(commandFor(state), "Copy command"));
    agentField.hidden = state.workflow !== "quick";
    save();
  };
  const implementers = ctx.catalog?.agents?.filter((a) => ["build", "data", "infra", "quality"].includes(a.domain) &&
    (a.tools || []).includes("Edit")) || [];
  const textarea = h("textarea", { class: "textarea", id: "task-text", rows: 4, placeholder: "e.g. Rate limit the signup form to 5 attempts per IP per 10 minutes",
    oninput: (e) => { state.text = e.target.value; update(); } });
  textarea.value = state.text;
  const wfSelect = h("select", { class: "select", id: "task-wf", onchange: (e) => { state.workflow = e.target.value; update(); } },
    WORKFLOWS.map(([v, l]) => h("option", { value: v, selected: v === state.workflow }, l)));
  const sizeSelect = h("select", { class: "select", id: "task-size", onchange: (e) => { state.size = e.target.value; update(); } },
    [["auto", "Auto"], ["S", "Small"], ["M", "Medium"], ["L", "Large (plan approval)"]].map(([v, l]) => h("option", { value: v, selected: v === state.size }, l)));
  mount(agentField, h("label", { for: "task-agent" }, "Specialist"),
    h("select", { class: "select", id: "task-agent", onchange: (e) => { state.agent = e.target.value; update(); } },
      implementers.map((a) => h("option", { value: a.name, selected: a.name === state.agent }, a.title))));
  const addBtn = h("button", { class: "btn", type: "button", onclick: async () => {
    if (!state.text.trim()) { toast("Describe the task first", "error"); textarea.focus(); return; }
    addBtn.disabled = true;
    try {
      await post("/api/backlog", { text: state.text.trim() });
      toast("Added to the backlog — run /factory next to start it");
      state.text = ""; textarea.value = ""; update();
      ctx.refresh();
    } catch (err) { toast(err.message, "error"); }
    addBtn.disabled = false;
  } }, icon("inbox"), "Add to backlog");
  update();
  return h("section", { class: "card", id: "new-task", "aria-labelledby": "composer-h" },
    h("div", { class: "card-head" }, h("h2", { class: "h3", id: "composer-h" }, "New task"),
      h("span", { class: "faint xs" }, "Runs in Claude Code")),
    h("div", { class: "card-body stack" },
      h("div", { class: "field" }, h("label", { for: "task-text" }, "Outcome you want"), textarea),
      h("div", { class: "row", style: { alignItems: "end" } },
        h("div", { class: "field", style: { flex: 1 } }, h("label", { for: "task-wf" }, "Workflow"), wfSelect),
        h("div", { class: "field", style: { width: "150px" } }, h("label", { for: "task-size" }, "Size"), sizeSelect)),
      agentField,
      h("div", { class: "field" }, h("span", { class: "label" }, "Command"), preview),
      h("p", { class: "hint" }, "Paste the command into Claude Code in this repository; the console shows the job as soon as it starts. The console never starts agents by itself."),
      h("div", { class: "row" },
        h("button", { class: "btn btn-primary", type: "button", onclick: () => copyText(commandFor(state)) }, icon("copy"), "Copy command"),
        addBtn)));
}

function backlogCard(state, ctx) {
  const items = state.backlog || [];
  const open = items.filter((i) => !i.done);
  return h("section", { class: "card", "aria-labelledby": "backlog-h" },
    h("div", { class: "card-head" }, h("h2", { class: "h3", id: "backlog-h" }, "Backlog"),
      open.length ? copyField("/factory next", "Copy") : h("span", { class: "faint xs" }, "empty")),
    h("div", { class: "card-body" },
      items.length ? h("div", {}, items.slice(-12).reverse().map((it) => h("div", { class: ["backlog-item", it.done ? "done" : ""] },
        icon(it.done ? "check" : "dot"), h("span", { class: "truncate", style: { flex: 1 } }, it.text),
        it.job ? h("a", { href: `#/jobs/${it.job}`, class: "xs mono" }, it.job.split("-")[0]) : null)))
        : h("p", { class: "faint small" }, "Queue ideas here with “Add to backlog”; /factory next builds the first unchecked one.")));
}
