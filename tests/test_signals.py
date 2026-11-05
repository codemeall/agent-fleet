"""Turn signals (#48): an adapter's `signals` field, Codex's `notify` program (`fleet _notify`) and wait's IDLE for a
Codex worker, the older `hooks` field, Cursor's fallback to the screen hash, and doctor's line. `fleet _notify` is fed
the JSON Codex sends (openai/codex codex-rs/hooks/src/legacy_notify.rs); no real CLI, account or network is used."""
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from fleet_harness import FIXTURES, HAS_TMUX, FleetCase  # noqa: E402
from test_runtime import RuntimeCase, fleet  # noqa: E402

FLEET = Path(__file__).resolve().parents[1] / "skill" / "bin" / "fleet"
FAKE_CLI = FIXTURES / "fake_cli.py"
SHIPPED = fleet.load_config(Path("/nonexistent/fleet-config.toml"))
NOTIFY_LAUNCH = "{bin} --model {model} --effort {effort} {signals} {prompt}"
ODD = "/tmp/a dir/it's \"odd\" \\ ü\tx"  # a space, both quotes, a backslash, non-ASCII and a control character


def turn_complete(message, **extra) -> str:
    """The notification Codex appends as the program's last argument (legacy_notify.rs, its wire-shape test)."""
    return json.dumps({"type": "agent-turn-complete", "thread-id": "b5f6c1c2-1111-2222-3333-444455556666",
                       "turn-id": "12345", "cwd": "/repo", "client": "codex-tui",
                       "input-messages": ["Rename `foo` to `bar` and update the callsites."],
                       "last-assistant-message": message, **extra})


def notify_argv(command: str) -> list[str]:
    """The notify program a launch line passes, read back the way a shell and then Codex's -c parser read it."""
    words = shlex.split(command)
    values = [words[i + 1] for i, w in enumerate(words) if w == "-c" and words[i + 1].startswith("notify=")]
    [value] = values
    return tomllib.loads("notify = " + value.partition("=")[2].strip())["notify"]


def load(text: str) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as fh:
        fh.write(text)
    try:
        return fleet.load_config(Path(fh.name))
    finally:
        os.unlink(fh.name)


