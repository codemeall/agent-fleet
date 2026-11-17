// Fleet's worker rows at Claude Code's prompt, for a plugin install: no `fleet statusline --install` step.
//
// A plugin cannot set the session's statusLine, so this hooks module draws the same rows itself, in the band
// directly above the prompt. It runs the launcher the status line runs (skill/bin/fleet-statusline) and draws
// what it prints: one row per live worker in the session's checkout, whatever its provider, and a line for the
// other active repos. It reads run state only, outside the model's context, so the lead spends no tokens on it.
//
// It stays out of the way in three places: inside a Fleet worker (rows whose clocks change would keep the
// worker's screen from settling), in a session with nobody at the prompt, and where Fleet's own status line is
// already installed (the rows would show twice).
import type { Register } from 'claude-code'

const LIVE_MS = 2000 // between refreshes while rows show: the status line's own refreshInterval
const IDLE_MS = 10000 // between refreshes while no worker is live here or in another repo
const RUN_MS = 5000 // what one refresh may take before it is dropped
const ROWS = '6' // worker rows before "+k more", as the status line's default
const SETTINGS_EVERY = 5 // refreshes between two reads of the statusLine setting

/** Whether a statusLine setting already runs Fleet's rows, as `fleet statusline --install` writes it. */
export const isFleetLine = (value: unknown): boolean => {
  const command = (value as { command?: unknown } | null | undefined)?.command

  return typeof command === 'string' && /fleet-statusline\b|\bfleet['"]? statusline\b/.test(command)
}

export const register: Register = on => {
  let lines: string[] = []

  on('session.start', async ($, e, next) => {
    const started = await next(e)

    if (!e.isInteractive || (await $.env.get('FLEET_WORKER'))) {
      return started
    }

    const launcher = `${$.plugin.root}/skill/bin/fleet-statusline`
    const stdin = JSON.stringify({ cwd: e.cwd, workspace: { project_dir: e.cwd } })
    let isInstalled = false
    let refreshes = 0

    const show = (rows: string[]) => {
      if (rows.join('\n') !== lines.join('\n')) {
        lines = rows
        $.ui.invalidate('ui.render')
      }
    }

    const refresh = async (): Promise<boolean> => {
      if (refreshes++ % SETTINGS_EVERY === 0) {
        isInstalled = isFleetLine(await $.settings.read({ key: 'statusLine' }))
        // What `fleet init` reads to tell the lead where the owner sees the workers.
        await $.env.set('FLEET_PROMPT_ROWS', isInstalled ? undefined : '1')
      }

      if (isInstalled) {
        show([])

        return false
      }

      const ran = await $.process.run(['python3', launcher, '--rows', ROWS], { stdin, timeoutMs: RUN_MS })
      const text = ran.exitCode === 0 ? ran.stdout.replace(/\s+$/, '') : ''
      show(text === '' ? [] : text.split('\n'))

      return text !== ''
    }

    const loop = async () => {
      let hasRows = false

      try {
        hasRows = await refresh()
      } catch {
        show([]) // no python3, a refresh past RUN_MS: no rows, and the next one tries again
      }

      $.clock.after(hasRows ? LIVE_MS : IDLE_MS, () => void loop())
    }

    void loop()

    return started
  })

  on('ui.render', { component: 'AbovePrompt' }, ($, e, next) => {
    if (e.props.hasSurvey || lines.length === 0) {
      return next(e)
    }

    const { Box, Text } = $.ui.resolve(e)

    return (
      <Box flexDirection="column">
        {lines.map(line => (
          <Text dimColor={!line.startsWith('●')} wrap="truncate-end">
            {line}
          </Text>
        ))}
      </Box>
    )
  })
}
