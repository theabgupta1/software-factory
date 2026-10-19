// Crew: every agent on the job with its avatar and live state; click one to see its activity, read its output
// and message it while it works. Delivery is real: a running agent gets the message at its next tool call
// (factory hook), an idle one in its next prompt; the panel shows which happened and when.
import { h, mount, patch } from "../dom.js";
import { icon } from "../icons.js";
import { avatar, moodFor } from "../avatars.js";
import { get, post, poll } from "../api.js";
import { ago, between, clock, duration, stepLabel, tokens } from "../format.js";
import { closeDrawer, empty, openDrawer, stepPill, toast } from "../components.js";
import { markdown } from "../markdown.js";

export function renderCrew(root, ctx, detail) {
  const { job } = detail;
  const crew = detail.crew;
  const upcoming = upcomingAgents(detail, ctx);
  const orchestratorMsgs = (detail.messages || []).filter((m) => m.to.role === "orchestrator");
  mount(root, h("div", { class: "stack-lg" },
    h("div", { class: "row between", style: { flexWrap: "wrap" } },
      h("p", { class: "small muted", style: { margin: 0 } },
        crew.length ? `${crew.length} agent ${crew.length === 1 ? "run" : "runs"} so far. Select an agent to follow its work or send it an instruction.`
          : "No agent has started yet."),
      detail.legacy ? null : h("button", { class: "btn btn-sm", type: "button", onclick: () => openOrchestrator(ctx, detail) },
        icon("message"), "Message the orchestrator", orchestratorMsgs.length ? h("span", { class: "count" }, String(orchestratorMsgs.length)) : null)),
    crew.length ? h("div", { class: "crew-grid" }, crew.map((c) => agentCard(c, ctx, detail))) : null,
    upcoming.length ? h("section", { class: "stack" },
      h("div", { class: "section-head" }, h("h2", { class: "h3" }, "Up next"), h("span", { class: "faint small" }, "From the workflow definition")),
      h("div", { class: "crew-grid" }, upcoming.map((u) => upcomingCard(u, ctx)))) : null,
    !crew.length && !upcoming.length ? empty("users", "No crew yet", "Agents appear here as the workflow starts them.") : null));
}

function agentCard(c, ctx, detail) {
  const meta = ctx.agentMeta(c.agent);
  const queued = c.messages.filter((m) => m.status === "queued").length;
  const delivered = c.messages.filter((m) => m.status === "delivered").length;
  return h("button", { class: ["agent-card", c.status === "issued" ? "is-running" : ""], type: "button",
    "aria-label": `${c.title}, ${stepLabel(c.step)}, ${c.status === "issued" ? "working" : c.value || c.status}`,
    onclick: () => { ctx.go(`#/jobs/${detail.job.id}/crew?step=${encodeURIComponent(c.step)}`, { replace: true }); openAgent(ctx, detail, c); } },
    h("div", { class: "row between" },
      h("div", { class: "who" }, avatar({ ...meta, model: c.model && c.model.includes("opus") ? "opus" : meta.model }, { size: "lg", step: c }),
        h("div", { class: "truncate" }, h("div", { class: "name truncate" }, c.title), h("div", { class: "step truncate" }, stepLabel(c.step)))),
      stepPill(c)),
    c.why ? h("div", { class: "why" }, h("strong", {}, "Why: "), c.why) : null,
    (c.problems || []).length ? h("div", { class: "warn-box" }, icon("alert"), c.problems[0]) : null,
    h("div", { class: "facts" },
      h("span", {}, h("b", {}, duration(c.seconds)), c.status === "issued" ? " so far" : ""),
      c.usage?.total ? h("span", {}, h("b", {}, tokens(c.usage.total)), " tokens") : null,
      c.tool_uses ? h("span", {}, h("b", {}, String(c.tool_uses)), " tool calls") : null,
      c.attempt > 1 ? h("span", {}, `attempt ${c.attempt}`) : null,
      queued ? h("span", { class: "msg-badge" }, icon("message"), `${queued} queued`) : null,
      delivered ? h("span", { class: "faint" }, icon("check"), ` ${delivered} delivered`) : null));
}

