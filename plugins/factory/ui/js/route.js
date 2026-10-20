// Hash routes: #/, #/jobs/<id>/<tab>?k=v, #/catalog[/<agent>], #/settings.
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
