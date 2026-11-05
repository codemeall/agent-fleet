"""Headless reviewers and investigators (review_transport = "headless").

With an adapter's `headless` line, a reviewer or an investigate ticket runs as one non-interactive CLI call through
`fleet _headless`, inside its own tmux window or cmux pane: read-only by the line's own flags, its final message
saved as its report, its usage recorded in the ledger by reconcile. These tests use fake CLIs
(tests/fixtures/fake_headless.py) that accept only the shipped headless lines and print JSON shaped like the real
CLIs' output; they prove the lines' shape and Fleet's handling, not that a real CLI version or account accepts them.
"""
import copy
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from test_runtime import RuntimeCase, fleet  # noqa: E402
from fleet_harness import FIXTURES, HAS_TMUX, FleetCase  # noqa: E402

FAKE = FIXTURES / "fake_headless.py"
SHIPPED = fleet.load_config(Path("/nonexistent/fleet-config.toml"))["providers"]
CLAUDE_USAGE = {"input_tokens": 1200, "output_tokens": 450, "cache_read_input_tokens": 4000,
                "cache_creation_input_tokens": 300}
CODEX_USAGE = {"input_tokens": 5000, "cached_input_tokens": 3000, "output_tokens": 600, "reasoning_output_tokens": 100}
LINE = "Read /r/.fleet/runs/demo/prompts/review-01.md and follow it exactly. You are fleet worker review-01. x"


def install_fakes(bindir: Path) -> None:
    bindir.mkdir()
    for cli in ("claude", "codex"):
        path = bindir / cli
        path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" {cli} "$@"\n')
        path.chmod(0o755)


