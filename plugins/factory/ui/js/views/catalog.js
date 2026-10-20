// Catalog: the agent crew by domain (with their "mod" avatars and full contracts) and the workflows.
import { h, mount } from "../dom.js";
import { icon } from "../icons.js";
import { avatar } from "../avatars.js";
import { closeDrawer, copyField, empty, openDrawer, workflowIcon } from "../components.js";
import { markdown } from "../markdown.js";

const ORDER = ["Purpose and scope", "Not responsible for", "Inputs", "Output contract", "Working rules", "Verification",
  "Security constraints", "Errors and escalation", "Handoff", "Success criteria"];

export function renderCatalog(root, ctx, route) {
  const cat = ctx.catalog;
  if (!cat) return mount(root, h("div", { class: "card" }, empty("users", "Loading the catalog…")));
  const tab = route.params?.view === "workflows" ? "workflows" : "agents";
  const filter = { q: route.params?.q || "", domain: route.params?.domain || "" };
  const results = h("div", { class: "stack-lg" });
  const draw = () => mount(results, tab === "agents" ? agents(cat, filter, ctx) : workflows(cat, ctx));
  const search = h("input", { class: "input", id: "catalog-search", type: "search", placeholder: "Search agents, skills, tools…",
    value: filter.q, style: { maxWidth: "320px" }, oninput: (e) => { filter.q = e.target.value.toLowerCase(); draw(); } });
  mount(root, h("div", { class: "stack-lg" },
    h("div", { class: "hero" },
      h("div", { class: "stack" }, h("span", { class: "eyebrow" }, "Catalog"),
        h("h1", { class: "h1" }, `${cat.agents.length} specialists, ${cat.workflows.length} workflows`),
        h("p", { class: "muted", style: { margin: 0, maxWidth: "720px" } }, "Every agent has one job, an explicit contract and a file it hands to the next. Workflows compose them; the engine enforces the contracts.")),
      h("div", { class: "seg", role: "group", "aria-label": "Catalog view" },
        h("button", { type: "button", "aria-pressed": tab === "agents" ? "true" : "false", onclick: () => ctx.go("#/catalog") }, "Agents"),
        h("button", { type: "button", "aria-pressed": tab === "workflows" ? "true" : "false", onclick: () => ctx.go("#/catalog?view=workflows") }, "Workflows"))),
    tab === "agents" ? h("div", { class: "filter-bar" },
      h("label", { class: "sr-only", for: "catalog-search" }, "Search agents"), search,
      h("div", { class: "row-wrap", role: "group", "aria-label": "Domain" },
        [{ id: "", title: "All" }, ...cat.domains].map((d) => h("button", { type: "button", class: "btn btn-sm" + (filter.domain === d.id ? " btn-primary" : ""),
          "aria-pressed": filter.domain === d.id ? "true" : "false",
          onclick: () => { filter.domain = d.id; draw(); [...document.querySelectorAll('[aria-label="Domain"] button')].forEach((b) => {
            const on = b.textContent === d.title; b.classList.toggle("btn-primary", on); b.setAttribute("aria-pressed", on ? "true" : "false"); }); } }, d.title)))) : null,
    results));
  draw();
  if (route.agent) {
    const a = cat.agents.find((x) => x.name === route.agent);
    if (a) openAgentContract(a, ctx);
  }
}

function agents(cat, filter, ctx) {
  const match = (a) => (!filter.domain || a.domain === filter.domain) && (!filter.q ||
    [a.name, a.title, a.summary, a.tools.join(" "), a.workflows.join(" ")].join(" ").toLowerCase().includes(filter.q));
  const sections = cat.domains.map((d) => {
    const list = cat.agents.filter((a) => a.domain === d.id && match(a));
    if (!list.length) return null;
    return h("section", { class: "stack", "aria-labelledby": `dom-${d.id}` },
      h("div", { class: "domain-head" }, h("span", { class: "domain-swatch", style: { background: `hsl(${d.hue} 80% 58%)` } }),
        h("h2", { class: "h3", id: `dom-${d.id}` }, d.title), h("span", { class: "faint small" }, String(list.length))),
      h("div", { class: "grid-cards" }, list.map((a) => h("button", { class: "catalog-card", type: "button", "data-focus-key": `catalog:${a.name}`,
        onclick: () => ctx.go(`#/catalog/${a.name}`) },
        h("div", { class: "row" }, avatar(a, { size: "lg", mood: "idle" }),
          h("div", { class: "truncate" }, h("div", { style: { fontWeight: 650 } }, a.title), h("div", { class: "mono xs faint" }, a.name))),
        h("div", { class: "summary" }, a.summary),
        h("div", { class: "row-wrap" }, h("span", { class: "badge-model" }, a.model),
          a.workflows.slice(0, 4).map((w) => h("span", { class: "chip" }, w)),
          a.workflows.length > 4 ? h("span", { class: "chip" }, `+${a.workflows.length - 4}`) : null)))));
  }).filter(Boolean);
  return sections.length ? sections : [empty("search", "No agents match", "Try a different word or domain.")];
}

