"""Claude worker hooks (#34, #19): the per-worker settings file, the scope guard, wait's PERMISSION and IDLE events,
and scope_permissions. The hooks run as `fleet _hook` subprocesses fed the JSON Claude Code sends
(code.claude.com/docs/en/hooks); no live agent, cmux or network is used."""
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_runtime import RuntimeCase, fleet  # noqa: E402

FLEET = Path(__file__).resolve().parents[1] / "skill" / "bin" / "fleet"
FAKE_CLI = Path(__file__).resolve().parent / "fixtures" / "fake_cli.py"
SHIPPED = fleet.load_config(Path("/nonexistent/fleet-config.toml"))
HOOKED_LAUNCH = "{bin} --model {model} --effort {effort} {profile} {settings} {prompt}"


def pre_tool_use(tool: str, path: str, cwd: str, **extra) -> dict:
    """PreToolUse stdin as the hooks docs give it, for a file-editing tool."""
    key = "notebook_path" if tool == "NotebookEdit" else "file_path"
    tool_input = {key: path, **({"content": "new text\n"} if tool == "Write" else
                                {"old_string": "a", "new_string": "b", "replace_all": False} if tool == "Edit" else {}),
                  **extra}
    return {"session_id": "abc123", "prompt_id": "550e8400-e29b-41d4-a716-446655440000",
            "transcript_path": "/home/user/.claude/projects/x/transcript.jsonl", "cwd": cwd,
            "permission_mode": "acceptEdits", "hook_event_name": "PreToolUse", "tool_name": tool,
            "tool_input": tool_input, "tool_use_id": "toolu_01ABC123"}


def notification(kind: str, message: str) -> dict:
    return {"session_id": "abc123", "transcript_path": "/home/user/.claude/projects/x/transcript.jsonl",
            "cwd": "/repo", "hook_event_name": "Notification", "message": message,
            "title": "Permission needed" if kind == "permission_prompt" else "Claude Code", "notification_type": kind}


def stop(message: str) -> dict:
    return {"session_id": "abc123", "transcript_path": "/home/user/.claude/projects/x/transcript.jsonl",
            "cwd": "/repo", "permission_mode": "default", "hook_event_name": "Stop", "stop_hook_active": False,
            "last_assistant_message": message, "background_tasks": [], "session_crons": []}


class HookCase(RuntimeCase):
    """RuntimeCase with both test adapters launching through Claude-style hooks."""

    def setUp(self):
        super().setUp()
        for name in ("writer", "reviewer"):
            self.cfg["providers"][name].update(
                launch=HOOKED_LAUNCH, hooks="claude-settings",
                profiles={"worker": {"args": "", "level": "workspace-write"},
                          "review": {"args": "--permission-mode manual", "level": "read-only"}})

    def worker_rec(self, ticket="01"):
        return self.state()["workers"][ticket]

    def settings(self, ticket="01") -> dict:
        return json.loads(Path(self.worker_rec(ticket)["hooks"]["settings"]).read_text())

    def hook(self, event: str, payload, ticket="01", matcher_target: str = "") -> subprocess.CompletedProcess:
        """Run the settings file's command for an event exactly as Claude Code would: `sh -c`, JSON on stdin."""
        groups = self.settings(ticket)["hooks"][event]
        [group] = [g for g in groups if not matcher_target or matcher_target in g.get("matcher", "").split("|")]
        [handler] = group["hooks"]
        stdin = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.run(["/bin/sh", "-c", handler["command"]], input=stdin, text=True, capture_output=True,
                              timeout=30, cwd=self.repo)

    def guard(self, tool: str, path, ticket="01", cwd=None) -> subprocess.CompletedProcess:
        return self.hook("PreToolUse", pre_tool_use(tool, str(path), str(cwd or self.repo)), ticket, tool)

    def events(self, ticket="01") -> list[dict]:
        f = Path(self.worker_rec(ticket)["hooks"]["events"])
        return [json.loads(line) for line in f.read_text().splitlines()] if f.exists() else []

    def keystrokes(self) -> list:
        return [c for c in self.cmux_calls if c and c[0] in ("send", "send-key")]


