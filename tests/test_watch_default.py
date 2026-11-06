"""The owner's view with no step of their own: init's and launch's WATCH line by the lead's host, the background
dashboard (one per user, reused, stopped, idle exit), and every active Fleet session on the machine: the registry
of active checkouts, status --brief --all, the status line's summary of the other checkouts and the dashboard's list
of them. Background dashboards started here are real processes; each test that starts one stops it on cleanup."""
import argparse
import contextlib
import fcntl
import io
import json
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_watch import FLEET, WatchCase  # noqa: E402
from test_runtime import fleet  # noqa: E402

REC = {"pid": 4242, "port": 50505, "token": "tok", "nonce": "n", "url": "http://127.0.0.1:50505/?t=tok",
       "started": 1.0}
FOREIGN_SERVER = """import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"pid": os.getpid(), "nonce": "n"}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    do_HEAD = do_GET
    def log_message(self, *a):
        pass
server = HTTPServer(("127.0.0.1", 0), H)
print(server.server_address[1], flush=True)
server.serve_forever()
"""  # any process's server, here one that even answers /api/id with its own pid and the record's nonce
# What the suite inherits from a lead it may run under, and the switches these tests set themselves.
HOST_ENV = ("FLEET_NO_DASHBOARD", "CI", "FLEET_WORKER", "CLAUDE_CODE_ENTRYPOINT")


class Machine(WatchCase):
    """A checkout with a run, and helpers for other checkouts with runs of their own."""

    def other_checkout(self, name="other", parent=None, live=True) -> Path:
        repo = (parent or self.claude_home) / name
        repo.mkdir(parents=True)
        repo = repo.resolve()  # as git names it, and so the registry: on macOS /var is /private/var
        for args in (["init", "-q"], ["config", "user.email", "t@example.invalid"], ["config", "user.name", "t"]):
            subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
        (repo / "a.txt").write_text("base\n")
        subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
        ticket = repo / "t.md"
        ticket.write_text(f"# Ticket in {name}\n")
        (repo / "plan.json").write_text(json.dumps({"tickets": [
            {"id": "07", "files": ["a.txt"], "ticket": str(ticket), "provider": "writer", "tier": "standard"}]}))
        with patch.object(fleet, "repo_root", return_value=repo):
            self.last_init = self.call("init", "api", "--workspace", "workspace:1", "--review", "off", "--reason", "x")
            self.call("plan", "api", "--file", str(repo / "plan.json"))
            self.call("prompt", "api", "07")
            prompt = repo / ".fleet/runs/api/prompts/07.md"
            prompt.write_text(re.sub(r"<!--.*?-->", "Lead: inspect the diff.", prompt.read_text(), flags=re.S))
            if live:
                self.call("launch", "api", "07", "writer", "--tier", "standard")
        return repo

    def entry(self, repo: Path) -> Path:
        return self.cache / "agent-fleet" / "active" / f"{fleet.checkout_key(repo.resolve())}.json"

    @staticmethod
    def fetch(url: str) -> dict:
        """GET a dashboard URL straight to 127.0.0.1, never through a proxy."""
        import http.client
        from urllib.parse import urlsplit
        u = urlsplit(url)
        conn = http.client.HTTPConnection(u.hostname, u.port, timeout=10)
        try:
            conn.request("GET", f"{u.path}?{u.query}", headers={"Host": u.netloc})
            return json.loads(conn.getresponse().read())
        finally:
            conn.close()

    def end_worker(self, repo: Path, run="api", wid="07"):
        w = json.loads((repo / f".fleet/runs/{run}/run.json").read_text())["workers"][wid]
        Path(w["exit_file"]).write_text(json.dumps({"token": w["token"], "returncode": 0}))


