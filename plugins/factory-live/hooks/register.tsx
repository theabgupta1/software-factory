// factory-live: the software factory inside Claude Code.
//
// A status line, a band above the prompt when a job needs you, notifications when that changes, and a
// board pane (/factory-board) with every job, the agents at work (robot avatars in their own colours),
// "Watch" to open an agent's transcript and "Message" to steer it mid-run. Everything it shows is read
// from the engine's own files; everything it changes goes through factoryctl. It never runs agents.

import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, RenderInput, UiPressArgument } from 'claude-code'

import type { FactoryLiveJob, FactoryLiveSnapshot, FactoryLiveTarget } from '../types'
import {
  ATTENTION_LABEL, attentionSignature, avatarPixels, avatarSvg, factoryctlCandidates, finished, moodFor,
  needsYou, parseAgentMap, parseIndex, parseRoster, primaryCommand, rasterCells, running, shortId, since,
  stageLabel, statusLine, stepLabel, transitions, verdicts,
} from './model'
import type { Mood } from './model'

const PANE = 'factory'
const POLL_MS = 2000
const EMPTY: FactoryLiveSnapshot = { repo: null, jobs: [], readAt: 0, error: null }

const snapshot = atom({ plugin: 'factory-live', key: 'snapshot' } as const, EMPTY)
const agentIds = atom({ plugin: 'factory-live', key: 'agentIds' } as const, {})
const live = atom({ plugin: 'factory-live', key: 'live' } as const, [])
const roster = atom({ plugin: 'factory-live', key: 'roster' } as const, {})
const target = atom({ plugin: 'factory-live', key: 'target' } as const, null)
const bandHidden = atom({ plugin: 'factory-live', key: 'bandHidden' } as const, null)
const consoleUrl = atom({ plugin: 'factory-live', key: 'consoleUrl' } as const, null)

type $ = EngineInterface
type PaneEvent = RenderInput<'Pane'>

// Polling bookkeeping and options; what the surfaces draw lives in $.state. A reload re-runs the module.
const live$ = {
  notify: true,
  showStatus: true,
  configuredCtl: undefined as string | undefined,
  repo: null as string | null,
  ctl: null as string | null,
  lastJobs: null as FactoryLiveJob[] | null,
  indexMtime: -1,
  agentsMtime: -1,
  ticks: 0,
}

export const register: Register = (on, options) => {
  live$.notify = options.notify !== false
  live$.showStatus = options.statusLine !== false
  live$.configuredCtl = typeof options.factoryctl === 'string' && options.factoryctl ? options.factoryctl : undefined

  // ---- lifecycle --------------------------------------------------------------------------------------
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'factory-board', description: 'Open the software factory board: jobs that need you, agents at work, and messages to them' })
    await $.command.register({ name: 'factory-console', description: 'Start the Factory Console (web UI) for this repository and show its address' })
    live$.ctl = await findFactoryctl($)
    if (live$.ctl) await loadRoster($, live$.ctl)
    await refresh($)
    $.clock.every(POLL_MS, () => { void refreshQuietly($) })
    return next(e)
  })

  on('command.run', { command: 'factory-board' }, async ($) => {
    if (!live$.repo) live$.repo = await findRepo($)
    if (!live$.repo) return { text: 'No software factory in this repository yet. Start a job with /factory <what you want>.' }
    await refresh($)
    await openBoard($)
    return { text: 'Factory board opened.' }
  })

  on('command.run', { command: 'factory-console' }, async ($) => startConsole($))

  // ---- the band above the prompt: only when something needs you ---------------------------------------
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey) return next(e)
    const snap = await read($, snapshot)
    const attn = needsYou(snap.jobs)
    const sig = attentionSignature(snap.jobs)
    if (!attn.length || (await read($, bandHidden)) === sig) return next(e)
    const first = attn[0]!
    const cmd = primaryCommand(first)
    const { Box, Text, Button } = $.ui.resolve(e)
    const label = ATTENTION_LABEL[first.attention!.kind] ?? first.attention!.kind
    return (
      <Box flexDirection="row" gap={1} flexWrap="wrap">
        <Text color="warning" bold>◆ {attn.length === 1 ? 'A factory job needs you' : `${attn.length} factory jobs need you`}</Text>
        <Text dimColor wrap="truncate-end">{shortId(first.id)} {label}: {first.title}</Text>
        {cmd ? <Button key="fill" label={cmd.length > 40 ? `${cmd.slice(0, 39)}…` : cmd} hotkey="f" variant="primary"
          onPress={() => { void $.prompt.fill({ text: cmd }) }} /> : null}
        <Button key="board" label="Board" hotkey="b" onPress={() => { void openBoard($) }} />
        <Button key="hide" label="Hide" onPress={() => { void update($, bandHidden, () => sig) }} />
      </Box>
    )
  })

  // ---- the board pane ---------------------------------------------------------------------------------
  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => board($, e))
}

