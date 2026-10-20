// Agent "mods": each agent's profile picture is a small robot module drawn in its own hue, wearing its role
// emblem. The face reflects what the agent is actually doing (from real step state): working (blinking eyes,
// pulsing antenna), happy (passed / done), concerned (changes requested / escalated), blocked, idle, asleep.
import { s, h, nextId } from "./dom.js";
import { iconPaths } from "./icons.js";

const MODEL_LETTER = { opus: "O", sonnet: "S", haiku: "H", inherit: "·" };

export function moodFor(step) {
  if (!step) return "idle";
  const st = step.status;
  if (st === "issued") return "working";
  if (st === "invalid" || st === "failed" || step.value === "BLOCKED") return "blocked";
  if (st === "lost") return "asleep";
  if (st === "upcoming" || st === "skipped") return "asleep";
  if (st === "done") {
    if (step.value === "CHANGES" || step.value === "ESCALATE" || step.value === "PARTIAL") return "concerned";
    return "happy";
  }
  return "idle";
}

const LED = {
  working: "var(--st-running)", happy: "var(--st-pass)", concerned: "var(--st-changes)", blocked: "var(--st-blocked)",
  idle: "var(--text-3)", asleep: "var(--border-strong)",
};

function eyes(mood, glow) {
  const L = 24, R = 40, Y = 29.5;
  const stroke = { stroke: glow, "stroke-width": 2.6, "stroke-linecap": "round", fill: "none" };
  switch (mood) {
    case "happy":
      return [s("path", { d: `M${L - 4} ${Y + 1.5} q4 -5 8 0`, ...stroke }), s("path", { d: `M${R - 4} ${Y + 1.5} q4 -5 8 0`, ...stroke })];
    case "concerned":
      return [s("path", { d: `M${L - 4} ${Y - 1} l8 2`, ...stroke }), s("path", { d: `M${R + 4} ${Y - 1} l-8 2`, ...stroke }),
        s("circle", { cx: L, cy: Y + 2.5, r: 2, fill: glow }), s("circle", { cx: R, cy: Y + 2.5, r: 2, fill: glow })];
    case "blocked":
      return [L, R].map((x) => s("path", { d: `M${x - 3} ${Y - 3} l6 6 M${x + 3} ${Y - 3} l-6 6`, ...stroke }));
    case "asleep":
      return [L, R].map((x) => s("path", { d: `M${x - 3.5} ${Y + 0.5} h7`, ...stroke, "stroke-opacity": 0.6 }));
    default:
      return [L, R].map((x) => s("circle", { class: "av-eye", cx: x, cy: Y, r: 3.4, fill: glow }));
  }
}

/**
 * agent: { name, title, hue, emblem, model }   opts: { size: 'sm'|'md'|..., step, mood, label }
 */
function buildSvg(agent, mood, label, extra = {}) {
  const hue = Number.isFinite(agent?.hue) ? agent.hue : 230;
  const gid = nextId("avg");
  const glow = `hsl(${hue} 100% 86%)`;
  const emblem = iconPaths(agent?.emblem);
  return s("svg", { viewBox: "0 0 64 64", role: "img", "aria-label": label, class: `av-${mood}`, ...extra },
    s("defs", {},
      s("linearGradient", { id: gid, x1: "0", y1: "0", x2: "1", y2: "1" },
        s("stop", { offset: "0", "stop-color": `hsl(${hue} 88% 68%)` }),
        s("stop", { offset: "1", "stop-color": `hsl(${(hue + 32) % 360} 72% 44%)` }))),
    s("line", { x1: 32, y1: 10, x2: 32, y2: 4, stroke: `hsl(${hue} 45% 40%)`, "stroke-width": 2.6, "stroke-linecap": "round" }),
    s("circle", { class: "av-led", cx: 32, cy: 3.6, r: 2.8, fill: LED[mood] || LED.idle }),
    s("rect", { x: 2.5, y: 26, width: 6, height: 13, rx: 3, fill: `hsl(${hue} 40% 34%)` }),
    s("rect", { x: 55.5, y: 26, width: 6, height: 13, rx: 3, fill: `hsl(${hue} 40% 34%)` }),
    s("rect", { x: 7, y: 9.5, width: 50, height: 48, rx: 15, fill: `url(#${gid})` }),
    s("path", { d: "M14 19.5q5.5-6 16-6.2", stroke: "#fff", "stroke-opacity": 0.38, "stroke-width": 2.6, fill: "none", "stroke-linecap": "round" }),
    s("rect", { x: 12.5, y: 20.5, width: 39, height: 18, rx: 9, fill: "#0a0c14", "fill-opacity": 0.84 }),
    ...eyes(mood, glow),
    s("g", { transform: "translate(25.2 40.6) scale(0.57)", stroke: "#fff", "stroke-width": 2.3, fill: "none", "stroke-linecap": "round", "stroke-linejoin": "round", "stroke-opacity": 0.95 },
      emblem.map((d) => s("path", { d }))));
}

// The bare <svg> for embedding inside other SVG drawings (the Flow graph).
export function avatarSvg(agent, { mood = "idle", x = 0, y = 0, size = 28, label } = {}) {
  return buildSvg(agent, mood, label ?? (agent?.title || "agent"), { x, y, width: size, height: size });
}

export function avatar(agent, opts = {}) {
  const mood = opts.mood || moodFor(opts.step);
  const label = opts.label ?? `${agent?.title || agent?.name || "Agent"}${opts.step ? ` — ${mood}` : ""}`;
  const svg = buildSvg(agent, mood, label);
  const size = opts.size || "md";
  const wrap = h("span", { class: ["avatar", `avatar-${size}`, `av-${mood}`], title: opts.title ?? label }, svg);
  if (opts.model !== false && agent?.model) {
    wrap.appendChild(h("span", { class: "model-dot", "aria-hidden": "true", title: agent.model }, MODEL_LETTER[agent.model] || agent.model[0].toUpperCase()));
  }
  return wrap;
}

export function factoryMark() {
  return s("svg", { class: "brand-mark", viewBox: "0 0 32 32", "aria-hidden": "true" },
    s("defs", {}, s("linearGradient", { id: "fm-g", x1: 0, y1: 0, x2: 1, y2: 1 },
      s("stop", { offset: 0, "stop-color": "#8b7dff" }), s("stop", { offset: 1, "stop-color": "#22d3ee" }))),
    s("rect", { x: 1, y: 1, width: 30, height: 30, rx: 9, fill: "url(#fm-g)" }),
    s("path", { d: "M9 21V12l4 3v-3l4 3v-3l6 4.5V21z", fill: "#fff", "fill-opacity": 0.95 }),
    s("circle", { cx: 23, cy: 9, r: 2, fill: "#fff", "fill-opacity": 0.9 }));
}
