# Lead host requirements

The lead needs this skill, a local shell, access to the intended Git checkout, and permission to reach the cmux socket. Run `fleet doctor` in that same environment; a terminal outside the host is not proof that the host's sandbox can reach cmux.

| Host | Standalone invocation | User installation |
| --- | --- | --- |
| Claude Code | `/fleet …` | `~/.claude/skills/fleet` |
| Codex CLI / desktop | `$fleet …` or skill picker | `~/.agents/skills/fleet` |
| Cursor CLI / IDE | `/fleet …` or skill discovery | `~/.cursor/skills/fleet` |
| ChatGPT with local execution | Host-supported skill invocation | Requires a connection to the local runtime |

Install with `npx skills add codemeall/agent-fleet`, `npx @codemeall/agent-fleet@preview setup --harness <host>` or `./install.sh` from a clone; Claude Code can alternatively use the plugin marketplace (`/plugin marketplace add codemeall/agent-fleet`). Project setup uses the corresponding `.claude/skills`, `.agents/skills` or `.cursor/skills` beneath the repository. Codex's legacy `~/.codex/skills` and custom skill directories may still contain an older copy: remove stale duplicate registrations deliberately rather than installing multiple copies. An npm executable alone does not register the skill. A plugin may namespace the command; use the name shown by the host (Claude example: `/agent-fleet:fleet`). `fleet version` prints the version and directory of the runtime the shell resolved; `fleet doctor` also shows each host's installed copy and its version.

Optional companion skills for the lead: `cmux` from `manaflow-ai/cmux`, `grill-with-docs` (or `grill-me`), `to-spec` and `to-tickets` from `mattpocock/skills`, and `feature-docs` from `codemeall/feature-init`. Install them into the lead host's skill directories with `npx skills add <source> --skill <name>`. Name each skill, because the cmux repository also ships skills for developing cmux itself. `fleet doctor` reports where it finds each one, in user-level and current-directory project skill directories, and never fails on a missing one.

## Permission and connectivity checks

1. Confirm Python 3.11+, Git, cmux and selected worker CLIs are available in the lead's shell.
2. Confirm cmux is running and the intended workspace exists. Inside cmux the current workspace can be used; otherwise supply `fleet init <run> --workspace <ref>` explicitly.
3. Run `fleet doctor`; diagnose its actual socket, executable or authentication error.
4. If the host blocks a required operation, use its normal approval mechanism or have the owner configure a narrow allowance. Consult documentation for that installed host version. Do not disable the sandbox, switch permission modes or route to another harness to evade a rejection.
5. Launch one small, scoped ticket and inspect it with `fleet peek` before starting a larger wave. Trust, authentication and model selection may still need owner input.

Claude permission classifiers, Codex sandbox profiles, Cursor allowances and remote-session topology vary by version. There is no universal setup flag that safely fixes all of them. Fleet's bundled launch configuration does not override host restrictions. A cloud-only chat can help plan the work but requires an explicit local execution connection to operate this fleet.

`fleet wait` defaults to 45 seconds and accepts up to 600. Pass the longest timeout the host's command tool allows: Claude Code allows up to 10 minutes per command (use about 540) and can run the wait in the background; other hosts may allow less. A worker may use the same CLI as the lead; each pane is a distinct process and session.
