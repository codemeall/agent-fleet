// Run with: claude plugin test <this plugin's folder>
import { expect, test } from 'claude-code/testing'
import type { TestBody } from 'claude-code/testing'

const BAND = {
  plugin: 'agent-fleet',
  component: 'AbovePrompt',
  props: {
    hasSurvey: false,
    isCollapsed: false,
    isWorking: false,
    maxRows: 20,
    bodyColumns: 120,
    scroll: { offset: 0, bodyRows: 20 },
    view: {},
  },
} as const

const ROWS = [
  'fleet demo · 2 live',
  '● 01  claude:claude-opus-5-5@high  working · 12s',
  '● 02  codex:gpt-6-sol@high         working · 9s',
].join('\n')

type Ran = { argv: readonly string[]; stdin?: string }

// The first refresh starts as the session does and is not waited on: let it finish.
const settle = async () => {
  for (let i = 0; i < 50; i++) {
    await Promise.resolve()
  }
}

// The engine beneath the plugin: what the launcher prints, the statusLine setting, the environment, and a clock
// whose wait never ends, so one refresh runs.
const engine = (
  on: Parameters<TestBody>[1],
  stdout: string,
  { statusLine, worker }: { statusLine?: unknown; worker?: string } = {},
) => {
  const ran: Ran[] = []
  const env: Record<string, string | undefined> = {}

  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('settings.read', () => ({ value: statusLine === undefined ? {} : { statusLine } }))
  on('env.get', () => ({ value: worker }))
  on('env.set', (_, e) => {
    env[e.name] = e.value

    return { value: {} }
  })
  on('process.run', (_, e) => {
    ran.push({ argv: e.argv, stdin: e.init?.stdin })

    return { value: { exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('clock.after', () => new Promise<never>(() => {}))

  return { ran, env }
}

test('live workers draw as rows above the prompt, from the launcher beside the skill', async ($, on) => {
  const { ran, env } = engine(on, `${ROWS}\n`)

  await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })
  await settle()

  expect(ran.length).toBe(1)
  expect(ran[0]?.argv.slice(0, 1)).toEqual(['python3'])
  expect(ran[0]?.argv[1]).toMatch(/\/skill\/bin\/fleet-statusline$/)
  expect(JSON.parse(ran[0]?.stdin ?? '{}').workspace.project_dir).toBe('/repo')
  expect(env.FLEET_PROMPT_ROWS).toBe('1')

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...BAND, surface })

    expect((await ui.find({ type: 'Text', text: /2 live/ }))?.text).toBe('fleet demo · 2 live')
    expect(await ui.find({ type: 'Text', text: /01 {2}claude:claude-opus-5-5@high/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /02 {2}codex:gpt-6-sol@high/ })).toBeDefined()
    await ui.unmount()
  }
})

test('no live worker draws no row', async ($, on) => {
  engine(on, '')

  await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })
  await settle()

  const ui = await $.ui.mount({ ...BAND, surface: 'terminal' })

  expect(await ui.find({ type: 'Text', text: /fleet/ })).toBeUndefined()
  await ui.unmount()
})

test("where Fleet's own status line is installed nothing runs and the lead is not told of rows", async ($, on) => {
  const installed = engine(on, `${ROWS}\n`, {
    statusLine: { type: 'command', command: "'/x/skill/bin/fleet-statusline' --then 'mine' || sh -c 'mine'" },
  })

  await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })
  await settle()
  expect(installed.ran.length).toBe(0)
  expect(installed.env.FLEET_PROMPT_ROWS).toBeUndefined()
})

test('a Fleet worker runs nothing', async ($, on) => {
  const { ran } = engine(on, `${ROWS}\n`, { worker: 'demo/01' })

  await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })
  await settle()
  expect(ran.length).toBe(0)
})

test('a session with nobody at the prompt runs nothing', async ($, on) => {
  const { ran } = engine(on, `${ROWS}\n`)

  await $.session.start({ cwd: '/repo', surface: null, isInteractive: false })
  await settle()
  expect(ran.length).toBe(0)
})