function upcomingAgents(detail, ctx) {
  if (["merged", "delivered", "cancelled"].includes(detail.job.status) || detail.legacy) return [];
  const started = new Set(detail.crew.map((c) => c.agent + "@" + (c.stage || "")));
  const out = [];
  for (const st of detail.stages || []) {
    if (st.state !== "upcoming") continue;
    const stage = (detail.workflow?.stages || []).find((s) => s.id === st.id) || {};
    const names = stage.kind === "parallel" ? (stage.members || []).map((m) => m.agent)
      : stage.kind === "panel" ? (stage.always || []).map((k) => ctx.reviewAgent(k))
        : stage.agent && !String(stage.agent).startsWith("$") ? [stage.agent] : [];
    names.filter(Boolean).forEach((n) => { if (!started.has(n + "@" + st.id)) out.push({ agent: n, stage: st }); });
  }
  return out.slice(0, 8);
}

function upcomingCard(u, ctx) {
  const meta = ctx.agentMeta(u.agent);
  return h("button", { class: "agent-card", type: "button", style: { opacity: 0.78 }, onclick: () => ctx.go(`#/catalog/${u.agent}`) },
    h("div", { class: "who" }, avatar(meta, { size: "lg", mood: "asleep" }),
      h("div", { class: "truncate" }, h("div", { class: "name" }, meta.title), h("div", { class: "step" }, `${u.stage.title} · upcoming`))),
    h("div", { class: "why" }, meta.summary || ""));
}

// ---- Agent panel ------------------------------------------------------------------------------------
let current = null;

