```markdown
# Report {{id}}

Status: needs-verification

## Work or review performed
- Implementation: each file changed and why, one line per file; review: captured diff inspected.

## Acceptance criteria
- [x] criterion: the test, command or location that shows it, in one line
- [ ] criterion: remaining gap or why it could not be checked

## Checks and evidence
- `command`: actual result in one line (exit code, counts); state when a check was not run

## Findings
- Severity, file/location, failure scenario and suggested correction; or no actionable findings with review limits.

## Decisions and blockers
- Decisions made, questions for the lead and any permission or scope blocker.

## Out-of-scope observations
- Relevant issues left untouched.

## Appendix
- Supporting detail only: longer reasoning, output excerpts, per-criterion notes.
```

Use one status only: `needs-verification` or `blocked`. The lead may request changes, but only `fleet verify` records acceptance. Do not copy the illustrative checkboxes as evidence.

The lead reads everything above `## Appendix` in every report and opens the appendix only to check a line it doubts. Above it, write what a verifier needs and nothing else: one line per item, no pasted command output, no diff excerpts (the lead has the diff) and no account of how you got there. Every finding, failed or unrun check, decision and blocker goes above it in full; never shorten one to save space. Supporting detail goes in the appendix; leave the section out when there is none.
