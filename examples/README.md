# A small first Fleet run

This example creates three local documentation files. It needs no package installs, network requests, secrets, servers or builds. Workers still consume whichever provider account you choose; inspect the prompts before launching. Run it in a disposable Git repository or an agreed documentation area.

The upstream path is optional: discuss a feature, use `grill-with-docs` or `grill-me`, save the agreed design with `to-spec`, then create local tickets with `to-tickets`. The three [sample tickets](tickets/) show the minimum inputs Fleet needs: ID, scope intent, blockers, acceptance criteria and status. Fleet's lead discovers and records exact implementation paths separately.

For this example the agreed spec is: “Explain Fleet's lead/worker relationship in a short overview and glossary, then write a guide linking both. Use only Markdown and relative links.” The testing decision is manual content/link inspection plus the exact local checks below; there is no behavior change requiring a unit test.

## Prepare and plan

Copy `tickets/`, `plan.json` and `rules.md` into your chosen repository's `examples/`, `examples/plan.json` and `.fleet/rules.md` locations respectively, preserving any existing rules by merging deliberately. The plan's ticket paths assume `examples/tickets/`. Do not overwrite project instructions. Choose available models in the plan before launch; the checked-in example uses Codex light-tier defaults only and explicitly turns cross-family review off for these prose tickets.

From the repository in the intended cmux workspace:

```sh
fleet doctor
fleet init docs-demo --routing single:codex --review off
fleet plan docs-demo --file examples/plan.json
fleet prompt docs-demo overview
fleet prompt docs-demo glossary
```

Inspect both prompts, fill the LEAD context/check fields and remove the LEAD instruction comment. Ticket scopes are disjoint: `docs/fleet-demo/overview.md` and `docs/fleet-demo/glossary.md`. Use each ticket's exact check from `.fleet/rules.md`. Then launch up to your configured capacity:

```sh
fleet launch docs-demo overview codex --tier light
fleet launch docs-demo glossary codex --tier light
fleet wait docs-demo
```

With capacity one, launch only the first; stop and verify it before launching the second. If this shell cannot find `fleet`, use the absolute installed skill's `bin/fleet` path throughout.

## Verify and continue

For each completed writer, read its report, inspect its file and run its exact check yourself. Stop the worker and capture its scoped diff:

```sh
fleet stop docs-demo overview
fleet diff docs-demo overview --output .fleet/runs/docs-demo/overview.diff
```

Write a real evidence file describing the acceptance criteria, commands and actual results—for example `.fleet/runs/docs-demo/overview-evidence.md`—then accept it:

```sh
fleet verify docs-demo overview --evidence .fleet/runs/docs-demo/overview-evidence.md
```

Repeat for `glossary`. Only when both are verified can the dependent guide start:

```sh
fleet prompt docs-demo guide
# Inspect/fill context and checks before launching.
fleet launch docs-demo guide codex --tier light
```

Inspect, check, stop and verify `guide` the same way. Run the combined check from `.fleet/rules.md`, then `fleet check docs-demo`. Review the final documentation and commit only if you choose. For interruption, start with `fleet resume docs-demo`; do not initialize another run simply to recover the same work.

To try cross-family review, use a fresh run with an available permitted reviewer family and `--review cross-all`. Follow the stop → diff → reviewer prompt/launch → reviewer stop/verify → writer verify sequence in [the skill](../skill/SKILL.md). `single:codex` cannot satisfy that cross-family policy.
