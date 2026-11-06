"""Watching workers from anywhere: `fleet status --brief` (one line per worker), `fleet statusline` (Claude Code's
status line, chained after the owner's own) with --install/--uninstall, and `fleet dashboard` (a local read-only
page). All three only read: no lock, nothing written in the checkout, worker text flattened. Hooks run as real
`fleet _hook` subprocesses; one live test drives a fake worker through real tmux."""
import contextlib
import copy
import http.client
import io
import json
import os
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_claude_hooks import HookCase, notification, stop  # noqa: E402
from test_runtime import fleet  # noqa: E402

FLEET = Path(__file__).resolve().parents[1] / "skill" / "bin" / "fleet"
CONTROL_CHARS = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩﻿]")
TOKEN_LAUNCH = "{bin} {session} --model {model} --effort {effort} {profile} {settings} {prompt}"


def call_line(mid, at, inp, out, cache_read=0, cache_write=0):
    """One assistant line of a Claude session log, as tests/test_ledger.py writes them."""
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(at))
    return {"type": "assistant", "timestamp": stamp,
            "message": {"id": mid, "usage": {"input_tokens": inp, "output_tokens": out,
                                             "cache_read_input_tokens": cache_read,
                                             "cache_creation_input_tokens": cache_write}}}


class WatchCase(HookCase):
    """Hooked test adapters, two independent tickets, and helpers that read the brief view."""

    def setUp(self):
        super().setUp()
        self.cfg["defaults"].update(max_parallel=4, acceptance="warn")
        self.cfg["providers"]["writer"]["max"] = 4
        self.claude_home = Path(tempfile.mkdtemp(dir=self.temp.name))

    def start(self, tickets=None):
        self.setup_run(tickets=tickets or [{"id": "01", "files": ["owned.txt"]}, {"id": "02", "files": ["next.txt"]}])

    def claude_writer(self):
        """The writer adapter as a Claude account: named sessions, logs under its own CLAUDE_CONFIG_DIR."""
        self.cfg["providers"]["writer"].update(launch=TOKEN_LAUNCH, session="--session-id {id}",
                                               resume="--resume {id}", account_env="CLAUDE_CONFIG_DIR",
                                               env={"CLAUDE_CONFIG_DIR": str(self.claude_home)})

    def session_log(self, session, lines):
        log = self.claude_home / "projects" / "-tmp-repo" / f"{session}.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a") as fh:
            for line in lines:
                fh.write(json.dumps(line) + "\n")
        return log

    def brief(self, *args, expected=0):
        return self.call("status", *args, "--brief", expected=expected)

    def brief_json(self, *args, expected=0):
        return json.loads(self.call("status", *args, "--brief", "--json", expected=expected))

    def rows(self, *args) -> dict:
        return {w["id"]: w for r in self.brief_json(*args)["runs"] for w in r["workers"]}

    def line(self, wid, *args) -> str:
        [line] = [x for x in self.brief(*args).splitlines() if re.match(rf"  [●○] {re.escape(wid)} ", x)]
        return line

    def write_report(self, wid, status, prose="Done.\n", **header):
        text = f"# Report {wid}\nStatus: {status}\n\n"
        if header:
            data = {"files_changed": [], "checks": [], "criteria": [], "findings": [], "questions": [], **header}
            text += f"```fleet\n{json.dumps(data)}\n```\n\n"
        (self.repo / ".fleet/runs/demo/reports" / f"{wid}.md").write_text(text + prose)

    def edit_state(self, change):
        state = self.state()
        change(state)
        fleet.save_run(self.repo, "demo", state)  # with Fleet's control copy, so locked commands still run

    def statusline(self, payload=b"{}", *args):
        stdin = io.TextIOWrapper(io.BytesIO(payload if isinstance(payload, bytes) else json.dumps(payload).encode()))
        with patch.object(sys, "stdin", stdin):
            return self.call("statusline", *args)

    def snapshot(self) -> dict:
        """Every path under .fleet/ and the control record, with its bytes and mtime; folders too."""
        out = {}
        for root in (self.repo / ".fleet", self.cache / "agent-fleet" / "control"):
            for f in sorted(root.rglob("*")) if root.exists() else ():
                out[str(f)] = (f.read_bytes(), f.stat().st_mtime_ns) if f.is_file() else "folder"
        return out


