# Provider adapters

`providers.toml` is the shipped adapter configuration; `fleet providers` prints it merged with personal overrides. Adapter command templates are trusted local executable configuration, not safe inputs from tickets. Model, effort and prompt substitutions are shell-quoted by the runtime. Keep account setup separate from model data.

All model IDs and efforts are examples tied to accounts and CLI versions. Before routing, authenticate through the owner's normal process. `doctor` checks binary presence and login signals, including the login command's exit status, and checks every tier model against the account's list where the CLI can list models (`models` field: Cursor, Antigravity, OpenCode, and Codex through `codex debug models`, which also lists each model's efforts). For Codex doctor also shows the configured default from `config.toml`; Claude's `opus`/`sonnet` aliases follow the current model. Doctor spends no model call and does not guarantee model access. A passing doctor is a preflight check, not an end-to-end compatibility claim.

## Tested CLI versions

What each shipped adapter was last checked against, from the changelog and the reviews in this repository. **Live-checked** means a real, signed-in CLI ran in a pane: launch with a first prompt, follow-up input and the quit sequence back to the shell. **Flags read from `--help` only** means the launch line was written from the CLI's help output; no Fleet worker was run on it.

| Adapter | CLI (`bin`) | Version | Checked | How |
| --- | --- | --- | --- | --- |
| `claude`, `claude-co` | Claude Code (`claude`) | 2.1.318 (2.1.284 earlier) | 2026-11-04 (2026-09-30) | Flags read from `--help` only |
| `codex` | Codex CLI (`codex`) | 0.156.1; tier models from 0.159's catalog | 2026-09-30 | Flags read from `--help` only |
| `cursor` | Cursor Agent (`cursor-agent`) | 2026.09.28-64d2043 | 2026-09-30 | Flags read from `--help` only |
| `agy` | Antigravity (`agy`) | 1.3.0 live; models and efforts listed on 1.3.1 and 1.4.8 | 2026-10-06 (listing 2026-10-27) | Live-checked on 1.3.0; 1.4.8 only listed, not launched |
| `opencode` | OpenCode (`opencode`) | 1.18.34 | 2026-10-06 | Live-checked |

