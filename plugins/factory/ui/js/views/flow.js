// Flow: the job's real execution as a graph. Columns run left to right in execution order; agents that ran in
// parallel share a column; a review round asking for changes loops into the next build round (dashed amber).
// Stages that haven't run yet are drawn as dashed "upcoming" ghosts from the workflow definition.
import { h, mount, s } from "../dom.js";
import { avatarSvg, moodFor } from "../avatars.js";
import { stepLabel } from "../format.js";
import { icon } from "../icons.js";
import { openAgent } from "./crew.js";

const COL_W = 172, NODE_H = 56, GAP_Y = 12, GAP_X = 44, PAD = 20, LABEL_H = 26;
let fit = false;

export function buildColumns(detail, reviewAgent) {
  const { job } = detail;
  const steps = job.steps || {};
  const crewByStep = Object.fromEntries((detail.crew || []).map((c) => [c.step, c]));
  const cols = [];
  const stageState = Object.fromEntries((detail.stages || []).map((s) => [s.id, s.state]));
  const node = (id, agent, label, sub, state, extra = {}) => ({ id, agent, label, sub, state, ...extra });
  const fromStep = (sid, fallbackAgent, label) => {
    const st = steps[sid];
    const c = crewByStep[sid];
    if (!st) return node(sid, fallbackAgent, label, "upcoming", "upcoming", { ghost: true });
    const state = st.status === "issued" ? "running" : st.status === "done"
      ? (st.value === "CHANGES" || st.value === "PARTIAL" ? "changes" : st.value === "ESCALATE" || st.value === "BLOCKED" ? "attention" : "pass")
      : st.status === "invalid" || st.status === "failed" ? "blocked" : "upcoming";
    return node(sid, st.agent || fallbackAgent, label, st.value || (st.status === "issued" ? "working" : st.status), state, { crew: c, step: st });
  };

  for (const stage of detail.workflow?.stages || []) {
    const sState = stageState[stage.id];
    if (sState === "skipped") {
      cols.push({ title: stage.title, nodes: [node(`skip:${stage.id}`, stage.agent, stage.title, "skipped", "skipped", { ghost: true })] });
      continue;
    }
    if (stage.kind === "setup") {
      cols.push({ title: "Setup", nodes: [node("setup", null, "Worktree", job.base_sha ? "ready" : "pending", job.base_sha ? "pass" : "upcoming", { system: true })] });
    } else if (stage.kind === "agent" || stage.kind === "approve") {
      cols.push({ title: stage.title, nodes: [fromStep(stage.id, stage.agent, null)] });
    } else if (stage.kind === "parallel") {
      cols.push({ title: stage.title, nodes: stage.members.map((m) => fromStep(`${stage.id}/${m.key}`, m.agent, null)).filter((n) => !(n.ghost && sState === "done")) });
    } else if (stage.kind === "build") {
      const order = job.plan?.order || [];
      order.forEach((tid) => cols.push({ title: `Task ${tid}`, nodes: [fromStep(`task/${tid}`, job.plan.tasks?.[tid]?.agent, null)] }));
      const panelStage = (detail.workflow.stages || []).find((s2) => s2.kind === "panel");
      const rounds = Math.max((job.rounds || []).length, Object.keys(steps).filter((k) => k.startsWith("build.r")).length, 1);
      for (let n = 1; n <= rounds; n++) {
        cols.push({ title: rounds > 1 ? `Build · R${n}` : "Build", nodes: [fromStep(`build.r${n}/builder`, stage.agent?.startsWith("$") ? "builder" : stage.agent || "builder", null)] });
        if (panelStage) {
          const rnd = (job.rounds || []).find((r) => r.n === n);
          const keys = rnd ? rnd.members : panelStage.always;
          cols.push({ title: `Review · R${n}`, review: n, loopNext: rnd?.result === "changes",
            nodes: keys.map((k) => fromStep(`review.r${n}/${k}`, reviewAgent(k), null)) });
        }
      }
    } else if (stage.kind === "panel") {
      continue; // drawn with the build rounds above
    } else if (stage.kind === "gate") {
      const g = job.gates?.[stage.id];
      const waiting = job.human?.kind === "plan-approval" && job.human?.stage === stage.id;
      cols.push({ title: "Gate", nodes: [node(stage.id, null, "Your approval", g ? "approved" : waiting ? "waiting" : "upcoming",
        g ? "pass" : waiting ? "attention" : "upcoming", { system: true, gate: true })] });
    } else if (stage.kind === "merge" || stage.kind === "deliver") {
      const fin = job.status === "merged" || job.status === "delivered";
      const blocked = ["merge-busy", "merge-conflict"].includes(job.human?.kind);
      cols.push({ title: stage.title, nodes: [node(stage.id, null, stage.kind === "merge" ? `Merge → ${job.base_branch || "main"}` : "Deliver",
        fin ? (job.merge?.sha || "").slice(0, 8) || "done" : blocked ? "waiting" : "upcoming", fin ? "pass" : blocked ? "attention" : "upcoming", { system: true })] });
    }
  }
  return cols;
}