class SettingsFile(HookCase):
    def test_launch_writes_the_settings_and_passes_them(self):
        self.setup_run()
        self.launch()
        w = self.worker_rec()
        d = self.repo / ".fleet/runs/demo"
        self.assertEqual(w["hooks"]["settings"], str(d / "settings/01.json"))
        self.assertIn(f"--settings {shlex.quote(str(d / 'settings/01.json'))} ", w["command"])
        self.assertNotIn("--permission-mode", w["command"])  # workers keep the account's mode by default
        settings = self.settings()
        self.assertIs(settings["disableAllHooks"], False)
        [pre] = settings["hooks"]["PreToolUse"]
        self.assertEqual(pre["matcher"].split("|"), ["Edit", "Write", "MultiEdit", "NotebookEdit"])
        [note] = settings["hooks"]["Notification"]
        self.assertEqual(note["matcher"], "permission_prompt|idle_prompt")
        self.assertNotIn("matcher", settings["hooks"]["Stop"][0])  # Stop takes no matcher
        for event, groups in settings["hooks"].items():
            for handler in groups[0]["hooks"]:
                with self.subTest(event):
                    self.assertEqual(handler["type"], "command")
                    self.assertEqual(handler["timeout"], fleet.HOOK_TIMEOUT_S)
                    self.assertNotIn("onFailure", handler)  # a failing hook lets the action go ahead
                    self.assertIn(" _hook ", handler["command"])
        self.assertNotIn("permissions", settings)
        scope = json.loads((d / "settings/01.scope.json").read_text())
        self.assertEqual((scope["files"], scope["token"], scope["report"]),
                         (["owned.txt"], w["token"], str(d / "reports/01.md")))

