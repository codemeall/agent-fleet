# Running the lead from each harness

The lead needs three things: the skill loaded, a shell that can reach the cmux socket, and a command allowance for `fleet` and `cmux`. Everything else is the same `fleet` CLI.

| | Claude Code (`claude`, `claude-co`) | Codex CLI | Cursor (`cursor-agent` or the IDE agent) |
|---|---|---|---|
| Invoke | `/fleet …` | `$fleet …` (or pick it in `/skills`) | `/fleet …` |
| Skill dir | `~/.claude/skills/fleet`, `~/.claude-co/skills/fleet` | `~/.codex/skills/fleet` | `~/.cursor/skills/fleet` |
| Reaching cmux | works | its sandbox blocks the socket. Start the lead with `-c sandbox_workspace_write.network_access=true`, or use a `[profiles.fleet]` entry with that setting (`codex -p fleet`) | works when `Shell(cmux)` and `Shell(fleet)` are allowed. If `fleet doctor` can't reach cmux, run with `--sandbox disabled` |
| Allowances | `Bash(fleet:*)`, `Bash(cmux send:*)`, `Bash(cmux send-key:*)`, `Bash(cmux read-screen:*)`, `Bash(cmux new-surface:*)`, `Bash(cmux rename-tab:*)`, `Bash(cmux notify:*)` | `-a on-request`, approving `fleet` once per session | add `Shell(fleet)` to `permissions.allow` in `~/.cursor/cli-config.json` |
| Watching | `fleet wait` in the foreground (fits the 10-minute tool limit) or in the background | `fleet wait --timeout 240`, raising the command timeout if Codex cuts it | `fleet wait --timeout 240` |

**Claude Code lead: leave auto mode first.** Its classifier blocks launching agents, even with allow rules in place. Plan mode is fine for step 2, but launching needs default or accept-edits mode.

**ChatGPT (web or desktop chat)** can't be a lead: it has no shell on this machine. Use Codex CLI with the ChatGPT login instead.

A worker can run on the same provider as the lead. Each tab is a separate process with its own session.
