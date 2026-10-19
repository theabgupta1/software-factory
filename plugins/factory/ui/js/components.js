// Shared UI components.
import { h, mount, clear } from "./dom.js";
import { icon } from "./icons.js";
import { STATUS, STEP_STATUS, VALUE } from "./format.js";

export function pill(cls, label, iconName) {
  return h("span", { class: ["pill", cls] }, iconName ? icon(iconName) : null, label);
}

export function statusPill(status) {
  const s = STATUS[status] || { label: status || "unknown", cls: "neutral", icon: "dot" };
  return pill(s.cls, s.label, s.icon);
}

export function stepPill(step) {
  if (step?.status === "done" && step.value && VALUE[step.value]) {
    const v = VALUE[step.value];
    return pill(v.cls, v.label, v.icon);
  }
  const s = STEP_STATUS[step?.status] || { label: step?.status || "—", cls: "neutral", icon: "dot" };
  return pill(s.cls, s.label, s.icon);
}

export function valuePill(value) {
  const v = VALUE[value];
  return v ? pill(v.cls, v.label, v.icon) : pill("neutral", value || "—");
}

export function verdictChips(reviews) {
  const entries = Object.entries(reviews || {});
  if (!entries.length) return null;
  return h("span", { class: "verdicts", "aria-label": "Review verdicts" },
    entries.map(([k, v]) => h("span", { class: ["vchip", v || "pending", v === "pending" ? "pulse" : ""], title: `${k}: ${v}` },
      v === "PASS" ? "✓" : v === "CHANGES" ? "!" : "…", " ", k)));
}

const WORKFLOW_ICON = { feature: "spark", fix: "bug", quick: "bolt", research: "telescope", review: "scan", data: "database",
  infra: "cloud", refactor: "refactor", perf: "gauge", security: "shield", docs: "book" };

export function workflowBadge(workflow, title) {
  return h("span", { class: "chip" }, icon(WORKFLOW_ICON[workflow] || "spark"), title || workflow);
}

export function workflowIcon(workflow) { return WORKFLOW_ICON[workflow] || "spark"; }

export function miniRail(stages) {
  return h("span", { class: "mini-rail", "aria-hidden": "true" }, (stages || []).map((st) => h("i", { class: st })));
}

let toastHost = null;
export function toast(message, kind = "info") {
  if (!toastHost) {
    toastHost = h("div", { class: "toasts", role: "status", "aria-live": "polite" });
    document.body.appendChild(toastHost);
  }
  const el = h("div", { class: ["toast", kind] }, icon(kind === "error" ? "alert" : "check"), message);
  toastHost.appendChild(el);
  setTimeout(() => el.remove(), 3200);
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Copied to clipboard");
    return true;
  } catch {
    const ta = h("textarea", { class: "sr-only" }, text);
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch { /* ignore */ }
    ta.remove();
    toast(ok ? "Copied to clipboard" : "Copy failed — select the text manually", ok ? "info" : "error");
    return ok;
  }
}

export function copyField(text, label = "Copy") {
  return h("div", { class: "copy-field" },
    h("code", { title: text }, text),
    h("button", { class: "btn btn-sm", type: "button", "aria-label": `${label}: ${text}`, onclick: () => copyText(text) }, icon("copy"), "Copy"));
}

export function empty(iconName, title, body, action) {
  return h("div", { class: "empty" },
    h("div", { class: "empty-icon" }, icon(iconName, "icon icon-lg")),
    h("h3", {}, title),
    body ? h("p", { class: "muted small" }, body) : null,
    action || null);
}

export function errorBox(message, retry) {
  return h("div", { class: "error-box", role: "alert" }, icon("alert"),
    h("div", { class: "stack" }, h("strong", {}, "Something went wrong"), h("span", { class: "small muted" }, message),
      retry ? h("div", {}, h("button", { class: "btn btn-sm", type: "button", onclick: retry }, "Try again")) : null));
}

export function skeleton(lines = 3) {
  return h("div", { class: "stack", "aria-busy": "true", "aria-label": "Loading" },
    Array.from({ length: lines }, (_, i) => h("div", { class: "skeleton", style: { height: i === 0 ? "28px" : "16px", width: `${90 - i * 12}%` } })));
}

// Tabs with roving focus (WAI-ARIA tabs pattern).
export function tabs(items, active, onSelect, idPrefix) {
  const list = h("div", { class: "tabs", role: "tablist", "aria-label": "Job sections" });
  items.forEach((item, idx) => {
    const selected = item.id === active;
    const btn = h("button", {
      class: "tab", role: "tab", type: "button", id: `${idPrefix}-tab-${item.id}`, "aria-selected": selected ? "true" : "false",
      "aria-controls": `${idPrefix}-panel`, tabindex: selected ? "0" : "-1",
      onclick: () => onSelect(item.id),
      onkeydown: (e) => {
        const dir = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
        if (e.key === "Home" || e.key === "End" || dir) {
          e.preventDefault();
          const next = e.key === "Home" ? 0 : e.key === "End" ? items.length - 1 : (idx + dir + items.length) % items.length;
          onSelect(items[next].id, true);
        }
      },
    }, item.icon ? icon(item.icon) : null, item.label, item.count ? h("span", { class: "count" }, String(item.count)) : null);
    list.appendChild(btn);
  });
  return list;
}

// Drawer (modal dialog) with focus trap and focus return.
let drawerState = null;
export function openDrawer(build, { label = "Details", onClose } = {}) {
  closeDrawer(true);
  const previous = document.activeElement;
  const scrim = h("div", { class: "scrim", onclick: () => closeDrawer() });
  const panel = h("aside", { class: "drawer", role: "dialog", "aria-modal": "true", "aria-label": label, tabindex: "-1" });
  const host = h("div", { class: "drawer-host" }, scrim, panel);
  document.body.appendChild(host);
  build(panel);
  requestAnimationFrame(() => {
    document.body.classList.add("drawer-open");
    (panel.querySelector("[data-autofocus]") || panel).focus();
  });
  const onKey = (e) => {
    if (e.key === "Escape") { e.preventDefault(); closeDrawer(); return; }
    if (e.key !== "Tab") return;
    const focusables = [...panel.querySelectorAll('a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])')]
      .filter((el) => el.offsetParent !== null);
    if (!focusables.length) return;
    const first = focusables[0], last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };
  document.addEventListener("keydown", onKey, true);
  drawerState = { host, panel, previous, onKey, onClose };
  return panel;
}

export function closeDrawer(immediate = false) {
  if (!drawerState) return;
  const { host, previous, onKey, onClose } = drawerState;
  drawerState = null;
  document.removeEventListener("keydown", onKey, true);
  document.body.classList.remove("drawer-open");
  const done = () => host.remove();
  if (immediate) done(); else setTimeout(done, 240);
  if (onClose) onClose();
  if (previous && previous.focus) previous.focus();
}

export function drawerIsOpen() { return Boolean(drawerState); }

export function section(title, body, extra) {
  return h("section", { class: "stack" },
    h("div", { class: "section-head" }, h("h2", { class: "h3" }, title), extra || null), body);
}

export function setContent(el, ...children) { return mount(el, ...children); }
export { clear };