class Shipped(unittest.TestCase):
    """The shipped config, build_launch, doctor and the strict fake; no run."""

    def test_other_adapters_and_old_lines_are_unchanged(self):
        p = SHIPPED["providers"]
        self.assertEqual(fleet.signal_method(p["claude"]), "claude-settings")  # `signals`, once `hooks` (#48)
        self.assertEqual(fleet.signal_method(p["claude-co"]), "claude-settings")  # extends copies it
        for name in ("codex", "cursor", "agy", "opencode"):
            with self.subTest(name):
                self.assertFalse(fleet.hooks_on(p[name]))
                self.assertNotIn("--settings", fleet.build_launch(p[name], "m", "high", "go", settings="/x.json"))
        # Without a settings file the shipped Claude lines are the ones from before hooks.
        self.assertEqual(fleet.build_launch(p["claude"], "opus", "high", "go", profile="review"),
                         "env -u CLAUDE_CONFIG_DIR claude --model opus --effort high --permission-mode manual go")
        # With one, a reviewer keeps manual mode: the settings add hooks and allow rules, never a looser mode.
        self.assertEqual(fleet.build_launch(p["claude"], "opus", "high", "go", settings="/r/s.json", profile="review"),
                         "env -u CLAUDE_CONFIG_DIR claude --model opus --effort high --permission-mode manual "
                         "--settings /r/s.json go")
        # An owner's launch line without {settings} runs as written: no hooks, and reviewers stay in manual mode.
        old = dict(p["claude"], launch="{bin} {session} --model {model} --effort {effort} {profile} {prompt}")
        self.assertFalse(fleet.hooks_on(old))
        self.assertEqual(fleet.build_launch(old, "opus", "high", "go", settings="/r/s.json", profile="review"),
                         "env -u CLAUDE_CONFIG_DIR claude --model opus --effort high --permission-mode manual go")
        self.assertFalse(fleet.hooks_on(dict(p["claude"], hooks="")))
        self.assertFalse(fleet.hooks_on(p["claude"], fleet.HEADLESS))  # a headless run has its own line, no hooks

    def test_an_edit_rule_matches_exactly_one_literal_path(self):
        self.assertEqual(fleet.edit_rule("/r/a.py"), "Edit(//r/a.py)")
        self.assertEqual(fleet.edit_rule("/r/[x]*?.py"), r"Edit(//r/\[x\]\*\?.py)")
        self.assertEqual(fleet.edit_rule("/r/a\\b"), r"Edit(//r/a\\b)")
        self.assertEqual(fleet.edit_rule("/r/odd  "), r"Edit(//r/odd\ \ )")  # gitignore drops unescaped trailing spaces
        self.assertEqual(fleet.edit_rule("/r/Finance (2024)/a.md"), "Edit(//r/Finance (2024)/a.md)")

    def test_config_checks_the_new_keys(self):
        self.assertIs(SHIPPED["defaults"]["scope_permissions"], False)
        for toml, words in (('[defaults]\nscope_permissions = "yes"\n', "scope_permissions must be true or false"),
                            ('[providers.claude]\nhooks = "codex-hooks"\n', "claude.hooks must be one of")):
            with self.subTest(toml), tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as fh:
                fh.write(toml)
            try:
                with self.assertRaises(fleet.FleetError) as caught:
                    fleet.load_config(Path(fh.name))
                self.assertIn(words, str(caught.exception))
            finally:
                os.unlink(fh.name)

    def test_doctor_checks_the_settings_flag_against_help(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "claude"
            fake.write_text("#!/bin/sh\necho '--session-id --resume --model --effort --permission-mode'\n")
            fake.chmod(0o755)
            missing, _ = fleet.flag_drift(dict(SHIPPED["providers"]["claude"], bin=str(fake)))
        self.assertEqual(missing, ["--settings"])

    def test_the_strict_fake_takes_a_settings_file_and_runs_its_hooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt = root / "run" / "prompts" / "01.md"
            prompt.parent.mkdir(parents=True)
            (root / "run" / "reports").mkdir()
            prompt.write_text("# prompt\n")
            (root / "bin").mkdir()
            fake = root / "bin" / "claude"
            fake.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_CLI}" claude "$@"\n')
            fake.chmod(0o755)
            env = dict(os.environ, PATH=f"{root / 'bin'}:{os.environ['PATH']}", FAKE_CLI_LOG=str(root),
                       FAKE_CLI_EXPECT=str(root / "expect.json"))
            env.pop("CLAUDE_CONFIG_DIR", None)
            (root / "expect.json").write_text(json.dumps({"model": "opus", "effort": "high", "session": None,
                                                          "account_dir": None, "settings": True}))
            stops = root / "stops.log"
            good = root / "good.json"
            good.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
                {"type": "command", "command": f"cat >> {shlex.quote(str(stops))}"}]}]}}))
            bad = root / "bad.json"
            bad.write_text("{not json")
            line = fleet.worker_prompt_line(prompt, "01")
            p = SHIPPED["providers"]["claude"]
            for settings, code in ((str(good), 0), (str(bad), 2), (str(root / "missing.json"), 2), (None, 2)):
                with self.subTest(settings=settings):
                    command = fleet.build_launch(p, "opus", "high", line, settings=settings)
                    res = subprocess.run(command, shell=True, env=env, input="/exit\n", text=True,
                                         capture_output=True, timeout=30)
                    self.assertEqual(res.returncode, code, f"{command}\n{res.stdout}{res.stderr}")
            self.assertEqual(json.loads(stops.read_text())["hook_event_name"], "Stop")


