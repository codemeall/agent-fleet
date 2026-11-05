# Fleet teamwork review

How the lead and its workers collaborate, compared with Claude Code agent teams, Codex multi-agent and T3 Code's delegation. The goal: match their collaboration, beat them on quality, and spend fewer tokens.

Reviewed 2026-11-04 at `0.1.0-preview.23`. **Release 1 (P1–P7) is implemented in `0.1.0-preview.24`** (spec #21; tickets #10 and #22–#27), and **release 2 in `0.1.0-preview.25`** (spec #31): P8 (gate loop) and P9 (report header), plus untrusted-data rules, plan-time review checks, a review-round cap and adapter smoke tests in CI. **Release 3 in `0.1.0-preview.26`** (spec #35) delivers P13 (Claude hooks), the read-only half of P15 (headless reviewers), P19's ledger, the permission ceiling of P14, and a live smoke kit. **Release 4 in `0.1.0-preview.27`** (spec #39) delivers P16 (worktree isolation, opt-in), P20's supervisor (`fleet supervise`) and a benchmark kit for F13 (the paid runs, #16, are the owner's). **Release 5 in `0.1.0-preview.28`** (spec #43) delivers P17's investigate fan-out with a judge, P18 (warm workers) and a read-only GitHub issue import (F4). The rest of Phases 3–4 below remain proposals. Claims about Fleet were checked against `skill/bin/fleet`, the skill and its templates; the defects below were reproduced with a fake worker CLI in headless tmux. Claims about other harnesses come from their current docs and source, cited in [Sources](#sources).

## Summary

Fleet already leads where it counts for quality:

- **Cross-family review as a gate.** Only Fleet and T3 Code mix vendors, and only Fleet requires a reviewer from another model family before it accepts a ticket. Claude Code agent teams and Codex multi-agent run one vendor; T3 Code can delegate a review to another provider, but nothing requires it.
- **Durable runs.** The plan, ownership, reports and evidence live on disk, so a run survives lead compaction or a fresh lead. Claude Code's in-process teammates are lost on `/resume`, and its team config is deleted at session end.
- **Diff-bound acceptance.** A review is tied to the SHA of the diff it read; any later edit makes it stale. Claude Code's `TaskCompleted` hook and Codex's `SubagentStop` hook can hold an agent until a gate passes, but none of the others binds a review to content.
- **Exact file ownership** enforced at launch, and per-ticket model and effort tiers. The others keep agents apart by instruction only.

It trails on the mechanics of working as a team:

1. **Enforcement is by prompt alone.** A worker can edit files outside its scope, and every gate passes ([D1](#d1-out-of-scope-edits-pass-every-gate)). Reviewers and investigators launch with the same writable command line as implementers.
2. **Communication is one-way and typed into a terminal.** Workers can only flip a `Status:` line. The lead's messages are keystrokes with no framing, no delivery record and no copy on disk ([D2](#d2-messages-are-raw-keystrokes)). There is no channel between workers.
3. **The lead does the deterministic work by hand.** A reviewed heavy ticket takes about 15 lead tool calls (launch, wait, read, check, stop, diff, prompt, launch reviewer, wait, read, stop, two evidence files, two verifies). Each call is a turn over the lead's whole context.
4. **Worker state is inferred from screen hashes.** T3 Code drives every provider through its own structured protocol, and Claude Code and Codex use internal events; Fleet reads terminals.
5. **Workers start cold and stay cold.** Dependents never see what their blockers delivered ([D3](#d3-dependents-never-see-what-blockers-delivered)). One extra file means a follow-up run ([D4](#d4-one-more-file-means-a-follow-up-run)).
6. **One shared checkout.** Workers may not build or run servers, so they cannot fully verify their own work, and any shared file serializes tickets.

The direction: **the LLM lead makes judgments, and the runtime does the mechanics.** Move gates, review plumbing, messaging and dispatch into `fleet`. Use each CLI's hooks and structured protocols for enforcement and signals. Add worktree isolation and cross-vendor fan-out where they pay for themselves.

## Verified defects

### D1. Out-of-scope edits pass every gate

`scoped_diff` and `capture_files` read only the ticket's own files, and `fleet check` compares only HEAD and the index. Nothing looks at the rest of the working tree.

Reproduced: a worker scoped to `src/a.txt` also modified `src/b.txt` and created `src/secret_side_effect.txt`. `fleet diff` showed only `src/a.txt`, so the reviewer would see the same. `fleet verify` accepted the ticket, and `fleet check` printed `OK`. `git status` showed all three files.

This also makes the evidence in [example 5](skill/references/examples.md#5-an-evidence-file) prove nothing: "No change outside src/api.ts and src/api.test.ts: `fleet diff` lists only those files" holds for every ticket, because `fleet diff` cannot list anything else.

### D2. Messages are raw keystrokes

`fleet send` joins its arguments and types them into the pane (`tmux send-keys -l`, or `cmux send`), then presses Enter. A message with line breaks reached a line-reading worker as three separate inputs. Whether a raw line feed submits or inserts a newline is up to each CLI's TUI, so a numbered fix list can arrive as several prompts. Nothing is saved: a worker relaunched with `--resume` or replaced by a fresh one never sees earlier messages, and the only record is whatever the lead chose to write in `notes.md`.

### D3. Dependents never see what blockers delivered

A dependent's prompt is built from the plan's `context`, which was written before its blockers were implemented, and the plan is frozen after the first launch. `launch --ready` writes the prompt from that plan. No template mentions `notes.md`, the blockers' reports or their verified diffs. A dependent therefore rediscovers the interface its blocker built, or builds against the shape the lead guessed at planning time.

### D4. One more file means a follow-up run

The worker rules say "If you need another file, report the blocker and wait for a revised assignment". The skill says scope additions after launch need a follow-up run: stop overlapping workers, carry tickets and partial edits forward, and save a new plan. This is the most common blocker in practice, and it costs the most to resolve.

## How the harnesses compare

### Claude Code and Codex

| | Claude Code agent teams | Codex multi-agent | Fleet today |
| --- | --- | --- | --- |
| Status | Experimental, off by default (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`); interactive sessions only | `multi_agent` on by default; a second tool set (`multi_agent_v2`) behind a flag; spawns only when the user or instructions ask | Preview; any host with a shell and tmux or cmux |
| Workers | Claude Code sessions only | Codex threads only; per-agent model and effort (not on a full-history fork in v2) | Claude Code, Codex, Cursor, OpenCode, Antigravity |
| Context at start | Spawn prompt plus CLAUDE.md, MCP servers and skills; not the lead's history | Prompt only, or a fork of the parent's whole history (`fork_context` / `fork_turns`), which reuses the parent's prompt cache | Generated prompt: worker rules, `.fleet/rules.md`, ticket, plan `context` and `checks`; not the lead's history |
| Task list | Shared; pending, in progress, completed; dependencies; teammates self-claim unblocked tasks with file locking | None; the parent assigns each task when it spawns or messages an agent | Saved plan; lead assigns; blockers and exact file ownership enforced at launch; `launch --ready` starts what can start |
| Messaging | Mailbox per agent; direct messages between any two agents, delivered automatically; no broadcast | v1: parent to child (`send_input`). v2: any agent to any agent by path (`send_message`, `followup_task`); a message board is in development | Worker to lead: a report's `Status:` line. Lead to worker: keystrokes. No channel between workers |
| Idle and done signals | Teammate's stop notifies the lead with its final answer | `wait_agent` on any of several agents with a timeout; a completion notice lands in the parent's context | `fleet wait` polls report files, exit receipts and screen hashes |
| Plan approval | Teammates spawned from plan mode must have their plan approved | None | None |
| Quality gates | `TaskCompleted` and `TeammateIdle` hooks; exit 2 sends feedback and keeps the teammate working | `SubagentStop` hook sees the last message and transcript and can block with a reason | Lead runs checks by hand; cross-family review bound to the diff's SHA; evidence required to verify |
| Scope and permissions | Teammates start in the lead's permission mode; prompts surface in the lead's session; file conflicts avoided by guidance | Children inherit the parent's sandbox, approvals and directory; a role can only switch tools off (shell, plugins); write sets kept disjoint by instruction | Exact files per ticket checked at launch; edits outside scope not detected (D1); read-only roles by prompt only |
| Isolation | Shared checkout (subagents can use `isolation: worktree`) | Shared directory ("edits made by one agent are immediately visible to all"); worktrees are a separate feature, not per agent | Shared checkout |
| Roles | Subagent definitions as teammate types (tools, model, effort, prompt) | Roles in config or `.codex/agents/*.toml`: instructions, model, effort; built-in `explorer` and `worker` | Ticket kinds (`implement`, `investigate`) and the reviewer role; tiers pick model and effort |
| Limits | 3–5 teammates suggested; teammates cannot spawn teammates | v1: 6 threads, depth 1; v2: 4 threads counting the parent | `max_parallel` and a per-provider `max` |
| Model choice | Per teammate; one vendor | Per agent; one vendor | Per ticket by tier, across vendors, with model-family tracking |
| Persistence | Task list persists; team config deleted at session end; `/resume` does not restore in-process teammates | Threads persist with the session; `resume_agent` reopens a closed one | Everything on disk; `resume` and `handoff` survive compaction and fresh leads |
| Cost visibility | Docs only: cost grows per active teammate | Docs only: subagents use more tokens than one agent | Lead's own context size (`fleet context`); nothing per worker |

Both built-in harnesses share one checkout and keep agents apart by instruction, as Fleet does; neither enforces file ownership the way Fleet's launch checks do. Where they lead is in the plumbing: messages that arrive without polling, hooks that hold an agent until a gate passes, and roles whose tools are removed rather than forbidden in prose.

### T3 Code

T3 Code is a GUI for coding agents that lets any session delegate to another provider through an MCP tool, `delegate_task`, with the provider and model chosen per task. Of the three, it is closest to Fleet: a lead session handing work to other vendors' agents.

| | T3 Code | Fleet today |
| --- | --- | --- |
| How a child runs | Each provider's structured protocol: `codex app-server` JSON-RPC, the Claude Agent SDK, Cursor's SDK, ACP for others; no terminal scraping | Interactive TUI in tmux or cmux; prompt passed at launch, messages typed as keystrokes, state read from the screen |
| Delegating | MCP tools in every session: `delegate_task(task, target{provider, model}, role, isolation, mode)`, `task_status`, `task_cancel`; it tells agents to use their own subagents for same-provider work | Shell commands: `plan` or `add`, `prompt`, `launch` |
| Context in | The task text, plus one line naming the role | Generated prompt with rules, ticket, context and checks |
| Result out | The child's last message. The parent gets a one-line wake-up, batched across children, and pulls the result when it wants it | Report file. `fleet wait` prints one line per report with the lines to read, batching events that land together |
| Messages mid-task | The parent can steer, queue or interrupt a child; a child's question reaches the parent as a short notice | Lead types into the pane; worker writes `Status: blocked` |
| Approvals | A child never gets broader permissions than its parent. Its approvals go to the human, inline in the parent's conversation; the parent agent cannot answer them | The lead never answers; the owner opens the worker's pane |
| Isolation | Shared, or a git worktree from the parent's HEAD (uncommitted edits left out); no file ownership; no merge back | Shared checkout with exact file ownership |
| Quality | No review or verification step; its instructions tell the parent to fix small review findings itself | Cross-family review bound to the diff, lead-run checks, evidence to verify |
| State | Event-sourced in SQLite; retries are idempotent; cancel cascades; resumes after a restart | JSON run state and report files; `resume` and `handoff` |

T3 Code's lesson for Fleet is the transport. Driving each CLI through its own protocol gives exact turn-completed events, approvals as structured requests, messages without keystroke races, and exact token counts (P15). Fleet should take that while keeping what T3 Code lacks: ownership, review and verification. Fleet's `wait` already matches T3 Code's batched, pointer-only wake-ups.

## Gaps and proposals

Ordered by value for the effort. Each item names the gap, the change, why it beats the other harnesses, and where it lands.

### Phase 1: close the holes

Small changes that fix the defects and cut the lead's work without removing a single check.

**P1. Whole-tree change detection** (fixes D1). At launch, record the tree's state: `git status --porcelain=v2 -z --untracked-files=all` plus a content hash of every dirty or untracked path outside `.fleet`. On `wait`, `stop`, `diff` and `verify`, compare with the last snapshot. A path that changed outside the scopes of the workers live since then is an unowned change:

- `wait` prints `SCOPE <path> changed while <ids> were running`.
- `diff` appends an "Unowned changes" section, so the reviewer sees it too.
- `verify` refuses until the lead resolves it, or accepts it with `--unowned-ok` and evidence.

Owner edits during a run show up as well, which is what the lead needs to know. Cost: one `git status` per poll. Lands in `capture_files`/`scoped_diff`, `scan_workers`, `cmd_diff`, `cmd_verify` and example 5.

**P2. A durable inbox** (fixes D2). `fleet send` appends a numbered message to `.fleet/runs/<run>/inbox/<id>.md` and types one line: `Lead message 3: read <path>`. Messages can span lines, every one is on record, and a resumed or relaunched worker reads the earlier ones. Each message carries a key, so a retried `send` never delivers twice (T3 Code's request IDs do the same). `send --changes` also sets the report to `changes-requested`, replacing the manual edit in example 3.

**P3. Hand-down to dependents** (fixes D3). When a dependent's prompt is written, add "What your blockers delivered": each verified blocker's report summary (the lines above `## Appendix`, already short by contract), its changed files, and decisions added since the plan was saved. Cap it per blocker. Claude Code teammates pass this along by messaging each other mid-task; Fleet would hand down verified facts instead of work in progress.

**P4. Scope grants** (fixes D4). `fleet grant <run> <id> <file>... --reason <text>` adds files to a pending or live ticket when no other unverified ticket or live worker holds them. It captures their baseline, records a decision and tells the worker through its inbox. If the files are held, the follow-up run stays the path.

**P5. Executable checks and generated evidence.** Let a `checks` entry be `{"run": "npm test -- src/api.test.ts", "cwd": ".", "timeout": 300}` alongside prose. `fleet gate <run> <id>` runs them quietly and writes command, exit code, duration and the failing tail to `evidence/<id>.gate.md`, which `verify` can take as evidence. The lead stops retyping what it ran, and evidence records what actually happened, not what the lead remembers.

**P6. Compound review and acceptance.** `fleet review <run> <id> [--provider <p>]` stops the writer, captures the diff, writes the reviewer prompt, picks a permitted reviewer from another family and launches it. `fleet accept <run> <id> --evidence <file>` stops the reviewer, verifies reviewer then writer, and lists the dependents it unblocked (`--launch-ready` launches them). Every precondition is already enforced by the runtime, so nothing is skipped; a reviewed ticket drops from about 15 lead calls to about 5.

**P7. Say when not to use Fleet.** T3 Code tells agents to use their own subagents for same-provider work and to delegate only for another provider, a worktree or requested settings. Fleet's delegation guide should say the same: a quick read-only lookup in the lead's own vendor goes to the host's subagent (Claude's Explore, a Codex `explorer`), which costs no pane, no prompt file and no verification turn. Fleet is for work that owns files, needs another vendor, runs long, or must be verified. A change to `references/delegation.md` only.

### Phase 2: team mechanics

**P8. Gate before waking the lead.** This is the cross-vendor version of Claude Code's `TaskCompleted` hook and Codex's `SubagentStop` block. With `wait --gate` (or `gate = true` under `[defaults]`), a report that turns `needs-verification` first runs the ticket's executable checks (P5) and the scope check (P1). On failure, Fleet writes the failing tail to the worker's inbox, sets `changes-requested` and lets the worker fix it, up to `gate_rounds` times (default 2), without waking the lead. The lead then wakes to `REPORT api [needs-verification] gate 3/3 pass, scope ok` or `GATE-FAILED api after 2 rounds`. In a shared checkout a failure may be another worker's half-done edit, so only scoped checks run here; combined-tree checks stay the lead's batch step until P16.

**P9. A structured report header.** A short JSON or YAML block at the top of each report: `status`, `files_changed`, `checks` (command, exit), `criteria` (id, met, where), `findings` (severity, file, confidence) and `questions`. Fleet validates it (changed files inside scope, every criterion answered, every planned check reported) and summarizes it on the `REPORT` line. The lead opens the prose only when something is off.

**P10. Plan first for heavy tickets.** With `"plan_first": true`, the worker writes its approach (files, interfaces, test plan, risks) with `Status: plan-ready` and waits. The lead approves with `fleet approve <run> <id> [--note]`, or first sends the plan to a reviewer from another family. Reviewing a 40-line plan costs a fraction of reviewing a wrong diff. Claude Code has plan approval within one model family; Codex and T3 Code have none.

**P11. A team board: pull, not push.** `.fleet/runs/<run>/board.md` is append-only. Workers add one-line entries with a lock-free `fleet note <run> <id> "<text>"` (the prompt gives the exact command): interface decisions, gotchas, renames. `@lead` wakes the lead with a `NOTE` line; `@<id>` puts a pointer in that worker's inbox. Workers read the board when they start and before touching a shared interface. In Claude Code's mailbox every message lands in the recipient's context; on the board an entry costs only the workers who read it, and the board stays in the run record for the next lead.

**P12. Writer and reviewer talk directly.** Findings get IDs. Fleet sends them to the writer's inbox (relaunching it with `--resume` where the adapter supports it). The writer fixes each finding or rebuts it with evidence, and only rebuttals reach the lead. Each re-review is a fresh reviewer given the previous findings and the writer's answers, so it checks resolutions instead of starting over, as T3 Code's instructions prescribe. A reviewer's false positive no longer costs a fix round plus a lead turn.

### Phase 3: native signals and enforcement

**P13. Per-worker settings for Claude Code workers.** Launch with `--settings <run>/settings/<id>.json` holding:

- a `PreToolUse` hook on `Edit|Write|MultiEdit|NotebookEdit` that blocks paths outside the scope (exit 2, with the message "outside your Fleet scope; ask with fleet note @lead");
- `Stop` and `Notification` hooks that append `turn-ended`, `permission-prompt` and `idle` events to `.fleet/runs/<run>/events/<id>.jsonl`;
- only when the owner opts in (`scope_permissions = true`), `permissions.allow` entries for exactly the scope files and the executable checks.

Reviewers and investigators get file edits denied except for their report. Scope is then enforced by the tool rather than the prompt, idle and permission states are exact rather than guessed from screen hashes, and fewer edits stop at a permission prompt, with nothing allowed beyond the ticket's own assignment.

**P14. The same for Codex and Cursor, plus a permission ceiling.** Codex: a `notify` program passed with `-c` writes `agent-turn-complete` events to the same file, and reviewers run with `-s read-only`. Cursor: its hooks for file edits and stop events, checked against the installed version. Each adapter gets a `signals` field naming its mechanism; screen hashing stays as the fallback. Like T3 Code, which never gives a child broader permissions than its parent, Fleet should know each adapter's effective mode and let the owner set a ceiling: `doctor` and `launch` refuse an adapter above it. OpenCode's default build agent, for example, allows every tool without asking.

**P15. Structured adapters, starting with read-only roles.** T3 Code drives every provider through its own protocol: `codex app-server` (JSON-RPC with `turn/completed` events and approval requests), the Claude Agent SDK, Cursor's SDK and ACP. Fleet can do the same, adapter by adapter, with a `transport` field (`tui` or a protocol), in two steps:

1. **Reviewers and investigators.** They need no TUI. Run them through each CLI's non-interactive mode (`claude -p --output-format json`, `codex exec --json --output-last-message <file> -s read-only`, `cursor-agent -p`) with read-only tools, and save the final message as the report. They become read-only by mechanism, cannot stall at an edit prompt, and report exact token use.
2. **Implementers**, over a long-lived session: `codex app-server`, or `claude -p --input-format stream-json --output-format stream-json`. Turn completion becomes an event, approvals arrive as requests Fleet shows the owner (the lead still never answers them), messages go in as protocol input instead of keystrokes, and token use is exact.

The cost: `fleet agents` must render each worker's event stream instead of a live terminal, and the owner can no longer type into a pane; they message through Fleet, or open the session in its own TUI with the CLI's resume once the worker is idle. Keep `tui` as the default until each protocol adapter passes the same launch, report, verify and stop test. This is the largest reliability gain on the list, and the most work.

### Phase 4: past the teams

**P16. Worktree isolation**, opt-in per run or ticket. Build each worker's tree from the run baseline (`git stash create` records tracked edits without touching the checkout; untracked scoped files are copied). The worker may build and test freely. Fleet diffs the whole worktree (no blind spot), gives the reviewer a frozen snapshot, and applies the patch to the main checkout on `accept` with plain `git apply`, which changes only the working tree. Not `--3way`: it writes the index, which Fleet must never touch, and it refuses files with unstaged owner edits. A patch that no longer applies goes to the lead, or to a per-file `git merge-file`.

Checked in a scratch repo: a worktree made from `git stash create` holds the owner's tracked and staged edits, `git stash create` leaves the checkout untouched, and the worker's patch applies onto the dirty checkout with the owner's staged file still staged. T3 Code's worktrees start from HEAD, so a child never sees the owner's uncommitted edits, and nothing merges its work back; this design does both.

Overlapping tickets can run in parallel, the build ban can lift, and combined checks stop failing on other workers' half-done edits. Cost: disk space and dependency setup per tree, so the shared checkout stays the default.

**P17. Cross-vendor fan-out.** `"fanout": ["claude", "codex", "cursor:<model>"]` on an `investigate` ticket runs one investigator per family on the same question. Fleet lists where they agree and differ, and the lead, or a judge from another family, settles the rest. With P16, a heavy or uncertain implementation can run best-of-two across vendors with a cross-family judge. Claude Code's docs recommend competing hypotheses for hard bugs; Fleet can add model diversity that the single-vendor harnesses lack, with the judging step T3 Code lacks. Reserve it for heavy tickets, since it multiplies cost.

**P18. Warm workers.** `launch --after <verified-id>` hands a new ticket to the same CLI session (`--resume`) when it works in the same files or area, so the worker keeps what it already read. Cap it at a few tickets per session (Claude Code's docs suggest 5–6 tasks per teammate) and use it only for light or standard follow-ups, because context grows.

**P19. Usage accounting.** `fleet usage <run>` reads each worker's session log and reports tokens, wall time, rework rounds and first-pass acceptance per ticket, provider, model and tier, plus the lead's own reading. Fleet already picks Claude's session ID at launch and parses lead logs in `fleet context`; structured adapters (P15) report usage directly. Tiers and routing can then be tuned on data.

**P20. A supervisor.** `fleet supervise <run>` runs in the background: gates (P8), inbox delivery, stall checks and, when the owner opts in with `auto_dispatch`, `launch --ready` after each acceptance. The lead wakes only for judgment calls: a gate that still fails after its rounds, a blocked question, a plan to approve, a disputed finding, an unowned change.

**P21. Fleet as an MCP server** (optional). T3 Code exposes its orchestrator as MCP tools, with a token scoped to each session. Fleet could do the same: typed tools for the lead (`plan`, `launch`, `wait`, `review`, `accept`) with structured results instead of parsed text, and for each worker a token that allows only `note`, `ask` and `report` on its own ticket. The CLI stays the source of truth; this is a second front door.

### Suggested order

1. **Release 1:** P1–P7. Fixes every verified defect and cuts the lead's calls per reviewed ticket from about 15 to about 5.
2. **Release 2:** P8, P9 and P13. Failed checks go back to workers without waking the lead, and Claude workers' scope is enforced by the tool.
3. **Release 3:** P10–P12, P14 and P15. Collaboration on par with agent teams, across vendors, over structured protocols.
4. **Release 4:** P16–P21. Where Fleet pulls ahead of all three.

## Where the tokens go

**The lead.** Every tool call is a model turn over the lead's whole context. Prompt caching makes most of that a cache read, but at a 150K-token lead a reviewed ticket's ~15 calls still read about 2M cached tokens, and [lead context](skill/references/lead-context.md) notes that answers degrade well before the window is full.

| Step for one reviewed heavy ticket | Today | After P5, P6 and P8 | After P20 |
| --- | --- | --- | --- |
| Launch | 1 (shared with `--ready`) | 1 (shared) | 0 (`auto_dispatch`) |
| Wake on report, read it, run checks | 3–4 | 1 (wake carries the gate result) | 1 |
| Fix round after a failed check | 3–4 per round | 0 (gate sends it back) | 0 |
| Review: stop, diff, prompt, launch, wait, read | 6 | 2 (`fleet review`, wait) | 2 |
| Accept: stop, two evidence files, two verifies | 5 | 1–2 (`fleet accept`, generated evidence) | 1–2 |
| **Total, no fix round** | **~15** | **~5** | **~4** |

**The workers.** Every worker rereads repo instructions, rules, spec and scoped code. Hand-down (P3) and the board (P11) cut rediscovery; warm workers (P18) skip it for follow-ups; plan-first (P10) prevents a whole wrong implementation; headless reviewers (P15) drop the TUI; same-vendor lookups move to the host's own subagents (P7). The gate (P8) also saves worker tokens: a failing check comes back as the failing tail, not a lead's rewrite of it.

**What it would cost to copy push messaging.** Claude Code's docs say team token use grows with each active teammate, and every message lands in its recipient's context. Fleet's file-based reports already avoid that, as T3 Code's pull-on-demand results do; the board and inbox keep it that way.

## What not to copy

- **Self-claiming in a shared checkout.** Claude Code teammates pick unassigned tasks themselves, and its docs tell you to give each teammate distinct files to avoid overwrites. Fleet's lead-assigned waves with exact ownership are safer; `launch --ready` plus `auto_dispatch` (P20) gives the same throughput.
- **Push messages into every context.** Use the pull board plus targeted inbox pointers (P2, P11).
- **Forking the lead's history into workers.** Codex can fork a parent's whole history into a child, which reuses the prompt cache when both run the same model. Fleet's workers run other vendors' models, so a fork would be a cold, large prompt. A short brief plus hand-down (P3) is the cheaper path.
- **Workers that spawn workers.** Claude Code teammates cannot spawn teammates, and T3 Code tells subagents not to delegate. Keep Fleet one level deep. A worker may still use its own CLI's read-only helpers, such as Claude's Explore subagent, to search without filling its context.
- **Worktrees from HEAD.** T3 Code's drop the owner's uncommitted edits. Start from the run baseline instead (P16).
- **Automatic approvals.** The rule that the lead never answers a trust, login or permission prompt is right, and T3 Code follows it too. P13's allow-list is the owner's opt-in, and it only covers the ticket's own files and checks.
- **The lead fixing findings itself.** T3 Code tells the parent to fix small review findings itself. In Fleet the lead would then verify its own work; fixes go back to workers.

## Sources

- Fleet: `skill/bin/fleet`, `skill/SKILL.md`, `skill/templates/`, `skill/references/` and `skill/providers.toml` at `0.1.0-preview.23`. D1 and D2 were reproduced with a fake worker adapter in headless tmux (`backend = "tmux"`); Claude Code flags were checked against `claude --help` 2.1.318.
- Claude Code agent teams: <https://code.claude.com/docs/en/agent-teams>; subagents: <https://code.claude.com/docs/en/sub-agents>; hooks: <https://code.claude.com/docs/en/hooks>; team token costs: <https://code.claude.com/docs/en/costs#agent-team-token-costs>.
- Codex: source of `openai/codex` at `main` commit `9ff072db` (2026-11-04; latest stable release 0.166.0), chiefly `codex-rs/core/src/tools/handlers/multi_agents_spec.rs`, `codex-rs/core/src/agent/role.rs`, `codex-rs/core/src/agent/child_config.rs`, `codex-rs/prompts/src/multi_agent_instructions.rs`, `codex-rs/config/src/config_toml.rs` and `codex-rs/features/src/lib.rs`; docs at <https://developers.openai.com/codex/subagents> (read through search summaries; the session's network blocked the site).
- T3 Code: source of [`pingdotgg/t3code`](https://github.com/pingdotgg/t3code) at commit `dd5f98e` (2026-11-04), chiefly `apps/server/src/mcp/OrchestratorMcpService.ts` (`delegateTask`), `packages/contracts/src/orchestratorMcp.ts` (tool schemas), `apps/server/src/mcp/delegatedTaskModes.ts` (permission ceiling), `packages/provider-core/src/server/orchestrationInstructions.ts`, `packages/provider-core/src/server/subagentProjection.ts`, `apps/server/src/orchestration-v2/ProviderContinuationService.ts` and `docs/orchestration-v2/orchestrator-mcp-server.md`; [discussion #10242](https://github.com/pingdotgg/t3code/discussions/10242).
