# Agent Fleet review

Reviewed 2026-09-30. Scope: the current working tree, developer and agent instructions, compatibility with local Claude Code/Codex/Cursor, and proposed GitHub/npm distribution. This is a review, not an implementation or publication.

## Assessment

The architecture is heading in the right direction. Keep the lead responsible for understanding tickets, routing, decisions and final verification; keep the CLI responsible for deterministic process and run mechanics. Visible cmux workers, explicit file ownership, bounded concurrency and file-based reports are useful foundations.

The current tree is a personal prototype. It is suitable to share on GitHub as a clearly labeled preview, but it is not yet ready to advertise as a reliable, generally installable npm package or cross-harness plugin. There are execution lifecycle defects as well as packaging gaps. “Extensible to coding-agent CLIs through tested adapters” is an accurate goal; “any provider” is not currently demonstrated.

Fleet currently covers tickets → implementation → verification. Brainstorming, grilling, specification and ticket generation remain upstream workflows. That boundary is sensible; document the connection instead of duplicating those skills inside Fleet.

## Findings, in priority order

### 1. P1 — Verified workers never release capacity in the documented workflow

Evidence: `skill/bin/fleet:171`, `skill/bin/fleet:228`, `skill/bin/fleet:231`, `skill/SKILL.md:62`.

Capacity counts workers whose persisted state is `running`. A report changing to `verified` does not change that state. The instructions stop workers only at the end of the entire run, after telling the lead to launch newly unblocked tickets and reviewers. Once the first wave fills the global or provider cap, later work cannot start; waiting does not release the slots.

Reproduced with `max_parallel=1`: worker 01 had a verified report, but launching 02 failed with `max_parallel=1 reached; wait for a worker to finish`.

Define separate ticket and process states. After acceptance and any required review, stop or retire the worker and release its slot before advancing. Preserve tabs independently from whether they consume an execution slot. Cover a multi-wave run and a full-capacity review cycle in integration tests.

### 2. P1 — Failed shutdown is recorded as a successful stop

Evidence: `skill/bin/fleet:353`, especially lines 367–371.

`cmd_stop` catches a cmux error and still records `stopped` or `closed`. Subsequent launches treat that worker as inactive even though it may still be editing the shared checkout. Even a successful send of quit keys is not proof that the CLI exited.

Reproduced by making `send_keys` raise `FleetError`: the persisted worker state still became `stopped`.

Keep failures as `stop-failed` or `unknown`, return an unsuccessful result, and reconcile actual surface/process state before releasing capacity. Also document recovery from manually closed tabs, CLI crashes and expired logins. The current fallback says to relaunch a tab, but `launch` rejects an existing worker still marked `running`.

### 3. P1 — A first-time installation can install no skill, and seeds the wrong workspace

Evidence: `install.sh:22`, `install.sh:32`, `config.example.toml:5`, `skill/bin/fleet:187`.

The installer treats absence of a `skills` directory as proof that the harness is absent. A developer using a harness for the first time may have no such directory. Installation continues and prints `next: fleet doctor`, but `/fleet` is unavailable.

The seeded config also selects `workspace:2`. This takes precedence over the lead's current workspace. On another machine it can select an unrelated workspace or fail because that ref does not exist; the claimed fallback is not implemented for an invalid configured ref.

Provide explicit installation targets, create the selected skill directories, report failures clearly, and leave workspace selection unset by default. Resolve the current workspace at run creation and validate it. Make the second Claude account an optional named profile rather than a public default.

