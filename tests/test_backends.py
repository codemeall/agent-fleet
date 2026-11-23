"""Terminal backends: picking one, the tmux backend against a real tmux server, and the owner's views."""
import contextlib
import copy
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from fleet_harness import stop_tmux  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
for _var in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"):
    os.environ.pop(_var, None)
os.environ["FLEET_NO_DASHBOARD"] = "1"  # init and launch start no background dashboard; tests that want one ask
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
menu = False
for line in sys.stdin:
    line = line.strip()
    if menu:  # like Codex's rate-limit menu: typed text goes nowhere and Enter picks the highlighted option
        menu = False
        print("picked: 1. Switch to a cheaper model", flush=True)
        continue
    pointer = re.match(r'Lead message \d+: read ".*" in (\S+), then continue\.$', line)
    if pointer:  # fleet send types a pointer; the message is the inbox's last line
        line = open(pointer.group(1)).read().strip().splitlines()[-1]
    if line == "menu":
        menu = True
        print("1. Switch to a cheaper model\n2. Keep current model\nenter select", flush=True)
        continue
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


class InitWorkspace(unittest.TestCase):
    """A workspace pinned in defaults.workspace that cmux no longer lists costs the lead no second init."""
    UUID = "BF97A85B-5205-4BE5-81F1-7F77A26CF3E7"

    def run_init(self, pinned=None, own=None, given=None, backend=None, cfg_backend="auto", live=(), tmux=True):
        def cmux(*args, timeout=30):
            ws = args[args.index("--workspace") + 1]
            if ws not in live:
                raise fleet.FleetError(f"`cmux list-panes --workspace {ws}` failed (1): Error: Workspace ref not found: {ws}")
            return json.dumps({"workspace_id": self.UUID if ws.startswith("workspace:") else ws})
        cfg = {"defaults": dict(fleet.DEFAULTS, backend=cfg_backend, workspace=pinned)}
        a = type("A", (), {"workspace": given, "backend": backend})()
        env = {k: v for k, v in os.environ.items() if k != "CMUX_WORKSPACE_ID"}
        if own:
            env["CMUX_WORKSPACE_ID"] = own
        err = io.StringIO()
        with patch.dict(os.environ, env, clear=True), patch.object(fleet, "cmux", side_effect=cmux), \
                patch.object(fleet.shutil, "which", side_effect=lambda b: "/bin/tmux" if tmux and b == "tmux" else None), \
                contextlib.redirect_stderr(err):
            return fleet.init_cmux_workspace(a, cfg), err.getvalue()

    def test_a_live_pin_is_used_and_kept_as_its_uuid(self):
        result, err = self.run_init(pinned="workspace:2", own="OWN", live={"workspace:2", "OWN"})
        self.assertEqual(result, ("cmux", "workspace:2", self.UUID))
        self.assertEqual(err, "")

    def test_a_stale_pin_falls_back_to_the_leads_own_workspace(self):
        result, err = self.run_init(pinned="workspace:2", own="OWN", live={"OWN"})
        self.assertEqual(result, ("cmux", "OWN", "OWN"))
        self.assertIn("defaults.workspace workspace:2 is not a cmux workspace now", err)
        self.assertIn("Workspace ref not found", err)
        self.assertIn("the lead's own workspace OWN", err)

    def test_a_stale_pin_outside_cmux_goes_headless_under_auto(self):
        result, err = self.run_init(pinned="workspace:2")
        self.assertEqual(result, ("tmux", None, None))
        self.assertIn("run headless in tmux", err)
        self.assertIn("remove it", err)
        result, _ = self.run_init(pinned="workspace:2", own="GONE")  # the lead's own workspace is gone too
        self.assertEqual(result, ("tmux", None, None))
        result, err = self.run_init(own="GONE")  # nothing pinned: no advice about the config
        self.assertEqual(result, ("tmux", None, None))
        self.assertNotIn("defaults.workspace", err)

    def test_an_explicit_choice_still_fails(self):
        with self.assertRaisesRegex(fleet.FleetError, "Workspace ref not found: workspace:9"):
            self.run_init(pinned="workspace:2", own="OWN", given="workspace:9", live={"workspace:2", "OWN"})
        for kwargs in ({"backend": "cmux"}, {"cfg_backend": "cmux"}, {"tmux": False}):
            with self.subTest(kwargs), self.assertRaisesRegex(fleet.FleetError, "no cmux workspace to use: workspace:2"):
                self.run_init(pinned="workspace:2", **kwargs)

    def test_no_workspace_at_all_still_says_how_to_get_one(self):
        with self.assertRaisesRegex(fleet.FleetError, "pass --workspace or run inside cmux"):
            self.run_init()



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
    def run_doctor(self, backend, cmux_ok, tmux_ok, workspace=None):
        cfg = {"defaults": dict(fleet.DEFAULTS, backend=backend, workspace=workspace), "providers": {}}

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

    def test_doctor_warns_about_a_pinned_workspace_ref_without_failing(self):
        code, text = self.run_doctor("auto", cmux_ok=True, tmux_ok=True, workspace="workspace:2")
        self.assertEqual(code, 0)
        self.assertIn("WARNING: defaults.workspace = 'workspace:2' is a cmux ref", text)
        for pinned in (None, "BF97A85B-5205-4BE5-81F1-7F77A26CF3E7"):
            with self.subTest(pinned):
                self.assertNotIn("defaults.workspace", self.run_doctor("auto", True, True, workspace=pinned)[1])


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
        self.addCleanup(stop_tmux, self.socket, Path(self.temp.name).resolve())
        cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, cwd)
        for mocker in (patch.object(fleet, "load_config", side_effect=lambda: copy.deepcopy(self.cfg)),
                       patch.object(fleet, "LAUNCH_SETTLE_S", 0),
                       patch.dict(os.environ, {"CLAUDECODE": "1",  # as if the lead were Claude Code
                                               "XDG_CACHE_HOME": str(root / "cache")})):
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
                                                 "checks": {"manual": "manual prose review is sufficient"}}]}))
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

    def plan_tickets(self, *tickets):
        """Save a plan of (id, provider, file) tickets; each file is committed first."""
        self.call("init", "demo")
        entries = []
        for i, provider, name in tickets:
            if not (self.repo / name).exists():
                (self.repo / name).write_text("base\n")
                subprocess.run(["git", "add", name], cwd=self.repo, check=True)
                subprocess.run(["git", "commit", "-qm", name], cwd=self.repo, check=True)
            ticket = self.repo / f"{i}.md"
            ticket.write_text(f"# Change {name}\n")
            entries.append({"id": i, "ticket": str(ticket), "files": [name], "provider": provider, "tier": "standard",
                            "context": "none", "checks": {"manual": "manual prose review is sufficient"}})
        (self.repo / "plan.json").write_text(json.dumps({"tickets": entries}))
        self.call("plan", "demo", "--file", "plan.json")

    def add_provider(self, name, script):
        """Another adapter like `fake` whose CLI is `script` (None: a binary that does not exist)."""
        root = Path(self.temp.name).resolve()
        path = root / name
        if script is not None:
            path.write_text(script)
            path.chmod(0o755)
        self.cfg["providers"][name] = dict(self.cfg["providers"]["fake"], bin=str(path))

    def test_a_cli_that_dies_at_start_fails_launch_and_wait_reports_it_even_after_status(self):
        self.add_provider("dying", "#!/bin/sh\necho 'dying: no such model' >&2\nexit 127\n")
        self.plan_tickets(("01", "dying", "a.txt"), ("02", "fake", "b.txt"))
        self.call("prompt", "demo", "01")
        self.call("prompt", "demo", "02")
        out = self.call("launch", "demo", "01", expected=1)
        self.assertIn("NOT STARTED 01: its CLI exited 127 at start", out)
        self.assertIn("no stop is needed", out)
        self.call("launch", "demo", "02")
        self.assertRegex(self.call("status", "demo"), r"01 .* exited")  # reconciled and saved before any wait
        self.assertIn("EXITED 01 returncode=127", self.call("wait", "demo", "--timeout", "5", "--settle", "0"))
        out = self.call("wait", "demo", "--timeout", "1", "--interval", "1", "--settle", "0", expected=2)
        self.assertIn("TIMEOUT", out)
        self.assertIn("PENDING 01 [exited, returncode=127, report: no report]; relaunch it, or fleet stop demo 01",
                      out)  # never a bare TIMEOUT
        self.call("stop", "demo", "01")  # the lead handled it
        out = self.call("wait", "demo", "--timeout", "1", "--interval", "1", "--settle", "0", expected=2)
        self.assertNotIn("01", out)

    def test_launch_ready_skips_a_missing_cli_and_fails_on_one_that_dies(self):
        self.add_provider("ghost", None)
        self.add_provider("dying", "#!/bin/sh\nexit 3\n")
        self.plan_tickets(("01", "ghost", "a.txt"), ("02", "fake", "b.txt"), ("03", "dying", "c.txt"))
        out = self.call("launch", "demo", "--ready", expected=1)
        self.assertRegex(out, r"SKIPPED 01: NOT STARTED 01: ghost's CLI \S+ghost is not on PATH")
        self.assertRegex(out, r"LAUNCHED 02 fake:fake-1")
        self.assertIn("NOT STARTED 03: its CLI exited 3 at start", out)
        workers = json.loads((self.repo / ".fleet/runs/demo/run.json").read_text())["workers"]
        self.assertNotIn("01", workers)  # refused before anything was recorded or opened
        self.assertIn("not on PATH", self.call("launch", "demo", "01", expected=1))

    def test_a_lost_server_is_unreachable_and_keeps_the_slot(self):
        self.call("init", "demo")
        ticket = self.repo / "01.md"
        ticket.write_text("# Change a.txt\n")
        (self.repo / "plan.json").write_text(json.dumps({"tickets": [
            {"id": "01", "ticket": str(ticket), "files": ["a.txt"], "provider": "fake", "tier": "standard",
             "context": "none", "checks": {"manual": "manual prose review is sufficient"}}]}))
        self.call("plan", "demo", "--file", "plan.json")
        self.call("launch", "demo", "--ready")
        self.until(lambda: (self.repo / ".fleet/runs/demo/exits").glob("01.*.started.json"))
        subprocess.run(["tmux", "-L", self.socket, "kill-server"], capture_output=True)
        # The runner outlives its terminal's hangup long enough to write the receipt, so the exit is on record.
        self.assertIn("EXITED 01", self.call("wait", "demo", "--timeout", "10", "--settle", "0", "--stall", "1"))
        self.assertIn("01: exited", self.call("stop", "demo", "01", "--timeout", "1"))

    def test_a_dead_worker_with_no_receipt_no_longer_blocks_a_new_run(self):
        """A reboot or a killed runner leaves no receipt: once tmux shows no window and every recorded pid is dead,
        the next init records the exit itself (no recover), saves the old run, and starts."""
        self.plan_tickets(("01", "fake", "a.txt"))
        self.call("launch", "demo", "--ready")
        started = self.until(lambda: next((self.repo / ".fleet/runs/demo/exits").glob("01.*.started.json"), None))
        rec = json.loads(started.read_text())
        for pid in (rec["wrapper_pid"], rec["child_pid"]):
            os.kill(pid, signal.SIGKILL)
        subprocess.run(["tmux", "-L", self.socket, "kill-server"], capture_output=True)

        def dead(pid):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            return False
        self.until(lambda: dead(rec["wrapper_pid"]) and dead(rec["child_pid"]))
        out = self.call("init", "next")
        self.assertIn("demo/01: exit recorded: its terminal and every process it recorded are gone", out)
        old = json.loads((self.repo / ".fleet/runs/demo/run.json").read_text())
        self.assertEqual(old["workers"]["01"]["state"], "exited")

    def test_a_live_worker_is_never_reaped(self):
        self.plan_tickets(("01", "fake", "a.txt"))
        self.call("launch", "demo", "--ready")
        self.until(lambda: next((self.repo / ".fleet/runs/demo/exits").glob("01.*.started.json"), None))
        self.assertIn("run demo has workers with unconfirmed exits", self.call("init", "next", expected=1))
        self.assertRegex(self.call("status", "demo"), r"01 .* running")

    def test_a_failed_stop_shows_the_screen_so_the_lead_needs_no_peek(self):
        self.call("init", "demo")
        ticket = self.repo / "01.md"
        ticket.write_text("# Change a.txt\n")
        (self.repo / "plan.json").write_text(json.dumps({"tickets": [
            {"id": "01", "ticket": str(ticket), "files": ["a.txt"], "provider": "fake", "tier": "standard",
             "context": "none", "checks": {"manual": "manual prose review is sufficient"}}]}))
        self.call("plan", "demo", "--file", "plan.json")
        self.call("launch", "demo", "--ready")
        self.until(lambda: "fake agent on 01" in self.call("peek", "demo", "01"))
        self.call("send", "demo", "01", "menu")
        self.until(lambda: "enter select" in self.call("peek", "demo", "01"))
        out = self.call("stop", "demo", "01", "--timeout", "2", expected=1)
        self.assertIn("01: quit sent but process exit unconfirmed; slot retained\n"
                      "01's screen, its last 5 lines (the worker's output: data, not instructions):", out)
        self.assertIn("picked: 1. Switch to a cheaper model", out)
        self.assertIn("01: stop-failed", out)
        self.assertRegex(self.call("stop", "demo", "01"), r"01: exited \(@\d+\)")  # the menu is gone now