export function openAgent(ctx, detail, entry) {
  const meta = ctx.agentMeta(entry.agent);
  let view = "activity";
  let feed = { items: [], cursor: 0, missing: !entry.has_transcript };
  let stopPoll = null;
  const body = h("div", { class: "drawer-body", tabindex: "0", "aria-label": "Agent activity" });
  const viewSeg = h("div", { class: "seg", role: "group", "aria-label": "Panel view" });
  const headPill = h("span", {});
  const facts = h("div", { class: "row-wrap xs faint" });
  const foot = h("div", { class: "drawer-foot" });
  current = { step: entry.step, update: null };

  function render() {
    const e = current.entry || entry;
    mount(headPill, stepPill(e));
    mount(facts,
      h("span", { class: "chip" }, icon("spark"), e.model || meta.model || "model"),
      h("span", { class: "chip" }, icon("clock"), duration(e.seconds), e.status === "issued" ? " so far" : ""),
      e.usage?.total ? h("span", { class: "chip" }, `${tokens(e.usage.total)} tokens`) : null,
      e.attempt > 1 ? h("span", { class: "chip" }, `attempt ${e.attempt}`) : null,
      e.output ? h("a", { class: "chip mono", href: `#/jobs/${detail.job.id}/artifacts?file=${encodeURIComponent(e.output)}`, onclick: () => closeDrawer() }, icon("file"), e.output) : null);
    mount(viewSeg, ...[["activity", "Activity"], ["output", "Output"], ["contract", "Role"]].map(([id, label]) =>
      h("button", { type: "button", "aria-pressed": view === id ? "true" : "false", onclick: () => { view = id; renderBody(); render(); } }, label)));
  }

  function renderBody() {
    const e = current.entry || entry;
    if (view === "output") return renderOutput(body, detail, e);
    if (view === "contract") {
      const agentInfo = (ctx.catalog?.agents || []).find((a) => a.name === e.agent);
      return mount(body, h("div", { class: "stack" },
        h("p", { class: "small muted" }, meta.summary || ""),
        e.why ? h("p", { class: "small" }, h("strong", {}, "Selected because: "), e.why) : null,
        agentInfo?.contract?.["Purpose and scope"] ? h("div", { class: "md" }, rawMd(agentInfo.contract["Purpose and scope"])) : null,
        h("a", { class: "btn btn-sm", href: `#/catalog/${e.agent}`, onclick: () => closeDrawer() }, "Full contract", icon("chevron"))));
    }
    const items = conversation(feed, e.messages || [], e);
    patch(body, JSON.stringify([items.length, feed.cursor, (e.messages || []).map((m) => m.status)]), () =>
      items.length ? h("div", { class: "convo" }, items) :
        empty(feed.missing ? "info" : "clock", feed.missing ? "No live activity recorded" : "Waiting for the first step",
          feed.missing ? "Activity streams from the agent's transcript once the factory hooks record it. You can still message the agent." : "Activity appears here as the agent works."));
    if (e.status === "issued") body.scrollTop = body.scrollHeight;
  }

  const textarea = h("textarea", { class: "textarea", rows: 2, id: "agent-msg", "data-autofocus": true,
    placeholder: `Message ${meta.title || entry.agent}…`, "aria-describedby": "delivery-note",
    onkeydown: (ev) => { if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) { ev.preventDefault(); send(); } } });
  const sendBtn = h("button", { class: "btn btn-primary", type: "button", onclick: () => send() }, icon("send"), "Send");
  async function send() {
    const text = textarea.value.trim();
    const e = current.entry || entry;
    if (!text) { textarea.focus(); return; }
    sendBtn.disabled = true;
    try {
      const to = e.status === "issued" ? { step: e.step } : { agent: e.agent };
      await post(`/api/jobs/${detail.job.id}/messages`, { to, text });
      textarea.value = "";
      toast(e.status === "issued" ? "Sent — it reaches the agent at its next tool call" : "Queued for the agent's next run");
      ctx.refresh();
    } catch (err) { toast(err.message, "error"); }
    sendBtn.disabled = false;
    textarea.focus();
  }

  openDrawer((panel) => {
    mount(panel,
      h("div", { class: "drawer-head" },
        avatar(meta, { size: "xl", step: entry }),
        h("div", { class: "stack", style: { gap: "4px", minWidth: 0 } },
          h("span", { class: "eyebrow" }, stepLabel(entry.step)),
          h("h2", { class: "h2" }, entry.title),
          headPill),
        h("button", { class: "btn btn-ghost btn-icon", type: "button", "aria-label": "Close", onclick: () => closeDrawer() }, icon("x"))),
      h("div", { class: "drawer-sub" }, facts, viewSeg),
      body,
      detail.legacy ? h("div", { class: "drawer-foot faint small" }, "v1 jobs are read-only.") : foot);
    mount(foot, h("div", { class: "composer" },
      h("label", { class: "sr-only", for: "agent-msg" }, `Message ${entry.title}`),
      h("div", { class: "composer-row" }, textarea, sendBtn),
      h("div", { class: "delivery-note", id: "delivery-note" }, icon("info"), deliveryNote(entry))));
    render();
    renderBody();
  }, { label: `${entry.title} — ${stepLabel(entry.step)}`, onClose: () => {
    if (stopPoll) stopPoll();
    current = null;
    if (location.hash.includes("step=")) ctx.go(`#/jobs/${detail.job.id}/crew`, { replace: true, quiet: true });
  } });

  current.update = (newDetail) => {
    const fresh = newDetail.crew.find((c) => c.step === entry.step);
    if (!fresh) return;
    current.entry = fresh;
    detail = newDetail;
    render();
    if (view === "activity") renderBody();
    const note = document.getElementById("delivery-note");
    if (note) mount(note, icon("info"), deliveryNote(fresh));
  };
  if (entry.agent_id) {
    stopPoll = poll(`/api/jobs/${detail.job.id}/agents/${entry.agent_id}/activity?after=0`, 2500, (data) => {
      if (data) { feed = data; if (view === "activity") renderBody(); }
    }, () => {});
  }
}

export function updateAgentDrawer(detail) {
  if (current && current.update) current.update(detail);
}

export function agentDrawerStep() { return current?.step || null; }

function deliveryNote(e) {
  if (e.status === "issued") return "It is working now: your message is delivered at its next tool call through the factory hook, and the receipt shows here.";
  if (e.status === "done") return "It has finished this step. Your message is queued and folded into this agent's next prompt in this job (next round or a rework); if it doesn't run again, message the orchestrator instead.";
  return "Queued: delivered in this agent's next prompt.";
}