def config_with(text: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.toml"
        path.write_text(text)
        return fleet.load_config(path)


class ShippedHeadlessLines(unittest.TestCase):
    def test_claude_and_codex_ship_read_only_headless_lines(self):
        claude = shlex.split(fleet.build_launch(SHIPPED["claude"], "opus", "high", LINE, "0" * 8, profile=fleet.HEADLESS))
        self.assertEqual(claude, ["env", "-u", "CLAUDE_CONFIG_DIR", "claude", "-p", "--output-format", "json",
                                  "--model", "opus", "--effort", "high", "--permission-mode", "manual",
                                  "--permission-prompts", "none", LINE])  # no session: a one-shot run
        co = shlex.split(fleet.build_launch(SHIPPED["claude-co"], "opus", "high", LINE, profile=fleet.HEADLESS))
        self.assertEqual(co[:2], ["env", f"CLAUDE_CONFIG_DIR={Path('~/.claude-co').expanduser()}"])
        self.assertEqual(co[2:], claude[3:])
        codex = shlex.split(fleet.build_launch(SHIPPED["codex"], "gpt-6.1-sol", "high", LINE, profile=fleet.HEADLESS))
        self.assertEqual(codex, ["env", "-u", "CODEX_HOME", "codex", "exec", "--json", "-m", "gpt-6.1-sol",
                                 "-c", "model_reasoning_effort=high", "-s", "read-only", "-c", "approval_policy=never",
                                 LINE])

    def test_only_checked_adapters_have_a_headless_line(self):
        self.assertEqual({n for n, p in SHIPPED.items() if p.get("headless")}, {"claude", "claude-co", "codex"})

    def test_a_headless_run_is_read_only_and_passes_every_ceiling(self):
        cfg = {"defaults": dict(fleet.DEFAULTS, permission_ceiling="read-only", review_transport="headless")}
        for name in ("claude", "codex"):
            self.assertEqual(fleet.adapter_profile(SHIPPED[name], fleet.HEADLESS), {"args": "", "level": "read-only"})
            self.assertIsNone(fleet.ceiling_problem(cfg, name, SHIPPED[name], fleet.HEADLESS))
        self.assertIn("above permission_ceiling", fleet.ceiling_problem(cfg, "codex", SHIPPED["codex"], "review"))

    def test_transport_defaults_to_tui_and_needs_a_headless_line(self):
        tui = {"defaults": dict(fleet.DEFAULTS)}
        headless = {"defaults": dict(fleet.DEFAULTS, review_transport="headless")}
        self.assertEqual(fleet.DEFAULTS["review_transport"], "tui")
        for name in ("claude", "codex", "cursor", "agy", "opencode"):
            self.assertEqual(fleet.review_transport(tui, SHIPPED[name]), "tui")
        self.assertEqual(fleet.review_transport(headless, SHIPPED["claude"]), "headless")
        self.assertEqual(fleet.review_transport(headless, SHIPPED["cursor"]), "tui")  # no checked line: keeps its TUI
        self.assertEqual(fleet.review_transport(headless, dict(SHIPPED["codex"], review_transport="tui")), "tui")
        self.assertEqual(fleet.review_transport(tui, dict(SHIPPED["codex"], review_transport="headless")), "headless")

    def test_config_refuses_a_bad_transport(self):
        self.assertEqual(config_with('[defaults]\nreview_transport = "headless"\n')["defaults"]["review_transport"],
                         "headless")
        with self.assertRaisesRegex(fleet.FleetError, "defaults.review_transport must be tui or headless"):
            config_with('[defaults]\nreview_transport = "pipe"\n')
        with self.assertRaisesRegex(fleet.FleetError, "cursor.review_transport is \"headless\" but cursor has no headless"):
            config_with('[providers.cursor]\nreview_transport = "headless"\n')
        with self.assertRaisesRegex(fleet.FleetError, "codex.headless must be a launch line"):
            config_with('[providers.codex]\nheadless = 1\n')

    def test_doctor_checks_the_headless_line_against_the_clis_help(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli = Path(tmp) / "claude"
            cli.write_text("#!/bin/sh\necho 'Usage: claude -p --print --output-format --model --effort "
                           "--permission-mode --session-id --resume'\n")  # no --permission-prompts
            cli.chmod(0o755)
            p = dict(SHIPPED["claude"], bin=str(cli))
            headless = {"defaults": dict(fleet.DEFAULTS, review_transport="headless")}
            lines, ok = fleet.profile_report(headless, "claude", p, str(cli))
            text = "\n".join(lines)
            self.assertTrue(ok)
            self.assertIn("reviewers and investigators run headless, read-only", text)
            self.assertIn("WARNING: --permission-prompts (headless line) is not in", text)
            lines, _ = fleet.profile_report({"defaults": dict(fleet.DEFAULTS)}, "claude", p, str(cli))
            self.assertNotIn("headless", "\n".join(lines))  # a TUI transport: doctor's lines are as before

    def test_output_parsing_follows_each_cli(self):
        claude = fleet.headless_output(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                                                   "result": "Status: blocked", "usage": CLAUDE_USAGE,
                                                   "permission_denials": [{"tool_name": "Edit", "tool_use_id": "t",
                                                                           "tool_input": {"file_path": "a.txt"}}]}))
        self.assertEqual((claude["message"], claude["usage"]), ("Status: blocked", CLAUDE_USAGE))
        self.assertEqual(claude["denials"], ['Edit {"file_path": "a.txt"}'])
        events = [{"type": "thread.started", "thread_id": "t"},
                  {"type": "item.completed", "item": {"id": "1", "type": "agent_message", "text": "first"}},
                  {"type": "item.completed", "item": {"id": "2", "type": "command_execution", "command": "touch x",
                                                      "status": "declined"}},
                  {"type": "item.completed", "item": {"id": "3", "type": "agent_message", "text": "last"}},
                  {"type": "turn.completed", "usage": CODEX_USAGE}]
        codex = fleet.headless_output("\n".join(json.dumps(e) for e in events) + "\n")
        self.assertEqual(codex["message"], "last")
        self.assertEqual(codex["denials"], ["command touch x"])
        # Codex counts cached input inside input_tokens; Fleet's input is the uncached part, as Claude's is.
        self.assertEqual(fleet.usage_totals(codex["usage"]), {"input": 2000, "output": 600, "cache_read": 3000})
        self.assertEqual(fleet.headless_output("plain words\n")["message"], "plain words\n")
        self.assertIsNone(fleet.headless_output("")["message"])

    def test_the_report_is_the_final_message_or_a_blocked_one(self):
        parsed = {"message": "```markdown\n# Report r\n\nStatus: needs-verification\n\nok\n```", "usage": None,
                  "denials": [], "errors": []}
        self.assertEqual(fleet.headless_report("r", 0, parsed, []), "# Report r\n\nStatus: needs-verification\n\nok\n")
        wrong = fleet.headless_report("r", 0, dict(parsed, message="Status: verified\nall good"), [])
        self.assertEqual(fleet.parse_status(wrong), "blocked")  # only the lead verifies; a wake status or blocked
        self.assertIn("all good", wrong)
        none = fleet.headless_report("r", 1, dict(parsed, message=None, errors=["no auth"]), ["boom"])
        self.assertEqual(fleet.parse_status(none), "blocked")
        self.assertIn("the CLI exited 1 without a final message", none)
        self.assertIn("The CLI reported: no auth", none)
        self.assertIn("boom", none)


