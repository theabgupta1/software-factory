// Unit tests for the console's pure UI logic (Node's built-in test runner; no browser).
import { test } from "node:test";
import assert from "node:assert/strict";
import { markdown } from "../../plugins/factory/ui/js/markdown.js";
import { duration, stepLabel, tokens, humanEvent } from "../../plugins/factory/ui/js/format.js";
import { parseDiff } from "../../plugins/factory/ui/js/views/changes.js";
import { commandFor } from "../../plugins/factory/ui/js/views/home.js";
import { parseRoute } from "../../plugins/factory/ui/js/route.js";

test("markdown escapes HTML and script injection", () => {
  const html = markdown('# Title <img src=x onerror=alert(1)>\n\n<script>alert(1)</script>\n\n`<b>`');
  assert.ok(!html.includes("<script>"));
  assert.ok(!html.includes("<img"));
  assert.ok(html.includes("&lt;script&gt;"));
  assert.ok(html.includes("<code>&lt;b&gt;</code>"));
});

test("markdown only renders safe links", () => {
  const html = markdown("[x](javascript:alert(1)) [y](https://example.com/a?b=1&c=2) [z](./spec.md)");
  assert.ok(!html.includes('href="javascript'));
  assert.ok(html.includes('href="https://example.com/a?b=1&amp;c=2"'));
  assert.ok(html.includes('href="./spec.md"'));
  assert.ok(html.includes('rel="noopener noreferrer"'));
});

test("markdown attribute breakout is impossible", () => {
  const html = markdown('[x](https://a.com/"onmouseover="alert(1))');
  assert.ok(!/onmouseover="/.test(html));
});

test("markdown links cannot nest inside an href or go protocol-relative", () => {
  const html = markdown("[a](http://x/(http://y/onmouseover=alert(1)//) and [b](//evil.example/x) and see https://ok.example/p");
  assert.equal((html.match(/<a /g) || []).length, 2, html);
  assert.ok(!/<a[^>]*\sonmouseover=/.test(html), html);
  assert.ok(!html.includes('href="//'), html);
  assert.ok(html.includes('href="https://ok.example/p"'), html);
  const nested = markdown("[**bold** `code`](https://a.example/x_y_z) _em_");
  assert.ok(nested.includes('href="https://a.example/x_y_z"'), nested);
  assert.ok(nested.includes("<em>em</em>"), nested);
});

test("markdown structures: tables, task lists, fences, headers skipped", () => {
  const html = markdown("VERDICT: PASS\n\n## Findings\n- [x] done\n- [ ] todo\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```js\nx < 1\n```", { skipHeader: true });
  assert.ok(!html.includes("VERDICT"));
  assert.match(html, /<li class="task done">done<\/li>/);
  assert.match(html, /<th>a<\/th>/);
  assert.match(html, /<pre><span class="lang">js<\/span><code>x &lt; 1<\/code><\/pre>/);
});

test("format helpers", () => {
  assert.equal(duration(5), "5s");
  assert.equal(duration(125), "2m 05s");
  assert.equal(duration(7260), "2h 01m");
  assert.equal(duration(null), "—");
  assert.equal(tokens(1234), "1.2k");
  assert.equal(tokens(2_500_000), "2.5M");
  assert.equal(stepLabel("review.r2/security"), "Review · round 2 · security");
  assert.equal(stepLabel("build.r1/builder"), "Build · round 1");
  assert.equal(stepLabel("task/T3"), "Task T3");
  assert.match(humanEvent({ type: "stage.looped", changes: ["security"], to: "build", round: 2 }), /security/);
});

test("parseDiff tracks line numbers", () => {
  const files = parseDiff("diff --git a/x.py b/x.py\nindex 1..2\n--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,3 @@\n a\n-b\n+c\n+d\n");
  assert.equal(files.length, 1);
  assert.equal(files[0].path, "x.py");
  const kinds = files[0].lines.map((l) => l.t);
  assert.deepEqual(kinds, ["hunk", "ctx", "del", "add", "add"]);
  assert.equal(files[0].lines[3].n, 2);
});

test("composer builds the exact /factory command", () => {
  assert.equal(commandFor({ text: "  Fix   checkout ", workflow: "fix", size: "S" }), "/factory fix --size S Fix checkout");
  assert.equal(commandFor({ text: "Add x", workflow: "auto", size: "auto" }), "/factory Add x");
  assert.equal(commandFor({ text: "Tune q", workflow: "quick", size: "auto", agent: "sql-engineer" }), "/factory quick --agent sql-engineer Tune q");
  assert.equal(commandFor({ text: "", workflow: "auto", size: "auto" }), "/factory <describe the outcome>");
});

test("routes parse", () => {
  assert.deepEqual(parseRoute("#/jobs/004-x/crew?step=review.r2%2Fux"), { name: "job", id: "004-x", tab: "crew", params: { step: "review.r2/ux" } });
  assert.deepEqual(parseRoute("#/catalog/sql-engineer"), { name: "catalog", agent: "sql-engineer", params: {} });
  assert.deepEqual(parseRoute(""), { name: "home", params: {} });
});