class Field(unittest.TestCase):
    """The `signals` field, the older `hooks`, and the shipped adapters."""

    def test_each_shipped_adapter_names_its_mechanism(self):
        p = SHIPPED["providers"]
        want = {"claude": "claude-settings", "claude-co": "claude-settings", "codex": "codex-notify"}
        for name, adapter in p.items():
            with self.subTest(name):
                self.assertEqual(fleet.signal_method(adapter), want.get(name, ""))
        self.assertTrue(fleet.notify_on(p["codex"]))
        self.assertFalse(fleet.hooks_on(p["codex"]))
        self.assertFalse(fleet.notify_on(p["codex"], fleet.HEADLESS))  # a headless run has its own line, no signals
        self.assertFalse(fleet.notify_on(p["claude"]))
        for name in ("cursor", "agy", "opencode"):
            with self.subTest(name):
                self.assertFalse(fleet.notify_on(p[name]) or fleet.hooks_on(p[name]))

    def test_hooks_is_the_older_name_and_an_empty_value_turns_signals_off(self):
        self.assertEqual(fleet.signal_method({"hooks": "claude-settings"}), "claude-settings")
        self.assertEqual(fleet.signal_method({"signals": "claude-settings", "hooks": ""}), "")  # an owner's old off
        self.assertEqual(fleet.signal_method({"signals": "", "hooks": "claude-settings"}), "")
        self.assertEqual(fleet.signal_method({"signals": "codex-notify", "hooks": "claude-settings"}), "codex-notify")
        self.assertEqual(fleet.signal_method({}), "")
        # Old user configs load as before: Claude's hooks off with hooks = "", Codex's off with signals = "".
        cfg = load('[providers.claude]\nhooks = ""\n[providers.codex]\nsignals = ""\n')
        self.assertFalse(fleet.hooks_on(cfg["providers"]["claude"]))
        self.assertFalse(fleet.notify_on(cfg["providers"]["codex"]))
        cfg = load('[providers.claude]\nhooks = "claude-settings"\n')
        self.assertTrue(fleet.hooks_on(cfg["providers"]["claude"]))

    def test_config_refuses_unknown_mechanisms_and_cursor_hooks(self):
        for toml, words in (('[providers.cursor]\nsignals = "cursor-hooks"\n', "cursor-hooks is not available"),
                            ('[providers.codex]\nsignals = "codex-hooks"\n', "codex.signals must be one of"),
                            ('[providers.codex]\nhooks = "codex-notify"\n', "codex.hooks must be one of")):
            with self.subTest(toml):
                with self.assertRaises(fleet.FleetError) as caught:
                    load(toml)
                self.assertIn(words, str(caught.exception))

    def test_the_codex_line_carries_notify_only_with_signals(self):
        p = SHIPPED["providers"]["codex"]
        before = ("env -u CODEX_HOME codex -m m -c model_reasoning_effort=high -c check_for_update_on_startup=false "
                  "-s workspace-write -a on-request go")
        self.assertEqual(fleet.build_launch(p, "m", "high", "go"), before)  # the line from before signals
        events = "/r/.fleet/runs/demo/events/01.jsonl"
        line = fleet.build_launch(p, "m", "high", "go", signals=fleet.notify_args(events, "ab" * 16))
        self.assertEqual(notify_argv(line), [sys.executable, str(FLEET.resolve()), "_notify", events, "ab" * 16])
        self.assertTrue(line.endswith(" -s workspace-write -a on-request go"))
        # An owner's line without {signals} runs as written; other adapters never take the fragment.
        old = dict(p, launch="{bin} -m {model} -c model_reasoning_effort={effort} {profile} {prompt}")
        self.assertFalse(fleet.notify_on(old))
        self.assertNotIn("notify", fleet.build_launch(old, "m", "high", "go", signals="-c notify=[]"))
        for name in ("claude", "cursor", "agy", "opencode"):
            with self.subTest(name):
                self.assertNotIn("notify", fleet.build_launch(SHIPPED["providers"][name], "m", "high", "go",
                                                              signals="-c notify=[]"))
        self.assertNotIn("notify", fleet.build_launch(p, "m", "high", "go", signals="-c notify=[]",
                                                      profile=fleet.HEADLESS))

    def test_notify_quoting_survives_spaces_quotes_and_backslashes(self):
        for events in (ODD + "/events/01.jsonl", "/r/plain/events/01.jsonl", "/r/$(touch x)/`y`/events/01.jsonl"):
            with self.subTest(events), patch.object(fleet.sys, "executable", "/opt/my python/bin/python3"):
                fragment = fleet.notify_args(events, "cd" * 16)
                words = shlex.split(fragment)
                self.assertEqual(len(words), 2)  # -c and one notify=[…] word
                self.assertEqual(words[0], "-c")
                argv = tomllib.loads("notify = " + words[1].partition("=")[2])["notify"]
                self.assertEqual(argv, ["/opt/my python/bin/python3", str(FLEET.resolve()), "_notify", events,
                                        "cd" * 16])
        self.assertEqual(fleet.toml_string('a"b\\c\x7f'), '"a\\"b\\\\c\\u007f"')
        with self.assertRaises(ValueError):
            fleet.toml_string("/r/\udcff/events/01.jsonl")  # bytes that are not UTF-8 have no TOML form