`tests/test_adapters_smoke.py` runs every shipped adapter through `fleet launch`, `wait` and `stop` in headless tmux against a fake executable named after its `bin`, which accepts only the exact command line the adapter builds (model, effort, session and resume fragments, Codex's `-c` overrides, Cursor's `--trust`, OpenCode's `--prompt`) and quits only on the adapter's own quit sequence. Those tests use fakes: they prove the launch line's shape and the quit sequence, not that a CLI version accepts them, that the account is signed in or that a model is offered. Re-check a CLI live after it changes its flags, and update this table.

### Profile flags checked

What was checked on 2026-11-05 for the [launch profiles](#permission-profiles). Only Claude Code is installed where this was done; nothing was launched.

| Adapter | Flags its launch line and profiles use | How they were checked |
| --- | --- | --- |
| `claude`, `claude-co` | `--session-id`, `--resume`, `--model`, `--effort`, `--permission-mode manual` | `claude --help` on 2.1.318: every flag listed, and `manual` is one of `--permission-mode`'s choices (`acceptEdits`, `auto`, `bypassPermissions`, `manual`, `dontAsk`, `plan`). The 2.1.318 binary's own mode table titles it "Manual" and stores it as `default`, the name older versions' `--help` used. `fleet doctor claude` printed `flags listed by claude --help`. |
| `codex` | `-m`, `-c`, `-s workspace-write`, `-a on-request` | Read, not run, in the Codex source at commit `9ff072d` (2026-11-04): `utils/cli/src/shared_options.rs` (`-m, --model`; `-s, --sandbox` with `read-only`, `workspace-write`, `danger-full-access`), `tui/src/cli.rs` (`-a, --ask-for-approval`: `on-request` or `never`), `utils/cli/src/config_override.rs` (`-c, --config`, global). No `codex --help` was run. |
| `cursor` | `--model`, `--trust` | Not checked: no Cursor CLI here. **`--trust` in the interactive TUI is unverified**: it is in the shipped line and the smoke fake accepts it, but no real Cursor run has shown that the TUI honours it rather than rejecting or ignoring it. Check it with the `fleet smoke` path (#32) on a machine with Cursor signed in, and record the result here. Cursor's docs also name the CLI `agent`; that was not checked either, and doctor only names `agent` when `cursor-agent` is missing. |
| `agy` | `--model`, `--effort`, `--mode accept-edits`, `-i` | Unchanged since the 1.3.0 live check and the 1.4.8 `--help` listing; not re-checked. |
| `opencode` | `--model`, `--prompt` | Unchanged since the 1.18.34 live check. Its docs (opencode.ai) were unreachable from this session, so no narrower agent for reviewers was checked and none is shipped. |

`fleet doctor` repeats the help check on the owner's machine for every enabled adapter (see [below](#permission-profiles)), which is how drift in a newer CLI shows up.

### Headless lines checked

What was checked on 2026-11-05 for [headless reviewers](#headless-reviewers). Nothing was launched against a real account: the lines' shape and Fleet's handling of each CLI's output are proven by fakes in `tests/test_headless.py`, not by a real run.

| Adapter | Headless line | How it was checked |
| --- | --- | --- |
| `claude`, `claude-co` | `-p --output-format json --model … --effort … --permission-mode manual --permission-prompts none` | `claude --help` on 2.1.318 lists every flag: `-p/--print`, `--output-format` (`json`: "single result"), `manual` among `--permission-mode`'s choices, and `--permission-prompts host\|none`, where `none` means "anything that would prompt is denied automatically; the permission mode still decides everything else". The headless docs (code.claude.com/docs/en/headless, read 2026-11-05) say a manual-mode `-p` run denies any call that needs approval and that `--permission-prompts none` tells Claude not to retry it (2.1.259 or later); the Agent SDK reference gives the `json` result's fields: `result` (the final text), `is_error`, `usage` (`input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`) and `permission_denials` (`tool_name`, `tool_input`). `fleet doctor claude` with `review_transport = "headless"` printed no headless-flag warning. |
| `codex` | `exec --json -m … -c model_reasoning_effort=… -s read-only -c approval_policy=never` | Read, not run, in the Codex source at commit `9ff072d` (2026-11-04): `exec/src/cli.rs` (`--json` prints JSONL events; a prompt argument with stdin that is not a terminal only appends a non-empty stdin, so Fleet passes an empty one), `exec/src/lib.rs` (exec sets `approval_policy = never` unless the owner's config turns on `auto_review`, hence the explicit `-c approval_policy=never`), `exec/src/exec_events.rs` (`item.completed` with an `agent_message` carries the final text; `turn.completed` carries `usage`: `input_tokens`, which includes `cached_input_tokens`, `output_tokens`, `cache_write_input_tokens`), `protocol/src/protocol.rs` (`never`: failures go back to the model and are never escalated). No `codex exec --help` was run. |
| `cursor` | none | No Cursor CLI here and nothing checked; its print mode is not shipped. |
| `agy` | none | Its non-interactive mode was never checked; none is shipped. |
| `opencode` | none | `opencode run` exists (it takes `--variant`), but its output format and a read-only agent were not checked (its docs were unreachable), so none is shipped. |

An owner may add a `headless` line for another adapter in the user config once they have checked it: its own flags must deny every write and every prompt, since Fleet counts it as read-only.

## Turning providers on and off

Which providers are on is the owner's decision. `fleet doctor` shows each one as `off`, as a sign-in result, or as `PLACEHOLDER`.

| Shipped | Providers |
| --- | --- |
| On | `claude`, `codex`, `cursor` |
| Off, supported | `agy` (Antigravity), `opencode`, `claude-co` (a second Claude account) |
| Off, placeholder | `grok`, `muse` ([not supported](#placeholder-adapters)) |

```bash
fleet provider enable agy       # turn a provider on
fleet doctor agy                # then check its binary, sign-in and tier models
fleet provider disable cursor   # turn one off, a shipped one included
```

`fleet provider enable|disable <name>` sets `enabled` under `[providers.<name>]` in `~/.config/agent-fleet/config.toml` (or `$FLEET_CONFIG`), creating the file or the section if needed. Every other line stays as it was, including comments (a comment on the `enabled` line is kept as written) and line endings; a symlinked config is written through to its target with its permissions. It does nothing when the provider is already in that state. If it cannot make the edit safely, for example when the config sets the provider through a dotted key or an inline table, it changes nothing and says so; the owner then edits that line by hand. A non-boolean value such as `enabled = "yes"` makes every Fleet command fail until the owner fixes it by hand. Editing the file by hand does the same thing as the command:

```toml
[providers.agy]
enabled = true

[providers.cursor]
enabled = false
```

- **Auto routing** uses enabled, authenticated providers in `defaults.prefer` order. Turning a provider on does not add it there; add its name to `prefer` under `[defaults]` yourself. `single:<name>` and `agents=` routing can name it without that; `fleet init` refuses such routing when a named provider is off or a placeholder.
- **Turning one off** stops new plans, launches, steers and review launches that name it. Workers already running on it keep running until they finish or you stop them. To move a live run off a failing provider, steer it ([skill](../SKILL.md#steering-routing)) rather than turning the provider off mid-run. A run steered to a provider that is then turned off cannot launch until `fleet steer reset <run>` or a steer elsewhere.
- **Sign-in** is separate. Enabling a provider never signs it in: the owner signs the CLI in, then runs `fleet doctor <name>`.
- **Placeholders** cannot be turned on until they are filled in ([below](#placeholder-adapters)).

### Agent rules

A Fleet lead, or any other agent working in the session, follows these rules:

- Turn a provider on or off only when the owner asks for that provider in the session ("turn on Antigravity", "stop using Cursor"). A routing request such as `single:agy` that names a provider that is off is not that request: tell the owner it is off and ask.
- Use `fleet provider enable|disable <name>`, never a hand edit of the owner's config, then run `fleet doctor <name>` and report the result. If the command refuses, show the owner its message; don't edit the file. Adding to `defaults.prefer` is also the owner's call: tell them the line, don't write it.
- Never turn a provider on to get around a failure, a usage limit, a permission denial or a login prompt, and never turn one off to route a run somewhere else. Steer within the run's routing, or ask the owner.
- Never sign a CLI in, answer its login or trust prompt, or set `placeholder = false`.

## Folder trust

Claude Code, Codex and Antigravity ask once per folder whether to trust it. With `auto_trust = true` under `[defaults]` in the owner's `~/.config/agent-fleet/config.toml` (off by default), launch marks the repo trusted first, the way each adapter's `trust` field says:

| `trust` | CLI | What launch does |
|---|---|---|
| `claude-config` | Claude Code and its extra accounts | sets the project's `hasTrustDialogAccepted` in that account's `.claude.json` (`CLAUDE_CONFIG_DIR`, else `~`) |
| `codex-flag` | Codex | passes `-c projects={"<repo>"={trust_level="trusted"}}`: trust for that session only, nothing saved |
| `agy-settings` | Antigravity | adds the repo to `trustedWorkspaces` in `~/.gemini/antigravity-cli/settings.json` |

Cursor always launches with `--trust`; OpenCode asks nothing. Launch never creates a CLI's file: a CLI that has not run yet, or a file it cannot read or write, gets one stderr line and the worker shows its trust screen as before. `auto_trust` is the owner's decision. The lead never turns it on, and never answers a trust, login or permission prompt with `fleet send`.

## Permission profiles

Each adapter launches a role through a profile. `profiles.worker` runs implementation tickets. `profiles.review` runs reviewers and investigate tickets, which write only their report. A profile has `args`, the fragment that fills the launch line's `{profile}` slot (dropped when empty), and a `level`, what that launch lets the CLI do without the owner answering in its pane:

| Level | Meaning |
| --- | --- |
| `read-only` | Changes nothing unasked: every edit, and every command that is not read-only, waits for the owner. |
| `workspace-write` | Edits files in the checkout without asking; commands that could reach further run in a sandbox or wait for the owner. |
| `full-access` | Edits and runs any command without asking. |

`[defaults] permission_ceiling` (default `workspace-write`) is the widest level Fleet will start. `fleet launch` (and the reviewer `fleet review` picks) refuses a profile above it, naming the adapter, the profile and the ceiling, before it touches the run. `fleet doctor` fails an enabled adapter with such a profile. Raising the ceiling is the owner's decision; the lead never edits it, and never routes to another provider just to get under it without telling the owner.

| Adapter | `worker` | `review` | Why |
| --- | --- | --- | --- |
| `claude`, `claude-co` | no flag, `workspace-write` | `--permission-mode manual`, `read-only` | Workers keep the account's `permissions.defaultMode`, as before. Reviewers pin manual mode, so a looser `defaultMode` (such as `acceptEdits`) never reaches them; Claude then asks before each edit and command, the report write included, and the owner answers. Allow rules in Claude's settings still apply in manual mode. Manual mode is the most a Claude TUI reviewer can be narrowed and still write its report. |
| `codex` | `-s workspace-write -a on-request`, `workspace-write` | the same | A TUI reviewer must write its report. Under `-s read-only` that write, and any check that writes a cache, would wait for an approval, so review runs the worker's sandbox and only the review contract keeps it to its report. |
| `cursor` | no flag, `workspace-write` | the same | Edits apply without asking; commands ask unless Cursor's own config allows them (Fleet never passes `--force`). No checked interactive mode both stops edits and lets a reviewer write its report. |
| `agy` | `--mode accept-edits`, `workspace-write` | the same | Edits are approved automatically, shell commands ask. No other mode was checked. |
| `opencode` | no flag, `full-access` | the same | Its default build agent runs every tool without asking, so the default ceiling refuses it ([below](#opencode)). |

Levels are what the launch Fleet builds asks for over the CLI's own default settings. A CLI's own configuration can still widen or narrow that (Claude's settings, Cursor's CLI config, `opencode.json`), and doctor does not read it. If you widen a CLI there, raise its profile's `level` to match; if you narrow it, you may lower it. No level loosens the worker rules: whatever the level, a worker never makes network calls, installs packages, reads secrets or touches Git, and a reviewer writes only its report. Those are instructions, not OS isolation; a [headless run](#headless-reviewers) makes reviewers and investigators read-only by mechanism, and Claude hooks will come through the review profile.

An owner override of `launch` written before profiles, with no `{profile}` slot, keeps working: every role runs that line as written, and it counts at the worker profile's level. An adapter that declares no profiles counts as `workspace-write`. To change one field, override just that key:

```toml
[providers.opencode.profiles.worker]
level = "workspace-write"   # only after setting `permission` in opencode.json so bash and webfetch ask
```

Doctor prints, for each enabled adapter, the binary it found, each profile's level and the ceiling. It also runs `<bin> --help` (and the launch's subcommand help, when the launch line runs one) with a 10-second timeout and warns, naming the adapter and flag, when a flag the launch line, its session fragments or its profiles use is missing from that text. A warning never fails doctor: help text is not a contract, so check the flag against the CLI's docs and a real launch. Help that exits non-zero, prints nothing or times out shows `flags unverified`. A missing binary still fails; for Cursor, doctor also names `agent` if that is what is installed (`bin_aliases`), and the owner then sets `bin`, `login` and `models` to it.

## Headless reviewers

A TUI reviewer or investigator is read-only only by its prompt, writes its own report and can stall at a permission prompt. With `review_transport = "headless"` (under `[defaults]` for every adapter that has a `headless` line, or under one adapter; default `"tui"`), reviewers and investigate tickets on that adapter run its `headless` line instead: one non-interactive CLI call whose own flags deny every write and every prompt. Implementation workers always run in a TUI.

| Adapter | Headless line (beyond model and effort) | What makes it read-only |
| --- | --- | --- |
| `claude`, `claude-co` | `-p --output-format json --permission-mode manual --permission-prompts none` | Manual mode asks before every edit and every command outside the read-only set; with nobody to answer, each such call is denied and Claude is told not to retry. |
| `codex` | `exec --json -s read-only -c approval_policy=never` | The read-only sandbox fails every write, and with approvals never asked nothing escalates out of it. |

Others ship none ([checked](#headless-lines-checked)), so a `[defaults]` headless leaves them in a TUI; `review_transport = "headless"` on an adapter without a `headless` line is a config error. `fleet doctor` names an adapter that runs reviewers headless and checks its headless line's flags against the CLI's help, as for its launch line.

- **Launch.** `fleet review` and `fleet launch --tier review`, and an investigate ticket's launch, start `fleet _headless` in the run's tmux window or cmux pane, so `fleet agents` shows it. It counts as `read-only` against `permission_ceiling`, whatever the adapter's `review` profile says, and has no session, so `--resume` refuses it.
- **Prompt.** A headless reviewer cannot write its report, so its prompt (`fleet prompt … --provider <p>`; `fleet review` passes it) says to reply with the complete report, `Status:` line and `fleet` header included, as its final message. Launch refuses a prompt written for the other transport and says how to rewrite it.
- **Report.** The runner writes the final message as `reports/<id>.md`. One without `Status: needs-verification` or `Status: blocked`, or no final message at all, becomes a `Status: blocked` report saying why, with the message and the CLI's last stderr lines quoted, so `wait` wakes the lead either way. Calls the CLI reports it denied (Claude's `permission_denials`, Codex's declined commands and failed file changes) are listed at the end under `## Denied by the read-only run`.
- **Usage.** The runner writes what the CLI reported to `usage/<id>.<token>.json` (its raw output beside it as `.out`), and reconcile records it in the ledger under the run's lock. Codex counts cached input inside its input tokens; Fleet records the uncached part as input, as Claude reports it. A CLI that reports no usage stays `unknown`.
- **Wait, stop and send.** `wait` wakes on the report exactly as for a TUI reviewer, header summary included. A headless run cannot stall at a prompt and its screen stays still while it works, so it never shows as `STALLED`; it still shows `UNREACHABLE` when its window goes. `fleet stop` sends Ctrl-C: the runner ends the CLI's process group and writes the exit receipt, without a report. A run that finished has already exited. `fleet send` only saves the message: a headless run reads its inbox when it starts.
- **Limits.** Allow rules in Claude's own settings (or a mod or `PermissionRequest` hook) still approve what they match, in manual mode as in the TUI profile; an owner who allows edits or writing commands there has widened headless reviewers too. Codex's user `config.toml` still applies apart from the sandbox and approval policy the line sets. Neither CLI's network access is changed. A CLI that hangs without exiting keeps its window until `fleet stop`.

## Keeping models current

Tier models are pinned on purpose: plans record the exact model and cross-family review needs a known family. When a provider ships new models, `fleet models <provider> [filter]` lists what the account offers, then override the tier in the user config (with `family` for a mixed adapter) and run `fleet doctor`. No Fleet release is needed. Shipped defaults are refreshed in releases and noted in the changelog.

## Allowed models

`models_allow` is the owner's list of models the lead may choose, either a list (`["opus", "sonnet"]`) or a table of model ID = family. With a live list (Codex, Cursor, Antigravity, OpenCode) it narrows that list; without one (Claude) it is the list. Tier models always count as allowed, so overriding a tier needs no list edit; the list governs per-ticket pins and review models. `fleet plan` and review launches refuse models outside it, and its families feed cross-family review. With no list, plan, steer and review launches refuse a model the account's live list does not offer; tier models are left to doctor, parameterized IDs (`model[key=value,…]`) pass unchecked, and a listing that fails or times out only warns (once per command). Doctor fails when a tier model is outside it and warns when an allowed model is no longer offered. A user's `models_allow` replaces the shipped one; an adapter that `extends` another inherits it. `fleet models <provider> --all` shows the account's full list.

| Provider | Shipped `models_allow` |
| --- | --- |
| Claude | Aliases `opus`, `sonnet`, `fable` and full IDs `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-fable-5-1`, `claude-haiku-5-5` (the CLI cannot list models, so only a launch proves your plan offers one) |
| Cursor, OpenCode | None: every model the account lists, with families from `family_prefixes` |
| Codex, Antigravity | None: the live list is used as is |

`model_families` (model ID = family) names the family of models in a mixed adapter without narrowing what the lead may choose.

`family_prefixes` (model ID prefix = family) labels models by name, so a model the CLI adds later gets a family without a config edit. The longest matching prefix wins; an exact `model_families` or `models_allow` entry, or a tier that runs the model with its own `family`, beats it. Cursor ships prefixes for every vendor it lists (`claude-` = Anthropic, `gpt-` = OpenAI, `gemini-` = Google, `grok-` = xAI, and so on); on the account it was checked with, only `auto` was left without one. Antigravity ships `gemini-`, `claude-` and `gpt-oss-`, so its Claude and GPT-OSS models count as Anthropic and OpenAI. OpenCode ships one per lab that serves its own models (`openrouter/anthropic/`, `huggingface/Qwen/`, …). Resellers and fine-tune houses (`openrouter/nvidia/`, `openrouter/perplexity/`, `openrouter/bytedance/`, most of `opencode/`) have none, because their models are built on other labs' models; pass an accurate `--family` or add an exact entry for one of those.

`efforts` lists the effort levels a CLI accepts when it cannot report them per model. Claude ships `low, medium, high, xhigh, max` from `claude --help`; that is CLI-wide, so whether a given model accepts a level is only proven at launch. Tier efforts, plans and review launches outside the list are refused. Antigravity ships `low, medium, high, xhigh, max` from `agy --help` (1.3.1 and 1.4.8). Codex reports efforts per model in its catalog; Cursor encodes effort in the model ID; OpenCode's interactive TUI has no effort flag, so its tiers carry none.

Codex's list comes from `codex debug models`, a debug command that refreshes its catalog over the network and may change between Codex versions. If it fails, doctor reports "model list failed" without failing. When the catalog marks a tier model for retirement, doctor warns (`RETIRING: gpt-5.5 retires 2026-10-14, switch to gpt-5.6-sol`) without failing, and `fleet models codex` shows the date and replacement.

## Claude Code

The default `claude` adapter uses the normal account and interactive permission controls. Its reviewers and investigators launch with `--permission-mode manual`, so they ask before writing even their report when the account's settings would accept edits; expect a `STALLED` reviewer at a permission prompt that the owner answers ([profiles](#permission-profiles)), unless the owner sets `review_transport = "headless"`, which runs them as `claude -p` with those prompts denied ([headless reviewers](#headless-reviewers)). `opus`, `sonnet` and `fable` are aliases for the newest model of each line; pin a full ID such as `claude-opus-5-5` when the plan must record an exact version. Effort support depends on the installed CLI and model. Do not respond to a permission denial by switching account or bypassing the host's controls.

`claude-co` is a disabled example of a second account using `CLAUDE_CONFIG_DIR=~/.claude-co`. Turn it on with `fleet provider enable claude-co` only after the owner has signed that account in. It must receive a self-contained prompt; do not assume the same plugins exist under both configurations. Both accounts use Anthropic-family models, so switching between them does not satisfy cross-family review.

Claude workers start with `--session-id` set to a UUID Fleet records, so `fleet launch --resume` can continue the same conversation with `--resume` after a usage limit resets. A session stays with the account that ran it. Other adapters can opt in through the `session` and `resume` fields in `providers.toml`.

## Extra subscriptions

An adapter's `account_env` names the CLI's account-directory variable (`CLAUDE_CONFIG_DIR` for Claude Code, `CODEX_HOME` for Codex). Fleet clears that variable for the launch and the login check unless the adapter's own `env` sets it, so no adapter silently uses the account the lead runs under. An extra account is an adapter that `extends` another and sets `env`:

```toml
[providers.claude-co]
extends = "claude"          # copies every field except `enabled`
enabled = true
env = { CLAUDE_CONFIG_DIR = "~/.claude-co" }
plugins = false
max = 1
```

`fleet account add <name> --from <adapter> --dir <path> [--max N] [--disabled]` appends such a block to the user config without touching existing lines. It never signs in: the owner signs the account in, then runs `fleet doctor <name>`. Doctor fails when two providers report the same signed-in email (Claude Code prints one; Codex does not, so Codex accounts are distinguished only by directory). Cursor has no `account_env`, so a second Cursor account is not supported. If your primary Claude account uses a custom `CLAUDE_CONFIG_DIR`, set it in `[providers.claude] env` because Fleet no longer inherits it.

## Codex

Both of the example adapter's [profiles](#permission-profiles) use `workspace-write` with `on-request` approval. Effective network and filesystem permissions still come from the host configuration; neither the adapter nor the preamble guarantees OS isolation. Fleet's worker contract prohibits network calls and package installation regardless of those capabilities. Request owner help for a necessary permission instead of weakening the sandbox.

A Codex worker that needs an approval waits in its pane until the owner answers, because the lead never accepts a prompt for them. Codex 0.159+ can route those prompts to its own reviewer instead: `approvals_reviewer = "auto_review"` (`--approve-for-me` on the command line). It reviews sandbox escalations against the policy and does not widen what the sandbox allows. It is the owner's choice, made once in the user config rather than per run, and it is not a way past a decision the owner owes:

```toml
[providers.codex]
launch = "{bin} -m {model} -c model_reasoning_effort={effort} -c check_for_update_on_startup=false -c approvals_reviewer=auto_review {profile} {prompt}"
```

An older copy of this line that spells out `-s workspace-write -a on-request` instead of `{profile}` still works; both roles then run it as written.

This is approval routing inside Codex. It is unrelated to Fleet's cross-family review of a ticket's diff, which `review=` controls.

Use model IDs and effort levels supported by the authenticated account. The shipped OpenAI IDs are examples. Model changes must be reflected in the plan and recorded worker metadata; do not silently switch a running worker's model in its UI and leave routing records stale.

## Cursor

The default launches `cursor-agent` with the chosen model and repository trust, retaining interactive permission handling; it does not use `--force`. `--trust` is in the shipped launch line, so Cursor is trusted with or without `auto_trust`; that the interactive TUI honours it is [unverified](#profile-flags-checked). A trusted workspace is not permission for an arbitrary command. Inspect trust or command prompts as part of launch verification.

Cursor can serve several families. The example tiers identify xAI (`grok-…`), Moonshot (`kimi-…`) and Meta (`muse-…`) separately. The lead may choose any model `cursor-agent --list-models` offers (`fleet models cursor gemini` filters them); `family_prefixes` names each one's family. Parameterized IDs must remain a single argument. A model whose name hides its base model needs an exact family entry, especially for cross-family review. A different CLI name alone is not evidence of a different family.

## Antigravity

`agy` ships disabled. To use it, install and sign in to `agy`, run `fleet provider enable agy`, then `fleet doctor agy`. It was checked live with agy 1.3.0: launch with an interactive first prompt (`-i`), follow-up input, `/exit` back to the shell, `doctor` and `fleet models agy`. `/exit` is the verified quit sequence. Its models and efforts were refreshed from `agy models` and `agy --help` on 1.3.1 and 1.4.8: neither offers Gemini Pro, so every tier uses Gemini 3.8 Flash (heavy and review at `gemini-3.8-flash-high`).

- It runs in `accept-edits` mode: file edits are approved automatically, shell commands still ask. A worker's checks (even `git status`) wait at a prompt until someone answers it in the pane, so watch `fleet peek` after launch.
- On first use in a folder it asks whether to trust it. With `auto_trust` on, launch adds the repo to its trusted workspaces first ([Folder trust](#folder-trust)); otherwise the owner answers, once per checkout.
- The model ID names a thinking level (`gemini-3.8-flash-medium`) and `--effort` sets reasoning effort; tiers set both.
- Its account also offers Claude and GPT-OSS models. `family_prefixes` labels them by name (`gemini-` as Google, `claude-` as Anthropic, `gpt-oss-` as OpenAI), so an Antigravity Claude model cannot review a Claude writer, and a model agy adds later under those names needs no config edit.
- It cannot name a session at launch (only resume one with `--conversation`), so `fleet launch --resume` does not apply.

## OpenCode

`opencode` ships disabled; turn it on with `fleet provider enable opencode`, then run `fleet doctor opencode`. It was checked live with opencode 1.18.34: launch through the TUI's `--prompt` (submitted at once, the session stays interactive), follow-up input, `/exit` back to the shell, `doctor`, `fleet models opencode`, and a Fleet launch that ended in an in-scope change and a complete report.

- Model IDs are `provider/model`. The shipped tiers and login check assume OpenRouter (`opencode auth login`). `opencode models` lists only providers with credentials, so `login = "opencode models openrouter"` with `login_ok = "openrouter/"` is a real sign-in check; point both at the provider you use.
- It serves many families, so the adapter is `mixed`: every tier carries its family, and `family_prefixes` names the rest by vendor path. Every model `opencode models` lists may be chosen. The shipped tiers use Zhipu, Alibaba and DeepSeek models, which gives cross-family review options outside Anthropic, OpenAI and Google.
- Effort: the TUI takes no `--variant` (only `opencode run` does), so tiers carry no effort.
- Permissions: OpenCode's default build agent allows every tool without asking, except paths outside the workspace and repeated identical calls (`opencode debug agent build` shows the resolved rules). Fleet's worker contract is the only limit unless your `opencode.json` sets `permission`; tighten it there, not in the adapter. So both its [profiles](#permission-profiles) are `full-access`, and with the default `permission_ceiling` launch refuses OpenCode and `fleet doctor opencode` fails. The owner chooses one: set `permission` in `opencode.json` (edits allowed, `bash` and `webfetch` set to `ask`) and then `level = "workspace-write"` on its profiles, or raise `permission_ceiling` to `full-access`. Fleet ships no narrower profile because it cannot see `opencode.json` from the launch line, and no read-only agent for reviewers was checked against a real OpenCode.
- Its sessions get IDs OpenCode picks, so `fleet launch --resume` does not apply.

## Placeholder adapters

`grok` and `muse` are placeholders, not supported providers. No Grok or Muse CLI has been checked, so their binary name (`grok`, `muse`), launch flags and `/quit` are guesses, and they have no sign-in check, model list or tiers. They ship with `placeholder = true`:

- `fleet doctor` reports an enabled placeholder as `PLACEHOLDER` and fails.
- `fleet provider enable`, `fleet plan`, `fleet launch` and `fleet steer provider` refuse it.

**Grok and Muse models work today through Cursor.** The `cursor` adapter, on by default, offers `grok-4.7-high`, `grok-4.7-high-fast` and `muse-spark-1.3-max`. Its heavy and light tiers use Grok, and its review tier uses Muse. `fleet models cursor grok` and `fleet models cursor muse` list what your account offers. Use `agents=cursor:muse-spark-1.3-max` to pin one.

To turn a placeholder into a working adapter, the owner fills it in under `[providers.grok]` (or `muse`) in the user config:

1. Confirm that the installed binary is the intended product, and set `bin`.
2. Set `launch` from the CLI's help: `{bin}`, `{model}`, `{effort}` if it has an effort flag, and `{prompt}` as an interactive first prompt that leaves the session open.
3. Set `quit` to the key sequence that returns to the shell, checked in a real pane.
4. Set `login` and `login_ok` to a command that spends no model call and prints a known string only when signed in.
5. Set `models` if the CLI can list models; otherwise set `models_allow`.
6. Add `tiers.heavy`, `tiers.standard`, `tiers.light` and `tiers.review` with models the account offers, plus `efforts` if the CLI takes one and `family` (on each tier for a mixed adapter).
7. Put a `{profile}` slot in `launch` and set `profiles.worker` and `profiles.review`, each with its permission flags in `args` and an honest `level` ([permission profiles](#permission-profiles)). Without them it counts as `workspace-write`.
8. Set `placeholder = false`, then run `fleet provider enable <name>` and `fleet doctor <name>`.

Then test it as below before routing real work to it.

For every new adapter, test a harmless local ticket, multiple waves, a required review at capacity, process exit confirmation and recovery. Keep unavailable adapters out of auto routing. Login expiry needs the owner. On quota exhaustion the lead may resume after the reset or steer within the run's routing (skill step 1); otherwise it needs the owner. Neither is a reason to ignore the worker contract.
