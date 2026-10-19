"""Terminal backends: picking one, the tmux backend against a real tmux server, and the owner's views."""
import contextlib
import copy
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
for _var in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"):
    os.environ.pop(_var, None)
loader = importlib.machinery.SourceFileLoader("fleet_backend_tests", str(ROOT / "skill/bin/fleet"))
spec = importlib.util.spec_from_loader(loader.name, loader)
fleet = importlib.util.module_from_spec(spec)
loader.exec_module(fleet)

# A stand-in for an agent CLI: shows its environment, writes its report on "report", exits on "/quit".
FAKE_AGENT = r'''
import os, re, sys
prompt = sys.argv[-1]
path = re.search(r"Read (\S+) and follow", prompt).group(1)
ticket = re.search(r"worker (\S+)\.", prompt).group(1)
report = os.path.join(os.path.dirname(os.path.dirname(path)), "reports", ticket + ".md")
print("fake agent on " + ticket + " CLAUDECODE=" + str(os.environ.get("CLAUDECODE")), flush=True)
for line in sys.stdin:
    line = line.strip()
    print("got " + line, flush=True)
    if line == "report":
        open(report, "w").write("# Report " + ticket + "\n\nStatus: needs-verification\n")
    if line == "/quit":
        sys.exit(0)
'''


class PickBackend(unittest.TestCase):
    def cfg(self, backend="auto"):
        return {"defaults": dict(fleet.DEFAULTS, backend=backend)}

    def test_auto_keeps_cmux_wherever_it_worked_before(self):
        with patch.dict(os.environ, {"CMUX_WORKSPACE_ID": "ws"}):
            self.assertEqual(fleet.pick_backend(None, self.cfg(), None), "cmux")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CMUX_WORKSPACE_ID", None)
            self.assertEqual(fleet.pick_backend(None, self.cfg(), "workspace:2"), "cmux")

    def test_auto_goes_headless_outside_cmux(self):
        env = {k: v for k, v in os.environ.items() if k != "CMUX_WORKSPACE_ID"}
        with patch.dict(os.environ, env, clear=True), \
                patch.object(fleet.shutil, "which", side_effect=lambda b: "/bin/tmux" if b == "tmux" else None):
            self.assertEqual(fleet.pick_backend(None, self.cfg(), None), "tmux")
        with patch.dict(os.environ, env, clear=True), patch.object(fleet.shutil, "which", return_value=None):
            with self.assertRaisesRegex(fleet.FleetError, "install tmux"):
                fleet.pick_backend(None, self.cfg(), None)

    def test_an_explicit_choice_wins(self):
        with patch.dict(os.environ, {"CMUX_WORKSPACE_ID": "ws"}):
            self.assertEqual(fleet.pick_backend("tmux", self.cfg(), None), "tmux")
            self.assertEqual(fleet.pick_backend(None, self.cfg("tmux"), None), "tmux")
        self.assertEqual(fleet.pick_backend("cmux", self.cfg("tmux"), None), "cmux")

    def test_config_rejects_unknown_backends_and_socket_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "config.toml")
            for bad in ('backend = "zellij"', 'tmux_socket = "a b"', "tmux_socket = 3"):
                path.write_text(f"[defaults]\n{bad}\n")
                with self.assertRaises(fleet.FleetError):
                    fleet.load_config(path)
            path.write_text('[defaults]\nbackend = "tmux"\ntmux_socket = ""\n')
            self.assertEqual(fleet.load_config(path)["defaults"]["backend"], "tmux")

    def test_session_names_are_tmux_safe_and_differ_per_checkout(self):
        a = fleet.tmux_session_name(Path("/work/my.app"), "run-1")
        b = fleet.tmux_session_name(Path("/other/my.app"), "run-1")
        self.assertRegex(a, r"\Afleet-my-app-run-1-[0-9a-f]{4}\Z")
        self.assertNotEqual(a, b)

    def test_old_runs_without_a_backend_stay_on_cmux(self):
        self.assertEqual(fleet.terminal({"workspace": "WS"}).name, "cmux")
        self.assertEqual(fleet.terminal({"workspace": "s", "backend": "tmux"}).name, "tmux")