class Watch(Machine):
    """init's and launch's WATCH line, by host. lead_host, the settings lookup and the dashboard are patched."""

    def init_with(self, run="demo", host=None, installed=False, env=None, started=None, **cfg) -> tuple[str, list]:
        self.cfg["defaults"].update(cfg)
        calls = []

        def start(repo):
            calls.append(repo)
            if isinstance(started, Exception):
                raise started
            return REC
        clean = {k: v for k, v in os.environ.items() if k not in HOST_ENV}
        with patch.dict(os.environ, {**clean, **(env or {})}, clear=True), \
                patch.object(fleet, "lead_host", return_value=host), \
                patch.object(fleet, "statusline_installed", return_value=installed), \
                patch.object(fleet, "read_dashboard", return_value=None), \
                patch.object(fleet, "dashboard_background", side_effect=start):
            out = self.call("init", run, "--workspace", "workspace:1", "--review", "off", "--reason", "tests")
        return out, calls

    def watch(self, out: str) -> list[str]:
        return [line for line in out.splitlines() if line.startswith(("WATCH", "note:"))]

    def test_each_host_gets_its_view(self):
        track = ("run fleet track in another terminal tab to see your workers live (fleet track --pane opens it below, "
                 "in tmux or cmux)")
        out, calls = self.init_with("a1", host="claude", installed=True)
        self.assertEqual(self.watch(out), ["WATCH your workers show under Claude Code's prompt (fleet status line) "
                                           "· in a terminal: fleet agents a1"])
        out, more = self.init_with("a2", host="claude")
        self.assertEqual(self.watch(out), [f"WATCH fleet statusline --install shows them under the prompt; meanwhile "
                                           f"{track}"])
        self.assertEqual(calls + more, [])  # a Claude Code terminal lead never gets a dashboard
        for run, host, env in (("a3", "codex", {}), ("a4", None, {}),
                               ("a5", "claude", {"CLAUDE_CODE_ENTRYPOINT": "sdk-ts"})):  # T3 Code, an app or IDE
            with self.subTest(host=host, env=env):
                out, calls = self.init_with(run, host=host, installed=True, env=env)
                self.assertEqual(self.watch(out), [f"WATCH {track} · in a desktop app: {REC['url']} (live, read-only; "
                                                   "fleet dashboard --stop ends it)"])
                self.assertEqual(len(calls), 1)

    def test_watch_off_names_only_fleet_agents(self):
        out, calls = self.init_with(host="codex", watch="off")
        self.assertEqual(self.watch(out), ["WATCH in a terminal: fleet agents demo"])
        self.assertEqual(calls, [])

    def test_nothing_starts_in_a_worker_in_ci_or_when_turned_off_for_tests(self):
        for run, env, why in (("b1", {"FLEET_WORKER": "demo/01"}, "inside a Fleet worker"),
                              ("b2", {"CI": "true"}, "in CI"),
                              ("b3", {"FLEET_NO_DASHBOARD": "1"}, "FLEET_NO_DASHBOARD is set")):
            with self.subTest(why=why):
                out, calls = self.init_with(run, host="codex", env=env)
                self.assertEqual(self.watch(out), [f"WATCH {fleet.TRACK_HINT} (no dashboard started: {why})"])
                self.assertEqual(calls, [])

    def test_a_failing_dashboard_never_fails_init(self):
        out, _ = self.init_with(host="codex", started=OSError("no space left"))
        self.assertEqual(self.watch(out), [f"WATCH {fleet.TRACK_HINT}",
                                           "note: the dashboard did not start (no space left); fleet dashboard "
                                           "--background tries again"])
        self.assertTrue((self.repo / ".fleet/runs/demo/run.json").exists())
        with patch.object(fleet, "watch_host", side_effect=RuntimeError("anything")):
            self.assertIn(f"WATCH {fleet.TRACK_HINT}",
                          self.call("init", "d2", "--workspace", "workspace:1", "--review", "off", "--reason", "x"))

    def test_launch_adds_a_line_only_while_no_view_runs(self):
        self.start(tickets=[{"id": f"0{n}", "files": [f"f{n}.txt"]} for n in range(1, 4)])
        clean = {k: v for k, v in os.environ.items() if k not in HOST_ENV}
        with patch.dict(os.environ, clean, clear=True), patch.object(fleet, "lead_host", return_value="codex"), \
                patch.object(fleet, "dashboard_background", return_value=REC) as start:
            with patch.object(fleet, "read_dashboard", return_value=REC), \
                    patch.object(fleet, "dashboard_answers", return_value=True):
                self.assertNotIn("WATCH", self.launch("01"))  # one is running: nothing to add
            with patch.object(fleet, "read_dashboard", return_value=None):
                self.assertIn(f"WATCH {fleet.TRACK_HINT} · in a desktop app: {REC['url']}", self.launch("02"))
                with patch.object(fleet, "lead_host", return_value="claude"):
                    self.assertNotIn("WATCH", self.launch("03"))  # a Claude Code lead learns it at init
            self.assertEqual(start.call_count, 1)

    def test_init_and_launch_never_split_the_owners_terminal(self):
        self.start()
        env = {k: v for k, v in os.environ.items() if k not in HOST_ENV}
        env.update(TMUX="/tmp/tmux-1000/default,4242,0", TMUX_PANE="%3", CMUX_SURFACE_ID="surface:lead")
        with patch.dict(os.environ, env, clear=True), patch.object(fleet, "lead_host", return_value="codex"), \
                patch.object(fleet, "dashboard_background", return_value=REC), \
                patch.object(fleet, "read_dashboard", return_value=None), \
                patch.object(fleet, "lead_tmux", side_effect=AssertionError("split the owner's tmux")):
            self.call("init", "other", "--workspace", "workspace:1", "--review", "off", "--reason", "x")
            self.launch()
            self.call("launch", "demo", "--ready")
        self.assertFalse([c for c in self.cmux_calls if " track" in " ".join(c)])

    def test_the_setting_is_checked(self):
        path = Path(self.temp.name) / "config.toml"
        path.write_text('[defaults]\nwatch = "always"\n')
        with self.assertRaisesRegex(fleet.FleetError, 'defaults.watch must be "auto"'):
            REAL_LOAD(path)
        path.write_text('[defaults]\nwatch = "off"\n')
        self.assertEqual(REAL_LOAD(path)["defaults"]["watch"], "off")
        self.assertEqual(REAL_LOAD(Path(self.temp.name) / "absent.toml")["defaults"]["watch"], "auto")

    def test_the_status_line_lookup_reads_user_and_project_settings(self):
        line = {"type": "command", "command": f"{fleet.Path(fleet.__file__).resolve()} statusline"}
        user = self.claude_home / "settings.json"
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.claude_home)}):
            self.assertFalse(fleet.statusline_installed(self.repo))
            user.write_text(json.dumps({"statusLine": line}))
            self.assertTrue(fleet.statusline_installed(self.repo))
            user.write_text(json.dumps({"statusLine": {"type": "command", "command": "echo mine"}}))
            self.assertFalse(fleet.statusline_installed(self.repo))
            (self.repo / ".claude").mkdir()
            (self.repo / ".claude/settings.local.json").write_text(json.dumps({"statusLine": line}))
            self.assertTrue(fleet.statusline_installed(self.repo))


    def test_the_line_claude_code_shows_decides_in_its_order(self):
        ours, mine = {"type": "command", "command": fleet.statusline_command(None)}, {"type": "command", "command": "x"}
        project = self.repo / ".claude"
        project.mkdir()
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.claude_home)}):
            (self.claude_home / "settings.json").write_text(json.dumps({"statusLine": ours}))
            (project / "settings.json").write_text(json.dumps({"statusLine": mine}))
            self.assertFalse(fleet.statusline_installed(self.repo))  # the project's own line hides Fleet's user line
            (project / "settings.local.json").write_text(json.dumps({"statusLine": ours}))
            self.assertTrue(fleet.statusline_installed(self.repo))  # settings.local.json comes first
            (project / "settings.local.json").write_text(json.dumps({"statusLine": mine}))
            (project / "settings.json").write_text(json.dumps({"statusLine": ours}))
            self.assertFalse(fleet.statusline_installed(self.repo))
            (project / "settings.local.json").write_text(json.dumps({"model": "opus"}))  # sets none: the next decides
            self.assertTrue(fleet.statusline_installed(self.repo))
            (project / "settings.json").write_text("{}")
            self.assertTrue(fleet.statusline_installed(self.repo))  # down to the user's


REAL_LOAD = fleet.load_config  # RuntimeCase patches it per test