class Guard(HookCase):
    def test_the_guard_allows_scope_and_report_and_refuses_everything_else(self):
        self.setup_run()
        self.launch()
        report = self.repo / ".fleet/runs/demo/reports/01.md"
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (self.repo / "sub").mkdir()
        (self.repo / "link").symlink_to(outside)
        (self.repo / "next-link.txt").symlink_to(self.repo / "other.txt")
        for tool, path, code in (
                ("Write", self.repo / "owned.txt", 0),
                ("Edit", self.repo / "owned.txt", 0),
                ("MultiEdit", self.repo / "owned.txt", 0),
                ("Write", report, 0),
                ("Edit", self.repo / "sub/../owned.txt", 0),               # `..` that stays on the file
                ("Write", self.repo / "other.txt", 2),
                ("Write", self.repo / "next.txt", 2),                      # another ticket's file
                ("Edit", self.repo / "sub/../../outside/x.txt", 2),        # `..` out of the repo
                ("Write", self.repo / "link/x.txt", 2),                    # a symlinked directory out of the repo
                ("Write", self.repo / "next-link.txt", 2),                 # a symlink to another file
                ("Write", self.repo / ".fleet/runs/demo/reports/02.md", 2),  # another worker's report
                ("NotebookEdit", self.repo / "nb.ipynb", 2)):
            with self.subTest(tool=tool, path=str(path)):
                res = self.guard(tool, path)
                self.assertEqual(res.returncode, code, res.stderr)
                if code == 2:
                    self.assertIn("Status: blocked", res.stderr)
                    self.assertIn("outside ticket 01's scope", res.stderr)
                    self.assertIn("owned.txt", res.stderr)
        # A relative notebook path resolves against Claude's cwd: in scope only where it names the ticket's file.
        self.assertEqual(self.guard("NotebookEdit", "owned.txt").returncode, 0)
        self.assertEqual(self.guard("NotebookEdit", "owned.txt", cwd=self.repo / "sub").returncode, 2)
        denied = [e for e in self.events() if e["event"] == "denied"]
        self.assertEqual(len(denied), 8)
        self.assertEqual(denied[0]["path"], str(self.repo / "other.txt"))
        self.assertTrue(all(e["token"] == self.worker_rec()["token"] for e in denied))
        self.assertFalse((self.repo / "other.txt").exists())

    def test_a_reviewer_may_write_only_its_report_and_accepts_that_edit(self):
        self.setup_run(review="cross-all")
        self.launch()
        (self.repo / "owned.txt").write_text("implemented change\n")
        self.report()
        self.stop_with_receipt()
        self.review_prompt()
        self.launch("review-01", "reviewer", "review")
        w = self.worker_rec("review-01")
        self.assertIn("--permission-mode manual --settings ", w["command"])  # the guard never loosens the mode
        self.assertNotIn("acceptEdits", w["command"])
        self.assertEqual(fleet.adapter_profile(self.cfg["providers"]["reviewer"], "review")["level"], "read-only")
        report = self.repo / ".fleet/runs/demo/reports/review-01.md"
        # Its one file needs no answer; any other edit asks in manual mode even if the guard cannot run.
        self.assertEqual(self.settings("review-01")["permissions"], {"allow": [f"Edit(/{report.resolve()})"]})
        self.assertEqual(self.guard("Write", report, "review-01").returncode, 0)
        res = self.guard("Edit", self.repo / "owned.txt", "review-01")
        self.assertEqual(res.returncode, 2)
        self.assertIn("a reviewer or investigator writes only its report", res.stderr)

    def test_an_investigator_writes_only_its_report(self):
        self.setup_run(tickets=[{"id": "look", "files": [], "kind": "investigate"}])
        self.launch("look")
        self.assertIn("--permission-mode manual", self.worker_rec("look")["command"])
        report = self.repo / ".fleet/runs/demo/reports/look.md"
        self.assertEqual(self.settings("look")["permissions"], {"allow": [f"Edit(/{report.resolve()})"]})
        self.assertEqual(self.guard("Write", report, "look").returncode, 0)
        self.assertEqual(self.guard("Write", self.repo / "owned.txt", "look").returncode, 2)

    def test_a_grant_widens_the_running_guard(self):
        self.setup_run()
        self.launch()
        self.assertEqual(self.guard("Write", self.repo / "extra.txt").returncode, 2)
        self.call("grant", "demo", "01", "extra.txt", "--reason", "the helper lives there")
        self.assertEqual(self.guard("Write", self.repo / "extra.txt").returncode, 0)

    def test_a_broken_hook_fails_safe(self):
        self.setup_run()
        self.launch()
        hooks, token = self.worker_rec()["hooks"], self.worker_rec()["token"]
        res = self.hook("PreToolUse", "{not json", matcher_target="Write")
        self.assertEqual((res.returncode, "allowed" in res.stderr), (0, True))
        self.assertEqual(self.hook("PreToolUse", "[1, 2]", matcher_target="Write").returncode, 0)
        self.assertEqual(self.hook("PreToolUse", {"tool_name": "Write", "tool_input": {}},
                                   matcher_target="Write").returncode, 0)
        self.assertEqual(self.hook("Notification", "").returncode, 0)
        Path(hooks["scope"]).unlink()  # a missing scope file: cannot decide, so it allows and logs
        self.assertEqual(self.guard("Write", self.repo / "other.txt").returncode, 0)
        errors = [e for e in self.events() if e["event"] == "hook-error"]
        self.assertEqual(len(errors), 4)
        self.assertIn("FileNotFoundError", errors[-1]["error"])
        self.assertTrue(all(e["token"] == token for e in errors))
        # Wrong arguments, or an events file it cannot write, still never block.
        for args in ([], ["pre-tool-use", hooks["scope"], "/nonexistent/dir/events.jsonl", token]):
            res = subprocess.run([sys.executable, str(FLEET), "_hook", *args], text=True, capture_output=True,
                                 input=json.dumps(pre_tool_use("Write", str(self.repo / "other.txt"), str(self.repo))),
                                 timeout=30)
            self.assertEqual(res.returncode, 0, res.stderr)

    def test_the_hook_is_quick_and_never_takes_the_run_lock(self):
        self.setup_run()
        self.launch()
        with fleet.try_lock(self.repo) as held:  # the lead holds the lock: the hook must not wait on it
            self.assertTrue(held)
            started = time.monotonic()
            res = self.guard("Write", self.repo / "other.txt")
            elapsed = time.monotonic() - started
        self.assertEqual(res.returncode, 2)
        self.assertLess(elapsed, fleet.HOOK_TIMEOUT_S / 2)


