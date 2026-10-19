// Pure logic for factory-live: reading the engine's index, what changed, and what each surface says.
// No `$` here, so every rule is testable on its own. The engine (factoryctl) stays the only source of truth.

import type { FactoryLiveAgentMeta, FactoryLiveJob } from '../types'

export const FINISHED = new Set(['merged', 'delivered', 'cancelled'])
export const ATTENTION_LABEL: Record<string, string> = {
  escalate: 'escalated by the approver',
  'plan-approval': 'plan ready for approval',
  blocked: 'an agent is blocked',
  limit: 'a limit was reached',
  'rounds-exhausted': 'review rounds exhausted',
  'merge-busy': 'approved, main checkout busy',
  'merge-conflict': 'approved, merge would conflict',
  policy: 'needs a human by policy',
  integrity: 'worktree changed during review',
  'approve-merge': 'approved, waiting to merge',
}

const str = (v: unknown): string | null => (typeof v === 'string' ? v : null)

/** Parses `.factory/index.json` defensively: a malformed entry is dropped, never thrown on. */
export function parseIndex(text: string): FactoryLiveJob[] {
  let data: unknown
  try {
    data = JSON.parse(text)
  } catch {
    return []
  }
  const jobs = (data as { jobs?: unknown })?.jobs
  if (!Array.isArray(jobs)) return []
  const out: FactoryLiveJob[] = []
  for (const raw of jobs) {
    if (!raw || typeof raw !== 'object') continue
    const j = raw as Record<string, unknown>
    const id = str(j.id)
    const status = str(j.status)
    if (!id || !status) continue
    const att = j.attention as Record<string, unknown> | null | undefined
    out.push({
      id,
      title: str(j.title) ?? id,
      workflow: str(j.workflow),
      status,
      stage: str(j.stage),
      round: typeof j.round === 'number' ? j.round : 0,
      attention: att && typeof att === 'object'
        ? {
            kind: str(att.kind) ?? 'needs-human',
            reason: str(att.reason) ?? '',
            commands: Array.isArray(att.commands) ? att.commands.filter((c): c is string => typeof c === 'string') : [],
          }
        : null,
      running_detail: Array.isArray(j.running_detail)
        ? (j.running_detail as Record<string, unknown>[])
            .filter((r) => r && typeof r.step === 'string')
            .map((r) => ({ step: r.step as string, agent: str(r.agent), kind: str(r.kind), since: str(r.since) }))
        : [],
      reviews: j.reviews && typeof j.reviews === 'object' ? (j.reviews as Record<string, string | null>) : {},
      pending_messages: typeof j.pending_messages === 'number' ? j.pending_messages : 0,
      updated_at: str(j.updated_at),
      legacy: j.legacy === true,
    })
  }
  return out
}

/** `agents.json` (agent id → {job, step, ts}) inverted to `<job>/<step>` → newest agent id. */
export function parseAgentMap(text: string): Record<string, string> {
  let data: unknown
  try {
    data = JSON.parse(text)
  } catch {
    return {}
  }
  if (!data || typeof data !== 'object') return {}
  const best: Record<string, { id: string; ts: string }> = {}
  for (const [id, info] of Object.entries(data as Record<string, Record<string, unknown>>)) {
    const job = str(info?.job)
    const step = str(info?.step)
    if (!job || !step) continue
    const key = `${job}/${step}`
    const ts = str(info.ts) ?? ''
    if (!best[key] || ts >= best[key].ts) best[key] = { id, ts }
  }
  return Object.fromEntries(Object.entries(best).map(([k, v]) => [k, v.id]))
}

export function parseRoster(text: string): Record<string, FactoryLiveAgentMeta> {
  try {
    const data = JSON.parse(text) as { agents?: { name?: unknown; title?: unknown; hue?: unknown }[] }
    const out: Record<string, FactoryLiveAgentMeta> = {}
    for (const a of data.agents ?? []) {
      if (typeof a.name === 'string') {
        out[a.name] = { title: typeof a.title === 'string' ? a.title : a.name, hue: typeof a.hue === 'number' ? a.hue : 220 }
      }
    }
    return out
  } catch {
    return {}
  }
}

export const shortId = (id: string): string => id.split('-')[0] ?? id
export const needsYou = (jobs: FactoryLiveJob[]) => jobs.filter((j) => j.attention && !j.legacy)
export const running = (jobs: FactoryLiveJob[]) => jobs.filter((j) => j.status === 'running' && !j.legacy)
export const finished = (jobs: FactoryLiveJob[]) =>
  jobs.filter((j) => FINISHED.has(j.status) && !j.legacy)
    .sort((a, b) => (b.updated_at ?? '').localeCompare(a.updated_at ?? ''))

