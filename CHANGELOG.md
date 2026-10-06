# Changelog

## Unreleased

Helps the lead keep heavy models off light tickets.

- The ticket-tier rule moved from the routing reference into the skill's planning step, so the lead sees it while choosing tiers. It adds a tie-break: between two tiers, pick the lighter unless the ticket touches a heavy area. A light ticket that needs more reasoning gets a higher `effort` pin, not a heavier tier.
- The skill notes that `fleet steer model` replaces every tier's model, light tickets included, and suggests steering only the provider, which keeps its tier models.
- The `steered:` line from `fleet launch` now names the ticket's tier (`steered (light): claude:sonnet -> claude:opus`), so the lead can see when a light ticket is upgraded.

## 0.1.0-preview.11

Fleet no longer types into the wrong terminal after a cmux restart, and `feature-docs` joins the optional companion skills.

- `fleet send`, `peek`, `stop` and the stall check in `fleet wait` now address a worker's surface by the UUID recorded at launch. Refs like `surface:4` restart from 1 with cmux, so after a restart a saved ref could name someone else's terminal, and `fleet stop` would have typed the quit keys into it. A worker whose UUID was never recorded is still addressed by its ref.
- README: a hold does not make a shutdown safe. Workers killed by a shutdown leave no exit receipt and stay listed as running after `fleet release`; the README says how to recover and resume them, and example 7 warns that a pid can be reused after a reboot.
- `feature-docs` (from codemeall/feature-init) is a new optional companion skill. After a run is verified, the lead can use it to write or sync `docs/<feature>/feature-docs/`, the tracked record of a feature whose spec and tickets stay in the untracked `.scratch/` folder. `fleet doctor` reports where it is installed, and the skill, README, host reference and worked examples say when the lead uses it.

## 0.1.0-preview.10

A worker that hits its usage limit can continue its own session instead of starting over.

- Claude workers now start with `--session-id` set to a UUID that Fleet records on the worker. After the limit resets, `fleet launch <run> <id> <provider> --resume` relaunches with `--resume <id>`, so the worker keeps its conversation and reasoning. A short continuation prompt points it at its prompt file (for any Repair note) and its report path, since relaunch archives the old report. Exit tracking, capacity and file ownership work as for any launch.
- `--resume` refuses when the worker has no saved session (adapters without `resume`, or workers launched before this version) and when a steer would move it to another provider: a session stays with the account that ran it.
- Adapters opt in with a `{session}` slot in `launch` plus `session` and `resume` argument fragments in `providers.toml`. Only Claude ships them. A user `launch` override without the slot keeps the old behavior.
- `STALLED` now names a usage limit among the likely causes. The skill says how to handle one: resume after the reset, or steer within the run's routing and relaunch fresh. It no longer disagrees with the provider reference about whether that needs the owner.

## 0.1.0-preview.9

- `fleet doctor` groups companion skills under their source (`manaflow-ai/cmux`, `mattpocock/skills`), so the output names where each one comes from even when it is installed.

## 0.1.0-preview.8

Shows which Fleet version is running, and which optional companion skills the lead has.

- Add `fleet version` and `fleet --version`. They print the runtime's version (and, for `version`, its skill directory) without reading the user config. The version lives in `skill/bin/fleet`, so skills CLI and plugin installs carry it too. A test keeps it equal to `package.json`.
- `fleet doctor` prints the runtime's version first and shows each host's installed `fleet` skill with its version, flagging copies that differ from the runtime.
- `fleet doctor` reports the optional companion skills: `cmux` (manaflow-ai/cmux), and `grill-with-docs`, `grill-me`, `to-spec` and `to-tickets` (mattpocock/skills). It checks user-level skill directories and the current directory's `.claude/skills`, `.agents/skills` and `.cursor/skills`, and prints an install command for each one it can't find. Companion skills never change doctor's exit code.
- README: new "Companion skills (optional)" section with install commands, and `fleet version` and the new doctor output under Verify. The skill and host reference say when the lead may use the companions; the `cmux` skill stays inspection-only under the focus rule.

## 0.1.0-preview.7

Lets the owner steer, pause and reset a long-running fleet safely: reroute launches, hold new ones, and hand off or compact the lead between waves.

- Add `fleet steer` so the lead can reroute future implementation launches without editing the frozen plan: `fleet steer provider <run> <name>` (tier model and effort, or the `agents=` pinned model), `fleet steer model <run> <name>` (needs a steered provider; checked against `models_allow`), `fleet steer reset <run>`. The steer lives in the run's `live.json`, stays inside the run's routing, and is shown by `fleet resume`. Launch prints `steered: ...` and records the worker's actual route with `steered: true`. Open panes and review launches are never steered, and a review now checks its family against the writer's recorded family rather than the plan's. A steered launch of a ticket that needs cross-family review refuses a model with no known family.
- Add `fleet hold [--reason <text>]` and `fleet release` to pause new launches for the whole checkout without touching running workers. While held, `fleet launch` refuses before any side effect; `wait`, `peek`, `send`, `stop`, `verify` and `recover` work as usual, so running workers finish and can be verified. `fleet wait` on a held run with no running worker returns at once with `HOLD` instead of polling, and `status`/`resume` show the hold. The hold lives in `.fleet/runs/hold.json` and has no expiry: only `fleet release` lifts it.
- Add `fleet handoff <run> [--compact] (--note TEXT … | --no-notes)`. It refuses while a background `fleet wait` is running or before the plan is saved, appends a `## Handoff` entry to the run's `notes.md` (running workers; reports and exited workers awaiting the next lead; the lead's notes) and prints the owner's steps for the lead's host: `/clear` + `/fleet resume`, `/compact` with instructions, or the Codex equivalents. `fleet wait` records its pid while it runs so handoff can tell.
- Run commands measure the lead's own context from its session log (Claude Code via `CLAUDE_CODE_SESSION_ID`, Codex via `CODEX_THREAD_ID`) and, after a large drop, add one `LEAD compacted …` line on stderr telling the lead to run `fleet resume` before acting. Stdout, `--json` output and exit codes are unchanged, and a reading that fails prints nothing. A response with a server-side tool call (such as the advisor) is read from its last step, not its summed usage.
- Add `fleet context [--json]` to show the calling lead's context size.
- Add optional `[lead_context.claude]` and `[lead_context.codex]` marks (`warn_at`, `dumb_at`; none by default). They add a size line to run commands; the lead tells the owner and never hands off on its own.
- Skill: the lead reports its context size with each wave, hands off or prepares for compaction only when the owner asks, and resumes from disk after compaction. New reference `references/lead-context.md`; the README has a matching section for owners.
- Skill: new `references/examples.md` with worked command sequences for option mapping, blocked workers, change requests, cross-family review with a fix round, evidence files, repair, recovery, follow-up runs and finishing a run, each checked against the CLI.

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
