# Worked examples

Command sequences for the cases the skill describes in prose. Each uses the run `settings`; IDs, providers and messages are illustrative. The rules behind each step are in the [skill](../SKILL.md); this page only shows the shape.

## Contents

- [1. Turn the owner's request into a run](#1-turn-the-owners-request-into-a-run)
- [2. Answer a blocked worker](#2-answer-a-blocked-worker)
- [3. Request changes while the worker is still running](#3-request-changes-while-the-worker-is-still-running)
- [4. Cross-family review, with a fix round](#4-cross-family-review-with-a-fix-round)
- [5. An evidence file](#5-an-evidence-file)
- [6. Repair or relaunch a stopped worker](#6-repair-or-relaunch-a-stopped-worker)
- [7. Recover a worker whose exit cannot be confirmed](#7-recover-a-worker-whose-exit-cannot-be-confirmed)
- [8. Change scope after launch](#8-change-scope-after-launch)
- [9. Finish the run](#9-finish-the-run)
- [10. Delegate mid-conversation](#10-delegate-mid-conversation)
- [Handing off the lead](#handing-off-the-lead)

## 1. Turn the owner's request into a run

| Owner writes | You run |
| --- | --- |
| `/fleet -- .scratch/settings/issues` | `fleet init settings` (routing and review from the owner's config) |
| `/fleet auto review=cross-all -- …` | `fleet init settings --routing auto --review cross-all` |
| `/fleet single:codex review=off -- …` | `fleet init settings --routing single:codex --review off` |
| `/fleet agents=codex,claude:sonnet -- …` | `fleet init settings --routing agents=codex,claude:sonnet` |
| `/fleet workspace=workspace:3 -- …` | `fleet init settings --workspace workspace:3` |
| "Resume the settings run" | `fleet resume settings` (init refuses an existing run) |

In `agents=`, a listed model (`claude:sonnet`) replaces that provider's tier models in the plan; see [routing](routing.md). Then save the plan (skill step 2) and write prompts.

## 2. Answer a blocked worker

```text
REPORT banner-copy [blocked]
```

Read `.fleet/runs/settings/reports/banner-copy.md`. If the answer is an authorized task decision (the spec or the owner settles it), send it and record it, because the plan is frozen after launch:

```sh
fleet send settings banner-copy "Use the short copy from the spec: 'Saved'. Keep the existing aria-live region."
```

Fleet saves the answer as the next message in `.fleet/runs/settings/inbox/banner-copy.md` and types a one-line pointer to it, so a relaunched worker still has it.

```markdown
<!-- appended to .fleet/runs/settings/notes.md -->
- banner-copy: banner text is "Saved" (spec section 3); aria-live region unchanged.
```

If it needs the owner (scope, a permission, a login), ask them first and send nothing. The worker rewrites its report to `needs-verification`, and the next `fleet wait` wakes you with `REPORT banner-copy [needs-verification]`.

## 3. Request changes while the worker is still running

1. Send a bounded fix list with `--changes`, and say how to hand back. The fix list can span lines; `--file -` reads a longer one from stdin. `--key` makes a retry safe:
   ```sh
   fleet send settings api-client --changes --key fix-1 "Changes requested:
   1) return null on 404 in fetchSettings (src/api.ts)
   2) add that case to src/api.test.ts
   Set your report to needs-verification when done."
   ```
   Send first sets the report to `Status: changes-requested` (it refuses unless the report says `needs-verification`). `fleet wait` ignores a parked report, and it is not listed as `PENDING`.
2. `fleet wait`. When the worker flips the report back, you get `REPORT api-client [needs-verification]` again.

## 4. Cross-family review, with a fix round

The writer `api-client` (codex, heavy) reported `needs-verification` and you checked it (skill step 5). One command stops it, saves its diff to the run's `evidence/` folder, writes the reviewer prompt and launches a reviewer from another family:

```sh
fleet review settings api-client
fleet wait settings --timeout 540            # REPORT review-api-client [needs-verification]
```

```text
api-client: exited (surface:7)
DIFF .fleet/runs/settings/evidence/api-client.diff (2 files)
REVIEW review-api-client claude:opus@high family=anthropic on surface:9
```

`review` chose the first provider that fits (see [`review`](commands.md#review)); `--provider claude` names one yourself. When none fits, it lists each candidate's reason and you tell the owner. Read the reviewer's report.

**No findings to fix:** write the evidence files (example 5), then accept. It stops the reviewer, verifies it, verifies the writer and lists the tickets this unblocked:

```sh
fleet accept settings api-client --evidence .fleet/runs/settings/evidence/api-client.md \
  --review-evidence .fleet/runs/settings/evidence/review-api-client.md
```

```text
review-api-client: exited (surface:9)
review-api-client: verified; update the source ticket only after implementation acceptance
api-client: verified; update the source ticket only after implementation acceptance
UNBLOCKED settings-ui
```

Add `--launch-ready` to launch them at once, and `--gate` to run the ticket's planned commands first (a failing check verifies nothing). Then update the source ticket.

**Findings to fix:** repair the writer within its assignment (example 6); when it reports `needs-verification` again, check it and run `fleet review settings api-client` again. That round's diff still runs from the writer's first launch, so it covers both rounds; until then, `accept` refuses the first review as stale. Then accept as above.

**The long form.** `review` and `accept` run these commands, which you can still use one at a time, for example to read the reviewer prompt before it launches:

```sh
fleet stop settings api-client
fleet diff settings api-client --output .fleet/runs/settings/evidence/api-client.diff
fleet prompt settings review-api-client --review-of api-client --diff .fleet/runs/settings/evidence/api-client.diff   # --force in a later round
fleet launch settings review-api-client claude --tier review
fleet wait settings --timeout 540
fleet stop settings review-api-client
fleet verify settings review-api-client --evidence .fleet/runs/settings/evidence/review-api-client.md
fleet verify settings api-client --evidence .fleet/runs/settings/evidence/api-client.md
```

Only after the writer is verified, update the source ticket and launch its dependents.

## 5. An evidence file

`fleet verify` and `fleet recover` only require a non-empty file; the content is your record, and verify appends it to the report. Good evidence states what you ran and saw, not what the worker claimed:

```markdown
# api-client: lead verification

Commands (repository root):
- `npm test -- src/api.test.ts`: 14 passed, 0 failed.
- `npm run typecheck`: exit 0.

Acceptance criteria:
- [x] fetchSettings returns null on 404: new test "returns null on 404" passes; read src/api.ts lines 40–52.
- [x] No change outside src/api.ts and src/api.test.ts: `fleet verify` found no file outside every ticket's scope.
- [ ] Retry on 503: not in this ticket; the worker noted it as out of scope.

Review: review-api-client (claude, anthropic) found 1 issue (missing 404 test), fixed in round 2; re-review found none.
```

For a reviewer, record which diff it reviewed and how each finding was resolved.

## 6. Repair or relaunch a stopped worker

Use this after `EXITED api-client`, or when a stopped worker needs fixes within its assignment. The provider, tier and model must match the plan.

1. `fleet status settings` confirms the exit, then read its report.
2. Tell the new process what already exists: append a section to `.fleet/runs/settings/prompts/api-client.md`:
   ```markdown
   ## Repair
   Your earlier edits to src/api.ts are in place; keep them. Remaining: the 404 case and its test (review finding 1).
   ```
3. `fleet launch settings api-client codex --tier heavy`. The previous report is kept as `reports/api-client.<hex>.previous`, and the diff baseline stays at the first launch.

If the worker stopped on a usage limit and its adapter can resume (Claude), relaunch after the limit resets and add `--resume`: the worker continues its own session, so step 2 is only needed for new instructions.

A change of scope is not a repair; grant the file, or use a follow-up run when grant refuses (example 8). To move future launches to another provider or model, use `fleet steer` (skill step 1, Steering routing).

## 7. Recover a worker whose exit cannot be confirmed

```text
api-client: quit sent but process exit unconfirmed; slot retained
```

1. `fleet peek settings api-client --lines 60` to see whether it is stuck on a prompt; answer or ask the owner.
2. If it must go: `fleet resume settings --json` shows the worker's `pid`. Confirm it is gone (for example `ps -p <pid>` prints no process), never just because the tab disappeared. After a reboot every earlier process is gone and the pid may have been reused: a process that still shows is the worker only if its command is the worker's.
3. Write what you checked to `.fleet/runs/settings/evidence/api-client-recovery.md`, then:
   ```sh
   fleet recover settings api-client --evidence .fleet/runs/settings/evidence/api-client-recovery.md
   ```

Recovery frees the slot only. Verify or relaunch the ticket as usual afterwards.

## 8. Change scope after launch

The plan of `settings` is frozen, but a ticket can still be granted files. `api-client` reports `blocked`: retry on 503 belongs in `src/retry.ts` and `src/retry.test.ts`, which no ticket owns.

1. Check that the files are this ticket's work; ask the owner first when they widen what the ticket set out to do. Then:
   ```sh
   fleet grant settings api-client src/retry.ts src/retry.test.ts --reason "retry on 503 lives in src/retry.ts (owner, 2026-10-06)"
   ```
   Fleet adds the files to the ticket and records `Granted src/retry.test.ts, src/retry.ts to api-client: … (<time>)` in the decisions. The worker is live, so the grant goes to its inbox and its pane gets the pointer, as with `fleet send`. A stopped worker reads it when relaunched; regenerate a written prompt with `fleet prompt settings api-client --force` before that launch, since its scope line is stale.
2. `fleet wait` as usual. The ticket's `fleet diff` now compares the granted files with their content when `api-client` first launched, so an edit the worker made before the grant goes to review too. A review captured before the grant goes stale once a granted file differs: capture a fresh diff and review.

The same command resolves a scope-check refusal when the stray file is the ticket's own work: grant it, then run `diff` and `verify` again. If the file was already changed when the ticket launched and has changed since, Fleet has no copy of its launch content and refuses; look at the file with the owner, and add `--accept-current` when its current content is the right starting point.

### When grant refuses: a follow-up run

Grant refuses a file that a live worker of another ticket holds, or that another unverified ticket owns when neither waits on the other. If waiting for that ticket to be verified is not an option, use a follow-up run (moving launches to another provider or model needs only `fleet steer`):

1. Stop every worker that could overlap: `fleet stop settings --all`. A new `fleet init` refuses ("one active run per checkout") until every worker in `settings` has a confirmed exit.
2. **Review the partial edits yourself first:** `git diff -- src/api.ts src/api.test.ts`. The follow-up run's launch baseline includes them, so its `fleet diff` and any cross-family reviewer will not see them.
3. Start the follow-up and carry everything forward:
   ```sh
   fleet init settings-2 --review cross-heavy
   ```
   ```json
   {"tickets": [{"id": "api-client", "ticket": ".scratch/settings/issues/api-client.md", "files": ["src/api.ts", "src/api.test.ts", "src/retry.ts", "src/retry.test.ts"],
                 "blockers": [], "provider": "codex", "tier": "heavy",
                 "context": "Follow-up of run settings. src/api.ts already has the 404 handling (lead-reviewed); add retry on 503 in src/retry.ts.",
                 "checks": "npm test -- src/api.test.ts src/retry.test.ts; exit 0"}],
    "decisions": ["Follow-up of settings: owner added retry on 503 to api-client (2026-10-06).", "banner-copy: banner text is \"Saved\"."]}
   ```
   ```sh
   fleet plan settings-2 --file plan.json
   ```
4. Continue in `settings-2` only. Tickets already verified in `settings` stay verified there; do not plan them again.

## 9. Finish the run

```sh
npm test && npm run typecheck && npm run build   # the combined-tree gates from .fleet/rules.md
fleet check settings
fleet stop settings --all --close                # --close only when every exit is confirmed
```

`fleet check` prints `OK: captured HEAD and index states match the run baseline`, or lists what changed; investigate with the owner. Then report to the owner: each ticket's outcome with its resolved provider, model and effort, the evidence files, the decisions, any remaining limits, and their review and commit steps.

When `feature-docs` is installed and `docs/settings/feature-docs/` exists, check it now, before that report:

```sh
bash <feature-docs-skill-path>/scripts/feature-docs.sh status . settings
```

Put its `DRIFT`, `STALE` and `MISSING` lines in the report as sync work for the owner; `PLANNED` is a reminder, not drift. The feature is `settings` because the tickets live in `.scratch/settings/`; a follow-up run `settings-2` documents the same feature.

If the owner asks for feature docs, follow the skill now: the workers have stopped, so nothing else is writing to the checkout. With no `docs/settings/feature-docs/` yet, write it from `.scratch/settings/` and the code. The run's decisions in `.fleet/runs/settings/notes.md` belong in its `decisions.md`; the reports and evidence files under `.fleet/runs/settings/` feed its Testing section, and `verified_at` in `fleet resume settings --json` dates each delivered ticket. Add the folder to the files you list for the owner's commit.

## 10. Delegate mid-conversation

Owner: "Find out why test_perf got slower, and fix the flaky test_retry." You propose (see [delegation](delegation.md)):

```text
fix-flaky → implement · codex · light · tests/test_x.py · checks: pytest -q tests/test_x.py
why-slow  → investigate · claude · standard · checks: pytest -q tests/test_perf.py --durations=5
```

After "yes", into the open run (here `settings`; with none open, today's `desk-YYYYMMDD`):

```sh
fleet add settings --file tasks.json
fleet launch settings --ready
fleet wait settings --timeout 540        # in the background; keep talking with the owner
```

`REPORT why-slow [needs-verification]` names `src/cache.py:88` rebuilding the index on every call:

```sh
fleet accept settings why-slow --evidence .fleet/runs/settings/evidence/why-slow.md   # stops it, then verifies; no combined-tree checks
```

Propose `cache-fix → implement · claude · standard · src/cache.py · checks: pytest -q tests/test_perf.py`; on "yes", `fleet add` it with the finding in `context`, then `fleet launch settings --ready`.

## Handing off the lead

See [lead context](lead-context.md): the owner asks, you run `fleet handoff`, relay the steps and end your turn.
