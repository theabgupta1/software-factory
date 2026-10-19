// Factory Console application shell: routing, polling, sidebar, keyboard shortcuts and theme.
import { h, mount } from "./dom.js";
import { icon } from "./icons.js";
import { factoryMark, avatar } from "./avatars.js";
import { ApiError, MODE, currentProject, get, poll, setProject } from "./api.js";
import { ago } from "./format.js";
import { closeDrawer, drawerIsOpen, errorBox, skeleton, statusPill, empty } from "./components.js";
import { renderHome } from "./views/home.js";
import { renderJob, TABS } from "./views/job.js";
import { agentDrawerStep, updateAgentDrawer } from "./views/crew.js";
import { renderCatalog } from "./views/catalog.js";
import { renderSettings } from "./views/settings.js";

const app = {
  state: null,
  catalog: null,
  settings: null,
  detail: null,
  route: null,
  lastOk: null,
  error: null,
  stopDetail: null,
  stopSettings: null,
  jobKey: null,
};

// ---------- routing ----------
export function parseRoute(hash) {
  const raw = (hash || "").replace(/^#/, "") || "/";
  const [path, query = ""] = raw.split("?");
  const params = Object.fromEntries(new URLSearchParams(query));
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  if (parts[0] === "jobs" && parts[1]) return { name: "job", id: parts[1], tab: parts[2] || "overview", params };
  if (parts[0] === "catalog") return { name: "catalog", agent: parts[1] || null, params };
  if (parts[0] === "settings") return { name: "settings", params };
  return { name: "home", params };
}

let quietNav = false;
function go(hash, { replace = false, quiet = false } = {}) {
  if (location.hash === hash) return;
  quietNav = quiet;
  if (replace) { history.replaceState(null, "", hash); onRoute(); }
  else location.hash = hash;
}

// ---------- context passed to views ----------
const ctx = {
  go,
  refresh: () => { refreshState(); if (app.route?.name === "job") refreshDetail(); },
  get state() { return app.state; },
  get catalog() { return app.catalog; },
  agentMeta(name) {
    const a = app.catalog?.agents?.find((x) => x.name === name);
    return a || { name, title: name || "agent", hue: 220, emblem: "dot" };
  },
  reviewAgent(key) {
    return app.catalog?.agents?.find((a) => a.review_key === key)?.name || `${key}-reviewer`;
  },
  workflowTitle(id) { return app.catalog?.workflows?.find((w) => w.id === id)?.title || id; },
  agentDrawerFor(step) { return agentDrawerStep() === step; },
};

// ---------- layout ----------
const els = {};
function shell() {
  els.sidebar = h("nav", { class: "sidebar", "aria-label": "Primary" });
  els.crumbs = h("div", { class: "crumbs" });
  els.content = h("main", { class: "content", id: "main", tabindex: "-1" });
  els.banner = h("div", {});
  const topbar = h("header", { class: "topbar" },
    h("button", { class: "btn btn-ghost btn-icon menu-btn", type: "button", "aria-label": "Open navigation",
      onclick: () => document.body.classList.toggle("nav-open") }, icon("menu")),
    els.crumbs, h("div", { class: "spacer" }),
    h("a", { class: "btn btn-ghost btn-sm", href: "#/catalog" }, icon("users"), "Agents"));
  document.body.appendChild(h("a", { class: "skip-link", href: "#main" }, "Skip to content"));
  document.body.appendChild(h("div", { class: "shell" }, els.sidebar, h("div", { class: "main" }, topbar, els.content)));
  els.content.before(els.banner);
}

function renderSidebar() {
  const st = app.state;
  const route = app.route || {};
  const jobs = st?.jobs || [];
  const recent = jobs.filter((j) => !j.legacy).slice().sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || "")).slice(0, 8);
  const live = app.error ? "down" : app.lastOk && Date.now() - app.lastOk > 10000 ? "stale" : "";
  const projects = st?.projects || [];
  mount(els.sidebar,
    h("a", { class: "brand", href: "#/", "aria-label": "Factory Console home" }, factoryMark(),
      h("span", {}, h("span", { class: "brand-name" }, "Factory"), h("span", { class: "brand-sub" }, st?.repo?.name || "Console"))),
    projects.length > 1 ? h("div", { class: "field" }, h("label", { for: "project", class: "sr-only" }, "Project"),
      h("select", { class: "select project-switch", id: "project", onchange: (e) => { setProject(e.target.value === st.repo.root ? null : e.target.value); app.state = null; app.detail = null; go("#/"); refreshState(); } },
        projects.map((p) => h("option", { value: p.path, selected: p.path === (currentProject() || st.repo.root) }, p.name)))) : null,
    h("div", { class: "nav" },
      navLink("#/", "grid", "Command Center", route.name === "home", st?.attention || null),
      navLink("#/catalog", "users", "Agents & workflows", route.name === "catalog"),
      navLink("#/settings", "settings", "Settings", route.name === "settings")),
    recent.length ? h("div", { class: "nav" }, h("div", { class: "nav-label" }, "Jobs"),
      recent.map((j) => h("a", { href: `#/jobs/${j.id}`, "aria-current": route.name === "job" && route.id === j.id ? "page" : null, title: j.title },
        h("span", { class: ["tone-dot", j.attention ? "stop" : j.status === "running" ? "" : "good"], style: j.status === "running" ? { background: "var(--st-running)" } : {} }),
        h("span", { class: "truncate" }, j.title)))) : null,
    h("div", { class: "sidebar-foot" },
      h("div", { class: ["live", live].join(" "), role: "status", "aria-live": "polite" }, h("span", { class: "live-dot" }),
        app.error ? "Disconnected" : app.lastOk ? `Live · ${ago(new Date(app.lastOk).toISOString())}` : "Connecting…"),
      h("div", { class: "foot-actions" },
        h("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: toggleTheme, "aria-label": "Toggle colour theme" }, icon(isDark() ? "sun" : "moon"), isDark() ? "Light" : "Dark"),
        h("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: showShortcuts, "aria-label": "Keyboard shortcuts" }, icon("keyboard"), "Keys"))));
}

function navLink(href, iconName, label, current, count) {
  return h("a", { href, "aria-current": current ? "page" : null }, icon(iconName), label,
    count ? h("span", { class: "count", "aria-label": `${count} need you` }, String(count)) : null);
}

function renderCrumbs() {
  const r = app.route;
  const parts = [h("a", { href: "#/" }, "Factory")];
  if (r.name === "job") {
    const title = app.detail?.job?.title || r.id;
    parts.push(icon("chevron"), h("span", { class: "current" }, title));
  } else if (r.name === "catalog") {
    parts.push(icon("chevron"), r.agent ? h("a", { href: "#/catalog" }, "Agents & workflows") : h("span", { class: "current" }, "Agents & workflows"));
    if (r.agent) parts.push(icon("chevron"), h("span", { class: "current" }, ctx.agentMeta(r.agent).title));
  } else if (r.name === "settings") parts.push(icon("chevron"), h("span", { class: "current" }, "Settings"));
  else parts.push(icon("chevron"), h("span", { class: "current" }, "Command Center"));
  mount(els.crumbs, h("nav", { "aria-label": "Breadcrumb", class: "crumbs" }, parts));
}

function renderBanner() {
  if (MODE === "demo" || app.state?.demo) {
    mount(els.banner, h("div", { class: "content", style: { paddingBottom: 0 } }, h("div", { class: "demo-banner", role: "note" }, icon("info"),
      h("span", {}, h("strong", {}, "Demo data. "), "These jobs were produced by the real factory engine driven by scripted agents in a throwaway repo; agent wording, timings and token counts are illustrative."))));
  } else mount(els.banner);
}

// ---------- views ----------
function render() {
  const r = app.route;
  renderSidebar();
  renderCrumbs();
  renderBanner();
  if (!app.state && app.error) {
    return mount(els.content, errorBox(app.error.message, () => { app.error = null; refreshState(); }));
  }
  if (!app.state) return mount(els.content, skeleton(6));
  if (r.name === "home") {
    document.title = (app.state.attention ? `(${app.state.attention}) ` : "") + "Factory Console";
    return renderHome(els.content, ctx);
  }
  if (r.name === "catalog") { document.title = "Agents · Factory Console"; return renderCatalog(els.content, ctx, r); }
  if (r.name === "settings") { document.title = "Settings · Factory Console"; return renderSettings(els.content, ctx, app.settings); }
  if (r.name === "job") {
    if (app.detail && app.detail.job.id === r.id) {
      document.title = `${app.detail.job.id} · Factory Console`;
      try {
        renderJob(els.content, ctx, app.detail, r);
        updateAgentDrawer(app.detail);
      } catch (err) {
        console.error("Factory Console render error:", err);
        mount(els.content, errorBox(`This view failed to render: ${err.message}`, () => render()));
      }
    } else if (app.detailError) {
      mount(els.content, h("div", { class: "card" }, empty("alert", "Job not found", app.detailError, h("a", { class: "btn", href: "#/" }, "Back to the Command Center"))));
    } else mount(els.content, skeleton(8));
  }
}

function onRoute() {
  const prev = app.route;
  app.route = parseRoute(location.hash);
  document.body.classList.remove("nav-open");
  if (quietNav) { quietNav = false; renderSidebar(); return; }
  if (drawerIsOpen() && !(app.route.name === "job" && app.route.params?.step) && !(app.route.name === "catalog" && app.route.agent)) closeDrawer(true);
  if (app.route.name === "job") {
    if (app.detailFor !== app.route.id) startDetail(app.route.id);
  } else stopDetail();
  if (app.route.name === "settings" && !app.stopSettings) {
    app.stopSettings = poll("/api/settings", 30000, (d) => { app.settings = d; if (app.route.name === "settings") render(); }, () => {});
  }
  render();
  if (!prev || prev.name !== app.route.name || prev.id !== app.route.id) els.content.focus({ preventScroll: true });
}

// ---------- data ----------
async function refreshState() {
  try {
    const { data, changed } = await get("/api/state");
    app.lastOk = Date.now();
    const wasError = app.error;
    app.error = null;
    if (changed || wasError || !app.state) {
      const first = !app.state;
      app.state = data;
      if (first || wasError || app.route.name === "home") render(); else renderSidebar();
    } else renderSidebar();
  } catch (err) {
    app.error = err instanceof ApiError ? err : new ApiError(String(err), 0);
    if (!app.state) render(); else renderSidebar();
  }
}

function startDetail(id) {
  stopDetail();
  app.detailFor = id;
  app.detail = null;
  app.detailError = null;
  app.stopDetail = poll(`/api/jobs/${encodeURIComponent(id)}`, 2000, (data) => {
    app.detailError = null;
    if (app.route.name !== "job" || app.route.id !== id) return;
    const key = JSON.stringify([data.job.updated_at, data.crew.map((c) => [c.status, c.has_transcript, c.messages.length, c.messages.map((m) => m.status)]), data.artifacts.length, data.events.length]);
    const firstOrChanged = !app.detail || key !== app.jobKey;
    app.detail = data;
    app.jobKey = key;
    if (firstOrChanged) render();
  }, (err) => {
    app.detailError = err.message;
    if (!app.detail) render();
  });
}

function stopDetail() {
  if (app.stopDetail) app.stopDetail();
  app.stopDetail = null;
  app.detailFor = null;
}

// ---------- theme & keys ----------
function isDark() {
  const t = document.documentElement.dataset.theme;
  return t ? t === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
}
function toggleTheme() {
  const next = isDark() ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("factory.theme", next); } catch { /* ignore */ }
  renderSidebar();
}

function showShortcuts() {
  const rows = [["g h", "Command Center"], ["g a", "Agents & workflows"], ["g s", "Settings"], ["1 – 7", "Job tabs (on a job)"],
    ["/", "Search agents"], ["t", "Toggle theme"], ["Esc", "Close a panel"], ["?", "This help"]];
  const dlg = h("dialog", { class: "modal", "aria-label": "Keyboard shortcuts" },
    h("div", { class: "modal-body" }, h("h2", { class: "h2" }, "Keyboard shortcuts"),
      h("table", { class: "table" }, h("tbody", {}, rows.map(([k, d]) => h("tr", {}, h("td", {}, k.split(" ").map((x) => h("span", { class: "kbd" }, x))), h("td", {}, d))))),
      h("form", { method: "dialog" }, h("button", { class: "btn", type: "submit" }, "Close"))));
  document.body.appendChild(dlg);
  dlg.addEventListener("close", () => dlg.remove());
  dlg.showModal();
}

let pendingG = false;
document.addEventListener("keydown", (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const tag = (e.target.tagName || "").toLowerCase();
  if (["input", "textarea", "select"].includes(tag) || e.target.isContentEditable || document.querySelector("dialog[open]")) return;
  if (pendingG) {
    pendingG = false;
    const target = { h: "#/", a: "#/catalog", c: "#/catalog", s: "#/settings" }[e.key];
    if (target) { e.preventDefault(); go(target); }
    return;
  }
  if (e.key === "g") { pendingG = true; setTimeout(() => { pendingG = false; }, 1200); return; }
  if (e.key === "?") { e.preventDefault(); showShortcuts(); return; }
  if (e.key === "t") { toggleTheme(); return; }
  if (e.key === "/") {
    e.preventDefault();
    if (app.route.name !== "catalog") go("#/catalog");
    setTimeout(() => document.getElementById("catalog-search")?.focus(), 60);
    return;
  }
  if (app.route?.name === "job" && /^[1-7]$/.test(e.key) && !drawerIsOpen()) {
    const tab = TABS[Number(e.key) - 1];
    if (tab) go(`#/jobs/${app.route.id}/${tab.id}`);
  }
});

// ---------- boot ----------
async function loadCatalog() {
  try { app.catalog = (await get("/api/catalog")).data; render(); } catch { /* catalog shows its own empty state */ }
}

function boot() {
  try {
    const t = localStorage.getItem("factory.theme");
    if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
  } catch { /* ignore */ }
  shell();
  app.route = parseRoute(location.hash);
  window.addEventListener("hashchange", onRoute);
  onRoute();
  loadCatalog();
  refreshState();
  setInterval(refreshState, 2500);
  setInterval(renderSidebar, 15000);
}

boot();