For Codex, support the currently documented user location `~/.agents/skills` and account for legacy/custom locations without installing duplicate copies. The current installer only considers `~/.codex/skills`. [OpenAI skill locations](https://learn.chatgpt.com/docs/build-skills)

### 4. P2 — `fleet check` misses modifications to already-staged files

Evidence: `skill/bin/fleet:103`, `skill/bin/fleet:135`.

The baseline stores staged filenames, not the index contents. If the owner already staged `x.txt`, a worker can change and re-stage it without adding a new filename. The check reports no problem, despite the no-staging contract.

Reproduced in an isolated Git repository: stage owner content, capture the baseline, change and stage the same file, compare snapshots. The result was an empty problem list.

Record and compare index entries including object IDs and modes, with submodule coverage. Describe the result as a comparison of captured states, not proof that no intervening Git operation occurred. Separately capture relevant pre-existing working-tree changes so verification can distinguish a ticket's changes from the owner's existing edits, including untracked files.

### 5. P2 — Model arguments are not shell-quoted

Evidence: `skill/bin/fleet:83`.

Only the prompt is quoted before creating the shell command. Model and effort strings are interpolated directly. The installed Cursor CLI documents parameterized models such as `claude-opus-4-8[context=1m,effort=high,fast=false]`. Passing that through the current builder causes zsh to reject it with `no matches found` before Cursor starts. Shell metacharacters in an override can also change command meaning.

Reproduced using a harmless `printf` adapter and zsh. Quote model and effort as arguments, or construct an argument vector and serialize it safely for the terminal command. Treat executable/environment setup separately from model data. Test spaces, brackets, quotes and literal shell metacharacters.

### 6. P2 — Review mode needs an explicit contract and completion gate

Evidence: `skill/SKILL.md:62`, `skill/references/routing.md:35`, `skill/bin/fleet:197`, `skill/providers.toml`.

`--tier review` only chooses a model/effort. Prompt generation still produces an implementation prompt, and launch permissions remain those of the normal writer. The lead is expected to invent a reviewer prompt, a separate ID and a report relationship. The ticket is already marked resolved before the cross-review completes.

Add a reviewer prompt/template and explicit review role, stable input diff, separate report ID, permitted report-output path, and a rule that dependents remain blocked until required review findings are resolved. Use available read-only controls or an isolated review snapshot where appropriate; explain when read-only is only an instruction.

Track model family per resolved model rather than only per CLI. Cursor is `mixed`; Antigravity can serve several families. Different CLI names do not establish independent model families. Define what happens when `single:<provider>` or a pinned pool cannot supply a cross-family reviewer.

### 7. P2 — Resuming a run depends on the old lead's conversation

Evidence: `skill/bin/fleet:190`, `skill/bin/fleet:251`, `skill/SKILL.md:23`.

`run.json` records launched workers and a Git baseline, but not the complete ticket graph, pending assignments, file ownership, routing/review overrides or verification decisions. The skill always starts with `init`, which rejects an existing run. A fresh lead must reconstruct the missing plan and infer how to resume.

For the intended day-to-day workflow, persist the plan and selected options, then document a resume path that reconciles reports and actual workers before launching anything. Map conversational options explicitly to CLI flags: for example, `workspace=...` to `init --workspace`, and a pinned model to `launch --model`. These options are currently interpreted by the lead, not a CLI-level scheduler; make that distinction explicit.

### 8. P2 — Run and worker IDs can escape the run directory

Evidence: `skill/bin/fleet:149`, `skill/bin/fleet:201`.

Run names and ticket IDs become path components without validation. Absolute paths or `../` segments can redirect run/prompt writes outside `.fleet/runs`. Ticket IDs inferred from URLs or paths are especially easy to mishandle.

An isolated path-resolution check confirmed that a traversal run name resolves outside the repository. Require safe slug/ID syntax and verify resolved paths remain inside the intended directory before writing. Return actionable errors rather than raw filesystem tracebacks.

## Workflow and documentation corrections

The short lead workflow and reference split are good. The documentation is understandable to someone who knows the author's machine, but leaves a new developer or fresh agent to supply several missing pieces:

- Add one complete example: upstream brainstorming/grill → spec → approved tickets → Fleet, showing two independent tickets and a dependent third ticket.
- Explain that Fleet consumes ticket artifacts. Keep the upstream skills optional; ordinary tickets should work too. Document required fields: ID, description, blockers, acceptance criteria and status. Add a sample `.fleet/rules.md` with real-looking check commands.
- Read the configured issue-tracker instructions when available. The installed `to-tickets` skill supports both local Markdown and remote trackers, while Fleet documents only local files/task lists. For an initial release, explicitly support local exports; do not imply that passing a GitHub issue URL to `--ticket` fetches it.
- Reconcile upstream implementation conventions. The installed `/implement` skill ends by committing, while Fleet forbids commits. Document Fleet's worker contract explicitly and avoid invoking an upstream implementation workflow with conflicting side effects.
- Resolve the contradictory watch instructions: “Answer approval ... prompts yourself” versus “Only the owner can fix ... permission prompt.” A lead may clarify a worker task, but should not treat a permission rejection as permission to switch to a less restrictive harness.
- Replace categorical sandbox recipes with capability checks and host-specific, versioned setup. An unreachable cmux socket is not automatically evidence that disabling the sandbox is the right remedy. The installed Codex 0.156.1 describes `-p` as a separate `<name>.config.toml` file, unlike the documented `[profiles.fleet]` recipe.
- Make timeouts adapt to the host's command execution limits and preserve progress reporting. The nine-minute default is not portable across all host tools.
- Remove the private `~/Documents/orchestrator-ai` reference from public onboarding, or replace it with an accessible optional link.
- Treat model IDs as account-specific examples until validated. `doctor` currently checks for a binary and a login-output substring; it does not verify model availability, effort support or launch readiness. It also ignores the login command's exit code.
- Expose capabilities needed for routing, such as network-dependent work, rather than requiring the lead to infer them from prose. The provider notes route network tickets away from Codex, while the shared worker preamble prohibits network calls for every provider.

## Integration with mattpocock/skills

Checked the public repository supplied by the user, not only the locally installed copies. Position Fleet as its optional execution companion. The upstream project deliberately offers composable skills and supports both a Claude plugin and editable skill installation. [Upstream repository and installation](https://github.com/mattpocock/skills)

The documented example should be: configure the upstream skills once per repository → brainstorm/discuss → `grill-with-docs` (or `grill-me`) → `to-spec` → `to-tickets` → Fleet routing, implementation and verification. Resolve invocation syntax through the host: slash commands, plugin-qualified names, or Codex skill mentions. Avoid assuming every host has a tool literally named `Skill`.

Upstream `to-spec` captures the agreed design and testing decisions; Fleet should carry these into worker context. [Upstream to-spec](https://github.com/mattpocock/skills/blob/main/skills/engineering/to-spec/SKILL.md)

The current `to-tickets` local format fits Fleet's intended input: individual files under `.scratch/<feature>/issues`, blockers, status and acceptance checkboxes. Upstream also supports real issue trackers and deliberately omits specific implementation file paths. Fleet's lead should discover file ownership during planning, preserve ticket dependencies, and normalize tracker issues to local worker context if remote input is supported. [Upstream to-tickets](https://github.com/mattpocock/skills/blob/main/skills/engineering/to-tickets/SKILL.md)

Two concrete integration corrections are needed:

- `skill/templates/ticket-prompt.md` tells workers to read `CONTEXT.md`. Current upstream domain modeling uses `GLOSSARY.md` and optionally `GLOSSARY-MAP.md`. Read project configuration, support the older name as a fallback, and pass the actual glossary/ADR paths to workers. [Upstream domain modeling](https://github.com/mattpocock/skills/blob/main/skills/engineering/domain-modeling/SKILL.md)
- The public upstream `implement` skill, just like the local copy, explicitly ends by committing. Fleet should own the implementation step under its existing no-commit contract rather than blindly composing that skill into every worker. Its test/review practices can still inform Fleet's workflow. [Upstream implement](https://github.com/mattpocock/skills/blob/main/skills/engineering/implement/SKILL.md)

Do not require every worker account to install the entire upstream collection. Passing the ticket, agreed decisions, glossary/ADR references and explicit checks is sufficient. Document the tested upstream version or revision so future changes can be checked without coupling every Fleet release to upstream updates.

## Compatibility assessment

“Lead host”, “worker CLI”, “account/provider” and “model” should be separate concepts. A Claude lead can launch a Codex worker; a Cursor worker can use models from several vendors. The current adapter mostly describes a worker CLI plus account configuration.

| Surface | Current assessment |
| --- | --- |
| Local Claude Code lead/worker | Plausible design; installed CLI recognizes the configured launch flags. Full run not verified. Fix installation and lifecycle first. |
| Local Codex CLI lead/worker | Plausible design; configured launch flags exist locally. Update skill discovery and host setup docs; verify cmux access in the actual permission profile. |
| Codex desktop / local ChatGPT execution | Assess capabilities: local shell, repository access, and cmux socket access. Current docs omit this path. |
| Cursor CLI / local IDE lead | Skill format and launch flags are plausible; shell quoting fails for current parameterized model syntax. Remote/cloud sessions must not be assumed to reach local cmux. |
| ChatGPT or another cloud-only session without local execution | Can read instructions, but cannot directly run this local cmux workflow. Would require an explicitly provided local execution connection. |
| Antigravity, Grok, Muse, arbitrary providers | Adapter candidates, not verified support. Grok/Muse are disabled placeholders; no live Antigravity test was performed. |

The blanket statement that ChatGPT web/desktop cannot use the skill is outdated. Current documentation distinguishes skill availability across ChatGPT/Codex and local versus packaged distribution. Execution still requires Fleet's local capabilities. [OpenAI skills documentation](https://learn.chatgpt.com/docs/build-skills)

Cursor supports user skills in both `~/.cursor/skills` and `~/.agents/skills`, but local skill installation does not automatically make local execution available to cloud/remote sessions. [Cursor skills documentation](https://cursor.com/docs/skills)

## GitHub and npm release plan

Use one canonical skill and runtime, with distribution metadata around them. Retain Python if desired; npm distribution does not require rewriting the runtime. Make Python 3.11+, a compatible cmux installation and selected authenticated worker CLIs explicit prerequisites.

1. **GitHub source release:** choose a license, add release/version information, public setup and uninstall instructions, a support matrix, examples and CI. The inspected checkout has no commits yet. GitHub hosting itself is straightforward; it does not establish compatibility.
2. **npm runtime package:** add `package.json` with name/version, executable mapping, an explicit published-files list, repository metadata and license information. Include templates, providers and references in the tarball. A small Node launcher can check for the required Python version and delegate to the existing runtime. Verify with `npm pack` and installation from that exact tarball. These are proposed changes; the current tree has no npm package. [npm package configuration](https://docs.npmjs.com/cli/v11/configuring-npm/package-json/)
3. **Explicit skill setup:** offer an idempotent setup command that installs for the chosen host/scope, handles missing directories and conflicts, and supports uninstall. Avoid relying on symlinks into an ephemeral `npx` cache. Installing an executable through npm does not by itself register skills in a harness.
4. **Plugin distribution:** package the same skill using supported manifests and paths, and test actual discovery. Claude plugin skills are namespaced; a plugin named `agent-fleet` containing `fleet` is invoked as `/agent-fleet:fleet`, unlike a standalone `/fleet`. [Claude skills](https://code.claude.com/docs/en/skills), [Claude plugin layout](https://code.claude.com/docs/en/plugins-reference)
5. **Portable plugin where supported:** current Cursor docs support the Agent Plugins format with a root `plugin.json`, as well as Cursor-specific manifests. OpenAI documents its plugin packaging and distribution requirements. Validate the chosen package in each host instead of assuming that npm publication makes it a plugin everywhere. [Cursor plugins](https://cursor.com/docs/plugins), [OpenAI plugin packaging](https://developers.openai.com/plugins/build/plugins)

A sensible first release is a local cmux-based preview with verified Claude Code, Codex and Cursor adapters. Keep additional providers experimental until they pass the same launch → report → verify → stop smoke test. Separate personal account/model preferences from shipped defaults.

## Verification performed and limits

- All 14 existing unit tests passed with `python3 -m unittest discover tests -v`.
- Inspected local CLI help: Claude Code 2.1.284, Codex 0.156.1, Cursor 2026.09.28-64d2043 and cmux 0.64.25.
- Ran isolated reproductions for retained capacity, false successful stop, missed index changes, path escape and zsh rejection of parameterized model IDs.
- Checked current official skill/plugin documentation for OpenAI, Claude and Cursor, and npm package metadata documentation.
- Checked the supplied public `mattpocock/skills` repository, including `to-spec`, `to-tickets`, `implement`, `grill-with-docs` and domain-modeling conventions.
- The bundled skill-format validator could not run because the selected Python lacks PyYAML. This is a validator dependency, not a Fleet runtime dependency. No format-validation success is claimed.
- No paid agent sessions were launched, no live cmux workers were created, and no package was installed or published. Existing tests mostly exercise helpers; they do not establish end-to-end provider compatibility.
- Preferred OpenRouter handoff MCP tools and the `handoff` executable were unavailable. Review and final verification used the available local tools.

Before a reliable public release, add tests for a fresh install, two implementation waves, required review at capacity, crash/stop recovery, a resumed lead, and the exact npm tarball. These cover the behaviors the current helper tests miss.