class TmuxUnit(unittest.TestCase):
    def term(self, socket="fleet"):
        return fleet.Tmux({"workspace": "fleet-app-demo-abcd", "backend": "tmux", "tmux_socket": socket})

    def test_pane_option_is_refused(self):
        with self.assertRaisesRegex(fleet.FleetError, "--pane applies to cmux"):
            self.term().place({}, "pane:2")

    def test_attach_selects_the_window_then_attaches(self):
        t = self.term()
        with patch.object(t, "tmux", return_value="42 @3\n42 @4\n"), patch.dict(os.environ, {"TMUX": ""}):
            os.environ.pop("TMUX")
            argv = t.attach_argv({"id": "01", "surface_id": "42/@3"}, read_only=True)
        self.assertEqual(argv, ["tmux", "-L", "fleet", "select-window", "-t", "@3", ";",
                                "attach-session", "-r", "-t", "=fleet-app-demo-abcd"])

    def test_inside_the_same_server_attach_switches_the_client(self):
        t = self.term(socket="")
        with patch.object(t, "tmux", return_value="42 @3\n"), patch.dict(os.environ, {"TMUX": "/tmp/x,1,0"}):
            self.assertEqual(t.attach_argv({"id": "01", "surface_id": "42/@3"}), ["tmux", "switch-client", "-t", "@3"])

    def test_a_window_of_another_server_is_never_targeted(self):
        t = self.term()
        with patch.object(t, "tmux", return_value="99 @3\n"):  # the server restarted and reused @3
            with self.assertRaisesRegex(fleet.FleetError, "is gone"):
                t.read({"id": "01", "surface_id": "42/@3"}, 10)
            t.close({"id": "01", "surface_id": "42/@3"})  # already gone counts as closed


class Doctor(unittest.TestCase):
    def run_doctor(self, backend, cmux_ok, tmux_ok):
        cfg = {"defaults": dict(fleet.DEFAULTS, backend=backend), "providers": {}}

        def cmux(*a, **k):
            if not cmux_ok:
                raise fleet.FleetError("socket unavailable")
            return ""

        def tmux_check(cfg):
            if not tmux_ok:
                raise fleet.FleetError("tmux is not installed")
            return "tmux 3.4"
        out = io.StringIO()
        with patch.object(fleet, "cmux", side_effect=cmux), patch.object(fleet.Tmux, "check", side_effect=tmux_check), \
                patch.object(fleet, "skill_dirs", return_value={}), patch.object(fleet, "companion_skills", return_value={}), \
                contextlib.redirect_stdout(out):
            code = fleet.cmd_doctor(type("A", (), {"providers": []})(), cfg)
        return code, out.getvalue()

    def test_doctor_needs_only_the_chosen_backend(self):
        self.assertEqual(self.run_doctor("tmux", cmux_ok=False, tmux_ok=True)[0], 0)
        self.assertEqual(self.run_doctor("tmux", cmux_ok=True, tmux_ok=False)[0], 1)
        self.assertEqual(self.run_doctor("cmux", cmux_ok=False, tmux_ok=True)[0], 1)
        code, text = self.run_doctor("cmux", cmux_ok=True, tmux_ok=False)
        self.assertEqual(code, 0)
        self.assertIn("not your backend; ignored", text)
        self.assertEqual(self.run_doctor("auto", cmux_ok=False, tmux_ok=True)[0], 0)
        self.assertEqual(self.run_doctor("auto", cmux_ok=False, tmux_ok=False)[0], 1)