class HeadlessRuntime(RuntimeCase):
    """Command-level: fake cmux records the window's command, and the test runs it the way the pane would."""

    def setUp(self):
        super().setUp()
        tools = tempfile.TemporaryDirectory()
        self.addCleanup(tools.cleanup)
        self.tools = Path(tools.name).resolve()
        install_fakes(self.tools / "bin")
        self.log = self.tools / "log"
        self.log.mkdir()
        self.expect = self.tools / "expect.json"
        self.env = {k: v for k, v in os.environ.items() if not re.match(r"(CLAUDE|CODEX|FLEET)", k)}
        self.env.update(FAKE_HEADLESS_LOG=str(self.log), FAKE_HEADLESS_EXPECT=str(self.expect))
        self.expecting()

    def expecting(self, mode="report", cli_usage=None, **extra):
        self.expect.write_text(json.dumps({"by_cli": {
            "claude": {"model": "reviewer-model", "effort": "high", "mode": mode, "usage": cli_usage or CLAUDE_USAGE,
                       **extra},
            "codex": {"model": "writer-model", "effort": "high", "mode": mode, "usage": cli_usage or CODEX_USAGE,
                      **extra}}}))

    def use_headless(self, name="reviewer", cli="claude"):
        self.cfg["providers"][name].update(bin=str(self.tools / "bin" / cli), headless=SHIPPED[cli]["headless"],
                                           review_transport="headless")

    def quits(self):
        def quit_keys(workspace, surface, items):
            for i, w in self.state()["workers"].items():
                if surface in (w.get("surface_id"), w["surface"]):
                    self.receipt(i)
        return patch.object(fleet, "send_keys", side_effect=quit_keys)

    def reviewed(self):
        """A handed-back writer and `fleet review`, which launches the reviewer."""
        self.setup_run(review="cross-all")
        self.launch()
        (self.repo / "owned.txt").write_text("implemented change\n")
        self.report()
        with self.quits():
            return self.call("review", "demo", "01")

    def window_command(self) -> str:
        """The command the last new pane runs, as cmux was asked to start it."""
        call = [c for c in self.cmux_calls if c[0] in ("new-split", "new-surface")][-1]
        return call[call.index("--command") + 1]

    def run_window(self, wait=True):
        cmd = self.window_command()
        if not wait:
            return subprocess.Popen(cmd, shell=True, cwd=self.repo, env=self.env, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True)
        res = subprocess.run(cmd, shell=True, cwd=self.repo, env=self.env, capture_output=True, text=True, timeout=60)
        errors = self.log / "claude.errors", self.log / "codex.errors"
        self.assertFalse(any(e.exists() for e in errors), "".join(e.read_text() for e in errors if e.exists()))
        return res

    def calls(self, cli="claude"):
        f = self.log / f"{cli}.jsonl"
        return [json.loads(line) for line in f.read_text().splitlines()] if f.exists() else []

    def report_text(self, i="review-01"):
        return (self.repo / ".fleet/runs/demo/reports" / f"{i}.md").read_text()

    def wait(self):
        return self.call("wait", "demo", "--timeout", "5", "--interval", "1", "--settle", "0", "--stall", "0")

    def ledger(self, i="review-01"):
        return [e for e in json.loads(self.call("cost", "demo", "--json"))["ledger"] if e["worker"] == i][-1]

    def test_a_headless_reviewers_final_message_is_its_report_and_wait_wakes_on_it(self):
        self.use_headless()
        out = self.reviewed()
        self.assertIn("REVIEW review-01 reviewer:reviewer-model@high family=anthropic", out)
        reviewer = self.state()["workers"]["review-01"]
        self.assertEqual((reviewer["transport"], reviewer["state"]), ("headless", "running"))
        self.assertNotIn("session", reviewer)
        prompt = (self.repo / ".fleet/runs/demo/prompts/review-01.md").read_text()
        self.assertIn("**Fleet runs you headless:**", prompt)
        self.assertIn(f"Do not write `{self.repo}/.fleet/runs/demo/reports/review-01.md`", prompt)
        self.assertIn(" _headless ", self.window_command())
        res = self.run_window()
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("review-01 runs headless and read-only", res.stdout)
        [call] = self.calls()
        self.assertIn("--permission-prompts", call["argv"])
        self.assertFalse(call["stdin_tty"])
        report = self.report_text()
        self.assertTrue(report.startswith("# Report review-01\n\nStatus: needs-verification\n"))
        out = self.wait()
        self.assertRegex(out, r"REPORT review-01 \[needs-verification\] criteria 1/1, checks none, findings 0, "
                              r"questions 0, files none \S+/reports/review-01\.md")
        self.assertNotIn("EXITED", out)
        [raw] = (self.repo / ".fleet/runs/demo/usage").glob("review-01.*.out")  # the CLI's own output, kept
        self.assertEqual(json.loads(raw.read_text())["type"], "result")
        self.call("status", "demo")
        self.assertEqual(self.state()["workers"]["review-01"]["state"], "exited")
        entry = self.ledger()
        self.assertEqual(entry["tokens"], {"input": 1200, "output": 450, "cache_read": 4000, "cache_write": 300})
        self.assertEqual((entry["tokens_source"], entry["role"], entry["ticket"]), ("reported", "reviewer", "01"))
        self.assertIn("tokens in 1.2K out 450 cache-read 4.0K cache-write 300", self.call("cost", "demo"))
        with self.quits():
            self.assertIn("UNBLOCKED", self.call("accept", "demo", "01", "--evidence", str(self.evidence)))
        self.assertEqual(self.state()["tickets"]["01"]["status"], "verified")

    def test_a_tui_reviewer_is_launched_exactly_as_before(self):
        self.reviewed()
        reviewer = self.state()["workers"]["review-01"]
        self.assertNotIn("transport", reviewer)
        self.assertNotIn("usage_file", reviewer)
        self.assertIn(" _run ", self.window_command())
        prompt = (self.repo / ".fleet/runs/demo/prompts/review-01.md").read_text()
        self.assertNotIn("headless", prompt)
        self.assertNotIn("{{transport}}", prompt)
        self.assertIn("Only the lead can verify your report or accept the original ticket.\n", prompt)
        self.assertNotIn("transport", self.state()["prompts"]["review-01"])

    def test_an_investigator_runs_headless_on_codex_and_its_usage_is_recorded(self):
        self.use_headless("writer", "codex")
        self.setup_run(tickets=[{"id": "q", "kind": "investigate", "files": []}])
        self.assertIn("Fleet runs you headless", (self.repo / ".fleet/runs/demo/prompts/q.md").read_text())
        self.launch("q")
        self.assertEqual(self.run_window().returncode, 0)
        [call] = self.calls("codex")
        self.assertEqual(call["argv"][:2], ["exec", "--json"])
        self.assertIn("REPORT q [needs-verification]", self.wait())
        entry = self.ledger("q")
        self.assertEqual(entry["role"], "investigator")
        self.assertEqual(entry["tokens"], {"input": 2000, "output": 600, "cache_read": 3000})
        stop = self.call("stop", "demo", "q")  # a finished run has already exited: no keys are sent
        self.assertIn("q: exited", stop)
        self.assertFalse([c for c in self.cmux_calls if c[0] in ("send", "send-key")])

    def test_a_write_attempt_is_denied_and_reported(self):
        self.use_headless()
        self.expecting("write", write_path=str(self.repo / "owned.txt"))
        self.reviewed()
        self.run_window()
        self.assertEqual((self.repo / "owned.txt").read_text(), "implemented change\n")
        report = self.report_text()
        self.assertEqual(fleet.parse_status(report), "needs-verification")
        self.assertIn("## Denied by the read-only run", report)
        self.assertIn(f'- Write {{"file_path": "{self.repo}/owned.txt"}}', report)
        [usage] = (self.repo / ".fleet/runs/demo/usage").glob("review-01.*.json")
        self.assertEqual(len(json.loads(usage.read_text())["denials"]), 1)
        self.assertIn("findings 1 (1 low)", self.wait())

    def test_a_final_message_without_a_status_line_becomes_a_blocked_report(self):
        self.use_headless()
        self.expecting("no-status")
        self.reviewed()
        self.run_window()
        report = self.report_text()
        self.assertEqual(fleet.parse_status(report), "blocked")
        self.assertIn("its final message has no `Status: needs-verification` or `Status: blocked` line", report)
        self.assertIn("I looked at the diff and it seems fine.", report)
        out = self.wait()
        self.assertIn("REPORT review-01 [blocked]", out)
        self.assertIn("EXITED review-01 returncode=0", out)

    def test_a_cli_that_fails_leaves_a_blocked_report_and_unknown_tokens(self):
        self.use_headless("writer", "codex")
        self.expecting("fail")
        self.setup_run(tickets=[{"id": "q", "kind": "investigate", "files": []}])
        self.launch("q")
        self.assertEqual(self.run_window().returncode, 1)
        report = self.report_text("q")
        self.assertIn("the CLI exited 1 without a final message", report)
        self.assertIn("The CLI reported: not signed in", report)
        self.assertIn("Error: not signed in", report)  # its stderr tail
        self.assertIn("REPORT q [blocked]", self.wait())
        self.assertIsNone(self.ledger("q")["tokens"])

    def test_a_cli_that_reports_no_usage_stays_unknown(self):
        self.use_headless()
        self.expecting("no-usage")
        self.reviewed()
        self.run_window()
        self.assertIn("REPORT review-01 [needs-verification]", self.wait())
        self.assertIsNone(self.ledger()["tokens"])
        self.assertRegex(self.call("cost", "demo"), r"01 +2 workers, wall .*, tokens unknown")

    def test_launch_refuses_a_prompt_written_for_the_other_transport(self):
        self.use_headless()
        self.setup_run(review="cross-all")
        self.launch()
        (self.repo / "owned.txt").write_text("implemented change\n")
        self.hand_back()
        diff = self.review_prompt()  # no --provider: written for a TUI
        out = self.launch("review-01", "reviewer", "review", expected=1)
        self.assertIn("review-01's prompt was written for a tui run, but reviewer runs it headless", out)
        self.assertIn(f"fleet prompt demo review-01 --force --review-of 01 --diff <its diff> --provider reviewer", out)
        self.assertNotIn("review-01", self.state()["workers"])
        self.prompt("review-01", "--review-of", "01", "--diff", str(diff), "--force", "--provider", "reviewer")
        self.launch("review-01", "reviewer", "review")
        self.assertEqual(self.state()["workers"]["review-01"]["transport"], "headless")

    def test_a_headless_run_cannot_resume(self):
        self.use_headless()
        self.reviewed()
        self.run_window()
        self.call("status", "demo")
        out = self.call("launch", "demo", "review-01", "reviewer", "--tier", "review", "--resume", expected=1)
        self.assertIn("a one-shot run has no session to resume", out)

    def test_stop_interrupts_a_running_headless_run(self):
        self.use_headless()
        self.expecting("sleep")
        self.reviewed()
        proc = self.run_window(wait=False)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        exits = self.repo / ".fleet/runs/demo/exits"
        deadline = time.monotonic() + 20
        while not list(exits.glob("review-01.*.started.json")) and time.monotonic() < deadline:
            time.sleep(0.1)
        time.sleep(0.5)  # the CLI is up and waiting
        sent = []

        def ctrl_c(workspace, surface, items):  # the terminal delivers Ctrl-C to the runner in the foreground
            sent.append(items)
            proc.send_signal(signal.SIGINT)
        with patch.object(fleet, "send_keys", side_effect=ctrl_c):
            out = self.call("stop", "demo", "review-01", "--timeout", "15")
        self.assertEqual(sent, [["ctrl+c"]])
        self.assertIn("review-01: exited", out)
        proc.wait(timeout=10)
        self.assertIn("interrupted; stopping the CLI", proc.stdout.read())
        self.assertFalse((self.repo / ".fleet/runs/demo/reports/review-01.md").exists())
        [usage] = (self.repo / ".fleet/runs/demo/usage").glob("review-01.*.json")
        self.assertTrue(json.loads(usage.read_text())["interrupted"])

    def test_wait_never_calls_a_headless_run_stalled(self):
        self.use_headless()
        self.reviewed()
        state = self.state()
        state["workers"]["review-01"]["launched_at"] = time.time() - 3600
        seen = self.repo / ".fleet/runs/demo/.seen"
        seen.mkdir(exist_ok=True)
        for _ in range(2):
            self.assertEqual(fleet.scan_workers(self.repo, state, seen, 1), [])
            time.sleep(1.1)
        self.assertFalse((seen / "review-01.screen").exists())

    def test_send_saves_a_message_a_headless_run_reads_only_when_launched_again(self):
        self.use_headless()
        self.reviewed()
        out = self.call("send", "demo", "review-01", "look", "again")
        self.assertIn("runs headless and reads no input once started", out)
        self.assertIn("look again", (self.repo / ".fleet/runs/demo/inbox/review-01.md").read_text())
        self.assertFalse([c for c in self.cmux_calls if c[0] in ("send", "send-key")])