class Notify(unittest.TestCase):
    """`fleet _notify` as Codex runs it: argv plus the notification JSON, nothing on stdin, never waited on."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name) / "a run" / "events"
        self.dir.mkdir(parents=True)
        self.events = self.dir / "01.jsonl"

    def notify(self, *args, env=None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(FLEET), "_notify", *args], text=True, capture_output=True,
                              stdin=subprocess.DEVNULL, timeout=30, env=env)

    def logged(self) -> list[dict]:
        return [json.loads(line) for line in self.events.read_text().splitlines()] if self.events.exists() else []

    def test_a_turn_complete_logs_turn_ended_with_its_last_message(self):
        res = self.notify(str(self.events), "tok", turn_complete("Rename complete and verified."),
                          env={"PATH": "/usr/bin:/bin"})  # Codex rebuilds the environment
        self.assertEqual(res.returncode, 0, res.stderr)
        [e] = self.logged()
        self.assertEqual((e["event"], e["token"], e["message"], e["turn"], e["thread"]),
                         ("turn-ended", "tok", "Rename complete and verified.", "12345",
                          "b5f6c1c2-1111-2222-3333-444455556666"))
        self.notify(str(self.events), "tok", turn_complete(None))  # Codex may send no last message
        self.notify(str(self.events), "tok", turn_complete("x" * 2000))
        self.assertEqual([e["message"] for e in self.logged()[1:]], ["", "x" * 500])

    def test_other_types_are_ignored_and_bad_input_fails_safe(self):
        self.assertEqual(self.notify(str(self.events), "tok", json.dumps({"type": "approval-requested"})).returncode, 0)
        self.assertEqual(self.logged(), [])
        for args in ([str(self.events), "tok", "{not json"], [str(self.events), "tok", "[1]"],
                     [str(self.events), "tok"], [str(self.events), "tok", "{}", "extra"]):
            with self.subTest(args):
                self.assertEqual(self.notify(*args).returncode, 0)
        self.assertEqual([e["event"] for e in self.logged()], ["notify-error"] * 4)
        for args in ([], ["only-one"], ["/nonexistent/dir/events.jsonl", "tok", turn_complete("x")]):
            with self.subTest(args):
                self.assertEqual(self.notify(*args).returncode, 0)

    def test_it_is_quick_and_never_takes_the_run_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            with patch.object(fleet, "repo_root", return_value=repo), fleet.try_lock(repo) as held:
                self.assertTrue(held)
                started = time.monotonic()
                res = self.notify(str(self.events), "tok", turn_complete("done"))
                elapsed = time.monotonic() - started
        self.assertEqual(res.returncode, 0)
        self.assertLess(elapsed, 5)
        self.assertEqual(len(self.logged()), 1)


class Doctor(unittest.TestCase):
    def fake(self, tmp: str, text: str) -> str:
        path = Path(tmp) / "codex"
        path.write_text(f"#!/bin/sh\necho '{text}'\n")
        path.chmod(0o755)
        return str(path)

    def test_doctor_names_the_mechanism_and_warns_when_its_flag_is_gone(self):
        p = SHIPPED["providers"]
        cfg = {"defaults": dict(fleet.DEFAULTS)}
        with tempfile.TemporaryDirectory() as tmp:
            codex = dict(p["codex"], bin=self.fake(tmp, "-m --model -s --sandbox -a --ask-for-approval"))
            lines, _ = fleet.profile_report(cfg, "codex", codex, codex["bin"])
        text = "\n".join(lines)
        self.assertIn("signals codex-notify (-c notify): a turn that ends without a hand-back wakes IDLE", text)
        self.assertIn("WARNING: signals codex-notify passes -c, which", text)
        self.assertEqual(text.count("WARNING"), 1)  # -c is named once, as the signal's flag
        with tempfile.TemporaryDirectory() as tmp:
            codex = dict(p["codex"], bin=self.fake(tmp, "-c, --config <key=value> -m -s -a"))
            lines, _ = fleet.profile_report(cfg, "codex", codex, codex["bin"])
        self.assertNotIn("WARNING", "\n".join(lines))
        self.assertEqual(fleet.signals_line(p["cursor"]), "signals none: wait watches the screen (STALLED)")
        self.assertIn("no {signals} slot", fleet.signals_line(dict(p["codex"], launch="{bin} -m {model} {prompt}")))
        self.assertIn("signals claude-settings (--settings)", fleet.signals_line(p["claude"]))


class CodexWait(RuntimeCase):
    """A Codex-style test adapter: launch passes notify, the program logs, wait wakes IDLE."""

    def setUp(self):
        super().setUp()
        self.cfg["providers"]["writer"].update(launch=NOTIFY_LAUNCH, signals="codex-notify")

    def worker_rec(self, ticket="01"):
        return self.state()["workers"][ticket]

    def end_turn(self, message, ticket="01") -> None:
        """Run the launch line's notify program as Codex does after a turn."""
        argv = notify_argv(self.worker_rec(ticket)["command"])
        res = subprocess.run([*argv, turn_complete(message)], stdin=subprocess.DEVNULL, capture_output=True,
                             text=True, timeout=30)
        self.assertEqual(res.returncode, 0, res.stderr)

    def wait(self, expected=0):
        return self.call("wait", "demo", "--timeout", "0", "--settle", "0", expected=expected)

    def keystrokes(self) -> list:
        return [c for c in self.cmux_calls if c and c[0] in ("send", "send-key")]

    def test_launch_records_the_events_file_and_passes_its_program(self):
        self.setup_run()
        self.launch()
        w, d = self.worker_rec(), self.repo / ".fleet/runs/demo"
        self.assertEqual(w["signals"], {"method": "codex-notify", "events": str(d / "events/01.jsonl")})
        self.assertNotIn("hooks", w)  # no Claude settings, scope file or guard
        self.assertEqual(notify_argv(w["command"]),
                         [sys.executable, str(FLEET.resolve()), "_notify", str(d / "events/01.jsonl"), w["token"]])

    def test_a_turn_that_ends_without_a_hand_back_wakes_idle_with_its_last_words(self):
        self.setup_run()
        self.launch()
        self.end_turn("Should I also update\nthe docs?")
        out = self.wait()
        self.assertIn('IDLE 01: Codex ended its turn with "Should I also update the docs?" and has not handed back; '
                      "fleet peek demo 01", out)
        self.assertIn("TIMEOUT", self.wait(2))  # each turn wakes once
        self.end_turn("First.")
        self.end_turn("Second.")
        out = self.wait()
        self.assertEqual(out.count("IDLE 01"), 1)  # turns that ended in one pass wake once, with the latest words
        self.assertIn('"Second."', out)
        self.assertEqual(self.keystrokes(), [])

    def test_a_turn_that_ends_after_a_hand_back_is_quiet(self):
        self.setup_run()
        self.launch()
        self.report()
        self.end_turn("Report written.")
        out = self.wait()
        self.assertIn("REPORT 01 [needs-verification]", out)
        self.assertNotIn("IDLE", out)
        self.assertIn("TIMEOUT", self.wait(2))

    def test_an_idle_line_keeps_the_same_screen_from_also_waking_as_stalled(self):
        self.setup_run()
        self.launch()
        seen = self.repo / ".fleet/runs/demo/.seen"
        self.assertEqual(fleet.scan_workers(self.repo, self.state(), seen, 60), [])
        self.end_turn("Which file?")
        [line] = fleet.scan_workers(self.repo, self.state(), seen, 60)
        self.assertTrue(line.startswith("IDLE 01: Codex ended its turn"))
        marker = seen / "01.screen"
        key, since, flagged = marker.read_text().split("|")
        marker.write_text(f"{key}|{float(since) - 61}|{flagged}")
        self.assertEqual(fleet.scan_workers(self.repo, self.state(), seen, 60), [])

    def test_an_earlier_launchs_turns_are_ignored(self):
        self.setup_run()
        self.launch()
        with Path(self.worker_rec()["signals"]["events"]).open("a") as fh:
            fh.write(json.dumps({"event": "turn-ended", "token": "old", "message": "x", "at": 0}) + "\n")
        self.assertIn("TIMEOUT", self.wait(2))

    def test_signals_off_and_old_runs_keep_the_screen_hash(self):
        self.cfg["providers"]["writer"]["signals"] = ""
        self.setup_run()
        self.launch()
        w = self.worker_rec()
        self.assertNotIn("signals", w)
        self.assertNotIn("notify", w["command"])
        # A run from before signals: its record has no `signals`, so an events line is never read for it.
        (self.repo / ".fleet/runs/demo/events").mkdir(exist_ok=True)
        (self.repo / ".fleet/runs/demo/events/01.jsonl").write_text(
            json.dumps({"event": "turn-ended", "token": w["token"], "message": "x", "at": 0}) + "\n")
        seen = self.repo / ".fleet/runs/demo/.seen"
        self.assertEqual(fleet.scan_workers(self.repo, self.state(), seen, 60), [])
        marker = seen / "01.screen"
        key, since, flagged = marker.read_text().split("|")
        marker.write_text(f"{key}|{float(since) - 61}|{flagged}")
        [stalled] = fleet.scan_workers(self.repo, self.state(), seen, 60)
        self.assertIn("STALLED 01", stalled)

    def test_claude_turns_still_wake_only_through_idle(self):
        """A Claude worker's turn-ended still waits for its idle event; Codex's IDLE is Codex's alone."""
        self.cfg["providers"]["writer"].update(launch="{bin} --model {model} --effort {effort} {settings} {prompt}",
                                                signals="claude-settings")
        self.setup_run()
        self.launch()
        w = self.worker_rec()
        self.assertNotIn("signals", w)
        with Path(w["hooks"]["events"]).open("a") as fh:
            fh.write(json.dumps({"event": "turn-ended", "token": w["token"], "message": "x", "at": 0}) + "\n")
        self.assertIn("TIMEOUT", self.wait(2))


