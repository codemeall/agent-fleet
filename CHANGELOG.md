# Changelog

## 0.1.0-preview.30

Teamwork release 7 (#51): Codex workers signal their turns, Fleet reports how each provider, model and tier performs, a heavy change can run as best-of-two across vendors, and accept refuses work below a strict quality bar by default.

- **Turn signals (#48).** Adapters get a `signals` field: `claude-settings`, `codex-notify` or `""`. `hooks`, its older name, still works, and an owner's `hooks = ""` still turns signals off.
  - Codex workers launch with `-c notify=[…]` in a new `{signals}` slot, so each finished turn runs `fleet _notify`. That appends a `turn-ended` event to the worker's events file without reading any config, state or lock.
  - `wait` wakes `IDLE` when a Codex turn ends without a hand-back, quoting its last words. An approval prompt still wakes `STALLED`.
  - Cursor gets no signals, since its hooks were not checked against `cursor-agent`, and `signals = "cursor-hooks"` is refused.
  - `doctor` names each adapter's mechanism, and warns when the CLI's `--help` lacks the flag a signal slot passes, since the launch itself may then fail.
  - providers.md records what was checked in the Codex source, and when.
- **Usage report (#49).** `fleet usage [<run>...] [--all] [--by ticket|provider|model|tier|role] [--json]` reports, per group:
  - tickets and outcomes, and first-pass acceptance;
  - repairs, gate rounds and review rounds, and findings per round;
  - launches, wall time and tokens.

  It takes no lock and saves nothing, and a count Fleet lacks stays `unknown`, never zero. A best-of-two ticket goes to the writer kept with `--pick` and counts both writers' rework, and its judges count as its review rounds; its first pass is the kept writer's own. A ticket kept below the bar by the owner's words is never a first pass: it shows as `accepted anyway`. `--all` lists a run it cannot read, or a folder that is not a run, as skipped. A report that is not valid UTF-8 never stops it.
- **Best of two (#50).** In a worktree run, `"fanout"` on an implement ticket runs two writers from two model families, `<id>--<provider>`, each in its own tree.
  - Each writer has its own prompt, gate rounds and ledger entry. `wait` and `supervise` gate both, then wake once with `FANOUT`, giving each writer's gate result, header and diff size.
  - `fleet judge` freezes both diffs and launches a read-only judge from a third family, whose `pick` wakes the lead as `VERDICT`.
  - `fleet accept <run> <id> --pick <writer-id>` merges that writer's tree, verifies it and the judge, and saves the other writer's change as a patch. The judge is the kept diff's cross-family review when its family differs and its frozen diff still matches. Plain `accept`, `verify` and `review` are refused, naming the writers, and so is `grant` once a writer has launched.
  - The `FANOUT` wake comes even when a busy lock deferred a writer's gate.
  - **With release 6's rules:**
    - A writer relaunched after a judge gets its gate rounds back, as after a review round.
    - Each new judge starts its own scope window.
    - An earlier judge's report is set to `superseded` once a later one is verified, and the writer set aside no longer waits on the lead.
    - `@<ticket>` on the board reaches both writers.
    - `send` to the ticket's own ID is refused, naming the writers and judges, and so is `send --findings`, since the judge is the ticket's review.
- **Strict acceptance (#53).** `[defaults] acceptance = "strict"` is the default for new runs. A run keeps its value, and a run from before this release reads as `"warn"` and behaves as before. `verify`, `accept`, fan-out accept, `accept --pick` and supervise's `READY` ask one policy.
  - **What it refuses:** a missing or faulty `fleet` header, an unmet criterion, a failing check or an open question. Also a missing, stale or unreadable review, or one that lists an open high or medium finding. For best of two, so does a judge that has not handed back, recommends neither writer or picked the other one. Each refusal names every item and its way out, before anything is stopped, merged or verified.
  - **`--accept-finding F<n> "<why>"`** accepts the writer's rebuttal of one finding the latest review lists. That includes a rebuttal in the writer's hand-back after `send --findings`, before any re-review.
  - **`--accept-anyway "<owner's words>"`** overrides the bar and records each item it overrode. It never overrides a missing or stale required review, a report not at `needs-verification`, or a change outside every ticket.
  - A refusal after the bar records no override and claims none.
  - **`[defaults] accept_checks`** run the combined-tree checks in the checkout at accept, after a worktree ticket's merge and before anything is verified. A pass is reused while the tree is unchanged, and the evidence keeps a failure's tail.
  - A check that changes a ticket's own files refuses as the checks' doing, never the worker's, and the owner's words cannot override it. A merged file the checkout changed afterwards is a `CONFLICT`, never recorded as the merge.
- **Safety boundaries.** An independent review found ways a worker could get past Fleet's own checks; each was reproduced first.
  - **Fleet's own files.** Each file Fleet writes in `.fleet/runs/<run>/` that it decides with (`run.json`, steers, launch baselines and review diffs, a Claude worker's settings and guard scope, the records that excuse a check's output or reuse a pass) now also gets a copy outside the checkout, under `agent-fleet/control/` in the cache folder. A command holding the lock refuses when a file differs from its copy, naming it and the `cp` that puts Fleet's back. A worker could otherwise add its stray file to `lead_files`, delete its launch snapshot, forge a check output or widen another worker's guard. Older runs start a record without refusing.
  - **The scope check** now counts `.fleet/rules.md`, which every prompt carries and which Fleet no longer reads through a symlink, and a gitignored `.gitignore` that is created or changed, since one that lists itself hid new files. A file name with a line break is fingerprinted too.
  - **Worker text in wake lines.** A header's file names are quoted and its problems stay on one line, so they can no longer forge a wake line or send a terminal escape. `IDLE` quotes the last words so they cannot close the quotes, marked as data. `peek` marks the screen as the worker's output. Bidi, zero-width and C1 control characters are flattened. An imported issue's title is quoted as untrusted.
  - **Docs.** `scope_permissions` says plainly that allowing a check lets the worker run code it wrote, and providers.md no longer claims a worker never runs in `acceptEdits`: it keeps the account's mode.
- **CI.** `push` runs only on `main`, so a PR commit is tested once, and each job stops after 30 minutes.
- **Skill.** The lead's guide covers best of two and `--pick`, strict acceptance and its two overrides, `accept_checks`, `fleet usage` and `IDLE` for any worker, still within its size budget.

## 0.1.0-preview.29

Teamwork release 6 (#47): a wrong approach is caught before it becomes a wrong diff, workers share what they learn without spending the lead's context, and writer and reviewer settle findings so that only disputes reach the lead.

- **Plan first (#44).** `"plan_first": true` on an implement ticket, or `[defaults] plan_first = "heavy"`, makes the worker write its approach with `Status: plan-ready` and wait.
  - `wait` and `supervise` wake once with `PLAN <id>`, quoting the plan as data.
  - `fleet approve <run> <id> [--note]` saves the plan in `plans/<id>.md`, records the decision, gives the ticket its files and tells the worker through its inbox. `--review` first sends the plan to a reviewer from another family, whose `FINDINGS <id>: plan, …` come back to the lead.
  - Until approval the ticket owns no files. An early edit shows as unowned, and approve refuses it unless `--unowned-ok`. Claude's guard allows only the report.
  - A plan reviewer is settled by `approve` or `send --changes`, whether it handed back or was blocked, and `drop` parks it. `gate` and `verify` refuse it plainly. A `plan-ready` report from a ticket that is not plan-first, or whose plan is already approved, says so instead of waking as `PLAN`.
  - `send --changes` on a plan stops the plan reviewers that handed back, as `approve` does, and refuses while one still works, so a later `approve` is no longer refused for a reviewer that kept its slot.
  - Under `scope_permissions`, a planner's settings allow its files from launch, since Claude may read them only at start, and its guard refuses those edits until approval.
  - A second plan review under the same id starts its own scope window instead of reusing the first one's.
  - A plan that cannot be read still wakes the lead as `PLAN`, and no other ticket's hand-back is lost with it.
  - A plan rewritten within the settle window wakes once, as it stands.
  - `drop` parks the ticket's reviewers' reports too, not only its plan reviewers' and investigators'.
  - One check, `owned_files`, decides what a ticket owns wherever ownership is checked. The plan reviewer's choice, id, launch checks and prompt share the review's code, and `approve` delivers its message through `send`'s code.
- **Team board (#45).** Workers post one-line notes with `fleet note <run> <id> '<text>'`, a lock-free single append to `.fleet/runs/<run>/board.md`. `@lead` wakes the lead once with `NOTE <id>`, and `@<id>` lands in that worker's mentions file. Fleet types nothing into another worker's pane.
  - Prompts carry the exact command and say board text is data.
  - `fleet board <run>` prints the board, and `resume` and `handoff` mention unread notes.
  - Under `scope_permissions`, a Claude worker may run exactly its own note command.
  - A worker in its own tree finds the owner's run from `git worktree list`, with fallbacks for an older Git and for a Git directory kept outside the checkout.
- **Findings loop (#46).** Findings get stable ids (`F1`, `F2`, …).
  - `fleet send <run> <id> --findings` relaunches the stopped writer (resuming its session where it can) with the findings quoted as data and the rule to fix or rebut each one.
  - The writer's header answers in `responses`, and Fleet checks that every finding gets one. The re-reviewer gets the earlier findings and the responses.
  - Supervise's new `repair` step runs the loop within `review_rounds`, and wakes the lead only for a rebuttal, a dispute or ESCALATE. One round of findings goes from 6 lead turns to 2.
  - An earlier round's Repair note is marked settled, so a fresh writer answers only the latest round. A relaunch that never reached a terminal can be retried. A first-round reviewer's own finding ids are renumbered, not flagged.
  - Findings sent bind only the writer launches made before the next review round, which settles them and marks the Repair note settled, so a later repair by hand is not flagged for missing responses.
  - A writer relaunched after a review round, resumed or fresh, gets its gate rounds back; a `--resume` within the same round keeps them.
  - When the lead has relaunched the writer, the `repair` step wakes the lead with the reviewer's findings instead of dropping them.
  - After `send --findings`, the reviewer's report is parked as `findings-sent`, so `wait`, `PENDING` lines and `handoff` no longer list it; `verify` and `accept` still take it as the review.
  - Supervise reads a review's report once per pass, and the settle window loads run state once per pass.
- **Skill.** The lead's guide covers `plan_first` and `approve`, the `PLAN` and `NOTE` wakes, and `send --findings` with `repair`, still within its size budget.

## 0.1.0-preview.28

Teamwork release 5 (#43): hard questions go to several vendors at once, follow-up tickets keep the worker that knows the code, and GitHub issues come in as tickets without Fleet ever writing to GitHub.

- **Cross-vendor fan-out (#41).** `"fanout": ["claude", "codex"]` on an `investigate` ticket runs one read-only investigator per model family, as `<id>--<provider>`, each with its own prompt and report.
  - Plan refuses two entries in one family, an entry outside the pool or above the permission ceiling, and fan-out on an implement ticket.
  - When all investigators are back, `wait` and `supervise` wake once with `FANOUT <id>: n reports; agree on …; differ on …`. Agreement is computed from the report headers (findings by file and severity, criteria by id), and everything is quoted as data.
  - `fleet judge <run> <id>` launches a judge from a family none of them used. It gets every report as data, and its verdict wakes the lead as `VERDICT <id>`.
  - One `verify` covers the whole ticket, every judge that handed back included. A judge that exited without a verdict never blocks it: Fleet notes this and records it as a decision. `fleet cost` lists each investigator and the judge.
- **Warm workers (#42).** `fleet launch … --after <verified-id>`, or the plan field `"after"`, hands a light or standard follow-up to the CLI session that did the earlier ticket, so the worker keeps what it read.
  - The new ticket's prompt, report, inbox and scope replace the old ones.
  - It works only for a verified, exited writer on a resumable adapter (Claude today; Codex cannot name a session at launch), with the same provider, model and effort, in a shared run.
  - A chain is capped at `warm_max` tickets (default 4), and launch warns above `warm_context_tokens`.
  - Plan adds `after` to the ticket's blockers, and the ledger and `fleet cost` show each chain. A ticket relaunched fresh still counts as having used the session.
- **Issue import (#20).** `fleet import <owner/repo#N|URL>... [--label L] [--milestone M] --dir <dir>` writes one local ticket per GitHub issue. Each file has its title, source link, labels and assignees, with the body fenced as untrusted data and its checkboxes kept.
  - It only reads (`gh issue view` and `gh issue list`; a test asserts it).
  - Re-import refreshes a file only when the issue changed. It keeps a file the lead edited unless `--force` is given.
  - A missing or signed-out `gh` is the owner's to fix; Fleet never signs in.
- **Skill.** The lead's guide covers import, fan-out, `judge` and `after`, still within its size budget.

## 0.1.0-preview.27

Teamwork release 4 (#39): workers can run in their own git worktrees, a supervisor takes the mechanical steps off the lead, and a benchmark kit gets ready to measure Fleet against one agent.

- **Worktree isolation (#15).** `fleet init <run> --isolation worktree` (opt-in; shared checkout stays the default) gives each implementation ticket its own git worktree. Each tree is made from the current checkout with `git stash create`, so it has the owner's tracked, staged and untracked edits, and ignored files are never copied. Trees live outside the checkout, under the cache folder (`worktree_root` overrides). An optional `worktree_setup` command prepares each one.
  - Workers may build and test in their own tree, and tickets with overlapping files may run together.
  - Diff, the scope check, gate, the review snapshot and the Claude hooks guard all read the tree.
  - `fleet merge`, which `accept` runs first, applies the ticket to the checkout with plain `git apply`. It changes the working tree only, never HEAD, the index or refs. It refuses with `CONFLICT <id>: <files>` and applies nothing when the checkout's copy moved or the patch does not apply. The lead then re-adds the work, or merges by hand and runs `merge --resolved`.
  - Repairs and `--resume` reuse the ticket's tree. A ticket repaired after a merge merges only what changed since. `accept` and `drop` remove the tree (`drop` keeps its changes as a patch, and says when merged changes stay in the checkout). A launch that fails removes the tree it made. `fleet check` lists trees, and `--clean-worktrees` removes orphans.
  - `worktree_setup` runs while `launch` holds the checkout's lock, so keep it short.
  - Claude and Codex trust a worktree through its main checkout. Both get the run folder with `--add-dir`.
  - Fleet's own `git status` calls no longer refresh the index (`--no-optional-locks`), in shared runs too.
- **`fleet supervise` (#37).** It runs in place of `wait --gate`, with the same options and lines. It takes the steps the owner opted into (`[defaults] supervise` or `--auto gate,review,dispatch`; the default `["gate"]` is exactly `wait --gate`):
  - it starts the required cross-family review on a clean hand-back;
  - it launches only tickets a verification unblocked, never one the lead held back;
  - it wakes the lead with `READY <id>` or `FINDINGS <id>`, or for any judgment call.
  
  It never answers a prompt, verifies or accepts, and it takes the run lock only briefly, without waiting for it. For one reviewed ticket the lead goes from 4 turns to 2.
- **Benchmark kit (#38).** `python3 benchmarks/run.py <case> --arm fleet|single|both` runs Fleet and one agent of the same model on the same commit, ticket text and checks, each in its own temporary clone. It writes a results table with criteria, checks, findings, wall time, a cost estimate from the owner's own price file, and manual fixes. Losses are shown, and unknowns are never zero. Nothing paid runs without `--yes-paid`; `--fake` runs both arms with fake CLIs in CI. The three real cases and the paid runs stay with the owner (#16). The README makes no claim until results exist.
- **Skill.** The lead's guide covers `supervise` (`READY`, `FINDINGS`), worktree runs and `CONFLICT`. Wordy passages were trimmed to keep it within its size budget.

## 0.1.0-preview.26

Teamwork release 3 (#35): scope and read-only roles are enforced by the tools, cost and time are measured, and the owner can check the real CLIs with one command.

- **Launch profiles and a permission ceiling (#11).** Each adapter declares `profiles.worker` and `profiles.review` (`{ args, level }`, filling a `{profile}` slot), at a level of `read-only`, `workspace-write` or `full-access`. `[defaults] permission_ceiling` (default `workspace-write`) is enforced by `launch` and `doctor`, and the reviewer picker never chooses a provider launch would refuse. OpenCode's build agent ships honestly as `full-access`, so it needs the owner's ceiling or a tighter `opencode.json`. `doctor` warns when a flag an adapter uses is missing from its CLI's `--help` (and says "flags unverified" when help is unreadable), prints each profile's level, and finds Cursor as `cursor-agent` or `agent` (`bin_aliases`). Worker launch lines are unchanged.
- **Ledger, caps and `fleet cost` (#14).** Every worker launch, relaunch and resume is a ledger entry in the run: role, provider, model, effort, tier, start, end (from the exit receipt) and wall time, plus tokens a Claude worker's own session log reports (others show unknown, never zero). `max_workers` and `max_wall_minutes` under `[defaults]` refuse new launches past them, never stopping running workers. `fleet cost <run> [--json]` prints the table; `resume` shows totals and caps, and `status` each worker's time.
- **`fleet smoke` (#32).** The owner's one-command live check: without `--yes-paid` it only prints its plan; with it, it runs one tiny ticket per chosen provider through launch, gate, cross-family review and accept in a throwaway repo, on a private tmux server, with the owner's real CLIs, and writes a pass/fail report with each CLI's version and any stalled worker's screen. It never writes the owner's repository or config, and cleans up on failure and Ctrl-C.
- **Claude hooks (#34, #19).** Claude workers launch with `--settings` holding Fleet's hooks, run as `fleet _hook`, which reads a small scope file and never the run lock, and fails safe. A `PreToolUse` guard refuses an edit outside the ticket's files and its report as it happens (`..` and symlinks resolved); reviewers and investigators may write only their report, and a Claude reviewer stays in manual mode with one allow rule, for its report, so that write no longer asks. Hooks never change a permission mode, so a guard that errs, times out or cannot run leaves Claude's own prompts in charge. `Notification` and `Stop` events wake `fleet wait` as `PERMISSION <id>` (the owner answers it; Fleet never does) or `IDLE <id>`, and `STALLED` names a likely approval prompt for other CLIs. With `scope_permissions = true` (off by default), Claude workers also get allow rules for exactly their own files, report and planned check commands; `fleet grant` extends them. Headless runs get no hooks.
- **Headless read-only reviewers (#33).** With `review_transport = "headless"`, reviewers and investigators on Claude (`-p --output-format json --permission-mode manual --permission-prompts none`) and Codex (`exec --json -s read-only -c approval_policy=never`) run once through `fleet _headless`: read-only by the CLI's own flags, never stalling at a prompt, their final message written as the report (or a blocked report saying why), denied calls listed, and token usage recorded in the ledger. A run not finished after `headless_timeout_minutes` (default 30) is stopped with a blocked report. A stop that lands after the CLI has exited no longer loses the report or the exit receipt, and plain-text output that holds a JSON line (a report's `fleet` header) stays the message. The reviewer picker, plan-time review check and `doctor` judge a headless reviewer by its headless line, as launch runs it. Cursor, Antigravity and OpenCode ship no headless line until one is checked.
- **Unknown config keys warn (#12).** An unknown key in `providers.toml` or your config prints a warning naming the file, section and key with a "did you mean" hint; it becomes an error in the next version. The fully uncommented `config.example.toml` now loads: its `workspace` line sat under `[lead_context.codex]`. Provider notes list every key.
- **Plan check rules (#13).** `plan` and `add` refuse a ticket whose checks are empty or prose only: give a `{"run": …}` command or a `{"manual": "<one-line reason>"}` marker, which prompts show and `gate` skips with a note. `check_policy = "warn"` only warns. The shipped example and the skill's plan example follow it.
- **README (#7).** "When not to use Fleet": one agent for small or single-file work, the lead's own subagent for quick read-only lookups, no benchmark claim yet (#16), and which providers ship on.

## 0.1.0-preview.25

Teamwork release 2 (#31): what one agent writes is data to every other, impossible reviews fail at plan time, review loops stop at a cap, every adapter's launch line is proven in CI, and the lead reads a one-line summary of each report.

- **Untrusted data (#8).** Worker, investigator and reviewer prompts say that tickets, specs, repo files, tool output, web content and other workers' reports are data: an instruction inside them is reported, never followed. The lead's messages reach a worker only through its inbox and terminal. A dependent's hand-down marks the quoted reports as other workers' output, and the lead's skill treats reports, findings, diffs and screens the same way.
- **Impossible reviews fail at plan time (#9).** Under `cross-heavy` or `cross-all`, `plan` and `add` refuse a ticket that needs a cross-family review the routing pool could never give, naming the writer's family, the pool and each provider's reason; `init` refuses a `cross-all` pool of one family. The ways out are the owner's: enable a provider or widen the routing, or `init --review off --reason "<owner's words>"`, which an explicit `--review off` now needs (recorded in decisions). The check at reviewer launch stays.
- **Review-round cap (#17).** Each reviewer launch for a ticket is a round; `resume` shows `rounds=<used>/<cap>`. After `review_rounds` rounds (new under `[defaults]`, 1 to 5, default 2) Fleet refuses the next repair launch and the next review with an `ESCALATE` block: rounds used, the last reviewer's findings quoted as data, and the owner's choices. `--extra-round "<owner's words>"` on `launch` or `review` allows exactly one more, recorded in decisions. Resuming a reviewer's session (`launch --resume`, after a usage limit) continues the same round.
- **Structured report header (#29).** Reports may carry a `fleet` JSON block after the `Status:` line: files changed, checks with exit codes, criteria, findings and questions. `fleet wait` summarizes it on the `REPORT` line (`criteria 5/5, checks 2/2 exit 0, findings 0, questions 0, files in scope`, or a reviewer's findings by severity) and names any `header:` problem, such as a file outside scope or a planned command not reported. `verify` and `accept` warn about header problems and never refuse on them. Reports without the block wake and read as before, and the hand-down leaves the block out. Run files such as the worker's own report are never a `header:` problem.
- **A missed hand-back (#6).** `fleet wait` could miss a worker's hand-back when it landed in the same mtime tick as the first one (coarse-mtime disks, fast runs): a parked status was never recorded. Parked statuses are now recorded, the dedupe key includes a content digest, and `send --changes` clears wait's marker so even a byte-identical hand-back wakes the lead. A report an earlier Fleet already showed does not wake the lead again after the upgrade. CI also runs Python 3.13 next to 3.11.
- **Adapter smoke and end-to-end tests in CI (#18).** Each shipped adapter runs launch, wait and stop in headless tmux against a strict fake that accepts only its exact launch line and quit sequence, and release 1's teamwork commands run end to end with two fake providers. No real CLI or account is used. Provider notes gain a tested-version table.
- **The gate loop (#30).** `fleet wait --gate` (or `gate = true` under `[defaults]`, off by default) runs an implementation ticket's planned commands when its worker hands back. A pass wakes the lead once, with `gate N/N passed` and the evidence `fleet gate` would write. A failing check, while rounds remain (`gate_rounds`, default 2), goes back to the worker through its inbox (the failing tail quoted as data), the report is parked and the lead is not woken; once rounds are used up, `GATE-FAILED <id> after N rounds` wakes it. Each fresh worker launch (a repair) gets the rounds again, and a round that cannot be sent back (the inbox cannot be written) wakes the lead with the report left as it was. Scope problems always wake the lead and are never sent to the worker, and files the loop's own commands write never count when deciding to wake (verify still checks them). Wait changes run state only under a briefly taken lock, retrying on the next poll when the lead holds it, and never loses a hand-back. `accept --gate` reuses a passing gate when HEAD, the tree, the commands and the evidence are unchanged.

## 0.1.0-preview.24

Teamwork release 1 (#21): the runtime does the mechanics of review and acceptance, so the lead spends its turns on judgment. A reviewed ticket takes the lead about five calls instead of about fifteen, edits outside a ticket's files no longer pass unseen, and workers get lead messages and their blockers' results intact. Background and comparison with Claude Code agent teams, Codex multi-agent and T3 Code: [TEAM-REVIEW.md](TEAM-REVIEW.md).

- **The scope check (#10).** A worker's diff covered only its own files, so a stray edit or a new file passed `diff`, review, `verify` and `check`. Each launch and `init` now record HEAD and a fingerprint (mode and Git blob ID) of every changed or untracked file, writing nothing to `.git`. `diff` and `verify` refuse while a file changed since the worker launched that no ticket owns. A ticket owns its files from its first launch until it is verified or dropped, so work running alongside is never blamed. `.fleet/`, gitignored files, the run's ticket files, files the lead handed Fleet, files `fleet gate`'s checks wrote while no worker ran, and the new `scope_ignore` list under `[defaults]` (default `.scratch/`, `node_modules/` and common Python caches) never count. Reviewers and investigators own no files. Ways out, each recorded in decisions: the owner reverts, `fleet grant`, `fleet drop`, or `--unowned-ok "<owner's words>"` for the owner's own edits, which accepts that exact content. `stop` lists such files when a worker exits, `wait` adds `SCOPE` lines to a wake it is already making (never a wake of its own), and `check` notes them since `init`. Ignored folders are never hashed, a file that vanishes mid-check (another worker's test cache) no longer fails it, and a failed check during `stop --all` never keeps the other workers from stopping. Runs from earlier versions skip the check with a note. Example 5's evidence no longer claims what `fleet diff` could not see.
- **A durable inbox (#22).** `fleet send` typed raw keystrokes, so a multi-line message could arrive as several prompts and nothing was kept. It now appends `## Message N` to `.fleet/runs/<run>/inbox/<id>.md` and types one line pointing to it. Numbers survive a relaunch; prompts and `launch --resume` name the inbox. `--file` (or `-` for stdin) reads the message, `--key` makes a retry send nothing, and `--changes` sets a `needs-verification` report to `changes-requested` first, replacing the manual edit in example 3. A ticket with no running worker gets the message on record only. On Python 3.11, text after `send`'s options is accepted.
- **Hand-down to dependents (#23).** A dependent's prompt came from plan-time `context`. Its prompt now ends with "Since this ticket was planned": each verified blocker's changed files and its report above the appendix (at most 40 lines, then a pointer), unverified blockers named as such, decisions recorded after the plan (`plan` now counts its own), and the path of `notes.md`. `launch` warns when a prompt was written before a blocker was verified.
- **Executable checks and `fleet gate` (#25).** A ticket's `checks` may mix prose with commands, `{"run": "<cmd>", "cwd": "<dir>", "timeout": <s>}`. `fleet gate <run> <id>` runs them without the lead's session variables or stdin, kills a command's process group at its timeout, runs the scope check, and writes `evidence/<id>.gate.md` with each command's exit code, duration and failing tail. It exits 1 on any failure or unowned change, and takes no lock, so long checks never block the lead. `verify` (and `recover`) take `--evidence` more than once.
- **Scope grants (#24).** "I need one more file" cost a stop, a follow-up run and a new plan. `fleet grant <run> <id> <file>... --reason <text>` adds files to a pending `implement` ticket, launched or not. It refuses a file a live worker of another ticket holds, a file shared with another unverified ticket that neither waits on, and any grant while the ticket's reviewer runs. A launched ticket's baseline for the new file is its content at the first launch, rebuilt from Git without writing to `.git`, so an edit the worker already made lands in its diff and review. When Fleet has no copy of that content it refuses unless `--accept-current`. The grant is recorded on the ticket and in decisions, and the worker hears of it through its inbox. Example 8 grants first and keeps the follow-up run for when grant refuses.
- **`fleet review` and `fleet accept` (#26).** A reviewed ticket took the lead about 15 calls. `fleet review <run> <id>` stops the writer, saves its diff, writes the reviewer prompt and launches a permitted reviewer whose known family differs from the writer's, choosing one from the routing pool, `defaults.prefer` and the other enabled providers when `--provider` is not given, or listing each candidate's reason when none fits. `fleet accept <run> <id> --evidence <file> [--gate] [--launch-ready]` stops the reviewer and the writer, runs the gate when asked, verifies the reviewer and then the writer, and prints the `UNBLOCKED` dependents (`--launch-ready` launches them). Both run the existing commands, so every precondition still applies. `review` refuses before stopping a writer whose report does not say `needs-verification` or that changed files outside its scope, and each review round gets its own scope window and a fresh inbox. `accept` refuses every problem it can see before verifying anything; with `--gate` it holds the run's lock while the checks run, so for long checks run `fleet gate` first and pass its evidence. In the end-to-end check a reviewed ticket took the lead four calls: `gate`, `review`, one `wait` and `accept`.
- `fleet gate` records the files its own commands write outside every ticket while no worker runs (caches, reports), so the scope check does not blame a worker for them until they change again; the evidence says which case applied.
- **When not to use Fleet, and the skill (#27).** The delegation guide sends quick read-only lookups in the lead's own model to the host's subagent, and keeps everything that edits files, needs another vendor or must be verified in Fleet. `SKILL.md` step 5 uses `gate`, `send --changes`, `review`, `accept` and `grant` (18,975 bytes, within its budget), and the README describes the scope check, the inbox, hand-down, gate and grant.
- A template field name quoted in a prompt's content (a Handlebars `{{id}}` in `context` or a handed-down report) no longer makes `launch` refuse it; one added to the prompt by hand still does. The same goes for a `<!-- LEAD: … -->` comment quoted in content.

## 0.1.0-preview.23

Turn providers on and off with a command, placeholder adapters can no longer pass for working ones, and shipped model lists match what the CLIs offer today: Cursor and OpenCode offer every model they list.

- `fleet provider enable|disable <name>` sets `enabled` for one provider in the user config, creating the file or section if needed and leaving every other line, comment and line ending alone. It prints the next steps (`fleet doctor`, `defaults.prefer`) and changes nothing when it cannot make the edit safely.
- Writing the user config (`provider`, `account add`) goes through a symlink to its target and keeps the file's permissions; it used to replace a symlinked config with a plain 644 file.
- `fleet init` refuses `single:`/`agents=` routing that names a provider that is off or a placeholder, instead of failing later at `plan`. `fleet models` names a placeholder, and a non-boolean `enabled` or `placeholder` in config is an error that names the field.
- A new adapter field, `placeholder`, marks an adapter nobody has checked. The shipped `grok` and `muse` entries set it: doctor reports an enabled placeholder as `PLACEHOLDER` and fails (it used to pass as "installed (no login check defined)"), and `provider enable`, plan, launch and `steer provider` refuse it.
- Provider notes have a "Turning providers on and off" section: what ships on and off, the command and hand-edit forms, what turning one off does to a live run, and when an AI agent may change it (only when the owner asks, with the command, never to route around a failure or sign-in). The skill points to it.
- The placeholder notes say Grok and Muse models work today through the Cursor adapter, and list the fields to fill in to make a working adapter.
- README and `config.example.toml` show the command, turning off a shipped provider and the `defaults.prefer` step.
- Cursor and OpenCode no longer ship a starter `models_allow`: the lead may choose any model the account lists (270 in Cursor on the account it was checked with, including Gemini 3.8 Flash, Opus and Sonnet 5.5 and Fable 5.1). A user's own `models_allow` still narrows them.
- Without a `models_allow`, `fleet plan`, `steer model` and review launches check a model against the account's live list, so a mistyped ID is refused at plan time instead of failing in the worker's pane. This covers Codex and Antigravity too. Tier models are left to doctor, parameterized IDs pass unchecked, and a listing that fails only warns; a listing that times out is not retried within the same command.
- New adapter field `family_prefixes` (model ID prefix = family) names a model's family from its ID, so models a CLI adds later still take part in cross-family review. The longest prefix wins; an exact `model_families` or `models_allow` entry, or a tier that runs the model with its own `family`, beats it. Cursor ships prefixes for every vendor it lists (on the account it was checked with, only `auto` was left without one); OpenCode ships one per lab serving its own models and none for resellers or fine-tune houses.
- Claude's `models_allow` adds the `fable` alias and the full IDs `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-fable-5-1` and `claude-haiku-5-5`, so a plan can pin an exact version. The CLI cannot list models, so only a launch proves your plan offers one.
- The `agy` adapter follows agy 1.3.1 and later (checked on 1.4.8): they no longer offer Gemini 3.1 Pro, so the heavy and review tiers move to `gemini-3.8-flash-high` (on those versions, enabling agy failed `fleet doctor` before). `efforts` adds `xhigh`, and `family_prefixes` (`gemini-`, `claude-`, `gpt-oss-`) replaces the per-model `model_families`, so models an older or newer agy lists keep their family.

## 0.1.0-preview.22

Fleet is no longer tied to cmux: workers can run headless in tmux, and the owner can browse and open any worker.

- Terminal backends. `fleet init <run> --backend cmux|tmux` (or `backend` under `[defaults]`, or `backend=` in the request to the lead) picks where a run's workers live, and the run keeps it. The default, `auto`, uses cmux inside cmux or when a workspace is named, so every setup that worked before still uses cmux; elsewhere it runs workers headless in tmux. Runs created before this change stay on cmux.
- The tmux backend runs each worker in its own window of a detached session per run (`fleet-<checkout>-<run>-<hash>`) on Fleet's own tmux server (`tmux -L fleet`; `tmux_socket = ""` uses your default server). It works on macOS and Linux, over SSH and in containers, with no pane limit. A worker's window stays after it exits so its last screen stays readable; `stop --close` removes it. Window IDs are checked against the server that created them, so a restarted tmux server never has Fleet type into someone else's window; its lost workers show as `UNREACHABLE` and are recovered like a closed cmux pane.
- Workers no longer inherit the lead's session variables (`CLAUDECODE`, `CLAUDE_CODE_SESSION_ID`, `CODEX_THREAD_ID`, cmux and tmux markers) under tmux, so a Claude Code worker starts even when the lead is Claude Code.
- `fleet agents [<run>]` is the owner's worker browser, like Claude Code's agent list: arrow keys pick a worker and show its screen live, enter opens it full size, `a` attaches to it. `--list` prints the rows once. It reads run state only, so it can stay open beside the lead.
- `fleet attach <run> <id> [--read-only]` opens one headless worker's terminal (under cmux it names the worker's tab).
- `fleet doctor` reports cmux and tmux separately and fails only when your configured backend (under `auto`, both) is unavailable. `status` and `resume` name a tmux run's session instead of a workspace, and `resume --json` includes `backend`.
- CI installs tmux so the headless backend's live tests run on Linux and macOS.

## 0.1.0-preview.21

Workers in a shared checkout handle other workers' edits and the lead's local-server checks.

- The worker preamble says a failure in a file outside the worker's scope is probably another worker's edit in progress: do not fix it, re-run once after a short wait, and report it if it persists.
- The worker preamble says the ticket's checks run as given, including checks against a local server the owner already runs, so the rule against network calls no longer reads as forbidding them.

## 0.1.0-preview.20

Hand the lead work mid-conversation, without a ticket folder, and run it beside what is already going.

- `fleet add <run> --file <tasks.json|->` appends tickets to a run before or after its first launch. Saved tickets never change; a new ticket that touches an unverified ticket's files must list it as a blocker. `fleet resume` marks added tickets `+`.
- A ticket may give `task` (inline text) instead of `ticket`; Fleet writes it to `.fleet/runs/<run>/tasks/<id>.md`.
- `kind: "investigate"` tickets are read-only workers for research, investigation and debugging: no files, a read-only prompt, no cross-family review, verified on their report.
- `fleet launch <run> --ready` launches every ticket that can start now in one call, heaviest first, printing one `LAUNCHED` or `SKIPPED <id>: <reason>` line each and no worker screens.
- `fleet wait --settle <seconds>` (default 5) gathers events that land together into one wake.
- The skill's new delegation reference covers picking the run, one-line confirmations, desk runs and closing them.
- `fleet drop <run> <id> --reason <text>` retires a pending ticket nobody will finish: it frees its files, stops keeping its run open and no longer shows as `PENDING`.
- `fleet launch --ready` names the pane limit in its skip line, lists the tickets it did not attempt when a launch fails mid-batch, and refuses `--settle` (it prints no screens).
- `fleet wait --settle` reads worker screens once after settling instead of every second, and prints a report rewritten while settling once, with its latest status.
- `fleet verify` no longer tells you to update a source ticket for an inline task.
- `auto_trust = true` under `[defaults]` (off by default) has launch mark the repo trusted in Claude Code (the account's `.claude.json`), Codex (a session-only `-c` override) and Antigravity (`trustedWorkspaces`), so a worker no longer waits at the CLI's "trust this folder?" screen. A new adapter field, `trust`, names the method; a file launch cannot update only warns.
- Codex workers launch with `-c check_for_update_on_startup=false`, so Codex's "Update available" screen no longer stalls a worker before its prompt runs. Update Codex yourself.
- The skill's slash menu shows an `argument-hint`, the fallback launch line runs `bin/fleet` through `python3` so a dropped executable bit cannot block it, the recovery step says pane everywhere, and the worked examples open with a contents list.

## 0.1.0-preview.19

Feature docs stay in step with a run.

- `fleet verify` records `verified_at` on the accepted worker, so feature docs can date a delivered ticket from the run instead of a commit that does not exist yet. `fleet resume <run> --json` shows it.
- Before the closing report, a lead with `feature-docs` installed runs its `status` for a feature that already has `docs/<feature>/feature-docs/` and reports the `DRIFT`, `STALE` and `MISSING` lines, so a sync is not forgotten. The skill now says the feature is the `.scratch/<feature>/` folder the tickets live in, not the run name, and names the run's reports and evidence files as sources for the docs.
- The provider notes explain Codex's `approvals_reviewer = "auto_review"`: an owner-level launch override that sends a worker's approval prompts to Codex's own reviewer instead of leaving them in the pane. It is unrelated to Fleet's cross-family review.

## 0.1.0-preview.18

The lead reads less and spends fewer turns on a run. Nothing it needs is removed.

- Worker and reviewer reports are summary-first. The report format gains an `## Appendix` for supporting detail; everything a verifier needs, including every finding and blocker in full, stays above it. `fleet wait` ends a `REPORT` line with the lines to read, for example `(read lines 1-34 of 180; appendix below)`, and the lead opens the appendix only to check a line it doubts. Reports written by older prompts have no appendix and are read whole, as before.
- `fleet launch` ends by printing the last lines of the worker's screen 5 seconds in, so a login, trust, model or permission prompt shows without a `fleet peek`. The pause comes after launch releases its lock. `--settle <seconds>` changes it (at most 30); `--settle 0` skips the screen.
- `fleet wait` reports a worker launched in the last 4 minutes as `STALLED` after 60 seconds of an unchanged screen instead of `--stall` (default 180), which catches a prompt at start-up. `--stall 0` still turns stall detection off. The skill no longer asks for two peeks after every launch.
- `SKILL.md` has a size budget of 19,000 bytes, checked by a test. Pane placement detail moved to the command reference to make room for the changes above.

## 0.1.0-preview.17

A finished run's report tells you what to type next.

- A closing report ends with a **Next step** block: the lead's reset suggestion (a fresh lead by default, compaction only when the next run needs discussion that isn't on disk) and a start prompt for the next run with placeholders for the owner's open decisions. It's built from what the lead already knows, with no extra checks.

## 0.1.0-preview.16

Antigravity and OpenCode are supported worker adapters. Both ship disabled; enable one in your config.

- New `opencode` adapter: launches the TUI with `--prompt` and quits with `/exit`. Model IDs are `provider/model`; the shipped tiers use OpenRouter models from Zhipu, Alibaba and DeepSeek, so a mixed fleet gains cross-family reviewers. `models_allow` ships a starter set with families. The TUI has no effort flag, so tiers carry none. Checked live with opencode 1.18.34.
- `agy` adapter checked live with agy 1.3.0. It now quits with `/exit`, the verified sequence (it shipped with `ctrl+c` twice), declares its efforts (`low`, `medium`, `high`, `max`) and gives every listed model a family through `model_families`, so its Claude and GPT-OSS models can't review a writer from the same family.
- Model lists can be bare `provider/model` lines, as `opencode models` prints them; before, doctor reported "listed no models" for such a CLI.
- Provider notes cover both CLIs, including what they ask before they act: agy asks to trust a new folder and to run each shell command; OpenCode's default agent allows every tool inside the workspace.

## 0.1.0-preview.15

Fleet now records the cmux UUIDs it was meant to address workers and workspaces by.

- `fleet launch` reads the new surface's UUID from cmux's reply. It used to look the surface up in the pane listing straight after creating it, but cmux lists a new surface about 0.2s later, so the UUID was almost never recorded and `send`, `peek`, `stop` and `wait` fell back to the ref that preview.11 meant to avoid. A cmux that does not print the UUID gets a short retry of the listing, and launch now says so when it still has only the ref.
- `fleet init` stores the workspace's UUID instead of the ref it was given. cmux renumbers refs like `workspace:2` in sidebar order when it restarts, so a saved ref could name another project's workspace; UUIDs survive the restart. `fleet resume` flags an older run that still holds a ref.
- `fleet stop --close` no longer fails when cmux already closed the worker's tab on exit: a surface that is gone counts as closed.

## 0.1.0-preview.14

Clearer wording around `wait`, `hold` and `stop`.

- Where the lead was told to "stop" (after a hold, a `HOLD` from `fleet wait`, or a handoff), the CLI and docs now say "end your turn", and the hold messages add "do not run `fleet stop`", so a lead can't read it as quitting workers. The handoff line now reads `Lead: end your turn here.`
- The command reference gains a "Wait, hold or stop" note: `wait` is the lead listening, `hold` blocks workers that haven't started, `stop` quits running ones.
- README: `fleet wait` moved out of the owner command table (it's a lead command); a line above the table explains the difference.

## 0.1.0-preview.13

Adds a command reference for owners and the lead.

- New [command reference](skill/references/commands.md): every `fleet` command with what it does and when to use it, a one-table overview, and a note on the similar names (`hold`/`release` pause launches, `resume` reloads a run, `launch --resume` continues a worker's session). SKILL.md points the lead to it, and the README gains a short Commands section with the commands owners use most.

## 0.1.0-preview.12

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