@unittest.skipUnless(shutil.which("tmux"), "tmux is not installed")
class TmuxLive(unittest.TestCase):
    """The tmux backend end to end, against a real tmux server on a private socket."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.repo = root / "repo"
        self.repo.mkdir()
        for args in (["init", "-q"], ["config", "user.email", "t@example.invalid"], ["config", "user.name", "t"]):
            subprocess.run(["git", *args], cwd=self.repo, check=True)
        (self.repo / "a.txt").write_text("base\n")
        subprocess.run(["git", "add", "a.txt"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=self.repo, check=True)
        agent = root / "fake-agent"
        agent.write_text(f"#!{sys.executable}\n{FAKE_AGENT}")
        agent.chmod(0o755)
        self.socket = f"fleettest{os.getpid()}"
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
                       patch.dict(os.environ, {"CLAUDECODE": "1"})):  # as if the lead were Claude Code
            mocker.start()
            self.addCleanup(mocker.stop)

    def call(self, *args, expected=0):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = fleet.main(list(args))
        self.assertEqual(code, expected, f"fleet {' '.join(args)}\n{out.getvalue()}")
        return out.getvalue()

    def until(self, check, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if value := check():
                return value
            time.sleep(0.2)
        self.fail("timed out")

    def windows(self):
        res = subprocess.run(["tmux", "-L", self.socket, "list-windows", "-a", "-F", "#{window_id} #{pane_dead}"],
                             capture_output=True, text=True)
        return res.stdout.split("\n")[:-1] if res.returncode == 0 else []

    def test_a_headless_worker_launches_reports_stops_and_closes(self):
        text = self.call("init", "demo")
        self.assertIn("backend: tmux (headless)", text)
        state = json.loads((self.repo / ".fleet/runs/demo/run.json").read_text())
        self.assertEqual((state["backend"], state["tmux_socket"]), ("tmux", self.socket))
        ticket = self.repo / "01.md"
        ticket.write_text("# Change a.txt\nDo it.\n")
        plan = self.repo / "plan.json"
        plan.write_text(json.dumps({"tickets": [{"id": "01", "ticket": str(ticket), "files": ["a.txt"],
                                                 "provider": "fake", "tier": "standard", "context": "none",
                                                 "checks": "manual prose review is sufficient"}]}))
        self.call("plan", "demo", "--file", str(plan))
        self.assertRegex(self.call("launch", "demo", "--ready"), r"LAUNCHED 01 fake:fake-1 standard @\d+")
        self.until(lambda: "fake agent on 01" in self.call("peek", "demo", "01"))
        # The lead's session marker never reaches the worker.
        self.assertIn("CLAUDECODE=None", self.call("peek", "demo", "01"))
        self.call("send", "demo", "01", "report")
        self.assertIn("REPORT 01 [needs-verification]", self.call("wait", "demo", "--timeout", "10", "--settle", "0"))
        listing = self.call("agents", "--list")
        self.assertRegex(listing, r"● 01 +fake:fake-1 +running +needs-verification .* Change a.txt")
        self.assertIn(f"tmux -L {self.socket} attach", listing)
        self.assertRegex(self.call("stop", "demo", "01"), r"01: exited \(@\d+\)")
        self.assertEqual([w.split()[1] for w in self.windows()], ["1"])  # kept, dead, for its scrollback
        self.assertIn("got /quit", self.call("peek", "demo", "01"))
        self.assertRegex(self.call("stop", "demo", "01", "--close"), r"01: closed")
        self.assertEqual(self.windows(), [])

    def test_a_lost_server_is_unreachable_and_keeps_the_slot(self):
        self.call("init", "demo")
        ticket = self.repo / "01.md"
        ticket.write_text("# Change a.txt\n")
        (self.repo / "plan.json").write_text(json.dumps({"tickets": [
            {"id": "01", "ticket": str(ticket), "files": ["a.txt"], "provider": "fake", "tier": "standard",
             "context": "none", "checks": "manual prose review is sufficient"}]}))
        self.call("plan", "demo", "--file", "plan.json")
        self.call("launch", "demo", "--ready")
        subprocess.run(["tmux", "-L", self.socket, "kill-server"], capture_output=True)
        self.assertIn("UNREACHABLE 01", self.call("wait", "demo", "--timeout", "5", "--settle", "0", "--stall", "1"))
        self.assertIn("01: stop-failed", self.call("stop", "demo", "01", "--timeout", "1", expected=1))


if __name__ == "__main__":
    unittest.main()