@unittest.skipUnless(HAS_TMUX, "tmux is not installed")
class CodexSmoke(FleetCase):
    """The shipped codex adapter through fleet in headless tmux, in a repo whose path has a space and a quote,
    against the strict fake: it parses the -c notify value as TOML and runs the program after its turn."""

    def setUp(self):
        super().setUp()
        odd = self.root / "my \"odd\" repo's dir"
        odd.mkdir()
        self.repo.rename(odd / "repo")
        self.repo = odd / "repo"
        bindir = self.root / "bin"
        bindir.mkdir()
        fake = bindir / "codex"
        fake.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_CLI}" codex "$@"\n')
        fake.chmod(0o755)
        self.log = self.root / "fake-log"
        self.log.mkdir()
        self.expect = self.root / "expect.json"
        self.env.update(PATH=f"{bindir}:{self.env['PATH']}", FAKE_CLI_LOG=str(self.log),
                        FAKE_CLI_EXPECT=str(self.expect))

    def diagnostics(self) -> str:
        logged = "".join(f.read_text() for f in sorted(self.log.glob("*.errors")))
        return super().diagnostics() + (f"\n--- the fake CLI refused:\n{logged}" if logged else "")

    def start(self, **extra) -> None:
        p = SHIPPED["providers"]["codex"]
        model, effort = fleet.resolve_model(p, "standard", None, None)
        self.expect.write_text(json.dumps({"model": model, "effort": effort, "session": None, "account_dir": None,
                                           "notify": True, **extra}))
        self.write_config('review = "off"\nprefer = ["codex"]\n')
        self.fleet("init", "smoke")
        (self.repo / "01.md").write_text("# Smoke\nChange a.txt.\n")
        (self.repo / "plan.json").write_text(json.dumps({"tickets": [
            {"id": "01", "ticket": "01.md", "files": ["a.txt"], "provider": "codex", "tier": "standard",
             "context": "A smoke test.", "checks": {"manual": "manual prose review is sufficient"}}]}))
        self.fleet("plan", "smoke", "--file", str(self.repo / "plan.json"))
        self.assertIn("LAUNCHED 01 codex:", self.fleet("launch", "smoke", "--ready"))

    def stop(self) -> None:
        self.assertRegex(self.fleet("stop", "smoke", "01", "--timeout", "10"), r"01: exited \(@\d+\)")
        errors = self.log / "codex.errors"
        self.assertFalse(errors.exists(), errors.read_text() if errors.exists() else "")

    def test_a_codex_question_wakes_idle_with_its_words(self):
        self.start(turn_message="Which file holds the config?")
        out = self.wait_for("smoke", r"^IDLE 01\b")
        self.assertIn('IDLE 01: Codex ended its turn with "Which file holds the config?" and has not handed back', out)
        self.stop()

    def test_a_hand_back_wakes_report_and_no_idle(self):
        self.start()
        out = self.wait_for("smoke", r"^REPORT 01\b")
        self.assertNotIn("IDLE", out)
        events = self.repo / ".fleet/runs/smoke/events/01.jsonl"
        deadline = time.monotonic() + 10
        while not events.exists() and time.monotonic() < deadline:
            time.sleep(0.2)
        [e] = [json.loads(line) for line in events.read_text().splitlines()]
        self.assertEqual((e["event"], e["message"]), ("turn-ended", "Report written for 01."))
        quiet = self.fleet("wait", "smoke", "--timeout", "2", "--interval", "1", "--settle", "0", "--stall", "0",
                           expected=2)
        self.assertIn("TIMEOUT", quiet)
        self.assertNotIn("IDLE", quiet)
        self.stop()


if __name__ == "__main__":
    unittest.main()