// ---- reading the engine ------------------------------------------------------------------------------
async function findRepo($: $): Promise<string | null> {
  let dir = (await $.session.root()).replace(/\/+$/, '')
  for (let i = 0; i < 8 && dir; i++) {
    if (await $.fs.exists(`${dir}/.factory/index.json`)) return dir
    const up = dir.replace(/\/[^/]+$/, '')
    if (up === dir) break
    dir = up
  }
  return null
}

async function findFactoryctl($: $): Promise<string | null> {
  const root = $.plugin.root
  const candidates = factoryctlCandidates(root, live$.configuredCtl)
  // An installed marketplace copy sits at <cache>/<marketplace>/<plugin>/<version>: look beside it too.
  const grand = root.replace(/\/+$/, '').replace(/\/[^/]+$/, '').replace(/\/[^/]+$/, '')
  try {
    const versions = (await $.fs.list(`${grand}/factory`)).filter((v) => v.kind === 'dir').map((v) => v.name).sort().reverse()
    for (const v of versions) candidates.push(`${grand}/factory/${v}/bin/factoryctl`)
  } catch { /* not a marketplace install */ }
  const configDir = await $.env.get('CLAUDE_CONFIG_DIR')
  const home = await $.env.get('HOME')
  if (configDir) candidates.push(`${configDir}/skills/factory/bin/factoryctl`)
  if (home) candidates.push(`${home}/.claude/skills/factory/bin/factoryctl`)
  for (const c of candidates) {
    if (await $.fs.exists(c)) return c
  }
  return null
}

async function loadRoster($: $, ctlPath: string) {
  try {
    const text = await $.fs.read(ctlPath.replace(/\/bin\/factoryctl$/, '/contracts/agents.json'))
    await update($, roster, () => parseRoster(text))
  } catch { /* titles fall back to agent names */ }
}

async function refreshQuietly($: $) {
  try {
    await refresh($)
  } catch (err) {
    $.ui.log(`factory-live: ${String(err)}`, { to: 'debug' })
  }
}

async function refresh($: $) {
  live$.ticks += 1
  if (!live$.repo) {
    if (live$.ticks > 1 && live$.ticks % 15 !== 0) return // look for a newly initialised repo every 30 s
    live$.repo = await findRepo($)
    if (!live$.repo) return
  }
  const repo = live$.repo
  const index = `${repo}/.factory/index.json`
  let mtime: number
  try {
    mtime = (await $.fs.stat(index)).mtimeMs
  } catch {
    return
  }
  if (mtime !== live$.indexMtime) {
    live$.indexMtime = mtime
    const jobs = parseIndex(await $.fs.read(index))
    for (const t of transitions(live$.lastJobs, jobs)) {
      $.ui.toast(`Factory: ${t.text}`)
      if (live$.notify && t.kind === 'attention') {
        await $.ui.notify(t.text, { title: 'Software factory needs you' }).catch(() => undefined)
      }
    }
    live$.lastJobs = jobs
    const readAt = await $.clock.now()
    await update($, snapshot, () => ({ repo, jobs, readAt, error: null }))
    if (live$.showStatus) $.ui.status(statusLine(jobs))
  }
  const map = `${repo}/.factory/agents.json`
  if (await $.fs.exists(map)) {
    const st = await $.fs.stat(map)
    if (st.mtimeMs !== live$.agentsMtime) {
      live$.agentsMtime = st.mtimeMs
      const ids = parseAgentMap(await $.fs.read(map))
      await update($, agentIds, () => ids)
    }
  }
  if ((live$.lastJobs ?? []).some((j) => j.running_detail.length)) {
    const ids = (await $.agent.list()).filter((a) => a.status === 'running').map((a) => a.id).sort()
    const prev = await read($, live)
    if (ids.join() !== prev.join()) await update($, live, () => ids)
  }
}

// ---- acting through factoryctl -----------------------------------------------------------------------
async function tell($: $, to: FactoryLiveTarget, text: string) {
  const body = text.trim()
  if (!body) return
  if (!live$.ctl || !live$.repo) {
    $.ui.toast('Factory: factoryctl was not found; set the factoryctl option of factory-live')
    return
  }
  const res = await $.process.run(['python3', live$.ctl, '--repo', live$.repo, 'tell', to.job, '--step', to.step, '--', body],
    { timeoutMs: 15000 })
  if (res.exitCode === 0) {
    $.ui.toast(`Sent to ${to.title}: it reads it at its next tool call`)
    await update($, target, () => null)
  } else {
    $.ui.toast(`Factory: message not sent: ${(res.stderr || res.stdout).trim().slice(0, 200)}`)
  }
}

