## Your ticket

Ticket: `{{ticket}}`

<!-- LEAD: fill these three before launch, then delete this comment. -->
**Files in scope:** <!-- the files this ticket is expected to touch; everything else belongs to other workers -->

**Context you can't find by looking:** <!-- decisions already made, owner calls, gotchas from earlier waves -->

**Checks to run:** <!-- exact type-check / test commands for this repo -->

## How to work

1. **Read** the ticket end to end, then the code it touches, the domain glossary (`CONTEXT.md`) and any ADRs in that area. Use the project's own terms.
2. **Test first**, one slice at a time: write a failing test for one acceptance criterion through the highest public seam, see it go red, write the smallest code that turns it green, then refactor. Repeat until every criterion has a passing test or a stated reason it can't have one (for example, visual-only).
3. **Match the code around you**: naming, comment density, patterns, existing helpers.
4. **Run the checks** listed above. Your work is done only when they pass, or when you have recorded exactly which failure is pre-existing and how you know.
5. **Review your own diff** against every acceptance criterion and every hard rule before writing the report.