/** A step id as a person reads it: `review.r2/security` → `review r2 · security`. */
export function stepLabel(step: string): string {
  let m = step.match(/^build\.r(\d+)\/builder$/)
  if (m) return `build r${m[1]}`
  m = step.match(/^review\.r(\d+)\/([\w-]+)$/)
  if (m) return `review r${m[1]} · ${m[2]}`
  m = step.match(/^task\/(T\d+)$/)
  if (m) return `task ${m[1]}`
  return step.replace('/', ' · ')
}

export function stageLabel(job: FactoryLiveJob): string {
  const stage = job.stage ?? job.status
  return job.round && /review|build|panel/.test(stage) ? `${stage} r${job.round}` : stage
}

const GLYPH: Record<string, string> = { PASS: '✓', CHANGES: '✗', pending: '…' }
export function verdicts(reviews: Record<string, string | null>): string {
  return Object.entries(reviews).map(([k, v]) => `${k} ${GLYPH[v ?? ''] ?? '·'}`).join('  ')
}

export function since(iso: string | null, now: number): string {
  if (!iso) return ''
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return ''
  const s = Math.max(0, Math.round((now - t) / 1000))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  return h < 48 ? `${h}h ${String(m % 60).padStart(2, '0')}m` : `${Math.floor(h / 24)}d`
}

/** The one command that moves a job forward, with placeholders the person must fill made obvious. */
export function primaryCommand(job: FactoryLiveJob): string | null {
  const cmd = job.attention?.commands[0]
  return cmd ? cmd.replace('<note>', '"what to change"') : null
}

/** Changes worth a notification: a job newly needing the person, or reaching an end state. */
export type Transition = { job: FactoryLiveJob; kind: 'attention' | 'finished'; text: string }
export function transitions(before: FactoryLiveJob[] | null, after: FactoryLiveJob[]): Transition[] {
  if (before === null) return [] // first read after a load: a baseline, not news
  const prev = new Map(before.map((j) => [j.id, j]))
  const out: Transition[] = []
  for (const j of after) {
    if (j.legacy) continue
    const p = prev.get(j.id)
    const wasAttn = p?.attention ? `${p.status}:${p.attention.kind}` : null
    const isAttn = j.attention ? `${j.status}:${j.attention.kind}` : null
    if (isAttn && isAttn !== wasAttn) {
      const label = ATTENTION_LABEL[j.attention!.kind] ?? j.attention!.kind
      out.push({ job: j, kind: 'attention', text: `${shortId(j.id)} ${label}: ${j.title}` })
    } else if (FINISHED.has(j.status) && p && p.status !== j.status) {
      out.push({ job: j, kind: 'finished', text: `${shortId(j.id)} ${j.status}: ${j.title}` })
    }
  }
  return out
}

/** The status line under the prompt; undefined clears it (nothing to say). */
export function statusLine(jobs: FactoryLiveJob[]): string | undefined {
  const attn = needsYou(jobs)
  const run = running(jobs)
  const agents = run.reduce((n, j) => n + j.running_detail.length, 0)
  const parts: string[] = []
  if (attn.length) parts.push(`${attn.length} need${attn.length === 1 ? 's' : ''} you`)
  if (run.length === 1) {
    const j = run[0]!
    parts.push(`${shortId(j.id)} ${stageLabel(j)}${agents ? ` · ${agents} agent${agents === 1 ? '' : 's'} working` : ''}`)
  } else if (run.length > 1) {
    parts.push(`${run.length} jobs running${agents ? ` · ${agents} agents working` : ''}`)
  }
  return parts.length ? `factory ▸ ${parts.join(' · ')}` : undefined
}

/** Identifies the set of things that need the person; hiding the band holds until it changes. */
export function attentionSignature(jobs: FactoryLiveJob[]): string {
  return needsYou(jobs).map((j) => `${j.id}:${j.attention!.kind}`).sort().join('|')
}

// ---- avatars: one pixel matrix, drawn as a Raster on the terminal and an SVG elsewhere ---------------
export type Mood = 'working' | 'happy' | 'concerned' | 'blocked' | 'idle'
const ROBOT = [
  '..a...',
  'hhhhhh',
  'heehhe',
  'hhhhhh',
  'hmmmmh',
  '.h..h.',
]

