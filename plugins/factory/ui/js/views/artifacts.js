// Artifacts: every file the job's agents wrote, grouped, with verdict tone and the step that produced it.
import { h, mount } from "../dom.js";
import { icon } from "../icons.js";
import { avatar } from "../avatars.js";
import { get } from "../api.js";
import { bytes, stepLabel } from "../format.js";
import { copyText, empty, errorBox } from "../components.js";
import { markdown } from "../markdown.js";

export function renderArtifacts(root, ctx, detail, route) {
  const files = detail.artifacts || [];
  if (!files.length) {
    return mount(root, h("div", { class: "card" }, empty("file", "No artifacts yet", "Specs, plans, reviews and decisions appear here as agents write them.")));
  }
  const selected = files.find((f) => f.path === route.params?.file) || preferred(files);
  const viewer = h("div", { class: "split-main", tabindex: "0", "aria-label": "Artifact" });
  let group = null;
  const nav = h("nav", { class: "split-nav", "aria-label": "Artifacts" }, files.map((f) => {
    const out = [];
    const g = f.group === "Rounds" ? `Round ${f.path.split("/")[0].replace("round-", "")}` : f.group;
    if (g !== group) { out.push(h("div", { class: "file-group" }, g)); group = g; }
    out.push(h("button", { class: "file-btn", type: "button", "aria-current": f.path === selected.path ? "true" : "false",
      onclick: () => ctx.go(`#/jobs/${detail.job.id}/artifacts?file=${encodeURIComponent(f.path)}`, { replace: true }) },
      h("span", { class: ["tone-dot", f.tone || ""] }), h("span", { class: "truncate" }, f.group === "Rounds" ? f.path.split("/")[1] : f.path)));
    return out;
  }));
  mount(root, h("div", { class: "card", style: { overflow: "hidden" } }, h("div", { class: "split" }, nav, viewer)));
  loadArtifact(viewer, ctx, detail, selected);
}

function preferred(files) {
  const order = ["final-report.md", "decision.md", "research.md", "report.md", "build.md", "spec.md"];
  for (const name of order) {
    const f = files.find((x) => x.path === name);
    if (f) return f;
  }
  return files[0];
}

async function loadArtifact(viewer, ctx, detail, file) {
  mount(viewer, h("div", { class: "skeleton", style: { height: "240px" } }));
  let data;
  try {
    ({ data } = await get(`/api/jobs/${detail.job.id}/artifact?path=${encodeURIComponent(file.path)}`, { fresh: true }));
  } catch (err) {
    return mount(viewer, errorBox(err.message));
  }
  const meta = file.agent ? ctx.agentMeta(file.agent) : null;
  const head = h("div", { class: "row between", style: { marginBottom: "16px", flexWrap: "wrap" } },
    h("div", { class: "row" },
      meta ? avatar(meta, { size: "sm", model: false }) : h("span", { class: "faint" }, icon("file")),
      h("div", {}, h("div", { class: "mono small" }, file.path),
        h("div", { class: "xs faint" }, meta ? `${meta.title} · ${stepLabel(file.step)}` : "written by the factory", ` · ${bytes(file.size)}`))),
    h("div", { class: "row" },
      file.step ? h("a", { class: "btn btn-sm", href: `#/jobs/${detail.job.id}/crew?step=${encodeURIComponent(file.step)}` }, icon("users"), "Open agent") : null,
      h("button", { class: "btn btn-sm", type: "button", onclick: () => copyText(data.text) }, icon("copy"), "Copy")));
  const body = h("div", {});
  if (file.path.endsWith(".json")) {
    let parsed = null;
    try { parsed = JSON.parse(data.text); } catch { /* show raw */ }
    if (parsed && Array.isArray(parsed.tasks)) body.appendChild(planTable(parsed, ctx));
    body.appendChild(h("pre", { class: "md", style: { maxWidth: "none" } }, h("code", {}, parsed ? JSON.stringify(parsed, null, 2) : data.text)));
  } else {
    const firstLine = data.text.split("\n").find((l) => l.trim()) || "";
    if (file.header) body.appendChild(h("div", { class: ["verdict-banner", file.tone || ""] }, firstLine.trim()));
    const md = h("div", { class: "md" });
    md.innerHTML = markdown(data.text, { skipHeader: Boolean(file.header) });
    body.appendChild(md);
  }
  if (data.truncated) body.appendChild(h("div", { class: "warn-box" }, icon("alert"), "This file is larger than 1 MB; only the beginning is shown."));
  mount(viewer, head, body);
}

function planTable(plan, ctx) {
  return h("div", { class: "table-wrap", style: { marginBottom: "16px" } }, h("table", { class: "table" },
    h("thead", {}, h("tr", {}, ["Task", "Agent", "Depends on", "Owns", "Acceptance"].map((t) => h("th", {}, t)))),
    h("tbody", {}, plan.tasks.map((t) => h("tr", {},
      h("td", {}, h("strong", {}, t.id), " ", t.title),
      h("td", {}, h("span", { class: "row" }, avatar(ctx.agentMeta(t.agent), { size: "xs", model: false }), ctx.agentMeta(t.agent).title || t.agent)),
      h("td", { class: "mono xs" }, (t.depends_on || []).join(", ") || "—"),
      h("td", { class: "mono xs" }, (t.owns || []).join(", ")),
      h("td", { class: "xs" }, (t.acceptance || []).join(", ") || "—"))))));
}