class WaitEvents(HookCase):
    def wait(self, expected=0, *extra):
        return self.call("wait", "demo", "--timeout", "0", "--settle", "0", *extra, expected=expected)

    def test_a_permission_prompt_wakes_the_lead_once_with_no_keystrokes(self):
        self.setup_run()
        self.launch()
        sent = len(self.keystrokes())
        self.assertEqual(self.hook("Notification", notification("permission_prompt",
                                                                "Claude needs your permission to use Bash")).returncode, 0)
        out = self.wait()
        self.assertIn("PERMISSION 01: Claude needs your permission to use Bash; the owner answers it in the "
                      "worker's pane (fleet agents); the lead never does", out)
        self.assertIn("TIMEOUT", self.wait(2))  # each event wakes once
        self.assertEqual(len(self.keystrokes()), sent)

    def test_idle_without_a_hand_back_wakes_with_its_last_words(self):
        self.setup_run()
        self.launch()
        self.hook("Stop", stop("Should I also update\nthe docs?"))
        self.assertIn("TIMEOUT", self.wait(2))  # a turn that ended is not a wake on its own
        self.hook("Notification", notification("idle_prompt", "Claude is waiting for your input"))
        out = self.wait()
        self.assertIn('IDLE 01: Claude reports it idle with no hand-back; its turn ended with "Should I also '
                      'update the docs?"', out)
        self.assertIn("fleet peek demo 01", out)
        self.assertIn("TIMEOUT", self.wait(2))
        self.assertEqual([e["event"] for e in self.events()], ["turn-ended", "idle"])
        self.assertEqual(self.keystrokes(), [])

    def test_idle_after_a_hand_back_is_quiet(self):
        self.setup_run()
        self.launch()
        self.report()
        self.hook("Stop", stop("Report written."))
        self.hook("Notification", notification("idle_prompt", "Claude is waiting for your input"))
        out = self.wait()
        self.assertIn("REPORT 01 [needs-verification]", out)
        self.assertNotIn("IDLE", out)
        self.assertIn("TIMEOUT", self.wait(2))

    def test_an_idle_from_before_send_changes_wakes_nothing(self):
        """Final review, 1: Claude's idle lands about a minute after the hand-back. When the lead parks the report
        with send --changes after it, that idle belongs to the answered turn and wakes no IDLE while the worker
        works; an idle after the message still wakes."""
        self.setup_run()
        self.launch()
        self.report()
        self.hook("Stop", stop("Report written."))
        self.assertIn("REPORT 01 [needs-verification]", self.wait())
        self.hook("Notification", notification("idle_prompt", "Claude is waiting for your input"))
        self.call("send", "demo", "01", "--changes", "Rename the helper too.")
        self.assertIn("TIMEOUT", self.wait(2))
        self.hook("Stop", stop("Which helper?"))
        self.hook("Notification", notification("idle_prompt", "Claude is waiting for your input"))
        self.assertIn('IDLE 01: Claude reports it idle with no hand-back; its turn ended with "Which helper?"',
                      self.wait())

    def test_an_idle_from_before_an_answer_to_a_blocked_report_wakes_nothing(self):
        self.setup_run()
        self.launch()
        (self.repo / ".fleet/runs/demo/reports/01.md").write_text("# Worker report\nStatus: blocked\n\nWhich file?\n")
        self.hook("Stop", stop("Which file?"))
        self.assertIn("REPORT 01 [blocked]", self.wait())
        self.hook("Notification", notification("idle_prompt", "Claude is waiting for your input"))
        self.call("send", "demo", "01", "Use api.txt.")
        out = self.wait(2)
        self.assertNotIn("IDLE", out)
        self.assertNotIn("PENDING 01", out)  # answered (final review, 10)

    def test_other_notifications_and_other_launches_are_ignored(self):
        self.setup_run()
        self.launch()
        self.hook("Notification", notification("auth_success", "Signed in"))
        self.assertEqual(self.events(), [])  # the matcher would not send it; the hook ignores it anyway
        stale = Path(self.worker_rec()["hooks"]["events"])
        with stale.open("a") as fh:  # an earlier launch's event, and a line a hook is still writing
            fh.write(json.dumps({"event": "permission-prompt", "token": "old", "message": "x", "at": 0}) + "\n")
            fh.write('{"event": "permission-prompt", "tok')
        self.assertIn("TIMEOUT", self.wait(2))

    def test_a_reported_prompt_does_not_also_wake_as_stalled(self):
        self.setup_run()
        self.launch()
        seen = self.repo / ".fleet/runs/demo/.seen"
        self.assertEqual(fleet.scan_workers(self.repo, self.state(), seen, 60), [])  # the screen clock starts
        marker = seen / "01.screen"
        self.hook("Notification", notification("permission_prompt", "Claude needs your permission to use Bash"))
        [line] = fleet.scan_workers(self.repo, self.state(), seen, 60)
        self.assertTrue(line.startswith("PERMISSION 01"))
        key, since, flagged = marker.read_text().split("|")
        marker.write_text(f"{key}|{float(since) - 61}|{flagged}")
        self.assertEqual(fleet.scan_workers(self.repo, self.state(), seen, 60), [])  # already told: no STALLED
        events = seen / "01.events"  # as if the line was shown before the screen last changed, which then stuck
        token, done, told, words = events.read_text().split("|", 3)
        events.write_text(f"{token}|{done}|{float(since) - 62}|{words}")
        marker.write_text(f"{key}|{float(since) - 61}|0")
        [stalled] = fleet.scan_workers(self.repo, self.state(), seen, 60)
        self.assertIn("STALLED 01 screen unchanged for", stalled)
        self.assertIn("likely at an approval prompt the owner answers in its pane", stalled)

    def test_an_old_run_without_hooks_still_waits_on_its_screen(self):
        self.cfg["providers"]["writer"].pop("hooks")
        self.setup_run()
        self.launch()
        self.assertNotIn("hooks", self.worker_rec())
        self.assertNotIn("--settings", self.worker_rec()["command"])
        seen = self.repo / ".fleet/runs/demo/.seen"
        self.assertEqual(fleet.scan_workers(self.repo, self.state(), seen, 60), [])
        marker = seen / "01.screen"
        key, since, flagged = marker.read_text().split("|")
        marker.write_text(f"{key}|{float(since) - 61}|{flagged}")
        [stalled] = fleet.scan_workers(self.repo, self.state(), seen, 60)
        self.assertIn("likely at an approval prompt", stalled)