class Registry(Machine):
    def test_init_and_launch_record_the_checkout_owner_only(self):
        self.start()
        f = self.entry(self.repo)
        e = json.loads(f.read_text())
        self.assertEqual((e["checkout"], e["runs"]), (str(self.repo), ["demo"]))
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(f.parent.stat().st_mode), 0o700)
        seen = e["seen"]
        time.sleep(0.01)
        self.launch()
        self.assertGreater(json.loads(f.read_text())["seen"], seen)
        self.assertFalse(f.resolve().is_relative_to(self.repo))  # never inside the checkout
        with patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.repo / ".cache")}):  # as when the checkout is ~
            self.launch("02")
        self.assertFalse((self.repo / ".cache").exists())

    def test_reads_take_no_lock_and_skip_or_drop_what_is_stale_or_damaged(self):
        self.start()
        self.launch()
        live_old = self.other_checkout("old-but-live")
        idle_old = self.other_checkout("old-and-idle", live=False)
        gone = self.other_checkout("gone")
        for repo in (live_old, idle_old):
            e = json.loads(self.entry(repo).read_text())
            self.entry(repo).write_text(json.dumps(dict(e, seen=time.time() - 2 * 86400)))
        import shutil
        shutil.rmtree(gone)
        home = self.cache / "agent-fleet" / "active"
        (home / "garbage.json").write_text("{not json")
        (home / "wrong-name.json").write_text(json.dumps({"checkout": str(self.repo), "seen": time.time()}))
        (home / "nested.json").write_text("[" * 100_000)
        with patch.object(fleet, "try_lock", side_effect=AssertionError("took the lock")):
            listed = [e["checkout"] for e in fleet.active_entries()]
        self.assertEqual(sorted(listed), sorted([str(self.repo), str(live_old)]))
        self.assertFalse(self.entry(gone).exists())
        self.assertFalse(self.entry(idle_old).exists())
        self.assertTrue((home / "garbage.json").exists())  # damaged: skipped, never removed or fatal


    def test_an_entry_unseen_for_a_week_is_dropped_even_while_its_run_says_running(self):
        self.start()
        other = self.other_checkout()
        e = json.loads(self.entry(other).read_text())
        self.entry(other).write_text(json.dumps(dict(e, seen=time.time() - 8 * 86400)))
        self.assertNotIn(str(other), [x["checkout"] for x in fleet.active_entries()])
        self.assertFalse(self.entry(other).exists())


class AllCheckouts(Machine):
    def test_all_groups_by_checkout_this_one_first(self):
        self.start()
        self.launch()
        zeta, alpha = self.other_checkout("zeta"), self.other_checkout("alpha")
        self.other_checkout("quiet", live=False)
        out = self.brief("--all")
        heads = [line for line in out.splitlines() if line.startswith("fleet ")]
        self.assertEqual(heads, [f"fleet {self.repo.name}/demo · 1 live", "fleet alpha/api · 1 live",
                                 "fleet zeta/api · 1 live"])
        data = json.loads(self.call("status", "--brief", "--all", "--json"))
        self.assertEqual([(g["label"], g["current"]) for g in data["checkouts"]],
                         [(self.repo.name, True), ("alpha", False), ("zeta", False)])
        self.assertEqual(data["checkouts"][1]["checkout"], str(alpha))
        self.end_worker(zeta)
        self.assertNotIn("zeta", self.brief("--all"))
        self.assertIn("--all lists every active checkout", self.call("status", "demo", "--brief", "--all", expected=1))

    def test_two_checkouts_with_one_name_show_their_paths(self):
        self.start()
        one = self.other_checkout("app", parent=self.claude_home / "a")
        two = self.other_checkout("app", parent=self.claude_home / "b")
        labels = {g["checkout"]: g["label"] for g in
                  json.loads(self.call("status", "--brief", "--all", "--json"))["checkouts"]}
        self.assertEqual(labels[str(one)], fleet.one_line(f"app ({one})", 120))  # cut where a temp path is long
        self.assertEqual(labels[str(two)], fleet.one_line(f"app ({two})", 120))

    def test_a_folder_name_never_reaches_the_terminal_raw(self):
        self.start()
        self.other_checkout("ev\x1b[31mil\u202e")
        out = self.brief("--all")
        self.assertNotRegex(out, "[\x1b\u202e]")
        self.assertIn("fleet ev [31mil/api · 1 live", out)
        lines, _ = fleet.track_frame(self.repo, argparse.Namespace(run=None), None, fleet.TokenCache())
        self.assertNotRegex("\n".join(lines), "[\x1b\u202e]")
        [group] = json.loads(self.call("status", "--brief", "--all", "--json"))["checkouts"]
        self.assertEqual(group["label"], "ev [31mil")

    def test_the_current_checkout_without_live_workers_is_left_out(self):
        self.start()
        other = self.other_checkout()
        groups = json.loads(self.call("status", "--brief", "--all", "--json"))["checkouts"]
        self.assertEqual([g["checkout"] for g in groups], [str(other)])


class StatuslineSummary(Machine):
    def test_one_line_sums_the_other_checkouts_after_this_ones_rows(self):
        self.start(tickets=[{"id": f"0{n}", "files": [f"f{n}.txt"]} for n in range(1, 4)])
        for n in range(1, 4):
            self.launch(f"0{n}")
        self.assertNotIn("other repo", self.statusline())  # no other checkout is live
        self.other_checkout("one")
        self.other_checkout("two")
        with patch.object(fleet, "OTHERS_TTL_S", 0):
            lines = self.statusline(b"{}", "--rows", "2").splitlines()
        self.assertEqual(lines[0], "fleet demo · 3 live")
        self.assertEqual(lines[-2:], ["+1 more: fleet status --brief",
                                      "+2 workers in 2 other repos (fleet status --brief --all)"])
        self.assertEqual(len(lines), 5)  # the cap still holds this checkout's rows

    def test_counts_are_reused_briefly(self):
        self.start()
        self.launch()
        self.other_checkout()
        real, counted = fleet.live_workers, []
        with patch.object(fleet, "live_workers", side_effect=lambda r, *rest: counted.append(r) or real(r, *rest)):
            self.statusline()
            first = len(counted)
            self.statusline()
        self.assertGreater(first, 0)
        self.assertEqual(len(counted), first)  # the second refresh read the kept counts


