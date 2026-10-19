// Timeline: a Gantt of every agent step from its real issue/finish timestamps, plus the filterable event log.
import { h, mount } from "../dom.js";
import { avatar, moodFor } from "../avatars.js";
import { clock, duration, eventIcon, humanEvent, parseTs, stepLabel, tokens } from "../format.js";
import { icon } from "../icons.js";
import { empty } from "../components.js";
import { openAgent } from "./crew.js";

const STEPS_MIN = [60, 120, 300, 600, 900, 1800, 3600, 7200, 14400, 28800];

export function renderTimeline(root, ctx, detail) {
  const rows = (detail.crew || []).filter((c) => c.issued_at).sort((a, b) => a.issued_at.localeCompare(b.issued_at));
  const now = new Date();
  const start = parseTs(detail.job.created_at) || (rows[0] && parseTs(rows[0].issued_at));
  let end = rows.reduce((m, r) => {
    const t = r.done_at ? parseTs(r.done_at) : now;
    return t && t > m ? t : m;
  }, start || now);
  if (["merged", "delivered", "cancelled"].includes(detail.job.status)) end = parseTs(detail.job.updated_at) || end;
  const span = Math.max(60, (end - start) / 1000);
  const pct = (t) => `${Math.min(100, Math.max(0, ((parseTs(t) - start) / 1000 / span) * 100))}%`;
  const tickStep = STEPS_MIN.find((s) => span / s <= 8) || 28800;
  const ticks = [];
  for (let t = 0; t <= span; t += tickStep) ticks.push(t);

  const gantt = rows.length && start ? h("div", { class: "gantt", role: "table", "aria-label": "Agent timeline" },
    h("div", { class: "gantt-label xs faint", role: "columnheader", style: { borderTop: 0 } }, "Agent · step"),
    h("div", { class: "gantt-axis", role: "columnheader" }, ticks.map((t) => h("div", { class: "gantt-tick", style: { left: `${(t / span) * 100}%` } },
      h("span", {}, t === 0 ? clock(detail.job.created_at) : `+${duration(t)}`)))),
    rows.flatMap((r) => {
      const meta = ctx.agentMeta(r.agent);
      const cls = r.status === "issued" ? "running" : r.value === "CHANGES" || r.value === "PARTIAL" ? "changes"
        : r.status === "invalid" || r.value === "BLOCKED" ? "blocked" : r.status === "done" ? "pass" : "neutral";
      const left = pct(r.issued_at);
      const right = r.done_at ? pct(r.done_at) : (r.status === "issued" ? "100%" : left);
      const bar = h("button", { class: ["gantt-bar", cls], type: "button", title: `${r.title} — ${stepLabel(r.step)}: ${duration(r.seconds)}`,
        "aria-label": `${r.title}, ${stepLabel(r.step)}, ${duration(r.seconds)}${r.usage?.total ? `, ${tokens(r.usage.total)} tokens` : ""}`,
        onclick: () => openAgent(ctx, detail, r) },
        h("span", {}, `${duration(r.seconds)}${r.usage?.total ? ` · ${tokens(r.usage.total)}` : ""}`));
      bar.style.left = left;
      bar.style.width = `calc(${right} - ${left})`;
      return [
        h("div", { class: "gantt-label", role: "cell" }, avatar(meta, { size: "xs", step: r, model: false }),
          h("span", { class: "truncate" }, h("strong", {}, meta.title || r.agent), h("span", { class: "faint" }, ` · ${stepLabel(r.step)}`))),
        h("div", { class: "gantt-track", role: "cell" }, h("div", { class: "gantt-grid" }, ticks.map((t) => h("div", { class: "gantt-tick", style: { left: `${(t / span) * 100}%` } }))), bar),
      ];
    })) : empty("bars", "Nothing on the timeline yet", detail.legacy ? "v1 jobs did not record timestamps." : "Agent runs appear here as they start.");

  const types = [...new Set((detail.events || []).map((e) => e.type))].sort();
  const filter = { q: "", type: "", all: false };
  const list = h("div", { class: "events", "aria-live": "off" });
  const draw = () => {
    const evs = (detail.events || []).filter((e) => (!filter.type || e.type === filter.type) &&
      (!filter.q || humanEvent(e).toLowerCase().includes(filter.q))).slice().reverse();
    const shown = filter.all ? evs : evs.slice(0, 50);
    mount(list, evs.length ? [...shown.map((ev) => h("div", { class: "event" }, h("time", { datetime: ev.ts }, clock(ev.ts)),
      h("span", { class: "ev-icon" }, icon(eventIcon(ev.type))), h("span", {}, humanEvent(ev), " ", h("span", { class: "chip mono" }, ev.type)))),
      evs.length > shown.length ? h("div", { style: { paddingTop: "12px" } }, h("button", { class: "btn btn-sm", type: "button",
        onclick: () => { filter.all = true; draw(); } }, `Show all ${evs.length} events`)) : null]
      : h("p", { class: "faint small" }, "No matching events."));
  };
  draw();
  mount(root, h("div", { class: "stack-lg" },
    h("section", { class: "card card-pad stack" },
      h("div", { class: "section-head" }, h("h2", { class: "h3" }, "Agent timeline"),
        h("span", { class: "xs faint" }, start ? `${duration(span)} from ${clock(detail.job.created_at)}` : "")),
      h("div", { class: "table-wrap" }, gantt)),
    h("section", { class: "card card-pad stack" },
      h("div", { class: "section-head" }, h("h2", { class: "h3" }, "Event log"),
        h("div", { class: "row" },
          h("label", { class: "sr-only", for: "ev-q" }, "Filter events"),
          h("input", { class: "input", id: "ev-q", type: "search", placeholder: "Filter…", style: { width: "180px" },
            oninput: (e) => { filter.q = e.target.value.toLowerCase(); draw(); } }),
          h("label", { class: "sr-only", for: "ev-type" }, "Event type"),
          h("select", { class: "select", id: "ev-type", style: { width: "190px" }, onchange: (e) => { filter.type = e.target.value; draw(); } },
            h("option", { value: "" }, "All event types"), types.map((t) => h("option", { value: t }, t))))),
      list)));
}
