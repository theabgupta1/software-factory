// Console API client: ETag-aware polling, a per-run write token, and project selection.

const TOKEN = document.querySelector('meta[name="factory-token"]')?.content || "";
export const MODE = document.querySelector('meta[name="factory-mode"]')?.content || "live";

const etags = new Map();
const cache = new Map();
let project = null;
try { project = localStorage.getItem("factory.project") || null; } catch { /* storage unavailable */ }

export function setProject(path) {
  project = path || null;
  etags.clear();
  cache.clear();
  try {
    if (project) localStorage.setItem("factory.project", project);
    else localStorage.removeItem("factory.project");
  } catch { /* ignore */ }
}

export function currentProject() { return project; }

function withProject(url) {
  if (!project) return url;
  return url + (url.includes("?") ? "&" : "?") + "project=" + encodeURIComponent(project);
}

export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

export async function get(url, { fresh = false } = {}) {
  const full = withProject(url);
  const headers = {};
  if (!fresh && etags.has(full)) headers["If-None-Match"] = etags.get(full);
  let res;
  try {
    res = await fetch(full, { headers, cache: "no-store" });
  } catch (err) {
    throw new ApiError("The console server is not reachable. Is `factoryctl serve` still running?", 0);
  }
  if (res.status === 304) return { data: cache.get(full), changed: false };
  let body = null;
  try { body = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) {
    if (res.status === 404 && project && url.startsWith("/api/state")) setProject(null);
    throw new ApiError(body?.error || `HTTP ${res.status}`, res.status);
  }
  const tag = res.headers.get("ETag");
  if (tag) etags.set(full, tag);
  cache.set(full, body);
  return { data: body, changed: true };
}

export async function post(url, payload) {
  let res;
  try {
    res = await fetch(withProject(url), {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Factory-Token": TOKEN },
      body: JSON.stringify(payload),
    });
  } catch {
    throw new ApiError("The console server is not reachable.", 0);
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(body.error || `HTTP ${res.status}`, res.status);
  return body;
}

// Poll `url` every `ms` while the page is visible; `onData(data, changed)` and `onError(err)`.
export function poll(url, ms, onData, onError) {
  let timer = null;
  let stopped = false;
  async function tick() {
    if (stopped) return;
    if (document.hidden) { timer = setTimeout(tick, ms); return; }
    let result = null;
    try {
      result = await get(url);
    } catch (err) {
      if (!stopped && onError) onError(err);
    }
    if (result && !stopped) {
      try {
        onData(result.data, result.changed);
      } catch (err) {
        console.error("Factory Console render error:", err);
        if (onError) onError(err);
      }
    }
    if (!stopped) timer = setTimeout(tick, ms);
  }
  tick();
  const onVis = () => { if (!document.hidden && !stopped) { clearTimeout(timer); tick(); } };
  document.addEventListener("visibilitychange", onVis);
  return () => { stopped = true; clearTimeout(timer); document.removeEventListener("visibilitychange", onVis); };
}