class MachineDashboard(Machine):
    def setUp(self):
        super().setUp()
        import threading
        self.server = fleet.dashboard_server(self.repo, 0, None)
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def get(self, path, **query):
        import http.client
        from urllib.parse import urlencode
        port = self.server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        self.addCleanup(conn.close)
        conn.request("GET", path + "?" + urlencode({"t": self.server.board.token, **query}),
                     headers={"Host": f"127.0.0.1:{port}"})
        r = conn.getresponse()
        return r.status, json.loads(r.read())

    def test_it_lists_every_active_checkout_and_their_screens(self):
        self.start()
        self.launch()
        other = self.other_checkout()
        code, data = self.get("/api/status")
        self.assertEqual(code, 200)
        self.assertEqual([g["checkout"] for g in data["checkouts"]], [str(self.repo), str(other)])
        self.assertEqual(data["checkouts"][1]["runs"][0]["workers"][0]["id"], "07")
        code, screen = self.get("/api/screen", checkout=str(other), run="api", id="07")
        self.assertEqual((code, screen["screen"], screen["checkout"]), (200, "OK", str(other)))

    def test_a_screen_is_refused_for_any_checkout_the_registry_does_not_list(self):
        self.start()
        self.launch()
        other = self.other_checkout()
        stranger = self.other_checkout("stranger")
        self.entry(stranger).unlink()  # a real Fleet checkout, but not registered
        for checkout in (str(stranger), "/etc", f"{other}/../{other.name}", f"{other}/", str(other).upper(),
                         str(other) + "\x00"):
            with self.subTest(checkout=checkout):
                code, data = self.get("/api/screen", checkout=checkout, run="api", id="07")
                self.assertEqual((code, data["error"]), (403, "refused: not an active Fleet checkout on this computer"))


