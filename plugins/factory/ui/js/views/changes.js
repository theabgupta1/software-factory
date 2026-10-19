// Changes: commits and the diff of the job branch against its base, straight from git.
import { h, mount } from "../dom.js";
import { icon } from "../icons.js";
import { get } from "../api.js";
import { dateTime } from "../format.js";
import { empty, errorBox } from "../components.js";

const OPEN_LIMIT = 4;
const LINE_LIMIT = 1500;

export async function renderChanges(root, ctx, detail) {
  mount(root, h("div", { class: "card card-pad" }, h("div", { class: "skeleton", style: { height: "200px" } })));
  let data;
  try {
    ({ data } = await get(`/api/jobs/${detail.job.id}/diff`));
  } catch (err) {
    return mount(root, errorBox(err.message, () => renderChanges(root, ctx, detail)));
  }
  if (!data || !data.available) {
    return mount(root, h("div", { class: "card" }, empty("diff", "No diff available", data?.reason || "")));
  }
  const files = data.files || [];
  const parsed = parseDiff(data.patch || "");
  const totalAdd = files.reduce((n, f) => n + (f.added || 0), 0);
  const totalDel = files.reduce((n, f) => n + (f.removed || 0), 0);
  mount(root, h("div", { class: "stack-lg" },
    h("div", { class: "grid-2" },
      h("section", { class: "card" },
        h("div", { class: "card-head" }, h("h2", { class: "h3" }, "Commits"), h("span", { class: "faint small" }, String((data.commits || []).length))),
        (data.commits || []).length ? h("div", {}, data.commits.map((c) => h("div", { class: "job-row", style: { cursor: "default" } },
          h("div", { class: "truncate" }, h("div", { class: "truncate small", style: { fontWeight: 600 } }, c.subject),
            h("div", { class: "job-meta" }, h("code", {}, c.short), h("span", {}, c.author), h("span", {}, dateTime(c.date)))))))
          : h("div", { class: "card-body faint small" }, "No commits on the branch yet.")),
      h("section", { class: "card" },
        h("div", { class: "card-head" }, h("h2", { class: "h3" }, "Files"),
          h("span", { class: "small" }, h("span", { style: { color: "var(--st-pass)" } }, `+${totalAdd}`), " ", h("span", { style: { color: "var(--st-blocked)" } }, `−${totalDel}`))),
        h("div", { class: "card-body" }, files.length ? h("table", { class: "table" }, h("tbody", {}, files.map((f) => h("tr", {},
          h("td", { class: "mono xs" }, h("a", { href: `#diff-${anchor(f.path)}` }, f.path)),
          h("td", { class: "xs nowrap", style: { textAlign: "right" } }, f.binary ? "binary" : `+${f.added} −${f.removed}`),
          h("td", { style: { width: "60px" } }, stat(f))))))
          : h("p", { class: "faint small" }, "No file changes.")))),
    data.truncated ? h("div", { class: "warn-box" }, icon("alert"), "The diff is large; it was cut off at 1.5 MB.") : null,
    h("section", { class: "stack", "aria-label": "Diff" }, parsed.map((f, i) => fileDiff(f, i < OPEN_LIMIT)))));
}

function stat(f) {
  const total = (f.added || 0) + (f.removed || 0);
  const add = total ? Math.round(((f.added || 0) / total) * 5) : 0;
  return h("span", { class: "diffstat", "aria-hidden": "true" }, [0, 1, 2, 3, 4].map((i) => h("i", { class: f.binary ? "" : i < add ? "add" : total ? "del" : "" })));
}

function anchor(path) { return path.replace(/[^a-zA-Z0-9]+/g, "-"); }

export function parseDiff(patch) {
  const files = [];
  let cur = null;
  let oldNo = 0, newNo = 0;
  for (const line of patch.split("\n")) {
    if (line.startsWith("diff --git ")) {
      const m = line.match(/ b\/(.+)$/);
      cur = { path: m ? m[1] : line, lines: [] };
      files.push(cur);
      continue;
    }
    if (!cur) continue;
    if (line.startsWith("@@")) {
      const m = line.match(/@@ -(\d+)(?:,\d+)? \+(\d+)/);
      oldNo = m ? Number(m[1]) : 0;
      newNo = m ? Number(m[2]) : 0;
      cur.lines.push({ t: "hunk", text: line });
    } else if (line.startsWith("+++") || line.startsWith("---") || line.startsWith("index ") || line.startsWith("new file") || line.startsWith("deleted file")) {
      continue;
    } else if (line.startsWith("+")) {
      cur.lines.push({ t: "add", text: line.slice(1), n: newNo++ });
    } else if (line.startsWith("-")) {
      cur.lines.push({ t: "del", text: line.slice(1), o: oldNo++ });
    } else if (line.startsWith(" ")) {
      cur.lines.push({ t: "ctx", text: line.slice(1), o: oldNo++, n: newNo++ });
    }
  }
  return files;
}

function fileDiff(f, open) {
  const rows = f.lines.slice(0, LINE_LIMIT).map((l) => l.t === "hunk"
    ? h("tr", { class: "hunk" }, h("td", { colspan: "3" }, l.text))
    : h("tr", { class: l.t }, h("td", { class: "ln" }, l.o ?? ""), h("td", { class: "ln" }, l.n ?? ""),
      h("td", { class: "code" }, (l.t === "add" ? "+ " : l.t === "del" ? "− " : "  ") + l.text)));
  return h("details", { class: "diff-file", id: `diff-${anchor(f.path)}`, open },
    h("summary", {}, icon("file"), f.path, h("span", { class: "faint" }, `${f.lines.filter((l) => l.t === "add").length} added · ${f.lines.filter((l) => l.t === "del").length} removed`)),
    h("div", { class: "table-wrap" }, h("table", { class: "diff" }, h("tbody", {}, rows))),
    f.lines.length > LINE_LIMIT ? h("div", { class: "card-body faint small" }, `${f.lines.length - LINE_LIMIT} more lines not shown.`) : null);
}
