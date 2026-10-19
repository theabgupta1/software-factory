// Markdown → HTML for factory artifacts. Escapes everything first; only http(s), relative and anchor links are
// rendered; no raw HTML passes through. Supports headings, paragraphs, nested/ordered/task lists, tables, code
// fences, block quotes, rules and inline code/bold/italic/links.
import { esc } from "./dom.js";

function inline(src) {
  const codes = [];
  let s = esc(src).replace(/`([^`]+)`/g, (_, c) => `\u0000${codes.push(c) - 1}\u0000`);
  s = s
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*([^*\s][^*]*?)\*(?!\w)/g, "$1<em>$2</em>")
    .replace(/(^|[^\w])_([^_\s][^_]*?)_(?!\w)/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\(((?:https?:\/\/|\.{0,2}\/|#)[^)\s]*)\)/g,
      (_, text, href) => `<a href="${href}" target="_blank" rel="noopener noreferrer">${text}</a>`)
    .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, (_, pre, url) => `${pre}<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`);
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[Number(i)]}</code>`);
}

const LIST = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;

export function markdown(src, { skipHeader = false } = {}) {
  const lines = String(src || "").replace(/\r/g, "").split("\n");
  let i = 0;
  let out = "";
  if (skipHeader) {
    while (i < lines.length && !lines[i].trim()) i++;
    i++;
  }
  const isBlockStart = (l) => /^(#{1,6}\s|```|~~~|\s*>|\|)/.test(l) || LIST.test(l) || /^\s*(---|\*\*\*)\s*$/.test(l);
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    const fence = line.match(/^(```|~~~)\s*([\w+-]*)/);
    if (fence) {
      const body = [];
      i++;
      while (i < lines.length && !lines[i].startsWith(fence[1])) body.push(lines[i++]);
      i++;
      out += `<pre>${fence[2] ? `<span class="lang">${esc(fence[2])}</span>` : ""}<code>${esc(body.join("\n"))}</code></pre>`;
      continue;
    }
    if (/^\s*(---|\*\*\*)\s*$/.test(line)) { out += "<hr>"; i++; continue; }
    const hm = line.match(/^(#{1,6})\s+(.*?)\s*#*\s*$/);
    if (hm) {
      // The page owns the h1; document headings start one level down and keep their visual size via h-N.
      const n = Math.min(hm[1].length, 5);
      out += `<h${n + 1} class="h-${n}">${inline(hm[2])}</h${n + 1}>`;
      i++;
      continue;
    }
    if (/^\s*>/.test(line)) {
      const body = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) body.push(lines[i++].replace(/^\s*>\s?/, ""));
      out += `<blockquote>${markdown(body.join("\n"))}</blockquote>`;
      continue;
    }
    if (line.startsWith("|")) {
      const rows = [];
      while (i < lines.length && lines[i].startsWith("|")) rows.push(lines[i++]);
      const cells = (r) => r.replace(/^\||\|\s*$/g, "").split("|").map((c) => inline(c.trim()));
      const body = rows.filter((r) => !/^\|[\s:|-]+\|?\s*$/.test(r));
      out += "<table>" + body.map((r, n) => `<tr>${cells(r).map((c) => (n ? `<td>${c}</td>` : `<th>${c}</th>`)).join("")}</tr>`).join("") + "</table>";
      continue;
    }
    if (LIST.test(line)) {
      const ordered = /^\s*\d/.test(line);
      const items = [];
      while (i < lines.length && (LIST.test(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && items.length))) {
        const l = lines[i++];
        const m = l.match(LIST);
        if (m) items.push({ depth: Math.min(3, Math.floor(m[1].length / 2)), text: m[3] });
        else items[items.length - 1].text += " " + l.trim();
      }
      const lis = items.map(({ depth, text }) => {
        const task = text.match(/^\[([ xX])\]\s+(.*)$/);
        const cls = [depth ? `depth-${depth}` : "", task ? `task${task[1] !== " " ? " done" : ""}` : ""].filter(Boolean).join(" ");
        return `<li${cls ? ` class="${cls}"` : ""}>${inline(task ? task[2] : text)}</li>`;
      }).join("");
      out += ordered ? `<ol>${lis}</ol>` : `<ul>${lis}</ul>`;
      continue;
    }
    const para = [];
    while (i < lines.length && lines[i].trim() && !isBlockStart(lines[i])) para.push(lines[i++]);
    if (!para.length) { para.push(lines[i++]); }
    out += `<p>${inline(para.join(" "))}</p>`;
  }
  return out;
}