class Brief(WatchCase):
    def test_nothing_is_printed_without_a_live_worker(self):
        self.assertEqual(self.brief(), "")  # no .fleet at all
        self.assertEqual(self.statusline(), "")
        self.start()
        self.assertEqual(self.brief(), "")  # a run, nothing launched
        self.launch()
        self.assertIn("● 01", self.brief())
        self.report()
        self.stop_with_receipt()
        self.verify()
        self.assertEqual(self.brief(), "")  # verified and stopped: nothing live
        self.assertEqual(self.statusline(), "")
        self.assertEqual(self.brief_json(), {"checkout": str(self.repo), "runs": [], "skipped": []})
        self.assertIn("○ 01", self.brief("demo"))  # a named run lists every worker it has

    def test_a_working_worker_shows_its_route_and_time_and_unknown_tokens_are_left_out(self):
        self.start()
        self.launch()
        self.edit_state(lambda s: s["ledger"][-1].update(start=s["ledger"][-1]["start"] - 125))
        out = self.brief()
        self.assertEqual(out.splitlines()[0], "fleet demo · 1 live")
        self.assertRegex(self.line("01"), r"\A  ● 01  writer:writer-model@high  working · 2m \d+s\Z")
        row = self.rows()["01"]
        self.assertEqual((row["activity"], row["live"], row["report"], row["tokens"]), ("working", True, None, None))
        self.assertGreaterEqual(row["elapsed_s"], 125)
        self.assertEqual(row["title"], "Ticket 01")
        self.assertNotIn("in", self.line("01").split("working")[1])  # no token text at all, never "0 in"

    def test_a_live_claude_workers_tokens_come_from_its_log_read_only_where_it_grew(self):
        self.claude_writer()
        self.start()
        self.launch()
        w = self.state()["workers"]["01"]
        at = w["launched_at"]
        log = self.session_log(w["session"], [call_line("m1", at + 1, 100, 20, 5000, 700),
                                              call_line("m1", at + 2, 100, 25, 5000, 700),  # same call, later block
                                              call_line("m0", at - 600, 9, 9, 9, 9)])  # before this launch
        self.assertEqual(self.rows()["01"]["tokens"],
                         {"input": 100, "output": 25, "cache_read": 5000, "cache_write": 700})
        self.assertIn("· 5.8K in, 25 out", self.line("01"))
        cache = self.cache / "agent-fleet" / "brief-tokens.json"
        self.assertEqual(json.loads(cache.read_text())["logs"][f"{log}|{at}|None"]["offset"], log.stat().st_size)
        with log.open("a") as fh:
            fh.write(json.dumps(call_line("m2", at + 3, 50, 400, 6000)) + "\n" + '{"type": "assistant", "mess')
        self.assertEqual(self.rows()["01"]["tokens"],
                         {"input": 150, "output": 425, "cache_read": 11000, "cache_write": 700})
        with log.open("a") as fh:  # the line Claude was still writing ends: it is read now, and only it
            fh.write('age": {"id": "m3", "usage": {"output_tokens": 5}}}\n')
        self.assertEqual(self.rows()["01"]["tokens"]["output"], 430)
        with patch.object(fleet.TokenCache, "advance", side_effect=AssertionError("re-read an unchanged log")):
            self.assertEqual(self.rows()["01"]["tokens"]["output"], 430)
        cache.write_text("{not json")  # a damaged cache costs a full read, never a wrong count
        self.assertEqual(self.rows()["01"]["tokens"]["output"], 430)
        fleet.write_text_atomic(log, log.read_text())  # a replaced log (another inode) is read from the start
        self.assertEqual(self.rows()["01"]["tokens"]["output"], 430)

    def test_a_streamed_reply_split_across_refreshes_counts_once(self):
        self.claude_writer()
        self.start()
        self.launch()
        w = self.state()["workers"]["01"]
        at = w["launched_at"]
        self.session_log(w["session"], [call_line("m1", at + 1, 100, 1)])
        self.rows()
        self.session_log(w["session"], [call_line("m1", at + 2, 100, 30)] +
                         [call_line(f"n{n}", at + 3, 1, 1) for n in range(40)])  # m1 settles in the cache
        self.assertEqual(self.rows()["01"]["tokens"], {"input": 140, "output": 70, "cache_read": 0, "cache_write": 0})

    def test_handed_back_blocked_and_plan_ready(self):
        self.start()
        self.launch()
        self.launch("02")
        self.write_report("01", "needs-verification")
        self.write_report("02", "blocked", questions=["Which API version, v1 or v2?"])
        self.assertIn("  ● 01  writer:writer-model@high  handed back: needs-verification · ", self.brief())
        self.assertIn('blocked: "Which API version, v1 or v2?"', self.line("02"))
        self.assertEqual(self.brief().splitlines()[0], "fleet demo · 2 live · 2 handed back")
        self.write_report("02", "blocked", "## Decisions and blockers\n- Need the schema file\n")
        self.assertIn('blocked: "Need the schema file"', self.line("02"))
        self.write_report("02", "plan-ready")
        self.assertEqual(self.rows()["02"]["activity"], "plan-ready")
        self.assertIn("handed back: plan-ready", self.line("02"))
        self.call("send", "demo", "01", "--changes", "Rename the helper too.")
        self.assertIn("  ● 01  writer:writer-model@high  working", self.brief())  # the lead answered it

    def test_a_stopped_worker_shows_while_its_hand_back_waits_on_the_lead(self):
        self.start()
        self.launch()
        self.launch("02")
        self.hand_back("01")
        self.assertRegex(self.line("01"), r"○ 01 .* handed back: needs-verification · \d+s")
        self.verify("01")
        self.assertNotIn(" 01 ", self.brief())  # verified: it waits on no one
        self.receipt("02", returncode=3)
        self.assertEqual(self.brief(), "")  # nothing live in the run
        self.assertIn("exited (code 3), no hand-back", self.line("02", "demo"))

    def test_gate_and_review_state_follow_a_hand_back(self):
        self.start()
        self.launch()
        self.launch("02")
        self.write_report("01", "needs-verification")
        f = self.repo / ".fleet/runs/demo/reports/01.md"
        record = {"report": fleet.report_key(f, f.read_text()), "passed": True, "summary": "2 of 2 passed"}
        fleet.atomic_json(fleet.gate_file(self.repo, "demo", "01", ".gate.json"), record)
        self.assertIn("handed back: needs-verification · gate passed", self.line("01"))
        now = time.time()
        self.edit_state(lambda s: s["workers"].update({"review-01": {
            "id": "review-01", "review_of": "01", "role": "review", "state": "running", "provider": "reviewer",
            "model": "reviewer-model", "launched_at": now, "token": "r1", "surface": "surface:9"}}))
        self.assertIn("· gate passed · in review by review-01", self.line("01"))
        self.assertIn("working", self.line("review-01"))
        self.assertEqual(self.rows()["review-01"]["title"], "review of 01")
        f.write_text(f.read_text() + "\nedited\n")  # the gate ran on an earlier hand-back
        self.assertNotIn("gate", self.line("01"))

    def test_permission_idle_and_turn_end_come_from_the_events_file(self):
        self.start()
        self.launch()
        self.hook("Notification", notification("permission_prompt", "Claude needs your permission to use Bash"))
        self.assertRegex(self.line("01"), r"permission prompt \d+s ago: Claude needs your permission to use Bash")
        self.assertEqual(self.rows()["01"]["activity"], "permission")
        self.hook("Stop", stop("Should I also update\nthe docs?"))
        self.assertIn('turn ended, no hand-back: "Should I also update the docs?"', self.line("01"))
        self.hook("Notification", notification("idle_prompt", "Claude is waiting for your input"))
        self.assertIn('idle, no hand-back: "Should I also update the docs?"', self.line("01"))
        self.call("send", "demo", "01", "Yes, the docs too.")
        self.assertIn("working", self.line("01"))  # every event so far came before the lead's answer
        events = Path(self.worker_rec()["hooks"]["events"])
        with events.open("a") as fh:  # another launch's event, and a line a hook is still writing
            fh.write(json.dumps({"event": "permission-prompt", "token": "other", "at": time.time()}) + "\n{\"ev")
        self.assertIn("working", self.line("01"))
        self.write_report("01", "needs-verification")
        self.assertIn("handed back", self.line("01"))  # a hand-back outranks every event

    def test_worker_text_is_flattened_everywhere(self):
        evil = "Run\x1b[2J\x1b]0;pwned\x07 this?\nrm -rf /‮​ok\r"
        self.start()
        self.launch()
        self.launch("02")
        self.write_report("01", "blocked", questions=[evil])
        self.hook("Notification", notification("permission_prompt", evil), ticket="02")
        (self.repo / "02.md").write_text(f"# Title {evil}\n")
        self.edit_state(lambda s: s["workers"]["02"].update(model="m\x1b[31mred\nx"))
        out = self.brief() + self.statusline() + self.call("status", "--brief", "--json")
        self.assertNotRegex(out.replace("\n", ""), CONTROL_CHARS)
        for line in self.brief().splitlines():
            self.assertNotRegex(line, CONTROL_CHARS)
        self.assertIn('blocked: "Run [2J ]0;pwned this? rm -rf / ok"', self.line("01"))
        self.assertIn("writer:m [31mred x@high", self.line("02"))
        self.assertEqual(self.rows()["02"]["title"], "Title Run [2J ]0;pwned this?")  # its first line only

    def test_damaged_and_older_runs_print_what_they_can(self):
        self.start()
        self.launch()
        other = self.repo / ".fleet/runs/broken"
        other.mkdir()
        (other / "run.json").write_text('{"schema": 2, "workers": {"x": {"state": "running"')
        (self.repo / ".fleet/runs/bad.name").mkdir()
        (self.repo / ".fleet/runs/bad.name/run.json").write_text("{}")
        self.assertIn("● 01", self.brief())  # the damaged runs are left out of the default view
        self.assertEqual({s["run"] for s in self.brief_json()["skipped"]}, {"broken", "bad.name"})
        self.assertIn("fleet broken: cannot read this run: ", self.brief("broken", expected=1))
        self.assertIn("run/worker IDs must be", self.brief("bad.name", expected=1))
        self.assertEqual(self.brief_json("nosuch", expected=1)["skipped"][0]["run"], "nosuch")

        def damage(state):
            state["workers"]["02"] = {"id": "02", "state": "running", "launched_at": "yesterday", "provider": ["x"],
                                      "token": {"no": 1}, "hooks": "nope", "last_send": "soon"}
            state["workers"]["../up"] = {"id": "../up", "state": "running"}
            state["workers"]["junk"] = "not a record"
            del state["ledger"]  # a run from before the ledger
        self.edit_state(damage)
        rows = self.rows()
        self.assertEqual(rows["01"]["activity"], "working")
        self.assertEqual(rows["02"]["activity"], "working")
        self.assertIsNone(rows["02"]["elapsed_s"])
        self.assertIn("record unreadable: run/worker IDs must be", rows["../up"]["doing"])
        Path(self.state()["workers"]["01"]["exit_file"]).write_text("{half a receipt")
        self.assertEqual(self.rows()["01"]["activity"], "working")  # an unreadable receipt leaves it as recorded

    def test_it_takes_no_lock_and_writes_nothing_in_the_checkout(self):
        self.claude_writer()
        self.start()
        self.launch()
        self.launch("02")
        w = self.state()["workers"]["01"]
        self.session_log(w["session"], [call_line("m1", w["launched_at"] + 1, 1, 2)])
        self.write_report("02", "blocked", questions=["Which?"])
        self.hook("Notification", notification("idle_prompt", "Claude is waiting for your input"))
        before, status = self.snapshot(), self.git("status", "--porcelain", "--ignored")
        reading = {"session": "lead-1", "tokens": 131_000, "zone": "dumb", "warn_at": 100_000, "dumb_at": 125_000}
        with patch.object(fleet, "try_lock", side_effect=AssertionError("took the lock")), \
                patch.object(fleet, "lead_reading", return_value=reading):
            out = "".join([self.brief(), self.brief("demo"), self.call("status", "--brief", "--json"),
                           self.call("status", "demo", "--brief", "--json"), self.statusline(),
                           self.statusline(b"{}", "--then", "true")])
        self.assertNotIn("LEAD", out)  # no lead notice, so no .seen marker either
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.git("status", "--porcelain", "--ignored"), status)
        self.assertTrue((self.cache / "agent-fleet" / "brief-tokens.json").exists())  # outside the checkout
        self.assertIn("running 2/4", self.call("status", "demo"))  # locked commands still pass the control check

    def test_status_without_a_run_needs_brief(self):
        self.start()
        self.assertIn("status needs a run; fleet status --brief", self.call("status", expected=1))


