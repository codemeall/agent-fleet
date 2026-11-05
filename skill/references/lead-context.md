# Lead context

A lead's answers get worse as its context fills, long before the window is full. The owner decides when to reset you; your part is to show them your size and, when they ask, reach a safe point and save what you know. Everything a run needs is on disk (`run.json`, reports, evidence, `notes.md`), so a fresh or compacted lead loses nothing that was saved.

Never hand off, ask to be compacted or delay a wave because of your size alone. That is the owner's call.

## Your size

- `fleet context` prints your reading and the session it measured. When you report a wave's outcome to the owner, include it (for example "lead context: 312K").
- The reading comes from `CLAUDE_CODE_SESSION_ID` (Claude Code) or `CODEX_THREAD_ID` (Codex) and that session's log. Other hosts, such as Cursor, are not measured and `fleet context` says "unknown"; then leave the size out of your report.
- If the owner has set zones (`[lead_context.<host>]` in their config), run commands may add a line on stderr:
  - `LEAD 520K nearing the dumb zone (600K): …` Keep reads lean (failures-only check output, no reflexive peeks, no full diffs you have already reviewed).
  - `LEAD 610K dumb zone (from 600K): …` Tell the owner once, then keep working.

## When the owner asks

| Owner says | You run |
| --- | --- |
| "Prepare a handoff", "start a fresh lead", "hand off" | `fleet handoff <run> --note …` |
| "Prepare to be compacted", "I'm going to compact you" | `fleet handoff <run> --compact --note …` |

1. Finish the current step (for example a verification you started). Do not launch the next wave.
2. End any background `fleet wait`. `fleet handoff` refuses while one is running, because it would consume events the next lead needs.
3. Run `fleet handoff`, passing every decision or owner preference that is not in the saved plan as its own `--note`. Use `--no-notes` only when there is none. Reports still waiting on you, and workers that exited without handing back, are listed for the next lead; they do not block the handoff.
4. Relay the printed owner steps exactly, then end your turn: no `launch`, no `wait` until the owner has acted. Do not `fleet stop` anyone; workers keep running.

You cannot compact yourself. Only the owner can, or the host when it auto-compacts.

## When a run closes

No handoff is needed: nothing is running and the run is on disk. Their decisions travel in the start prompt, so nothing goes to `notes.md`. Your closing report (skill step 6) ends with a **Next step** block the owner can paste, built from what you already know (no extra checks):

1. Your reset suggestion, in one line next to your `fleet context` reading. Default to a fresh lead: nothing is running and the run is on disk, so `/clear` (Codex: `/new`; other hosts: a new session) loses nothing. Suggest compaction only when the next run depends on discussion that is not on disk and does not fit in the start prompt. When your reading is small, say continuing in this session is also fine. The owner decides.
2. A start prompt for the next run: `/fleet` (Codex: `$fleet`; plugin install: `/agent-fleet:fleet`), the next run's name, routing, the tickets with their path, your proposed order, a `<placeholder>` for each decision the owner still owes, and one line of context pointing at the files this run left (run directory, decision records, uncommitted changes) plus any constraint the next run must respect.

When no further run is proposed, the block holds only the reset suggestion and the owner's review/commit steps.

## After a compaction or a fresh start

Run commands print `LEAD compacted 230K -> 35K: run fleet resume <run> …` after a large drop. Before any other action, whether or not that line appeared:

1. `fleet resume <run>` and read `notes.md`; the latest `## Handoff` entry is where the previous lead stopped.
2. Trust the files over your summary when they disagree.
3. Handle everything in that entry's "Awaiting the next lead" list first: verify a report, answer a blocked worker with `fleet send`, or deal with a worker listed as exited (skill step 4, `EXITED`): `fleet wait` already woke the previous lead for those, so it lists them only as `PENDING` when it times out, which can be minutes later.
4. Then continue with `fleet wait` or the next launch.

A fresh lead starts at its host's baseline (system prompt, tools and skills; often 40–60K in Claude Code). If `fleet context` still shows the previous session's size right after `/clear` or `/new`, the reading is stale: tell the owner instead of reporting it as yours.
