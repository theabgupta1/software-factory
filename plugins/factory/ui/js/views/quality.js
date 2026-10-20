// Quality: review verdicts per round, acceptance criteria with evidence, recorded checks, open findings and risks.
// Everything is parsed from what the agents wrote; nothing here is inferred or estimated.
import { h, mount } from "../dom.js";
import { icon } from "../icons.js";
import { avatar } from "../avatars.js";
import { empty, pill, valuePill } from "../components.js";
import { markdown } from "../markdown.js";

function evidence(text) {
  const el = h("div", { class: "xs muted" });
  el.innerHTML = markdown(text);
  return el;
}

export function renderQuality(root, ctx, detail) {
  const q = detail.quality || {};
  const keys = [...new Set((q.matrix || []).flatMap((r) => Object.keys(r.verdicts || {})))];
  const met = (q.criteria || []).filter((c) => c.met).length;
  const failed = (q.checks || []).filter((c) => c.result === "fail").length;
  mount(root, h("div", { class: "stack-lg" },
    h("div", { class: "stats" },
      stat("Acceptance criteria", q.criteria?.length ? `${met} / ${q.criteria.length} met` : "not reported"),
      stat("Checks recorded", q.checks?.length ? `${q.checks.length - failed} pass · ${failed} fail` : "none recorded"),
      stat("Open findings", String((q.open_findings || []).length)),
      stat("Decision", q.decision || "pending")),
    h("section", { class: "card card-pad stack" },
      h("h2", { class: "h3" }, "Review verdicts"),
      keys.length ? h("div", { class: "table-wrap" }, h("table", { class: "table" },
        h("thead", {}, h("tr", {}, h("th", {}, "Round"), keys.map((k) => h("th", {}, h("span", { class: "row" },
          avatar(ctx.agentMeta(ctx.reviewAgent(k)), { size: "xs", model: false }), k))), h("th", {}, "Result"))),
        h("tbody", {}, q.matrix.map((r) => h("tr", {}, h("td", {}, `R${r.round}`),
          keys.map((k) => h("td", {}, r.verdicts?.[k] ? (r.verdicts[k] === "pending" || r.verdicts[k] === null ? pill("running", "pending", "play") : valuePill(r.verdicts[k])) : h("span", { class: "faint" }, "—"))),
          h("td", {}, r.result ? valuePill(r.result === "pass" ? "PASS" : "CHANGES") : pill("running", "in progress", "play")))))))
        : h("p", { class: "faint small" }, "No review round has started.")),
    h("div", { class: "grid-2" },
      h("section", { class: "card card-pad stack" },
        h("h2", { class: "h3" }, "Acceptance criteria"),
        q.criteria?.length ? h("ul", { class: "md", style: { margin: 0 } }, q.criteria.map((c) => h("li", { class: ["task", c.met ? "done" : ""] },
          h("div", {}, h("strong", {}, c.criterion), c.evidence ? evidence(c.evidence) : null))))
          : h("p", { class: "faint small" }, "build.md has not reported acceptance criteria yet.")),
      h("section", { class: "card card-pad stack" },
        h("h2", { class: "h3" }, "Open findings"),
        q.open_findings?.length ? h("ol", { class: "md", style: { margin: 0 } }, q.open_findings.map((f) => h("li", {},
          h("span", { class: "chip" }, f.reviewer), " ", f.text)))
          : h("p", { class: "faint small" }, "No blocking findings in the latest round."))),
    h("section", { class: "card card-pad stack" },
      h("h2", { class: "h3" }, "Checks run by agents"),
      q.checks?.length ? h("div", { class: "table-wrap" }, h("table", { class: "table" },
        h("thead", {}, h("tr", {}, ["Check", "Result", "Reported in", "Detail"].map((t) => h("th", {}, t)))),
        h("tbody", {}, q.checks.map((c) => h("tr", {},
          h("td", { class: "mono xs" }, c.check),
          h("td", {}, c.result === "pass" ? pill("pass", "pass", "check") : c.result === "fail" ? pill("fail", "fail", "x") : pill("neutral", "not run", "dot")),
          h("td", { class: "mono xs" }, c.source),
          h("td", { class: "xs muted" }, c.detail || ""))))))
        : empty("shield", "No checks recorded yet", "Agents list the exact commands they ran under “Validation” and “Checks”. Coverage appears only if a real tool reported it.")),
    (q.warnings || []).length ? h("section", { class: "card card-pad stack" }, h("h2", { class: "h3" }, "Risks and warnings"),
      q.warnings.map((w) => h("div", { class: "warn-box" }, icon("alert"), w))) : null));
}

function stat(label, value) {
  return h("div", { class: "stat" }, h("div", { class: "kpi-label" }, label), h("div", { class: "stat-value" }, value));
}