class BriefText(unittest.TestCase):
    def data(self, n, runs=1):
        def row(i, run):
            return {"run": run, "id": f"w{i:02d}", "live": i % 2 == 0, "worker": "codex:gpt-6-sol@high",
                    "activity": "working", "doing": "working", "elapsed_s": 61.0 * i, "tokens": None}
        return {"runs": [{"run": f"r{k}", "live": 1, "workers": [row(i, f"r{k}") for i in range(n)]}
                         for k in range(runs)]}

    def test_rows_are_capped_with_a_count_of_the_rest(self):
        lines = fleet.brief_lines(self.data(9), rows=6)
        self.assertEqual(len(lines), 8)
        self.assertEqual(lines[-1], "  +3 more: fleet status --brief")
        self.assertEqual(lines[2], "  ○ w01  codex:gpt-6-sol@high  working · 1m 1s")
        self.assertEqual(len(fleet.brief_lines(self.data(3), rows=6)), 4)  # no "more" line when all fit
        lines = fleet.brief_lines(self.data(4, runs=2), rows=6)
        self.assertEqual([x for x in lines if x.startswith("fleet")], ["fleet r0 · 1 live", "fleet r1 · 1 live"])
        self.assertEqual(lines[-1], "  +2 more: fleet status --brief")

    def test_clock_counts_and_tokens(self):
        self.assertEqual([fleet.clock(s) for s in (0, 59.9, 61, 2761, 7500)], ["0s", "59s", "1m 1s", "46m 1s", "2h 5m"])
        self.assertEqual(fleet.brief_tokens_text({"output": 542_700}), "542.7K out")
        self.assertEqual(fleet.brief_tokens_text({"input": 10, "cache_read": 1_200_000}), "1.2M in")
        self.assertEqual(fleet.brief_tokens_text(None), "")
        self.assertIsNone(fleet.brief_token_counts({"input": "many", "other": 3}))
        self.assertEqual(fleet.brief_token_counts({"input": 0}), {"input": 0})  # a real zero stays a zero


