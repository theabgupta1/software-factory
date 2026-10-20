// Formatting helpers: times, durations, numbers, step names. Pure functions (unit-testable in Node).

export function parseTs(ts) {
  if (!ts) return null;
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function duration(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return "—";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
  const h = Math.floor(s / 3600);
  return `${h}h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
}

export function between(a, b) {
  const x = parseTs(a);
  const y = b ? parseTs(b) : new Date();
  if (!x || !y) return null;
  return (y - x) / 1000;
}

export function ago(ts, now = new Date()) {
  const d = parseTs(ts);
  if (!d) return "—";
  const s = Math.round((now - d) / 1000);
  if (s < 10) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function clock(ts) {
  const d = parseTs(ts);
  if (!d) return "—";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function dateTime(ts) {
  const d = parseTs(ts);
  if (!d) return "—";
  return d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function tokens(n) {
  if (n === null || n === undefined) return "—";
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

export function bytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} kB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

const STAGE_WORDS = { spec: "Specify", plan: "Plan", build: "Build", review: "Review", approve: "Approve", merge: "Merge",
  deliver: "Deliver", setup: "Set up", diagnose: "Diagnose", investigate: "Investigate", context: "Map codebase",
  audit: "Audit", synthesis: "Synthesise", baseline: "Baseline", characterize: "Pin behaviour" };

export function stepLabel(stepId) {
  if (!stepId) return "";
  let m = stepId.match(/^build\.r(\d+)\/builder$/);
  if (m) return `Build · round ${m[1]}`;
  m = stepId.match(/^review\.r(\d+)\/([\w-]+)$/);
  if (m) return `Review · round ${m[1]} · ${m[2]}`;
  m = stepId.match(/^task\/(T\d+)$/);
  if (m) return `Task ${m[1]}`;
  m = stepId.match(/^([\w-]+)\/([\w-]+)$/);
  if (m) return `${STAGE_WORDS[m[1]] || m[1]} · ${m[2]}`;
  return STAGE_WORDS[stepId] || stepId;
}

export const STATUS = {
  running: { label: "Running", cls: "running", icon: "play" },
  "needs-human": { label: "Needs you", cls: "attention", icon: "bell" },
  "awaiting-approval": { label: "Awaiting approval", cls: "attention", icon: "bell" },
  merged: { label: "Merged", cls: "merged", icon: "merge" },
  delivered: { label: "Delivered", cls: "delivered", icon: "check" },
  cancelled: { label: "Cancelled", cls: "cancelled", icon: "x" },
  stale: { label: "Stale (v1)", cls: "stale", icon: "clock" },
  corrupt: { label: "Unreadable", cls: "blocked", icon: "alert" },
};

export const STEP_STATUS = {
  issued: { label: "Working", cls: "running", icon: "play" },
  done: { label: "Done", cls: "pass", icon: "check" },
  invalid: { label: "Rejected output", cls: "blocked", icon: "alert" },
  failed: { label: "Failed", cls: "blocked", icon: "alert" },
  lost: { label: "Lost", cls: "neutral", icon: "clock" },
  upcoming: { label: "Upcoming", cls: "neutral", icon: "clock" },
  skipped: { label: "Skipped", cls: "skipped", icon: "chevron" },
};

export const VALUE = {
  PASS: { label: "PASS", cls: "pass", icon: "check" },
  CHANGES: { label: "CHANGES", cls: "changes", icon: "alert" },
  READY: { label: "READY", cls: "pass", icon: "check" },
  PARTIAL: { label: "PARTIAL", cls: "changes", icon: "alert" },
  BLOCKED: { label: "BLOCKED", cls: "blocked", icon: "stop" },
  APPROVE: { label: "APPROVE", cls: "pass", icon: "check" },
  ESCALATE: { label: "ESCALATE", cls: "attention", icon: "bell" },
};

export function humanEvent(ev) {
  const t = ev.type;
  const d = ev;
  switch (t) {
    case "job.created": return `Job created as ${d.workflow} (size ${d.size})`;
    case "worktree.created": return "Worktree created";
    case "stage.started": return `Stage started: ${STAGE_WORDS[d.stage] || d.stage}`;
    case "stage.completed": return `Stage completed: ${STAGE_WORDS[d.stage] || d.stage}`;
    case "stage.skipped": return `Stage skipped: ${STAGE_WORDS[d.stage] || d.stage}`;
    case "stage.looped": return `Changes requested by ${(d.changes || []).join(", ")} → back to ${d.to} (round ${d.round})`;
    case "step.issued": return `${stepLabel(d.step)} issued to ${d.agent || "orchestrator"}${d.attempt > 1 ? ` (attempt ${d.attempt})` : ""}`;
    case "step.done": return `${stepLabel(d.step)} finished${d.value ? `: ${d.value}` : ""}`;
    case "step.invalid": return `${stepLabel(d.step)} output rejected: ${(d.problems || [])[0] || ""}`;
    case "step.lost": return `${stepLabel(d.step)} lost (session ended)`;
    case "step.recovered": return `${stepLabel(d.step)} recovered from disk`;
    case "panel.selected": return `Review panel: ${(d.members || []).map((m) => m.key).join(", ")}`;
    case "round.started": return `Review round ${d.round} started`;
    case "round.completed": return `Review round ${d.round} completed`;
    case "plan.validated": return `Plan validated: ${(d.order || []).join(" → ")}`;
    case "gate.waiting": return "Waiting for plan approval";
    case "gate.approved": return "Plan approved";
    case "human.needed": return `Needs you (${d.kind}): ${d.reason || ""}`;
    case "human.approved": return "Approved by you";
    case "merge.done": return `Merged (${(d.sha || "").slice(0, 8)})`;
    case "merge.failed": return `Merge not done: ${d.message || d.status}`;
    case "job.reworked": return `Rework requested → ${d.target}`;
    case "job.cancelled": return "Job cancelled";
    case "job.delivered": return "Delivered";
    case "message.posted": return `Message sent to ${d.to?.agent || d.to?.step || d.to?.role}`;
    case "lease.acquired": return "A new session took over the job";
    case "ownership.violation": return `${stepLabel(d.step)} changed files outside its ownership`;
    case "steps.redelivered": return `Re-sent: ${(d.steps || []).join(", ")}`;
    default: return t;
  }
}

export function eventIcon(type) {
  if (type.startsWith("merge")) return "merge";
  if (type.startsWith("human") || type.startsWith("gate")) return "bell";
  if (type.startsWith("message")) return "message";
  if (type === "step.invalid" || type === "ownership.violation") return "alert";
  if (type.startsWith("step")) return "play";
  if (type.startsWith("round") || type.startsWith("panel")) return "users";
  return "dot";
}