class Background(Machine):
    """Real detached dashboards, each stopped on cleanup."""

    def setUp(self):
        super().setUp()
        # every variable that stops init's dashboard goes, CI's own CI=true included
        self.env = patch.dict(os.environ, {k: v for k, v in os.environ.items() if k not in HOST_ENV}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(fleet.dashboard_stop)

    def wait(self, check, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if check():
                return True
            time.sleep(0.1)
        return False

    def test_one_background_dashboard_serves_every_checkout_and_is_reused_then_stopped(self):
        self.start()
        self.launch()
        other = self.other_checkout()
        rec = fleet.dashboard_background(self.repo)
        self.assertEqual(fleet.dashboard_background(other)["pid"], rec["pid"])  # reused, from any checkout
        record = self.cache / "agent-fleet" / "dashboard" / "dashboard.json"
        self.assertEqual(stat.S_IMODE(record.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(record.parent.stat().st_mode), 0o700)
        data = self.fetch(rec["url"].replace("/?t=", "/api/status?t="))
        self.assertEqual({g["checkout"] for g in data["checkouts"]}, {str(self.repo), str(other)})
        self.assertNotIn(rec["token"], (record.parent / "dashboard.log").read_text())
        out = self.call("dashboard", "--background")
        self.assertIn(rec["url"], out)  # the same link
        self.assertIn(f"stopped the background dashboard (pid {rec['pid']})", self.call("dashboard", "--stop"))
        self.assertFalse(fleet.dashboard_answers(rec))
        self.assertFalse(record.exists())
        self.assertIn("no background dashboard is running", self.call("dashboard", "--stop"))

    def test_init_starts_it_and_a_second_checkouts_init_reuses_it(self):
        with patch.object(fleet, "lead_host", return_value="codex"):
            out = self.call("init", "demo", "--workspace", "workspace:1", "--review", "off", "--reason", "x")
            url = re.search(r"in a desktop app: (http://\S+) \(live, read-only", out).group(1)
            pid = fleet.read_dashboard()["pid"]
            self.other_checkout(live=False)
        self.assertIn(f"in a desktop app: {url} (live", self.last_init)  # the other checkout's init: the same one
        self.assertEqual(fleet.read_dashboard()["pid"], pid)

    def foreign(self, *cmd) -> subprocess.Popen:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.addCleanup(p.wait)
        self.addCleanup(p.kill)
        return p

    def test_a_record_naming_another_process_is_never_trusted_or_signalled(self):
        """After an unclean exit the record's pid and port can belong to anything: a sleep, someone's dev server."""
        sleeper = self.foreign("sleep", "60")
        server = self.foreign(sys.executable, "-c", FOREIGN_SERVER)
        port = int(server.stdout.readline())
        for pid in (sleeper.pid, server.pid):
            with self.subTest(pid=pid):
                rec = {"pid": pid, "port": port, "token": "tok", "nonce": "n", "url": f"http://127.0.0.1:{port}/?t=tok",
                       "started": 1.0}
                fleet.write_private(fleet.private_dir(fleet.dashboard_home()) / "dashboard.json", json.dumps(rec))
                self.assertFalse(fleet.dashboard_answers(rec))
                out = self.call("dashboard", "--stop")
                self.assertIn(f"its record (pid {pid}) named a process that is gone or not Fleet's, so it was "
                              "dropped and nothing was signalled", out)
                self.assertIsNone(fleet.read_dashboard())
                self.assertEqual((sleeper.poll(), server.poll()), (None, None))  # both still running
        fleet.write_private(fleet.dashboard_home() / "dashboard.json", json.dumps(dict(rec, pid=server.pid)))
        new = fleet.dashboard_background(self.repo)  # init starts a dashboard of its own, never trusts that one
        self.assertNotIn(new["pid"], (sleeper.pid, server.pid))
        self.assertTrue(fleet.dashboard_answers(new))

    def test_starting_never_looks_up_a_host_name(self):
        """HTTPServer's own bind calls socket.getfqdn, which stalls for seconds on a Mac with slow reverse DNS."""
        import socket
        with patch.object(socket, "getfqdn", side_effect=AssertionError("looked up a host name")):
            server = fleet.dashboard_server(self.repo, 0, None)
        self.addCleanup(server.server_close)
        self.assertEqual(server.server_address[0], "127.0.0.1")

    def test_another_start_or_a_reused_pid_is_told_apart(self):
        rec = fleet.dashboard_background(self.repo)
        self.assertTrue(fleet.dashboard_answers(rec))
        self.assertEqual(rec["proc_start"], fleet.process_start(rec["pid"]))
        self.assertFalse(fleet.dashboard_answers(dict(rec, nonce="an earlier start")))
        self.assertFalse(fleet.dashboard_answers(dict(rec, proc_start="1")))  # the pid, reused by a newer process

    def test_a_dashboard_too_slow_to_answer_is_stopped_not_left_running(self):
        started, real = [], subprocess.Popen

        def popen(*args, **kwargs):
            p = real(*args, **kwargs)
            started.append(p)
            self.addCleanup(p.wait)
            self.addCleanup(lambda: p.poll() is None and p.kill())
            return p
        with patch.object(fleet.subprocess, "Popen", side_effect=popen), patch.object(fleet, "DASHBOARD_START_S", 0.01):
            with self.assertRaisesRegex(fleet.FleetError, "did not answer within 0.01s"):
                fleet.dashboard_background(self.repo)
        [child] = started
        self.assertIsNotNone(child.poll())
        self.assertIsNone(fleet.read_dashboard())

    def test_no_dashboard_file_lands_inside_a_home_checkout(self):
        self.start()
        inside = {"XDG_CACHE_HOME": str(self.repo / ".cache")}  # as when the checkout is the home folder

        def stop():  # only a Fleet that started one there leaves anything to stop
            with patch.dict(os.environ, inside):
                fleet.dashboard_stop()
        self.addCleanup(stop)
        with patch.dict(os.environ, inside), patch.object(fleet, "watch_host", return_value="codex"):
            lines = fleet.watch_lines(self.repo, self.cfg, "demo", True)
            with self.assertRaisesRegex(fleet.FleetError, "no dashboard: its files"):
                fleet.dashboard_background(self.repo)
            self.assertIn("no background dashboard is running", self.call("dashboard", "--stop"))
        self.assertEqual(lines, [f"WATCH {fleet.TRACK_HINT} (no dashboard: its files would land inside this "
                                 "checkout)"])
        self.assertFalse((self.repo / ".cache").exists())

    def test_it_stops_itself_once_no_active_checkout_has_a_live_worker(self):
        with patch.dict(os.environ, {"FLEET_NO_DASHBOARD": "1"}):  # so these inits start none with the defaults
            self.start()
            other = self.other_checkout()  # live there, none here: the dashboard stays
        with patch.object(fleet, "DASHBOARD_IDLE_S", 1), patch.object(fleet, "DASHBOARD_CHECK_S", 0.2):
            rec = fleet.dashboard_background(self.repo)
        time.sleep(2.5)
        self.assertTrue(fleet.dashboard_answers(rec))
        self.end_worker(other)
        log = self.cache / "agent-fleet" / "dashboard" / "dashboard.log"
        self.assertTrue(self.wait(lambda: not fleet.dashboard_answers(rec)), log.read_text())
        self.assertTrue(self.wait(lambda: fleet.read_dashboard() is None))
        self.assertIn("no live worker in any active checkout; stopping", log.read_text())


class StopRaces(Machine):
    """--stop against a dashboard served in this process: its pid is this test's, so the signal is caught here."""

    def serve(self) -> dict:
        server = fleet.dashboard_server(self.repo, 0, None)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        b = server.board
        rec = {"pid": os.getpid(), "proc_start": fleet.process_start(os.getpid()), "port": b.port, "token": b.token,
               "nonce": b.nonce, "url": b.url, "started": time.time()}
        fleet.write_private(fleet.private_dir(fleet.dashboard_home()) / "dashboard.json", json.dumps(rec))
        return rec

    def test_a_dashboard_still_shutting_down_keeps_its_record(self):
        rec = self.serve()
        sent, real = [], os.kill

        def kill(pid, sig):
            return real(pid, sig) if sig == 0 else sent.append((pid, sig))
        with patch.object(fleet.os, "kill", side_effect=kill), patch.object(fleet, "DASHBOARD_STOP_S", 0.3):
            out = fleet.dashboard_stop()
        self.assertEqual(sent, [(os.getpid(), signal.SIGTERM)])
        self.assertEqual(out, f"asked the background dashboard (pid {os.getpid()}) to stop; it is still shutting down")
        self.assertEqual(fleet.read_dashboard(), rec)  # so the next init reuses it rather than start a second one

    def test_stop_waits_for_the_dashboard_lock(self):
        dead = subprocess.Popen(["true"])
        dead.wait()
        rec = {"pid": dead.pid, "port": 9, "token": "t", "nonce": "n", "url": "http://127.0.0.1:9/?t=t", "started": 1}
        fleet.write_private(fleet.private_dir(fleet.dashboard_home()) / "dashboard.json", json.dumps(rec))
        held = open(fleet.dashboard_home() / "dashboard.lock", "a")
        fcntl.flock(held, fcntl.LOCK_EX)  # a start in progress
        said = []
        stopper = threading.Thread(target=lambda: said.append(fleet.dashboard_stop()))
        stopper.start()
        time.sleep(0.5)
        self.assertEqual(said, [])
        held.close()
        stopper.join(10)
        self.assertIn("nothing was signalled", said[0])

    def test_a_record_is_dropped_only_while_it_names_that_dashboard(self):
        rec = self.serve()
        fleet.forget_dashboard(dict(rec, nonce="an earlier start"))
        fleet.forget_dashboard(dict(rec, pid=rec["pid"] + 1))
        self.assertEqual(fleet.read_dashboard(), rec)  # a newer dashboard's record stays
        fleet.forget_dashboard(rec)
        self.assertIsNone(fleet.read_dashboard())


class TtyIO(io.StringIO):
    def isatty(self) -> bool:
        return True


CSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")  # a terminal's control sequences, as fleet track draws with them


class OwnerTerminal(Machine):
    """The owner's own terminal: none of an agent's shell variables (the suite may run under an agent, or in a
    Fleet worker), and wide enough that no row is cut."""

    def setUp(self):
        super().setUp()
        env = {k: v for k, v in os.environ.items() if k not in fleet.AGENT_SHELL_ENV}
        self.env = patch.dict(os.environ, {**env, "COLUMNS": "250", "LINES": "60"}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)


class Track(OwnerTerminal):
    """fleet track: the live view in a person's terminal, one frame wherever its output goes to an agent, and
    --pane, with the owner's tmux runner patched."""

    def track(self, *args, stdout=None, frames=None) -> str:
        """Run fleet track, on a terminal unless `stdout` says otherwise; with `frames`, Ctrl-C arrives after that
        many redraws. self.sleeps holds the pauses between redraws."""
        out = TtyIO() if stdout is None else stdout
        self.sleeps = []

        def sleep(seconds):
            self.sleeps.append(seconds)
            if frames is not None and len(self.sleeps) >= frames:
                raise KeyboardInterrupt
            real_sleep(min(seconds, 0.01))
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), \
                patch.object(fleet.time, "sleep", side_effect=sleep):
            code = fleet.main(["track", *args])
        self.assertEqual(code, 0, out.getvalue())
        return out.getvalue()

    def test_it_redraws_every_active_checkout_until_ctrl_c(self):
        self.start()
        self.launch()
        other = self.other_checkout()
        out = CSI.sub("", self.track("--interval", "0.5", frames=3))
        frames = [f for f in re.split(r"(?m)^(?=fleet track · )", out) if f]
        self.assertEqual(len(frames), 3)
        for frame in frames:
            self.assertRegex(frame, r"\Afleet track · \d\d:\d\d:\d\d · every 0.5s · Ctrl-C quits\n")
            self.assertIn(f"fleet {self.repo.name}/demo · 1 live", frame)
            self.assertIn(f"fleet {other.name}/api · 1 live", frame)
            self.assertIn("● 01", frame)
        narrowed = self.track("--run", "demo", frames=1)
        self.assertIn("fleet demo · 1 live", narrowed)
        self.assertNotIn(other.name, narrowed)

    def test_it_ends_after_the_idle_minutes_and_writes_nothing(self):
        self.start()
        before = self.snapshot()
        with patch.object(fleet, "try_lock", side_effect=AssertionError("took the lock")):
            out = self.track("--interval", "0.01", "--exit-when-idle", "0.001")
        self.assertIn("no Fleet workers live", out)
        self.assertIn("ends after 0.001 min with none live", out)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(any((self.repo / ".fleet").rglob("*tokens*")))

    def test_without_a_terminal_it_prints_one_frame_and_ends(self):
        """Piped, as an agent's shell tool runs a command (Claude Code's, Codex's): one frame, never a stream of
        frames into the agent's context."""
        self.start()
        self.launch()
        before = self.snapshot()
        out = self.track("--interval", "0.01", stdout=io.StringIO())
        self.assertEqual(self.sleeps, [])  # no redraw ever waited for
        self.assertNotIn("\x1b", out)
        head, blank, *rows = out.splitlines()
        self.assertRegex(head, r"\Afleet track · \d\d:\d\d:\d\d · printed once: not a terminal\. The live view is for "
                               r"your own terminal: fleet track there, or fleet track --pane\Z")
        self.assertEqual(blank, "")
        self.assertTrue(any("● 01" in row for row in rows), out)
        self.assertEqual(out.count("fleet track · "), 1)
        self.assertEqual(self.snapshot(), before)

    def test_in_an_agents_shell_it_prints_one_frame_even_on_a_terminal(self):
        """Gemini CLI, Cursor's agent and Codex can run a command on a terminal: their shell's variables say whose
        it is."""
        self.start()
        for name in fleet.AGENT_SHELL_ENV:
            with self.subTest(name=name), patch.dict(os.environ, {name: "1"}):
                out = self.track("--interval", "0.01", "--exit-when-idle", "5")
                self.assertEqual(self.sleeps, [])
                self.assertNotIn("\x1b", out)
                self.assertIn(f" · printed once: an agent's shell ({name} is set). The live view is for your own "
                              "terminal", out)
                self.assertIn("no Fleet workers live", out)
        with patch.dict(os.environ, {"AI_AGENT": ""}):  # set but empty: no agent's
            self.assertTrue(self.track(frames=1).startswith("\x1b[?1049h"))

    def test_a_terminal_gets_the_alternate_screen_back_on_ctrl_c(self):
        self.start()
        self.launch()
        out = self.track(stdout=TtyIO(), frames=2)
        self.assertTrue(out.startswith("\x1b[?1049h\x1b[?25l\x1b[H"))
        self.assertTrue(out.endswith("\x1b[?25h\x1b[?1049l"))
        self.assertEqual(out.count("\x1b[H"), 2)  # each frame drawn over the last, never a scroll of frames
        self.assertNotIn("\x1b[2J", out)  # no full clear between frames: no flicker

    def in_pty(self, sig, argv=None, env=None) -> tuple[int, bytes]:
        """fleet track (or `argv`) on a real terminal, sent `sig` once it has drawn a frame (with None, left to end
        by itself): its exit code and all it wrote."""
        import pty
        import select
        master, slave = pty.openpty()
        argv = argv or [sys.executable, str(FLEET), "track", "--interval", "0.2"]
        p = subprocess.Popen(argv, cwd=self.repo, stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
                             env=env)
        os.close(slave)
        self.addCleanup(os.close, master)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        out = bytearray()

        def read(until, timeout) -> None:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and not until():
                if select.select([master], [], [], 0.1)[0]:
                    try:
                        chunk = os.read(master, 1 << 16)
                    except OSError:  # the terminal's other end closed
                        return
                    if not chunk:
                        return
                    out.extend(chunk)
        read(lambda: "fleet track ·".encode() in out, 60)
        if sig is not None:
            p.send_signal(sig)
        read(lambda: p.poll() is not None, 30)
        read(lambda: False, 0.3)
        return p.wait(10), bytes(out)

    def test_a_terminal_gets_the_screen_back_on_sigterm_and_sighup(self):
        self.start()
        for sig in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(sig=sig.name):
                code, out = self.in_pty(sig)
                self.assertTrue(out.startswith(b"\x1b[?1049h\x1b[?25l"), out[:80])
                self.assertEqual(code, 0, out[-200:])
                self.assertTrue(out.endswith(b"\x1b[?25h\x1b[?1049l"), out[-80:])
                self.assertEqual(out.count(b"\x1b[?1049l"), 1)  # put back once

    def test_an_agent_on_a_real_terminal_gets_one_frame(self):
        self.start()
        code, out = self.in_pty(None, env={**os.environ, "GEMINI_CLI": "1"})
        self.assertEqual(code, 0, out[-200:])
        self.assertEqual(out.count("fleet track ·".encode()), 1)
        self.assertIn(b"printed once: an agent's shell (GEMINI_CLI is set)", out)
        self.assertNotIn(b"\x1b[?1049h", out)  # never the live view's screen

    def test_the_pane_stays_live_whatever_agent_variables_its_server_holds(self):
        """A tmux server an agent started holds that agent's variables, and its panes start with them."""
        self.start()
        a = argparse.Namespace(interval=0.2, exit_when_idle=None, run=None)
        agent = {name: "1" for name in fleet.AGENT_SHELL_ENV}
        code, out = self.in_pty(signal.SIGTERM, argv=["/bin/sh", "-c", f"exec {fleet.track_command(a)}"],
                                env={**os.environ, **agent})
        self.assertEqual(code, 0, out[-200:])
        self.assertTrue(out.startswith(b"\x1b[?1049h\x1b[?25l"), out[:120])  # the live view
        self.assertNotIn(b"printed once", out)

    def test_the_pane_carries_the_callers_fleet_environment(self):
        a = argparse.Namespace(interval=2.0, exit_when_idle=None, run="demo")
        words = shlex.split(fleet.track_command(a, env={"XDG_CACHE_HOME": "/my cache", "FLEET_NO_DASHBOARD": "1",
                                                        "FLEET_WORKER": "demo/01", "CLAUDECODE": "1",
                                                        "AI_AGENT": "codex", "HOME": "/h"}))
        agents = [x for name in fleet.AGENT_SHELL_ENV for x in ("-u", name)]  # never an agent's: the owner's pane
        self.assertEqual(words[:words.index(sys.executable)], ["env", "-u", "FLEET_CONFIG", *agents,
                                                               "FLEET_NO_DASHBOARD=1", "XDG_CACHE_HOME=/my cache"])
        self.assertEqual(words[words.index(sys.executable) + 2:], ["track", "--interval", "2", "--exit-when-idle", "30",
                                                                   "--run", "demo"])

    def test_pane_records_of_panes_that_are_gone_are_dropped(self):
        self.start()
        sock, record = "/tmp/tmux-1000/mine", self.cache / "agent-fleet" / "track-panes.json"
        fleet.private_dir(record.parent)
        record.write_text(json.dumps({f"tmux:{sock}:999:%2": {"kind": "tmux", "pane": "%5"},  # its pane closed
                                      f"tmux:{sock}:888:%3": {"kind": "tmux", "pane": "%9"},  # an earlier server's
                                      "tmux:/other:1:%1": {"kind": "tmux", "pane": "%1"}}))  # not this server's

        def run(socket, *args):
            if args[0] == "split-window":
                return subprocess.CompletedProcess(args, 0, "%11\n", "")
            if args[0] == "list-panes":
                return subprocess.CompletedProcess(args, 0, "%7\n%9\n%11\n", "")
            return subprocess.CompletedProcess(args, 1, "", "can't find pane")
        with patch.dict(os.environ, {"TMUX": f"{sock},999,0", "TMUX_PANE": "%7"}), \
                patch.object(fleet, "lead_tmux", side_effect=run):
            self.assertIn("opened fleet track in the pane below (%11)", self.track("--pane"))
        self.assertEqual(sorted(json.loads(record.read_text())), ["tmux:/other:1:%1", f"tmux:{sock}:999:%7"])

    def test_stale_cmux_records_never_keep_a_pane_from_a_worker(self):
        record = self.cache / "agent-fleet" / "track-panes.json"
        fleet.private_dir(record.parent)
        record.write_text(json.dumps({
            "cmux:workspace:1:surface:2": {"kind": "cmux", "workspace": "workspace:1", "surface": "surface:3",
                                           "surface_id": "uuid-gone"},  # closed; surface:3 is someone else's now
            "cmux:workspace:1:surface:9": {"kind": "cmux", "workspace": "workspace:1", "surface": "surface:1",
                                           "surface_id": "uuid-mine"},  # still open, its ref changed since
            "cmux:workspace:2:surface:1": {"kind": "cmux", "workspace": "workspace:2", "surface": "surface:1"}}))
        panes = [{"ref": "pane:1", "surface_refs": ["surface:3", "surface:7"], "surface_ids": ["uuid-x", "uuid-mine"]}]
        self.assertEqual(fleet.track_surfaces("workspace:1", panes), {"surface:7"})
        self.assertEqual(sorted(json.loads(record.read_text())), ["cmux:workspace:1:surface:9",
                                                                  "cmux:workspace:2:surface:1"])

    def test_no_pane_record_lands_inside_a_home_checkout(self):
        self.start()
        calls, run = self.lead_tmux()
        env = {"TMUX": "/tmp/tmux-1000/mine,999,0", "TMUX_PANE": "%7", "XDG_CACHE_HOME": str(self.repo / ".cache")}
        with patch.dict(os.environ, env), patch.object(fleet, "lead_tmux", side_effect=run):
            self.assertIn("opened fleet track", self.track("--pane"))
        self.assertFalse((self.repo / ".cache").exists())

    def lead_tmux(self, alive=True):
        calls = []

        def run(socket, *args):
            calls.append((socket, *args))
            if args[0] == "split-window":
                return subprocess.CompletedProcess(args, 0, "%9\n", "")
            if args[0] == "display-message" and alive and any(c[1] == "split-window" for c in calls):
                return subprocess.CompletedProcess(args, 0, f"%9\t0\t{fleet.sys.executable} fleet track\n", "")
            return subprocess.CompletedProcess(args, 1, "", "can't find pane")
        return calls, run

    def test_pane_splits_below_on_the_owners_own_tmux_without_focus_and_is_reused(self):
        self.start()
        calls, run = self.lead_tmux()
        env = {"TMUX": "/tmp/tmux-1000/mine,999,0", "TMUX_PANE": "%7"}
        with patch.dict(os.environ, env), patch.object(fleet, "lead_tmux", side_effect=run):
            self.assertIn("opened fleet track in the pane below (%9), without focus", self.track("--pane"))
            self.assertIn("fleet track already runs in the pane below (%9)", self.track("--pane"))
        splits = [c for c in calls if c[1] == "split-window"]
        self.assertEqual(len(splits), 1)  # reused, never a second pane
        socket, *args = splits[0]
        self.assertEqual(socket, "/tmp/tmux-1000/mine")  # the owner's server, not Fleet's -L fleet one
        self.assertEqual(args[:12], ["split-window", "-v", "-d", "-l", "8", "-t", "%7", "-c", str(self.repo),
                                     "-P", "-F", "#{pane_id}"])
        words = shlex.split(args[12])
        self.assertEqual(words[0], "env")  # the pane gets the tmux server's environment, so Fleet's go with it
        self.assertEqual(words[words.index(sys.executable):], [sys.executable, str(FLEET.resolve()), "track",
                                                               "--interval", "2", "--exit-when-idle", "30"])
        record = self.cache / "agent-fleet" / "track-panes.json"
        self.assertEqual(stat.S_IMODE(record.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(record.parent.stat().st_mode), 0o700)
        calls, run = self.lead_tmux(alive=False)  # the pane closed: the next --pane opens one again
        with patch.dict(os.environ, env), patch.object(fleet, "lead_tmux", side_effect=run):
            self.assertIn("opened fleet track", self.track("--pane"))

    def test_pane_in_cmux_splits_down_without_focus_and_keeps_worker_tabs_out(self):
        self.start()
        env = {"CMUX_WORKSPACE_ID": "workspace:1", "CMUX_SURFACE_ID": "surface:lead"}
        env_clean = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}
        with patch.dict(os.environ, {**env_clean, **env}, clear=True):
            self.assertIn("opened fleet track in the pane below (surface:1)", self.track("--pane"))
            self.assertIn("already runs", self.track("--pane"))
        [split] = [c for c in self.cmux_calls if c[0] == "new-split"]
        self.assertEqual(split[:6], ("new-split", "down", "--workspace", "workspace:1", "--surface", "surface:lead"))
        self.assertEqual(split[-2:], ("--focus", "false"))
        self.assertIn(" track --interval 2 --exit-when-idle 30", split[split.index("--command") + 1])
        self.assertEqual(fleet.track_surfaces("workspace:1", fleet.list_panes("workspace:1")), {"surface:1"})
        self.assertEqual(fleet.track_surfaces("workspace:2", fleet.list_panes("workspace:1")), set())

    def test_pane_outside_tmux_or_cmux_says_to_use_another_tab(self):
        env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE", "CMUX_WORKSPACE_ID",
                                                                "CMUX_SURFACE_ID")}
        with patch.dict(os.environ, env, clear=True):
            out = self.call("track", "--pane", expected=1)
        self.assertIn("is neither", out)
        self.assertIn("run fleet track in a second tab or window instead", out)