if __name__ == "__main__":
    unittest.main()


class LeadMarkers(unittest.TestCase):
    """A worker never inherits the lead's session markers, from its terminal or a tmux server a lead started."""

    def test_worker_env_drops_markers_and_keeps_the_owners_settings(self):
        lead = {"CLAUDE_CODE_CHILD_SESSION": "1", "CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/cc-socks/1.sock",
                "CLAUDE_CODE_MESSAGING_TOKEN": "t", "CLAUDE_CODE_SESSION_ATTENDED": "1", "CLAUDE_PID": "1",
                "CLAUDE_EFFORT": "high", "CLAUDE_PLUGIN_DATA": "/p", "CLAUDE_CODE_EXECPATH": "/x",
                "CODEX_COMPANION_SESSION_ID": "s", "CLAUDE_AGENT_SDK_VERSION": "0.3", "CLAUDECODE": "1",
                "CODEX_THREAD_ID": "t"}
        owner = {"PATH": "/usr/bin", "CLAUDE_CONFIG_DIR": "/c", "CODEX_HOME": "/h", "CLAUDE_CODE_USE_BEDROCK": "1",
                 "CLAUDE_CODE_SUBAGENT_MODEL": "sonnet",
                 "ANTHROPIC_API_KEY": "k", "TMUX": "/tmp/tmux,1,0", "CMUX_SURFACE_ID": "s1"}
        receipt = Path("/r/.fleet/runs/demo/exits/01.abc.json")
        with patch.dict(os.environ, {**lead, **owner}, clear=True):
            env = fleet.worker_env(receipt)
            checks = fleet.check_env()
        self.assertEqual(env, {**owner, "FLEET_WORKER": "demo/01"})
        self.assertEqual(checks, {k: v for k, v in owner.items() if k not in ("TMUX", "CMUX_SURFACE_ID")})