async function openBoard($: $) {
  const opened = await $.ui.open({ id: PANE, title: 'Factory', focus: true })
  if (!opened.isPlaced) $.ui.log(`factory-live: the board is waiting for room (${opened.reason})`, { to: 'debug' })
}

async function startConsole($: $) {
  const known = await read($, consoleUrl)
  if (known) return { text: `The Factory Console is running at ${known}` }
  if (!live$.repo) live$.repo = await findRepo($)
  const repo = live$.repo
  const ctl = live$.ctl
  if (!ctl || !repo) return { text: 'factoryctl or a factory repository was not found, so the console cannot start here.' }
  // The server lives as long as this module: leaving the loop (or a reload) stops it.
  void (async () => {
    for await (const piece of $.process.spawn({ argv: ['python3', ctl, '--repo', repo, 'serve'] })) {
      const m = piece.stream === 'stdout' ? piece.text.match(/"url":\s*"([^"]+)"/) : null
      if (m?.[1]) {
        const url = m[1]
        await update($, consoleUrl, () => url)
        $.ui.toast(`Factory Console: ${url}`)
      }
    }
    await update($, consoleUrl, () => null)
  })()
  return { text: 'Starting the Factory Console; its address appears in a moment (and on the board).' }
}

// ---- drawing -----------------------------------------------------------------------------------------
async function board($: $, e: PaneEvent) {
  const { Box, Text, Button } = $.ui.resolve(e)
  const snap = await read($, snapshot)
  const ids = await read($, agentIds)
  const liveIds = new Set(await read($, live))
  const meta = await read($, roster)
  const to = await read($, target)
  const url = await read($, consoleUrl)
  const now = await $.clock.now()
  const narrow = (e.props.bodyColumns ?? e.viewport?.columns ?? 80) < 70
  const title = (agent: string | null) => (agent && meta[agent]?.title) || agent || 'agent'
  const hue = (agent: string | null) => meta[agent ?? '']?.hue ?? 220
  const section = (label: string) => <Text color="subtle" bold>{label.toUpperCase()}</Text>

  if (!snap.repo) {
    return (
      <Box flexDirection="column" gap={1}>
        <Text bold>Software factory</Text>
        <Text dimColor>No factory state in this repository yet. Start a job with /factory &lt;what you want&gt;.</Text>
      </Box>
    )
  }

  const attn = needsYou(snap.jobs)
  const run = running(snap.jobs)
  const done = finished(snap.jobs).slice(0, 5)
  const workers = run.flatMap((j) => j.running_detail.map((r) => ({ job: j, ...r })))
  const repoName = snap.repo.replace(/^.*\//, '')

  return (
    <Box flexDirection="column" gap={1}>
      <Box flexDirection="row" justifyContent="space-between">
        <Text bold>Factory · {repoName}</Text>
        <Text dimColor>{attn.length} need you · {run.length} running · {workers.length} agents</Text>
      </Box>

      {attn.length ? (
        <Box flexDirection="column">
          {section('Needs you')}
          {attn.map((j) => {
            const cmd = primaryCommand(j)
            return (
              <Box key={`attn:${j.id}`} flexDirection="row" gap={1}>
                {avatar($, e, `av:attn:${j.id}`, hue('approver'), moodFor(j, null), 'Approver')}
                <Box flexDirection="column" flexShrink={1}>
                  <Text wrap="truncate-end"><Text bold>{shortId(j.id)}</Text> {j.title}</Text>
                  <Text color="warning" wrap="truncate-end">{ATTENTION_LABEL[j.attention!.kind] ?? j.attention!.kind}{j.attention!.reason ? `: ${j.attention!.reason}` : ''}</Text>
                  {cmd ? (
                    <Box flexDirection="row" gap={1}>
                      <Button key={`fill:${j.id}`} variant="primary" label={narrow ? 'Put in prompt' : `Put in prompt: ${cmd}`}
                        onPress={() => { void $.prompt.fill({ text: cmd }) }} />
                      <Button key={`copy:${j.id}`} label="Copy" onPress={(p: UiPressArgument) => { void $.ui.copy({ text: cmd, surface: p.surface }) }} />
                    </Box>
                  ) : null}
                </Box>
              </Box>
            )
          })}
        </Box>
      ) : null}

      {workers.length ? (
        <Box flexDirection="column">
          {section('Working now')}
          {workers.map((w) => {
            const agentId = ids[`${w.job.id}/${w.step}`]
            const isLive = agentId !== undefined && liveIds.has(agentId)
            const chosen = to?.job === w.job.id && to.step === w.step
            const pick: FactoryLiveTarget = { job: w.job.id, step: w.step, agent: w.agent ?? '', title: title(w.agent) }
            return (
              <Box key={`w:${w.job.id}/${w.step}`} flexDirection="row" gap={1} alignItems="center">
                {avatar($, e, `av:${w.job.id}/${w.step}`, hue(w.agent), 'working', title(w.agent))}
                <Box flexDirection="column" flexGrow={1} flexShrink={1}>
                  <Text bold={chosen} wrap="truncate-end">{title(w.agent)}</Text>
                  <Text dimColor wrap="truncate-end">{shortId(w.job.id)} · {stepLabel(w.step)} · {since(w.since, now)}</Text>
                </Box>
                {isLive ? <Button key={`watch:${w.job.id}/${w.step}`} label="Watch" onPress={() => { void $.ui.view({ agentId: agentId! }) }} /> : null}
                <Button key={`msg:${w.job.id}/${w.step}`} label={chosen ? 'Messaging' : 'Message'}
                  onPress={() => { void chooseTarget($, pick) }} />
              </Box>
            )
          })}
        </Box>
      ) : null}

      {to ? (
        <Box flexDirection="column">
          {section(`Message ${to.title}`)}
          {e.surface === 'mobile'
            ? <Text dimColor>Messaging needs a keyboard; use the Factory Console or /factory tell.</Text>
            : messageBox($, e, to)}
          <Text dimColor>The factory hook hands it to the agent at its next tool call; the console shows the receipt.</Text>
        </Box>
      ) : null}

      {run.length ? (
        <Box flexDirection="column">
          {section('Running jobs')}
          {run.map((j) => (
            <Box key={`job:${j.id}`} flexDirection={narrow ? 'column' : 'row'} gap={narrow ? 0 : 2}>
              <Text wrap="truncate-end"><Text bold>{shortId(j.id)}</Text> {j.title}</Text>
              <Text dimColor>{stageLabel(j)}{Object.keys(j.reviews).length ? `  ${verdicts(j.reviews)}` : ''}{j.pending_messages ? `  ✉ ${j.pending_messages} queued` : ''}</Text>
            </Box>
          ))}
        </Box>
      ) : null}

      {done.length ? (
        <Box flexDirection="column">
          {section('Finished')}
          {done.map((j) => (
            <Text key={`done:${j.id}`} dimColor wrap="truncate-end">{shortId(j.id)} {j.status === 'merged' ? '✓ merged' : j.status} · {j.title}</Text>
          ))}
        </Box>
      ) : null}

      {!attn.length && !run.length && !done.length ? <Text dimColor>No jobs yet. Start one with /factory &lt;what you want&gt;.</Text> : null}

      {url ? <Text dimColor>Console: {url}</Text>
        : <Button key="console" label="Open the web console" onPress={() => { void $.prompt.fill({ text: '/factory-console' }) }} />}
    </Box>
  )
}

async function chooseTarget($: $, pick: FactoryLiveTarget) {
  await update($, target, () => pick)
  // The field autofocuses when the pane holds the keys; moving the ring is a nicety that may be refused.
  await $.ui.focus({ requestId: PANE, key: 'msg' }).catch(() => undefined)
}

/** The agent's robot: cells on the terminal, an SVG on the other surfaces; one pixel matrix for both. */
function avatar($: $, e: PaneEvent, key: string, hue: number, mood: Mood, who: string) {
  const pixels = avatarPixels(hue, mood)
  if (e.surface === 'terminal') {
    const { Raster } = $.ui.resolve(e as PaneEvent & { surface: 'terminal' })
    const r = rasterCells(pixels)
    return <Raster key={key} columns={r.columns} rows={r.rows} cells={r.cells} />
  }
  const { Svg } = $.ui.resolve(e as PaneEvent & { surface: 'desktop' })
  return <Svg source={avatarSvg(pixels)} alt={`${who} (${mood})`} width={24} height={24} />
}

/** The message field; Input exists on every surface but mobile, which the caller handles. */
function messageBox($: $, e: PaneEvent, dest: FactoryLiveTarget) {
  const { Box, Button, Input } = $.ui.resolve(e as PaneEvent & { surface: 'terminal' })
  return (
    <Box flexDirection="row" gap={1}>
      <Input key="msg" label="›" placeholder={`Tell ${dest.title} what to change…`} submitLabel="send" autoFocus
        onSubmit={(value: string) => { void tell($, dest, value) }} />
      <Button key="cancel-msg" label="Cancel" onPress={() => { void update($, target, () => null) }} />
    </Box>
  )
}
