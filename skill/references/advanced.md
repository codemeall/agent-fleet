# Advanced features

Open a section only when the owner asked for that feature. A two-worker wave does not need this page. The happy path (doctor, init, plan, `launch --ready`, wait, status, send, stop, review, accept) is [SKILL.md](../SKILL.md). Flags for each command are in [commands.md](commands.md).

Hard rules stay on the skill page and still apply here: do not answer permission prompts, do not commit, and do not exceed `permission_ceiling`.

## Gate

When tickets have planned `{"run":...}` checks and the owner asked for the gate, add `--gate` to `fleet wait <run>`. Fleet runs those checks on each hand-back, sends failures back to the worker, and wakes you only on a pass, `GATE-FAILED`, or a scope problem. `fleet gate <run> <id>` runs the same commands and the scope check, and writes `evidence/<id>.gate.md`. You still run prose checks yourself. Flags: [gate](commands.md#gate).

`GATE-FAILED <id> after N rounds` means the checks still fail after Fleet sent them back. Read the evidence, then repair with `fleet send --changes` or tell the owner.

## Supervise

When the owner's config or request names supervise steps, run `fleet supervise <run>` with the same `--timeout` as `wait` (it also refuses a value above 600 s), instead of `wait`. It also starts required reviews and launches unblocked tickets. It returns as soon as something needs you. With `--gate`, or under supervise when its gate step is on, a wait runs planned checks inline (up to 3600 s each, in sequence). With its dispatch or review step, supervise also launches tickets and starts reviews, which run worktree setup (up to 1800 s for each new tree) in a worktree run that sets worktree_setup. So either can run past its own `--timeout`: run it in the background, and run `fleet status <run>` before retrying one that was cut off. Flags: [supervise](commands.md#supervise).

Extra wake lines you will see only on this path:

- `READY <id>`: the gate passed and any required review is clean. Read the report and the diff, then accept.
- `FINDINGS <id>`: resolve the reviewer's findings with the writer. Supervise's repair step relaunches the writer for findings and wakes you only for a rebuttal or a dispute.

## Steering

When a provider is failing, rate-limited, or a poor fit, and the owner wants the run moved, `fleet steer provider|model|reset <run> ...` changes future launches inside the run's routing. Omit the route on the next launch, or pass the plan's provider and tier, so the steer can apply. A worker on a usage limit: stop it, then relaunch after the reset with `--resume`, or steer and relaunch fresh. When neither is possible, tell the owner. Detail: [steering](routing.md#steering).

## Fan-out

For a hard question the owner wants asked more than once, an `investigate` ticket's `"fanout":["claude","codex"]` runs one read-only investigator per family. Launch that ticket with `fleet launch <run> --ready` (or `fleet launch <run> <fanout-id>`), not as a normal implement ticket. When every investigator is back, `wait` prints `FANOUT <id>`. Read where they agree and differ, then verify, or `fleet judge <run> <id>` the differences (`VERDICT <id>` is the judge's call). Detail: [fan-out](routing.md#fan-out).

## Warm workers

A light or standard follow-up in the same area may set `"after":"<id>"` so the next ticket keeps that writer's session. Same provider, model and effort, on an adapter that can resume, in a shared checkout. Heavy work, a different area, and a session past the warm context limit start fresh. Detail: [warm workers](routing.md#warm-workers).

## Plan first

A heavy or uncertain ticket may set `"plan_first":true`. Its worker writes `Status: plan-ready` and waits. `wait` prints `PLAN <id>` with the approach quoted as data. Approve it with `fleet approve <run> <id>`, or ask for changes with `fleet send --changes`. `--review` sends the plan to another family first. Detail: [plan first](routing.md#plan-first).

## Worktrees and best of two

`--isolation worktree` on init, only when the owner asks, gives each implementation ticket its own worktree of this checkout (uncommitted edits too). Overlapping tickets may then run together, and workers may build. `accept` applies the work to the checkout. `CONFLICT <id>` means the checkout's copy moved: with the owner, drop and re-add the work or merge it by hand, then `fleet merge <run> <id> --resolved "<what you did>"`. Detail: [worktree isolation](commands.md#worktree-isolation).

In a worktree run, a heavy implement ticket's `fanout` runs two writers. Judge, then `fleet accept <run> <id> --pick <writer-id> --evidence <file>` keeps one. Detail: [best of two](commands.md#best-of-two).

## Import

Bring GitHub issues in with read-only `fleet import <owner/repo#N|URL> --dir <dir>`. Export other remote content to local files first. A URL is not a `--ticket` input. Preserve original IDs and source links, using safe slug IDs (`settings-copy`, not a path or URL). Detail: [import](commands.md#import).

No ticket folder, or new work mid-run: [delegating mid-conversation](delegation.md).

## Owner views

`fleet agents`, `fleet track`, `fleet attach`, the status line and the dashboard are the owner's. Do not take their focus, and do not answer a permission prompt from those views. The owner answers `PERMISSION` there. Detail: [owner views](commands.md#owner-views).

## Notes

- `NOTE <id>`: a worker's `@lead` note. `fleet board <run>` shows the rest.

`fleet hold` and `fleet release` are the owner's ask only. `PLAN`, `FANOUT`, `VERDICT`, `READY`, `FINDINGS` and `GATE-FAILED` are in the sections above.

On the owner's ask to hand off or compact, follow [lead context](lead-context.md). Include your `fleet context` reading and `fleet cost <run>` when you report a wave. A launch refused at a cap (`max_workers`, `max_wall_minutes`) goes to the owner. Never retry around it.