class Statusline(WatchCase):
    def test_it_prints_fleets_rows_for_the_session_json(self):
        self.start()
        self.launch()
        payload = {"session_id": "abc", "cwd": str(self.repo), "workspace": {"current_dir": str(self.repo)},
                   "model": {"id": "claude-opus-5-5", "display_name": "Opus"}}
        out = self.statusline(payload)
        self.assertEqual(out.splitlines()[0], "fleet demo · 1 live")
        self.assertRegex(out.splitlines()[1], r"\A  ● 01  writer:writer-model@high  working · \d+s\Z")
        for garbage in (b"", b"\xff\xfe not json", b"[1, 2]", b'{"workspace": "x", "cwd": 7}', b"null"):
            with self.subTest(garbage=garbage):
                self.assertEqual(self.statusline(garbage), out)

    def test_then_runs_the_owners_command_first_with_the_same_input(self):
        self.start()
        self.launch()
        payload = {"model": {"display_name": "Opus"}, "cwd": str(self.repo)}
        out = self.statusline(payload, "--then", "printf '\\033[1mmine\\033[0m\\n'; cat")
        lines = out.splitlines()
        self.assertEqual(lines[0], "\x1b[1mmine\x1b[0m")  # the owner's own output, colours and all
        self.assertEqual(json.loads(lines[1]), payload)
        self.assertEqual(lines[2], "fleet demo · 1 live")

    def test_a_failing_or_slow_chained_command_still_exits_zero(self):
        self.start()
        self.launch()
        self.assertEqual(self.statusline(b"{}", "--then", "echo partial; exit 3").splitlines()[:2],
                         ["partial", "fleet demo · 1 live"])
        self.assertEqual(self.statusline(b"{}", "--then", "no-such-command-anywhere").splitlines()[0],
                         "fleet demo · 1 live")
        with patch.object(fleet, "STATUSLINE_THEN_S", 0.5):
            began = time.monotonic()
            out = self.statusline(b"{}", "--then", "echo early; sleep 30")
        self.assertLess(time.monotonic() - began, 5)
        self.assertEqual(out.splitlines()[:2], ["early", "fleet demo · 1 live"])

    def test_without_workers_only_the_owners_line_prints(self):
        self.assertEqual(self.statusline(b"{}", "--then", "echo mine"), "mine\n")
        self.assertEqual(self.statusline(b"{}", "--bogus-flag"), "")  # never a failure, even a bad flag

    def test_a_broken_config_or_checkout_never_blanks_the_line(self):
        self.start()
        self.launch()
        with patch.object(fleet, "load_config", side_effect=fleet.FleetError("broken config")):
            self.assertIn("● 01", self.statusline())
        with patch.object(fleet, "repo_root", side_effect=fleet.FleetError("not a git repository")):
            self.assertEqual(self.statusline(b"{}", "--then", "echo mine"), "mine\n")
        with patch.object(fleet, "brief_runs", side_effect=RuntimeError("anything at all")):
            self.assertEqual(self.statusline(b"{}", "--then", "echo mine"), "mine\n")

    def test_rows_flag_caps_the_rows(self):
        self.start(tickets=[{"id": f"0{n}", "files": [f"f{n}.txt"]} for n in range(1, 4)])
        for n in range(1, 4):
            self.launch(f"0{n}")
        lines = self.statusline(b"{}", "--rows", "2").splitlines()
        self.assertEqual(lines[-1], "  +1 more: fleet status --brief")
        self.assertEqual(len(lines), 4)

    def test_the_session_directory_comes_from_the_json(self):
        here = Path.cwd()
        self.assertEqual(fleet.statusline_dir({"workspace": {"current_dir": str(self.repo)}, "cwd": "/"}), self.repo)
        self.assertEqual(fleet.statusline_dir({"cwd": str(self.repo)}), self.repo)
        for data in ({}, {"cwd": "relative/dir"}, {"cwd": "/no/such/dir"}, {"workspace": [1]}, {"cwd": None}):
            self.assertEqual(fleet.statusline_dir(data), here)
        self.assertEqual(fleet.statusline_input(b"\x00\x01"), {})