function conversation(feed, messages, entry) {
  const rows = [];
  (feed.items || []).forEach((it) => rows.push({ ts: it.ts || "", kind: it.kind, it }));
  messages.forEach((m) => rows.push({ ts: m.ts, kind: "user-msg", m }));
  rows.sort((a, b) => (a.ts || "").localeCompare(b.ts || ""));
  return rows.map((r) => {
    if (r.kind === "user-msg") {
      const m = r.m;
      const status = m.status === "delivered"
        ? `Delivered ${m.delivered?.via === "hook" ? "mid-run" : m.delivered?.via === "prompt" ? "in its prompt" : `via ${m.delivered?.via}`} · ${clock(m.delivered?.ts)}`
        : "Queued";
      return h("div", { class: "bubble user" }, m.text, h("div", { class: "meta" }, `You · ${clock(m.ts)}`, " · ", status));
    }
    const it = r.it;
    if (it.kind === "text") return h("div", { class: "bubble agent" }, h("div", { class: "md" }, rawMd(it.text)), h("div", { class: "meta" }, clock(it.ts)));
    if (it.kind === "tool") return h("div", { class: "tool-line" }, h("span", { class: "tool-name" }, it.tool), h("code", { class: "truncate" }, it.summary));
    if (it.kind === "result") return h("pre", { class: ["tool-result", it.error ? "error" : ""] }, it.text);
    if (it.kind === "user") return h("div", { class: "bubble agent" }, h("div", { class: "xs faint" }, "Prompt / feedback"), h("div", { class: "small" }, it.text.slice(0, 600)));
    return null;
  });
}

function rawMd(text) {
  const div = h("div", {});
  div.innerHTML = markdown(text);
  return div;
}

async function renderOutput(body, detail, e) {
  if (!e.output) return mount(body, empty("file", "No output file", "This step has no output file."));
  mount(body, h("div", { class: "skeleton", style: { height: "120px" } }));
  try {
    const { data } = await get(`/api/jobs/${detail.job.id}/artifact?path=${encodeURIComponent(e.output)}`, { fresh: true });
    const md = h("div", { class: "md" });
    md.innerHTML = markdown(data.text);
    mount(body, md);
  } catch (err) {
    mount(body, empty("file", "Not written yet", e.status === "issued" ? "The agent writes this file when it finishes." : err.message));
  }
}

function openOrchestrator(ctx, detail) {
  const msgs = (detail.messages || []).filter((m) => m.to.role === "orchestrator");
  const textarea = h("textarea", { class: "textarea", rows: 3, "data-autofocus": true, placeholder: "e.g. Pause after this review round; I want to look at the diff." });
  openDrawer((panel) => mount(panel,
    h("div", { class: "drawer-head" },
      h("div", { class: "attention-icon" }, icon("message", "icon icon-lg")),
      h("div", { class: "stack", style: { gap: "4px" } }, h("span", { class: "eyebrow" }, detail.job.id), h("h2", { class: "h2" }, "Orchestrator")),
      h("button", { class: "btn btn-ghost btn-icon", type: "button", "aria-label": "Close", onclick: () => closeDrawer() }, icon("x"))),
    h("div", { class: "drawer-sub small muted" }, "The Claude Code session running /factory for this job. It receives your message the next time it asks the engine what to do, and its Stop hook surfaces it if it is about to stop."),
    h("div", { class: "drawer-body" }, msgs.length ? h("div", { class: "convo" }, msgs.map((m) =>
      h("div", { class: "bubble user" }, m.text, h("div", { class: "meta" }, clock(m.ts), " · ", m.status === "delivered" ? `Delivered ${clock(m.delivered?.ts)}` : "Queued"))))
      : empty("message", "No messages yet", "Steer the whole job: pause, skip, change priorities.")),
    h("div", { class: "drawer-foot" }, h("div", { class: "composer" }, h("div", { class: "composer-row" }, textarea,
      h("button", { class: "btn btn-primary", type: "button", onclick: async () => {
        const text = textarea.value.trim();
        if (!text) return;
        try {
          await post(`/api/jobs/${detail.job.id}/messages`, { to: { role: "orchestrator" }, text });
          toast("Queued for the orchestrator");
          closeDrawer();
          ctx.refresh();
        } catch (err) { toast(err.message, "error"); }
      } }, icon("send"), "Send"))))), { label: "Message the orchestrator" });
}
