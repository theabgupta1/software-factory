// factory-live under `claude plugin test`: the pure rules, then the mod against the engine with a fake
// repository answered from memory (fs, process, agents, clock), drawn on every surface it supports.
import { describe, expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'
import type { Engine } from 'claude-code/testing'

import {
  attentionSignature, avatarPixels, avatarSvg, base64, DEFAULT_BG, factoryctlCandidates, parseAgentMap, parseIndex,
  primaryCommand, rasterCells, statusLine, stepLabel, transitions,
} from '../hooks/model'

const REPO = '/work/acme-shop'
const CTL = '/plugins/factory/bin/factoryctl'

const job = (over: Record<string, unknown>) => ({
  id: '004-rate-limit-signup-form', title: 'Rate limit the signup form', workflow: 'feature', status: 'running',
  stage: 'review', round: 2, attention: null, running_detail: [], reviews: {}, pending_messages: 0,
  updated_at: '2026-10-19T10:00:00.000Z', legacy: false, ...over,
})

const RUNNING = job({
  running_detail: [{ step: 'review.r2/security', agent: 'security-engineer', kind: 'review', since: '2026-10-19T10:00:00.000Z' }],
  reviews: { code: 'PASS', security: 'pending' },
})
const ESC_ATTENTION = { kind: 'escalate', reason: 'Touches the deploy config', commands: ['/factory approve 006-reading-time', '/factory rework 006-reading-time <note>'] }
const ESCALATED = job({ id: '006-reading-time', title: 'Show reading time', status: 'needs-human', stage: 'approve', round: 1, attention: ESC_ATTENTION })
const MERGED = job({ id: '003-dark-mode', title: 'Dark mode toggle', status: 'merged', stage: 'merge' })
const LEGACY = job({ id: '001-blog', title: 'Old job', status: 'needs-human', legacy: true, attention: { kind: 'escalate', reason: '', commands: [] } })

const index = (...jobs: unknown[]) => JSON.stringify({ schema: 1, jobs })

describe('model', () => {
  test('parseIndex keeps well-formed jobs and drops the rest', () => {
    expect(parseIndex('not json')).toEqual([])
    expect(parseIndex('{"jobs": 3}')).toEqual([])
    const jobs = parseIndex(JSON.stringify({ jobs: [RUNNING, { id: 7 }, null, { id: 'x' }, ESCALATED] }))
    expect(jobs.map((j) => j.id)).toEqual(['004-rate-limit-signup-form', '006-reading-time'])
    expect(jobs[1]?.attention?.commands[0]).toBe('/factory approve 006-reading-time')
  })

  test('transitions: a baseline first, then only news', () => {
    const before = parseIndex(index(RUNNING, MERGED))
    expect(transitions(null, before)).toEqual([])
    const after = parseIndex(index({ ...RUNNING, status: 'merged' }, MERGED, ESCALATED, LEGACY))
    const t = transitions(before, after)
    expect(t.map((x) => [x.kind, x.job.id])).toEqual([['finished', '004-rate-limit-signup-form'], ['attention', '006-reading-time']])
    expect(transitions(after, after)).toEqual([])
  })

  test('status line and the band signature', () => {
    expect(statusLine([])).toBeUndefined()
    expect(statusLine(parseIndex(index(RUNNING)))).toBe('factory ▸ 004 review r2 · 1 agent working')
    expect(statusLine(parseIndex(index(RUNNING, ESCALATED, LEGACY)))).toBe('factory ▸ 1 needs you · 004 review r2 · 1 agent working')
    expect(attentionSignature(parseIndex(index(ESCALATED, RUNNING)))).toBe('006-reading-time:escalate')
  })

  test('labels and commands people read', () => {
    expect(stepLabel('review.r2/security')).toBe('review r2 · security')
    expect(stepLabel('task/T3')).toBe('task T3')
    expect(stepLabel('build.r1/builder')).toBe('build r1')
    const [esc] = parseIndex(index({ ...ESCALATED, attention: { ...ESC_ATTENTION, commands: ['/factory rework 006 <note>'] } }))
    expect(primaryCommand(esc!)).toBe('/factory rework 006 "what to change"')
  })

  test('agent ids: the newest spawn for a step wins', () => {
    const map = parseAgentMap(JSON.stringify({
      a1: { job: '004', step: 'review.r1/security', ts: '2026-10-19T09:00:00Z' },
      a2: { job: '004', step: 'review.r2/security', ts: '2026-10-19T10:00:00Z' },
      a3: { job: '004', step: 'review.r2/security', ts: '2026-10-19T10:05:00Z' },
      bad: { step: 'x' },
    }))
    expect(map).toEqual({ '004/review.r1/security': 'a1', '004/review.r2/security': 'a3' })
  })

  test('avatars: a 6x6 robot as half-block cells and as SVG', () => {
    const pixels = avatarPixels(210, 'working')
    expect(pixels.length).toBe(6)
    expect(pixels[0]?.[0]).toBe(DEFAULT_BG)
    const r = rasterCells(pixels)
    expect([r.columns, r.rows]).toEqual([6, 3])
    expect(r.cells.length).toBe((Math.ceil((6 * 3 * 3 * 4) / 3)) * 4) // 18 cells x 3 u32 words, base64
    expect(avatarSvg(pixels)).toMatch(/^<svg [^>]*viewBox="0 0 6 6"/)
    expect(avatarPixels(210, 'blocked')[2]).not.toEqual(avatarPixels(210, 'happy')[2]) // eyes show the mood
  })

  test('base64 matches the standard alphabet and padding', () => {
    expect(base64(Uint32Array.of(0x2588))).toBe('iCUAAA==')
    expect(base64(Uint32Array.of(0xff8800, 0x01000000))).toBe('AIj/AAAAAAE=')
  })

  test('factoryctl is looked for beside the plugin, after an explicit setting', () => {
    expect(factoryctlCandidates('/x/plugins/factory-live', undefined)).toEqual(['/x/plugins/factory/bin/factoryctl'])
    expect(factoryctlCandidates('/x/plugins/factory-live/', '/opt/f')).toEqual(['/opt/f', '/x/plugins/factory/bin/factoryctl'])
  })
})

// ---- the mod against the engine --------------------------------------------------------------------
type World = {
  files: Map<string, string>
  touch: (path: string, text: string) => void
  runs: (readonly string[])[]
  toasts: string[]
  notices: string[]
  statuses: (string | undefined)[]
  fills: string[]
  views: unknown[]
  opened: unknown[]
  copies: string[]
  spawns: (readonly string[])[]
}

type WorldOptions = {
  run?: { exitCode: number; stdout: string; stderr: string }
  runThrows?: string
  failReads?: number // the first N file reads fail, as a transient error or a file over 4 MiB would
  notifyDelayMs?: number // a person's Notification hooks can make notify slow
  viewDeny?: string
  draft?: string
  spawn?: { stream: 'stdout' | 'stderr'; text: string }[] | 'fail'
}
const RUN_OK = { exitCode: 0, stdout: '{"id": "m1"}', stderr: '' }

// Every call the mod makes on `$` lands on one of these: the kit's bottom answers `{ value }`.
function world(on: On, files: Record<string, string>, opts: WorldOptions = {}): World {
  const w: World = {
    files: new Map(Object.entries(files)), touch: () => undefined,
    runs: [], toasts: [], notices: [], statuses: [], fills: [], views: [], opened: [], copies: [], spawns: [],
  }
  let failReads = opts.failReads ?? 0
  const mtimes = new Map<string, number>([...w.files.keys()].map((k) => [k, 1]))
  w.touch = (path, text) => { w.files.set(path, text); mtimes.set(path, (mtimes.get(path) ?? 0) + 1) }
  mock.env(on, { HOME: '/home/me' })
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('session.root', () => ({ value: REPO }))
  on('fs.exists', ($, e) => ({ value: w.files.has(e.path) }))
  on('fs.stat', ($, e) => {
    if (!w.files.has(e.path)) throw new Error(`ENOENT ${e.path}`)
    return { value: { kind: 'file' as const, size: w.files.get(e.path)!.length, mtimeMs: mtimes.get(e.path) ?? 1, isLink: false } }
  })
  on('fs.read', ($, e) => {
    if (failReads > 0) {
      failReads -= 1
      throw new Error('EIO: read failed')
    }
    const text = w.files.get(e.path)
    if (text === undefined) throw new Error(`ENOENT ${e.path}`)
    return { value: text }
  })
  on('fs.list', () => ({ value: [] }))
  on('agent.list', () => ({ value: [{ id: 'agent-sec', status: 'running', description: 'review', type: 'factory:security-engineer' }] }))
  on('process.run', ($, e) => {
    w.runs.push(e.argv)
    if (opts.runThrows) throw new Error(opts.runThrows)
    return { value: { ...(opts.run ?? RUN_OK), isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('command.register', ($, e) => ({ value: { command: e.name } }))
  on('ui.status', ($, e) => { w.statuses.push(e.text); return { value: undefined } })
  on('ui.toast', ($, e) => { w.toasts.push(e.text); return { value: undefined } })
  on('ui.notify', async ($, e) => {
    w.notices.push(e.text)
    if (opts.notifyDelayMs) await $.clock.sleep(opts.notifyDelayMs)
    return { value: { isSent: true, channel: 'terminal_bell' } }
  })
  on('ui.copy', ($, e) => { w.copies.push(e.text); return { value: { isCopied: true } } })
  on('prompt.read', () => ({ value: { text: opts.draft ?? '', cursor: 0 } }))
  on('process.spawn', async function* ($, e) {
    w.spawns.push(e.argv)
    if (opts.spawn === 'fail') throw new Error('python3: not found')
    for (const piece of opts.spawn ?? []) yield piece
    return { value: { code: 0, signal: null } }
  })
  on('ui.log', () => ({ value: undefined }))
  on('ui.open', ($, e) => { w.opened.push(e); return { value: { isPlaced: true } } })
  on('ui.focus', () => ({}))
  on('ui.view', ($, e) => { w.views.push(e); return { value: opts.viewDeny ? { deny: opts.viewDeny } : {} } })
  on('prompt.fill', ($, e) => { w.fills.push(e.text); return { isFilled: true } })
  // What the engine draws when the mod hands a site on (the band with nothing to say): nothing.
  on('ui.render', ($, e) => { const { Box } = $.ui.resolve(e); return <Box /> })
  return w
}

const STATE = {
  [`${REPO}/.factory/index.json`]: index(RUNNING, ESCALATED, MERGED, LEGACY),
  [`${REPO}/.factory/agents.json`]: JSON.stringify({ 'agent-sec': { job: RUNNING.id, step: 'review.r2/security', ts: '2026-10-19T10:00:00Z' } }),
  [CTL]: '#!/usr/bin/env python3',
  '/plugins/factory/contracts/agents.json': JSON.stringify({ agents: [
    { name: 'security-engineer', title: 'Security Engineer', hue: 8 }, { name: 'approver', title: 'Approver', hue: 48 }] }),
}

const PANE_PROPS = (bodyColumns: number) => ({
  title: 'Factory', isFocused: true, bodyColumns, placement: 'dock' as const,
  scroll: { offset: 0, bodyRows: 40 }, view: {},
})

async function start($: Engine, cwd = REPO) {
  await $.session.start({ cwd, surface: 'terminal', isInteractive: true } as never)
}

describe('session', () => {
  test('reads the repo at start: status line, no notification for what was already true', { options: { factoryctl: CTL } }, async ($, on) => {
    mock.clock(on, { now: Date.parse('2026-10-19T10:12:00Z') })
    const w = world(on, STATE)
    await start($)
    expect(w.statuses.at(-1)).toBe('factory ▸ 1 needs you · 004 review r2 · 1 agent working')
    expect(w.notices).toEqual([])
    expect(w.toasts).toEqual([])
  })

  test('a job that starts needing you notifies once; the band shows it', { options: { factoryctl: CTL } }, async ($, on) => {
    const clock = mock.clock(on, { now: Date.parse('2026-10-19T10:12:00Z') })
    const w = world(on, { ...STATE, [`${REPO}/.factory/index.json`]: index(RUNNING, MERGED) })
    await start($)
    w.touch(`${REPO}/.factory/index.json`, index(RUNNING, MERGED, ESCALATED))
    await clock.advance(2100)
    expect(w.notices).toEqual(['006 escalated by the approver: Show reading time'])
    await clock.advance(2100)
    expect(w.notices.length).toBe(1)

    for (const surface of ['terminal', 'desktop'] as const) {
      const band = await $.ui.mount({ plugin: 'factory-live', surface, component: 'AbovePrompt',
        props: { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 100, scroll: { offset: 0, bodyRows: 10 }, view: {} } })
      expect((await band.find({ type: 'Text', text: /factory job needs you/ }))).toBeDefined()
      await band.press({ key: 'fill' })
      expect(w.fills.at(-1)).toBe('/factory approve 006-reading-time')
      await band.unmount()
    }
  })

  test('hiding the band holds until something else needs you', { options: { factoryctl: CTL } }, async ($, on) => {
    const clock = mock.clock(on, { now: 0 })
    const w = world(on, STATE)
    await start($)
    const props = { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 100, scroll: { offset: 0, bodyRows: 10 }, view: {} }
    const band = await $.ui.mount({ plugin: 'factory-live', surface: 'terminal', component: 'AbovePrompt', props })
    await band.press({ key: 'hide' })
    expect(await band.find({ key: 'fill' })).toBeUndefined()
    await band.unmount()
    w.touch(`${REPO}/.factory/index.json`, index(RUNNING, ESCALATED, { ...MERGED, id: '009-new', status: 'needs-human',
      attention: { kind: 'blocked', reason: 'tests fail', commands: ['/factory resume 009-new'] } }))
    await clock.advance(2100)
    const again = await $.ui.mount({ plugin: 'factory-live', surface: 'terminal', component: 'AbovePrompt', props })
    expect(await again.find({ type: 'Text', text: /2 factory jobs need you/ })).toBeDefined()
  })

  test('outside a factory repository it stays silent', async ($, on) => {
    mock.clock(on, { now: 0 })
    const w = world(on, {})
    await start($, '/elsewhere')
    expect(w.statuses).toEqual([])
    const r = await $.command.run({ command: 'factory-board', args: '' } as never)
    expect(JSON.stringify(r)).toMatch(/No software factory/)
  })
})

describe('board', () => {
  for (const surface of ['terminal', 'desktop', 'vscode'] as const) {
    test(`${surface}: jobs, agents with avatars, watch and message`, { options: { factoryctl: CTL } }, async ($, on) => {
      mock.clock(on, { now: Date.parse('2026-10-19T10:12:00Z') })
      const w = world(on, STATE)
      await start($)
      await $.command.run({ command: 'factory-board', args: '' } as never)
      expect(w.opened.length).toBe(1)
      const ui = await $.ui.mount({ plugin: 'factory-live', surface, component: 'Pane', requestId: 'factory', props: PANE_PROPS(100) })
      expect(await ui.find({ type: 'Text', text: 'Security Engineer' })).toBeDefined()
      expect(await ui.find({ type: 'Text', text: /review r2 · security · 12m/ })).toBeDefined()
      expect(await ui.find({ type: 'Text', text: /Old job/ })).toBeUndefined() // legacy jobs stay in the console
      const avatars = await ui.findAll({ type: surface === 'terminal' ? 'Raster' : 'Svg' })
      expect(avatars.length).toBe(2) // the approver for the escalation, the security engineer at work

      await ui.press({ key: 'watch:004-rate-limit-signup-form/review.r2/security' })
      expect(w.views).toEqual([{ agentId: 'agent-sec' }])

      await ui.press({ key: 'msg:004-rate-limit-signup-form/review.r2/security' })
      await ui.input({ key: 'msg', text: '-n also check X-Forwarded-For' })
      expect(w.runs).toEqual([['python3', CTL, '--repo', REPO, 'tell', '004-rate-limit-signup-form', '--step', 'review.r2/security',
        '--', '-n also check X-Forwarded-For']])
      expect(w.toasts.at(-1)).toMatch(/^Sent to Security Engineer/)
      expect(await ui.find({ key: 'msg' })).toBeUndefined()

      await ui.press({ key: 'fill:006-reading-time' })
      expect(w.fills.at(-1)).toBe('/factory approve 006-reading-time')
      await ui.unmount()
    })
  }

  test('mobile: the board draws, messaging defers to a keyboard surface', { options: { factoryctl: CTL } }, async ($, on) => {
    mock.clock(on, { now: 0 })
    world(on, STATE)
    await start($)
    const ui = await $.ui.mount({ plugin: 'factory-live', surface: 'mobile', component: 'Pane', requestId: 'factory', props: PANE_PROPS(40) })
    await ui.press({ key: 'msg:004-rate-limit-signup-form/review.r2/security' })
    expect(await ui.find({ type: 'Text', text: /Messaging needs a keyboard/ })).toBeDefined()
  })

  test('a failed tell keeps the message box and says why', { options: { factoryctl: CTL } }, async ($, on) => {
    mock.clock(on, { now: 0 })
    const w = world(on, STATE, { run: { exitCode: 1, stdout: '', stderr: 'error: no such step' } })
    await start($)
    const ui = await $.ui.mount({ plugin: 'factory-live', surface: 'terminal', component: 'Pane', requestId: 'factory', props: PANE_PROPS(100) })
    await ui.press({ key: 'msg:004-rate-limit-signup-form/review.r2/security' })
    await ui.input({ key: 'msg', text: 'hello' })
    expect(w.toasts.at(-1)).toBe('Factory: message not sent: error: no such step')
    expect(await ui.find({ key: 'msg' })).toBeDefined()
  })
})

describe('robustness', () => {
  test('a failed first read does not stop polling', { options: { factoryctl: CTL } }, async ($, on) => {
    const clock = mock.clock(on, { now: 0 })
    const w = world(on, STATE, { failReads: 2 })
    await start($)
    expect(w.statuses).toEqual([])
    await clock.advance(2100)
    await clock.advance(2100)
    expect(w.statuses.at(-1)).toMatch(/^factory ▸ 1 needs you/)
  })

  test('a slow notification does not make the next tick announce again', { options: { factoryctl: CTL } }, async ($, on) => {
    const clock = mock.clock(on, { now: 0 })
    const w = world(on, { ...STATE, [`${REPO}/.factory/index.json`]: index(RUNNING) }, { notifyDelayMs: 7000 })
    await start($)
    w.touch(`${REPO}/.factory/index.json`, index(RUNNING, ESCALATED))
    for (let i = 0; i < 6; i++) await clock.advance(2100)
    expect(w.notices).toEqual(['006 escalated by the approver: Show reading time'])
    expect(w.toasts.filter((x) => x.includes('escalated'))).toHaveLength(1)
  })

  test('several changes in one poll go into one toast', { options: { factoryctl: CTL } }, async ($, on) => {
    const clock = mock.clock(on, { now: 0 })
    const w = world(on, { ...STATE, [`${REPO}/.factory/index.json`]: index(RUNNING) })
    await start($)
    w.touch(`${REPO}/.factory/index.json`, index({ ...RUNNING, status: 'merged' }, ESCALATED))
    await clock.advance(2100)
    expect(w.toasts).toHaveLength(1)
    expect(w.toasts[0]).toMatch(/004 merged.*006 escalated/)
  })

  test('a tell that cannot start says so and keeps the message box', { options: { factoryctl: CTL } }, async ($, on) => {
    mock.clock(on, { now: 0 })
    const w = world(on, STATE, { runThrows: 'spawn python3 ENOENT' })
    await start($)
    const ui = await $.ui.mount({ plugin: 'factory-live', surface: 'terminal', component: 'Pane', requestId: 'factory', props: PANE_PROPS(100) })
    await ui.press({ key: 'msg:004-rate-limit-signup-form/review.r2/security' })
    await ui.input({ key: 'msg', text: 'hello' })
    expect(w.toasts.at(-1)).toMatch(/^Factory: message not sent: /)
    expect(await ui.find({ key: 'msg' })).toBeDefined()
  })

  test('Watch says why when the transcript cannot be shown', { options: { factoryctl: CTL } }, async ($, on) => {
    mock.clock(on, { now: 0 })
    const w = world(on, STATE, { viewDeny: 'no agent holds the id' })
    await start($)
    const ui = await $.ui.mount({ plugin: 'factory-live', surface: 'terminal', component: 'Pane', requestId: 'factory', props: PANE_PROPS(100) })
    await ui.press({ key: 'watch:004-rate-limit-signup-form/review.r2/security' })
    expect(w.toasts.at(-1)).toBe("Cannot open that agent's transcript: no agent holds the id")
  })

  test('a draft in the prompt is never overwritten', { options: { factoryctl: CTL } }, async ($, on) => {
    mock.clock(on, { now: 0 })
    const w = world(on, STATE, { draft: 'half-written thought' })
    await start($)
    const ui = await $.ui.mount({ plugin: 'factory-live', surface: 'terminal', component: 'Pane', requestId: 'factory', props: PANE_PROPS(100) })
    await ui.press({ key: 'fill:006-reading-time' })
    expect(w.fills).toEqual([])
    expect(w.copies).toEqual(['/factory approve 006-reading-time'])
  })

  test('the board shows a way back from an agent transcript', { options: { factoryctl: CTL } }, async ($, on) => {
    mock.clock(on, { now: 0 })
    const w = world(on, STATE)
    await start($)
    const ui = await $.ui.mount({ plugin: 'factory-live', surface: 'terminal', component: 'Pane', requestId: 'factory',
      props: { ...PANE_PROPS(100), view: { agentId: 'agent-sec' } } })
    await ui.press({ key: 'back' })
    expect(w.views.at(-1)).toEqual({})
  })
})

describe('console', () => {
  test('starts once, reads an address split across pieces, and shows it', { options: { factoryctl: CTL } }, async ($, on) => {
    const clock = mock.clock(on, { now: 0 })
    const w = world(on, STATE, { spawn: [{ stream: 'stdout', text: '{"url": "http://127.0.0.1:5' },
      { stream: 'stdout', text: '555/", "repo": "x"}\n' }] })
    await start($)
    await $.command.run({ command: 'factory-console', args: '' } as never)
    await $.command.run({ command: 'factory-console', args: '' } as never)
    await clock.settle()
    expect(w.spawns.length).toBe(1)
    expect(w.toasts).toContain('Factory Console: http://127.0.0.1:5555/')
  })

  test('a console that cannot start says why', { options: { factoryctl: CTL } }, async ($, on) => {
    const clock = mock.clock(on, { now: 0 })
    const w = world(on, STATE, { spawn: 'fail' })
    await start($)
    await $.command.run({ command: 'factory-console', args: '' } as never)
    await clock.settle()
    expect(w.toasts.some((x) => x.startsWith('Factory Console could not start'))).toBe(true)
  })
})