export function renderFlow(root, ctx, detail) {
  const cols = buildColumns(detail, (k) => ctx.reviewAgent(k));
  const maxNodes = Math.max(1, ...cols.map((c) => c.nodes.length));
  const height = PAD * 2 + LABEL_H + maxNodes * NODE_H + (maxNodes - 1) * GAP_Y + 18;
  const width = PAD * 2 + cols.length * COL_W + (cols.length - 1) * GAP_X;
  const svg = s("svg", { class: "flow-svg", width, height, viewBox: `0 0 ${width} ${height}`, role: "group", "aria-label": "Job execution graph" });
  const pos = [];
  cols.forEach((col, ci) => {
    const x = PAD + ci * (COL_W + GAP_X);
    const colH = col.nodes.length * NODE_H + (col.nodes.length - 1) * GAP_Y;
    const y0 = PAD + LABEL_H + (maxNodes * NODE_H + (maxNodes - 1) * GAP_Y - colH) / 2;
    svg.appendChild(s("text", { class: "flow-col-label", x, y: PAD + 8 }, col.title));
    pos.push(col.nodes.map((n, ni) => ({ x, y: y0 + ni * (NODE_H + GAP_Y), n })));
  });
  // Edges first so nodes sit on top.
  for (let i = 0; i < pos.length - 1; i++) {
    const a = pos[i], b = pos[i + 1];
    const loop = cols[i].loopNext;
    const active = b.some((p) => p.n.state === "running");
    const cls = ["flow-edge", loop ? "loop" : "", active ? "active" : ""].join(" ");
    const curve = (x1, y1, x2, y2) => `M${x1} ${y1} C${x1 + GAP_X / 2} ${y1}, ${x2 - GAP_X / 2} ${y2}, ${x2} ${y2}`;
    if (a.length > 1 && b.length > 1) {
      const jx = a[0].x + COL_W + GAP_X / 2;
      const jy = (Math.min(...a.map((p) => p.y)) + Math.max(...a.map((p) => p.y)) + NODE_H) / 2;
      a.forEach((p) => svg.appendChild(s("path", { class: cls, d: curve(p.x + COL_W, p.y + NODE_H / 2, jx, jy) })));
      b.forEach((p) => svg.appendChild(s("path", { class: cls, d: curve(jx, jy, p.x, p.y + NODE_H / 2) })));
    } else {
      a.forEach((p) => b.forEach((q) => svg.appendChild(s("path", { class: cls, d: curve(p.x + COL_W, p.y + NODE_H / 2, q.x, q.y + NODE_H / 2) }))));
    }
    if (loop) {
      const bottom = Math.max(...a.map((p) => p.y)) + NODE_H;
      svg.appendChild(s("text", { class: "flow-col-label", x: a[0].x + COL_W + 6, y: bottom + 14, fill: "var(--st-changes)" }, "changes ↺"));
    }
  }
  pos.flat().forEach(({ x, y, n }) => svg.appendChild(nodeEl(n, x, y, ctx, detail)));
  const statusCounts = cols.flatMap((c) => c.nodes).reduce((m, n) => ((m[n.state] = (m[n.state] || 0) + 1), m), {});
  const wrap = h("div", { class: "flow-wrap" }, svg);
  const applyFit = () => {
    svg.setAttribute("width", fit ? "100%" : String(width));
    svg.setAttribute("height", fit ? "auto" : String(height));
  };
  applyFit();
  mount(root, h("div", { class: "card card-pad stack" },
    h("div", { class: "row between", style: { flexWrap: "wrap" } },
      h("div", { class: "legend" },
        h("span", {}, h("i", {}), "sequence"), h("span", {}, h("i", { class: "loop" }), "changes requested → next round"),
        h("span", {}, icon("play"), `${statusCounts.running || 0} running`),
        h("span", {}, icon("clock"), `${statusCounts.upcoming || 0} upcoming`)),
      h("div", { class: "row" },
        h("span", { class: "xs faint" }, "Select an agent to open it; arrow keys move between nodes."),
        h("div", { class: "seg", role: "group", "aria-label": "Zoom" },
          h("button", { type: "button", "aria-pressed": fit ? "false" : "true", onclick: () => { fit = false; renderFlow(root, ctx, detail); } }, "Actual size"),
          h("button", { type: "button", "aria-pressed": fit ? "true" : "false", onclick: () => { fit = true; renderFlow(root, ctx, detail); } }, "Fit")))),
    wrap));
  // Bring the work in progress (or the decision waiting on you) into view.
  const focusNode = svg.querySelector(".flow-node.running, .flow-node.attention, .flow-node.blocked");
  if (focusNode && !fit) {
    const m = /translate\(([\d.]+)/.exec(focusNode.getAttribute("transform"));
    if (m) wrap.scrollLeft = Math.max(0, Number(m[1]) - wrap.clientWidth / 2 + COL_W / 2);
  }
}

function nodeEl(n, x, y, ctx, detail) {
  const meta = n.agent ? ctx.agentMeta(n.agent) : null;
  const title = n.label || meta?.title || n.agent || n.id;
  const sub = n.ghost ? n.sub : `${shortStep(n.id)} · ${n.sub}`;
  const g = s("g", { class: ["flow-node", n.state].join(" "), transform: `translate(${x} ${y})`, tabindex: "0", role: "button",
    "aria-label": `${title}, ${stepLabel(n.id)}: ${n.state}` });
  g.appendChild(s("rect", { class: "box", width: COL_W, height: NODE_H, rx: 12 }));
  if (meta) {
    g.appendChild(avatarSvg(meta, { mood: n.ghost ? "asleep" : moodFor(n.step), x: 10, y: 12, size: 32 }));
  } else {
    const glyph = s("g", { transform: "translate(16 18)", class: "icon", stroke: "currentColor", fill: "none", "stroke-width": 1.8, color: "var(--text-3)" });
    const ic = icon(n.gate ? "bell" : n.id === "setup" ? "branch" : "merge");
    [...ic.childNodes].forEach((c) => glyph.appendChild(c));
    g.appendChild(glyph);
  }
  g.appendChild(s("text", { x: 50, y: 25 }, truncate(title, 17)));
  g.appendChild(s("text", { class: "sub", x: 50, y: 41 }, truncate(sub, 22)));
  const dotColor = { running: "var(--st-running)", pass: "var(--st-pass)", changes: "var(--st-changes)", attention: "var(--st-attention)",
    blocked: "var(--st-blocked)" }[n.state];
  if (dotColor) g.appendChild(s("circle", { cx: COL_W - 12, cy: 12, r: 4, fill: dotColor, class: n.state === "running" ? "pulse" : "" }));
  g.appendChild(s("title", {}, `${title} — ${stepLabel(n.id)} (${n.state})`));
  const open = () => {
    if (n.crew) openAgent(ctx, detail, n.crew);
    else if (n.agent) ctx.go(`#/catalog/${n.agent}`);
  };
  g.addEventListener("click", open);
  g.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
    if (["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp"].includes(e.key)) {
      e.preventDefault();
      const all = [...g.ownerSVGElement.querySelectorAll(".flow-node")];
      const i = all.indexOf(g);
      const next = all[i + (e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : -1)];
      if (next) next.focus();
    }
  });
  return g;
}

function shortStep(id) {
  const m = id.match(/^review\.r(\d+)\/(.+)$/);
  if (m) return m[2];
  const b = id.match(/^build\.r(\d+)/);
  if (b) return `round ${b[1]}`;
  return id.replace(/^[\w-]+\//, "");
}

function truncate(t, n) { t = String(t || ""); return t.length > n ? t.slice(0, n - 1) + "…" : t; }