class Install(unittest.TestCase):
    """--install and --uninstall against a throwaway Claude config folder and Fleet config folder."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.claude = self.root / "claude"
        self.settings = self.claude / "settings.json"
        env = {"CLAUDE_CONFIG_DIR": str(self.claude), "XDG_CACHE_HOME": str(self.root)}
        for mocker in (patch.dict(os.environ, env),
                       patch.object(fleet, "USER_CONFIG", self.root / "fleet-config" / "config.toml"),
                       patch.object(fleet, "load_config", return_value={"defaults": dict(fleet.DEFAULTS)})):
            mocker.start()
            self.addCleanup(mocker.stop)

    def run_fleet(self, *args, expected=0):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = fleet.main(["statusline", *args])
        self.assertEqual(code, expected, out.getvalue())
        return out.getvalue()

    def line(self) -> dict:
        return json.loads(self.settings.read_text())["statusLine"]

    def backups(self) -> list[Path]:
        return sorted((self.root / "fleet-config" / "statusline-backups").glob("*"))

    def test_into_a_missing_file_and_back_out(self):
        out = self.run_fleet("--install")
        self.assertIn("did not exist; Fleet created it", out)
        line = self.line()
        self.assertEqual((line["type"], line["refreshInterval"]), ("command", 2))
        words = shlex.split(line["command"])
        self.assertEqual(words[-1], "statusline")
        self.assertEqual(Path(words[-2]).resolve(), FLEET.resolve())
        self.assertEqual(self.backups(), [])
        self.assertIn("now: (none)", self.run_fleet("--uninstall"))
        self.assertEqual(json.loads(self.settings.read_text()), {})
        self.assertIn("is not in", self.run_fleet("--uninstall"))
        self.assertIn("statusLine in", self.run_fleet("--inst"))  # an abbreviated flag installs, never just shows

    def test_it_chains_the_owners_line_keeps_every_key_and_backs_up_first(self):
        mine = "bash -c 'echo \"$(whoami)\"' # it's mine"
        original = {"model": "opus", "env": {"KEY": "secret"}, "hooks": {"Stop": []},
                    "statusLine": {"type": "command", "command": mine, "padding": 1, "refreshInterval": 9}}
        self.claude.mkdir()
        self.settings.write_text(json.dumps(original, indent=4))
        self.settings.chmod(0o640)
        raw = self.settings.read_bytes()
        out = self.run_fleet("--install")
        data = json.loads(self.settings.read_text())
        self.assertEqual({k: v for k, v in data.items() if k != "statusLine"},
                         {k: v for k, v in original.items() if k != "statusLine"})
        line = data["statusLine"]
        self.assertEqual((line["padding"], line["refreshInterval"]), (1, 2))
        words = shlex.split(line["command"])
        self.assertEqual(words[-3:], ["statusline", "--then", mine])
        self.assertEqual(stat.S_IMODE(self.settings.stat().st_mode), 0o640)
        [backup] = self.backups()
        self.assertEqual(backup.read_bytes(), raw)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)  # a settings file may hold keys
        self.assertIn(f"backup of the file as it was: {backup}", out)
        self.assertIn(f"was: {json.dumps(original['statusLine'])}", out)
        # Idempotent: a second install changes nothing and never chains Fleet to itself.
        before = self.settings.read_bytes()
        self.assertIn("already in", self.run_fleet("--install"))
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertEqual(line["command"].count("statusline"), 1)
        self.run_fleet("--uninstall")
        self.assertEqual(json.loads(self.settings.read_text()), original)  # padding and refreshInterval too
        self.assertEqual(len(self.backups()), 2)

    def test_the_installed_command_runs_the_owners_line_first(self):
        self.claude.mkdir()
        self.settings.write_text(json.dumps({"statusLine": {"type": "command", "command": "head -c 20; echo"}}))
        self.run_fleet("--install")
        res = subprocess.run(["/bin/sh", "-c", self.line()["command"]], input='{"cwd": "/"}', text=True,
                             capture_output=True, timeout=60, cwd=self.root,
                             env=dict(os.environ, XDG_CACHE_HOME=str(self.root)))
        self.assertEqual((res.returncode, res.stdout), (0, '{"cwd": "/"}\n'))

    def test_refuses_what_it_cannot_read_or_chain(self):
        self.claude.mkdir()
        for text in ("{not json", "[1, 2]", '{"statusLine": "echo hi"}', '{"statusLine": {"type": "static"}}'):
            with self.subTest(text=text):
                self.settings.write_text(text)
                out = self.run_fleet("--install", expected=1)
                self.assertIn("nothing was changed", out.lower())
                self.assertEqual(self.settings.read_text(), text)
                self.assertEqual(self.backups(), [])
        self.settings.write_text("{bad")
        self.assertIn("is not valid JSON", self.run_fleet("--uninstall", expected=1))

    def test_a_symlinked_settings_file_stays_a_link(self):
        dotfiles = self.root / "dotfiles"
        dotfiles.mkdir()
        (dotfiles / "settings.json").write_text('{"theme": "dark"}')
        self.claude.mkdir()
        self.settings.symlink_to(dotfiles / "settings.json")
        self.run_fleet("--install")
        self.assertTrue(self.settings.is_symlink())
        self.assertEqual(json.loads((dotfiles / "settings.json").read_text())["theme"], "dark")
        self.assertIn("statusLine", json.loads((dotfiles / "settings.json").read_text()))

    def test_a_moved_fleet_is_repointed_and_its_chain_kept(self):
        self.claude.mkdir()
        self.settings.write_text(json.dumps({"statusLine": {
            "type": "command", "command": "/old/place/fleet statusline --then 'my-line --fast'"}}))
        self.run_fleet("--install")
        words = shlex.split(self.line()["command"])
        self.assertEqual(Path(words[-4]).resolve(), FLEET.resolve())
        self.assertEqual(words[-2:], ["--then", "my-line --fast"])
        self.run_fleet("--uninstall")  # no record of its own: the chained command comes back
        self.assertEqual(self.line(), {"type": "command", "command": "my-line --fast"})

    def test_only_the_one_settings_file_changes(self):
        home = self.root / "home"
        for d in (".codex", ".cursor", ".gemini"):
            (home / d).mkdir(parents=True)
            (home / d / "config").write_text("theirs")
        before = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*"))
        with patch.dict(os.environ, {"HOME": str(home)}):
            self.run_fleet("--install")
        after = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*"))
        self.assertEqual(sorted(set(after) - set(before)),
                         ["claude", "claude/settings.json", "fleet-config", "fleet-config/statusline.json"])


class ProjectInstall(WatchCase):
    def test_a_project_install_waits_for_a_gitignored_file_or_no_running_workers(self):
        target = self.repo / ".claude/settings.local.json"
        with patch.object(fleet, "USER_CONFIG", Path(self.temp.name) / "fleet-config" / "config.toml"):
            out = self.call("statusline", "--install", "--scope", "project")
            self.assertIn("is not gitignored here", out)
            self.assertTrue(target.exists())
            self.call("statusline", "--uninstall", "--scope", "project")
            self.start()
            self.launch()
            out = self.call("statusline", "--install", "--scope", "project", expected=1)
            self.assertIn("run demo has workers running", out)
            self.assertEqual(json.loads(target.read_text()), {})
            (self.repo / ".gitignore").write_text(".claude/settings.local.json\n")
            self.assertIn("Undo with: fleet statusline --uninstall --scope project",
                          self.call("statusline", "--install", "--scope", "project"))
            self.assertIn("statusLine", json.loads(target.read_text()))


class Dashboard(WatchCase):
    def setUp(self):
        super().setUp()
        self.server = fleet.dashboard_server(self.repo, 0, copy.deepcopy(self.cfg))
        self.port = self.server.server_address[1]
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.token = self.server.board.token

    def request(self, path, method="GET", host="default", token=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        self.addCleanup(conn.close)
        if token:
            path += ("&" if "?" in path else "?") + f"t={self.token}"
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        if host:
            conn.putheader("Host", f"127.0.0.1:{self.port}" if host == "default" else host)
        conn.endheaders()
        r = conn.getresponse()
        return r.status, dict(r.getheaders()), r.read()

    def test_it_binds_this_computer_only_and_prints_a_tokened_link(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertRegex(self.server.board.url, rf"\Ahttp://127\.0\.0\.1:{self.port}/\?t=[A-Za-z0-9_-]{{32}}\Z")
        other = fleet.dashboard_server(self.repo, 0, None)
        self.addCleanup(other.server_close)
        self.assertNotEqual(other.board.token, self.token)  # a new token at every start
        self.assertIn("port must be between 0 and 65535", self.call("dashboard", "--port", "70000", expected=1))

    def test_the_page_and_the_api_answer_with_the_token(self):
        self.start()
        self.launch()
        code, headers, body = self.request("/")
        self.assertEqual(code, 200)
        self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
        page = body.decode()
        nonce = re.search(r"script-src 'nonce-([^']+)'", headers["Content-Security-Policy"]).group(1)
        self.assertIn(f'<script nonce="{nonce}">', page)
        self.assertIn("textContent", page)
        self.assertNotIn("innerHTML", page)
        self.assertNotRegex(page, r"<(?:link|img|iframe)\b|https?://")  # no external asset: it works offline
        self.assertIn("prefers-color-scheme:dark", page)
        code, headers, body = self.request("/api/status")
        self.assertEqual((code, headers["Content-Type"]), (200, "application/json"))
        data = json.loads(body)
        [run] = data["runs"]
        [row] = run["workers"]
        self.assertEqual(set(row), {"run", "id", "live", "state", "worker", "provider", "model", "effort", "activity",
                                    "doing", "report", "elapsed_s", "tokens", "title"})
        self.assertEqual((run["run"], row["id"], row["activity"], row["tokens"]), ("demo", "01", "working", None))
        for h in headers:
            self.assertFalse(h.lower().startswith("access-control"), h)
        self.assertEqual(self.request("/", method="HEAD")[:1], (200,))
        self.assertEqual(self.request("/", method="HEAD")[2], b"")

    def test_it_refuses_a_missing_or_wrong_token_host_or_method(self):
        self.assertEqual(self.request("/", token=False)[0], 403)
        self.assertEqual(self.request("/api/status", token=False)[0], 403)
        self.assertEqual(self.request("/api/status?t=wrong", token=False)[0], 403)
        self.assertEqual(self.request(f"/api/status?t={self.token[:-1]}", token=False)[0], 403)
        for host in (f"evil.example:{self.port}", "127.0.0.1", f"127.0.0.1:{self.port + 1}", f"localhost:{self.port}.x",
                     None):
            with self.subTest(host=host):
                self.assertEqual(self.request("/api/status", host=host)[0], 403)
        self.assertEqual(self.request("/api/status", host=f"localhost:{self.port}")[0], 200)
        for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS", "BREW"):
            with self.subTest(method=method):
                code, headers, _ = self.request("/api/status", method=method)
                self.assertEqual((code, headers.get("Allow")), (405, "GET, HEAD"))
        self.assertEqual(self.request("/elsewhere")[0], 404)
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as s:  # HTTP/1.0, no Host at all
            s.sendall(f"GET /?t={self.token} HTTP/1.0\r\n\r\n".encode())
            self.assertIn(b" 403 ", s.recv(200))

    def test_worker_strings_come_back_escaped(self):
        self.start()
        self.launch()
        self.write_report("01", "blocked", questions=["</script><script>alert(1)</script> & more"])
        (self.repo / "01.md").write_text("# <img src=x onerror=alert(1)>\n")
        code, _, body = self.request("/api/status")
        self.assertNotIn(b"<", body)
        self.assertNotIn(b">", body)
        row = json.loads(body)["runs"][0]["workers"][0]
        self.assertEqual(row["doing"], 'blocked: "</script><script>alert(1)</script> & more"')
        self.assertEqual(row["title"], "<img src=x onerror=alert(1)>")

    def test_a_workers_screen_is_read_capped_and_flattened(self):
        self.start()
        self.launch()
        self.assertEqual(json.loads(self.request("/api/screen?run=demo&id=01")[2])["screen"], "OK")  # the fake cmux
        screen = "\n".join(f"line {n}" for n in range(500)) + "\nbad\x1b[31m red‮\n"
        with patch.object(fleet.Cmux, "read", return_value=screen):
            data = json.loads(self.request("/api/screen?run=demo&id=01")[2])
        lines = data["screen"].split("\n")
        self.assertLessEqual(len(lines), fleet.DASHBOARD_SCREEN_LINES)
        self.assertEqual(lines[-1], "bad [31m red")
        with patch.object(fleet.Cmux, "read", side_effect=fleet.FleetError("surface gone")):
            self.assertEqual(json.loads(self.request("/api/screen?run=demo&id=01")[2])["error"],
                             "screen unreadable: surface gone")
        self.assertEqual(self.request("/api/screen?run=demo&id=../x")[0], 400)
        self.assertEqual(self.request("/api/screen?run=demo&id=nobody")[0], 404)
        self.assertEqual(self.request("/api/screen?run=nosuch&id=01")[0], 404)

    def test_it_takes_no_lock_and_writes_nothing(self):
        self.start()
        self.launch()
        before = self.snapshot()
        with patch.object(fleet, "try_lock", side_effect=AssertionError("took the lock")):
            for path in ("/", "/api/status", "/api/screen?run=demo&id=01", "/api/status"):
                self.assertEqual(self.request(path)[0], 200)
        self.assertEqual(self.snapshot(), before)


@unittest.skipUnless(shutil.which("tmux"), "tmux is not installed")
class LiveTmux(unittest.TestCase):
    """status --brief and the status line against a fake worker in a real tmux window."""

    def setUp(self):
        from test_backends import FAKE_AGENT
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        self.repo, self.cache = root / "repo", root / "cache"
        self.repo.mkdir()
        for args in (["init", "-q"], ["config", "user.email", "t@example.invalid"], ["config", "user.name", "t"]):
            subprocess.run(["git", *args], cwd=self.repo, check=True)
        (self.repo / "a.txt").write_text("base\n")
        subprocess.run(["git", "add", "a.txt"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=self.repo, check=True)
        agent = root / "fake-agent"
        agent.write_text(f"#!{sys.executable}\n{FAKE_AGENT}")
        agent.chmod(0o755)
        self.socket = f"fleetwatch{os.getpid()}"
        self.cfg = {"defaults": dict(fleet.DEFAULTS, backend="tmux", tmux_socket=self.socket, review="off"),
                    "providers": {"fake": {"bin": str(agent), "launch": "{bin} --model {model} {prompt}",
                                           "enabled": True, "family": "test", "max": 1, "quit": ["/quit", "enter"],
                                           "tiers": {"standard": {"model": "fake-1", "family": "test"}}}}}
        self.addCleanup(subprocess.run, ["tmux", "-L", self.socket, "kill-server"], capture_output=True)
        cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, cwd)
        for mocker in (patch.object(fleet, "load_config", side_effect=lambda: copy.deepcopy(self.cfg)),
                       patch.object(fleet, "LAUNCH_SETTLE_S", 0),
                       patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.cache)})):
            mocker.start()
            self.addCleanup(mocker.stop)

    def call(self, *args, expected=0):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = fleet.main(list(args))
        self.assertEqual(code, expected, f"fleet {' '.join(args)}\n{out.getvalue()}")
        return out.getvalue()

    def statusline(self) -> str:
        """The status line as Claude Code runs it: a separate process, elsewhere, with the session JSON on stdin."""
        payload = json.dumps({"session_id": "s", "workspace": {"current_dir": str(self.repo)}, "cwd": str(self.repo)})
        res = subprocess.run([sys.executable, str(FLEET), "statusline", "--then", "echo mine"], input=payload,
                             text=True, capture_output=True, timeout=60, cwd="/",
                             env=dict(os.environ, XDG_CACHE_HOME=str(self.cache)))
        self.assertEqual(res.returncode, 0, res.stderr)
        return res.stdout

    def plan_one(self):
        self.call("init", "demo")
        ticket = self.repo / "01.md"
        ticket.write_text("# Change a.txt\nDo it.\n")
        (self.repo / "plan.json").write_text(json.dumps({"tickets": [
            {"id": "01", "ticket": str(ticket), "files": ["a.txt"], "provider": "fake", "tier": "standard",
             "context": "none", "checks": {"manual": "manual prose review is sufficient"}}]}))
        self.call("plan", "demo", "--file", "plan.json")

    def test_a_lost_tmux_server_shows_unreachable_not_working(self):
        self.plan_one()
        self.call("launch", "demo", "--ready")
        subprocess.run(["tmux", "-L", self.socket, "kill-server"], capture_output=True)
        self.assertRegex(self.call("status", "--brief"),
                         r"  ● 01  fake:fake-1  unreachable: its tmux window is gone and it left no exit receipt")

    def test_a_worker_shows_working_then_handed_back(self):
        self.plan_one()
        self.call("launch", "demo", "--ready")
        brief = self.call("status", "--brief")
        self.assertEqual(brief.splitlines()[0], "fleet demo · 1 live")
        self.assertRegex(brief.splitlines()[1], r"\A  ● 01  fake:fake-1  working · \d+s\Z")
        shown = self.statusline()
        self.assertEqual(shown.splitlines()[:2], ["mine", "fleet demo · 1 live"])
        self.assertRegex(shown.splitlines()[2], r"\A  ● 01  fake:fake-1  working · \d+s\Z")
        self.call("send", "demo", "01", "report")
        self.assertIn("REPORT 01 [needs-verification]", self.call("wait", "demo", "--timeout", "10", "--settle", "0"))
        self.assertRegex(self.call("status", "--brief"), r"fleet demo · 1 live · 1 handed back\n"
                                                          r"  ● 01  fake:fake-1  handed back: needs-verification")
        self.assertIn("handed back: needs-verification", self.statusline())
        self.call("stop", "demo", "01")
        self.assertEqual(self.call("status", "--brief"), "")  # nothing live in this checkout
        self.assertEqual(self.statusline(), "mine\n")
        self.assertRegex(self.call("status", "demo", "--brief"),
                         r"  ○ 01  fake:fake-1  handed back: needs-verification")


if __name__ == "__main__":
    unittest.main()
