# Changelog

## Unreleased

- Add `fleet hold [--reason <text>]` and `fleet release` to pause new launches for the whole checkout without touching running workers. While held, `fleet launch` refuses before any side effect; `wait`, `peek`, `send`, `stop`, `verify` and `recover` work as usual, so running workers finish and can be verified. `fleet wait` on a held run with no running worker returns at once with `HOLD` instead of polling, and `status`/`resume` show the hold. The hold lives in `.fleet/runs/hold.json` and has no expiry: only `fleet release` lifts it.

## 0.1.0-preview.6

- `fleet launch` opens each worker in a new pane instead of a tab in an existing one, splitting the largest pane (along its long side) until the workspace has 8 panes. At 8 it replaces a pane, other than the lead's, that holds only verified workers whose exit is confirmed, opening the new worker there and closing their surfaces; failing that, it adds a tab to an idle pane (no live worker), so an unverified worker keeps its scrollback. When every other pane has a live worker it refuses before touching the run (no baseline snapshot, no moved report). `--pane` now chooses the pane to split, replace or add a tab to, and accepts a pane ref, UUID or index as listed by `cmux list-panes`.
- Workers record their cmux surface UUID at launch. Replacing a pane and `fleet stop --close` close surfaces by UUID, so a short ref that cmux reuses after a restart can never close someone else's terminal. Workers launched before this change have no UUID, so their panes are never replaced and `--close` falls back to the ref.

## 0.1.0-preview.5

- `fleet launch` gives each worker its own pane instead of stacking tabs in the lead's pane. It reuses the first pane (other than the lead's) with no live worker, including idle shells and panes whose workers have exited, and splits the largest pane along its long side once every pane is taken. `--pane` still places a worker explicitly.

## 0.1.0-preview.4

Cuts the lead's token use during long runs without loosening any verification gate.

- `fleet wait` accepts up to 600 seconds (other commands keep their 60-second limit). Besides reports it now wakes when a worker exits before handing back (`EXITED`), when a working worker's screen stays unchanged for `--stall` seconds (`STALLED`, default 180, catching permission, login and trust prompts, including after the lead answered a blocked worker) and when its pane can no longer be read (`UNREACHABLE`, once per episode). Each event wakes the lead once; `TIMEOUT` lists reports still waiting on the lead as `PENDING`. `wait` still saves no run state; `fleet send` now records when the lead answered.
- Plans accept per-ticket `context` and `checks` (text or a list). `fleet prompt` fills them in, so the lead no longer rereads and edits every prompt; missing fields stay `LEAD` comments that `launch` refuses, and `prompt` names them. Empty values are rejected.
- Fix: prompts substitute known placeholders in one pass, so `{{…}}` in `.fleet/rules.md`, `context` or `checks` (GitHub Actions, Handlebars, Jinja) is kept as content and no longer blocks launch; launch names any known placeholder still unfilled.
- `fleet resume` prints one line per ticket plus decisions, workers (with pid and return code) and the notes path; `--json` prints the full records.
- `fleet peek` shows the last 20 non-padding lines by default instead of 40 raw lines.
- Skill: wait with the host's longest command timeout, one wait at a time, and do not peek on every timeout; peek early after launch instead; run combined-tree checks once per verification batch rather than once per ticket; a fresh lead session may continue a long run from `fleet resume`. `routing.md` points to the skill instead of repeating its review and recovery steps.
- The example plan carries `context` and `checks`, so its prompts need no editing.

## 0.1.0-preview.3

- Codex tiers now ship `gpt-6.1-sol` for heavy, standard and review (Codex CLI 0.159). `light` stays `gpt-6-luna`.
- `doctor` warns when the Codex catalog marks a tier model for retirement, with the date and replacement; `fleet models codex` shows the same.

## 0.1.0-preview.2

- Add extra subscriptions as providers: `extends` reuses an adapter, `env` points it at its own account directory, and `fleet account add` writes the block to the user config.
- The bundled `claude-co` example now uses `extends = "claude"` with `env`, and `max = 1`.
- Fix: the `claude` and `codex` login checks inherited `CLAUDE_CONFIG_DIR`/`CODEX_HOME` from the lead, so a lead running under a second account could report the wrong account. Adapters with `account_env` now clear it unless their `env` sets it. If your primary account uses a custom directory, set it in that provider's `env`.
- `doctor` checks each tier model against the account's list (Codex via `codex debug models`, Cursor, Antigravity) and fails on a model, or Codex effort, that isn't offered.
- Add `fleet models <provider> [filter]` to list the account's model IDs, marking the ones tiers use.
- Add `models_allow`: the owner's list of models the lead may choose, with optional families. It narrows a live list or stands in for a missing one (Claude). Plans and review launches outside it are refused. Tier models always count as allowed. Ships `opus`/`sonnet` for Claude and a starter set with families for Cursor.
- Add `efforts`: the effort levels a CLI accepts when it cannot report them per model. Claude ships `low` to `max`; doctor, plans and review launches reject anything else.
- README: add an Updating section and the missing git-clone update step.
- `doctor` fails when two providers are signed in to the same account, and lists skill installs for extra Claude accounts from config.

## 0.1.0-preview.1

Initial distributable preview, under the MIT license.

- Published to npm as `@codemeall/agent-fleet` under the `preview` tag: `npx @codemeall/agent-fleet@preview setup`.
- Install with the skills CLI: `npx skills add codemeall/agent-fleet`.
- Install from GitHub without npm: `npx github:codemeall/agent-fleet setup`.
- Add a Claude Code plugin marketplace manifest (`/plugin marketplace add codemeall/agent-fleet`).
- Restructure the README around installation methods.
- Persist ticket dependencies, exact ownership, model/effort assignments and decisions; resume existing runs.
- Track worker exit receipts independently from reports. Failed stops retain capacity; reviewers can use a released writer slot even at capacity one.
- Gate acceptance on lead evidence and required cross-family review of the current scoped diff.
- Compare full Git index entries, quote model arguments, validate IDs and paths, and serialize mutations within a checkout.
- Provide dedicated reviewer prompts and current Matt Pocock skill integration guidance.
- Install durable skill copies for Claude Code, Codex and Cursor, preserving modified or unrelated installations.
- Package the runtime for npm, a Claude plugin at the source root, and a generated Codex plugin bundle. Include MIT notices in standalone copies.
- Add CI and 43 tests, including a real child-process exit receipt and installation from an npm tarball.

Validated locally: automated tests, JavaScript syntax, Claude plugin manifest, generated Codex plugin and skill, and package installation. Live authenticated provider runs and marketplace installation remain unverified. The npm package has not been published by this change.

Existing prototype run records are not automatically migrated: inspect and stop their workers before starting a new run. New run records use schema version 2.