real_sleep = time.sleep


@unittest.skipUnless(shutil.which("tmux"), "tmux is not installed")
class TrackPaneLive(Machine):
    """track --pane for real, on a throwaway tmux server standing in for the owner's own."""

    def test_the_pane_opens_below_shows_the_rows_and_is_reused(self):
        self.start()
        self.launch()
        other = self.other_checkout()  # found through this test's registry only
        sock = f"fleetlead{os.getpid()}"
        self.addCleanup(subprocess.run, ["tmux", "-L", sock, "kill-server"], capture_output=True)
        elsewhere = Path(self.temp.name) / "server-cache"  # the owner's server started with another cache
        subprocess.run(["tmux", "-L", sock, "new-session", "-d", "-s", "lead", "-x", "160", "-y", "40"], check=True,
                       env=dict(os.environ, XDG_CACHE_HOME=str(elsewhere), CLAUDECODE="1"))  # an agent started it
        socket, pid, pane = subprocess.run(["tmux", "-L", sock, "display-message", "-p", "-t", "lead",
                                            "#{socket_path}\t#{pid}\t#{pane_id}"], capture_output=True, text=True,
                                           check=True).stdout.strip().split("\t")
        env = dict(os.environ, TMUX=f"{socket},{pid},0", TMUX_PANE=pane)
        run = [sys.executable, str(FLEET), "track", "--pane"]
        first = subprocess.run(run, cwd=self.repo, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(first.returncode, 0, first.stderr)
        new = re.search(r"\((%\d+)\)", first.stdout).group(1)
        panes = subprocess.run(["tmux", "-L", sock, "list-panes", "-t", "lead", "-F", "#{pane_id} #{pane_active}"],
                               capture_output=True, text=True).stdout.split("\n")
        self.assertIn(f"{pane} 1", panes)  # the owner's pane keeps the focus
        self.assertIn(f"{new} 0", panes)
        deadline, screen = time.monotonic() + 20, ""
        while time.monotonic() < deadline and not ("● 01" in screen and "● 07" in screen):
            real_sleep(0.2)
            screen = subprocess.run(["tmux", "-L", sock, "capture-pane", "-p", "-t", new], capture_output=True,
                                    text=True).stdout
        self.assertIn("fleet track ·", screen)
        self.assertIn("· every 2s · Ctrl-C quits", screen)  # live, though its server holds an agent's variable
        self.assertIn(f"fleet {self.repo.name}/demo · 1 live", screen)
        self.assertIn(f"fleet {other.name}/api · 1 live", screen)  # the pane read the caller's registry
        again = subprocess.run(run, cwd=self.repo, env=env, capture_output=True, text=True, timeout=60)
        self.assertIn(f"already runs in the pane below ({new})", again.stdout)
        count = subprocess.run(["tmux", "-L", sock, "list-panes", "-t", "lead"], capture_output=True, text=True)
        self.assertEqual(len(count.stdout.splitlines()), 2)


class OwnerViewsInAnAgentsShell(OwnerTerminal):
    """agents and attach, where what they print would go to an agent: agents lists once, attach refuses."""

    def agents(self, env=None, stdout=None):
        out = TtyIO() if stdout is None else stdout
        with patch.object(sys, "stdin", TtyIO()), contextlib.redirect_stdout(out), \
                patch.dict(os.environ, env or {}):
            code = fleet.main(["agents", "demo"])
        return code, out.getvalue()

    def test_agents_lists_the_workers_once_in_an_agents_shell(self):
        self.start()
        self.launch()
        with patch("curses.wrapper", side_effect=AssertionError("opened the live view")):
            for env, stdout in (({"CODEX_THREAD_ID": "t"}, None), ({}, io.StringIO())):
                with self.subTest(env=env):
                    code, out = self.agents(env, stdout)
                    self.assertEqual(code, 0, out)
                    self.assertIn("run demo", out)
                    self.assertIn("● 01", out)
        with patch("curses.wrapper", return_value=0) as live:  # a person's terminal still gets the live view
            self.assertEqual(self.agents()[0], 0)
        live.assert_called_once()

    def test_attach_refuses_where_the_workers_screen_would_go_to_an_agent(self):
        self.start()
        self.launch()

        class Tmux:
            def attach_argv(self, w, read_only):
                return ["tmux", "attach-session", "-t", "fleet-demo"]
        with patch.object(fleet, "terminal", return_value=Tmux()), \
                patch.object(fleet.subprocess, "call", side_effect=AssertionError("attached")):
            for env, out, why in (({"CLAUDECODE": "1"}, TtyIO(), "an agent's shell (CLAUDECODE is set)"),
                                  ({}, io.StringIO(), "not a terminal")):
                with self.subTest(why=why), patch.dict(os.environ, env), contextlib.redirect_stdout(out), \
                        contextlib.redirect_stderr(out):
                    self.assertEqual(fleet.main(["attach", "demo", "01"]), 1)
                self.assertIn(f"fleet attach is for your own terminal, and this is {why}", out.getvalue())
                self.assertIn("fleet agents demo lists the workers here", out.getvalue())
        with patch.object(fleet, "terminal", return_value=Tmux()), \
                patch.object(fleet.subprocess, "call", return_value=0) as call, contextlib.redirect_stdout(TtyIO()):
            self.assertEqual(fleet.main(["attach", "demo", "01"]), 0)  # a person's terminal attaches
        call.assert_called_once()


if __name__ == "__main__":
    unittest.main()