export function hslToRgb(h: number, s: number, l: number): number {
  const k = (n: number) => (n + h / 30) % 12
  const a = s * Math.min(l, 1 - l)
  const f = (n: number) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)))
  const to = (x: number) => Math.round(x * 255)
  return (to(f(0)) << 16) | (to(f(8)) << 8) | to(f(4))
}

const EYES: Record<Mood, number> = {
  working: 0xf8fafc, happy: 0x4ade80, concerned: 0xfbbf24, blocked: 0xf87171, idle: 0x94a3b8,
}
export const DEFAULT_BG = 0x01000000 // the terminal's own background

/** Rows of 0xRRGGBB colours (or DEFAULT_BG for empty pixels). */
export function avatarPixels(hue: number, mood: Mood): number[][] {
  const head = hslToRgb(hue, mood === 'idle' ? 0.25 : 0.65, 0.55)
  const visor = hslToRgb(hue, 0.55, 0.3)
  const antenna = mood === 'working' ? 0x38bdf8 : hslToRgb(hue, 0.7, 0.7)
  const map: Record<string, number> = { h: head, e: EYES[mood], m: visor, a: antenna, '.': DEFAULT_BG }
  return ROBOT.map((row) => [...row].map((c) => map[c] ?? DEFAULT_BG))
}

/** Two pixel rows per terminal cell: '▀' with the top pixel as foreground and the bottom as background. */
export function rasterCells(pixels: number[][]): { cells: string; columns: number; rows: number } {
  const columns = pixels[0]?.length ?? 0
  const rows = Math.ceil(pixels.length / 2)
  const words: number[] = []
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < columns; c++) {
      const top = pixels[r * 2]?.[c] ?? DEFAULT_BG
      const bottom = pixels[r * 2 + 1]?.[c] ?? DEFAULT_BG
      if (top === DEFAULT_BG && bottom === DEFAULT_BG) words.push(0x20, DEFAULT_BG, DEFAULT_BG)
      else if (top === DEFAULT_BG) words.push(0x2584, bottom, DEFAULT_BG) // '▄'
      else words.push(0x2580, top, bottom) // '▀'
    }
  }
  return { cells: base64(Uint32Array.from(words)), columns, rows }
}

export function avatarSvg(pixels: number[][]): string {
  const hex = (c: number) => `#${c.toString(16).padStart(6, '0')}`
  const rects: string[] = []
  pixels.forEach((row, y) => row.forEach((c, x) => {
    if (c !== DEFAULT_BG) rects.push(`<rect x="${x}" y="${y}" width="1.02" height="1.02" fill="${hex(c)}"/>`)
  }))
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 6 6" shape-rendering="crispEdges">${rects.join('')}</svg>`
}

export function moodFor(job: FactoryLiveJob, step: string | null): Mood {
  if (job.attention?.kind === 'blocked') return 'blocked'
  if (step && job.running_detail.some((r) => r.step === step)) return 'working'
  if (job.attention) return 'concerned'
  if (FINISHED.has(job.status)) return 'happy'
  return 'idle'
}

const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
/** Little-endian bytes of a Uint32Array as padded base64 (no reliance on platform helpers). */
export function base64(words: Uint32Array): string {
  const bytes: number[] = []
  for (const w of words) bytes.push(w & 0xff, (w >>> 8) & 0xff, (w >>> 16) & 0xff, (w >>> 24) & 0xff)
  let out = ''
  for (let i = 0; i < bytes.length; i += 3) {
    const [a, b, c] = [bytes[i]!, bytes[i + 1], bytes[i + 2]]
    const n = (a << 16) | ((b ?? 0) << 8) | (c ?? 0)
    out += B64[(n >> 18) & 63]! + B64[(n >> 12) & 63]! + (b === undefined ? '=' : B64[(n >> 6) & 63]!) + (c === undefined ? '=' : B64[n & 63]!)
  }
  return out
}

/** Where factoryctl may be, relative to this plugin's folder, in the order to try. */
export function factoryctlCandidates(pluginRoot: string, configured: string | undefined): string[] {
  const root = pluginRoot.replace(/\/+$/, '')
  const parent = root.replace(/\/[^/]+$/, '')
  const out: string[] = []
  if (configured) out.push(configured)
  out.push(`${parent}/factory/bin/factoryctl`) // repo checkout, or both plugins in ~/.claude/skills
  return out
}