@unittest.skipUnless(HAS_TMUX, "tmux is not installed")
class HeadlessLive(FleetCase):
    """The shipped claude adapter with review_transport = "headless", through fleet in a real tmux window."""

    def setUp(self):
        super().setUp()
        install_fakes(self.root / "bin")
        self.log = self.root / "fake-log"
        self.log.mkdir()
        self.expect = self.root / "expect.json"
        self.env.update(PATH=f"{self.root / 'bin'}:{self.env['PATH']}", FAKE_HEADLESS_LOG=str(self.log),
                        FAKE_HEADLESS_EXPECT=str(self.expect))
        model, effort = fleet.resolve_model(SHIPPED["claude"], "standard", None, None)
        self.want = {"model": model, "effort": effort, "usage": CLAUDE_USAGE}
        self.write_config('review = "off"\nreview_transport = "headless"\nprefer = ["claude"]\n')
        self.fleet("init", "live")
        (self.repo / "q.md").write_text("# Why\nFind out why a.txt says base.\n")
        (self.repo / "plan.json").write_text(json.dumps({"tickets": [
            {"id": "q", "kind": "investigate", "ticket": "q.md", "files": [], "provider": "claude",
             "tier": "standard", "context": "Read a.txt.", "checks": [{"manual": "the lead reads the report"}]}]}))
        self.fleet("plan", "live", "--file", str(self.repo / "plan.json"))

    def diagnostics(self) -> str:
        logged = "".join(f.read_text() for f in sorted(self.log.glob("*.errors")))
        return super().diagnostics() + (f"\n--- the fake CLI refused:\n{logged}" if logged else "")

    def test_an_investigator_runs_headless_in_tmux_end_to_end(self):
        self.expect.write_text(json.dumps(self.want))
        launched = self.fleet("launch", "live", "--ready")
        self.assertRegex(launched, rf"LAUNCHED q claude:{re.escape(self.want['model'])}@{self.want['effort']} "
                                   r"standard @\d+")
        self.assertIn("q", self.fleet("agents", "live", "--list"))
        self.wait_for("live", r"^REPORT q \[needs-verification\] criteria 1/1, checks none, findings 0")
        self.assertRegex(self.fleet("status", "live"), r"q +claude:\S+ +@\d+ +exited +needs-verification")
        self.assertIn("tokens in 1.2K out 450 cache-read 4.0K cache-write 300", self.fleet("cost", "live"))
        self.assertIn("q: exited", self.fleet("stop", "live", "q"))  # it had already exited
        self.assertIn("q: closed", self.fleet("stop", "live", "q", "--close"))
        [call] = [json.loads(line) for line in (self.log / "claude.jsonl").read_text().splitlines()]
        self.assertEqual(call["argv"][:3], ["-p", "--output-format", "json"])

    def test_stop_ends_a_running_headless_run_in_tmux(self):
        self.expect.write_text(json.dumps(dict(self.want, mode="sleep")))
        self.fleet("launch", "live", "--ready")
        deadline = time.monotonic() + 20
        while not (self.log / "claude.jsonl").exists() and time.monotonic() < deadline:
            time.sleep(0.2)
        self.assertIn("q: exited", self.fleet("stop", "live", "q", "--timeout", "20"))
        self.assertFalse((self.repo / ".fleet/runs/live/reports/q.md").exists())
        self.assertIn("q: closed", self.fleet("stop", "live", "q", "--close"))


if __name__ == "__main__":
    unittest.main()