function workflows(cat, ctx) {
  return h("div", { class: "grid-cards", style: { gridTemplateColumns: "repeat(auto-fill, minmax(360px, 1fr))" } }, cat.workflows.map((w) =>
    h("article", { class: "card card-pad stack" },
      h("div", { class: "row" }, h("span", { class: "attention-icon", style: { color: "var(--accent)", background: "var(--accent-soft)" } }, icon(workflowIcon(w.id), "icon icon-lg")),
        h("div", {}, h("h2", { class: "h3" }, w.title), h("div", { class: "mono xs faint" }, w.id))),
      h("p", { class: "small muted", style: { margin: 0 } }, w.summary),
      h("p", { class: "xs", style: { margin: 0 } }, h("strong", {}, "Use for: "), w.use_when),
      pipeline(w, ctx),
      w.escalate_always ? h("div", { class: "warn-box" }, icon("shield"), "Always ends with a human: the factory never applies or deploys.") : null,
      copyField(w.id === "feature" ? "/factory <what you want>" : `/factory ${w.id} <what you want>`))));
}

export function pipeline(w, ctx) {
  const parts = [];
  w.stages.forEach((st, i) => {
    if (i) parts.push(h("span", { class: "wf-arrow", "aria-hidden": "true" }, "›"));
    let agentsList = [];
    if (st.kind === "parallel") agentsList = st.members.map((m) => m.agent);
    else if (st.kind === "panel") agentsList = st.always.map((k) => ctx.reviewAgent(k));
    else if (st.agent && !String(st.agent).startsWith("$")) agentsList = [st.agent];
    parts.push(h("span", { class: ["wf-stage", st.kind === "gate" ? "gate" : "", st.kind === "panel" ? "loop" : ""],
      title: st.kind === "panel" ? "Review panel; loops back to Build on changes" : st.when ? "Conditional" : st.title },
      agentsList.slice(0, 3).map((n) => avatar(ctx.agentMeta(n), { size: "xs", model: false, mood: "idle" })),
      st.title, st.when ? h("span", { class: "faint" }, "?") : null, st.kind === "panel" ? h("span", { class: "faint" }, "⟲") : null));
  });
  return h("div", { class: "wf-pipeline" }, parts);
}

function openAgentContract(a, ctx) {
  openDrawer((panel) => {
    const body = h("div", { class: "drawer-body" });
    const sections = ORDER.filter((k) => a.contract?.[k]).map((k) => {
      const md = h("div", { class: "md" });
      md.innerHTML = markdown(a.contract[k]);
      return h("section", { class: "stack", style: { gap: "6px" } }, h("h3", { class: "h3" }, k), md);
    });
    mount(body, h("div", { class: "stack-lg" },
      h("p", { class: "muted", style: { margin: 0 } }, a.summary),
      h("div", { class: "stack", style: { gap: "6px" } },
        h("span", { class: "label" }, "Tools"), h("div", { class: "row-wrap" }, a.tools.map((t) => h("span", { class: "chip mono" }, t)))),
      h("div", { class: "stack", style: { gap: "6px" } },
        h("span", { class: "label" }, "Writes"), h("div", { class: "row-wrap" }, a.outputs.map((o) => h("span", { class: "chip" }, ctx.catalog.artifacts?.[o]?.label || o)))),
      a.workflows.length ? h("div", { class: "stack", style: { gap: "6px" } },
        h("span", { class: "label" }, "Used in"), h("div", { class: "row-wrap" }, a.workflows.map((w) => h("span", { class: "chip" }, w)))) : null,
      sections.length ? sections : h("p", { class: "faint small" }, "The contract file for this agent is not generated yet.")));
    mount(panel,
      h("div", { class: "drawer-head" }, avatar(a, { size: "xl", mood: "happy" }),
        h("div", { class: "stack", style: { gap: "4px" } }, h("span", { class: "eyebrow" }, a.domain), h("h2", { class: "h2" }, a.title),
          h("div", { class: "row-wrap" }, h("span", { class: "mono xs faint" }, `factory:${a.name}`), h("span", { class: "badge-model" }, a.model))),
        h("button", { class: "btn btn-ghost btn-icon", type: "button", "aria-label": "Close", "data-autofocus": true, onclick: () => closeDrawer() }, icon("x"))),
      h("div", { class: "drawer-sub small muted" }, a.was ? `Evolved from the original factory's ${a.was}.` : "New in Software Factory v2."),
      body);
  }, { label: `${a.title} contract`, onClose: () => {
    if (location.hash.startsWith(`#/catalog/${a.name}`)) ctx.go("#/catalog", { replace: true, quiet: true });
  } });
}
