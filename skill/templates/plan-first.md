## Plan first

This ticket is plan-first: the lead approves your approach before you change any file.

1. If `{{plan}}` exists, the lead has already approved your plan. Follow it, skip the rest of this section and do the ticket as above.
2. Otherwise, before you edit anything, read what you need and write your plan in `{{report}}` with `Status: plan-ready`. Cover the files you will change, the interfaces you will add or change, your test plan and the risks, one short line each. Its `fleet` block lists `"files_changed": []` and your open questions for the lead in `questions`; leave `checks`, `criteria` and `findings` empty until you hand back the work. Then stop and wait for the lead.
3. Until the lead approves, edit no file but your report. Fleet does not count the ticket's files as yours before approval: an edit made before it shows up as a change outside every ticket, and it blocks approval and acceptance.
4. The lead answers in your inbox, `{{inbox}}`, and types a line pointing to the message. A message that starts "Plan approved" means implement now: follow the plan as saved in `{{plan}}`, and any note in the message. Any other answer while your plan waits asks for changes: revise the plan as it says, write it again with `Status: plan-ready`, then wait again.
5. After approval, work as above and hand back with `Status: needs-verification`. A repair round or a relaunch after approval needs no new plan.
