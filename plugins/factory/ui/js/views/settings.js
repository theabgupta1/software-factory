// Settings & integrations: effective configuration, detected tools, safety policy and installation facts.
// Read-only by design: configuration lives in .factory/config.json in the repo, where it can be reviewed.
import { h, mount } from "../dom.js";
import { icon } from "../icons.js";
import { copyField, empty, errorBox, pill } from "../components.js";

export function renderSettings(root, ctx, data) {
  if (!data) return mount(root, h("div", { class: "card" }, empty("settings", "Loading settings…")));
  const cfg = data.config;
  const tools = data.doctor?.tools || [];
  const cats = [...new Set(tools.map((t) => t.category))];
  mount(root, h("div", { class: "stack-lg" },
    h("div", { class: "stack" }, h("span", { class: "eyebrow" }, "Settings & integrations"), h("h1", { class: "h1" }, "How this factory is set up"),
      h("p", { class: "muted", style: { margin: 0, maxWidth: "760px" } }, "Everything here is detected on this machine or read from your repository. Change behaviour by editing the config file; the console never writes it.")),
    data.config_error ? errorBox(data.config_error) : null,
    h("div", { class: "grid-2" },
      h("section", { class: "card card-pad stack" },
        h("div", { class: "section-head" }, h("h2", { class: "h3" }, "Repository configuration"),
          data.config_exists ? pill("pass", "custom", "check") : pill("neutral", "defaults", "dot")),
        data.config_path ? copyField(data.config_path, "Copy path") : null,
        cfg ? configTable(cfg, data.defaults) : h("p", { class: "faint small" }, "No repository is attached (demo or catalog only)."),
        h("p", { class: "hint", style: { margin: 0 } }, "All keys are optional. Unknown keys are rejected so typos never silently do nothing.")),
      h("section", { class: "card card-pad stack" },
        h("h2", { class: "h3" }, "Environment checks"),
        h("ul", { class: "md", style: { margin: 0 } }, (data.doctor?.checks || []).map((c) => h("li", { class: ["task", c.ok ? "done" : ""] },
          h("div", {}, h("strong", {}, c.check), h("div", { class: "xs muted" }, c.detail))))),
        h("div", { class: "stack", style: { gap: "6px" } }, h("span", { class: "label" }, "Plugin"),
          h("div", { class: "mono xs" }, data.plugin_root), h("div", { class: "xs faint" }, `version ${data.version} · hooks: ${(data.hooks || []).join(", ")}`)))),
    h("section", { class: "card card-pad stack" },
      h("div", { class: "section-head" }, h("h2", { class: "h3" }, "Integrations"),
        h("span", { class: "faint small" }, `${tools.filter((t) => t.available).length} of ${tools.length} available`)),
      h("p", { class: "small muted", style: { margin: 0 } }, "Agents use these when present and report “not run” when they are missing — nothing is faked."),
      h("div", { class: "table-wrap" }, h("table", { class: "table" },
        h("thead", {}, h("tr", {}, ["Tool", "Status", "Version", "Used for"].map((t) => h("th", {}, t)))),
        h("tbody", {}, cats.flatMap((cat) => [h("tr", {}, h("td", { colspan: "4", class: "eyebrow", style: { paddingTop: "14px" } }, cat)),
          ...tools.filter((t) => t.category === cat).map((t) => h("tr", {},
            h("td", { class: "mono" }, t.name),
            h("td", {}, t.available ? pill("pass", "available", "check") : pill("neutral", "not found", "dot")),
            h("td", { class: "mono xs" }, t.version || "—"),
            h("td", { class: "small muted" }, t.purpose)))]))))),
    h("section", { class: "card card-pad stack" },
      h("div", { class: "section-head" }, h("h2", { class: "h3" }, "Safety guard"),
        h("span", { class: "faint small" }, `scope: ${cfg?.guard?.scope || "factory"} agents`)),
      h("p", { class: "small muted", style: { margin: 0 } }, "A PreToolUse hook checks every shell command a factory agent runs. Deny rules block outright; ask rules make Claude Code ask you. It complements Claude Code's own permission prompts; it is not a sandbox."),
      h("div", { class: "table-wrap" }, h("table", { class: "table" },
        h("thead", {}, h("tr", {}, ["Rule", "Decision", "Why"].map((t) => h("th", {}, t)))),
        h("tbody", {}, (data.guard || []).map((r) => h("tr", {}, h("td", { class: "mono xs" }, r.id),
          h("td", {}, r.decision === "deny" ? pill("blocked", "deny", "stop") : pill("changes", "ask", "alert")),
          h("td", { class: "small" }, r.reason)))))))));
}

function configTable(cfg, defaults) {
  const rows = [];
  const walk = (obj, def, prefix) => {
    for (const [k, v] of Object.entries(obj)) {
      const key = prefix ? `${prefix}.${k}` : k;
      const d = def ? def[k] : undefined;
      if (v && typeof v === "object" && !Array.isArray(v) && Object.keys(v).length && key !== "models") { walk(v, d, key); continue; }
      const changed = JSON.stringify(v) !== JSON.stringify(d) && !(key === "base_branch");
      rows.push(h("tr", {}, h("td", { class: "mono xs" }, key),
        h("td", { class: "mono xs", style: { wordBreak: "break-word" } }, shorten(JSON.stringify(v))),
        h("td", {}, changed ? h("span", { class: "chip" }, "custom") : h("span", { class: "faint xs" }, key === "base_branch" ? "detected" : "default"))));
    }
  };
  walk(cfg, defaults, "");
  return h("div", { class: "table-wrap" }, h("table", { class: "table" }, h("tbody", {}, rows)));
}

function shorten(s) { return s && s.length > 140 ? s.slice(0, 139) + "…" : s; }