class ScopePermissions(HookCase):
    TICKETS = [{"id": "01", "files": ["owned.txt"],
                "checks": [{"run": "python3 -m unittest discover -s tests"}, {"run": "ls *.txt"},
                           "Manual review of the copy"]}]

    def test_off_by_default_workers_keep_their_mode_and_get_no_rules(self):
        self.setup_run(tickets=[dict(t) for t in self.TICKETS])
        self.launch()
        self.assertNotIn("--permission-mode", self.worker_rec()["command"])
        self.assertNotIn("permissions", self.settings())

    def test_on_a_worker_may_edit_its_own_files_and_run_only_its_planned_checks_unasked(self):
        self.cfg["defaults"]["scope_permissions"] = True
        self.setup_run(tickets=[dict(t) for t in self.TICKETS])
        self.launch()
        command = self.worker_rec()["command"]
        self.assertNotIn("--permission-mode", command)  # the account's own mode; only the rules below are added
        report = (self.repo / ".fleet/runs/demo/reports/01.md").resolve()
        self.assertEqual(self.settings()["permissions"],
                         {"allow": [f"Edit(/{self.repo.resolve()}/owned.txt)", f"Edit(/{report})",
                                    fleet.note_rule("demo", "01"),  # its own fleet note (#45)
                                    "Bash(python3 -m unittest discover -s tests)"]})  # `*` would be a wildcard
        self.assertEqual(self.guard("Write", self.repo / "owned.txt").returncode, 0)
        self.assertEqual(self.guard("Write", self.repo / "other.txt").returncode, 2)

    def test_a_grant_extends_a_scoped_workers_rules(self):
        self.cfg["defaults"]["scope_permissions"] = True
        self.setup_run(tickets=[dict(t) for t in self.TICKETS])
        self.launch()
        self.call("grant", "demo", "01", "extra.txt", "--reason", "the helper lives there")
        allow = self.settings()["permissions"]["allow"]
        root = self.repo.resolve()
        self.assertEqual(allow[:2], [f"Edit(/{root}/extra.txt)", f"Edit(/{root}/owned.txt)"])
        self.assertEqual(allow[-1], "Bash(python3 -m unittest discover -s tests)")
        self.assertEqual(self.guard("Write", self.repo / "extra.txt").returncode, 0)

    def test_without_scope_permissions_a_grant_adds_no_rules(self):
        self.setup_run()
        self.launch()
        self.call("grant", "demo", "01", "extra.txt", "--reason", "the helper lives there")
        self.assertNotIn("permissions", self.settings())


if __name__ == "__main__":
    unittest.main()
